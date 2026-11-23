"""Native Home Assistant To-Do list platform for Mental Load invisible head-tasks."""
from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MentalLoadCoordinator, make_checklist_item_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the native interactive Mental Load To-Do list entity."""
    coordinator: MentalLoadCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MentalLoadTodoListEntity(coordinator, entry)])


class MentalLoadTodoListEntity(CoordinatorEntity[MentalLoadCoordinator], TodoListEntity):
    """Interactive To-Do list containing every uncovered invisible head-task & checklist step."""

    _attr_has_entity_name = False
    _attr_supported_features = (
        TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: MentalLoadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_todo"
        self._attr_name = f"{entry.title or 'Mental Load'} Aufgaben"

    @property
    def todo_items(self) -> list[TodoItem]:
        """Build interactive To-Do items from uncovered hidden tasks across the horizon."""
        data = self.coordinator.data or {}
        completed_uids = self.coordinator.completed_item_uids
        items: list[TodoItem] = []

        for day in sorted(data.values(), key=lambda d: d.date):
            day_label = day.date.strftime("%d.%m.")
            for task in day.hidden_tasks:
                if task.checklist:
                    for idx, step in enumerate(task.checklist):
                        uid = make_checklist_item_uid(task.uid, idx)
                        status = (
                            TodoItemStatus.COMPLETED
                            if uid in completed_uids
                            else TodoItemStatus.NEEDS_ACTION
                        )
                        parent_suffix = f" ({task.parent_event_summary})" if task.parent_event_summary else ""
                        if task.parent_event_date and task.parent_event_date != task.scheduled_date:
                            p_short = f"{task.parent_event_date[8:10]}.{task.parent_event_date[5:7]}."
                            parent_suffix = f" [🔗 für {p_short}: {task.parent_event_summary}]"
                        items.append(
                            TodoItem(
                                uid=uid,
                                summary=f"[{day_label} {task.start.strftime('%H:%M')}] {step}{parent_suffix}",
                                status=status,
                                due=task.start,
                                description=f"{task.title} | +{task.load_contribution}% Mental Load",
                            )
                        )
                else:
                    uid = make_checklist_item_uid(task.uid, 0)
                    status = (
                        TodoItemStatus.COMPLETED
                        if uid in completed_uids
                        else TodoItemStatus.NEEDS_ACTION
                    )
                    items.append(
                        TodoItem(
                            uid=uid,
                            summary=f"[{day_label} {task.start.strftime('%H:%M')}] {task.title}",
                            status=status,
                            due=task.start,
                            description=f"{task.parent_event_summary} | +{task.load_contribution}% Mental Load",
                        )
                    )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Persist checkbox toggle when user checks/unchecks an item in HA To-Do UI."""
        if not item.uid:
            return
        is_completed = item.status == TodoItemStatus.COMPLETED
        await self.coordinator.async_toggle_checklist_item(item.uid, completed=is_completed)
