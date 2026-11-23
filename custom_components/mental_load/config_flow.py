"""Config Flow and modular Menu-driven Options Flow for Mental Load Analyzer."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    ALL_PROVIDERS,
    CONF_API_KEY,
    CONF_AUTO_PUSH_TODO,
    CONF_BASE_URL,
    CONF_CALENDARS,
    CONF_CONTEXT_ENERGY_SENSOR,
    CONF_CONTEXT_TODOS,
    CONF_CONTEXT_WEATHER,
    CONF_CRITICAL_THRESHOLD,
    CONF_DEFAULT_FOLLOWUP_MINUTES,
    CONF_DEFAULT_PREP_MINUTES,
    CONF_DEFAULT_TRAVEL_MINUTES,
    CONF_HORIZON_DAYS,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_NOTIFY_SERVICE,
    CONF_PROVIDER,
    CONF_REACTION_ENTITY,
    CONF_REACTION_ON_CRITICAL,
    CONF_REACTION_ON_WARNING,
    CONF_SHOW_HIDDEN_TASK_BLOCKS,
    CONF_TARGET_TODO_ENTITY,
    CONF_TEMPERATURE,
    CONF_UPDATE_INTERVAL,
    CONF_USER_PROFILE,
    CONF_WARNING_THRESHOLD,
    CONF_WEIGHT_HA_CONTEXT,
    CONF_WEIGHT_HIDDEN_WORK,
    CONF_WEIGHT_MOBILITY,
    CONF_WEIGHT_PACING,
    CONF_WEIGHT_SOCIAL,
    DEFAULT_BASE_URLS,
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_FOLLOWUP_MINUTES,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_MODELS,
    DEFAULT_PREP_MINUTES,
    DEFAULT_TEMPERATURE,
    DEFAULT_TRAVEL_MINUTES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USER_PROFILE,
    DEFAULT_WARNING_THRESHOLD,
    DOMAIN,
    PROVIDER_ANTIGRAVITY_CLI,
    PROVIDER_ANTHROPIC,
    PROVIDER_CUSTOM_OPENAI,
    PROVIDER_GOOGLE,
    PROVIDER_HA_CONVERSATION,
    PROVIDER_HEURISTIC,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
)

PROVIDER_OPTIONS = [
    selector.SelectOptionDict(value=PROVIDER_ANTIGRAVITY_CLI, label="Antigravity CLI (agy – kein API-Key nötig)"),
    selector.SelectOptionDict(value=PROVIDER_OPENAI, label="OpenAI (GPT-4o / o3-mini)"),
    selector.SelectOptionDict(value=PROVIDER_ANTHROPIC, label="Anthropic (Claude)"),
    selector.SelectOptionDict(value=PROVIDER_MISTRAL, label="Mistral AI"),
    selector.SelectOptionDict(value=PROVIDER_GOOGLE, label="Google Gemini"),
    selector.SelectOptionDict(value=PROVIDER_CUSTOM_OPENAI, label="Ollama / OpenAI-Compatible URL"),
    selector.SelectOptionDict(value=PROVIDER_HA_CONVERSATION, label="Home Assistant Conversation Agent"),
    selector.SelectOptionDict(value=PROVIDER_HEURISTIC, label="Lokale Heuristik (Offline, kein API-Key)"),
]

WEIGHT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0.0, max=2.0, step=0.1, mode=selector.NumberSelectorMode.SLIDER)
)


class MentalLoadConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """3-step setup wizard for Mental Load Analyzer."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: Select source calendars and forecast horizon."""
        errors: dict[str, str] = {}
        if user_input is not None:
            calendars = user_input.get(CONF_CALENDARS, [])
            if not calendars:
                errors[CONF_CALENDARS] = "select_at_least_one_calendar"
            else:
                self._data.update(user_input)
                return await self.async_step_llm()

        schema = vol.Schema(
            {
                vol.Required(CONF_CALENDARS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar", multiple=True)
                ),
                vol.Optional(CONF_HORIZON_DAYS, default=DEFAULT_HORIZON_DAYS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=14, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=360, step=5, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="de", label="Deutsch"),
                            selector.SelectOptionDict(value="en", label="English"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_llm(self, user_input: dict[str, Any] | None = None):
        """Step 2: Configure AI provider, model, API key, and optional custom URL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = user_input[CONF_PROVIDER]
            api_key = str(user_input.get(CONF_API_KEY, "")).strip()
            if provider in (PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_MISTRAL, PROVIDER_GOOGLE) and not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            else:
                cleaned = dict(user_input)
                if not cleaned.get(CONF_MODEL):
                    cleaned[CONF_MODEL] = DEFAULT_MODELS.get(provider, "")
                if not cleaned.get(CONF_BASE_URL) and provider in DEFAULT_BASE_URLS:
                    cleaned[CONF_BASE_URL] = DEFAULT_BASE_URLS[provider]
                self._data.update(cleaned)
                return await self.async_step_profile_and_reactions()

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER, default=PROVIDER_ANTIGRAVITY_CLI): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=PROVIDER_OPTIONS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_API_KEY, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_MODEL, default=""): selector.TextSelector(),
                vol.Optional(CONF_BASE_URL, default=""): selector.TextSelector(),
                vol.Optional(CONF_TEMPERATURE, default=DEFAULT_TEMPERATURE): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.1, mode=selector.NumberSelectorMode.SLIDER)
                ),
            }
        )
        return self.async_show_form(step_id="llm", data_schema=schema, errors=errors)

    async def async_step_profile_and_reactions(self, user_input: dict[str, Any] | None = None):
        """Step 3: Personal profile, live HA context, invisible task display, and reactions."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Mental Load", data=self._data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_USER_PROFILE, default=DEFAULT_USER_PROFILE): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional(CONF_CONTEXT_TODOS, default=[]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="todo", multiple=True)
                ),
                vol.Optional(CONF_CONTEXT_ENERGY_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CONTEXT_WEATHER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
                vol.Optional(CONF_SHOW_HIDDEN_TASK_BLOCKS, default=True): selector.BooleanSelector(),
                vol.Optional(CONF_AUTO_PUSH_TODO, default=False): selector.BooleanSelector(),
                vol.Optional(CONF_TARGET_TODO_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="todo")
                ),
                vol.Optional(CONF_WARNING_THRESHOLD, default=DEFAULT_WARNING_THRESHOLD): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=20, max=95, step=5, mode=selector.NumberSelectorMode.SLIDER)
                ),
                vol.Optional(CONF_CRITICAL_THRESHOLD, default=DEFAULT_CRITICAL_THRESHOLD): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=30, max=100, step=5, mode=selector.NumberSelectorMode.SLIDER)
                ),
                vol.Optional(CONF_NOTIFY_SERVICE, default="persistent_notification.create"): selector.TextSelector(),
                vol.Optional(CONF_REACTION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["scene", "script", "input_boolean", "automation", "switch", "light"]
                    )
                ),
            }
        )
        return self.async_show_form(step_id="profile_and_reactions", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> MentalLoadOptionsFlow:
        return MentalLoadOptionsFlow(config_entry)


class MentalLoadOptionsFlow(config_entries.OptionsFlow):
    """Extensible menu-based OptionsFlow so every aspect can be tuned after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _cur(self, key: str, default: Any = None) -> Any:
        if key in self._config_entry.options:
            return self._config_entry.options[key]
        return self._config_entry.data.get(key, default)

    def _save_section(self, user_input: dict[str, Any]):
        updated = dict(self._config_entry.options)
        updated.update(user_input)
        return self.async_create_entry(title="", data=updated)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Show configuration menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "llm_settings",
                "factors_and_profile",
                "calendar_and_todo",
                "active_reactions",
            ],
        )

    async def async_step_llm_settings(self, user_input: dict[str, Any] | None = None):
        """Edit AI provider, API key, model, and temperature."""
        if user_input is not None:
            provider = user_input[CONF_PROVIDER]
            if not user_input.get(CONF_MODEL):
                user_input[CONF_MODEL] = DEFAULT_MODELS.get(provider, "")
            return self._save_section(user_input)

        current_provider = self._cur(CONF_PROVIDER, PROVIDER_OPENAI)
        if current_provider not in ALL_PROVIDERS:
            current_provider = PROVIDER_OPENAI

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER, default=current_provider): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=PROVIDER_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_API_KEY, default=self._cur(CONF_API_KEY, "")): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_MODEL, default=self._cur(CONF_MODEL, "")): selector.TextSelector(),
                vol.Optional(CONF_BASE_URL, default=self._cur(CONF_BASE_URL, "")): selector.TextSelector(),
                vol.Optional(CONF_TEMPERATURE, default=self._cur(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.1, mode=selector.NumberSelectorMode.SLIDER)
                ),
            }
        )
        return self.async_show_form(step_id="llm_settings", data_schema=schema)

    async def async_step_factors_and_profile(self, user_input: dict[str, Any] | None = None):
        """Tune the 5 factor weights, user profile, and live HA context entities."""
        if user_input is not None:
            return self._save_section(user_input)

        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_USER_PROFILE, default=self._cur(CONF_USER_PROFILE, DEFAULT_USER_PROFILE)): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_WEIGHT_PACING, default=self._cur(CONF_WEIGHT_PACING, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_SOCIAL, default=self._cur(CONF_WEIGHT_SOCIAL, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_MOBILITY, default=self._cur(CONF_WEIGHT_MOBILITY, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_HA_CONTEXT, default=self._cur(CONF_WEIGHT_HA_CONTEXT, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_HIDDEN_WORK, default=self._cur(CONF_WEIGHT_HIDDEN_WORK, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_CONTEXT_TODOS, default=self._cur(CONF_CONTEXT_TODOS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo", multiple=True)
            ),
        }

        energy_val = self._cur(CONF_CONTEXT_ENERGY_SENSOR)
        schema_dict[
            vol.Optional(CONF_CONTEXT_ENERGY_SENSOR, default=energy_val)
            if energy_val
            else vol.Optional(CONF_CONTEXT_ENERGY_SENSOR)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        weather_val = self._cur(CONF_CONTEXT_WEATHER)
        schema_dict[
            vol.Optional(CONF_CONTEXT_WEATHER, default=weather_val)
            if weather_val
            else vol.Optional(CONF_CONTEXT_WEATHER)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))

        return self.async_show_form(step_id="factors_and_profile", data_schema=vol.Schema(schema_dict))

    async def async_step_calendar_and_todo(self, user_input: dict[str, Any] | None = None):
        """Edit source calendars, invisible head-task block durations, and todo auto-push."""
        if user_input is not None:
            return self._save_section(user_input)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_CALENDARS, default=self._cur(CONF_CALENDARS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            vol.Optional(CONF_HORIZON_DAYS, default=self._cur(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=14, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_UPDATE_INTERVAL, default=self._cur(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=360, step=5, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_SHOW_HIDDEN_TASK_BLOCKS, default=self._cur(CONF_SHOW_HIDDEN_TASK_BLOCKS, True)): selector.BooleanSelector(),
            vol.Optional(CONF_DEFAULT_PREP_MINUTES, default=self._cur(CONF_DEFAULT_PREP_MINUTES, DEFAULT_PREP_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=90, step=5, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_DEFAULT_TRAVEL_MINUTES, default=self._cur(CONF_DEFAULT_TRAVEL_MINUTES, DEFAULT_TRAVEL_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=120, step=5, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_DEFAULT_FOLLOWUP_MINUTES, default=self._cur(CONF_DEFAULT_FOLLOWUP_MINUTES, DEFAULT_FOLLOWUP_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=60, step=5, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_AUTO_PUSH_TODO, default=self._cur(CONF_AUTO_PUSH_TODO, False)): selector.BooleanSelector(),
        }

        target_todo = self._cur(CONF_TARGET_TODO_ENTITY)
        schema_dict[
            vol.Optional(CONF_TARGET_TODO_ENTITY, default=target_todo)
            if target_todo
            else vol.Optional(CONF_TARGET_TODO_ENTITY)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="todo"))

        return self.async_show_form(step_id="calendar_and_todo", data_schema=vol.Schema(schema_dict))

    async def async_step_active_reactions(self, user_input: dict[str, Any] | None = None):
        """Edit load thresholds, notification targets, and automatic Smart Home reactions."""
        if user_input is not None:
            return self._save_section(user_input)

        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_WARNING_THRESHOLD, default=self._cur(CONF_WARNING_THRESHOLD, DEFAULT_WARNING_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=20, max=95, step=5, mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_CRITICAL_THRESHOLD, default=self._cur(CONF_CRITICAL_THRESHOLD, DEFAULT_CRITICAL_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=100, step=5, mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_NOTIFY_SERVICE, default=self._cur(CONF_NOTIFY_SERVICE, "")): selector.TextSelector(),
            vol.Optional(CONF_REACTION_ON_WARNING, default=self._cur(CONF_REACTION_ON_WARNING, False)): selector.BooleanSelector(),
            vol.Optional(CONF_REACTION_ON_CRITICAL, default=self._cur(CONF_REACTION_ON_CRITICAL, True)): selector.BooleanSelector(),
        }

        reaction_ent = self._cur(CONF_REACTION_ENTITY)
        schema_dict[
            vol.Optional(CONF_REACTION_ENTITY, default=reaction_ent)
            if reaction_ent
            else vol.Optional(CONF_REACTION_ENTITY)
        ] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["scene", "script", "input_boolean", "automation", "switch", "light"]
            )
        )

        return self.async_show_form(step_id="active_reactions", data_schema=vol.Schema(schema_dict))
