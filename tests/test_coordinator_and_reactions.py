"""Tests for MentalLoadCoordinator: caching, feedback learning, todo sync, and reactions."""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntry

from custom_components.mental_load.const import (
    CONF_AUTO_PUSH_TODO,
    CONF_CALENDARS,
    CONF_CRITICAL_THRESHOLD,
    CONF_HORIZON_DAYS,
    CONF_NOTIFY_SERVICE,
    CONF_PROVIDER,
    CONF_REACTION_ENTITY,
    CONF_REACTION_ON_CRITICAL,
    CONF_TARGET_TODO_ENTITY,
    CONF_WARNING_THRESHOLD,
    EVENT_THRESHOLD_EXCEEDED,
    PROVIDER_HEURISTIC,
)
from custom_components.mental_load.coordinator import MentalLoadCoordinator
from custom_components.mental_load.models import DayLoadAnalysis, HiddenTask
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
