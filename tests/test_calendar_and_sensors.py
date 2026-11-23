"""Tests for calendar.mental_load entity and sensor entities."""
from __future__ import annotations

import datetime

from homeassistant.config_entries import ConfigEntry

from custom_components.mental_load.calendar import MentalLoadCalendarEntity
from custom_components.mental_load.const import CONF_CALENDARS, CONF_HORIZON_DAYS, CONF_SHOW_HIDDEN_TASK_BLOCKS
from custom_components.mental_load.coordinator import MentalLoadCoordinator
from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask
from custom_components.mental_load.sensor import (
    MentalLoadDaySensor,
    MentalLoadHiddenTasksSensor,
    MentalLoadPeakDaySensor,
    MentalLoadWeekAverageSensor,
)
from tests.conftest import FakeHass


def _build_populated_coordinator() -> tuple[MentalLoadCoordinator, ConfigEntry]:
    hass = FakeHass()
    entry = ConfigEntry(
        entry_id="cal_test",
        data={
            CONF_CALENDARS: ["calendar.google"],
            CONF_HORIZON_DAYS: 2,
            CONF_SHOW_HIDDEN_TASK_BLOCKS: True,
        },
        title="Mental Load",
    )
    coord = MentalLoadCoordinator(hass, entry)
    tz = datetime.timezone(datetime.timedelta(hours=1))
    d1 = datetime.date(2026, 11, 23)
    d2 = datetime.date(2026, 11, 24)
    coord.data = {
        d1.isoformat(): DayLoadAnalysis(
            date=d1,
            score=76,
            level="high",
            badge_summary="🟠 76% Mental Load (Hoch) – Kindergeburtstag, 1 Kopf-Aufgabe",
            reasoning="Geburtstag braucht Vorbereitung.",
            factor_breakdown={
                "pacing_context": 70,
                "social_battery": 80,
                "mobility": 65,
                "ha_context": 60,
                "hidden_work": 85,
            },
            hidden_tasks=[
                HiddenTask(
                    uid="ht1",
                    parent_event_summary="Kindergeburtstag",
                    title="🎒 Mitbringsel & Geschenk",
                    category="packing",
                    start=datetime.datetime(2026, 11, 23, 14, 0, tzinfo=tz),
                    end=datetime.datetime(2026, 11, 23, 14, 30, tzinfo=tz),
                    load_contribution=16,
                    checklist=["Geschenk einpacken", "Karte schreiben"],
                )
            ],
            recommendations=["Geschenk morgens bereitlegen"],
            top_stressor="Kindergeburtstag",
            provider_used="openai",
        ),
        d2.isoformat(): DayLoadAnalysis(
            date=d2,
            score=30,
            level="low",
            badge_summary="🟢 30% Mental Load (Niedrig) – Ruhiger Tag",
            reasoning="Wenig los.",
            factor_breakdown={
                "pacing_context": 25,
                "social_battery": 30,
                "mobility": 20,
                "ha_context": 35,
                "hidden_work": 25,
            },
            hidden_tasks=[],
            recommendations=[],
            top_stressor="Ruhiger Tag",
            provider_used="openai",
        ),
    }
    return coord, entry


async def test_calendar_entity_returns_badges_and_hidden_task_blocks() -> None:
    coord, entry = _build_populated_coordinator()
    cal = MentalLoadCalendarEntity(coord, entry)

    assert cal.event is not None
    assert "🟠 76% Mental Load" in cal.event.summary

    tz = datetime.timezone(datetime.timedelta(hours=1))
    events = await cal.async_get_events(
        coord.hass,
        datetime.datetime(2026, 11, 23, 0, 0, tzinfo=tz),
        datetime.datetime(2026, 11, 25, 0, 0, tzinfo=tz),
    )
    # 2 all-day badges + 1 timed invisible head-task block
    assert len(events) == 3
    summaries = [e.summary for e in events]
    assert any("🟠 76% Mental Load" in s for s in summaries)
    assert any("🎒 Mitbringsel & Geschenk (+16%)" in s for s in summaries)
    assert any("🟢 30% Mental Load" in s for s in summaries)


def test_sensors_expose_scores_factors_and_hidden_tasks() -> None:
    coord, entry = _build_populated_coordinator()
    today_s = MentalLoadDaySensor(coord, entry, 0, "today", "Mental Load Today")
    tomorrow_s = MentalLoadDaySensor(coord, entry, 1, "tomorrow", "Mental Load Tomorrow")
    week_s = MentalLoadWeekAverageSensor(coord, entry)
    peak_s = MentalLoadPeakDaySensor(coord, entry)
    hidden_s = MentalLoadHiddenTasksSensor(coord, entry)

    assert today_s.native_value == 76
    assert today_s.extra_state_attributes["top_stressor"] == "Kindergeburtstag"
    assert tomorrow_s.native_value == 30
    assert week_s.native_value == 53
    assert week_s.extra_state_attributes["peak_date"] == "2026-11-23"
    assert peak_s.native_value == "2026-11-23 (76%)"
    assert hidden_s.native_value == 1
    assert "Geschenk einpacken" in hidden_s.extra_state_attributes["checklist_markdown"]


async def test_integration_setup_and_config_flow_import() -> None:
    import custom_components.mental_load as ml_init
    from custom_components.mental_load import config_flow

    hass = FakeHass()
    assert await ml_init.async_setup(hass, {}) is True
    assert hass.services.has_service("mental_load", "refresh")
    assert hass.services.has_service("mental_load", "submit_feedback")
    assert hass.services.has_service("mental_load", "push_hidden_tasks")
    assert hass.services.has_service("mental_load", "toggle_checklist_item")
    assert hass.services.has_service("mental_load", "sync_external_calendar")

    flow = config_flow.MentalLoadConfigFlow()
    assert flow.VERSION == 1
    opt_flow = config_flow.MentalLoadConfigFlow.async_get_options_flow(
        ConfigEntry(entry_id="x", data={})
    )
    assert opt_flow is not None


async def test_native_todo_entity_and_ical_feed() -> None:
    from homeassistant.components.todo import TodoItemStatus
    from custom_components.mental_load.ical_view import build_ical_payload
    from custom_components.mental_load.todo import MentalLoadTodoListEntity

    coord, entry = _build_populated_coordinator()
    todo_ent = MentalLoadTodoListEntity(coord, entry)

    items = todo_ent.todo_items
    assert len(items) == 2
    assert all(i.status == TodoItemStatus.NEEDS_ACTION for i in items)

    # Toggle first checklist item through TodoListEntity
    first_item = items[0]
    first_item.status = TodoItemStatus.COMPLETED
    await todo_ent.async_update_todo_item(first_item)
    assert todo_ent.todo_items[0].status == TodoItemStatus.COMPLETED

    # Verify iCal RFC 5545 output reflects [x] completion and badges
    ics = build_ical_payload([coord])
    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "[x] Geschenk einpacken" in ics
    assert "Mental Load" in ics
