"""Pytest fixtures and lightweight Home Assistant compatibility shims."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime
import inspect
import sys
import types
from typing import Any
from unittest.mock import AsyncMock


def pytest_pyfunc_call(pyfuncitem: Any) -> bool | None:
    """Allow async def test_* functions to run via asyncio.run without pytest-asyncio."""
    testfunction = pyfuncitem.obj
    if inspect.iscoroutinefunction(testfunction):
        kwargs = {
            arg: pyfuncitem.funcargs[arg]
            for arg in pyfuncitem._fixtureinfo.argnames
        }
        asyncio.run(testfunction(**kwargs))
        return True
    return None


def _install_ha_shims() -> None:
    """Install minimal Home Assistant & voluptuous shims if not installed in test env."""
    if "homeassistant" in sys.modules:
        return

    # Minimal voluptuous shim if absent
    if "voluptuous" not in sys.modules:
        vol = types.ModuleType("voluptuous")
        vol.Schema = lambda s, **kw: s
        vol.Required = lambda k, default=None: k
        vol.Optional = lambda k, default=None: k
        vol.All = lambda *args: args[0] if args else None
        vol.Coerce = lambda t: t
        vol.Range = lambda **kw: (lambda x: x)
        vol.In = lambda opts: (lambda x: x)
        sys.modules["voluptuous"] = vol

    ha = types.ModuleType("homeassistant")
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_core = types.ModuleType("homeassistant.core")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_cv = types.ModuleType("homeassistant.helpers.config_validation")
    ha_helpers_storage = types.ModuleType("homeassistant.helpers.storage")
    ha_helpers_coord = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_helpers_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
    ha_helpers_selector = types.ModuleType("homeassistant.helpers.selector")
    ha_helpers_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_util = types.ModuleType("homeassistant.util")
    ha_util_dt = types.ModuleType("homeassistant.util.dt")
    ha_components = types.ModuleType("homeassistant.components")
    ha_comp_calendar = types.ModuleType("homeassistant.components.calendar")
    ha_comp_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_comp_ws = types.ModuleType("homeassistant.components.websocket_api")
    ha_comp_http = types.ModuleType("homeassistant.components.http")
    ha_comp_conv = types.ModuleType("homeassistant.components.conversation")
    ha_comp_ll = types.ModuleType("homeassistant.components.lovelace")
    ha_comp_ll_const = types.ModuleType("homeassistant.components.lovelace.const")

    # Core
    class CoreState:
        running = "RUNNING"

    ha_core.CoreState = CoreState
    ha_core.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"
    ha_core.HomeAssistant = object
    ha_core.ServiceCall = object
    ha_core.callback = lambda fn: fn

    # ConfigEntries
    class ConfigEntry:
        def __init__(self, entry_id: str = "test_entry", data: dict | None = None, options: dict | None = None, title: str = "Mental Load") -> None:
            self.entry_id = entry_id
            self.data = data or {}
            self.options = options or {}
            self.title = title

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            pass

    class OptionsFlow:
        pass

    ha_config_entries.ConfigEntry = ConfigEntry
    ha_config_entries.ConfigFlow = ConfigFlow
    ha_config_entries.OptionsFlow = OptionsFlow

    # Helpers
    ha_helpers_cv.config_entry_only_config_schema = lambda d: {}
    ha_helpers_cv.string = str
    ha_helpers_cv.boolean = bool
    ha_helpers_cv.entity_id = str

    class Store:
        _memory: dict[str, Any] = {}

        def __init__(self, hass: Any, version: int, key: str) -> None:
            self.key = key

        async def async_load(self) -> Any:
            return self._memory.get(self.key)

        async def async_save(self, data: Any) -> None:
            self._memory[self.key] = data

    ha_helpers_storage.Store = Store

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass: Any, logger: Any, *, name: str, update_interval: Any = None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: Any = None

        def __class_getitem__(cls, item: Any) -> type:
            return cls

        async def async_refresh(self) -> None:
            self.data = await self._async_update_data()

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()

    class CoordinatorEntity:
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        def __class_getitem__(cls, item: Any) -> type:
            return cls

    ha_helpers_coord.DataUpdateCoordinator = DataUpdateCoordinator
    ha_helpers_coord.CoordinatorEntity = CoordinatorEntity
    ha_helpers_coord.UpdateFailed = UpdateFailed
    ha_helpers_ep.AddEntitiesCallback = Any
    ha_helpers_event.async_call_later = lambda hass, delay, cb: None
    ha_helpers_aiohttp.async_get_clientsession = lambda hass: None

    # Selector shims
    class _DummySelector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    for sel_name in (
        "EntitySelector",
        "EntitySelectorConfig",
        "NumberSelector",
        "NumberSelectorConfig",
        "NumberSelectorMode",
        "SelectSelector",
        "SelectSelectorConfig",
        "SelectSelectorMode",
        "SelectOptionDict",
        "TextSelector",
        "TextSelectorConfig",
        "TextSelectorType",
        "BooleanSelector",
    ):
        if "Mode" in sel_name or "Type" in sel_name:
            cls = type(sel_name, (), {"BOX": "box", "SLIDER": "slider", "DROPDOWN": "dropdown", "PASSWORD": "password"})
        elif sel_name == "SelectOptionDict":
            cls = dict
        else:
            cls = _DummySelector
        setattr(ha_helpers_selector, sel_name, cls)

    # dt util
    fixed_tz = datetime.timezone(datetime.timedelta(hours=1))
    ha_util_dt.DEFAULT_TIME_ZONE = fixed_tz
    ha_util_dt.now = lambda: datetime.datetime(2026, 11, 23, 8, 0, tzinfo=fixed_tz)
    ha_util_dt.utcnow = lambda: datetime.datetime(2026, 11, 23, 7, 0, tzinfo=datetime.timezone.utc)

    # Calendar & Sensor
    @dataclass
    class CalendarEvent:
        start: datetime.date | datetime.datetime
        end: datetime.date | datetime.datetime
        summary: str
        description: str | None = None
        location: str | None = None

    class CalendarEntity:
        pass

    class SensorEntity:
        pass

    class SensorStateClass:
        MEASUREMENT = "measurement"

    ha_comp_calendar.CalendarEntity = CalendarEntity
    ha_comp_calendar.CalendarEvent = CalendarEvent
    ha_comp_sensor.SensorEntity = SensorEntity
    ha_comp_sensor.SensorStateClass = SensorStateClass

    # Websocket & HTTP & Conversation & Lovelace
    ha_comp_ws.websocket_command = lambda schema: (lambda fn: fn)
    ha_comp_ws.async_response = lambda fn: fn
    ha_comp_ws.async_register_command = lambda hass, cmd: None
    ha_comp_ws.ActiveConnection = object
    ha_comp_http.StaticPathConfig = lambda url, path, cache: (url, path, cache)
    ha_comp_conv.async_converse = AsyncMock()
    ha_comp_ll_const.LOVELACE_DATA = "lovelace"

    modules = {
        "homeassistant": ha,
        "homeassistant.config_entries": ha_config_entries,
        "homeassistant.core": ha_core,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.config_validation": ha_helpers_cv,
        "homeassistant.helpers.storage": ha_helpers_storage,
        "homeassistant.helpers.update_coordinator": ha_helpers_coord,
        "homeassistant.helpers.entity_platform": ha_helpers_ep,
        "homeassistant.helpers.event": ha_helpers_event,
        "homeassistant.helpers.selector": ha_helpers_selector,
        "homeassistant.helpers.aiohttp_client": ha_helpers_aiohttp,
        "homeassistant.util": ha_util,
        "homeassistant.util.dt": ha_util_dt,
        "homeassistant.components": ha_components,
        "homeassistant.components.calendar": ha_comp_calendar,
        "homeassistant.components.sensor": ha_comp_sensor,
        "homeassistant.components.websocket_api": ha_comp_ws,
        "homeassistant.components.http": ha_comp_http,
        "homeassistant.components.conversation": ha_comp_conv,
        "homeassistant.components.lovelace": ha_comp_ll,
        "homeassistant.components.lovelace.const": ha_comp_ll_const,
    }
    sys.modules.update(modules)


_install_ha_shims()


@dataclass
class FakeState:
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class FakeStates:
    def __init__(self, initial: dict[str, FakeState] | None = None) -> None:
        self._states = initial or {}

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)

    def set(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        self._states[entity_id] = FakeState(state=state, attributes=attributes or {})


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, event_data or {}))

    def async_listen_once(self, event_type: str, listener: Any) -> None:
        return None


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._handlers: dict[tuple[str, str], Any] = {}
        self._registered: set[tuple[str, str]] = set()

    def set_response(self, domain: str, service: str, response: Any) -> None:
        self._handlers[(domain, service)] = response

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._registered

    def async_register(self, domain: str, service: str, handler: Any, schema: Any = None) -> None:
        self._registered.add((domain, service))

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        blocking: bool = False,
        return_response: bool = False,
    ) -> Any:
        payload = service_data or {}
        self.calls.append((domain, service, payload))
        handler = self._handlers.get((domain, service))
        if callable(handler):
            return handler(payload)
        return handler


class FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.states = FakeStates()
        self.bus = FakeBus()
        self.services = FakeServices()
        self.state = "RUNNING"
