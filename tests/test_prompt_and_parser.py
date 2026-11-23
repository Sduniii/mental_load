"""Tests for 5-factor prompt builder, JSON parsing, and invisible task timing."""
from __future__ import annotations

import datetime

from custom_components.mental_load.const import (
    CONF_DEFAULT_PREP_MINUTES,
    CONF_USER_PROFILE,
    CONF_WEIGHT_MOBILITY,
    CONF_WEIGHT_PACING,
    FACTOR_HA_CONTEXT,
    FACTOR_HIDDEN_WORK,
    FACTOR_MOBILITY,
    FACTOR_PACING,
    FACTOR_SOCIAL,
)
from custom_components.mental_load.models import CalendarEventInput, FeedbackEntry
from custom_components.mental_load.prompt_builder import (
    analyze_day_heuristic,
    build_day_user_prompt,
    build_system_prompt,
    parse_llm_day_response,
)


def _events_for_day() -> list[CalendarEventInput]:
    tz = datetime.timezone(datetime.timedelta(hours=1))
    return [
        CalendarEventInput(
            uid="1",
            summary="Sprint Review",
            start=datetime.datetime(2026, 11, 23, 14, 0, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 15, 0, tzinfo=tz),
            source_calendar="calendar.work",
        ),
        CalendarEventInput(
            uid="2",
            summary="Kinderarzt",
            start=datetime.datetime(2026, 11, 23, 15, 10, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 16, 0, tzinfo=tz),
            location="Praxis Zentrum",
            source_calendar="calendar.family",
        ),
    ]


def test_prompts_include_all_five_factors_and_feedback() -> None:
    feedback = [
        FeedbackEntry(
            timestamp="2026-11-22T20:00:00+01:00",
            date_str="2026-11-22",
            previous_score=80,
            perceived_score=60,
            adjustment="lower",
            note="Abendsport hat mich entspannt",
        )
    ]
    cfg = {
        CONF_USER_PROFILE: "Introvertiert, abends schnell erschöpft",
        CONF_WEIGHT_PACING: 1.5,
        CONF_WEIGHT_MOBILITY: 1.2,
    }
    sys_prompt = build_system_prompt(cfg, feedback)
    assert "`pacing_context` (weight 1.50x)" in sys_prompt
    assert "`social_battery`" in sys_prompt
    assert "`mobility` (weight 1.20x)" in sys_prompt
    assert "`ha_context`" in sys_prompt
    assert "`hidden_work`" in sys_prompt
    assert "Abendsport hat mich entspannt" in sys_prompt

    usr_prompt = build_day_user_prompt(
        datetime.date(2026, 11, 23),
        _events_for_day(),
        {"open_todos": ["Geschenk kaufen", "Steuer"], "energy_sensor": "42 %", "weather": "rainy, 6°"},
        cfg,
    )
    assert "BACK-TO-BACK (10m gap)" in usr_prompt
    assert "2 open tasks" in usr_prompt
    assert "42 %" in usr_prompt
    assert "rainy, 6°" in usr_prompt


def test_parse_markdown_fenced_json_and_task_placement() -> None:
    raw = """Hier ist die Analyse:
```json
{
  "score": 74,
  "top_stressor": "Kontextwechsel Arbeit/Arzt",
  "reasoning": "Nur 10 Min zwischen Meeting und Arzt.",
  "factor_breakdown": {
    "pacing_context": 85,
    "social_battery": 60,
    "mobility": 70,
    "ha_context": 50,
    "hidden_work": 80
  },
  "hidden_tasks": [
    {
      "parent_event_summary": "Kinderarzt",
      "title": "U-Heft einpacken",
      "category": "prep",
      "offset_type": "before",
      "offset_minutes": 20,
      "duration_minutes": 20,
      "load_contribution": 15,
      "checklist": ["U-Heft", "Karte"]
    },
    {
      "parent_event_summary": "Kinderarzt",
      "title": "Rezept einlösen",
      "category": "followup",
      "offset_type": "after",
      "offset_minutes": 15,
      "duration_minutes": 15,
      "load_contribution": 8,
      "checklist": ["Apotheke"]
    }
  ],
  "recommendations": ["Puffer einbauen"]
}
```
"""
    analysis = parse_llm_day_response(
        raw_text=raw,
        target_date=datetime.date(2026, 11, 23),
        events=_events_for_day(),
        config={CONF_DEFAULT_PREP_MINUTES: 20},
        provider_used="openai",
    )
    assert analysis.level == "high"
    assert "🟠" in analysis.badge_summary
    assert len(analysis.hidden_tasks) == 2

    prep_task, fup_task = analysis.hidden_tasks
    # Kinderarzt is 15:10 - 16:00 -> prep is 14:50 - 15:10, followup is 16:00 - 16:15
    assert prep_task.start.hour == 14 and prep_task.start.minute == 50
    assert prep_task.end.hour == 15 and prep_task.end.minute == 10
    assert fup_task.start.hour == 16 and fup_task.start.minute == 0
    assert fup_task.end.hour == 16 and fup_task.end.minute == 15


def test_heuristic_feedback_bias_and_factors() -> None:
    events = _events_for_day()
    base = analyze_day_heuristic(
        datetime.date(2026, 11, 23),
        events,
        {"open_todos": ["A", "B", "C"], "energy_sensor": "35", "weather": "rain"},
        {},
    )
    assert set(base.factor_breakdown.keys()) == {
        FACTOR_PACING,
        FACTOR_SOCIAL,
        FACTOR_MOBILITY,
        FACTOR_HA_CONTEXT,
        FACTOR_HIDDEN_WORK,
    }
    assert any(t.category == "travel" for t in base.hidden_tasks)

    lower_fb = [
        FeedbackEntry("2026-11-22T20:00:00", "2026-11-22", 80, 40, "much_lower", "Alles easy")
    ]
    adjusted = analyze_day_heuristic(
        datetime.date(2026, 11, 23),
        events,
        {"open_todos": ["A", "B", "C"], "energy_sensor": "35", "weather": "rain"},
        {},
        feedback_history=lower_fb,
    )
    assert adjusted.score < base.score
