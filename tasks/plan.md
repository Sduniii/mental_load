# Implementation Plan: `mental_load`

See [SPEC.md](../SPEC.md) for the full specification and capability map.

## Build Sequence
1. Foundation & Data Models (`manifest.json`, `const.py`, `models.py`)
2. Multi-LLM Engine & 5-Factor Prompt/Parser/Heuristic (`prompt_builder.py`, `llm_providers.py`)
3. Coordinator, SHA-256 Caching, Feedback Memory, Todo Sync & Active Reactions (`coordinator.py`)
4. Home Assistant Platforms (`calendar.py`, `sensor.py`)
5. UI Config/Options Flow, Translations, Services & Custom Lovelace Card (`config_flow.py`, `__init__.py`, `services.yaml`, `strings.json`, `translations/*.json`, `frontend/*`)
6. Automated Pytest Suite (`tests/*.py`) and Documentation (`README.md`)
