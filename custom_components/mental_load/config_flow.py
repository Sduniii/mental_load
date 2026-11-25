"""Config Flow and modular Menu-driven Options Flow for Mental Load Analyzer."""
from __future__ import annotations

from typing import Any
import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    ALL_PROVIDERS,
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
    CONF_DEFAULT_FOLLOWUP_MINUTES,
    CONF_DEFAULT_PREP_MINUTES,
    CONF_DEFAULT_TRAVEL_MINUTES,
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_MEMBERS,
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
    CONF_SHOW_HIDDEN_TASK_BLOCKS,
    CONF_TARGET_CALENDAR_ENTITY,
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
    DEFAULT_MAX_PROJECT_STEPS_WEEKDAY,
    DEFAULT_MAX_PROJECT_STEPS_WEEKEND,
    DEFAULT_MODELS,
    DEFAULT_PREP_MINUTES,
    DEFAULT_PROJECT_BASELINE_CAP,
    DEFAULT_PROJECT_BASELINE_PER_ITEM,
    DEFAULT_PROJECT_PLANNING_ENABLED,
    DEFAULT_TEMPERATURE,
    DEFAULT_TRAVEL_MINUTES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USER_PROFILE,
    DEFAULT_WARNING_THRESHOLD,
    DOMAIN,
    MEMBER_ROLE_ADULT,
    MEMBER_ROLE_CHILD,
    PROVIDER_ANTIGRAVITY_CLI,
    PROVIDER_ANTHROPIC,
    PROVIDER_CUSTOM_OPENAI,
    PROVIDER_GOOGLE,
    PROVIDER_HA_CONVERSATION,
    PROVIDER_HEURISTIC,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
)
from .prompt_builder import normalize_household_members

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


def _format_members_summary(members: list[dict[str, Any]], language: str = DEFAULT_LANGUAGE) -> str:
    """Render a compact one-line summary of configured household members for menu descriptions."""
    is_de = str(language).startswith("de")
    parts: list[str] = []
    for member in members:
        role_lbl = ("Kind" if is_de else "Child") if member.get("role") == MEMBER_ROLE_CHILD else ("Erwachsener" if is_de else "Adult")
        extras: list[str] = [role_lbl]
        if member.get("role") == MEMBER_ROLE_CHILD and member.get("birthdate"):
            extras.append(str(member["birthdate"]))
        cal_count = len(member.get("calendars") or [])
        if cal_count:
            extras.append(f"{cal_count} Kal." if is_de else f"{cal_count} cal.")
        parts.append(f"{member.get('name', '?')} ({', '.join(extras)})")
    return " | ".join(parts) if parts else ("Keine" if is_de else "None")


def _build_member_record(
    role: str,
    user_input: dict[str, Any],
    member_id: str | None = None,
) -> dict[str, Any]:
    """Build a normalized member dict from config/options form submission."""
    raw_cals = user_input.get("calendars") or []
    calendars = [str(c).strip() for c in raw_cals if str(c).strip()] if isinstance(raw_cals, list) else []
    return {
        "id": member_id or f"{role}_{uuid.uuid4().hex[:8]}",
        "role": role,
        "name": str(user_input.get("name") or "").strip() or role.title(),
        "birthdate": str(user_input.get("birthdate") or "").strip() if role == MEMBER_ROLE_CHILD else "",
        "calendars": calendars,
        "profile": str(user_input.get("profile") or "").strip(),
    }


def _build_member_form_schema(
    role: str,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Return a shared Home Assistant Voluptuous schema for creating/editing an adult or child."""
    cur = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required("name", default=str(cur.get("name") or "")): selector.TextSelector(),
    }
    if role == MEMBER_ROLE_CHILD:
        fields[vol.Optional("birthdate", default=str(cur.get("birthdate") or ""))] = selector.TextSelector()
    fields[vol.Optional("calendars", default=list(cur.get("calendars") or []))] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="calendar", multiple=True)
    )
    fields[vol.Optional("profile", default=str(cur.get("profile") or ""))] = selector.TextSelector(
        selector.TextSelectorConfig(multiline=True)
    )
    return vol.Schema(fields)


class MentalLoadConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Setup wizard with shared household calendars, AI provider, primary adult, and optional extra members."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._members: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: Select shared household read-only calendars, horizon, and language."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CALENDARS):
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
                    selector.NumberSelectorConfig(min=1, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
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
        """Step 2: Choose AI provider, credentials, and model."""
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = user_input[CONF_PROVIDER]
            api_key = str(user_input.get(CONF_API_KEY) or "").strip()
            if provider in (PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_MISTRAL, PROVIDER_GOOGLE) and not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            else:
                cleaned = dict(user_input)
                if not cleaned.get(CONF_MODEL):
                    cleaned[CONF_MODEL] = DEFAULT_MODELS.get(provider, "")
                if not cleaned.get(CONF_BASE_URL):
                    cleaned[CONF_BASE_URL] = DEFAULT_BASE_URLS.get(provider, "")
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
        """Step 3: Adult 1 profile textbox, HA context sensors, reactions & optional extra members."""
        lang = str(self._data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        default_adult_name = "Erwachsener 1" if lang.startswith("de") else "Adult 1"

        if user_input is not None:
            payload = dict(user_input)
            add_more = bool(payload.pop("add_more_people", False))
            adult_record = _build_member_record(
                MEMBER_ROLE_ADULT,
                {
                    "name": payload.pop("adult_name", default_adult_name),
                    "calendars": payload.pop("adult_calendars", []),
                    "profile": payload.pop("adult_profile", ""),
                },
                member_id="adult_1",
            )
            self._members = [adult_record]
            self._data.update(payload)
            self._data[CONF_HOUSEHOLD_MEMBERS] = list(self._members)
            if add_more:
                return await self.async_step_setup_members()
            return self.async_create_entry(title="Mental Load", data=self._data)

        schema = vol.Schema(
            {
                vol.Optional("adult_name", default=default_adult_name): selector.TextSelector(),
                vol.Optional("adult_calendars", default=[]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar", multiple=True)
                ),
                vol.Optional("adult_profile", default=DEFAULT_USER_PROFILE): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional("add_more_people", default=False): selector.BooleanSelector(),
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
                vol.Optional(CONF_AUTO_SYNC_CALENDAR, default=False): selector.BooleanSelector(),
                vol.Optional(CONF_TARGET_CALENDAR_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar")
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

    async def async_step_setup_members(self, user_input: dict[str, Any] | None = None):
        """Show setup submenu to optionally add additional adults or children."""
        lang = str(self._data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        return self.async_show_menu(
            step_id="setup_members",
            menu_options=["setup_add_adult", "setup_add_child", "setup_finish"],
            description_placeholders={
                "members_summary": _format_members_summary(self._members, lang),
            },
        )

    async def async_step_setup_add_adult(self, user_input: dict[str, Any] | None = None):
        """Add an additional adult during initial setup."""
        if user_input is not None:
            self._members.append(_build_member_record(MEMBER_ROLE_ADULT, user_input))
            self._data[CONF_HOUSEHOLD_MEMBERS] = list(self._members)
            return await self.async_step_setup_members()
        return self.async_show_form(
            step_id="setup_add_adult",
            data_schema=_build_member_form_schema(MEMBER_ROLE_ADULT),
        )

    async def async_step_setup_add_child(self, user_input: dict[str, Any] | None = None):
        """Add a child during initial setup."""
        if user_input is not None:
            self._members.append(_build_member_record(MEMBER_ROLE_CHILD, user_input))
            self._data[CONF_HOUSEHOLD_MEMBERS] = list(self._members)
            return await self.async_step_setup_members()
        return self.async_show_form(
            step_id="setup_add_child",
            data_schema=_build_member_form_schema(MEMBER_ROLE_CHILD),
        )

    async def async_step_setup_finish(self, user_input: dict[str, Any] | None = None):
        """Finish setup wizard and create config entry with all household members."""
        self._data[CONF_HOUSEHOLD_MEMBERS] = list(self._members)
        return self.async_create_entry(title="Mental Load", data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> MentalLoadOptionsFlow:
        return MentalLoadOptionsFlow(config_entry)


class MentalLoadOptionsFlow(config_entries.OptionsFlow):
    """Extensible menu-based OptionsFlow with dynamic household member management."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._editing_member_id: str | None = None

    def _cur(self, key: str, default: Any = None) -> Any:
        if key in self._config_entry.options:
            return self._config_entry.options[key]
        return self._config_entry.data.get(key, default)

    def _merged_config(self) -> dict[str, Any]:
        merged = dict(self._config_entry.data)
        merged.update(self._config_entry.options)
        return merged

    def _get_members(self) -> list[dict[str, Any]]:
        return normalize_household_members(self._merged_config())

    def _save_section(self, user_input: dict[str, Any]):
        updated = dict(self._config_entry.options)
        updated.update(user_input)
        updated.pop("sport_bag_mode", None)
        return self.async_create_entry(title="", data=updated)

    def _save_members(self, members: list[dict[str, Any]]):
        updated = dict(self._config_entry.options)
        updated[CONF_HOUSEHOLD_MEMBERS] = members
        for legacy_key in (CONF_USER_PROFILE, CONF_CHILDREN_PROFILES, CONF_CHILDREN_CALENDARS, "sport_bag_mode"):
            updated.pop(legacy_key, None)
        return self.async_create_entry(title="", data=updated)

    def _member_options(self, members: list[dict[str, Any]]) -> list[selector.SelectOptionDict]:
        lang = str(self._cur(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        is_de = lang.startswith("de")
        opts: list[selector.SelectOptionDict] = []
        for m in members:
            role_lbl = ("Kind" if is_de else "Child") if m["role"] == MEMBER_ROLE_CHILD else ("Erwachsener" if is_de else "Adult")
            label = f"{m['name']} ({role_lbl})"
            opts.append(selector.SelectOptionDict(value=m["id"], label=label))
        return opts

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Show top-level options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "llm_settings",
                "household_members",
                "factors_and_profile",
                "calendar_and_todo",
                "active_reactions",
            ],
        )

    async def async_step_household_members(self, user_input: dict[str, Any] | None = None):
        """Show dynamic household member management menu (add/edit/remove adults & children)."""
        members = self._get_members()
        lang = str(self._cur(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        return self.async_show_menu(
            step_id="household_members",
            menu_options=["add_adult", "add_child", "edit_member", "remove_member"],
            description_placeholders={
                "members_summary": _format_members_summary(members, lang),
            },
        )

    async def async_step_add_adult(self, user_input: dict[str, Any] | None = None):
        """Add an adult profile (name, optional calendars, free-text textbox)."""
        if user_input is not None:
            members = self._get_members()
            members.append(_build_member_record(MEMBER_ROLE_ADULT, user_input))
            return self._save_members(members)
        return self.async_show_form(
            step_id="add_adult",
            data_schema=_build_member_form_schema(MEMBER_ROLE_ADULT),
        )

    async def async_step_add_child(self, user_input: dict[str, Any] | None = None):
        """Add a child profile (name, birthdate, optional calendars, free-text textbox)."""
        if user_input is not None:
            members = self._get_members()
            members.append(_build_member_record(MEMBER_ROLE_CHILD, user_input))
            return self._save_members(members)
        return self.async_show_form(
            step_id="add_child",
            data_schema=_build_member_form_schema(MEMBER_ROLE_CHILD),
        )

    async def async_step_edit_member(self, user_input: dict[str, Any] | None = None):
        """Pick which household member to edit."""
        members = self._get_members()
        if not members:
            return self.async_show_form(
                step_id="edit_member",
                data_schema=vol.Schema({}),
                errors={"base": "no_members"},
            )
        if user_input is not None:
            self._editing_member_id = str(user_input.get("member_id") or "")
            target = next((m for m in members if m["id"] == self._editing_member_id), None)
            if target is not None:
                if target["role"] == MEMBER_ROLE_CHILD:
                    return await self.async_step_edit_child()
                return await self.async_step_edit_adult()

        opts = self._member_options(members)
        return self.async_show_form(
            step_id="edit_member",
            data_schema=vol.Schema(
                {
                    vol.Required("member_id", default=opts[0]["value"]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=opts, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )

    async def async_step_edit_adult(self, user_input: dict[str, Any] | None = None):
        """Edit the selected adult member."""
        members = self._get_members()
        target_idx = next((i for i, m in enumerate(members) if m["id"] == self._editing_member_id), 0)
        current = members[target_idx]
        if user_input is not None:
            members[target_idx] = _build_member_record(
                MEMBER_ROLE_ADULT,
                user_input,
                member_id=current["id"],
            )
            return self._save_members(members)
        return self.async_show_form(
            step_id="edit_adult",
            data_schema=_build_member_form_schema(MEMBER_ROLE_ADULT, current),
        )

    async def async_step_edit_child(self, user_input: dict[str, Any] | None = None):
        """Edit the selected child member."""
        members = self._get_members()
        target_idx = next((i for i, m in enumerate(members) if m["id"] == self._editing_member_id), 0)
        current = members[target_idx]
        if user_input is not None:
            members[target_idx] = _build_member_record(
                MEMBER_ROLE_CHILD,
                user_input,
                member_id=current["id"],
            )
            return self._save_members(members)
        return self.async_show_form(
            step_id="edit_child",
            data_schema=_build_member_form_schema(MEMBER_ROLE_CHILD, current),
        )

    async def async_step_remove_member(self, user_input: dict[str, Any] | None = None):
        """Remove a child or secondary adult (enforcing at least one remaining adult)."""
        members = self._get_members()
        errors: dict[str, str] = {}
        if user_input is not None:
            target_id = str(user_input.get("member_id") or "")
            target = next((m for m in members if m["id"] == target_id), None)
            adults = [m for m in members if m["role"] == MEMBER_ROLE_ADULT]
            if target is not None and target["role"] == MEMBER_ROLE_ADULT and len(adults) <= 1:
                errors["base"] = "cannot_remove_last_adult"
            elif target is not None:
                updated_members = [m for m in members if m["id"] != target_id]
                return self._save_members(updated_members)

        opts = self._member_options(members)
        return self.async_show_form(
            step_id="remove_member",
            data_schema=vol.Schema(
                {
                    vol.Required("member_id", default=opts[0]["value"]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=opts, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
            errors=errors,
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
        """Tune the 5 factor weights, linked project To-Dos, and live HA context entities."""
        if user_input is not None:
            return self._save_section(user_input)

        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_WEIGHT_PACING, default=self._cur(CONF_WEIGHT_PACING, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_SOCIAL, default=self._cur(CONF_WEIGHT_SOCIAL, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_MOBILITY, default=self._cur(CONF_WEIGHT_MOBILITY, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_HA_CONTEXT, default=self._cur(CONF_WEIGHT_HA_CONTEXT, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_WEIGHT_HIDDEN_WORK, default=self._cur(CONF_WEIGHT_HIDDEN_WORK, 1.0)): WEIGHT_SELECTOR,
            vol.Optional(CONF_CONTEXT_TODOS, default=self._cur(CONF_CONTEXT_TODOS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo", multiple=True)
            ),
            vol.Optional(
                CONF_PROJECT_BASELINE_PER_ITEM,
                default=self._cur(CONF_PROJECT_BASELINE_PER_ITEM, DEFAULT_PROJECT_BASELINE_PER_ITEM),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.0, max=5.0, step=0.5, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_PROJECT_BASELINE_CAP,
                default=self._cur(CONF_PROJECT_BASELINE_CAP, DEFAULT_PROJECT_BASELINE_CAP),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=40, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_PROJECT_PLANNING_ENABLED,
                default=self._cur(CONF_PROJECT_PLANNING_ENABLED, DEFAULT_PROJECT_PLANNING_ENABLED),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MAX_PROJECT_STEPS_WEEKEND,
                default=self._cur(CONF_MAX_PROJECT_STEPS_WEEKEND, DEFAULT_MAX_PROJECT_STEPS_WEEKEND),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=4, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_MAX_PROJECT_STEPS_WEEKDAY,
                default=self._cur(CONF_MAX_PROJECT_STEPS_WEEKDAY, DEFAULT_MAX_PROJECT_STEPS_WEEKDAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=3, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }

        energy_sensor = self._cur(CONF_CONTEXT_ENERGY_SENSOR)
        schema_dict[
            vol.Optional(CONF_CONTEXT_ENERGY_SENSOR, default=energy_sensor)
            if energy_sensor
            else vol.Optional(CONF_CONTEXT_ENERGY_SENSOR)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        weather_entity = self._cur(CONF_CONTEXT_WEATHER)
        schema_dict[
            vol.Optional(CONF_CONTEXT_WEATHER, default=weather_entity)
            if weather_entity
            else vol.Optional(CONF_CONTEXT_WEATHER)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))

        return self.async_show_form(step_id="factors_and_profile", data_schema=vol.Schema(schema_dict))

    async def async_step_calendar_and_todo(self, user_input: dict[str, Any] | None = None):
        """Edit shared household calendars, invisible head-task defaults, and todo auto-push."""
        if user_input is not None:
            return self._save_section(user_input)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_CALENDARS, default=self._cur(CONF_CALENDARS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            vol.Optional(CONF_HORIZON_DAYS, default=self._cur(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
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
            vol.Optional(CONF_AUTO_SYNC_CALENDAR, default=self._cur(CONF_AUTO_SYNC_CALENDAR, False)): selector.BooleanSelector(),
        }

        target_todo = self._cur(CONF_TARGET_TODO_ENTITY)
        schema_dict[
            vol.Optional(CONF_TARGET_TODO_ENTITY, default=target_todo)
            if target_todo
            else vol.Optional(CONF_TARGET_TODO_ENTITY)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="todo"))

        target_cal = self._cur(CONF_TARGET_CALENDAR_ENTITY)
        schema_dict[
            vol.Optional(CONF_TARGET_CALENDAR_ENTITY, default=target_cal)
            if target_cal
            else vol.Optional(CONF_TARGET_CALENDAR_ENTITY)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar"))

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
