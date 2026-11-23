"""Coordinator for Mental Load Analyzer: caching, 5-factor context, todo sync, and reactions."""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY,
    CONF_AUTO_PUSH_TODO,
    CONF_BASE_URL,
    CONF_CALENDARS,
    CONF_CONTEXT_ENERGY_SENSOR,
    CONF_CONTEXT_TODOS,
    CONF_CONTEXT_WEATHER,
    CONF_CRITICAL_THRESHOLD,
    CONF_HORIZON_DAYS,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_NOTIFY_SERVICE,
    CONF_PROVIDER,
    CONF_REACTION_ENTITY,
    CONF_REACTION_ON_CRITICAL,
    CONF_REACTION_ON_WARNING,
    CONF_TARGET_TODO_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_USER_PROFILE,
    CONF_WARNING_THRESHOLD,
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WARNING_THRESHOLD,
    DOMAIN,
    EVENT_ANALYSIS_UPDATED,
    EVENT_THRESHOLD_EXCEEDED,
    LEVEL_CRITICAL,
    LEVEL_HIGH,
    LEVEL_ORDER,
    MAX_FEEDBACK_HISTORY,
    STORAGE_VERSION,
)
from .llm_providers import LLMProviderClient
from .models import CalendarEventInput, DayLoadAnalysis, FeedbackEntry
from .prompt_builder import get_factor_weights

_LOGGER = logging.getLogger(__name__)

ADJUSTMENT_DELTAS = {
    "much_lower": -25,
    "lower": -12,
    "accurate": 0,
    "higher": 12,
    "much_higher": 25,
}


def _parse_event_boundary(raw_value: Any, tz: datetime.tzinfo | None) -> tuple[datetime.datetime, bool]:
    """Parse an HA calendar event start/end value into (aware datetime, is_all_day)."""
    if isinstance(raw_value, datetime.datetime):
        dt_val = raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=tz)
        return dt_val, False
    if isinstance(raw_value, datetime.date):
        return datetime.datetime.combine(raw_value, datetime.time.min, tzinfo=tz), True

    text = str(raw_value)
    if "T" not in text and len(text) == 10:
        d_val = datetime.date.fromisoformat(text)
        return datetime.datetime.combine(d_val, datetime.time.min, tzinfo=tz), True
    dt_val = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=tz)
    return dt_val, False


class MentalLoadCoordinator(DataUpdateCoordinator[dict[str, DayLoadAnalysis]]):
    """Coordinates calendar reading, AI scoring, caching, todo push, and reactions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        llm_client: LLMProviderClient | None = None,
    ) -> None:
        self.entry = entry
        config = self.merged_config
        interval_min = max(10, int(config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)))

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=datetime.timedelta(minutes=interval_min),
        )
        self.llm_client = llm_client or LLMProviderClient(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}"
        )
        self._state_loaded = False
        self._cache: dict[str, dict[str, Any]] = {}
        self.feedback_history: list[FeedbackEntry] = []
        self._pushed_todo_uids: set[str] = set()
        self._notified_thresholds: dict[str, str] = {}
        self._force_refresh = False

    @property
    def merged_config(self) -> dict[str, Any]:
        """Return ConfigEntry data merged with latest options."""
        merged = dict(self.entry.data)
        merged.update(self.entry.options)
        return merged

    async def async_load_state(self) -> None:
        """Load persistent cache, feedback memory, and deduplication state."""
        if self._state_loaded:
            return
        stored = await self._store.async_load() or {}
        self._cache = dict(stored.get("cache", {}))
        self.feedback_history = [
            FeedbackEntry.from_dict(item)
            for item in stored.get("feedback_history", [])
        ]
        self._pushed_todo_uids = set(stored.get("pushed_todo_uids", []))
        self._notified_thresholds = dict(stored.get("notified_thresholds", {}))
        self._state_loaded = True

    async def _async_save_state(self) -> None:
        """Persist cache, feedback memory, and deduplication markers."""
        await self._store.async_save(
            {
                "cache": self._cache,
                "feedback_history": [f.to_dict() for f in self.feedback_history[-MAX_FEEDBACK_HISTORY:]],
                "pushed_todo_uids": sorted(self._pushed_todo_uids),
                "notified_thresholds": self._notified_thresholds,
            }
        )

    async def async_force_refresh(self, clear_cache: bool = False) -> None:
        """Force immediate re-evaluation of the forecast horizon."""
        await self.async_load_state()
        if clear_cache:
            self._cache.clear()
        self._force_refresh = True
        try:
            await self.async_refresh()
        finally:
            self._force_refresh = False

    async def _async_update_data(self) -> dict[str, DayLoadAnalysis]:
        """Fetch calendars + HA context, score each day, sync todos, and run reactions."""
        await self.async_load_state()
        config = self.merged_config
        horizon = max(1, min(14, int(config.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS))))

        now = dt_util.now()
        today = now.date()
        tz = now.tzinfo
        start_dt = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
        end_dt = datetime.datetime.combine(
            today + datetime.timedelta(days=horizon), datetime.time.min, tzinfo=tz
        )

        events_by_day = await self._async_fetch_events_by_day(start_dt, end_dt, horizon, today, tz)
        ha_context = await self._async_collect_ha_context(config)

        results: dict[str, DayLoadAnalysis] = {}
        for offset in range(horizon):
            day_date = today + datetime.timedelta(days=offset)
            day_str = day_date.isoformat()
            day_events = events_by_day.get(day_str, [])
            fingerprint = self._compute_day_fingerprint(day_str, day_events, ha_context, config)

            cached_entry = self._cache.get(day_str)
            if (
                not self._force_refresh
                and cached_entry
                and cached_entry.get("fingerprint") == fingerprint
            ):
                analysis = DayLoadAnalysis.from_dict(cached_entry["analysis"])
                analysis.cached = True
                results[day_str] = analysis
                continue

            analysis = await self.llm_client.async_analyze_day(
                target_date=day_date,
                events=day_events,
                ha_context=ha_context,
                config=config,
                feedback_history=self.feedback_history,
            )
            self._cache[day_str] = {
                "fingerprint": fingerprint,
                "analysis": analysis.to_dict(),
            }
            results[day_str] = analysis

        # Prune stale cache entries older than yesterday
        cutoff = (today - datetime.timedelta(days=1)).isoformat()
        for stale_key in [k for k in self._cache if k < cutoff]:
            self._cache.pop(stale_key, None)

        if config.get(CONF_AUTO_PUSH_TODO) and config.get(CONF_TARGET_TODO_ENTITY):
            await self.async_push_hidden_tasks_to_todo(
                target_date=today.isoformat(),
                todo_entity=config[CONF_TARGET_TODO_ENTITY],
                analyses=results,
                save_after=False,
            )

        today_analysis = results.get(today.isoformat())
        tomorrow_analysis = results.get((today + datetime.timedelta(days=1)).isoformat())
        if today_analysis is not None:
            await self._async_evaluate_reactions(today_analysis, tomorrow_analysis, config)

        await self._async_save_state()

        self.hass.bus.async_fire(
            EVENT_ANALYSIS_UPDATED,
            {
                "entry_id": self.entry.entry_id,
                "today_score": today_analysis.score if today_analysis else None,
                "tomorrow_score": tomorrow_analysis.score if tomorrow_analysis else None,
            },
        )
        return results

    def _compute_day_fingerprint(
        self,
        day_str: str,
        events: list[CalendarEventInput],
        ha_context: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        """Compute deterministic SHA-256 hash to skip redundant LLM calls."""
        payload = {
            "date": day_str,
            "events": [e.to_dict() for e in sorted(events, key=lambda x: (x.start.isoformat(), x.summary))],
            "ha_context": ha_context,
            "weights": get_factor_weights(config),
            "profile": config.get(CONF_USER_PROFILE, ""),
            "provider": config.get(CONF_PROVIDER, ""),
            "model": config.get(CONF_MODEL, ""),
            "base_url": config.get(CONF_BASE_URL, ""),
            "language": config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            "feedback_count": len(self.feedback_history),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _async_fetch_events_by_day(
        self,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        horizon: int,
        today: datetime.date,
        tz: datetime.tzinfo | None,
    ) -> dict[str, list[CalendarEventInput]]:
        """Read events from all configured source calendars without modifying them."""
        calendars = list(self.merged_config.get(CONF_CALENDARS, []))
        buckets: dict[str, list[CalendarEventInput]] = {
            (today + datetime.timedelta(days=i)).isoformat(): [] for i in range(horizon)
        }
        if not calendars:
            return buckets

        try:
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendars,
                    "start_date_time": start_dt.isoformat(),
                    "end_date_time": end_dt.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to fetch events from %s: %s", calendars, err)
            return buckets

        if not isinstance(response, dict):
            return buckets

        for cal_id, cal_payload in response.items():
            raw_events = cal_payload.get("events", []) if isinstance(cal_payload, dict) else []
            for idx, item in enumerate(raw_events):
                ev_start, all_day = _parse_event_boundary(item.get("start"), tz)
                ev_end, _ = _parse_event_boundary(item.get("end", item.get("start")), tz)
                summary = str(item.get("summary", "Termin"))
                uid = str(item.get("uid") or f"{cal_id}:{idx}:{summary}:{ev_start.isoformat()}")
                ev = CalendarEventInput(
                    uid=uid,
                    summary=summary,
                    start=ev_start,
                    end=ev_end,
                    all_day=all_day,
                    description=str(item.get("description") or ""),
                    location=str(item.get("location") or ""),
                    source_calendar=str(cal_id),
                )
                # Assign event to each day within horizon that it overlaps
                ev_start_date = ev_start.date()
                ev_end_date = (ev_end - datetime.timedelta(seconds=1)).date() if ev_end > ev_start else ev_start_date
                for day_key in buckets:
                    d = datetime.date.fromisoformat(day_key)
                    if ev_start_date <= d <= ev_end_date:
                        buckets[day_key].append(ev)

        return buckets

    async def _async_collect_ha_context(self, config: dict[str, Any]) -> dict[str, Any]:
        """Collect Factor 4 live Home Assistant context (todos, energy sensor, weather)."""
        open_todos: list[str] = []
        for todo_entity in config.get(CONF_CONTEXT_TODOS, []) or []:
            try:
                resp = await self.hass.services.async_call(
                    "todo",
                    "get_items",
                    {"entity_id": todo_entity, "status": ["needs_action"]},
                    blocking=True,
                    return_response=True,
                )
                items = (resp or {}).get(todo_entity, {}).get("items", [])
                for item in items:
                    summary = str(item.get("summary", "")).strip()
                    if summary:
                        open_todos.append(summary)
            except Exception:  # noqa: BLE001
                state = self.hass.states.get(todo_entity)
                if state and state.state.isdigit():
                    open_todos.extend([f"{todo_entity} item"] * int(state.state))

        energy_info = ""
        energy_entity = config.get(CONF_CONTEXT_ENERGY_SENSOR)
        if energy_entity:
            state = self.hass.states.get(energy_entity)
            if state and state.state not in ("unknown", "unavailable"):
                unit = state.attributes.get("unit_of_measurement", "")
                energy_info = f"{state.state} {unit}".strip()

        weather_info = ""
        weather_entity = config.get(CONF_CONTEXT_WEATHER)
        if weather_entity:
            state = self.hass.states.get(weather_entity)
            if state and state.state not in ("unknown", "unavailable"):
                temp = state.attributes.get("temperature")
                weather_info = f"{state.state}" + (f", {temp}°" if temp is not None else "")

        return {
            "open_todos": open_todos[:25],
            "energy_sensor": energy_info,
            "weather": weather_info,
        }

    async def async_submit_feedback(
        self,
        target_date: str | None = None,
        perceived_score: int | None = None,
        adjustment: str = "accurate",
        note: str = "",
    ) -> FeedbackEntry:
        """Record user feedback (Factor 5 learning) and refresh that day's analysis."""
        await self.async_load_state()
        date_str = target_date or dt_util.now().date().isoformat()
        current_data = self.data or {}
        existing = current_data.get(date_str)
        prev_score = existing.score if existing else 50

        if perceived_score is None:
            delta = ADJUSTMENT_DELTAS.get(adjustment, 0)
            felt = max(0, min(100, prev_score + delta))
        else:
            felt = max(0, min(100, int(perceived_score)))

        entry = FeedbackEntry(
            timestamp=dt_util.now().isoformat(),
            date_str=date_str,
            previous_score=prev_score,
            perceived_score=felt,
            adjustment=adjustment,
            note=note.strip(),
        )
        self.feedback_history.append(entry)
        self.feedback_history = self.feedback_history[-MAX_FEEDBACK_HISTORY:]
        self._cache.pop(date_str, None)
        await self._async_save_state()

        await self.async_force_refresh(clear_cache=False)
        return entry

    async def async_push_hidden_tasks_to_todo(
        self,
        target_date: str | None = None,
        todo_entity: str | None = None,
        analyses: dict[str, DayLoadAnalysis] | None = None,
        save_after: bool = True,
    ) -> int:
        """Push uncovered invisible head-tasks into a target todo.* entity without duplicates."""
        await self.async_load_state()
        config = self.merged_config
        target_todo = todo_entity or config.get(CONF_TARGET_TODO_ENTITY)
        if not target_todo:
            return 0

        source_map = analyses if analyses is not None else (self.data or {})
        date_str = target_date or dt_util.now().date().isoformat()
        day_analysis = source_map.get(date_str)
        if day_analysis is None:
            return 0

        pushed_count = 0
        for task in day_analysis.hidden_tasks:
            if task.uid in self._pushed_todo_uids:
                continue
            item_name = f"{task.title} ({task.parent_event_summary})" if task.parent_event_summary else task.title
            checklist_lines = "\n".join(f"- [ ] {step}" for step in task.checklist)
            description = f"{checklist_lines}\n\n[ML-{task.uid}]".strip()
            try:
                await self.hass.services.async_call(
                    "todo",
                    "add_item",
                    {
                        "entity_id": target_todo,
                        "item": item_name,
                        "description": description,
                        "due_datetime": task.start.isoformat(),
                    },
                    blocking=True,
                )
                self._pushed_todo_uids.add(task.uid)
                pushed_count += 1
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed pushing hidden task '%s' to %s: %s", item_name, target_todo, err)

        if save_after and pushed_count > 0:
            await self._async_save_state()
        return pushed_count

    async def _async_evaluate_reactions(
        self,
        today_analysis: DayLoadAnalysis,
        tomorrow_analysis: DayLoadAnalysis | None,
        config: dict[str, Any],
    ) -> None:
        """Fire HA events, notifications, and entity actions when load thresholds are exceeded."""
        warn_th = int(config.get(CONF_WARNING_THRESHOLD, DEFAULT_WARNING_THRESHOLD))
        crit_th = int(config.get(CONF_CRITICAL_THRESHOLD, DEFAULT_CRITICAL_THRESHOLD))

        triggered_level: str | None = None
        if today_analysis.score >= crit_th:
            triggered_level = LEVEL_CRITICAL
        elif today_analysis.score >= warn_th:
            triggered_level = LEVEL_HIGH

        if triggered_level is None:
            return

        date_str = today_analysis.date.isoformat()
        prev_level = self._notified_thresholds.get(date_str)
        if prev_level and LEVEL_ORDER.get(prev_level, 0) >= LEVEL_ORDER.get(triggered_level, 0):
            return

        self._notified_thresholds[date_str] = triggered_level

        event_payload = {
            "entry_id": self.entry.entry_id,
            "date": date_str,
            "score": today_analysis.score,
            "level": triggered_level,
            "badge_summary": today_analysis.badge_summary,
            "top_stressor": today_analysis.top_stressor,
            "hidden_tasks_count": len(today_analysis.hidden_tasks),
            "recommendations": today_analysis.recommendations,
            "tomorrow_score": tomorrow_analysis.score if tomorrow_analysis else None,
        }
        self.hass.bus.async_fire(EVENT_THRESHOLD_EXCEEDED, event_payload)

        # Optional Notification
        notify_service = str(config.get(CONF_NOTIFY_SERVICE) or "").strip()
        if notify_service:
            tasks_preview = "\n".join(
                f"• {t.start.strftime('%H:%M')} {t.title}" for t in today_analysis.hidden_tasks[:5]
            )
            message = f"{today_analysis.reasoning}\n\n{tasks_preview}".strip()
            await self._async_call_service_string(
                notify_service,
                {
                    "title": today_analysis.badge_summary,
                    "message": message,
                },
            )

        # Optional Smart Home Entity Reaction
        reaction_entity = str(config.get(CONF_REACTION_ENTITY) or "").strip()
        should_react = (
            (triggered_level == LEVEL_CRITICAL and config.get(CONF_REACTION_ON_CRITICAL, True))
            or (triggered_level == LEVEL_HIGH and config.get(CONF_REACTION_ON_WARNING, False))
        )
        if reaction_entity and should_react:
            domain = reaction_entity.split(".", 1)[0] if "." in reaction_entity else ""
            service_map = {
                "scene": ("scene", "turn_on"),
                "script": ("script", "turn_on"),
                "input_boolean": ("input_boolean", "turn_on"),
                "switch": ("switch", "turn_on"),
                "light": ("light", "turn_on"),
                "automation": ("automation", "trigger"),
            }
            if domain in service_map:
                srv_domain, srv_name = service_map[domain]
                try:
                    await self.hass.services.async_call(
                        srv_domain,
                        srv_name,
                        {"entity_id": reaction_entity},
                        blocking=False,
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Reaction entity call failed for %s: %s", reaction_entity, err)

    async def _async_call_service_string(self, service_str: str, data: dict[str, Any]) -> None:
        """Call a 'domain.service' string safely."""
        if "." not in service_str:
            _LOGGER.warning("Invalid notify service format '%s' (expected domain.service)", service_str)
            return
        domain, service = service_str.split(".", 1)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed calling %s.%s: %s", domain, service, err)
