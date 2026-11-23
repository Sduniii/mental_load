"""JavaScript module registration for Mental Load Lovelace Card."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import JSMODULES, URL_BASE

_LOGGER = logging.getLogger(__name__)

_LOVELACE_DATA_KEY = "lovelace"
try:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    _LOVELACE_DATA_KEY = LOVELACE_DATA
except ImportError:
    _LOVELACE_DATA_KEY = "lovelace"


def _get_lovelace(hass: HomeAssistant) -> Any | None:
    """Return LovelaceData from hass.data across HA versions."""
    data = hass.data.get(_LOVELACE_DATA_KEY)
    if data is None:
        data = hass.data.get("lovelace")
    return data


def _get_resource_mode(lovelace: Any) -> str:
    """Return Lovelace resource mode safely."""
    if hasattr(lovelace, "resource_mode"):
        return lovelace.resource_mode
    if hasattr(lovelace, "mode"):
        return lovelace.mode
    return "yaml"


class JSModuleRegistration:
    """Registers static paths and Lovelace resources for mental-load-card.js."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.lovelace = _get_lovelace(hass)
        self._lovelace_retries = 0

    async def async_register(self) -> None:
        """Register HTTP static path and Lovelace module resource."""
        await self._async_register_path()

        if self.lovelace is None:
            _LOGGER.warning(
                "Lovelace not found — add resource manually: %s/mental-load-card.js",
                URL_BASE,
            )
            return

        mode = _get_resource_mode(self.lovelace)
        if mode == "storage":
            await self._async_wait_for_lovelace_resources()
        else:
            _LOGGER.info(
                "Lovelace YAML mode — add %s/mental-load-card.js as a module resource",
                URL_BASE,
            )

    async def _async_register_path(self) -> None:
        try:
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(URL_BASE, Path(__file__).parent, False)]
            )
        except RuntimeError:
            _LOGGER.debug("Static path already registered: %s", URL_BASE)

    async def _async_wait_for_lovelace_resources(self) -> None:
        self._lovelace_retries = 0

        async def _check(_now: Any) -> None:
            if self.lovelace.resources.loaded:
                await self._async_register_modules()
                return
            self._lovelace_retries += 1
            if self._lovelace_retries >= 10:
                _LOGGER.warning(
                    "Lovelace resources did not load in time — add %s/mental-load-card.js manually",
                    URL_BASE,
                )
                return
            async_call_later(self.hass, 5, _check)

        await _check(0)

    async def _async_register_modules(self) -> None:
        existing = [
            r
            for r in self.lovelace.resources.async_items()
            if r["url"].startswith(URL_BASE)
        ]
        for module in JSMODULES:
            url = f"{URL_BASE}/{module['filename']}"
            registered = False
            for resource in existing:
                if resource["url"].split("?")[0] == url:
                    registered = True
                    if resource["url"] != f"{url}?v={module['version']}":
                        await self.lovelace.resources.async_update_item(
                            resource["id"],
                            {"res_type": "module", "url": f"{url}?v={module['version']}"},
                        )
                    break
            if not registered:
                await self.lovelace.resources.async_create_item(
                    {"res_type": "module", "url": f"{url}?v={module['version']}"}
                )
