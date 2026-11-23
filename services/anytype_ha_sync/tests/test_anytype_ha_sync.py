"""Unit tests for the bidirectional Anytype <-> Home Assistant Todo sync worker."""
from __future__ import annotations

from dataclasses import dataclass, field

from services.anytype_ha_sync.sync import (
    AnytypeTask,
    HaTodoItem,
    extract_anytype_id,
    extract_task_due_date,
    extract_task_metadata,
    format_ha_description,
    is_anytype_task_done,
    run_sync_cycle,
)


@dataclass
class FakeSyncBackend:
    anytype_tasks: list[AnytypeTask] = field(default_factory=list)
    ha_items: list[HaTodoItem] = field(default_factory=list)
    marked_done_in_anytype: list[str] = field(default_factory=list)
    added: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)

    def list_anytype_tasks(self, space_id: str) -> list[AnytypeTask]:
        return list(self.anytype_tasks)

    def mark_anytype_done(self, space_id: str, object_id: str) -> None:
        self.marked_done_in_anytype.append(object_id)
        for t in self.anytype_tasks:
            if t.object_id == object_id:
                t.done = True

    def list_ha_todos(self, entity_id: str) -> list[HaTodoItem]:
        return list(self.ha_items)

    def add_ha_todo(self, entity_id: str, summary: str, description: str, due: str | None = None) -> None:
        uid = f"ha_{len(self.ha_items) + 1}"
        self.ha_items.append(
            HaTodoItem(uid=uid, summary=summary, status="needs_action", description=description, due=due)
        )
        self.added.append({"summary": summary, "description": description})

    def update_ha_todo(
        self,
        entity_id: str,
        uid: str,
        summary: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> None:
        for item in self.ha_items:
            if item.uid == uid:
                if summary is not None:
                    item.summary = summary
                if description is not None:
                    item.description = description
                if status is not None:
                    item.status = status
        self.updated.append({"uid": uid, "summary": summary, "status": status})


def test_is_anytype_task_done_handles_checkbox_and_status() -> None:
    assert is_anytype_task_done({"properties": {"done": True, "status": ["To Do"]}}) is True
    assert is_anytype_task_done({"properties": {"done": False, "status": ["Done"]}}) is True
    assert is_anytype_task_done({"properties": {"done": False, "status": ["In Progress"]}}) is False


def test_extract_task_metadata_includes_priority_status_and_due_date() -> None:
    raw = {
        "properties": {
            "priority": {"name": "Hoch"},
            "status": ["In Progress"],
            "due_date": "2026-11-28T00:00:00Z",
        },
        "snippet": "Projekt: Hausbau",
    }
    meta = extract_task_metadata(raw)
    assert meta.startswith("Prio: Hoch | Status: In Progress")
    assert "Projekt: Hausbau" in meta
    assert extract_task_due_date(raw) == "2026-11-28"

    raw_list_props = {
        "properties": [
            {"key": "priority", "select": {"name": "Niedrig"}},
            {"key": "status", "select": {"name": "To Do"}},
        ]
    }
    assert extract_task_metadata(raw_list_props) == "Prio: Niedrig | Status: To Do"


def test_sync_creates_idempotently_updates_and_bidirectional_complete() -> None:
    backend = FakeSyncBackend(
        anytype_tasks=[
            AnytypeTask(object_id="abc111", name="OG: Elektrik", done=False, details="Status: In Progress"),
            AnytypeTask(object_id="done222", name="Hänger TÜV", done=True, details="Status: To Do"),
            AnytypeTask(object_id="empty333", name="   ", done=False),
        ]
    )

    stats1, open_ids = run_sync_cycle(backend, "space_test_id", "todo.hausbau_anytype")
    assert stats1.created_in_ha == 1
    assert stats1.skipped_empty == 1
    assert "abc111" in open_ids
    assert extract_anytype_id(backend.ha_items[0].description) == "abc111"

    # Re-running with unchanged tasks produces 0 writes (no duplicates)
    stats2, open_ids = run_sync_cycle(backend, "space_test_id", "todo.hausbau_anytype", open_ids)
    assert stats2.created_in_ha == 0
    assert stats2.updated_in_ha == 0
    assert len(backend.ha_items) == 1

    # Renaming task in Anytype updates existing HA item without duplicating
    backend.anytype_tasks[0].name = "OG: Elektrik Schaltschrank"
    stats3, open_ids = run_sync_cycle(backend, "space_test_id", "todo.hausbau_anytype", open_ids)
    assert stats3.updated_in_ha == 1
    assert len(backend.ha_items) == 1
    assert backend.ha_items[0].summary == "OG: Elektrik Schaltschrank"

    # Completing item in Home Assistant marks it done in Anytype
    backend.ha_items[0].status = "completed"
    stats4, open_ids = run_sync_cycle(backend, "space_test_id", "todo.hausbau_anytype", open_ids)
    assert stats4.completed_in_anytype == 1
    assert backend.marked_done_in_anytype == ["abc111"]
    assert backend.anytype_tasks[0].done is True
    assert format_ha_description("abc111", "Test") == "Test\n\n[ANYTYPE-abc111]"
