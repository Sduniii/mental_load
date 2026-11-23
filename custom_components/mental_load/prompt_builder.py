"""5-Factor prompt builder, JSON response parser, and heuristic fallback engine."""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any

from .const import (
    ALL_FACTORS,
    CONF_DEFAULT_FOLLOWUP_MINUTES,
    CONF_DEFAULT_PREP_MINUTES,
    CONF_DEFAULT_TRAVEL_MINUTES,
    CONF_LANGUAGE,
    CONF_USER_PROFILE,
    CONF_WEIGHT_HA_CONTEXT,
    CONF_WEIGHT_HIDDEN_WORK,
    CONF_WEIGHT_MOBILITY,
    CONF_WEIGHT_PACING,
    CONF_WEIGHT_SOCIAL,
    DEFAULT_FOLLOWUP_MINUTES,
    DEFAULT_LANGUAGE,
    DEFAULT_PREP_MINUTES,
    DEFAULT_TRAVEL_MINUTES,
    DEFAULT_USER_PROFILE,
    FACTOR_HA_CONTEXT,
    FACTOR_HIDDEN_WORK,
    FACTOR_MOBILITY,
    FACTOR_PACING,
    FACTOR_SOCIAL,
    PROVIDER_HEURISTIC,
)
from .models import (
    CalendarEventInput,
    DayLoadAnalysis,
    FeedbackEntry,
    HiddenTask,
    level_emoji,
    level_label,
    score_to_level,
)

CATEGORY_PREFIXES = {
    "prep": "🧠 Vorbereitung",
    "travel": "🚗 Fahrt & Puffer",
    "packing": "🎒 Einpacken",
    "followup": "📝 Nachbereitung",
    "mental_note": "💭 Kopfarbeit",
}


def get_factor_weights(config: dict[str, Any]) -> dict[str, float]:
    """Extract normalized factor weights from config."""
    return {
        FACTOR_PACING: float(config.get(CONF_WEIGHT_PACING, 1.0)),
        FACTOR_SOCIAL: float(config.get(CONF_WEIGHT_SOCIAL, 1.0)),
        FACTOR_MOBILITY: float(config.get(CONF_WEIGHT_MOBILITY, 1.0)),
        FACTOR_HA_CONTEXT: float(config.get(CONF_WEIGHT_HA_CONTEXT, 1.0)),
        FACTOR_HIDDEN_WORK: float(config.get(CONF_WEIGHT_HIDDEN_WORK, 1.0)),
    }


def _compute_weighted_score(breakdown: dict[str, int], weights: dict[str, float]) -> int:
    """Compute weighted 0-100 score across the 5 factors."""
    total_weight = sum(max(0.0, weights.get(f, 1.0)) for f in ALL_FACTORS)
    if total_weight <= 0:
        return 0
    weighted_sum = sum(
        max(0, min(100, int(breakdown.get(f, 0)))) * max(0.0, weights.get(f, 1.0))
        for f in ALL_FACTORS
    )
    return max(0, min(100, round(weighted_sum / total_weight)))


def _make_task_uid(target_date: datetime.date, parent_summary: str, title: str, category: str) -> str:
    """Create a deterministic short UID for deduplicating invisible tasks."""
    raw = f"{target_date.isoformat()}|{parent_summary.strip().lower()}|{title.strip().lower()}|{category}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _format_badge_summary(
    score: int,
    level: str,
    hidden_count: int,
    top_stressor: str,
    language: str,
) -> str:
    """Build the calendar all-day badge summary string."""
    emoji = level_emoji(level)
    label = level_label(level, language)
    if language.startswith("de"):
        suffix_parts: list[str] = []
        if top_stressor:
            suffix_parts.append(top_stressor)
        if hidden_count:
            suffix_parts.append(f"{hidden_count} Kopf-Aufgabe{'n' if hidden_count != 1 else ''}")
        suffix = f" – {', '.join(suffix_parts)}" if suffix_parts else ""
        return f"{emoji} {score}% Mental Load ({label}){suffix}"

    suffix_parts = []
    if top_stressor:
        suffix_parts.append(top_stressor)
    if hidden_count:
        suffix_parts.append(f"{hidden_count} hidden task{'s' if hidden_count != 1 else ''}")
    suffix = f" – {', '.join(suffix_parts)}" if suffix_parts else ""
    return f"{emoji} {score}% Mental Load ({label}){suffix}"


def _build_event_gaps(events: list[CalendarEventInput]) -> list[str]:
    """Compute human-readable gap/overlap notes between consecutive timed events."""
    timed = sorted((e for e in events if not e.all_day), key=lambda ev: ev.start)
    notes: list[str] = []
    for prev_ev, curr_ev in zip(timed, timed[1:]):
        gap_min = int((curr_ev.start - prev_ev.end).total_seconds() // 60)
        if gap_min < 0:
            notes.append(f"OVERLAP ({abs(gap_min)}m): '{prev_ev.summary}' overlaps '{curr_ev.summary}'")
        elif gap_min < 15:
            notes.append(f"BACK-TO-BACK ({gap_min}m gap): '{prev_ev.summary}' -> '{curr_ev.summary}'")
        else:
            notes.append(f"Gap {gap_min}m between '{prev_ev.summary}' and '{curr_ev.summary}'")
    return notes


def build_system_prompt(
    config: dict[str, Any],
    feedback_history: list[FeedbackEntry],
) -> str:
    """Build the system instruction prompt covering all 5 factors and hidden-task extraction."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    profile = str(config.get(CONF_USER_PROFILE, DEFAULT_USER_PROFILE)).strip()
    weights = get_factor_weights(config)

    lang_instruction = (
        "Respond in German for all natural language fields (reasoning, title, checklist, recommendations, top_stressor)."
        if language.startswith("de")
        else "Respond in English for all natural language fields."
    )

    feedback_lines = []
    for entry in feedback_history[-15:]:
        feedback_lines.append(
            f"- {entry.date_str}: AI={entry.previous_score}%, User felt={entry.perceived_score}% "
            f"({entry.adjustment}). Note: {entry.note or 'none'}"
        )
    feedback_block = "\n".join(feedback_lines) if feedback_lines else "- No user calibration feedback yet."

    return f"""You are an empathetic Cognitive Load & Invisible Mental Work Analyst for Home Assistant.
Your job is to evaluate the user's daily calendar and Home Assistant context across 5 factors, calculate an honest mental load score (0-100), and EXPLICITLY UNCOVER the invisible head-tasks ("unsichtbare Kopfarbeit") attached to each calendar event so they can be made visible on the user's calendar and todo list.

{lang_instruction}

### User Personality & Lifestyle Profile
{profile}

### The 5 Mental Load Factors & User Weights
1. `pacing_context` (weight {weights[FACTOR_PACING]:.2f}x): Back-to-back events (<15m gap), overlaps, lack of recovery buffers, and context switches between domains (work, family, medical, errands).
2. `social_battery` (weight {weights[FACTOR_SOCIAL]:.2f}x): Social interaction drain, group events, evening events, conflict/high-stakes meetings vs. restorative events, evaluated through the user profile above.
3. `mobility` (weight {weights[FACTOR_MOBILITY]:.2f}x): Physical location changes, commuting, logistics, bad weather impact on travel.
4. `ha_context` (weight {weights[FACTOR_HA_CONTEXT]:.2f}x): Live Home Assistant context such as open todo backlog, low sleep/recovery/body-battery scores, and harsh weather.
5. `hidden_work` (weight {weights[FACTOR_HIDDEN_WORK]:.2f}x): Invisible cognitive overhead (remembering documents, gifts, preparing talking points, packing bags, post-appointment follow-up).

### Factor 5: Learned User Calibration Memory (Recent Feedback)
{feedback_block}

### Strict Output Contract
Return ONLY a single valid JSON object (no markdown fences, no prose outside JSON) with this exact structure:
{{
  "score": <integer 0-100>,
  "top_stressor": "<short 2-5 word headline of the main load driver>",
  "reasoning": "<2-3 empathetic sentences explaining why the day has this load>",
  "factor_breakdown": {{
    "pacing_context": <integer 0-100>,
    "social_battery": <integer 0-100>,
    "mobility": <integer 0-100>,
    "ha_context": <integer 0-100>,
    "hidden_work": <integer 0-100>
  }},
  "hidden_tasks": [
    {{
      "parent_event_summary": "<exact summary of the matching calendar event>",
      "title": "<short actionable title of the invisible task>",
      "category": "<prep | travel | packing | followup | mental_note>",
      "offset_type": "<before | after>",
      "offset_minutes": <integer minutes before event start or after event end, e.g. 20>,
      "duration_minutes": <integer duration 5-60>,
      "load_contribution": <integer 1-35>,
      "checklist": ["<concrete sub-step 1>", "<concrete sub-step 2>"]
    }}
  ],
  "recommendations": ["<practical buffer/self-care tip 1>", "<tip 2>"]
}}
"""


def build_day_user_prompt(
    target_date: datetime.date,
    events: list[CalendarEventInput],
    ha_context: dict[str, Any],
    config: dict[str, Any],
) -> str:
    """Build the user prompt containing one day's events and live HA context."""
    weekday = target_date.strftime("%A")
    prep_min = int(config.get(CONF_DEFAULT_PREP_MINUTES, DEFAULT_PREP_MINUTES))
    travel_min = int(config.get(CONF_DEFAULT_TRAVEL_MINUTES, DEFAULT_TRAVEL_MINUTES))
    followup_min = int(config.get(CONF_DEFAULT_FOLLOWUP_MINUTES, DEFAULT_FOLLOWUP_MINUTES))

    if not events:
        events_text = "No calendar events scheduled on this day."
    else:
        lines = []
        for idx, ev in enumerate(sorted(events, key=lambda x: x.start), start=1):
            time_str = "ALL-DAY" if ev.all_day else f"{ev.start.strftime('%H:%M')} - {ev.end.strftime('%H:%M')}"
            loc = f" | Location: {ev.location}" if ev.location else ""
            desc = f" | Notes: {ev.description[:240]}" if ev.description else ""
            lines.append(f"{idx}. [{time_str}] {ev.summary}{loc}{desc} (source: {ev.source_calendar})")
        events_text = "\n".join(lines)

    gaps = _build_event_gaps(events)
    gaps_text = "\n".join(f"- {g}" for g in gaps) if gaps else "- No tight transitions detected."

    todos = ha_context.get("open_todos", [])
    todo_summary = (
        f"{len(todos)} open tasks (sample: {', '.join(todos[:8])})"
        if todos
        else "None configured or 0 open tasks"
    )
    energy = ha_context.get("energy_sensor") or "Not configured"
    weather = ha_context.get("weather") or "Not configured"

    return f"""Analyze mental load for date: {target_date.isoformat()} ({weekday}).

### Scheduled Calendar Events
{events_text}

### Detected Pacing & Transition Gaps
{gaps_text}

### Live Home Assistant Context (Factor 4)
- Open To-Do items: {todo_summary}
- Sleep / Energy / Recovery sensor: {energy}
- Weather condition: {weather}

### Default Invisible Task Time Defaults
- Standard prep block: {prep_min} min
- Standard travel buffer: {travel_min} min
- Standard follow-up block: {followup_min} min

Uncover every realistic invisible task the user has to keep in mind for these appointments and return the JSON analysis."""


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from LLM output, stripping markdown fences if present."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON root must be an object")
    return data


def _resolve_task_window(
    raw_task: dict[str, Any],
    target_date: datetime.date,
    events: list[CalendarEventInput],
    config: dict[str, Any],
    tzinfo: datetime.tzinfo | None,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Place an invisible head-task cleanly before or after its parent calendar event."""
    parent_name = str(raw_task.get("parent_event_summary", "")).strip().lower()
    matched_event = next(
        (ev for ev in events if ev.summary.strip().lower() == parent_name),
        None,
    )
    if matched_event is None and parent_name:
        matched_event = next(
            (ev for ev in events if parent_name in ev.summary.strip().lower()),
            None,
        )

    category = str(raw_task.get("category", "prep")).lower()
    default_dur = (
        int(config.get(CONF_DEFAULT_TRAVEL_MINUTES, DEFAULT_TRAVEL_MINUTES))
        if category == "travel"
        else int(config.get(CONF_DEFAULT_FOLLOWUP_MINUTES, DEFAULT_FOLLOWUP_MINUTES))
        if category == "followup"
        else int(config.get(CONF_DEFAULT_PREP_MINUTES, DEFAULT_PREP_MINUTES))
    )
    duration_min = max(5, min(120, int(raw_task.get("duration_minutes", default_dur) or default_dur)))
    offset_type = str(raw_task.get("offset_type", "after" if category == "followup" else "before")).lower()
    offset_min = max(0, int(raw_task.get("offset_minutes", duration_min) or duration_min))

    if matched_event and not matched_event.all_day:
        if offset_type == "after":
            start_dt = matched_event.end + datetime.timedelta(minutes=max(0, offset_min - duration_min) if offset_min > duration_min else 0)
            end_dt = start_dt + datetime.timedelta(minutes=duration_min)
        else:
            # Place before parent event; ensure end does not pass parent start
            end_dt = matched_event.start - datetime.timedelta(minutes=max(0, offset_min - duration_min))
            if end_dt > matched_event.start:
                end_dt = matched_event.start
            start_dt = end_dt - datetime.timedelta(minutes=duration_min)
        return start_dt, end_dt

    # All-day or unmatched event: schedule at sensible daytime slots
    base_hour = 18 if offset_type == "after" else 8
    start_dt = datetime.datetime.combine(target_date, datetime.time(base_hour, 0), tzinfo=tzinfo)
    end_dt = start_dt + datetime.timedelta(minutes=duration_min)
    return start_dt, end_dt


def _format_task_title(raw_title: str, category: str) -> str:
    """Prefix an invisible task with a clear visual category emoji if not already prefixed."""
    stripped = raw_title.strip()
    if any(stripped.startswith(prefix.split(" ")[0]) for prefix in CATEGORY_PREFIXES.values()):
        return stripped
    prefix = CATEGORY_PREFIXES.get(category, "🧠 Aufgabe")
    return f"{prefix}: {stripped}" if stripped else prefix


def parse_llm_day_response(
    raw_text: str,
    target_date: datetime.date,
    events: list[CalendarEventInput],
    config: dict[str, Any],
    provider_used: str,
) -> DayLoadAnalysis:
    """Parse raw LLM JSON output into a normalized DayLoadAnalysis."""
    payload = _extract_json_object(raw_text)
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    weights = get_factor_weights(config)
    tzinfo = events[0].start.tzinfo if events else datetime.datetime.now().astimezone().tzinfo

    raw_breakdown = payload.get("factor_breakdown", {}) or {}
    breakdown = {
        factor: max(0, min(100, int(raw_breakdown.get(factor, payload.get("score", 30)))))
        for factor in ALL_FACTORS
    }

    # Blend LLM score with configured user weights if breakdown is provided
    if raw_breakdown:
        weighted = _compute_weighted_score(breakdown, weights)
        raw_score = int(payload.get("score", weighted))
        score = max(0, min(100, round(0.4 * raw_score + 0.6 * weighted)))
    else:
        score = max(0, min(100, int(payload.get("score", 0))))

    hidden_tasks: list[HiddenTask] = []
    for item in payload.get("hidden_tasks", []) or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "prep")).lower()
        if category not in CATEGORY_PREFIXES:
            category = "prep"
        parent_summary = str(item.get("parent_event_summary", "")).strip()
        title = _format_task_title(str(item.get("title", "Vorbereitung")), category)
        start_dt, end_dt = _resolve_task_window(item, target_date, events, config, tzinfo)
        checklist = [str(c).strip() for c in (item.get("checklist") or []) if str(c).strip()]
        contribution = max(1, min(40, int(item.get("load_contribution", 10) or 10)))
        hidden_tasks.append(
            HiddenTask(
                uid=_make_task_uid(target_date, parent_summary, title, category),
                parent_event_summary=parent_summary,
                title=title,
                category=category,
                start=start_dt,
                end=end_dt,
                load_contribution=contribution,
                checklist=checklist,
            )
        )

    level = score_to_level(score)
    top_stressor = str(payload.get("top_stressor", "")).strip()
    badge_summary = _format_badge_summary(
        score=score,
        level=level,
        hidden_count=len(hidden_tasks),
        top_stressor=top_stressor,
        language=language,
    )

    return DayLoadAnalysis(
        date=target_date,
        score=score,
        level=level,
        badge_summary=badge_summary,
        reasoning=str(payload.get("reasoning", "")).strip(),
        factor_breakdown=breakdown,
        hidden_tasks=hidden_tasks,
        recommendations=[str(r).strip() for r in (payload.get("recommendations") or []) if str(r).strip()],
        top_stressor=top_stressor,
        provider_used=provider_used,
        cached=False,
    )


def _classify_event_domain(summary: str) -> str:
    text = summary.lower()
    if any(k in text for k in ("arzt", "zahnarzt", "klinik", "doctor", "dentist", "therap", "impf")):
        return "medical"
    if any(k in text for k in ("kind", "kita", "schule", "eltern", "geburtstag", "family", "kids", "birthday")):
        return "family"
    if any(k in text for k in ("meeting", "standup", "review", "präsentation", "presentation", "call", "workshop", "kunde")):
        return "work"
    if any(k in text for k in ("sport", "yoga", "laufen", "gym", "meditation", "pause", "walk")):
        return "recovery"
    return "general"


def analyze_day_heuristic(
    target_date: datetime.date,
    events: list[CalendarEventInput],
    ha_context: dict[str, Any],
    config: dict[str, Any],
    feedback_history: list[FeedbackEntry] | None = None,
    provider_label: str = PROVIDER_HEURISTIC,
) -> DayLoadAnalysis:
    """Deterministic 5-factor local analyzer & invisible head-task generator.

    Used when PROVIDER_HEURISTIC is configured or as automatic fallback if an LLM API fails.
    """
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    is_de = language.startswith("de")
    weights = get_factor_weights(config)
    tzinfo = events[0].start.tzinfo if events else datetime.datetime.now().astimezone().tzinfo
    prep_min = int(config.get(CONF_DEFAULT_PREP_MINUTES, DEFAULT_PREP_MINUTES))
    travel_min = int(config.get(CONF_DEFAULT_TRAVEL_MINUTES, DEFAULT_TRAVEL_MINUTES))
    followup_min = int(config.get(CONF_DEFAULT_FOLLOWUP_MINUTES, DEFAULT_FOLLOWUP_MINUTES))

    sorted_events = sorted(events, key=lambda e: e.start)
    timed_events = [e for e in sorted_events if not e.all_day]

    # Factor 1: Pacing & Context Switching
    back_to_back = 0
    overlaps = 0
    domains = [_classify_event_domain(e.summary) for e in sorted_events]
    domain_switches = sum(1 for a, b in zip(domains, domains[1:]) if a != b)
    for prev_ev, curr_ev in zip(timed_events, timed_events[1:]):
        gap = (curr_ev.start - prev_ev.end).total_seconds() / 60.0
        if gap < 0:
            overlaps += 1
        elif gap < 15:
            back_to_back += 1
    pacing_score = min(100, len(sorted_events) * 11 + back_to_back * 16 + overlaps * 22 + domain_switches * 10)

    # Factor 2: Social Battery & Profile
    evening_events = sum(1 for e in timed_events if e.start.hour >= 18)
    social_events = sum(
        1 for e in sorted_events
        if any(k in e.summary.lower() for k in ("meeting", "party", "geburtstag", "dinner", "abend", "team", "eltern", "call"))
    )
    recovery_events = sum(1 for d in domains if d == "recovery")
    social_score = max(0, min(100, social_events * 18 + evening_events * 20 - recovery_events * 15))

    # Factor 3: Mobility & Locations
    located_events = [e for e in sorted_events if e.location.strip()]
    mobility_score = min(100, len(located_events) * 28)

    # Factor 4: Live HA Context (todos, energy/sleep, weather)
    open_todos = ha_context.get("open_todos", [])
    ha_score = min(60, len(open_todos) * 6)
    energy_raw = str(ha_context.get("energy_sensor", "")).lower()
    m_energy = re.search(r"(\d+(?:\.\d+)?)", energy_raw)
    if m_energy:
        energy_val = float(m_energy.group(1))
        if energy_val < 50:
            ha_score = min(100, ha_score + int((60 - energy_val) * 0.9))
    weather_raw = str(ha_context.get("weather", "")).lower()
    if any(w in weather_raw for w in ("rain", "snow", "storm", "lightning", "pouring", "regen", "sturm", "schnee")):
        ha_score = min(100, ha_score + 20)
        mobility_score = min(100, mobility_score + 15)

    # Uncover Hidden Tasks (Factor 5)
    hidden_tasks: list[HiddenTask] = []
    for ev in sorted_events:
        text = f"{ev.summary} {ev.description}".lower()
        ev_start = ev.start if not ev.all_day else datetime.datetime.combine(target_date, datetime.time(8, 30), tzinfo=tzinfo)
        ev_end = ev.end if not ev.all_day else datetime.datetime.combine(target_date, datetime.time(9, 0), tzinfo=tzinfo)

        if ev.location.strip():
            t_start = ev_start - datetime.timedelta(minutes=travel_min)
            title = f"🚗 Fahrt & Puffer: {ev.summary}" if is_de else f"🚗 Travel & Buffer: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, title, "travel"),
                    parent_event_summary=ev.summary,
                    title=title,
                    category="travel",
                    start=t_start,
                    end=ev_start,
                    load_contribution=10,
                    checklist=[
                        f"Route prüfen: {ev.location}" if is_de else f"Check route: {ev.location}",
                        "Schlüssel, Handy, Portemonnaie" if is_de else "Keys, phone, wallet",
                    ],
                )
            )
            prep_anchor = t_start
        else:
            prep_anchor = ev_start

        domain = _classify_event_domain(ev.summary)
        if domain == "medical":
            prep_title = f"🧠 Vorbereitung: {ev.summary}" if is_de else f"🧠 Prep: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, prep_title, "prep"),
                    parent_event_summary=ev.summary,
                    title=prep_title,
                    category="prep",
                    start=prep_anchor - datetime.timedelta(minutes=prep_min),
                    end=prep_anchor,
                    load_contribution=15,
                    checklist=(
                        ["Versichertenkarte & Befunde einpacken", "Fragen an Ärztin/Arzt notieren"]
                        if is_de
                        else ["Pack insurance card & medical notes", "Write down questions"]
                    ),
                )
            )
            fup_title = f"📝 Nachbereitung: {ev.summary}" if is_de else f"📝 Follow-up: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, fup_title, "followup"),
                    parent_event_summary=ev.summary,
                    title=fup_title,
                    category="followup",
                    start=ev_end,
                    end=ev_end + datetime.timedelta(minutes=followup_min),
                    load_contribution=8,
                    checklist=(
                        ["Rezept / Überweisung bearbeiten", "Folgetermin prüfen"]
                        if is_de
                        else ["Process prescription/referral", "Check follow-up appointment"]
                    ),
                )
            )
        elif "geburtstag" in text or "birthday" in text or "party" in text:
            prep_title = f"🎒 Mitbringsel & Geschenk: {ev.summary}" if is_de else f"🎒 Gift & Prep: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, prep_title, "packing"),
                    parent_event_summary=ev.summary,
                    title=prep_title,
                    category="packing",
                    start=prep_anchor - datetime.timedelta(minutes=prep_min),
                    end=prep_anchor,
                    load_contribution=16,
                    checklist=(
                        ["Geschenk einpacken & Karte schreiben", "Mitbringsel / Snacks prüfen"]
                        if is_de
                        else ["Wrap gift & write card", "Check snacks/drinks to bring"]
                    ),
                )
            )
        elif domain == "work" or "termin" in text or "interview" in text:
            prep_title = f"🧠 Vorbereitung: {ev.summary}" if is_de else f"🧠 Prep: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, prep_title, "prep"),
                    parent_event_summary=ev.summary,
                    title=prep_title,
                    category="prep",
                    start=prep_anchor - datetime.timedelta(minutes=prep_min),
                    end=prep_anchor,
                    load_contribution=12,
                    checklist=(
                        ["Agenda & Unterlagen bereitlegen", "5 Min mentaler Kontextwechsel"]
                        if is_de
                        else ["Review agenda & documents", "5 min mental transition"]
                    ),
                )
            )
        elif domain == "family":
            prep_title = f"🎒 Familien-Orga: {ev.summary}" if is_de else f"🎒 Family Prep: {ev.summary}"
            hidden_tasks.append(
                HiddenTask(
                    uid=_make_task_uid(target_date, ev.summary, prep_title, "packing"),
                    parent_event_summary=ev.summary,
                    title=prep_title,
                    category="packing",
                    start=prep_anchor - datetime.timedelta(minutes=prep_min),
                    end=prep_anchor,
                    load_contribution=14,
                    checklist=(
                        ["Tasche, Wasser & Unterlagen packen", "Abhol-/Betreuungszeiten abstimmen"]
                        if is_de
                        else ["Pack bag, water & documents", "Confirm pickup/care logistics"]
                    ),
                )
            )

    hidden_score = min(100, sum(t.load_contribution for t in hidden_tasks) + len(hidden_tasks) * 5)

    breakdown = {
        FACTOR_PACING: pacing_score,
        FACTOR_SOCIAL: social_score,
        FACTOR_MOBILITY: mobility_score,
        FACTOR_HA_CONTEXT: ha_score,
        FACTOR_HIDDEN_WORK: hidden_score,
    }

    score = _compute_weighted_score(breakdown, weights)

    # Factor 5: Apply learned user bias from feedback history
    if feedback_history:
        recent = feedback_history[-10:]
        avg_bias = sum(e.perceived_score - e.previous_score for e in recent) / len(recent)
        score = max(0, min(100, round(score + avg_bias * 0.5)))

    level = score_to_level(score)

    if not sorted_events:
        top_stressor = "Freier Kalendertag" if is_de else "Clear schedule"
        reasoning = (
            "Keine Kalendereinträge für diesen Tag – ideal für Erholung oder Fokusarbeit ohne Termindruck."
            if is_de
            else "No calendar events scheduled today — great space for recovery or deep focus."
        )
        recommendations = (
            ["Tag bewusst frei halten oder offene To-dos in Ruhe abarbeiten."]
            if is_de
            else ["Keep buffers protected or tackle open todos gently."]
        )
    else:
        stressor_ev = max(
            sorted_events,
            key=lambda e: (1 if e.location else 0) + (1 if e.start.hour >= 18 else 0) + len(e.summary) / 40.0,
        )
        top_stressor = stressor_ev.summary[:32]
        if is_de:
            reasoning = (
                f"{len(sorted_events)} Termine, {domain_switches} Kontextwechsel und "
                f"{len(hidden_tasks)} unsichtbare Kopf-Aufgaben prägen diesen Tag. "
                f"Haupttreiber ist '{stressor_ev.summary}'."
            )
            recommendations = [
                "10 Minuten Puffer nach jedem Termin ohne Bildschirm einplanen.",
                "Vorbereitungs-Checklisten bereits am Vorabend kurz abhaken.",
            ]
        else:
            reasoning = (
                f"{len(sorted_events)} events, {domain_switches} context switches, and "
                f"{len(hidden_tasks)} uncovered invisible tasks shape today. "
                f"Primary driver: '{stressor_ev.summary}'."
            )
            recommendations = [
                "Keep a 10-minute screen-free buffer between transitions.",
                "Check off preparation items the evening before.",
            ]

    badge_summary = _format_badge_summary(
        score=score,
        level=level,
        hidden_count=len(hidden_tasks),
        top_stressor=top_stressor,
        language=language,
    )

    return DayLoadAnalysis(
        date=target_date,
        score=score,
        level=level,
        badge_summary=badge_summary,
        reasoning=reasoning,
        factor_breakdown=breakdown,
        hidden_tasks=hidden_tasks,
        recommendations=recommendations,
        top_stressor=top_stressor,
        provider_used=provider_label,
        cached=False,
    )
