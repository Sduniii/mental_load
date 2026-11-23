"""5-Factor prompt builder, JSON response parser, and heuristic fallback engine."""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any

from .const import (
    ALL_FACTORS,
    CONF_CHILDREN_CALENDARS,
    CONF_CHILDREN_PROFILES,
    CONF_DEFAULT_FOLLOWUP_MINUTES,
    CONF_DEFAULT_PREP_MINUTES,
    CONF_DEFAULT_TRAVEL_MINUTES,
    CONF_LANGUAGE,
    CONF_PROJECT_BASELINE_CAP,
    CONF_PROJECT_BASELINE_PER_ITEM,
    CONF_USER_PROFILE,
    CONF_WEIGHT_HA_CONTEXT,
    CONF_WEIGHT_HIDDEN_WORK,
    CONF_WEIGHT_MOBILITY,
    CONF_WEIGHT_PACING,
    CONF_WEIGHT_SOCIAL,
    DEFAULT_CHILDREN_PROFILES,
    DEFAULT_FOLLOWUP_MINUTES,
    DEFAULT_LANGUAGE,
    DEFAULT_PREP_MINUTES,
    DEFAULT_PROJECT_BASELINE_CAP,
    DEFAULT_PROJECT_BASELINE_PER_ITEM,
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
    "project_step": "🔧 Etappe",
}

OPEN_BUSINESS_KEYWORDS = (
    "tüv",
    "baumarkt",
    "einkauf",
    "einkaufen",
    "kaufen",
    "besorgen",
    "abholen",
    "anruf",
    "anrufen",
    "telefon",
    "amt",
    "behörde",
    "arzt",
    "praxis",
    "werkstatt",
    "lieferant",
    "öffnungszeit",
    "apotheke",
    "post",
    "bank",
    "termin vereinbaren",
    "termin ausmachen",
    "handwerker",
    "garantie",
    "hänger",
    "umtauschen",
    "hardware",
    "shop",
    "store",
    "call ",
    "errand",
)

NOISY_WORK_KEYWORDS = (
    "schleifen",
    "bohren",
    "sägen",
    "fräsen",
    "flexen",
    "stemmen",
    "hämmern",
    "vertikutieren",
    "rasenmäh",
    "mähen",
    "häckseln",
    "hochdruckreiniger",
    "rüttelplatte",
    "motorsäge",
    "laubbläser",
    "kompressor",
    "baumaschine",
    "laut",
    "sand",
    "grind",
    "drill",
    "saw",
    "mow",
)


def extract_todo_priority(
    summary: str,
    description: str = "",
    due: str | None = None,
    reference_date: datetime.date | None = None,
) -> tuple[int, str]:
    """Return (rank 1..4, label) from Anytype Priority, due date urgency, or In Progress status."""
    combined = f"{summary} {description}".lower()

    high_markers = (
        "prio: hoch",
        "prio: high",
        "priority: hoch",
        "priority: high",
        "prio: 1",
        "prio: p1",
        "[p1]",
        "!1",
        "[hoch]",
        "[high]",
        "🔥",
        "dringend",
        "urgent",
    )
    if any(tok in combined for tok in high_markers):
        return 1, "high"

    low_markers = (
        "prio: niedrig",
        "prio: low",
        "priority: niedrig",
        "priority: low",
        "prio: 3",
        "prio: p3",
        "[p3]",
        "!3",
        "[niedrig]",
        "[low]",
    )
    if any(tok in combined for tok in low_markers):
        return 4, "low"

    medium_explicit_markers = (
        "prio: mittel",
        "prio: medium",
        "priority: mittel",
        "priority: medium",
        "prio: 2",
        "prio: p2",
        "[p2]",
        "!2",
        "[mittel]",
        "[medium]",
    )
    if any(tok in combined for tok in medium_explicit_markers):
        return 2, "medium"

    if due:
        due_text = str(due).strip()[:10]
        try:
            due_date = datetime.date.fromisoformat(due_text)
            ref = reference_date or datetime.date.today()
            if (due_date - ref).days <= 7:
                return 1, "high"
        except ValueError:
            pass

    if "status: in progress" in combined:
        return 2, "medium"

    return 3, "normal"


def _resolve_item_priority(item: dict[str, Any]) -> tuple[int, str]:
    """Read cached priority fields on a todo dict or extract them deterministically."""
    if "priority_rank" in item and "priority_label" in item:
        return int(item["priority_rank"]), str(item["priority_label"])
    return extract_todo_priority(
        summary=str(item.get("summary") or ""),
        description=str(item.get("description") or ""),
        due=item.get("due"),
    )


def sort_project_items_by_priority(project_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return project items sorted stably from highest priority & earliest due date to lowest."""
    indexed = list(enumerate(project_items))
    indexed.sort(
        key=lambda pair: (
            _resolve_item_priority(pair[1])[0],
            str(pair[1].get("due") or "9999-12-31"),
            pair[0],
        )
    )
    return [pair[1] for pair in indexed]


def compute_project_baseline(
    open_count: int,
    config: dict[str, Any],
    project_items: list[dict[str, Any]] | None = None,
) -> int:
    """Return capped daily mental load percentage weighted by task priority (Hoch=1.5x, Mittel/Normal=1.0x, Niedrig=0.5x)."""
    if open_count <= 0:
        return 0
    per_item = max(0.0, float(config.get(CONF_PROJECT_BASELINE_PER_ITEM, DEFAULT_PROJECT_BASELINE_PER_ITEM)))
    cap = max(0, min(40, int(config.get(CONF_PROJECT_BASELINE_CAP, DEFAULT_PROJECT_BASELINE_CAP))))
    if project_items:
        total = 0.0
        for item in project_items:
            rank, _ = _resolve_item_priority(item)
            if rank == 1:
                total += per_item * 1.5
            elif rank == 4:
                total += per_item * 0.5
            else:
                total += per_item
        return min(cap, round(total))
    return min(cap, round(open_count * per_item))


def infer_step_sunday_restrictions(
    text: str,
    raw_requires_open: Any = None,
    raw_is_noisy: Any = None,
) -> tuple[bool, bool]:
    """Determine whether a project sub-step needs open businesses/calls or makes noise (blocked on Sundays)."""
    lowered = text.lower()
    kw_open = any(kw in lowered for kw in OPEN_BUSINESS_KEYWORDS)
    kw_noisy = any(kw in lowered for kw in NOISY_WORK_KEYWORDS)
    requires_open = bool(raw_requires_open) or kw_open
    is_noisy = bool(raw_is_noisy) or kw_noisy
    return requires_open, is_noisy


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


def _parse_birthdate_token(raw_line: str) -> tuple[datetime.date | None, str]:
    """Extract YYYY-MM-DD or DD.MM.YYYY birthdate and return (date, remaining_text)."""
    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw_line)
    if iso_match:
        try:
            dob = datetime.date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
            cleaned = (raw_line[: iso_match.start()] + " " + raw_line[iso_match.end() :]).strip()
            return dob, cleaned
        except ValueError:
            pass
    de_match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", raw_line)
    if de_match:
        try:
            dob = datetime.date(int(de_match.group(3)), int(de_match.group(2)), int(de_match.group(1)))
            cleaned = (raw_line[: de_match.start()] + " " + de_match.group(0)[0:0] + raw_line[de_match.end() :]).strip()
            return dob, cleaned
        except ValueError:
            pass
    return None, raw_line.strip()


def parse_children_profiles(
    raw_text: str,
    reference_date: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """Parse child entries with birthdates and compute exact age & developmental stage as of reference_date."""
    if not raw_text or not str(raw_text).strip():
        return []
    ref = reference_date or datetime.date.today()
    children: list[dict[str, Any]] = []

    for chunk in re.split(r"[\n;]+", str(raw_text)):
        line = chunk.strip()
        if not line:
            continue
        dob, remainder = _parse_birthdate_token(line)
        note = ""
        paren_match = re.search(r"\(([^)]+)\)", remainder)
        if paren_match:
            note = paren_match.group(1).strip()
            remainder = (remainder[: paren_match.start()] + " " + remainder[paren_match.end() :]).strip()
        parts = [p.strip(" :-–|,") for p in re.split(r"[:,|]+", remainder, maxsplit=1) if p.strip(" :-–|,")]
        name = parts[0] if parts else line
        if len(parts) > 1 and not note:
            note = parts[1].strip(" :-–|,")
        elif len(parts) > 1:
            note = f"{parts[1]}, {note}".strip(", ")

        if dob is None:
            children.append(
                {
                    "name": name,
                    "birthdate": None,
                    "age_years": None,
                    "age_months": None,
                    "age_display": "Alter unbekannt",
                    "stage_hint": "Betreuung/Alter nach Kontext einschätzen",
                    "days_until_birthday": None,
                    "note": note,
                }
            )
            continue

        if ref < dob:
            total_months = 0
            years = 0
            age_display = "noch nicht geboren"
        else:
            total_months = (ref.year - dob.year) * 12 + (ref.month - dob.month) - (1 if ref.day < dob.day else 0)
            total_months = max(0, total_months)
            years = total_months // 12
            if years < 2:
                age_display = f"{total_months} Monat" if total_months == 1 else f"{total_months} Monate"
            else:
                age_display = f"{years} Jahre"

        if years < 1:
            stage_hint = "Säugling/Baby (sehr hoher Betreuungs- & Schlafbedarf)"
        elif years < 3:
            stage_hint = "Kleinkind (volle Begleitung & Wickel-/Schlafzeiten)"
        elif years < 6:
            stage_hint = "Kita-Alter (Bring-/Abholzeiten, Wechselkleidung)"
        elif years < 11:
            stage_hint = "Grundschule (Stundenplan, Sport-/Schwimmbeutel, Hausaufgaben, Bring-/Abhollogistik)"
        elif years < 15:
            stage_hint = "Weiterführende Schule (zunehmend selbstständig, Klassenarbeiten/Termine)"
        else:
            stage_hint = "Jugendliche/r (weitgehend selbstständig, v. a. besondere Termine beachten)"

        bday_day = 28 if (dob.month == 2 and dob.day == 29) else dob.day
        next_bday = datetime.date(ref.year, dob.month, bday_day)
        if next_bday < ref:
            next_bday = datetime.date(ref.year + 1, dob.month, bday_day)
        days_until = (next_bday - ref).days

        children.append(
            {
                "name": name,
                "birthdate": dob.isoformat(),
                "age_years": years,
                "age_months": total_months,
                "age_display": age_display,
                "stage_hint": stage_hint,
                "days_until_birthday": days_until,
                "note": note,
            }
        )
    return children


def format_children_context_block(
    config: dict[str, Any],
    target_date: datetime.date | None = None,
) -> str:
    """Format configured children profiles + child/school calendar rules for LLM prompts."""
    ref = target_date or datetime.date.today()
    children = parse_children_profiles(str(config.get(CONF_CHILDREN_PROFILES, DEFAULT_CHILDREN_PROFILES) or ""), ref)
    child_cals = [str(c).strip() for c in (config.get(CONF_CHILDREN_CALENDARS, []) or []) if str(c).strip()]
    if not children and not child_cals:
        return ""

    lines: list[str] = ["### Children & School Timetable Context"]
    for child in children:
        note_str = f" | Note: {child['note']}" if child.get("note") else ""
        bday_note = ""
        days_until = child.get("days_until_birthday")
        if days_until == 0:
            bday_note = " | 🎂 BIRTHDAY TODAY!"
        elif isinstance(days_until, int) and 1 <= days_until <= 14:
            bday_note = f" | 🎂 Upcoming birthday in {days_until} days (plan gift/party prep ahead)"
        lines.append(
            f"- Child `{child['name']}`: age **{child['age_display']}** (born {child.get('birthdate') or 'unknown'}) — "
            f"{child['stage_hint']}{note_str}{bday_note}"
        )

    if child_cals:
        lines.append(
            "- Dedicated child/school timetable calendars "
            f"({', '.join(child_cals)}) are tagged `[Stundenplan/Kind]`."
        )
        lines.append(
            "  IMPORTANT TIMETABLE RULE: Consecutive school lessons (e.g. Mathe, Deutsch, Kunst) belong to the child's timetable, "
            "NOT the parent's own meetings — do NOT score individual school lessons as parent back-to-back context switching! "
            "Instead derive ONLY the real parent load: morning drop-off / afternoon pick-up logistics (depending on child's age), "
            "special schedule changes (Ausfall, früher Schluss, Elternabend), and evening-before packing/prep (e.g. Sport -> Sportbeutel, Schwimmen -> Schwimmsachen, Ausflug -> Rucksack/Proviant)."
        )
    return "\n".join(lines)


def build_system_prompt(
    config: dict[str, Any],
    feedback_history: list[FeedbackEntry],
) -> str:
    """Build the system instruction prompt covering all 5 factors and hidden-task extraction."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    profile = str(config.get(CONF_USER_PROFILE, DEFAULT_USER_PROFILE)).strip()
    children_block = format_children_context_block(config)
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

    children_section = f"\n{children_block}\n" if children_block else ""

    return f"""You are an empathetic Cognitive Load & Invisible Mental Work Analyst for Home Assistant.
Your job is to evaluate the user's daily calendar and Home Assistant context across 5 factors, calculate an honest mental load score (0-100), and EXPLICITLY UNCOVER the invisible head-tasks ("unsichtbare Kopfarbeit") attached to each calendar event so they can be made visible on the user's calendar and todo list.

{lang_instruction}

### User Personality & Lifestyle Profile
{profile}
{children_section}

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
      "placement_mode": "<same_day | previous_evening | load_balanced>",
      "lead_days": <integer 0-14 (0=same day, 1=previous evening for packing/morning prep, 1-4=load_balanced errands, 7-14=major events like birthdays/gifts/trips/parties/school/holidays)>,
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
    children_block = format_children_context_block(config, target_date)
    children_extra = f"\n{children_block}\n" if children_block else ""

    return f"""Analyze mental load for date: {target_date.isoformat()} ({weekday}).

### Scheduled Calendar Events
{events_text}

### Detected Pacing & Transition Gaps
{gaps_text}
{children_extra}
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


def _infer_placement_metadata(
    title: str,
    category: str,
    checklist: list[str],
    start_dt: datetime.datetime,
    raw_mode: Any = None,
    raw_lead: Any = None,
    parent_summary: str = "",
) -> tuple[str, int]:
    """Classify whether a hidden task belongs on the same day, previous evening, or an earlier low-load day."""
    if category in ("travel", "followup"):
        return "same_day", 0

    valid_modes = {"same_day", "previous_evening", "load_balanced"}
    mode = str(raw_mode or "").strip().lower()
    if mode in valid_modes:
        lead = max(0, min(14, int(raw_lead if raw_lead is not None else (1 if mode == "previous_evening" else 2 if mode == "load_balanced" else 0))))
        if mode != "same_day" and lead == 0:
            lead = 1
        return mode, lead

    combined = f"{parent_summary} {title} {' '.join(checklist)}".lower()
    major_keywords = (
        "geburtstag", "kindergeburtstag", "birthday", "geschenk", "bestellen",
        "einschulung", "urlaub", "reise", "vacation", "trip", "hochzeit",
        "wedding", "weihnachten", "christmas", "feier", "party", "gäste",
        "gastgeber", "prüfung", "präsentation", "operation", "klinik",
    )
    if any(kw in combined for kw in major_keywords):
        return "load_balanced", 12

    errand_keywords = (
        "bargeld", "abheben", "kaufen", "besorgen",
        "überweisung", "rezept", "karte", "ferien", "gegenprüfen",
        "cash", "buy", "withdraw", "errand", "schedule",
    )
    if any(kw in combined for kw in errand_keywords):
        return "load_balanced", 3

    if category == "packing" or (category == "prep" and start_dt.hour < 12):
        return "previous_evening", 1

    return "same_day", 0


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
        placement_mode, lead_days = _infer_placement_metadata(
            title=title,
            category=category,
            checklist=checklist,
            start_dt=start_dt,
            raw_mode=item.get("placement_mode"),
            raw_lead=item.get("lead_days"),
            parent_summary=parent_summary,
        )
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
                parent_event_date=target_date.isoformat(),
                scheduled_date=target_date.isoformat(),
                lead_days=lead_days,
                placement_mode=placement_mode,
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
        base_score=score,
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
    child_events = [
        e for e in sorted_events
        if "[stundenplan/kind]" in e.source_calendar.lower() or e.source_calendar.lower().startswith("child:")
    ]
    parent_events = [e for e in sorted_events if e not in child_events]
    timed_parent_events = [e for e in parent_events if not e.all_day]

    # Factor 1: Pacing & Context Switching (only count parent events, not consecutive school lessons)
    back_to_back = 0
    overlaps = 0
    domains = [_classify_event_domain(e.summary) for e in parent_events]
    domain_switches = sum(1 for a, b in zip(domains, domains[1:]) if a != b)
    for prev_ev, curr_ev in zip(timed_parent_events, timed_parent_events[1:]):
        gap = (curr_ev.start - prev_ev.end).total_seconds() / 60.0
        if gap < 0:
            overlaps += 1
        elif gap < 15:
            back_to_back += 1
    pacing_score = min(100, len(parent_events) * 11 + back_to_back * 16 + overlaps * 22 + domain_switches * 10 + (4 if child_events else 0))

    # Factor 2: Social Battery & Profile
    evening_events = sum(1 for e in timed_parent_events if e.start.hour >= 18)
    social_events = sum(
        1 for e in parent_events
        if any(k in e.summary.lower() for k in ("meeting", "party", "geburtstag", "dinner", "abend", "team", "eltern", "call"))
    )
    recovery_events = sum(1 for d in domains if d == "recovery")
    social_score = max(0, min(100, social_events * 18 + evening_events * 20 - recovery_events * 15))

    # Factor 3: Mobility & Locations
    located_events = [e for e in parent_events if e.location.strip()]
    mobility_score = min(100, len(located_events) * 28)

    # Factor 4: Live HA Context (todos, energy/sleep, weather)
    open_todos = ha_context.get("open_todos", [])
    ha_score = 0 if provider_label == "fast_empty_day" else min(30, len(open_todos) * 4)
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
        is_child_cal = "[stundenplan/kind]" in ev.source_calendar.lower() or ev.source_calendar.lower().startswith("child:")
        ev_start = ev.start if not ev.all_day else datetime.datetime.combine(target_date, datetime.time(8, 30), tzinfo=tzinfo)
        ev_end = ev.end if not ev.all_day else datetime.datetime.combine(target_date, datetime.time(9, 0), tzinfo=tzinfo)

        if is_child_cal:
            needs_bag = any(k in text for k in ("sport", "schwimm", "turnen", "ausflug", "wandertag", "verkleid", "fasching"))
            is_special_child = any(k in text for k in ("eltern", "schulfest", "ferien", "ausfall", "entfall", "früh", "klassenarbeit"))
            if needs_bag:
                bag_title = f"🎒 Schulsachen packen: {ev.summary}" if is_de else f"🎒 Pack school bag: {ev.summary}"
                hidden_tasks.append(
                    HiddenTask(
                        uid=_make_task_uid(target_date, ev.summary, bag_title, "packing"),
                        parent_event_summary=ev.summary,
                        title=bag_title,
                        category="packing",
                        start=datetime.datetime.combine(target_date, datetime.time(7, 15), tzinfo=tzinfo),
                        end=datetime.datetime.combine(target_date, datetime.time(7, 30), tzinfo=tzinfo),
                        load_contribution=10,
                        checklist=(
                            ["Sport-/Schwimmsachen bzw. Proviant am Vorabend bereitlegen", "Trinkflasche & Brotdose einpacken"]
                            if is_de
                            else ["Prepare sports/swim bag or lunchbox evening before", "Pack water bottle"]
                        ),
                    )
                )
            elif is_special_child:
                prep_title = f"🧠 Kinder-Orga: {ev.summary}" if is_de else f"🧠 Child logistics: {ev.summary}"
                hidden_tasks.append(
                    HiddenTask(
                        uid=_make_task_uid(target_date, ev.summary, prep_title, "prep"),
                        parent_event_summary=ev.summary,
                        title=prep_title,
                        category="prep",
                        start=ev_start - datetime.timedelta(minutes=prep_min),
                        end=ev_start,
                        load_contribution=12,
                        checklist=(
                            ["Betreuungs-/Abholzeit anpassen", "Unterlagen oder Mitteilungen prüfen"]
                            if is_de
                            else ["Adjust pickup/care schedule", "Check school notes"]
                        ),
                    )
                )
            continue

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

    for task in hidden_tasks:
        task.parent_event_date = target_date.isoformat()
        task.scheduled_date = target_date.isoformat()
        mode, lead = _infer_placement_metadata(
            title=task.title,
            category=task.category,
            checklist=task.checklist,
            start_dt=task.start,
            parent_summary=task.parent_event_summary,
        )
        task.placement_mode = mode
        task.lead_days = lead

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
        base_score=score,
    )


def build_project_breakdown_prompt(
    project_items: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) to decompose ongoing To-Dos into small weekend/weekday sub-tasks."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    lang_line = (
        "Respond strictly in German for all titles and checklist steps."
        if language.startswith("de")
        else "Respond strictly in English for all titles and checklist steps."
    )
    system_prompt = (
        "You break large household/renovation/garden/admin To-Do items into realistic 20-45 minute starter steps "
        "that can be scheduled onto calm weekends or free weekday afternoons.\n"
        f"{lang_line}\n"
        "PRIORITY RULES:\n"
        "- The items are already ordered from highest to lowest priority.\n"
        "- For [PRIO: HOCH] and [PRIO: MITTEL] items, return 2 concrete sequential steps first.\n"
        "- For [PRIO: NIEDRIG] items, return at most 1 brief starter step at the end.\n"
        "CRITICAL SUNDAY CONSTRAINT: On Sundays in Germany shops, hardware stores (Baumarkt), TÜV, offices, doctors, "
        "and phone hotlines are CLOSED, and noisy power tools/garden machinery (schleifen, bohren, sägen, vertikutieren, mähen) "
        "are FORBIDDEN. Therefore mark EVERY step accurately with `requires_open_business` and `is_noisy`.\n"
        "Return ONLY a single JSON object ordered from highest to lowest priority:\n"
        '{"steps": [{"source_todo": "<exact summary>", "title": "<short 20-45m step title>", '
        '"duration_minutes": 30, "load_contribution": 10, '
        '"requires_open_business": false, "is_noisy": false, '
        '"checklist": ["<step 1>", "<step 2>"]}]}'
    )

    sorted_items = sort_project_items_by_priority(project_items)
    prio_badges = {
        1: "PRIO: HOCH",
        2: "PRIO: MITTEL",
        3: "PRIO: NORMAL",
        4: "PRIO: NIEDRIG",
    }
    lines = []
    for idx, item in enumerate(sorted_items[:25], start=1):
        summary = str(item.get("summary") or "").strip()
        desc = str(item.get("description") or "").strip()
        due = str(item.get("due") or "").strip()
        rank, _ = _resolve_item_priority(item)
        prio_tag = f"[{prio_badges.get(rank, 'PRIO: NORMAL')}] "
        due_note = f" | Fällig: {due[:10]}" if due else ""
        desc_note = f" — Details: {desc[:420]}" if desc else ""
        lines.append(f"{idx}. {prio_tag}{summary}{due_note}{desc_note}")

    user_prompt = (
        "Decompose each open project/To-Do below into concrete small 20-45 min steps ordered strictly by priority. "
        "Whenever technical/practical context is given after 'Details:', incorporate those specific materials, tools, circuits, or specs into the step titles and checklists "
        "(keep planning/measuring/quiet indoor prep separate from shopping/TÜV/calls or noisy work so calm Sundays can still get quiet prep steps):\n\n"
        + "\n".join(lines)
    )
    return system_prompt, user_prompt


def _match_source_priority(
    source_todo: str,
    combined_text: str,
    project_items: list[dict[str, Any]] | None,
) -> tuple[int, str]:
    """Find matching source todo item priority or infer from text."""
    if project_items:
        target = source_todo.strip().lower()
        for item in project_items:
            if str(item.get("summary") or "").strip().lower() == target:
                return _resolve_item_priority(item)
        for item in project_items:
            cand = str(item.get("summary") or "").strip().lower()
            if cand and (cand in target or target in cand):
                return _resolve_item_priority(item)
    return extract_todo_priority(source_todo, combined_text)


def parse_project_breakdown_response(
    raw_text: str,
    config: dict[str, Any],
    project_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parse LLM JSON response of project sub-steps and enforce priority & Sunday safety classification."""
    payload = _extract_json_object(raw_text)
    raw_steps = payload.get("steps") or []
    parsed: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        source_todo = str(item.get("source_todo") or "").strip()
        raw_title = str(item.get("title") or source_todo).strip()
        if not raw_title:
            continue
        title = _format_task_title(raw_title, "project_step")
        checklist = [str(c).strip() for c in (item.get("checklist") or []) if str(c).strip()]
        if not checklist:
            checklist = [f"20–45 Min. Fokus-Start für: {source_todo or raw_title}"]
        combined_text = f"{source_todo} {title} {' '.join(checklist)}"
        req_open, noisy = infer_step_sunday_restrictions(
            combined_text,
            raw_requires_open=item.get("requires_open_business"),
            raw_is_noisy=item.get("is_noisy"),
        )
        prio_rank, prio_label = _match_source_priority(source_todo or raw_title, combined_text, project_items)
        parsed.append(
            {
                "source_todo": source_todo or raw_title,
                "title": title,
                "duration_minutes": max(15, min(60, int(item.get("duration_minutes", 30) or 30))),
                "load_contribution": max(4, min(25, int(item.get("load_contribution", 10) or 10))),
                "requires_open_business": req_open,
                "is_noisy": noisy,
                "checklist": checklist[:4],
                "priority_rank": prio_rank,
                "priority_label": prio_label,
                "_order": idx,
            }
        )
    parsed.sort(key=lambda s: (int(s["priority_rank"]), int(s["_order"])))
    for step in parsed:
        step.pop("_order", None)
    return parsed


def decompose_project_todos_heuristic(
    project_items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Deterministic fallback that decomposes open project To-Dos into priority-ordered 20-45 min starter steps."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    is_de = language.startswith("de")
    steps: list[dict[str, Any]] = []

    for item in sort_project_items_by_priority(project_items)[:25]:
        summary = str(item.get("summary") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not summary:
            continue
        prio_rank, prio_label = _resolve_item_priority(item)
        combined = f"{summary} {desc}"
        req_open, noisy = infer_step_sunday_restrictions(combined)

        generated_for_item: list[dict[str, Any]] = []
        # Always generate a quiet planning/prep starter step first (safe for Sunday!)
        if any(k in combined.lower() for k in ("tüv", "werkstatt", "arzt", "termin")):
            quiet_title = (
                f"🔧 Etappe: Unterlagen & Wunschtermin prüfen ({summary})"
                if is_de
                else f"🔧 Step: Check documents & preferred dates ({summary})"
            )
            quiet_checks = (
                ["Fahrzeugschein/Papiere bereitlegen", "Mögliche Zeitfenster heraussuchen"]
                if is_de
                else ["Gather required papers", "Note preferred time slots"]
            )
            generated_for_item.append(
                {
                    "source_todo": summary,
                    "title": quiet_title,
                    "duration_minutes": 20,
                    "load_contribution": 8,
                    "requires_open_business": False,
                    "is_noisy": False,
                    "checklist": quiet_checks,
                    "priority_rank": prio_rank,
                    "priority_label": prio_label,
                }
            )
            action_title = (
                f"🔧 Etappe: Termin vereinbaren / erledigen ({summary})"
                if is_de
                else f"🔧 Step: Book / complete appointment ({summary})"
            )
            action_checks = (
                ["Öffnungszeiten prüfen und anrufen/hinfahren", "Termin fest im Kalender eintragen"]
                if is_de
                else ["Call/visit during opening hours", "Save confirmed slot to calendar"]
            )
            generated_for_item.append(
                {
                    "source_todo": summary,
                    "title": action_title,
                    "duration_minutes": 35,
                    "load_contribution": 12,
                    "requires_open_business": True,
                    "is_noisy": False,
                    "checklist": action_checks,
                    "priority_rank": prio_rank,
                    "priority_label": prio_label,
                }
            )
        elif noisy or any(k in combined.lower() for k in ("og:", "elektrik", "heizung", "bad", "zimmer", "fenster", "abdicht", "fussboden", "strom", "teich", "solar")):
            plan_title = (
                f"🔧 Etappe: Nächsten Schritt & Material planen ({summary})"
                if is_de
                else f"🔧 Step: Plan next milestone & materials ({summary})"
            )
            plan_checks = (
                ["Ist-Zustand prüfen & Maße/Details notieren", "Fehlende Teile auf Einkaufsliste setzen"]
                if is_de
                else ["Check current state & measurements", "List missing supplies"]
            )
            generated_for_item.append(
                {
                    "source_todo": summary,
                    "title": plan_title,
                    "duration_minutes": 30,
                    "load_contribution": 10,
                    "requires_open_business": False,
                    "is_noisy": False,
                    "checklist": plan_checks,
                    "priority_rank": prio_rank,
                    "priority_label": prio_label,
                }
            )
            work_title = (
                f"🔧 Etappe: 40-Min.-Praxisblock umsetzen ({summary})"
                if is_de
                else f"🔧 Step: Execute 40m hands-on block ({summary})"
            )
            work_checks = (
                ["Werkzeug/Material bereitlegen", "Ein abgegrenztes Teilziel sauber fertigstellen"]
                if is_de
                else ["Prepare tools/materials", "Finish one bounded sub-task cleanly"]
            )
            generated_for_item.append(
                {
                    "source_todo": summary,
                    "title": work_title,
                    "duration_minutes": 40,
                    "load_contribution": 12,
                    "requires_open_business": req_open,
                    "is_noisy": noisy or any(k in combined.lower() for k in ("schleifen", "elektrik", "fenster", "strom")),
                    "checklist": work_checks,
                    "priority_rank": prio_rank,
                    "priority_label": prio_label,
                }
            )
        else:
            title = _format_task_title(
                f"Teil-Schritt: {summary}" if is_de else f"Starter step: {summary}",
                "project_step",
            )
            generated_for_item.append(
                {
                    "source_todo": summary,
                    "title": title,
                    "duration_minutes": 30,
                    "load_contribution": 10,
                    "requires_open_business": req_open,
                    "is_noisy": noisy,
                    "checklist": (
                        ["Ersten 30-Min.-Schritt abgrenzen", "Fortschritt kurz in Anytype/To-Do festhalten"]
                        if is_de
                        else ["Scope first 30m step", "Update progress note when done"]
                    ),
                    "priority_rank": prio_rank,
                    "priority_label": prio_label,
                }
            )

        # Low-priority items get at most 1 brief starter step
        if prio_rank >= 4:
            steps.extend(generated_for_item[:1])
        else:
            steps.extend(generated_for_item)
    return steps
