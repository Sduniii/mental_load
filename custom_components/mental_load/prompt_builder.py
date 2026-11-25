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
    CONF_HOUSEHOLD_MEMBERS,
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
    MEMBER_ROLE_ADULT,
    MEMBER_ROLE_CHILD,
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
    """Compute compact gap/overlap notes ONLY for tight transitions (<15m) or overlaps."""
    timed = sorted((e for e in events if not e.all_day), key=lambda ev: ev.start)
    notes: list[str] = []
    for prev_ev, curr_ev in zip(timed, timed[1:]):
        gap_min = int((curr_ev.start - prev_ev.end).total_seconds() // 60)
        if gap_min < 0:
            notes.append(f"OVERLAP ({abs(gap_min)}m): '{prev_ev.summary}' / '{curr_ev.summary}'")
        elif gap_min < 15:
            notes.append(f"BACK-TO-BACK ({gap_min}m gap): '{prev_ev.summary}' -> '{curr_ev.summary}'")
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


NO_PREP_TAGS = ("#no-prep", "[ml:no-prep]", "#noprep", "#kein-packen")


def event_has_no_prep_tag(event: CalendarEventInput) -> bool:
    """Return True if event summary or description carries a no-prep suppression tag."""
    haystack = f"{event.summary} {event.description}".lower()
    return any(tag in haystack for tag in NO_PREP_TAGS)


def normalize_household_members(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized adult & child member records, migrating legacy config fields if needed."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    default_adult_name = "Erwachsener 1" if language.startswith("de") else "Adult 1"
    raw_members = config.get(CONF_HOUSEHOLD_MEMBERS)

    normalized: list[dict[str, Any]] = []
    if isinstance(raw_members, list) and raw_members:
        for idx, item in enumerate(raw_members, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or MEMBER_ROLE_ADULT).strip().lower()
            if role not in (MEMBER_ROLE_ADULT, MEMBER_ROLE_CHILD):
                role = MEMBER_ROLE_ADULT
            fallback_name = (
                f"{'Kind' if language.startswith('de') else 'Child'} {idx}"
                if role == MEMBER_ROLE_CHILD
                else f"{'Erwachsener' if language.startswith('de') else 'Adult'} {idx}"
            )
            name = str(item.get("name") or fallback_name).strip() or fallback_name
            raw_cals = item.get("calendars") or []
            cals = [str(c).strip() for c in raw_cals if str(c).strip()] if isinstance(raw_cals, list) else []
            normalized.append(
                {
                    "id": str(item.get("id") or f"member_{idx}"),
                    "role": role,
                    "name": name,
                    "birthdate": str(item.get("birthdate") or "").strip() if role == MEMBER_ROLE_CHILD else "",
                    "calendars": cals,
                    "profile": str(item.get("profile") or "").strip(),
                }
            )
        if any(m["role"] == MEMBER_ROLE_ADULT for m in normalized):
            return normalized

    # Legacy migration: user_profile -> Adult 1, children_profiles + children_calendars -> Child members
    legacy_profile = str(config.get(CONF_USER_PROFILE, DEFAULT_USER_PROFILE) or DEFAULT_USER_PROFILE).strip()
    adult_entry = {
        "id": "adult_1",
        "role": MEMBER_ROLE_ADULT,
        "name": default_adult_name,
        "birthdate": "",
        "calendars": [],
        "profile": legacy_profile,
    }
    normalized = [adult_entry] + [m for m in normalized if m["role"] == MEMBER_ROLE_CHILD]

    legacy_child_cals = [
        str(c).strip() for c in (config.get(CONF_CHILDREN_CALENDARS, []) or []) if str(c).strip()
    ]
    if not any(m["role"] == MEMBER_ROLE_CHILD for m in normalized):
        legacy_children = parse_children_profiles(
            str(config.get(CONF_CHILDREN_PROFILES, DEFAULT_CHILDREN_PROFILES) or "")
        )
        for idx, child in enumerate(legacy_children, start=1):
            normalized.append(
                {
                    "id": f"child_{idx}",
                    "role": MEMBER_ROLE_CHILD,
                    "name": str(child.get("name") or f"Kind {idx}").strip(),
                    "birthdate": str(child.get("birthdate") or "").strip(),
                    "calendars": list(legacy_child_cals) if idx == 1 else [],
                    "profile": str(child.get("note") or "").strip(),
                }
            )
        if not legacy_children and legacy_child_cals:
            normalized.append(
                {
                    "id": "child_1",
                    "role": MEMBER_ROLE_CHILD,
                    "name": "Kind 1" if language.startswith("de") else "Child 1",
                    "birthdate": "",
                    "calendars": legacy_child_cals,
                    "profile": "",
                }
            )
    return normalized


def _resolve_child_member_stats(
    member: dict[str, Any],
    ref: datetime.date,
) -> dict[str, Any]:
    """Compute age, stage hint, and birthday proximity for a normalized child member dict."""
    raw_dob = str(member.get("birthdate") or "").strip()
    dob, _ = _parse_birthdate_token(raw_dob) if raw_dob else (None, "")
    return parse_children_profiles(
        f"{member['name']}: {dob.isoformat() if dob else raw_dob}",
        reference_date=ref,
    )[0] if dob else {
        "name": member["name"],
        "birthdate": raw_dob or None,
        "age_years": None,
        "age_months": None,
        "age_display": "Alter unbekannt",
        "stage_hint": "Altersstufe nicht angegeben",
        "days_until_birthday": None,
        "note": member.get("profile", ""),
    }


def format_household_context_block(
    config: dict[str, Any],
    target_date: datetime.date | None = None,
) -> str:
    """Format household adults & children with per-person calendars and free-text routines for prompts."""
    ref = target_date or datetime.date.today()
    members = normalize_household_members(config)
    adults = [m for m in members if m["role"] == MEMBER_ROLE_ADULT]
    children = [m for m in members if m["role"] == MEMBER_ROLE_CHILD]

    lines: list[str] = ["### Household Context"]
    if len(adults) >= 2:
        lines.append(
            f"- Household has {len(adults)} adults sharing duties; evaluate total household load across available adults (solo-parenting or overlapping shifts raise strain)."
        )
    for adult in adults:
        cal_str = f" | Calendars: {', '.join(adult['calendars'])}" if adult.get("calendars") else ""
        prof_str = f": {adult['profile']}" if adult.get("profile") else ""
        lines.append(f"- Adult **{adult['name']}**{cal_str}{prof_str}")

    if children:
        lines.append("Children:")
        has_child_cals = False
        for child in children:
            stats = _resolve_child_member_stats(child, ref)
            bday_note = ""
            days_until = stats.get("days_until_birthday")
            if days_until == 0:
                bday_note = " | 🎂 BIRTHDAY TODAY!"
            elif isinstance(days_until, int) and 1 <= days_until <= 14:
                bday_note = f" | 🎂 Birthday in {days_until}d"
            cal_list = child.get("calendars") or []
            if cal_list:
                has_child_cals = True
            cal_str = f" | Calendars: {', '.join(cal_list)}" if cal_list else ""
            prof_str = f" | Rules/Notes: {child['profile']}" if child.get("profile") else ""
            lines.append(
                f"- Child **{child['name']}**: age **{stats['age_display']}** "
                f"({stats.get('birthdate') or '?'}) — {stats['stage_hint']}{cal_str}{prof_str}{bday_note}"
            )
        if has_child_cals:
            lines.append(
                "- Events tagged `[Stundenplan/Kind]` come from a child/school calendar: do NOT score consecutive school lessons as adult pacing stress; follow each child's Rules/Notes strictly for routine tasks (meals, gym bag, pickup) and respect `[NO-PREP]`."
            )

    return "\n".join(lines)


def format_children_context_block(
    config: dict[str, Any],
    target_date: datetime.date | None = None,
) -> str:
    """Backward-compatible wrapper returning household/children context."""
    return format_household_context_block(config, target_date)


def format_children_day_alerts(
    config: dict[str, Any],
    target_date: datetime.date,
) -> str:
    """Return compact per-day child alerts ONLY when a child's birthday is within 14 days."""
    children = [m for m in normalize_household_members(config) if m["role"] == MEMBER_ROLE_CHILD]
    alerts: list[str] = []
    for child in children:
        stats = _resolve_child_member_stats(child, target_date)
        days_until = stats.get("days_until_birthday")
        if days_until == 0:
            alerts.append(f"- {child['name']} ({stats['age_display']}): 🎂 BIRTHDAY TODAY!")
        elif isinstance(days_until, int) and 1 <= days_until <= 14:
            alerts.append(
                f"- {child['name']} ({stats['age_display']}): 🎂 Upcoming birthday in {days_until} days (plan gift/party prep ahead)"
            )
    return "\n".join(alerts)


def build_system_prompt(
    config: dict[str, Any],
    feedback_history: list[FeedbackEntry],
    batch_mode: bool = False,
) -> str:
    """Build compact system instruction prompt covering 5 factors, household profiles, defaults & JSON schema."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    household_block = format_household_context_block(config)
    weights = get_factor_weights(config)
    prep_min = int(config.get(CONF_DEFAULT_PREP_MINUTES, DEFAULT_PREP_MINUTES))
    travel_min = int(config.get(CONF_DEFAULT_TRAVEL_MINUTES, DEFAULT_TRAVEL_MINUTES))
    followup_min = int(config.get(CONF_DEFAULT_FOLLOWUP_MINUTES, DEFAULT_FOLLOWUP_MINUTES))

    lang_instruction = (
        "Respond in German for natural text fields (reasoning, title, checklist, recommendations, top_stressor)."
        if language.startswith("de")
        else "Respond in English for natural text fields."
    )

    feedback_section = ""
    if feedback_history:
        fb_lines = [
            f"- {e.date_str}: AI {e.previous_score}%->User {e.perceived_score}% ({e.adjustment})"
            + (f" [{e.note}]" if e.note else "")
            for e in feedback_history[-6:]
        ]
        feedback_section = "\nCalibration:\n" + "\n".join(fb_lines) + "\n"

    day_schema = (
        '{"score":0-100,"top_stressor":"2-5 words","reasoning":"2 short sentences",'
        '"factor_breakdown":{"pacing_context":0-100,"social_battery":0-100,"mobility":0-100,"ha_context":0-100,"hidden_work":0-100},'
        '"hidden_tasks":[{"parent_event_summary":"exact event summary","title":"short title","category":"prep|travel|packing|followup|mental_note",'
        '"placement_mode":"same_day|previous_evening|load_balanced","lead_days":"0-14 (0=same,1=prev_eve,1-4=errands,7-14=major)",'
        '"offset_type":"before|after","offset_minutes":20,"duration_minutes":"5-60","load_contribution":"1-35","checklist":["step1","step2"]}],'
        '"recommendations":["tip1","tip2"]}'
    )
    output_contract = (
        f'Return ONLY valid JSON object (no markdown) with key "days" mapping each YYYY-MM-DD date string to {day_schema}: {{"days":{{"YYYY-MM-DD":...}}}}'
        if batch_mode
        else f"Return ONLY valid JSON object (no markdown): {day_schema}"
    )

    return (
        "Cognitive Load & Invisible Mental Work Analyst for Home Assistant. Score daily load (0-100) across 5 factors and uncover hidden head-tasks.\n"
        f"{lang_instruction}\n"
        f"{household_block}\n"
        "Factors:\n"
        f"1. `{FACTOR_PACING}` (weight {weights[FACTOR_PACING]:.2f}x): tight gaps (<15m), overlaps, domain switches\n"
        f"2. `{FACTOR_SOCIAL}` (weight {weights[FACTOR_SOCIAL]:.2f}x): social drain, evening/group/high-stakes vs recovery\n"
        f"3. `{FACTOR_MOBILITY}` (weight {weights[FACTOR_MOBILITY]:.2f}x): travel, location changes, bad weather\n"
        f"4. `{FACTOR_HA_CONTEXT}` (weight {weights[FACTOR_HA_CONTEXT]:.2f}x): open todos, low energy/recovery, harsh weather\n"
        f"5. `{FACTOR_HIDDEN_WORK}` (weight {weights[FACTOR_HIDDEN_WORK]:.2f}x): docs, gifts, packing, prep, follow-up\n"
        "Events tagged [NO-PREP] (or #no-prep/#kein-packen): NEVER create hidden tasks for them.\n"
        f"Task defaults: prep={prep_min}m, travel={travel_min}m, followup={followup_min}m.{feedback_section}\n"
        f"{output_contract}"
    )


def _format_compact_day_block(
    target_date: datetime.date,
    events: list[CalendarEventInput],
    ha_context: dict[str, Any],
    config: dict[str, Any],
    include_live_context: bool = True,
) -> str:
    """Render one day's compact event & context block (shared by single-day and batch prompts)."""
    weekday = target_date.strftime("%A")
    if not events:
        events_text = "No events."
    else:
        lines = []
        for idx, ev in enumerate(sorted(events, key=lambda x: x.start), start=1):
            time_str = "ALL-DAY" if ev.all_day else f"{ev.start.strftime('%H:%M')}-{ev.end.strftime('%H:%M')}"
            loc = f" @{ev.location}" if ev.location else ""
            desc = f" | {ev.description[:120]}" if ev.description else ""
            src_raw = ev.source_calendar
            src = ""
            if "[stundenplan/kind" in src_raw.lower():
                bracket_idx = src_raw.find("[Stundenplan/Kind")
                src = f" {src_raw[bracket_idx:]}" if bracket_idx != -1 else " [Stundenplan/Kind]"
            elif "[erwachsener:" in src_raw.lower():
                bracket_idx = src_raw.find("[Erwachsener:")
                src = f" {src_raw[bracket_idx:]}" if bracket_idx != -1 else ""
            no_prep = " [NO-PREP]" if event_has_no_prep_tag(ev) else ""
            lines.append(f"{idx}. [{time_str}] {ev.summary}{loc}{desc}{src}{no_prep}")
        events_text = "\n".join(lines)

    parts = [f"Date: {target_date.isoformat()} ({weekday})", events_text]

    gaps = _build_event_gaps(events)
    if gaps:
        parts.append("Gaps: " + "; ".join(gaps))

    child_alerts = format_children_day_alerts(config, target_date)
    if child_alerts:
        parts.append(child_alerts)

    ctx_items: list[str] = []
    todos = ha_context.get("open_todos", [])
    if todos:
        ctx_items.append(f"{len(todos)} open tasks")
    if include_live_context:
        energy = ha_context.get("energy_sensor")
        weather = ha_context.get("weather")
        if energy:
            ctx_items.append(f"energy={energy}")
        if weather:
            ctx_items.append(f"weather={weather}")
    if ctx_items:
        parts.append("HA: " + ", ".join(ctx_items))

    return "\n".join(parts)


def build_day_user_prompt(
    target_date: datetime.date,
    events: list[CalendarEventInput],
    ha_context: dict[str, Any],
    config: dict[str, Any],
    include_live_context: bool = True,
) -> str:
    """Build compact user prompt for one day's events and live HA context."""
    return _format_compact_day_block(
        target_date=target_date,
        events=events,
        ha_context=ha_context,
        config=config,
        include_live_context=include_live_context,
    )


def build_batch_user_prompt(
    days_items: list[tuple[datetime.date, list[CalendarEventInput], dict[str, Any], bool]],
    config: dict[str, Any],
) -> str:
    """Build compact multi-day user prompt bundling up to 7 uncached days per LLM call."""
    blocks = [
        _format_compact_day_block(
            target_date=day_date,
            events=day_events,
            ha_context=day_ctx,
            config=config,
            include_live_context=include_live,
        )
        for day_date, day_events, day_ctx, include_live in days_items
    ]
    return "Analyze each day below and return JSON under `days`:\n\n" + "\n---\n".join(blocks)


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


def _parse_day_payload(
    payload: dict[str, Any],
    target_date: datetime.date,
    events: list[CalendarEventInput],
    config: dict[str, Any],
    provider_used: str,
) -> DayLoadAnalysis:
    """Parse one day's JSON dict payload into a normalized DayLoadAnalysis."""
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

    no_prep_summaries = {
        ev.summary.strip().lower()
        for ev in events
        if event_has_no_prep_tag(ev) and ev.summary.strip()
    }
    hidden_tasks: list[HiddenTask] = []
    for item in payload.get("hidden_tasks", []) or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "prep")).lower()
        if category not in CATEGORY_PREFIXES:
            category = "prep"
        parent_summary = str(item.get("parent_event_summary", "")).strip()
        p_lower = parent_summary.lower()
        if p_lower and any(p_lower == np_sum or np_sum in p_lower or p_lower in np_sum for np_sum in no_prep_summaries):
            continue
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


def parse_llm_day_response(
    raw_text: str,
    target_date: datetime.date,
    events: list[CalendarEventInput],
    config: dict[str, Any],
    provider_used: str,
) -> DayLoadAnalysis:
    """Parse raw LLM JSON output into a normalized DayLoadAnalysis."""
    payload = _extract_json_object(raw_text)
    return _parse_day_payload(
        payload=payload,
        target_date=target_date,
        events=events,
        config=config,
        provider_used=provider_used,
    )


def parse_llm_batch_response(
    raw_text: str,
    days_map: dict[str, tuple[datetime.date, list[CalendarEventInput]]],
    config: dict[str, Any],
    provider_used: str,
) -> dict[str, DayLoadAnalysis]:
    """Parse raw multi-day batch JSON response into a dict keyed by YYYY-MM-DD."""
    payload = _extract_json_object(raw_text)
    raw_days = payload.get("days")
    if not isinstance(raw_days, dict):
        raw_days = payload
    parsed: dict[str, DayLoadAnalysis] = {}
    for day_str, day_payload in raw_days.items():
        if day_str not in days_map or not isinstance(day_payload, dict):
            continue
        target_date, events = days_map[day_str]
        parsed[day_str] = _parse_day_payload(
            payload=day_payload,
            target_date=target_date,
            events=events,
            config=config,
            provider_used=provider_used,
        )
    return parsed


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
        if "[stundenplan/kind" in e.source_calendar.lower() or e.source_calendar.lower().startswith("child:")
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
        if event_has_no_prep_tag(ev):
            continue
        text = f"{ev.summary} {ev.description}".lower()
        is_child_cal = "[stundenplan/kind" in ev.source_calendar.lower() or ev.source_calendar.lower().startswith("child:")
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
    """Build compact (system_prompt, user_prompt) to decompose To-Dos into small sub-tasks."""
    language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
    lang_line = "Respond in German." if language.startswith("de") else "Respond in English."
    system_prompt = (
        "Break large To-Dos into 20-45m starter steps for calm weekends or free weekday afternoons.\n"
        f"{lang_line}\n"
        "Priority order is high->low: 2 steps for [PRIO: HOCH]/[PRIO: MITTEL], <=1 brief step for [PRIO: NIEDRIG].\n"
        "Sunday rule (Germany): shops/Baumarkt/TÜV/offices/doctors/hotlines are closed (requires_open_business=true) "
        "and noisy tools/garden machinery (schleifen, bohren, sägen, mähen) are forbidden (is_noisy=true). Keep quiet indoor prep separate.\n"
        'Return ONLY JSON: {"steps":[{"source_todo":"exact summary","title":"short step","duration_minutes":30,'
        '"load_contribution":10,"requires_open_business":false,"is_noisy":false,"checklist":["step1","step2"]}]}'
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
        raw_desc = str(item.get("description") or "").strip()
        # Strip redundant priority/status metadata already encoded in [PRIO: ...]
        cleaned_desc = re.sub(r"(?:Prio|Priority|Status)\s*:[^|\n]+(?:\|\s*)?", "", raw_desc, flags=re.IGNORECASE).strip(" |-\n")
        due = str(item.get("due") or "").strip()
        rank, _ = _resolve_item_priority(item)
        prio_tag = f"[{prio_badges.get(rank, 'PRIO: NORMAL')}] "
        due_note = f" | Due:{due[:10]}" if due else ""
        desc_note = f" — {cleaned_desc[:220]}" if cleaned_desc else ""
        lines.append(f"{idx}. {prio_tag}{summary}{due_note}{desc_note}")

    user_prompt = (
        "Decompose open To-Dos into priority-ordered 20-45m steps (use details after `—` when given):\n"
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
