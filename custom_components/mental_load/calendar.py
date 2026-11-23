"""Calendar platform for Mental Load Analyzer (calendar.mental_load)."""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_SHOW_HIDDEN_TASK_BLOCKS, DOMAIN
from .coordinator import MentalLoadCoordinator
from .models import DayLoadAnalysis, HiddenTask


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mental Load calendar entity."""
    coordinator: MentalLoadCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MentalLoadCalendarEntity(coordinator, entry)])


def _format_day_description(day: DayLoadAnalysis) -> str:
    """Render a detailed, human-readable description for the calendar popup."""
    fb = day.factor_breakdown
    lines = [
        day.reasoning,
        "",
        "📊 5-Faktoren-Analyse:",
        f"• Taktung & Kontextwechsel: {fb.get('pacing_context', 0)}%",
        f"• Soziale Batterie & Profil: {fb.get('social_battery', 0)}%",
        f"• Ortswechsel & Mobilität: {fb.get('mobility', 0)}%",
        f"• HA Live-Kontext: {fb.get('ha_context', 0)}%",
        f"• Unsichtbare Kopfarbeit: {fb.get('hidden_work', 0)}%",
    ]
    if day.hidden_tasks:
        lines.extend(["", "🧠 Unsichtbare Kopf-Aufgaben:"])
        for task in day.hidden_tasks:
            time_range = f"{task.start.strftime('%H:%M')}-{task.end.strftime('%H:%M')}"
            lines.append(f"• [{time_range}] {task.title} (+{task.load_contribution}%)")
            for step in task.checklist:
                lines.append(f"    - [ ] {step}")
    if day.recommendations:
        lines.extend(["", "💡 Empfehlungen:"])
        for rec in day.recommendations:
            lines.append(f"• {rec}")
    lines.append(f"\nAnalyse-Quelle: {day.provider_used}")
    return "\n".join(lines).strip()


def _format_hidden_task_description(task: HiddenTask) -> str:
    """Render checklist & parent context for an invisible task calendar block."""
    lines = []
    if task.parent_event_summary:
        lines.append(f"Ausgelöst durch Termin: {task.parent_event_summary}")
    lines.append(f"Kategorie: {task.category} | Mental-Load-Anteil: +{task.load_contribution}%")
    if task.checklist:
        lines.extend(["", "Checkliste:"])
        for item in task.checklist:
            lines.append(f"• [ ] {item}")
    return "\n".join(lines)


class MentalLoadCalendarEntity(CoordinatorEntity[MentalLoadCoordinator], CalendarEntity):
    """Exposes daily Mental Load badges and uncovered invisible tasks in HA's calendar."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_calendar"
        self._attr_name = entry.title or "Mental Load"

    @property
    def event(self) -> CalendarEvent | None:
        """Return today's all-day Mental Load badge event."""
        data = self.coordinator.data or {}
        today = dt_util.now().date()
        day = data.get(today.isoformat())
        if day is None:
            return None
        return CalendarEvent(
            start=day.date,
            end=day.date + datetime.timedelta(days=1),
            summary=day.badge_summary,
            description=_format_day_description(day),
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> list[CalendarEvent]:
        """Return all-day badges and timed invisible head-task blocks in the requested window."""
        data = self.coordinator.data or {}
        show_blocks = bool(self.coordinator.merged_config.get(CONF_SHOW_HIDDEN_TASK_BLOCKS, True))
        events: list[CalendarEvent] = []

        start_d = start_date.date()
        end_d = end_date.date()

        for day in sorted(data.values(), key=lambda d: d.date):
            if day.date < start_d or day.date > end_d:
                continue

            # 1. All-day summary badge
            events.append(
                CalendarEvent(
                    start=day.date,
                    end=day.date + datetime.timedelta(days=1),
                    summary=day.badge_summary,
                    description=_format_day_description(day),
                )
            )

            # 2. Timed invisible task blocks around original appointments
            if show_blocks:
                for task in day.hidden_tasks:
                    if task.end >= start_date and task.start <= end_date:
                        events.append(
                            CalendarEvent(
                                start=task.start,
                                end=task.end,
                                summary=f"{task.title} (+{task.load_contribution}%)",
                                description=_format_hidden_task_description(task),
                            )
                        )

        return events
