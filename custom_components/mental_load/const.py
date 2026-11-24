"""Constants for the Mental Load Analyzer integration."""
from __future__ import annotations

DOMAIN = "mental_load"
VERSION = "1.9.3"

URL_BASE = "/mental_load_card"
JSMODULES = [
    {
        "name": "Mental Load Card",
        "filename": "mental-load-card.js",
        "version": VERSION,
    }
]

# AI Providers
PROVIDER_ANTIGRAVITY_CLI = "antigravity_cli"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_MISTRAL = "mistral"
PROVIDER_GOOGLE = "google"
PROVIDER_CUSTOM_OPENAI = "custom_openai"
PROVIDER_HA_CONVERSATION = "ha_conversation"
PROVIDER_HEURISTIC = "heuristic"

ALL_PROVIDERS = [
    PROVIDER_ANTIGRAVITY_CLI,
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
    PROVIDER_MISTRAL,
    PROVIDER_GOOGLE,
    PROVIDER_CUSTOM_OPENAI,
    PROVIDER_HA_CONVERSATION,
    PROVIDER_HEURISTIC,
]

DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_ANTIGRAVITY_CLI: "gemini-3.8-flash-medium",
    PROVIDER_OPENAI: "gpt-4o-mini",
    PROVIDER_ANTHROPIC: "claude-3-5-haiku-latest",
    PROVIDER_MISTRAL: "mistral-small-latest",
    PROVIDER_GOOGLE: "gemini-2.0-flash",
    PROVIDER_CUSTOM_OPENAI: "llama3.1",
    PROVIDER_HA_CONVERSATION: "conversation.home_assistant",
    PROVIDER_HEURISTIC: "local-heuristic-v1",
}

DEFAULT_BASE_URLS: dict[str, str] = {
    PROVIDER_ANTIGRAVITY_CLI: "http://127.0.0.1:11435/v1",
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com/v1",
    PROVIDER_MISTRAL: "https://api.mistral.ai/v1",
    PROVIDER_GOOGLE: "https://generativelanguage.googleapis.com/v1beta",
    PROVIDER_CUSTOM_OPENAI: "http://localhost:11434/v1",
}

# Configuration Keys
CONF_CALENDARS = "calendars"
CONF_HORIZON_DAYS = "horizon_days"
CONF_UPDATE_INTERVAL = "update_interval"

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_BASE_URL = "base_url"
CONF_TEMPERATURE = "temperature"
CONF_LANGUAGE = "language"
CONF_AGY_BINARY = "agy_binary"
DEFAULT_AGY_BINARY = "agy"

# 5 Factors & Personal Profile
CONF_USER_PROFILE = "user_profile"
CONF_CHILDREN_PROFILES = "children_profiles"
CONF_CHILDREN_CALENDARS = "children_calendars"
DEFAULT_CHILDREN_PROFILES = ""
CONF_WEIGHT_PACING = "weight_pacing"
CONF_WEIGHT_SOCIAL = "weight_social"
CONF_WEIGHT_MOBILITY = "weight_mobility"
CONF_WEIGHT_HA_CONTEXT = "weight_ha_context"
CONF_WEIGHT_HIDDEN_WORK = "weight_hidden_work"

CONF_CONTEXT_TODOS = "context_todos"
CONF_CONTEXT_ENERGY_SENSOR = "context_energy_sensor"
CONF_CONTEXT_WEATHER = "context_weather"
CONF_PROJECT_BASELINE_PER_ITEM = "project_baseline_per_item"
CONF_PROJECT_BASELINE_CAP = "project_baseline_cap"
CONF_PROJECT_PLANNING_ENABLED = "project_planning_enabled"
CONF_MAX_PROJECT_STEPS_WEEKEND = "max_project_steps_weekend"
CONF_MAX_PROJECT_STEPS_WEEKDAY = "max_project_steps_weekday"

# Invisible Head Tasks & Calendar Display
CONF_SHOW_HIDDEN_TASK_BLOCKS = "show_hidden_task_blocks"
CONF_DEFAULT_PREP_MINUTES = "default_prep_minutes"
CONF_DEFAULT_TRAVEL_MINUTES = "default_travel_minutes"
CONF_DEFAULT_FOLLOWUP_MINUTES = "default_followup_minutes"
CONF_AUTO_PUSH_TODO = "auto_push_todo"
CONF_TARGET_TODO_ENTITY = "target_todo_entity"
CONF_AUTO_SYNC_CALENDAR = "auto_sync_calendar"
CONF_TARGET_CALENDAR_ENTITY = "target_calendar_entity"

# Active Reactions
CONF_WARNING_THRESHOLD = "warning_threshold"
CONF_CRITICAL_THRESHOLD = "critical_threshold"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_REACTION_ENTITY = "reaction_entity"
CONF_REACTION_ON_WARNING = "reaction_on_warning"
CONF_REACTION_ON_CRITICAL = "reaction_on_critical"

# Defaults
DEFAULT_HORIZON_DAYS = 30
DEFAULT_UPDATE_INTERVAL = 60
DEFAULT_TEMPERATURE = 0.2
DEFAULT_LANGUAGE = "de"
DEFAULT_USER_PROFILE = (
    "Arbeitstage Mo-Fr 09:00-17:00, introvertiert am Abend (Termine nach 18 Uhr belasten stärker), "
    "brauche mindestens 15 Min Pause zwischen Meetings."
)
DEFAULT_PREP_MINUTES = 20
DEFAULT_TRAVEL_MINUTES = 25
DEFAULT_FOLLOWUP_MINUTES = 15
DEFAULT_PROJECT_BASELINE_PER_ITEM = 2.0
DEFAULT_PROJECT_BASELINE_CAP = 15
DEFAULT_PROJECT_PLANNING_ENABLED = True
DEFAULT_MAX_PROJECT_STEPS_WEEKEND = 2
DEFAULT_MAX_PROJECT_STEPS_WEEKDAY = 1
DEFAULT_WARNING_THRESHOLD = 65
DEFAULT_CRITICAL_THRESHOLD = 80

# Load Levels
LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"
LEVEL_CRITICAL = "critical"

LEVEL_ORDER = {
    LEVEL_LOW: 0,
    LEVEL_MEDIUM: 1,
    LEVEL_HIGH: 2,
    LEVEL_CRITICAL: 3,
}

LEVEL_EMOJIS = {
    LEVEL_LOW: "🟢",
    LEVEL_MEDIUM: "🟡",
    LEVEL_HIGH: "🟠",
    LEVEL_CRITICAL: "🔴",
}

LEVEL_LABELS_DE = {
    LEVEL_LOW: "Niedrig",
    LEVEL_MEDIUM: "Mittel",
    LEVEL_HIGH: "Hoch",
    LEVEL_CRITICAL: "Kritisch",
}

LEVEL_LABELS_EN = {
    LEVEL_LOW: "Low",
    LEVEL_MEDIUM: "Medium",
    LEVEL_HIGH: "High",
    LEVEL_CRITICAL: "Critical",
}

# Factor Keys
FACTOR_PACING = "pacing_context"
FACTOR_SOCIAL = "social_battery"
FACTOR_MOBILITY = "mobility"
FACTOR_HA_CONTEXT = "ha_context"
FACTOR_HIDDEN_WORK = "hidden_work"

ALL_FACTORS = (
    FACTOR_PACING,
    FACTOR_SOCIAL,
    FACTOR_MOBILITY,
    FACTOR_HA_CONTEXT,
    FACTOR_HIDDEN_WORK,
)

# Services & Events
SERVICE_REFRESH = "refresh"
SERVICE_SUBMIT_FEEDBACK = "submit_feedback"
SERVICE_PUSH_HIDDEN_TASKS = "push_hidden_tasks"
SERVICE_TOGGLE_CHECKLIST_ITEM = "toggle_checklist_item"
SERVICE_SYNC_EXTERNAL_CALENDAR = "sync_external_calendar"

EVENT_THRESHOLD_EXCEEDED = "mental_load_threshold_exceeded"
EVENT_ANALYSIS_UPDATED = "mental_load_updated"

STORAGE_VERSION = 1
MAX_FEEDBACK_HISTORY = 25
