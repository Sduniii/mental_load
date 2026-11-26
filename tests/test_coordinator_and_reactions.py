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
    CONF_CHILDREN_CALENDARS,
    CONF_CHILDREN_PROFILES,
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

    # Only today has calendar events; tomorrow is empty and uses 0-token fast_empty_day path
    assert mock_llm.async_analyze_day.call_count == 1
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
    assert mock_llm.async_analyze_day.call_count == 1
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

    # Writing to dedicated secondary Google Calendar skips completed hidden task (only day badge syncs)
    synced_done = await coord.async_sync_to_external_calendar("calendar.google_mental_load")
    assert synced_done == 1  # 1 day badge only; completed hidden task omitted

    # Un-toggling reopens the task, so it syncs to the external calendar cleanly & deduplicates
    await coord.async_toggle_checklist_item("task_uid_1:step:0", completed=False)
    synced_open = await coord.async_sync_to_external_calendar("calendar.google_mental_load")
    assert synced_open == 1  # 1 reopened hidden task (badge already synced)
    create_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "create_event"]
    assert len(create_calls) == 2
    assert create_calls[0][2]["entity_id"] == "calendar.google_mental_load"

    # Subsequent sync does not create duplicates
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
                        "uid": "today_busy",
                        "summary": "Ganztägiger Workshop",
                        "start": f"{today.isoformat()}T09:00:00+01:00",
                        "end": f"{today.isoformat()}T17:00:00+01:00",
                    },
                    {
                        "uid": "osteo_1",
                        "summary": "Osteopathie",
                        "start": f"{wednesday.isoformat()}T10:00:00+01:00",
                        "end": f"{wednesday.isoformat()}T11:00:00+01:00",
                    },
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

    # 30 days generated, yet LLM called only for major_day (1 call instead of 30; empty days incl. today/tomorrow cost 0 tokens)
    assert len(coord.data) == 30
    assert mock_llm.async_analyze_day.call_count == 1

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


async def test_children_calendar_fetching_and_tagging() -> None:
    hass = FakeHass()
    today = datetime.date(2026, 11, 23)

    def _cal_handler(payload: dict[str, Any]) -> dict[str, Any]:
        entities = payload.get("entity_id") or []
        resp: dict[str, Any] = {}
        if "calendar.parent" in entities:
            resp["calendar.parent"] = {"events": []}
        if "calendar.stundenplan" in entities:
            resp["calendar.stundenplan"] = {
                "events": [
                    {
                        "summary": "Schwimmen",
                        "start": f"{today.isoformat()}T10:00:00+01:00",
                        "end": f"{today.isoformat()}T11:30:00+01:00",
                    }
                ]
            }
        return resp

    hass.services.set_response("calendar", "get_events", _cal_handler)
    entry = ConfigEntry(
        entry_id="children_cal_entry",
        data={
            CONF_CALENDARS: ["calendar.parent"],
            CONF_CHILDREN_CALENDARS: ["calendar.stundenplan"],
            CONF_CHILDREN_PROFILES: "Kind: 14.05.2018 (Grundschule)",
            CONF_HORIZON_DAYS: 2,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    cal_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "get_events"]
    assert "calendar.stundenplan" in cal_calls[0][2]["entity_id"]
    today_tasks = coord.data[today.isoformat()].hidden_tasks
    assert any("Schwimmen" in t.title and t.category == "packing" for t in today_tasks)


def test_fingerprint_stability_against_weather_and_energy_jitter() -> None:
    hass = FakeHass()
    entry = ConfigEntry(
        entry_id="fp_entry",
        data={CONF_CALENDARS: ["calendar.google"], CONF_HORIZON_DAYS: 5, CONF_PROVIDER: PROVIDER_HEURISTIC},
    )
    coord = MentalLoadCoordinator(hass, entry)
    cfg = coord.merged_config

    ctx_a = {"weather": "cloudy, 14.2°", "energy_sensor": "62 %"}
    ctx_b = {"weather": "cloudy, 14.9°", "energy_sensor": "64 %"}
    ctx_c = {"weather": "rainy, 8.0°", "energy_sensor": "20 %"}

    # Offset <= 1 (include_live_context=True): temperature jitter & small energy drift keep same fingerprint
    assert coord._compute_day_fingerprint("2026-11-23", [], ctx_a, cfg, include_live_context=True) == coord._compute_day_fingerprint(
        "2026-11-23", [], ctx_b, cfg, include_live_context=True
    )
    # Major weather condition change or energy bucket drop DOES invalidate offset <= 1
    assert coord._compute_day_fingerprint("2026-11-23", [], ctx_a, cfg, include_live_context=True) != coord._compute_day_fingerprint(
        "2026-11-23", [], ctx_c, cfg, include_live_context=True
    )
    # Offset >= 2 (include_live_context=False): even dramatic weather/energy changes NEVER invalidate future day cache
    assert coord._compute_day_fingerprint("2026-11-26", [], ctx_a, cfg, include_live_context=False) == coord._compute_day_fingerprint(
        "2026-11-26", [], ctx_c, cfg, include_live_context=False
    )


async def test_non_blocking_setup_seeds_cache_immediately(monkeypatch: Any) -> None:
    from custom_components.mental_load import async_setup_entry
    from custom_components.mental_load.const import DOMAIN
    from custom_components.mental_load.models import DayLoadAnalysis

    hass = FakeHass()
    today_str = datetime.date(2026, 11, 23).isoformat()
    entry = ConfigEntry(
        entry_id="seed_entry",
        data={CONF_CALENDARS: ["calendar.google"], CONF_HORIZON_DAYS: 2, CONF_PROVIDER: PROVIDER_HEURISTIC},
    )

    seeded_day = DayLoadAnalysis(
        date=datetime.date(2026, 11, 23),
        score=55,
        level="medium",
        badge_summary="🟡 55% Cached",
        reasoning="From cache",
        factor_breakdown={"pacing_context": 55},
    )

    class _PreseededStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def async_load(self) -> dict[str, Any]:
            return {"cache": {today_str: {"fingerprint": "abc", "analysis": seeded_day.to_dict()}}}

        async def async_save(self, data: dict[str, Any]) -> None:
            pass

    monkeypatch.setattr("custom_components.mental_load.coordinator.Store", _PreseededStore)

    scheduled: list[str] = []

    def _fake_bg_task(_hass: Any, coro: Any, name: str) -> None:
        scheduled.append(name)
        coro.close()

    entry.async_create_background_task = _fake_bg_task  # type: ignore[attr-defined]
    entry.async_on_unload = lambda cb: cb  # type: ignore[attr-defined]
    entry.add_update_listener = lambda fn: fn  # type: ignore[attr-defined]
    forward_mock = AsyncMock()
    hass.config_entries = type("CE", (), {"async_forward_entry_setups": forward_mock})()

    ok = await async_setup_entry(hass, entry)
    assert ok is True
    coord = hass.data[DOMAIN][entry.entry_id]
    assert today_str in coord.data
    assert coord.data[today_str].score == 55
    assert coord.data[today_str].cached is True
    # Warm/pre-seeded cache must skip immediate background LLM regeneration on reload
    assert scheduled == []
    forward_mock.assert_awaited_once()


async def test_cold_setup_schedules_initial_refresh(monkeypatch: Any) -> None:
    from custom_components.mental_load import async_setup_entry
    from custom_components.mental_load.const import DOMAIN

    hass = FakeHass()
    entry = ConfigEntry(
        entry_id="cold_seed_entry",
        data={CONF_CALENDARS: ["calendar.google"], CONF_HORIZON_DAYS: 2, CONF_PROVIDER: PROVIDER_HEURISTIC},
    )

    class _EmptyStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def async_load(self) -> dict[str, Any] | None:
            return None

        async def async_save(self, data: dict[str, Any]) -> None:
            pass

    monkeypatch.setattr("custom_components.mental_load.coordinator.Store", _EmptyStore)

    scheduled: list[str] = []

    def _fake_bg_task(_hass: Any, coro: Any, name: str) -> None:
        scheduled.append(name)
        coro.close()

    entry.async_create_background_task = _fake_bg_task  # type: ignore[attr-defined]
    entry.async_on_unload = lambda cb: cb  # type: ignore[attr-defined]
    entry.add_update_listener = lambda fn: fn  # type: ignore[attr-defined]
    forward_mock = AsyncMock()
    hass.config_entries = type("CE", (), {"async_forward_entry_setups": forward_mock})()

    ok = await async_setup_entry(hass, entry)
    assert ok is True
    assert scheduled == [f"{DOMAIN}_initial_refresh_{entry.entry_id}"]
    forward_mock.assert_awaited_once()


async def test_antigravity_cli_uses_batch_size_4() -> None:
    from custom_components.mental_load.const import PROVIDER_ANTIGRAVITY_CLI
    from custom_components.mental_load.models import DayLoadAnalysis

    hass = FakeHass()
    base_date = datetime.date(2026, 11, 23)
    events = [
        {
            "summary": f"Termin {i}",
            "start": f"{(base_date + datetime.timedelta(days=i)).isoformat()}T10:00:00+01:00",
            "end": f"{(base_date + datetime.timedelta(days=i)).isoformat()}T11:00:00+01:00",
        }
        for i in range(6)
    ]
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": events}})

    batch_sizes: list[int] = []

    async def _fake_batch(days_items: list[Any], cfg: dict[str, Any], fb: list[Any]) -> dict[str, DayLoadAnalysis]:
        batch_sizes.append(len(days_items))
        return {
            d_date.isoformat(): DayLoadAnalysis(
                date=d_date,
                score=42,
                level="medium",
                badge_summary="🟡 42%",
                reasoning="ok",
                factor_breakdown={},
                provider_used=PROVIDER_ANTIGRAVITY_CLI,
            )
            for d_date, _, _, _ in days_items
        }

    entry = ConfigEntry(
        entry_id="agy_batch_entry",
        data={CONF_CALENDARS: ["calendar.google"], CONF_HORIZON_DAYS: 6, CONF_PROVIDER: PROVIDER_ANTIGRAVITY_CLI},
    )
    coord = MentalLoadCoordinator(hass, entry)
    coord.llm_client = type("StubLLM", (), {"async_analyze_days_batch": staticmethod(_fake_batch)})()  # type: ignore[assignment]
    await coord.async_config_entry_first_refresh()

    assert batch_sizes == [4, 2]


async def test_push_hidden_tasks_falls_back_from_due_datetime_to_due_date() -> None:
    from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask

    hass = FakeHass()
    seen_payloads: list[dict[str, Any]] = []

    async def _todo_service(domain: str, service: str, data: dict[str, Any], blocking: bool = False, return_response: bool = False) -> dict[str, Any]:
        hass.services.calls.append((domain, service, data, return_response))
        if domain == "todo" and service == "add_item":
            seen_payloads.append(dict(data))
            if "due_datetime" in data:
                raise ValueError("Entity does not support setting field: due_datetime")
        return {}

    hass.services.async_call = _todo_service  # type: ignore[method-assign]

    entry = ConfigEntry(
        entry_id="todo_fallback_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_TARGET_TODO_ENTITY: "todo.zuhause",
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    tz = datetime.timezone(datetime.timedelta(hours=1))
    day = datetime.date(2026, 11, 23)
    task = HiddenTask(
        uid="task_due_fb",
        parent_event_summary="Sport",
        title="🎒 Einpacken: Sportbeutel packen",
        category="packing",
        start=datetime.datetime(2026, 11, 23, 8, 0, tzinfo=tz),
        end=datetime.datetime(2026, 11, 23, 8, 15, tzinfo=tz),
        checklist=["Turnschuhe"],
    )
    analysis = DayLoadAnalysis(
        date=day,
        score=30,
        level="low",
        badge_summary="🟢 30%",
        reasoning="ok",
        factor_breakdown={},
        hidden_tasks=[task],
    )
    pushed = await coord.async_push_hidden_tasks_to_todo(
        target_date=day.isoformat(),
        todo_entity="todo.zuhause",
        analyses={day.isoformat(): analysis},
        save_after=False,
    )
    assert pushed == 1
    assert len(seen_payloads) == 2
    assert "due_datetime" in seen_payloads[0]
    assert seen_payloads[1]["due_date"] == "2026-11-23"
    assert "due_datetime" not in seen_payloads[1]


async def test_external_calendar_sync_heals_missing_and_same_score_updated_badges() -> None:
    from custom_components.mental_load.models import DayLoadAnalysis

    hass = FakeHass()
    d1 = datetime.date(2026, 11, 26)
    d2 = datetime.date(2026, 11, 27)

    new_d1 = DayLoadAnalysis(
        date=d1,
        score=47,
        level="medium",
        badge_summary="🟡 47% Mental Load (Mittel) – Neue Zusammenfassung, 1 Kopf-Aufgabe",
        reasoning="Updated reasoning",
        factor_breakdown={},
    )
    new_d2 = DayLoadAnalysis(
        date=d2,
        score=45,
        level="medium",
        badge_summary="🟡 45% Mental Load (Mittel) – Adventsbasteln, 3 Kopf-Aufgaben",
        reasoning="Day 2 reasoning",
        factor_breakdown={},
    )

    def _cal_handler(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("entity_id") == "calendar.mental_load_2":
            return {
                "calendar.mental_load_2": {
                    "events": [
                        {
                            "summary": "🟡 47% Mental Load (Mittel) – Alte Zusammenfassung, 0 Kopf-Aufgaben",
                            "start": {"date": d1.isoformat()},
                            "end": {"date": (d1 + datetime.timedelta(days=1)).isoformat()},
                            "description": f"Veraltet\n\n[ML-BADGE-{d1.isoformat()}]",
                        }
                    ]
                }
            }
        return {}

    hass.services.set_response("calendar", "get_events", _cal_handler)

    entry = ConfigEntry(
        entry_id="heal_sync_entry",
        data={
            CONF_CALENDARS: ["calendar.google_source"],
            CONF_HORIZON_DAYS: 2,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_AUTO_SYNC_CALENDAR: True,
            CONF_TARGET_CALENDAR_ENTITY: "calendar.mental_load_2",
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    coord.data = {d1.isoformat(): new_d1, d2.isoformat(): new_d2}
    # Simulate stale markers that previously caused deleted/missing badges to stay skipped
    coord._synced_calendar_uids = {
        f"badge:calendar.mental_load_2:{d1.isoformat()}:47",
        f"badge:calendar.mental_load_2:{d2.isoformat()}:45",
    }

    await coord.async_sync_to_external_calendar("calendar.mental_load_2")

    delete_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "delete_event"]
    create_calls = [c for c in hass.services.calls if c[0] == "calendar" and c[1] == "create_event"]
    assert {c[2]["summary"] for c in delete_calls} == {
        "🟡 47% Mental Load (Mittel) – Alte Zusammenfassung, 0 Kopf-Aufgaben"
    }
    assert {c[2]["summary"] for c in create_calls} == {
        new_d1.badge_summary,
        new_d2.badge_summary,
    }


async def test_member_calendars_and_boot_cache_guard(monkeypatch: Any) -> None:
    from custom_components.mental_load.const import (
        CONF_HOUSEHOLD_MEMBERS,
        MEMBER_ROLE_ADULT,
        MEMBER_ROLE_CHILD,
    )
    from custom_components.mental_load.models import DayLoadAnalysis

    hass = FakeHass()
    today = datetime.date(2026, 11, 24)
    tz = datetime.timezone(datetime.timedelta(hours=1))

    def _cal_handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "calendar.family": {"events": []},
            "calendar.partner": {
                "events": [
                    {
                        "summary": "Schichtarbeit",
                        "start": f"{today.isoformat()}T13:00:00+01:00",
                        "end": f"{today.isoformat()}T21:00:00+01:00",
                    }
                ]
            },
            "calendar.leo": {
                "events": [
                    {
                        "summary": "Schwimmen",
                        "start": f"{today.isoformat()}T10:00:00+01:00",
                        "end": f"{today.isoformat()}T11:30:00+01:00",
                    }
                ]
            },
        }

    hass.services.set_response("calendar", "get_events", _cal_handler)
    entry = ConfigEntry(
        entry_id="members_entry",
        data={
            CONF_CALENDARS: ["calendar.family"],
            CONF_HORIZON_DAYS: 2,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_HOUSEHOLD_MEMBERS: [
                {
                    "id": "adult_1",
                    "role": MEMBER_ROLE_ADULT,
                    "name": "Elternteil 1",
                    "calendars": [],
                    "profile": "Homeoffice Vormittag",
                },
                {
                    "id": "adult_2",
                    "role": MEMBER_ROLE_ADULT,
                    "name": "Partner",
                    "calendars": ["calendar.partner"],
                    "profile": "Spätschicht",
                },
                {
                    "id": "child_1",
                    "role": MEMBER_ROLE_CHILD,
                    "name": "Leo",
                    "birthdate": "14.05.2018",
                    "calendars": ["calendar.leo"],
                    "profile": "Turnbeutel sonntags packen",
                },
            ],
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    start_dt = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
    end_dt = datetime.datetime.combine(today + datetime.timedelta(days=2), datetime.time.min, tzinfo=tz)
    by_day = await coord._async_fetch_events_by_day(start_dt, end_dt, 2, today, tz)

    todays = by_day[today.isoformat()]
    assert any(ev.source_calendar == "calendar.partner [Erwachsener: Partner]" for ev in todays)
    assert any(ev.source_calendar == "calendar.leo [Stundenplan/Kind: Leo]" for ev in todays)

    # Boot cache guard: failing calendar.get_events must NOT overwrite existing sensor data with 0%
    existing = {
        today.isoformat(): DayLoadAnalysis(
            date=today, score=55, level="medium", badge_summary="🟡 55%", reasoning="Cached", factor_breakdown={}
        )
    }
    coord.data = existing

    async def _failing_cal(*args: Any, **kwargs: Any) -> Any:
        if args[0] == "calendar" and args[1] == "get_events":
            raise RuntimeError("Service unavailable")
        return {}

    hass.services.async_call = _failing_cal  # type: ignore[method-assign]
    returned = await coord._async_update_data()
    assert returned is existing
    assert coord._last_calendar_fetch_failed is True


async def test_completed_task_survives_ai_rewording_without_repush_or_extra_tokens() -> None:
    from custom_components.mental_load.coordinator import make_checklist_item_uid, make_stable_task_key
    from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask

    hass = FakeHass()
    day = datetime.date(2026, 11, 26)
    tz = datetime.timezone(datetime.timedelta(hours=1))
    hass.services.set_response("calendar", "get_events", {"calendar.google": {"events": []}})

    entry = ConfigEntry(
        entry_id="stable_done_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_TARGET_TODO_ENTITY: "todo.zuhause",
            CONF_TARGET_CALENDAR_ENTITY: "calendar.mental_load_2",
        },
    )
    coord = MentalLoadCoordinator(hass, entry)

    # Same event + category, different AI-worded title & different uid on re-analysis
    orig_task = HiddenTask(
        uid="uid_v1",
        parent_event_summary="Schwimmen",
        title="🎒 Schwimmbeutel packen",
        category="packing",
        start=datetime.datetime(2026, 11, 26, 7, 30, tzinfo=tz),
        end=datetime.datetime(2026, 11, 26, 7, 45, tzinfo=tz),
        load_contribution=20,
        checklist=["Badehose einpacken"],
        parent_event_date=day.isoformat(),
        scheduled_date=day.isoformat(),
    )
    reworded_task = HiddenTask(
        uid="uid_v2_reworded",
        parent_event_summary="Schwimmen",
        title="🎒 Badesachen & Handtuch einpacken",
        category="packing",
        start=datetime.datetime(2026, 11, 26, 7, 30, tzinfo=tz),
        end=datetime.datetime(2026, 11, 26, 7, 45, tzinfo=tz),
        load_contribution=20,
        checklist=["Handtuch einpacken"],
        parent_event_date=day.isoformat(),
        scheduled_date=day.isoformat(),
    )
    assert make_stable_task_key(orig_task) == make_stable_task_key(reworded_task)

    coord.data = {
        day.isoformat(): DayLoadAnalysis(
            date=day,
            score=40,
            base_score=40,
            level="medium",
            badge_summary="🟡 40%",
            reasoning="Schwimmen",
            factor_breakdown={},
            hidden_tasks=[orig_task],
        )
    }

    fp_before = coord._compute_day_fingerprint(day.isoformat(), [], {}, entry.data, include_live_context=True)
    await coord.async_toggle_checklist_item(make_checklist_item_uid("uid_v1", 0), completed=True)
    fp_after = coord._compute_day_fingerprint(day.isoformat(), [], {}, entry.data, include_live_context=True)
    assert fp_before == fp_after

    # Simulate AI returning reworded task with a new UID
    rebalanced = coord._rebalance_and_score_horizon(
        {
            day.isoformat(): DayLoadAnalysis(
                date=day,
                score=40,
                base_score=40,
                level="medium",
                badge_summary="🟡 40%",
                reasoning="Schwimmen",
                factor_breakdown={},
                hidden_tasks=[reworded_task],
            )
        },
        day,
        tz,
    )
    assert make_checklist_item_uid("uid_v2_reworded", 0) in coord.completed_item_uids
    assert rebalanced[day.isoformat()].relief_points > 0

    pushed = await coord.async_push_hidden_tasks_to_todo(
        target_date=day.isoformat(),
        todo_entity="todo.zuhause",
        analyses=rebalanced,
        save_after=False,
    )
    assert pushed == 0

    hass.services.calls.clear()
    await coord.async_sync_to_external_calendar("calendar.mental_load_2", analyses=rebalanced, save_after=False)
    created_summaries = [
        c[2]["summary"] for c in hass.services.calls if c[0] == "calendar" and c[1] == "create_event"
    ]
    assert all("Badesachen" not in s and "Schwimmbeutel" not in s for s in created_summaries)


async def test_completed_item_in_target_todo_syncs_back_and_blocks_recreation() -> None:
    from custom_components.mental_load.coordinator import make_checklist_item_uid
    from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask

    hass = FakeHass()
    day = datetime.date(2026, 11, 26)
    tz = datetime.timezone(datetime.timedelta(hours=1))

    task_v1 = HiddenTask(
        uid="todo_uid_1",
        parent_event_summary="Zahnarzt",
        title="🧠 Unterlagen bereitlegen",
        category="prep",
        start=datetime.datetime(2026, 11, 26, 8, 0, tzinfo=tz),
        end=datetime.datetime(2026, 11, 26, 8, 15, tzinfo=tz),
        load_contribution=15,
        checklist=["Versichertenkarte"],
        parent_event_date=day.isoformat(),
        scheduled_date=day.isoformat(),
    )
    task_v2 = HiddenTask(
        uid="todo_uid_2",
        parent_event_summary="Zahnarzt",
        title="🧠 Impfpass & Karte einpacken",
        category="prep",
        start=datetime.datetime(2026, 11, 26, 8, 0, tzinfo=tz),
        end=datetime.datetime(2026, 11, 26, 8, 15, tzinfo=tz),
        load_contribution=15,
        checklist=["Karte einpacken"],
        parent_event_date=day.isoformat(),
        scheduled_date=day.isoformat(),
    )

    hass.services.set_response(
        "todo",
        "get_items",
        {
            "todo.zuhause": {
                "items": [
                    {
                        "summary": "🧠 Unterlagen bereitlegen (Zahnarzt)",
                        "status": "completed",
                        "description": "- [ ] Versichertenkarte\n\n[ML-todo_uid_1]",
                    }
                ]
            }
        },
    )

    entry = ConfigEntry(
        entry_id="todo_backsync_entry",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 1,
            CONF_PROVIDER: PROVIDER_HEURISTIC,
            CONF_AUTO_PUSH_TODO: True,
            CONF_TARGET_TODO_ENTITY: "todo.zuhause",
        },
    )
    coord = MentalLoadCoordinator(hass, entry)
    analyses = {
        day.isoformat(): DayLoadAnalysis(
            date=day,
            score=35,
            base_score=35,
            level="medium",
            badge_summary="🟡 35%",
            reasoning="Zahnarzt",
            factor_breakdown={},
            hidden_tasks=[task_v1],
        )
    }

    synced = await coord._async_sync_from_target_todo("todo.zuhause", analyses)
    assert synced is True
    assert make_checklist_item_uid("todo_uid_1", 0) in coord.completed_item_uids

    coord._apply_dynamic_completion_relief(analyses)
    assert analyses[day.isoformat()].relief_points > 0

    pushed = await coord.async_push_hidden_tasks_to_todo(
        target_date=day.isoformat(),
        todo_entity="todo.zuhause",
        analyses={
            day.isoformat(): DayLoadAnalysis(
                date=day,
                score=35,
                base_score=35,
                level="medium",
                badge_summary="🟡 35%",
                reasoning="Zahnarzt",
                factor_breakdown={},
                hidden_tasks=[task_v2],
            )
        },
        save_after=False,
    )
    assert pushed == 0
