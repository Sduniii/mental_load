# Specification: Home Assistant Mental Load Analyzer (`mental_load`)

## 1. Objective
A HACS-compatible Home Assistant custom integration (`mental_load`) that reads calendar events from one or more configured Home Assistant `calendar.*` entities (e.g., Google Calendar), evaluates the daily **Mental Load (0–100%)** using configurable AI models (Antigravity CLI `agy`, OpenAI, Anthropic, Mistral, Google Gemini, Ollama/Custom OpenAI-compatible endpoints, or Home Assistant Conversation Agents), uncovers **invisible head-tasks** (preparation, packing, travel/buffer time, follow-up) and surfaces them directly in a dedicated `calendar.mental_load` entity (and optionally in a `todo.*` list and custom Lovelace card), while triggering configurable Smart Home reactions when load thresholds are exceeded.

## 2. Capability Map

| Module ID | Responsibility | Depends On |
|---|---|---|
| `llm-engine` | Unified multi-provider AI adapter (`openai`, `anthropic`, `mistral`, `google`, `custom_openai`, `ha_conversation`) + deterministic heuristic fallback + strict JSON parser | — |
| `context-scorer` | Aggregates read-only source calendars (`calendar.get_events`), evaluates all **5 load factors**, extracts **invisible head-tasks** with pre/post time blocks, and manages the persistent **feedback memory** | `llm-engine` |
| `entities-calendar` | Provides `calendar.mental_load` (all-day score badge + timed `🧠` head-task blocks) and 5 `sensor.mental_load_*` entities | `context-scorer` |
| `reactions-todo` | Idempotently syncs discovered hidden tasks into a target `todo.*` entity and fires configurable threshold reactions (HA events, notifications, scenes/scripts/entities) | `context-scorer` |
| `config-ui-card` | Multi-step ConfigFlow & menu-driven OptionsFlow, domain services (`refresh`, `submit_feedback`, `push_hidden_tasks`), and custom Lovelace card (`mental-load-card.js`) | `entities-calendar`, `reactions-todo` |

**Build Order:** `llm-engine` → `context-scorer` → `entities-calendar` & `reactions-todo` → `config-ui-card`

## 3. Tech Stack & Commands
- **Tech Stack:** Python 3.12+, Home Assistant Core APIs (`DataUpdateCoordinator`, `CalendarEntity`, `SensorEntity`, `ConfigFlow`, `OptionsFlow`, `Store`, `aiohttp`), Web Components (Lovelace JS Module), `pytest` + `pytest-asyncio`.
- **Commands:**
  ```bash
  pytest tests -v
  python3 -m py_compile custom_components/mental_load/*.py custom_components/mental_load/frontend/*.py
  ```

## 4. Project Structure
```text
mental_load/
├── SPEC.md
├── README.md
├── hacs.json
├── pyproject.toml
├── tasks/
│   ├── plan.md
│   └── todo.md
├── custom_components/
│   └── mental_load/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── models.py
│       ├── llm_providers.py
│       ├── prompt_builder.py
│       ├── coordinator.py
│       ├── config_flow.py
│       ├── calendar.py
│       ├── sensor.py
│       ├── services.yaml
│       ├── strings.json
│       ├── translations/
│       │   ├── de.json
│       │   └── en.json
│       └── frontend/
│           ├── __init__.py
│           └── mental-load-card.js
└── tests/
    ├── conftest.py
    ├── test_llm_providers.py
    ├── test_prompt_and_parser.py
    ├── test_coordinator_and_reactions.py
    └── test_calendar_and_sensors.py
```

## 5. Code Style & Data Contract
Typed dataclasses (`from __future__ import annotations`, `dataclass(slots=True)`), async `aiohttp` REST calls without heavy external SDKs, and structured JSON schema between LLM and coordinator (`DayLoadAnalysis`, `HiddenTask`, `FeedbackEntry`).

## 6. Testing Strategy
- Unit tests for all 6 LLM provider adapters, markdown JSON cleanup, heuristic fallback, 5-factor prompt construction, and pre/post invisible task timestamp calculation.
- Component/integration tests for SHA-256 day caching, persistent feedback learning, deduplicated `todo.add_item` push, threshold reaction firing, `calendar.mental_load` event generation, and all 5 sensors.

## 7. Boundaries
- **Always:** Query source calendars read-only (`calendar.get_events`); store API keys in `ConfigEntry.data`; fall back gracefully to local heuristic scoring if an API call fails.
- **Ask first:** Adding external PyPI packages to `manifest.json` (`requirements`).
- **Never:** Modify or delete events in the user's original Google/source calendars; create duplicate `todo.*` entries; log API keys in plain text.

## 8. Success Criteria
1. OpenAI, Anthropic, Mistral, Google Gemini, Ollama/Custom OpenAI, and HA Conversation Agent are selectable in the UI and produce structured daily mental load analyses.
2. `calendar.mental_load` displays an all-day badge per day (`🟢`/`🟡`/`🟠`/`🔴`) with 5-factor breakdown and timed blocks (`🧠 Vorbereitung`, `🚗 Fahrt & Puffer`, `📝 Nachbereitung`) with checklists.
3. All 5 factors (pacing/context switching, social battery/profile, mobility/location, live HA context, and learned feedback via `mental_load.submit_feedback`) influence the score and reasoning.
4. Discovered hidden tasks can be pushed idempotently to `todo.*`, and threshold breaches fire `mental_load_threshold_exceeded`, optional notifications, and configurable HA entity actions.
