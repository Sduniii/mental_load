"""Sensor platform for Mental Load Analyzer."""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LEVEL_CRITICAL, LEVEL_HIGH, LEVEL_MEDIUM
from .coordinator import MentalLoadCoordinator
from .models import DayLoadAnalysis

LEVEL_ICONS = {
    "low": "mdi:brain",
    LEVEL_MEDIUM: "mdi:head-lightbulb-outline",
    LEVEL_HIGH: "mdi:head-alert-outline",
    LEVEL_CRITICAL: "mdi:head-flash",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mental Load sensor entities."""
    coordinator: MentalLoadCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MentalLoadDaySensor(coordinator, entry, offset_days=0, key="today", name="Mental Load Today"),
            MentalLoadDaySensor(coordinator, entry, offset_days=1, key="tomorrow", name="Mental Load Tomorrow"),
            MentalLoadWeekAverageSensor(coordinator, entry),
            MentalLoadPeakDaySensor(coordinator, entry),
            MentalLoadHiddenTasksSensor(coordinator, entry),
        ]
    )


class MentalLoadBaseSensor(CoordinatorEntity[MentalLoadCoordinator], SensorEntity):
    """Base entity for Mental Load sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name

    def _get_day(self, offset_days: int) -> DayLoadAnalysis | None:
        target = (dt_util.now().date() + datetime.timedelta(days=offset_days)).isoformat()
        return (self.coordinator.data or {}).get(target)


class MentalLoadDaySensor(MentalLoadBaseSensor):
    """Reports the 0-100% mental load score for today or tomorrow."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MentalLoadCoordinator,
        entry: ConfigEntry,
        offset_days: int,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry, key, name)
        self._offset_days = offset_days

    @property
    def native_value(self) -> int | None:
        day = self._get_day(self._offset_days)
        return day.score if day else None

    @property
    def icon(self) -> str:
        day = self._get_day(self._offset_days)
        return LEVEL_ICONS.get(day.level if day else "low", "mdi:brain")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._get_day(self._offset_days)
        if not day:
            return {}
        return {
            "date": day.date.isoformat(),
            "level": day.level,
            "base_score": day.base_score or day.score,
            "relief_points": day.relief_points,
            "project_baseline_load": day.project_baseline_load,
            "open_project_count": day.open_project_count,
            "badge_summary": day.badge_summary,
            "reasoning": day.reasoning,
            "top_stressor": day.top_stressor,
            "factor_breakdown": day.factor_breakdown,
            "hidden_tasks": [t.to_dict() for t in day.hidden_tasks],
            "linked_prep_tasks": day.linked_prep_tasks,
            "completed_item_uids": sorted(self.coordinator.completed_item_uids),
            "recommendations": day.recommendations,
            "provider_used": day.provider_used,
            "cached": day.cached,
        }


class MentalLoadWeekAverageSensor(MentalLoadBaseSensor):
    """Reports average mental load across the configured forecast horizon."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-range"

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "week_average", "Mental Load Week Average")

    @property
    def native_value(self) -> int | None:
        days = list((self.coordinator.data or {}).values())
        if not days:
            return None
        return round(sum(d.score for d in days) / len(days))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        days = sorted((self.coordinator.data or {}).values(), key=lambda d: d.date)
        if not days:
            return {}
        peak = max(days, key=lambda d: d.score)
        return {
            "daily_scores": [
                {
                    "date": d.date.isoformat(),
                    "score": d.score,
                    "level": d.level,
                    "badge_summary": d.badge_summary,
                    "hidden_tasks_count": len(d.hidden_tasks),
                    "project_baseline_load": d.project_baseline_load,
                    "open_project_count": d.open_project_count,
                }
                for d in days
            ],
            "days_detail": {d.date.isoformat(): d.to_dict() for d in days},
            "completed_item_uids": sorted(self.coordinator.completed_item_uids),
            "ical_path": f"/api/mental_load/calendar.ics?token={self.coordinator.ical_token}",
            "peak_date": peak.date.isoformat(),
            "peak_score": peak.score,
            "total_hidden_tasks_week": sum(len(d.hidden_tasks) for d in days),
        }


class MentalLoadPeakDaySensor(MentalLoadBaseSensor):
    """Reports the highest-load day in the upcoming horizon."""

    _attr_icon = "mdi:alert-decagram-outline"

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "peak_day", "Mental Load Peak Day")

    @property
    def native_value(self) -> str | None:
        days = list((self.coordinator.data or {}).values())
        if not days:
            return None
        peak = max(days, key=lambda d: d.score)
        return f"{peak.date.isoformat()} ({peak.score}%)"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        days = list((self.coordinator.data or {}).values())
        if not days:
            return {}
        peak = max(days, key=lambda d: d.score)
        return {
            "date": peak.date.isoformat(),
            "score": peak.score,
            "level": peak.level,
            "badge_summary": peak.badge_summary,
            "top_stressor": peak.top_stressor,
        }


class MentalLoadHiddenTasksSensor(MentalLoadBaseSensor):
    """Counts invisible head-tasks discovered for today and exposes their checklist."""

    _attr_native_unit_of_measurement = "tasks"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:head-cog-outline"

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "hidden_tasks_today", "Hidden Head Tasks Today")

    @property
    def native_value(self) -> int:
        day = self._get_day(0)
        return len(day.hidden_tasks) if day else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._get_day(0)
        if not day:
            return {"tasks": [], "checklist_markdown": ""}
        md_lines = []
        for t in day.hidden_tasks:
            md_lines.append(f"- **{t.start.strftime('%H:%M')}** {t.title} (+{t.load_contribution}%)")
            for step in t.checklist:
                md_lines.append(f"  - [ ] {step}")
        return {
            "tasks": [t.to_dict() for t in day.hidden_tasks],
            "completed_item_uids": sorted(self.coordinator.completed_item_uids),
            "checklist_markdown": "\n".join(md_lines),
        }
