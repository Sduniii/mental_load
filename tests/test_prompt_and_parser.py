"""Tests for 5-factor prompt builder, JSON parsing, and invisible task timing."""
from __future__ import annotations

import datetime

from custom_components.mental_load.const import (
    CONF_CHILDREN_CALENDARS,
    CONF_CHILDREN_PROFILES,
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
    build_batch_user_prompt,
    build_day_user_prompt,
    build_system_prompt,
    parse_children_profiles,
    parse_llm_batch_response,
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


def test_children_profiles_age_and_timetable_handling() -> None:
    ref = datetime.date(2026, 11, 23)
    parsed = parse_children_profiles(
        "Kind A: 14.05.2018 (Grundschule)\nBaby B: 2026-06-23, Zuhause",
        reference_date=ref,
    )
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Kind A"
    assert parsed[0]["age_years"] == 8
    assert parsed[0]["age_display"] == "8 Jahre"
    assert "Grundschule" in parsed[0]["stage_hint"]
    assert parsed[1]["name"] == "Baby B"
    assert parsed[1]["age_years"] == 0
    assert parsed[1]["age_months"] == 5
    assert parsed[1]["age_display"] == "5 Monate"

    cfg = {
        CONF_CHILDREN_PROFILES: "Kind A: 14.05.2018 (Grundschule)\nBaby B: 2026-11-30",
        CONF_CHILDREN_CALENDARS: ["calendar.stundenplan_kind"],
    }
    sys_prompt = build_system_prompt(cfg, [])
    assert "Children & School Timetable Context" in sys_prompt
    assert "calendar.stundenplan_kind" in sys_prompt
    assert "age **8 Jahre**" in sys_prompt
    assert len(sys_prompt) < 2450
    assert len(build_system_prompt({}, [])) < 1750

    usr_prompt = build_day_user_prompt(ref, [], {}, cfg)
    assert "Children & School Timetable Context" not in usr_prompt
    assert "Upcoming birthday in 7 days" in usr_prompt
    assert "Not configured" not in usr_prompt
    assert len(usr_prompt) < 300

    tz = datetime.timezone(datetime.timedelta(hours=1))
    school_events = [
        CalendarEventInput(
            uid="s1",
            summary="Mathe",
            start=datetime.datetime(2026, 11, 23, 8, 0, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 8, 45, tzinfo=tz),
            source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
        ),
        CalendarEventInput(
            uid="s2",
            summary="Sportunterricht",
            start=datetime.datetime(2026, 11, 23, 8, 45, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 9, 30, tzinfo=tz),
            source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
        ),
        CalendarEventInput(
            uid="s3",
            summary="Deutsch",
            start=datetime.datetime(2026, 11, 23, 9, 30, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 10, 15, tzinfo=tz),
            source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
        ),
    ]
    school_events.append(
        CalendarEventInput(
            uid="s4",
            summary="Schwimmen",
            start=datetime.datetime(2026, 11, 23, 10, 15, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 11, 0, tzinfo=tz),
            source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
        )
    )
    # Default per_day mode: keeps daily Sportunterricht packing AND Schwimmen packing
    school_only = analyze_day_heuristic(ref, school_events, {}, cfg)
    assert school_only.factor_breakdown[FACTOR_PACING] < 15
    assert any("Schulsachen packen" in t.title and "Sportunterricht" in t.title for t in school_only.hidden_tasks)
    assert any("Schulsachen packen" in t.title and "Schwimmen" in t.title for t in school_only.hidden_tasks)

    # sunday_weekly mode: suppresses weekday Sportunterricht packing while keeping Schwimmen
    weekly_cfg = {**cfg, "sport_bag_mode": "sunday_weekly"}
    assert "bundled once on Sunday" in build_system_prompt(weekly_cfg, [])
    school_weekly = analyze_day_heuristic(ref, school_events, {}, weekly_cfg)
    assert not any("Sportunterricht" in t.parent_event_summary for t in school_weekly.hidden_tasks)
    assert any("Schwimmen" in t.title for t in school_weekly.hidden_tasks)

    # Per-series #weekly-bag tag overrides per_day mode for that event only
    tagged_sport = CalendarEventInput(
        uid="s_wb",
        summary="Sportunterricht",
        start=datetime.datetime(2026, 11, 23, 8, 45, tzinfo=tz),
        end=datetime.datetime(2026, 11, 23, 9, 30, tzinfo=tz),
        description="Turnbeutel bleibt #weekly-bag",
        source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
    )
    assert "[WEEKLY-BAG]" in build_day_user_prompt(ref, [tagged_sport], {}, cfg)
    tagged_heur = analyze_day_heuristic(ref, [tagged_sport], {}, cfg)
    assert not any("Sportunterricht" in t.parent_event_summary for t in tagged_heur.hidden_tasks)


def test_no_prep_tags_suppress_hidden_tasks_in_prompt_parser_and_heuristic() -> None:
    import json

    ref = datetime.date(2026, 11, 24)
    tz = datetime.timezone(datetime.timedelta(hours=1))
    ev = CalendarEventInput(
        uid="np1",
        summary="Sportunterricht",
        start=datetime.datetime(2026, 11, 24, 9, 0, tzinfo=tz),
        end=datetime.datetime(2026, 11, 24, 10, 0, tzinfo=tz),
        description="Halle Nord #no-prep",
        source_calendar="calendar.stundenplan_kind [Stundenplan/Kind]",
    )
    ev2 = CalendarEventInput(
        uid="np2",
        summary="Elternabend #kein-packen",
        start=datetime.datetime(2026, 11, 24, 19, 0, tzinfo=tz),
        end=datetime.datetime(2026, 11, 24, 20, 0, tzinfo=tz),
        source_calendar="calendar.google",
    )

    usr_prompt = build_day_user_prompt(ref, [ev, ev2], {}, {})
    assert "[NO-PREP]" in usr_prompt

    heur = analyze_day_heuristic(ref, [ev, ev2], {}, {})
    assert heur.hidden_tasks == []

    llm_raw = json.dumps(
        {
            "score": 40,
            "top_stressor": "Abend",
            "reasoning": "Test",
            "factor_breakdown": {"pacing_context": 20, "social_battery": 40, "mobility": 20, "ha_context": 10, "hidden_work": 30},
            "hidden_tasks": [
                {
                    "parent_event_summary": "Sportunterricht",
                    "title": "Turnbeutel packen",
                    "category": "packing",
                    "offset_type": "before",
                    "offset_minutes": 15,
                    "duration_minutes": 15,
                    "load_contribution": 10,
                    "checklist": [],
                },
                {
                    "parent_event_summary": "Elternabend #kein-packen",
                    "title": "Vorbereiten",
                    "category": "prep",
                    "offset_type": "before",
                    "offset_minutes": 15,
                    "duration_minutes": 15,
                    "load_contribution": 10,
                    "checklist": [],
                },
            ],
            "recommendations": [],
        }
    )
    parsed = parse_llm_day_response(llm_raw, ref, [ev, ev2], {}, "openai")
    assert parsed.hidden_tasks == []


def test_batch_prompt_and_parser_roundtrip() -> None:
    d1 = datetime.date(2026, 11, 23)
    d2 = datetime.date(2026, 11, 24)
    ev1 = _events_for_day()
    ev2 = _events_for_day()[:1]
    batch_prompt = build_batch_user_prompt(
        [
            (d1, ev1, {"open_todos": ["A"], "weather": "rainy, 6°"}, True),
            (d2, ev2, {"open_todos": ["A"], "weather": "rainy, 6°"}, False),
        ],
        {},
    )
    assert "Date: 2026-11-23" in batch_prompt
    assert "Date: 2026-11-24" in batch_prompt
    assert "weather=rainy, 6°" in batch_prompt
    assert batch_prompt.count("weather=") == 1

    raw = """{
      "days": {
        "2026-11-23": {"score": 72, "top_stressor": "Arzt", "reasoning": "Voll", "factor_breakdown": {"pacing_context": 80, "social_battery": 60, "mobility": 70, "ha_context": 50, "hidden_work": 75}, "hidden_tasks": [], "recommendations": ["Pause"]},
        "2026-11-24": {"score": 35, "top_stressor": "Sprint", "reasoning": "Ruhig", "factor_breakdown": {"pacing_context": 30, "social_battery": 40, "mobility": 20, "ha_context": 30, "hidden_work": 40}, "hidden_tasks": [], "recommendations": ["Fokus"]}
      }
    }"""
    parsed = parse_llm_batch_response(
        raw,
        {d1.isoformat(): (d1, ev1), d2.isoformat(): (d2, ev2)},
        {},
        "openai",
    )
    assert set(parsed.keys()) == {"2026-11-23", "2026-11-24"}
    assert parsed["2026-11-23"].score > parsed["2026-11-24"].score
