"""Coordinator for Mental Load Analyzer: caching, 5-factor context, todo sync, and reactions."""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY,
    CONF_AUTO_PUSH_TODO,
    CONF_AUTO_SYNC_CALENDAR,
    CONF_BASE_URL,
    CONF_CALENDARS,
    CONF_CHILDREN_CALENDARS,
    CONF_CHILDREN_PROFILES,
    CONF_CONTEXT_ENERGY_SENSOR,
    CONF_CONTEXT_TODOS,
    CONF_CONTEXT_WEATHER,
    CONF_CRITICAL_THRESHOLD,
    CONF_HORIZON_DAYS,
    CONF_LANGUAGE,
    CONF_MAX_PROJECT_STEPS_WEEKDAY,
    CONF_MAX_PROJECT_STEPS_WEEKEND,
    CONF_MODEL,
    CONF_NOTIFY_SERVICE,
    CONF_PROJECT_BASELINE_CAP,
    CONF_PROJECT_BASELINE_PER_ITEM,
    CONF_PROJECT_PLANNING_ENABLED,
    CONF_PROVIDER,
    CONF_REACTION_ENTITY,
    CONF_REACTION_ON_CRITICAL,
    CONF_REACTION_ON_WARNING,
    CONF_TARGET_CALENDAR_ENTITY,
    CONF_TARGET_TODO_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_USER_PROFILE,
    CONF_WARNING_THRESHOLD,
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_PROJECT_STEPS_WEEKDAY,
    DEFAULT_MAX_PROJECT_STEPS_WEEKEND,
    DEFAULT_PROJECT_PLANNING_ENABLED,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WARNING_THRESHOLD,
    DOMAIN,
    EVENT_ANALYSIS_UPDATED,
    EVENT_THRESHOLD_EXCEEDED,
    FACTOR_HA_CONTEXT,
    LEVEL_CRITICAL,
    LEVEL_HIGH,
    LEVEL_ORDER,
    MAX_FEEDBACK_HISTORY,
    STORAGE_VERSION,
)
from .llm_providers import LLMProviderClient
from .models import CalendarEventInput, DayLoadAnalysis, FeedbackEntry, HiddenTask, score_to_level
from .prompt_builder import (
    _format_badge_summary,
    _make_task_uid,
    analyze_day_heuristic,
    compute_project_baseline,
    extract_todo_priority,
    get_factor_weights,
    infer_step_sunday_restrictions,
    sort_project_items_by_priority,
)

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


def make_checklist_item_uid(task_uid: str, step_index: int) -> str:
    """Create a stable UID for an individual checklist step inside a HiddenTask."""
    return f"{task_uid}:step:{step_index}"


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
        self.completed_item_uids: set[str] = set()
        self._synced_calendar_uids: set[str] = set()
        self.ical_token: str = secrets.token_urlsafe(24)
        self._notified_thresholds: dict[str, str] = {}
        self._force_refresh = False
        self._raw_analyses: dict[str, DayLoadAnalysis] = {}
        self._project_breakdown_cache: dict[str, Any] = {}
        self._project_steps: list[dict[str, Any]] = []
        self._last_open_project_count: int = 0
        self._last_open_project_items: list[dict[str, Any]] = []
        self._last_day_event_counts: dict[str, int] = {}

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
        self.completed_item_uids = set(stored.get("completed_item_uids", []))
        self._synced_calendar_uids = set(stored.get("synced_calendar_uids", []))
        if stored.get("ical_token"):
            self.ical_token = str(stored["ical_token"])
        self._notified_thresholds = dict(stored.get("notified_thresholds", {}))
        self._project_breakdown_cache = dict(stored.get("project_breakdown_cache", {}))
        self._state_loaded = True

    async def _async_save_state(self) -> None:
        """Persist cache, feedback memory, and deduplication markers."""
        await self._store.async_save(
            {
                "cache": self._cache,
                "feedback_history": [f.to_dict() for f in self.feedback_history[-MAX_FEEDBACK_HISTORY:]],
                "pushed_todo_uids": sorted(self._pushed_todo_uids),
                "completed_item_uids": sorted(self.completed_item_uids),
                "synced_calendar_uids": sorted(self._synced_calendar_uids),
                "ical_token": self.ical_token,
                "notified_thresholds": self._notified_thresholds,
                "project_breakdown_cache": self._project_breakdown_cache,
            }
        )

    async def async_force_refresh(self, clear_cache: bool = False) -> None:
        """Force immediate re-evaluation of the forecast horizon."""
        await self.async_load_state()
        if clear_cache:
            self._cache.clear()
            self._project_breakdown_cache.clear()
        self._force_refresh = True
        try:
            await self.async_refresh()
        finally:
            self._force_refresh = False

    async def _async_update_data(self) -> dict[str, DayLoadAnalysis]:
        """Fetch calendars + HA context, score each day, sync todos, and run reactions."""
        await self.async_load_state()
        config = self.merged_config
        horizon = max(1, min(30, int(config.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS))))

        now = dt_util.now()
        today = now.date()
        tz = now.tzinfo
        start_dt = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
        end_dt = datetime.datetime.combine(
            today + datetime.timedelta(days=horizon), datetime.time.min, tzinfo=tz
        )

        events_by_day = await self._async_fetch_events_by_day(start_dt, end_dt, horizon, today, tz)
        ha_context = await self._async_collect_ha_context(config)
        self._last_day_event_counts = {d_key: len(evs) for d_key, evs in events_by_day.items()}
        self._last_open_project_count = int(ha_context.get("open_todos_count", len(ha_context.get("open_todos", []))))
        self._last_open_project_items = list(ha_context.get("open_todo_details") or [])
        self._project_steps = await self._async_get_project_steps(ha_context, config)

        results: dict[str, DayLoadAnalysis] = {}
        uncached_days: list[tuple[datetime.date, str, list[CalendarEventInput], str, bool]] = []
        for offset in range(horizon):
            day_date = today + datetime.timedelta(days=offset)
            day_str = day_date.isoformat()
            day_events = events_by_day.get(day_str, [])
            include_live = offset <= 1
            fingerprint = self._compute_day_fingerprint(
                day_str,
                day_events,
                ha_context,
                config,
                include_live_context=include_live,
            )

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

            if not day_events:
                analysis = analyze_day_heuristic(
                    target_date=day_date,
                    events=[],
                    ha_context=ha_context,
                    config=config,
                    feedback_history=self.feedback_history,
                    provider_label="fast_empty_day",
                )
                self._cache[day_str] = {
                    "fingerprint": fingerprint,
                    "analysis": analysis.to_dict(),
                }
                results[day_str] = analysis
            else:
                uncached_days.append((day_date, day_str, day_events, fingerprint, include_live))

        if uncached_days:
            supports_batch = hasattr(type(self.llm_client), "async_analyze_days_batch") or (
                isinstance(getattr(self.llm_client, "__dict__", None), dict)
                and "async_analyze_days_batch" in self.llm_client.__dict__
            )
            if supports_batch:
                batch_size = 7
                for idx in range(0, len(uncached_days), batch_size):
                    chunk = uncached_days[idx : idx + batch_size]
                    batch_input = [
                        (d_date, d_events, ha_context, inc_live)
                        for d_date, _, d_events, _, inc_live in chunk
                    ]
                    batch_results = await self.llm_client.async_analyze_days_batch(
                        batch_input,
                        config,
                        self.feedback_history,
                    )
                    for d_date, d_str, d_events, fp, _ in chunk:
                        analysis = batch_results.get(d_str)
                        if analysis is None:
                            analysis = await self.llm_client.async_analyze_day(
                                target_date=d_date,
                                events=d_events,
                                ha_context=ha_context,
                                config=config,
                                feedback_history=self.feedback_history,
                            )
                        self._cache[d_str] = {
                            "fingerprint": fp,
                            "analysis": analysis.to_dict(),
                        }
                        results[d_str] = analysis
            else:
                for d_date, d_str, d_events, fp, _ in uncached_days:
                    analysis = await self.llm_client.async_analyze_day(
                        target_date=d_date,
                        events=d_events,
                        ha_context=ha_context,
                        config=config,
                        feedback_history=self.feedback_history,
                    )
                    self._cache[d_str] = {
                        "fingerprint": fp,
                        "analysis": analysis.to_dict(),
                    }
                    results[d_str] = analysis

        # Prune stale cache entries older than yesterday
        cutoff = (today - datetime.timedelta(days=1)).isoformat()
        for stale_key in [k for k in self._cache if k < cutoff]:
            self._cache.pop(stale_key, None)

        self._raw_analyses = results
        results = self._rebalance_and_score_horizon(
            results,
            today,
            tz,
            project_steps=self._project_steps,
            open_project_count=self._last_open_project_count,
            event_counts=self._last_day_event_counts,
        )

        if config.get(CONF_AUTO_PUSH_TODO) and config.get(CONF_TARGET_TODO_ENTITY):
            await self.async_push_hidden_tasks_to_todo(
                target_date=today.isoformat(),
                todo_entity=config[CONF_TARGET_TODO_ENTITY],
                analyses=results,
                save_after=False,
            )

        if config.get(CONF_AUTO_SYNC_CALENDAR) and config.get(CONF_TARGET_CALENDAR_ENTITY):
            await self.async_sync_to_external_calendar(
                target_calendar=config[CONF_TARGET_CALENDAR_ENTITY],
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

    @staticmethod
    def _quantize_ha_context_for_fingerprint(
        ha_context: dict[str, Any],
        include_live_context: bool,
    ) -> dict[str, str]:
        """Stabilize cache keys: ignore weather/energy beyond +1 day and bucket volatile decimals."""
        if not include_live_context:
            return {}
        raw_weather = str(ha_context.get("weather") or "").strip()
        weather_cond = raw_weather.split(",", 1)[0].strip().lower() if raw_weather else ""

        raw_energy = str(ha_context.get("energy_sensor") or "").strip()
        energy_bucket = ""
        if raw_energy:
            m_energy = re.search(r"(\d+(?:\.\d+)?)", raw_energy)
            energy_bucket = f"{round(float(m_energy.group(1)) / 10) * 10}%" if m_energy else raw_energy

        return {
            "energy_sensor": energy_bucket,
            "weather": weather_cond,
        }

    def _compute_day_fingerprint(
        self,
        day_str: str,
        events: list[CalendarEventInput],
        ha_context: dict[str, Any],
        config: dict[str, Any],
        include_live_context: bool = True,
    ) -> str:
        """Compute deterministic SHA-256 hash to skip redundant LLM calls."""
        # Keep volatile project todo backlog decoupled from per-day LLM cache, and
        # only include quantized live weather/energy on today & tomorrow (offset <= 1).
        day_ha_context = self._quantize_ha_context_for_fingerprint(ha_context, include_live_context)
        payload = {
            "date": day_str,
            "events": [e.to_dict() for e in sorted(events, key=lambda x: (x.start.isoformat(), x.summary))],
            "ha_context": day_ha_context,
            "weights": get_factor_weights(config),
            "profile": config.get(CONF_USER_PROFILE, ""),
            "children_profiles": config.get(CONF_CHILDREN_PROFILES, ""),
            "children_calendars": sorted(str(c) for c in (config.get(CONF_CHILDREN_CALENDARS, []) or [])),
            "provider": config.get(CONF_PROVIDER, ""),
            "model": config.get(CONF_MODEL, ""),
            "base_url": config.get(CONF_BASE_URL, ""),
            "language": config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            "feedback_count": len(self.feedback_history),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _async_get_project_steps(
        self,
        ha_context: dict[str, Any],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return cached or freshly decomposed 20-45 min project sub-tasks for ongoing To-Dos."""
        if not config.get(CONF_PROJECT_PLANNING_ENABLED, DEFAULT_PROJECT_PLANNING_ENABLED):
            return []
        project_items = list(ha_context.get("open_todo_details") or [])
        if not project_items:
            summaries = list(ha_context.get("open_todos") or [])
            project_items = [{"summary": str(s), "description": ""} for s in summaries if str(s).strip()]
        if not project_items:
            self._project_breakdown_cache = {}
            return []

        fp_payload = {
            "items": project_items[:40],
            "provider": config.get(CONF_PROVIDER, ""),
            "model": config.get(CONF_MODEL, ""),
            "language": config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        }
        fp = hashlib.sha256(
            json.dumps(fp_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        if (
            not self._force_refresh
            and self._project_breakdown_cache.get("fingerprint") == fp
            and isinstance(self._project_breakdown_cache.get("steps"), list)
        ):
            return list(self._project_breakdown_cache["steps"])

        breakdown_fn = getattr(self.llm_client, "async_breakdown_projects", None)
        steps: list[dict[str, Any]] = []
        if callable(breakdown_fn):
            candidate = await breakdown_fn(project_items, config)
            if isinstance(candidate, list):
                steps = candidate
        if not steps:
            from .prompt_builder import decompose_project_todos_heuristic

            steps = decompose_project_todos_heuristic(project_items, config)

        self._project_breakdown_cache = {"fingerprint": fp, "steps": steps}
        return list(steps)

    async def _async_fetch_events_by_day(
        self,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        horizon: int,
        today: datetime.date,
        tz: datetime.tzinfo | None,
    ) -> dict[str, list[CalendarEventInput]]:
        """Read events from all configured source calendars without modifying them."""
        main_calendars = [str(c) for c in (self.merged_config.get(CONF_CALENDARS, []) or []) if str(c).strip()]
        child_calendars = [
            str(c)
            for c in (self.merged_config.get(CONF_CHILDREN_CALENDARS, []) or [])
            if str(c).strip() and str(c) not in main_calendars
        ]
        calendars = main_calendars + child_calendars
        child_cal_set = set(child_calendars)
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
            source_label = f"{cal_id} [Stundenplan/Kind]" if str(cal_id) in child_cal_set else str(cal_id)
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
                    source_calendar=source_label,
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
        open_todo_details: list[dict[str, Any]] = []
        ref_today = dt_util.now().date()
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
                        raw_desc = str(item.get("description") or "")
                        clean_desc = re.sub(r"\[ANYTYPE-[^\]]+\]", "", raw_desc).strip()
                        due_raw = str(item.get("due") or "").strip()
                        rank, label = extract_todo_priority(
                            summary=summary,
                            description=clean_desc,
                            due=due_raw or None,
                            reference_date=ref_today,
                        )
                        open_todo_details.append(
                            {
                                "summary": summary,
                                "description": clean_desc[:450],
                                "due": due_raw[:10] if due_raw else "",
                                "priority_rank": rank,
                                "priority_label": label,
                                "entity_id": str(todo_entity),
                            }
                        )
            except Exception:  # noqa: BLE001
                state = self.hass.states.get(todo_entity)
                if state and state.state.isdigit():
                    for _ in range(int(state.state)):
                        open_todos.append(f"{todo_entity} item")
                        open_todo_details.append(
                            {
                                "summary": f"{todo_entity} item",
                                "description": "",
                                "due": "",
                                "priority_rank": 3,
                                "priority_label": "normal",
                                "entity_id": str(todo_entity),
                            }
                        )

        open_todo_details = sort_project_items_by_priority(open_todo_details)
        open_todos = [str(item["summary"]) for item in open_todo_details]

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
            "open_todos": open_todos[:100],
            "open_todos_count": len(open_todo_details),
            "open_todo_details": open_todo_details[:50],
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

    def _task_completion_ratio(self, task_uid: str, checklist: list[str]) -> float:
        """Return 0.0..1.0 ratio of completed steps for a hidden task."""
        step_count = len(checklist) if checklist else 1
        done = sum(
            1
            for idx in range(step_count)
            if make_checklist_item_uid(task_uid, idx) in self.completed_item_uids
        )
        return done / step_count

    def _rebalance_and_score_horizon(
        self,
        raw_results: dict[str, DayLoadAnalysis],
        today: datetime.date,
        tz: datetime.tzinfo | None = None,
        project_steps: list[dict[str, Any]] | None = None,
        open_project_count: int | None = None,
        event_counts: dict[str, int] | None = None,
    ) -> dict[str, DayLoadAnalysis]:
        """Apply capped project baseline, move prep tasks to earlier calm days, and schedule project steps."""
        config = self.merged_config
        language = str(config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        is_de = language.startswith("de")

        rebalanced: dict[str, DayLoadAnalysis] = {
            k: DayLoadAnalysis.from_dict(v.to_dict()) for k, v in raw_results.items()
        }
        proj_count = (
            open_project_count
            if open_project_count is not None
            else self._last_open_project_count
        )
        proj_items = list(self._last_open_project_items) if self._last_open_project_items else None
        baseline_load = compute_project_baseline(proj_count, config, project_items=proj_items)

        for day in rebalanced.values():
            day.base_score = day.base_score or day.score
            day.linked_prep_tasks = []
            day.project_baseline_load = baseline_load
            day.open_project_count = proj_count
            if baseline_load > 0:
                day.base_score = min(100, day.base_score + baseline_load)
                day.factor_breakdown[FACTOR_HA_CONTEXT] = min(
                    100,
                    max(day.factor_breakdown.get("ha_context", 0), baseline_load * 4),
                )
                if day.top_stressor in ("Freier Kalendertag", "Clear schedule", ""):
                    day.top_stressor = (
                        f"Projekt-Grundlast (+{baseline_load}%)"
                        if is_de
                        else f"Project Baseline (+{baseline_load}%)"
                    )
                    baseline_note = (
                        f" Zusätzlich erzeugen {proj_count} offene Hintergrund-Projekte eine gedeckelte Grundlast von +{baseline_load}%."
                        if is_de
                        else f" Ongoing open projects ({proj_count} items) add +{baseline_load}% capped background load."
                    )
                    if baseline_note.strip() not in day.reasoning:
                        day.reasoning = f"{day.reasoning}{baseline_note}".strip()

        for day_str in sorted(rebalanced.keys()):
            source_day = rebalanced[day_str]
            if source_day.date <= today:
                continue

            kept_tasks = []
            for task in source_day.hidden_tasks:
                if task.category in ("travel", "followup", "project_step") or task.placement_mode in ("same_day", "free_slot"):
                    kept_tasks.append(task)
                    continue

                target_day: DayLoadAnalysis | None = None
                target_hour = 17
                target_minute = 30

                if task.placement_mode == "previous_evening":
                    prev_str = (source_day.date - datetime.timedelta(days=1)).isoformat()
                    if prev_str in rebalanced and (source_day.date - datetime.timedelta(days=1)) >= today:
                        target_day = rebalanced[prev_str]
                        target_hour = 19
                        target_minute = 30
                elif task.placement_mode == "load_balanced":
                    max_lead = max(1, min(14, task.lead_days or 3))
                    min_lead = 7 if max_lead >= 7 else 1
                    candidates: list[DayLoadAnalysis] = []
                    for back in range(min_lead, max_lead + 1):
                        cand_date = source_day.date - datetime.timedelta(days=back)
                        if cand_date < today:
                            continue
                        cand = rebalanced.get(cand_date.isoformat())
                        if cand is not None:
                            candidates.append(cand)
                    if not candidates and min_lead > 1:
                        for back in range(1, min_lead):
                            cand_date = source_day.date - datetime.timedelta(days=back)
                            if cand_date < today:
                                continue
                            cand = rebalanced.get(cand_date.isoformat())
                            if cand is not None:
                                candidates.append(cand)
                    if candidates:
                        target_day = min(candidates, key=lambda c: (c.base_score, c.date))
                        target_hour = 17
                        target_minute = 30

                if target_day is None or target_day.date == source_day.date:
                    kept_tasks.append(task)
                    continue

                dur = max(datetime.timedelta(minutes=15), task.end - task.start)
                tzinfo = task.start.tzinfo or tz
                new_start = datetime.datetime.combine(
                    target_day.date,
                    datetime.time(target_hour, target_minute),
                    tzinfo=tzinfo,
                )
                task.parent_event_date = source_day.date.isoformat()
                task.scheduled_date = target_day.date.isoformat()
                task.lead_days = (source_day.date - target_day.date).days
                task.start = new_start
                task.end = new_start + dur
                target_day.hidden_tasks.append(task)
                target_day.hidden_tasks.sort(key=lambda t: t.start)

                source_day.linked_prep_tasks.append(
                    {
                        "uid": task.uid,
                        "title": task.title,
                        "scheduled_date": target_day.date.isoformat(),
                        "parent_event_date": source_day.date.isoformat(),
                        "parent_event_summary": task.parent_event_summary,
                        "load_contribution": task.load_contribution,
                        "checklist": list(task.checklist),
                        "placement_mode": task.placement_mode,
                    }
                )

                target_day.base_score = min(100, target_day.base_score + max(2, round(task.load_contribution * 0.4)))
                source_day.base_score = max(0, source_day.base_score - max(1, round(task.load_contribution * 0.25)))

            source_day.hidden_tasks = kept_tasks

        steps_to_schedule = (
            list(project_steps)
            if project_steps is not None
            else list(self._project_steps)
        )
        if config.get(CONF_PROJECT_PLANNING_ENABLED, DEFAULT_PROJECT_PLANNING_ENABLED) and steps_to_schedule:
            ev_counts = event_counts if event_counts is not None else self._last_day_event_counts
            self._schedule_project_steps(rebalanced, steps_to_schedule, today, tz, ev_counts, config)

        return self._apply_dynamic_completion_relief(rebalanced)

    def _schedule_project_steps(
        self,
        rebalanced: dict[str, DayLoadAnalysis],
        project_steps: list[dict[str, Any]],
        today: datetime.date,
        tz: datetime.tzinfo | None,
        event_counts: dict[str, int],
        config: dict[str, Any],
    ) -> None:
        """Assign decomposed project steps onto calm weekends (max 2, Sunday quiet-only) and free weekdays."""
        max_weekend = max(0, min(4, int(config.get(CONF_MAX_PROJECT_STEPS_WEEKEND, DEFAULT_MAX_PROJECT_STEPS_WEEKEND))))
        max_weekday = max(0, min(2, int(config.get(CONF_MAX_PROJECT_STEPS_WEEKDAY, DEFAULT_MAX_PROJECT_STEPS_WEEKDAY))))

        remaining = [
            pair[1]
            for pair in sorted(
                enumerate(project_steps),
                key=lambda p: (int(p[1].get("priority_rank", 3)), p[0]),
            )
        ]
        weekend_slots = [(10, 0), (14, 30), (11, 15), (16, 0)]
        weekday_slots = [(17, 15), (17, 50)]

        for day_str in sorted(rebalanced.keys()):
            if not remaining:
                break
            day = rebalanced[day_str]
            if day.date < today:
                continue

            is_weekend = day.date.weekday() >= 5
            is_sunday = day.date.weekday() == 6
            ev_cnt = int(event_counts.get(day_str, 0))
            if is_weekend:
                if day.base_score >= 45 or ev_cnt > 1:
                    continue
                day_cap = max_weekend
                slots = weekend_slots
            else:
                if day.base_score >= 38 or ev_cnt > 0:
                    continue
                day_cap = max_weekday
                slots = weekday_slots

            scheduled_today = 0
            idx = 0
            while idx < len(remaining) and scheduled_today < day_cap:
                step = remaining[idx]
                combined = f"{step.get('source_todo', '')} {step.get('title', '')} {' '.join(step.get('checklist', []))}"
                req_open, noisy = infer_step_sunday_restrictions(
                    combined,
                    raw_requires_open=step.get("requires_open_business"),
                    raw_is_noisy=step.get("is_noisy"),
                )
                if is_sunday and (req_open or noisy):
                    idx += 1
                    continue

                slot_hour, slot_minute = slots[min(scheduled_today, len(slots) - 1)]
                dur_min = max(15, min(60, int(step.get("duration_minutes", 30))))
                start_dt = datetime.datetime.combine(day.date, datetime.time(slot_hour, slot_minute), tzinfo=tz)
                end_dt = start_dt + datetime.timedelta(minutes=dur_min)

                # Avoid overlapping an existing task on the same day
                while any(not (end_dt <= t.start or start_dt >= t.end) for t in day.hidden_tasks):
                    start_dt += datetime.timedelta(minutes=45)
                    end_dt = start_dt + datetime.timedelta(minutes=dur_min)
                if start_dt.hour >= 19 or (not is_weekend and start_dt.hour < 17):
                    break

                source_todo = str(step.get("source_todo") or "Hausbau").strip()
                title = str(step.get("title") or source_todo).strip()
                uid = _make_task_uid(day.date, source_todo, title, "project_step")
                day.hidden_tasks.append(
                    HiddenTask(
                        uid=uid,
                        parent_event_summary=source_todo,
                        title=title,
                        category="project_step",
                        start=start_dt,
                        end=end_dt,
                        load_contribution=max(4, min(20, int(step.get("load_contribution", 10)))),
                        checklist=[str(c) for c in (step.get("checklist") or [])],
                        parent_event_date=day.date.isoformat(),
                        scheduled_date=day.date.isoformat(),
                        lead_days=0,
                        placement_mode="free_slot",
                        requires_open_business=req_open,
                        is_noisy=noisy,
                    )
                )
                day.hidden_tasks.sort(key=lambda t: t.start)
                day.base_score = min(100, day.base_score + 2)
                remaining.pop(idx)
                scheduled_today += 1

    def _apply_dynamic_completion_relief(
        self,
        horizon: dict[str, DayLoadAnalysis],
    ) -> dict[str, DayLoadAnalysis]:
        """Dynamically reduce daily Mental Load scores based on completed checklist items."""
        language = str(self.merged_config.get("language", "de"))
        for day in horizon.values():
            base = day.base_score or day.score
            day.base_score = base
            relief = 0
            for task in day.hidden_tasks:
                ratio = self._task_completion_ratio(task.uid, task.checklist)
                if ratio > 0:
                    relief += round(task.load_contribution * 0.6 * ratio)
            for linked in day.linked_prep_tasks:
                ratio = self._task_completion_ratio(str(linked["uid"]), list(linked.get("checklist") or []))
                if ratio > 0:
                    relief += round(int(linked.get("load_contribution", 10)) * 0.5 * ratio)

            day.relief_points = relief
            day.score = max(0, min(100, base - relief))
            day.level = score_to_level(day.score)
            badge = _format_badge_summary(
                score=day.score,
                level=day.level,
                hidden_count=len(day.hidden_tasks),
                top_stressor=day.top_stressor,
                language=language,
            )
            if relief > 0:
                badge += f" [-{relief}%]"
            day.badge_summary = badge
        return horizon

    async def async_toggle_checklist_item(
        self,
        item_uid: str,
        completed: bool | None = None,
    ) -> bool:
        """Toggle or explicitly set completion state for an invisible task checklist item."""
        await self.async_load_state()
        is_currently_done = item_uid in self.completed_item_uids
        new_done = (not is_currently_done) if completed is None else bool(completed)
        if new_done:
            self.completed_item_uids.add(item_uid)
        else:
            self.completed_item_uids.discard(item_uid)
        await self._async_save_state()

        if self._raw_analyses:
            updated = self._rebalance_and_score_horizon(
                self._raw_analyses,
                dt_util.now().date(),
                dt_util.now().tzinfo,
            )
            self.data = updated
            if hasattr(self, "async_set_updated_data"):
                self.async_set_updated_data(updated)
        elif hasattr(self, "async_set_updated_data") and self.data is not None:
            self._apply_dynamic_completion_relief(self.data)
            self.async_set_updated_data(self.data)
        return new_done

    async def async_sync_to_external_calendar(
        self,
        target_calendar: str | None = None,
        analyses: dict[str, DayLoadAnalysis] | None = None,
        save_after: bool = True,
    ) -> int:
        """Sync daily badges & invisible head-task blocks into a dedicated external calendar (e.g. Google Calendar).

        Safety guard: strictly refuses to write to any read-only source calendar in CONF_CALENDARS.
        """
        await self.async_load_state()
        config = self.merged_config
        target_cal = str(target_calendar or config.get(CONF_TARGET_CALENDAR_ENTITY) or "").strip()
        if not target_cal:
            return 0

        source_cals = {str(c) for c in (config.get(CONF_CALENDARS) or [])}
        if target_cal in source_cals:
            _LOGGER.warning(
                "Refusing to write Mental Load sync events into source calendar '%s'. "
                "Please create a dedicated secondary calendar (e.g. 'Mental Load' in Google Calendar) as target.",
                target_cal,
            )
            return 0

        source_map = analyses if analyses is not None else (self.data or {})
        synced_count = await self._async_cleanup_stale_external_events(target_cal, source_map)

        for day in sorted(source_map.values(), key=lambda d: d.date):
            badge_key = f"badge:{target_cal}:{day.date.isoformat()}:{day.score}"
            if badge_key not in self._synced_calendar_uids:
                try:
                    await self.hass.services.async_call(
                        "calendar",
                        "create_event",
                        {
                            "entity_id": target_cal,
                            "summary": day.badge_summary,
                            "start_date": day.date.isoformat(),
                            "end_date": (day.date + datetime.timedelta(days=1)).isoformat(),
                            "description": f"{day.reasoning}\n\n[ML-BADGE-{day.date.isoformat()}]",
                        },
                        blocking=True,
                    )
                    self._synced_calendar_uids.add(badge_key)
                    synced_count += 1
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Failed syncing day badge to %s: %s", target_cal, err)

            for task in day.hidden_tasks:
                task_key = f"task:{target_cal}:{task.uid}"
                if task_key in self._synced_calendar_uids:
                    continue
                checklist_lines = "\n".join(f"- [ ] {step}" for step in task.checklist)
                desc = (
                    f"Ausgelöst durch: {task.parent_event_summary}\n"
                    f"{checklist_lines}\n\n[ML-TASK-{task.uid}]"
                ).strip()
                summary_txt = f"{task.title} (+{task.load_contribution}%)"
                if task.parent_event_date and task.parent_event_date != task.scheduled_date:
                    p_short = f"{task.parent_event_date[8:10]}.{task.parent_event_date[5:7]}."
                    summary_txt = f"{task.title} [🔗 für {p_short}] (+{task.load_contribution}%)"
                try:
                    await self.hass.services.async_call(
                        "calendar",
                        "create_event",
                        {
                            "entity_id": target_cal,
                            "summary": summary_txt,
                            "start_date_time": task.start.isoformat(),
                            "end_date_time": task.end.isoformat(),
                            "description": desc,
                        },
                        blocking=True,
                    )
                    self._synced_calendar_uids.add(task_key)
                    synced_count += 1
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Failed syncing hidden task '%s' to %s: %s", task.title, target_cal, err)

        if save_after and synced_count > 0:
            await self._async_save_state()
        return synced_count

    async def _async_cleanup_stale_external_events(
        self,
        target_cal: str,
        source_map: dict[str, DayLoadAnalysis],
    ) -> int:
        """Delete outdated [ML-BADGE-*] and [ML-TASK-*] events from target_cal."""
        if not source_map:
            return 0
        days = sorted(d.date for d in source_map.values())
        tz = dt_util.now().tzinfo
        start_dt = datetime.datetime.combine(days[0], datetime.time.min, tzinfo=tz)
        end_dt = datetime.datetime.combine(days[-1] + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz)

        try:
            resp = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": target_cal,
                    "start_date_time": start_dt.isoformat(),
                    "end_date_time": end_dt.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except Exception:  # noqa: BLE001
            return 0

        if not isinstance(resp, dict):
            return 0

        expected_badges = {(d.date.isoformat(), d.badge_summary) for d in source_map.values()}
        expected_task_uids = {t.uid for d in source_map.values() for t in d.hidden_tasks}
        deleted = 0

        events = (resp.get(target_cal) or {}).get("events", [])
        for ev in events:
            desc = str(ev.get("description") or "")
            summary = str(ev.get("summary") or "")
            raw_start = str(ev.get("start") or "")
            date_part = raw_start[:10]
            is_stale = False

            if "[ML-BADGE-" in desc:
                is_stale = (date_part, summary) not in expected_badges
            elif "[ML-TASK-" in desc:
                uid_part = desc.split("[ML-TASK-", 1)[1].split("]", 1)[0]
                is_stale = uid_part not in expected_task_uids

            if not is_stale or len(date_part) != 10:
                continue

            try:
                d_val = datetime.date.fromisoformat(date_part)
            except ValueError:
                continue
            w_start = datetime.datetime.combine(d_val, datetime.time.min, tzinfo=tz).isoformat()
            w_end = datetime.datetime.combine(d_val + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz).isoformat()
            try:
                await self.hass.services.async_call(
                    "calendar",
                    "delete_event",
                    {
                        "entity_id": target_cal,
                        "summary": summary,
                        "start_date_time": w_start,
                        "end_date_time": w_end,
                    },
                    blocking=True,
                )
                deleted += 1
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Could not delete stale event '%s' from %s: %s", summary, target_cal, err)

        return deleted

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
