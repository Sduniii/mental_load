"""Tests for MentalLoadCoordinator: caching, feedback learning, todo sync, and reactions."""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntry

from custom_components.mental_load.const import (
    CONF_AUTO_PUSH_TODO,
    CONF_AUTO_SYNC_CALENDAR,
    CONF_CALENDARS,
    CONF_CONTEXT_TODOS,
    CONF_CRITICAL_THRESHOLD,
    CONF_HORIZON_DAYS,
    CONF_MAX_PROJECT_STEPS_WEEKDAY,
    CONF_MAX_PROJECT_STEPS_WEEKEND,
    CONF_NOTIFY_SERVICE,
    CONF_PROJECT_BASELINE_CAP,
    CONF_PROJECT_BASELINE_PER_ITEM,
    CONF_PROJECT_PLANNING_ENABLED,
    CONF_PROVIDER,
    CONF_REACTION_ENTITY,
    CONF_REACTION_ON_CRITICAL,
    CONF_TARGET_CALENDAR_ENTITY,
    CONF_TARGET_TODO_ENTITY,
    CONF_WARNING_THRESHOLD,
    EVENT_THRESHOLD_EXCEEDED,
    PROVIDER_HEURISTIC,
)
from custom_components.mental_load.coordinator import MentalLoadCoordinator
from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask
from custom_components.mental_load.prompt_builder import (
    compute_project_baseline,
    extract_todo_priority,
)
from tests.conftest import FakeHass


def _make_high_load_analysis(day: datetime.date, score: int = 88) -> DayLoadAnalysis:
    tz = datetime.timezone(datetime.timedelta(hours=1))
    return DayLoadAnalysis(
        date=day,
        score=score,
        level="critical" if score >= 85 else "high",
        badge_summary=f"🔴 {score}% Mental Load (Kritisch) – Zahnarzt, 2 Kopf-Aufgaben",
        reasoning="Sehr voller Tag.",
        factor_breakdown={
            "pacing_context": 85,
            "social_battery": 80,
            "mobility": 70,
            "ha_context": 75,
            "hidden_work": 90,
        },
        hidden_tasks=[
            HiddenTask(
                uid="task_uid_1",
                parent_event_summary="Zahnarzt",
                title="🧠 Vorbereitung: Zahnarzt",
                category="prep",
                start=datetime.datetime.combine(day, datetime.time(14, 0), tzinfo=tz),
                end=datetime.datetime.combine(day, datetime.time(14, 20), tzinfo=tz),
                load_contribution=15,
                checklist=["Karte mitnehmen"],
            )
        ],
        recommendations=["Ruhezeit abends einplanen"],
        top_stressor="Zahnarzt",
        provider_used="mock",
    )


async def test_coordinator_caching_todo_dedup_and_reactions() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)
    hass.services.set_response(
        "calendar",
        "get_events",
        {
            "calendar.google": {
                "events": [
                    {
                        "uid": "g1",
                        "summary": "Zahnarzt",
                        "start": "2026-11-23T14:30:00+01:00",
                        "end": "2026-11-23T15:30:00+01:00",
                        "location": "Zahnklinik",
                    }
                ]
            }
        },
    )

    entry = ConfigEntry(
        entry_id="entry_1",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 2,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_AUTO_PUSH_TODO: True,
            CONF_TARGET_TODO_ENTITY: "todo.mental_load",
            CONF_WARNING_THRESHOLD: 65,
            CONF_CRITICAL_THRESHOLD: 80,
            CONF_NOTIFY_SERVICE: "persistent_notification.create",
            CONF_REACTION_ENTITY: "scene.evening_calm",
            CONF_REACTION_ON_CRITICAL: True,
        },
    )

    mock_llm = AsyncMock()
    mock_llm.async_analyze_day = AsyncMock(
        side_effect=lambda target_date, **kw: _make_high_load_analysis(target_date, score=88)
    )

    coord = MentalLoadCoordinator(hass, entry, llm_client=mock_llm)
    await coord.async_config_entry_first_refresh()

    # 2 days analyzed on first run
    assert mock_llm.async_analyze_day.call_count == 2
    assert coord.data[today.isoformat()].score == 88

    # Todo item pushed once for today
    todo_calls = [c for c in hass.services.calls if c[0] == "todo" and c[1] == "add_item"]
    assert len(todo_calls) == 1
    assert "[ML-task_uid_1]" in todo_calls[0][2]["description"]

    # Active reactions fired: event, persistent_notification, scene.turn_on
    threshold_events = [e for e in hass.bus.events if e[0] == EVENT_THRESHOLD_EXCEEDED]
    assert len(threshold_events) == 1
    assert threshold_events[0][1]["level"] == "critical"
    assert ("persistent_notification", "create") in [(c[0], c[1]) for c in hass.services.calls]
    assert ("scene", "turn_on", {"entity_id": "scene.evening_calm"}) in hass.services.calls

    # Second refresh with unchanged calendar must hit SHA-256 cache (no extra LLM calls, no duplicate todo/notifications)
    await coord.async_refresh()
    assert mock_llm.async_analyze_day.call_count == 2
    assert coord.data[today.isoformat()].cached is True
    assert len([c for c in hass.services.calls if c[0] == "todo" and c[1] == "add_item"]) == 1
    assert len([e for e in hass.bus.events if e[0] == EVENT_THRESHOLD_EXCEEDED]) == 1


async def test_submit_feedback_stores_memory_and_recomputes() -> None:
    hass = FakeHass()
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": []}})

    entry = ConfigEntry(
        entry_id="entry_fb",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    fb = await coord.async_submit_feedback(
        target_date="2026-11-23",
        perceived_score=15,
        adjustment="much_lower",
        note="Freier Tag",
    )
    assert fb.perceived_score == 15
    assert len(coord.feedback_history) == 1
    assert coord.data["2026-11-23"].cached is False


async def test_toggle_checklist_and_external_calendar_sync_guard() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)
    entry = ConfigEntry(
        entry_id="sync_entry",
        data={
            CONF_CALENDARS: ["calendar.google_source"],
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_AUTO_SYNC_CALENDAR: True,
            CONF_TARGET_CALENDAR_ENTITY: "calendar.google_mental_load",
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    coord.data = {today.isoformat(): _make_high_load_analysis(today, score=82)}

    # Toggle checklist item
    done = await coord.async_toggle_checklist_item("task_uid_1:step:0", completed=True)
    assert done is True
    assert "task_uid_1:step:0" in coord.completed_item_uids

    # Safety guard: refuse writing to read-only source calendar
    blocked = await coord.async_sync_to_external_calendar("calendar.google_source")
    assert blocked == 0

    # Writing to dedicated secondary Google Calendar succeeds & deduplicates
    synced = await coord.async_sync_to_external_calendar("calendar.google_mental_load")
    assert synced == 2  # 1 day badge + 1 hidden task
    create_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "create_event"]
    assert len(create_calls) == 2
    assert create_calls[0][2]["entity_id"] == "calendar.google_mental_load"

    # Second sync does not create duplicates
    assert await coord.async_sync_to_external_calendar("calendar.google_mental_load") == 0


async def test_multiday_load_balancing_and_dynamic_completion_relief() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)
    tuesday = today + datetime.timedelta(days=1)
    wednesday = today + datetime.timedelta(days=2)
    tz = datetime.timezone(datetime.timedelta(hours=1))

    entry = ConfigEntry(
        entry_id="lb_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 3,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
        },
    )

    def _day_analysis(target_date: datetime.date, **kw: Any) -> DayLoadAnalysis:
        if target_date == today:
            return DayLoadAnalysis(
                date=today, score=50, level="medium", badge_summary="🟡 50%",
                reasoning="Normal", factor_breakdown={"pacing_context": 50, "social_battery": 50, "mobility": 50, "ha_context": 50, "hidden_work": 50},
                base_score=50,
            )
        if target_date == tuesday:
            return DayLoadAnalysis(
                date=tuesday, score=3, level="low", badge_summary="🟢 3%",
                reasoning="Frei", factor_breakdown={"pacing_context": 0, "social_battery": 0, "mobility": 0, "ha_context": 10, "hidden_work": 0},
                base_score=3,
            )
        # Wednesday has an errand (Bargeld abheben) + morning packing task (09:00)
        return DayLoadAnalysis(
            date=wednesday,
            score=45,
            level="medium",
            badge_summary="🟡 45%",
            reasoning="Osteopathie",
            factor_breakdown={"pacing_context": 40, "social_battery": 30, "mobility": 45, "ha_context": 30, "hidden_work": 70},
            base_score=45,
            hidden_tasks=[
                HiddenTask(
                    uid="errand_cash",
                    parent_event_summary="Osteopathie",
                    title="🧠 Vorbereitung: Bargeld abheben",
                    category="prep",
                    start=datetime.datetime.combine(wednesday, datetime.time(9, 0), tzinfo=tz),
                    end=datetime.datetime.combine(wednesday, datetime.time(9, 20), tzinfo=tz),
                    load_contribution=15,
                    checklist=["50 EUR abheben", "Rezept mitnehmen"],
                    parent_event_date=wednesday.isoformat(),
                    scheduled_date=wednesday.isoformat(),
                    lead_days=2,
                    placement_mode="load_balanced",
                ),
                HiddenTask(
                    uid="pack_bag",
                    parent_event_summary="Osteopathie",
                    title="🎒 Einpacken: Praxistasche",
                    category="packing",
                    start=datetime.datetime.combine(wednesday, datetime.time(9, 20), tzinfo=tz),
                    end=datetime.datetime.combine(wednesday, datetime.time(9, 35), tzinfo=tz),
                    load_contribution=10,
                    checklist=["Handtuch einpacken"],
                    parent_event_date=wednesday.isoformat(),
                    scheduled_date=wednesday.isoformat(),
                    lead_days=1,
                    placement_mode="previous_evening",
                ),
            ],
        )

    hass.services.set_response(
        "calendar",
        "get_events",
        {
            "calendar.google": {
                "events": [
                    {
                        "uid": "osteo_1",
                        "summary": "Osteopathie",
                        "start": f"{wednesday.isoformat()}T10:00:00+01:00",
                        "end": f"{wednesday.isoformat()}T11:00:00+01:00",
                    }
                ]
            }
        },
    )
    mock_llm = AsyncMock()
    mock_llm.async_analyze_day = AsyncMock(side_effect=_day_analysis)

    coord = MentalLoadCoordinator(hass, entry, llm_client=mock_llm)
    await coord.async_config_entry_first_refresh()

    tue_day = coord.data[tuesday.isoformat()]
    wed_day = coord.data[wednesday.isoformat()]

    # Both advance tasks moved from Wednesday to calm Tuesday and linked bidirectionally
    assert len(tue_day.hidden_tasks) == 2
    assert len(wed_day.hidden_tasks) == 0
    assert len(wed_day.linked_prep_tasks) == 2
    assert all(t.parent_event_date == wednesday.isoformat() for t in tue_day.hidden_tasks)
    assert tue_day.score > 3  # Calm Tuesday picked up light planning load
    assert wed_day.score < 45  # Wednesday got initial offload relief

    tue_before = tue_day.score
    wed_before = wed_day.score

    # Completing both steps of errand_cash dynamically lowers BOTH Tuesday and Wednesday scores
    await coord.async_toggle_checklist_item("errand_cash:step:0", completed=True)
    await coord.async_toggle_checklist_item("errand_cash:step:1", completed=True)

    assert coord.data[tuesday.isoformat()].score < tue_before
    assert coord.data[wednesday.isoformat()].score < wed_before
    assert coord.data[wednesday.isoformat()].relief_points > 0


async def test_30_day_horizon_major_event_7_to_14_day_lead_and_empty_fast_path() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)
    major_day = today + datetime.timedelta(days=12)  # 2026-12-05
    tz = datetime.timezone(datetime.timedelta(hours=1))

    # Only major_day has a calendar event; days 2..29 except major_day are empty
    hass.services.set_response(
        "calendar",
        "get_events",
        {
            "calendar.google": {
                "events": [
                    {
                        "uid": "bday1",
                        "summary": "Kindergeburtstag Emil",
                        "start": f"{major_day.isoformat()}T15:00:00+01:00",
                        "end": f"{major_day.isoformat()}T18:00:00+01:00",
                        "location": "Indoor-Spielplatz",
                    }
                ]
            }
        },
    )
    entry = ConfigEntry(
        entry_id="horizon_30_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 30,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
        },
    )

    mock_llm = AsyncMock()
    mock_llm.async_analyze_day = AsyncMock(
        side_effect=lambda target_date, events, **kw: DayLoadAnalysis(
            date=target_date,
            score=75 if events else 10,
            level="high" if events else "low",
            badge_summary="🟠 75%" if events else "🟢 10%",
            reasoning="Geburtstag" if events else "Frei",
            factor_breakdown={"pacing_context": 50, "social_battery": 80, "mobility": 60, "ha_context": 20, "hidden_work": 80} if events else {"pacing_context": 0, "social_battery": 0, "mobility": 0, "ha_context": 10, "hidden_work": 0},
            base_score=75 if events else 10,
            hidden_tasks=[
                HiddenTask(
                    uid="gift_1",
                    parent_event_summary="Kindergeburtstag Emil",
                    title="🎒 Mitbringsel & Geschenk: Kindergeburtstag Emil",
                    category="packing",
                    start=datetime.datetime.combine(major_day, datetime.time(14, 30), tzinfo=tz),
                    end=datetime.datetime.combine(major_day, datetime.time(14, 50), tzinfo=tz),
                    load_contribution=20,
                    checklist=["Geschenk bestellen", "Karte schreiben"],
                    parent_event_date=major_day.isoformat(),
                    scheduled_date=major_day.isoformat(),
                    lead_days=12,
                    placement_mode="load_balanced",
                )
            ] if events else [],
        )
    )

    coord = MentalLoadCoordinator(hass, entry, llm_client=mock_llm)
    await coord.async_config_entry_first_refresh()

    # 30 days generated, yet LLM called only for today, tomorrow, and major_day (3 calls instead of 30!)
    assert len(coord.data) == 30
    assert mock_llm.async_analyze_day.call_count == 3

    # Major event gift task moved into the 7..14 days before window (days 0..5 before major_day)
    placed_days = [
        d for d in coord.data.values()
        if any(t.uid == "gift_1" for t in d.hidden_tasks)
    ]
    assert len(placed_days) == 1
    lead_actual = (major_day - placed_days[0].date).days
    assert 7 <= lead_actual <= 14
    assert len(coord.data[major_day.isoformat()].linked_prep_tasks) == 1


async def test_project_baseline_load_capped_and_drops_when_completed() -> None:
    hass = FakeHass()
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": []}})
    todo_items = [{"summary": f"Hausbau Projekt {i}", "status": "needs_action"} for i in range(10)]
    hass.services.set_response(
        "todo",
        "get_items",
        {"todo.hausbau_anytype": {"items": todo_items}},
    )

    entry = ConfigEntry(
        entry_id="baseline_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_CONTEXT_TODOS: ["todo.hausbau_anytype"],
            CONF_HORIZON_DAYS: 7,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_PROJECT_BASELINE_PER_ITEM: 2.0,
            CONF_PROJECT_BASELINE_CAP: 15,
            CONF_PROJECT_PLANNING_ENABLED: False,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    empty_future_day = coord.data["2026-11-26"]
    assert empty_future_day.project_baseline_load == 15
    assert empty_future_day.open_project_count == 10
    assert empty_future_day.score == 15

    # When all todos are finished, baseline drops back to 0 without re-running daily LLM cache
    hass.services.set_response("todo", "get_items", {"todo.hausbau_anytype": {"items": []}})
    await coord.async_refresh()
    cleared_day = coord.data["2026-11-26"]
    assert cleared_day.project_baseline_load == 0
    assert cleared_day.open_project_count == 0
    assert cleared_day.score == 0


async def test_project_breakdown_weekend_limit_and_sunday_quiet_rule() -> None:
    hass = FakeHass()
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": []}})
    hass.services.set_response(
        "todo",
        "get_items",
        {
            "todo.hausbau_anytype": {
                "items": [
                    {"summary": "Hänger TÜV", "status": "needs_action"},
                    {"summary": "OG: Schleifen Balken", "status": "needs_action"},
                    {"summary": "Baumarkt Holz kaufen", "status": "needs_action"},
                    {"summary": "Heizkreis-Layout OG entwerfen", "status": "needs_action"},
                ]
            }
        },
    )

    entry = ConfigEntry(
        entry_id="sunday_rule_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_CONTEXT_TODOS: ["todo.hausbau_anytype"],
            CONF_HORIZON_DAYS: 7,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_PROJECT_BASELINE_PER_ITEM: 2.0,
            CONF_PROJECT_BASELINE_CAP: 15,
            CONF_PROJECT_PLANNING_ENABLED: True,
            CONF_MAX_PROJECT_STEPS_WEEKEND: 2,
            CONF_MAX_PROJECT_STEPS_WEEKDAY: 1,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    # 2026-11-28 is Saturday -> max 2 steps (can include open business / noisy work)
    saturday = coord.data["2026-11-28"]
    sat_steps = [t for t in saturday.hidden_tasks if t.category == "project_step"]
    assert len(sat_steps) <= 2

    # 2026-11-29 is Sunday -> strictly NO open business, calls, shopping, or noisy work!
    sunday = coord.data["2026-11-29"]
    sun_steps = [t for t in sunday.hidden_tasks if t.category == "project_step"]
    assert len(sun_steps) <= 2
    for step in sun_steps:
        assert step.requires_open_business is False
        assert step.is_noisy is False
        lowered = f"{step.title} {step.parent_event_summary}".lower()
        assert "schleifen" not in lowered
        assert "baumarkt" not in lowered

    # Calm weekday steps only start at or after 17:00
    for day in coord.data.values():
        if day.date.weekday() < 5:
            wd_steps = [t for t in day.hidden_tasks if t.category == "project_step"]
            assert len(wd_steps) <= 1
            for step in wd_steps:
                assert step.start.hour >= 17


async def test_external_calendar_sync_deletes_stale_badges_and_tasks() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)

    def _cal_handler(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("entity_id") == "calendar.mental_load_2":
            return {
                "calendar.mental_load_2": {
                    "events": [
                        {
                            "summary": "🟢 3% Mental Load (Niedrig) – Alt",
                            "start": today.isoformat(),
                            "end": (today + datetime.timedelta(days=1)).isoformat(),
                            "description": f"Veraltet\n\n[ML-BADGE-{today.isoformat()}]",
                        },
                        {
                            "summary": "🧠 Veraltete Aufgabe",
                            "start": f"{today.isoformat()}T08:00:00+01:00",
                            "end": f"{today.isoformat()}T08:20:00+01:00",
                            "description": "Alt\n\n[ML-TASK-olddeadbeef]",
                        },
                        {
                            "summary": "Privater Kalendereintrag",
                            "start": f"{today.isoformat()}T12:00:00+01:00",
                            "end": f"{today.isoformat()}T13:00:00+01:00",
                            "description": "Manuell erstellt",
                        },
                    ]
                }
            }
        return {}

    hass.services.set_response("calendar", "get_events", _cal_handler)

    entry = ConfigEntry(
        entry_id="cleanup_entry",
        data={
            CONF_CALENDARS: ["calendar.google_source"],
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_AUTO_SYNC_CALENDAR: True,
            CONF_TARGET_CALENDAR_ENTITY: "calendar.mental_load_2",
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    coord.data = {today.isoformat(): _make_high_load_analysis(today, score=82)}

    await coord.async_sync_to_external_calendar("calendar.mental_load_2")
    delete_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "delete_event"]
    deleted_summaries = {c[2]["summary"] for c in delete_calls}
    assert deleted_summaries == {"🟢 3% Mental Load (Niedrig) – Alt", "🧠 Veraltete Aufgabe"}
    assert "Privater Kalendereintrag" not in deleted_summaries


async def test_project_priority_baseline_and_scheduling_order() -> None:
    ref = datetime.date(2026, 11, 23)
    assert extract_todo_priority("Task A", "Prio: Hoch | Status: To Do", reference_date=ref) == (1, "high")
    assert extract_todo_priority("Task B", "Status: To Do", due="2026-11-27", reference_date=ref) == (1, "high")
    assert extract_todo_priority("Task C", "Status: In Progress", reference_date=ref) == (2, "medium")
    assert extract_todo_priority("Task D", "Status: To Do", reference_date=ref) == (3, "normal")
    assert extract_todo_priority("Task E", "Prio: Niedrig | Status: To Do", reference_date=ref) == (4, "low")
    # Explicit Prio: Niedrig overrides Status: In Progress and an older due date
    assert extract_todo_priority("Luftreiniger", "Prio: Niedrig | Status: In Progress", due="2026-03-30", reference_date=ref) == (4, "low")
    assert extract_todo_priority("OG: Fussboden", "Prio: Mittel | Status: In Progress", due="2026-04-02", reference_date=ref) == (2, "medium")

    cfg = {CONF_PROJECT_BASELINE_PER_ITEM: 2.0, CONF_PROJECT_BASELINE_CAP: 15}
    weighted_items = [
        {"summary": "A", "description": "Prio: Hoch"},
        {"summary": "B", "description": "Prio: Hoch"},
        {"summary": "C", "description": "Prio: Niedrig"},
    ]
    # 2 * 3% + 1 * 1% = 7%
    assert compute_project_baseline(3, cfg, project_items=weighted_items) == 7
    many_high = [{"summary": f"H{i}", "description": "Prio: Hoch"} for i in range(6)]
    assert compute_project_baseline(6, cfg, project_items=many_high) == 15

    hass = FakeHass()
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": []}})
    hass.services.set_response(
        "todo",
        "get_items",
        {
            "todo.hausbau_anytype": {
                "items": [
                    {"summary": "Garten Schuppen aufräumen", "description": "Prio: Niedrig | Status: To Do", "status": "needs_action"},
                    {"summary": "Zählerkasten Doku", "description": "Status: To Do", "status": "needs_action"},
                    {"summary": "OG: Elektrik Schaltplan", "description": "Status: In Progress", "status": "needs_action"},
                    {"summary": "Dachfenster Notabdichtung", "description": "Prio: Hoch | Status: To Do", "status": "needs_action"},
                ]
            }
        },
    )

    entry = ConfigEntry(
        entry_id="priority_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_CONTEXT_TODOS: ["todo.hausbau_anytype"],
            CONF_HORIZON_DAYS: 10,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_PROJECT_BASELINE_PER_ITEM: 2.0,
            CONF_PROJECT_BASELINE_CAP: 15,
            CONF_PROJECT_PLANNING_ENABLED: True,
            CONF_MAX_PROJECT_STEPS_WEEKEND: 2,
            CONF_MAX_PROJECT_STEPS_WEEKDAY: 1,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    # Monday 2026-11-23 is the earliest slot -> must go to Prio: Hoch even though it was last in input list
    mon_steps = [t for t in coord.data["2026-11-23"].hidden_tasks if t.category == "project_step"]
    assert len(mon_steps) == 1
    assert "Dachfenster Notabdichtung" in mon_steps[0].parent_event_summary

    # Collect chronological sequence of distinct scheduled projects across the horizon
    ordered_parents: list[str] = []
    for day_key in sorted(coord.data.keys()):
        for step in coord.data[day_key].hidden_tasks:
            if step.category == "project_step" and step.parent_event_summary not in ordered_parents:
                ordered_parents.append(step.parent_event_summary)

    assert ordered_parents[0] == "Dachfenster Notabdichtung"
    assert ordered_parents.index("OG: Elektrik Schaltplan") < ordered_parents.index("Zählerkasten Doku")
    assert ordered_parents.index("Zählerkasten Doku") < ordered_parents.index("Garten Schuppen aufräumen")
