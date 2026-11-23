"""iCal (.ics) HTTP feed endpoint for subscribing to Mental Load on iOS, macOS, and Android."""
from __future__ import annotations

import datetime
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import MentalLoadCoordinator, make_checklist_item_uid


def _escape_ics(text: str) -> str:
    """Escape text per RFC 5545."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _format_ics_dt(dt_val: datetime.datetime) -> str:
    """Format a datetime as UTC YYYYMMDDTHHMMSSZ."""
    if dt_val.tzinfo is not None:
        utc_dt = dt_val.astimezone(datetime.timezone.utc)
    else:
        utc_dt = dt_val.replace(tzinfo=datetime.timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def build_ical_payload(coordinators: list[MentalLoadCoordinator]) -> str:
    """Render an RFC 5545 VCALENDAR feed of badges and uncovered invisible head-tasks."""
    now_stamp = _format_ics_dt(datetime.datetime.now(datetime.timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Mental Load Analyzer//Home Assistant//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Mental Load & Kopfarbeit",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
        "X-PUBLISHED-TTL:PT15M",
    ]

    for coord in coordinators:
        data = coord.data or {}
        completed = coord.completed_item_uids
        for day in sorted(data.values(), key=lambda d: d.date):
            start_d = day.date.strftime("%Y%m%d")
            end_d = (day.date + datetime.timedelta(days=1)).strftime("%Y%m%d")
            fb = day.factor_breakdown
            desc = (
                f"{day.reasoning}\n\n"
                f"Taktung: {fb.get('pacing_context', 0)}% | "
                f"Sozial: {fb.get('social_battery', 0)}% | "
                f"Mobilität: {fb.get('mobility', 0)}% | "
                f"HA: {fb.get('ha_context', 0)}% | "
                f"Kopfarbeit: {fb.get('hidden_work', 0)}%"
            )
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:ml-day-{day.date.isoformat()}@homeassistant.local",
                    f"DTSTAMP:{now_stamp}",
                    f"DTSTART;VALUE=DATE:{start_d}",
                    f"DTEND;VALUE=DATE:{end_d}",
                    f"SUMMARY:{_escape_ics(day.badge_summary)}",
                    f"DESCRIPTION:{_escape_ics(desc)}",
                    "END:VEVENT",
                ]
            )

            for task in day.hidden_tasks:
                checklist_lines = []
                for idx, step in enumerate(task.checklist):
                    mark = "[x]" if make_checklist_item_uid(task.uid, idx) in completed else "[ ]"
                    checklist_lines.append(f"{mark} {step}")
                task_desc = (
                    f"Termin: {task.parent_event_summary} ({task.parent_event_date or task.scheduled_date})\n"
                    + "\n".join(checklist_lines)
                ).strip()
                ical_summary = f"{task.title} (+{task.load_contribution}%)"
                if task.parent_event_date and task.parent_event_date != task.scheduled_date:
                    p_short = f"{task.parent_event_date[8:10]}.{task.parent_event_date[5:7]}."
                    ical_summary = f"{task.title} [🔗 für {p_short}] (+{task.load_contribution}%)"
                lines.extend(
                    [
                        "BEGIN:VEVENT",
                        f"UID:ml-task-{task.uid}@homeassistant.local",
                        f"DTSTAMP:{now_stamp}",
                        f"DTSTART:{_format_ics_dt(task.start)}",
                        f"DTEND:{_format_ics_dt(task.end)}",
                        f"SUMMARY:{_escape_ics(ical_summary)}",
                        f"DESCRIPTION:{_escape_ics(task_desc)}",
                        "END:VEVENT",
                    ]
                )

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class MentalLoadICalView(HomeAssistantView):
    """Serves a live iCal (.ics) feed authenticated via per-entry URL token."""

    url = "/api/mental_load/calendar.ics"
    name = "api:mental_load:ical"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET /api/mental_load/calendar.ics?token=..."""
        token = request.query.get("token", "").strip()
        coordinators = [
            c
            for c in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(c, MentalLoadCoordinator)
        ]
        matched = [c for c in coordinators if token and c.ical_token == token]
        if not matched:
            return web.Response(status=401, text="Unauthorized: invalid or missing token")

        payload = build_ical_payload(matched)
        return web.Response(
            status=200,
            text=payload,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
