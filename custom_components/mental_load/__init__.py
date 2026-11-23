"""Home Assistant integration setup for Mental Load Analyzer."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, EVENT_HOMEASSISTANT_STARTED, HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_PUSH_HIDDEN_TASKS,
    SERVICE_REFRESH,
    SERVICE_SUBMIT_FEEDBACK,
    SERVICE_SYNC_EXTERNAL_CALENDAR,
    SERVICE_TOGGLE_CHECKLIST_ITEM,
    VERSION,
)
from .coordinator import MentalLoadCoordinator
from .frontend import JSModuleRegistration
from .ical_view import MentalLoadICalView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["calendar", "sensor", "todo"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/version"})
@websocket_api.async_response
async def _ws_get_version(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the integration version to the Lovelace frontend."""
    connection.send_result(msg["id"], {"version": VERSION})


def _get_coordinators(hass: HomeAssistant, entry_id: str | None = None) -> list[MentalLoadCoordinator]:
    entries = hass.data.get(DOMAIN, {})
    if entry_id and entry_id in entries:
        return [entries[entry_id]]
    return [c for c in entries.values() if isinstance(c, MentalLoadCoordinator)]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up domain-level services, WebSocket API, iCal feed, and Lovelace card registration."""
    hass.data.setdefault(DOMAIN, {})
    websocket_api.async_register_command(hass, _ws_get_version)
    if hasattr(hass, "http") and hasattr(hass.http, "register_view"):
        hass.http.register_view(MentalLoadICalView(hass))

    async def _setup_frontend(_event: Any = None) -> None:
        try:
            await JSModuleRegistration(hass).async_register()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not auto-register Mental Load Lovelace card: %s", err)

    if hass.state == CoreState.running:
        await _setup_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _setup_frontend)

    async def _handle_refresh(call: ServiceCall) -> None:
        clear_cache = bool(call.data.get("clear_cache", False))
        for coord in _get_coordinators(hass, call.data.get("entry_id")):
            await coord.async_force_refresh(clear_cache=clear_cache)

    async def _handle_submit_feedback(call: ServiceCall) -> None:
        target_date = call.data.get("date")
        perceived_score = call.data.get("perceived_score")
        adjustment = str(call.data.get("adjustment", "accurate"))
        note = str(call.data.get("note", ""))
        for coord in _get_coordinators(hass, call.data.get("entry_id")):
            await coord.async_submit_feedback(
                target_date=target_date,
                perceived_score=perceived_score,
                adjustment=adjustment,
                note=note,
            )

    async def _handle_push_hidden_tasks(call: ServiceCall) -> None:
        target_date = call.data.get("date")
        todo_entity = call.data.get("todo_entity")
        for coord in _get_coordinators(hass, call.data.get("entry_id")):
            await coord.async_push_hidden_tasks_to_todo(
                target_date=target_date,
                todo_entity=todo_entity,
            )

    async def _handle_toggle_checklist_item(call: ServiceCall) -> None:
        item_uid = str(call.data["item_uid"])
        completed = call.data.get("completed")
        for coord in _get_coordinators(hass, call.data.get("entry_id")):
            await coord.async_toggle_checklist_item(item_uid=item_uid, completed=completed)

    async def _handle_sync_external_calendar(call: ServiceCall) -> None:
        calendar_entity = call.data.get("calendar_entity")
        for coord in _get_coordinators(hass, call.data.get("entry_id")):
            await coord.async_sync_to_external_calendar(target_calendar=calendar_entity)

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH,
            _handle_refresh,
            schema=vol.Schema(
                {
                    vol.Optional("entry_id"): cv.string,
                    vol.Optional("clear_cache", default=False): cv.boolean,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_FEEDBACK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_FEEDBACK,
            _handle_submit_feedback,
            schema=vol.Schema(
                {
                    vol.Optional("entry_id"): cv.string,
                    vol.Optional("date"): cv.string,
                    vol.Optional("perceived_score"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                    vol.Optional("adjustment", default="accurate"): vol.In(
                        ["much_lower", "lower", "accurate", "higher", "much_higher"]
                    ),
                    vol.Optional("note", default=""): cv.string,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_PUSH_HIDDEN_TASKS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_PUSH_HIDDEN_TASKS,
            _handle_push_hidden_tasks,
            schema=vol.Schema(
                {
                    vol.Optional("entry_id"): cv.string,
                    vol.Optional("date"): cv.string,
                    vol.Optional("todo_entity"): cv.entity_id,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_TOGGLE_CHECKLIST_ITEM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_TOGGLE_CHECKLIST_ITEM,
            _handle_toggle_checklist_item,
            schema=vol.Schema(
                {
                    vol.Optional("entry_id"): cv.string,
                    vol.Required("item_uid"): cv.string,
                    vol.Optional("completed"): cv.boolean,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SYNC_EXTERNAL_CALENDAR):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SYNC_EXTERNAL_CALENDAR,
            _handle_sync_external_calendar,
            schema=vol.Schema(
                {
                    vol.Optional("entry_id"): cv.string,
                    vol.Optional("calendar_entity"): cv.entity_id,
                }
            ),
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mental Load Analyzer from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = MentalLoadCoordinator(hass, entry)
    await coordinator.async_seed_from_cache()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if hasattr(entry, "async_create_background_task"):
        entry.async_create_background_task(
            hass,
            coordinator.async_refresh(),
            f"{DOMAIN}_initial_refresh_{entry.entry_id}",
        )
    else:
        hass.async_create_task(coordinator.async_refresh())
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are updated via the UI."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and clean up coordinator state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
