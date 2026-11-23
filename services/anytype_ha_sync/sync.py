"""Bidirectional 24/7 sync worker: Anytype Space ('Hausbau') <-> Home Assistant local_todo."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import time
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOGGER = logging.getLogger("anytype_ha_sync")

SYNC_TAG_RE = re.compile(r"\[ANYTYPE-([a-zA-Z0-9]+)\]")
ANYTYPE_API_VERSION = "2025-11-08"


def format_ha_description(object_id: str, details: str) -> str:
    """Embed stable Anytype object ID so updates and completions stay idempotent."""
    clean = (details or "").strip()
    marker = f"[ANYTYPE-{object_id}]"
    if marker in clean:
        return clean
    return f"{clean}\n\n{marker}".strip() if clean else marker


def extract_anytype_id(description: str | None) -> str | None:
    """Extract the embedded Anytype object ID from a Home Assistant todo item description."""
    if not description:
        return None
    match = SYNC_TAG_RE.search(description)
    return match.group(1) if match else None


def is_anytype_task_done(raw_obj: dict[str, Any]) -> bool:
    """Determine completion state from both Anytype's 'done' checkbox and 'status' property."""
    props = raw_obj.get("properties") or {}
    if isinstance(props, list):
        props_map = {str(p.get("key")): p.get("checkbox") if "checkbox" in p else p.get("select") for p in props if isinstance(p, dict)}
        done_val = props_map.get("done")
        status_val = props_map.get("status")
    else:
        done_val = props.get("done")
        status_val = props.get("status")

    if bool(done_val) is True:
        return True
    if isinstance(status_val, list):
        status_tokens = [str(s).strip().lower() for s in status_val]
    elif isinstance(status_val, dict):
        status_tokens = [str(status_val.get("name") or status_val.get("key") or "").strip().lower()]
    elif status_val:
        status_tokens = [str(status_val).strip().lower()]
    else:
        status_tokens = []
    return any(tok in {"done", "erledigt", "completed", "fertig", "closed"} for tok in status_tokens)


def extract_task_metadata(raw_obj: dict[str, Any]) -> str:
    """Build a compact status/details summary from an Anytype task object."""
    props = raw_obj.get("properties") or {}
    parts: list[str] = []
    if isinstance(props, dict):
        status_val = props.get("status")
        if isinstance(status_val, list) and status_val:
            parts.append(f"Status: {', '.join(str(x) for x in status_val)}")
        elif isinstance(status_val, str) and status_val.strip():
            parts.append(f"Status: {status_val.strip()}")

        desc_val = str(props.get("description") or "").strip()
        if desc_val:
            parts.append(desc_val[:400])
    snippet = str(raw_obj.get("snippet") or "").strip()
    if snippet:
        parts.append(snippet[:400])
    return " | ".join(parts)


@dataclass
class AnytypeTask:
    """Normalized Anytype task record."""

    object_id: str
    name: str
    done: bool = False
    details: str = ""
    due_date: str | None = None


@dataclass
class HaTodoItem:
    """Normalized Home Assistant todo item record."""

    uid: str
    summary: str
    status: str  # "needs_action" | "completed"
    description: str = ""
    due: str | None = None


@dataclass
class SyncStats:
    """Summary counters for a single sync cycle."""

    created_in_ha: int = 0
    updated_in_ha: int = 0
    completed_in_ha: int = 0
    reopened_in_ha: int = 0
    completed_in_anytype: int = 0
    skipped_empty: int = 0


class SyncBackend(Protocol):
    """Abstract client interface so sync logic can be tested deterministically offline."""

    def list_anytype_tasks(self, space_id: str) -> list[AnytypeTask]: ...
    def mark_anytype_done(self, space_id: str, object_id: str) -> None: ...
    def list_ha_todos(self, entity_id: str) -> list[HaTodoItem]: ...
    def add_ha_todo(self, entity_id: str, summary: str, description: str, due: str | None = None) -> None: ...
    def update_ha_todo(
        self,
        entity_id: str,
        uid: str,
        summary: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> None: ...


class HttpSyncBackend:
    """Live HTTP backend connecting headless anytype-cli API and Home Assistant REST API."""

    def __init__(
        self,
        anytype_url: str,
        anytype_api_key: str,
        ha_url: str,
        ha_token: str,
        timeout: float = 20.0,
    ) -> None:
        self.anytype_url = anytype_url.rstrip("/")
        self.anytype_headers = {
            "Authorization": f"Bearer {anytype_api_key}",
            "Anytype-Version": ANYTYPE_API_VERSION,
            "Content-Type": "application/json",
        }
        self.ha_url = ha_url.rstrip("/")
        self.ha_headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib_request.Request(url, data=body, headers=headers, method=method)
        with urllib_request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

    def list_anytype_tasks(self, space_id: str) -> list[AnytypeTask]:
        tasks: list[AnytypeTask] = []
        offset = 0
        limit = 100
        while True:
            url = f"{self.anytype_url}/v1/spaces/{space_id}/search?limit={limit}&offset={offset}"
            try:
                payload = self._request_json("POST", url, self.anytype_headers, {"types": ["task"]})
            except urllib_error.HTTPError as err:
                if err.code == 400:
                    payload = self._request_json("POST", url, self.anytype_headers, {"query": ""})
                else:
                    raise
            rows = payload.get("data") or []
            for row in rows:
                obj_type = str(row.get("type") or "").lower()
                if obj_type and obj_type != "task":
                    continue
                name = str(row.get("name") or "").strip()
                obj_id = str(row.get("id") or "").strip()
                if not obj_id or not name:
                    continue
                tasks.append(
                    AnytypeTask(
                        object_id=obj_id,
                        name=name,
                        done=is_anytype_task_done(row),
                        details=extract_task_metadata(row),
                    )
                )
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more") and not payload.get("has_more"):
                break
            offset += limit
        return tasks

    def mark_anytype_done(self, space_id: str, object_id: str) -> None:
        self._request_json(
            "PATCH",
            f"{self.anytype_url}/v1/spaces/{space_id}/objects/{object_id}",
            self.anytype_headers,
            {"properties": [{"key": "done", "checkbox": True}]},
        )

    def list_ha_todos(self, entity_id: str) -> list[HaTodoItem]:
        body = self._request_json(
            "POST",
            f"{self.ha_url}/api/services/todo/get_items?return_response",
            self.ha_headers,
            {"entity_id": entity_id, "status": ["needs_action", "completed"]},
        )
        svc_resp = body.get("service_response") or body
        raw_items = (svc_resp.get(entity_id) or {}).get("items") or []
        return [
            HaTodoItem(
                uid=str(item.get("uid") or item.get("summary") or ""),
                summary=str(item.get("summary") or "").strip(),
                status=str(item.get("status") or "needs_action"),
                description=str(item.get("description") or ""),
                due=item.get("due"),
            )
            for item in raw_items
        ]

    def add_ha_todo(self, entity_id: str, summary: str, description: str, due: str | None = None) -> None:
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "item": summary,
            "description": description,
        }
        if due:
            payload["due_date"] = due[:10]
        self._request_json(
            "POST",
            f"{self.ha_url}/api/services/todo/add_item",
            self.ha_headers,
            payload,
        )

    def update_ha_todo(
        self,
        entity_id: str,
        uid: str,
        summary: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"entity_id": entity_id, "item": uid}
        if summary is not None:
            payload["rename"] = summary
        if description is not None:
            payload["description"] = description
        if status is not None:
            payload["status"] = status
        self._request_json(
            "POST",
            f"{self.ha_url}/api/services/todo/update_item",
            self.ha_headers,
            payload,
        )


def run_sync_cycle(
    backend: SyncBackend,
    space_id: str,
    ha_todo_entity: str,
    previously_seen_open_ids: set[str] | None = None,
) -> tuple[SyncStats, set[str]]:
    """Execute one idempotent bidirectional sync pass between Anytype and Home Assistant."""
    stats = SyncStats()
    any_tasks = backend.list_anytype_tasks(space_id)
    ha_items = backend.list_ha_todos(ha_todo_entity)

    ha_by_anytype_id: dict[str, HaTodoItem] = {}
    for item in ha_items:
        aid = extract_anytype_id(item.description)
        if aid:
            ha_by_anytype_id[aid] = item

    prior_open = previously_seen_open_ids or set()
    currently_open_ids: set[str] = set()

    for task in any_tasks:
        if not task.name.strip():
            stats.skipped_empty += 1
            continue

        expected_desc = format_ha_description(task.object_id, task.details)
        existing = ha_by_anytype_id.get(task.object_id)

        if existing is None:
            if not task.done:
                backend.add_ha_todo(
                    ha_todo_entity,
                    summary=task.name.strip(),
                    description=expected_desc,
                    due=task.due_date,
                )
                stats.created_in_ha += 1
                currently_open_ids.add(task.object_id)
            continue

        # Bidirectional completion check:
        # 1) User checked off item in Home Assistant while Anytype still had it open -> push to Anytype
        if existing.status == "completed" and not task.done and task.object_id in prior_open:
            backend.mark_anytype_done(space_id, task.object_id)
            stats.completed_in_anytype += 1
            continue

        # 2) Anytype marked task done -> reflect in HA
        if task.done and existing.status != "completed":
            backend.update_ha_todo(ha_todo_entity, existing.uid, status="completed")
            stats.completed_in_ha += 1
            continue

        # 3) Anytype task reopened -> reopen in HA
        if not task.done:
            currently_open_ids.add(task.object_id)
            needs_rename = existing.summary != task.name.strip()
            needs_desc = existing.description.strip() != expected_desc.strip()
            needs_reopen = existing.status == "completed"
            if needs_rename or needs_desc or needs_reopen:
                backend.update_ha_todo(
                    ha_todo_entity,
                    existing.uid,
                    summary=task.name.strip() if needs_rename else None,
                    description=expected_desc if needs_desc else None,
                    status="needs_action" if needs_reopen else None,
                )
                if needs_reopen:
                    stats.reopened_in_ha += 1
                else:
                    stats.updated_in_ha += 1

    return stats, currently_open_ids


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    anytype_url = os.environ.get("ANYTYPE_API_URL", "http://127.0.0.1:31012")
    anytype_api_key = os.environ["ANYTYPE_API_KEY"]
    space_id = os.environ.get("ANYTYPE_SPACE_ID", "space_test_id")
    ha_url = os.environ["HA_URL"]
    ha_token = os.environ["HA_TOKEN"]
    ha_todo_entity = os.environ.get("HA_TODO_ENTITY", "todo.hausbau_anytype")
    interval_sec = max(30, int(os.environ.get("SYNC_INTERVAL_SECONDS", "300")))

    backend = HttpSyncBackend(
        anytype_url=anytype_url,
        anytype_api_key=anytype_api_key,
        ha_url=ha_url,
        ha_token=ha_token,
    )
    prior_open: set[str] = set()
    _LOGGER.info("Starting Anytype <-> HA Todo sync loop (%s -> %s every %ss)", space_id, ha_todo_entity, interval_sec)
    while True:
        try:
            stats, prior_open = run_sync_cycle(
                backend=backend,
                space_id=space_id,
                ha_todo_entity=ha_todo_entity,
                previously_seen_open_ids=prior_open,
            )
            _LOGGER.info("Sync cycle complete: %s", stats)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Sync cycle failed (will retry in %ss): %s", interval_sec, err)
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
