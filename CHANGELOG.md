# Changelog

## [1.3.0] — 2026-11-23

### Added
- **Native interactive `todo.mental_load_aufgaben` entity** (`todo.py`) — every uncovered invisible head-task and checklist step is now exposed as a real checkable To-Do item with due time, rather than plain text only.
- **Dedicated external calendar sync** (`CONF_TARGET_CALENDAR_ENTITY` / `CONF_AUTO_SYNC_CALENDAR` / service `mental_load.sync_external_calendar`) — syncs daily badges and invisible task blocks into a separate writable calendar (e.g. a dedicated Google Calendar for iPhone/iPad/Mac/Apple Watch), while strictly protecting read-only source calendars from writes.
- **Live iOS / macOS iCal subscription endpoint** (`/api/mental_load/calendar.ics?token=...`) — subscribe directly from Apple Calendar (`webcal://`) without needing a secondary Google Calendar.
- **Dynamic Multi-Device Lovelace Card (`custom:mental-load-card`)**:
  - Clickable 7-day heatmap selector switching the card dynamically to any upcoming day.
  - Real interactive checkboxes with instant optimistic toggle (`mental_load.toggle_checklist_item`) and completion progress pill (`done/total`).
  - Responsive container layout for iPhone, tablet wall panels, and desktop, plus quick buttons for Google Sync and iOS iCal copy.

---

## [1.2.2] — 2026-11-23

### Fixed
- **Antigravity CLI OAuth URL extraction** — fixed OSC-8 terminal hyperlink parsing in `agy_bridge_server.py` so the generated Google OAuth URL contains a single clean `scope` parameter (fixing Google Error 400 `invalid_request`).

---

## [1.2.1] — 2026-11-23

### Fixed
- **HA App Remote SSH OAuth flow** — configured PTY `TERM=xterm-256color`, `SSH_CONNECTION`, wide `TIOCSWINSZ`, and `stdin=subprocess.DEVNULL` so the `Antigravity CLI Bridge` App generates a clickable Google OAuth login link directly in the Home Assistant Ingress Web UI.

---

## [1.2.0] — 2026-11-23

### Added
- **Home Assistant App (`antigravity_bridge`)** — added `repository.yaml` and `antigravity_bridge/` add-on running `agy` directly inside Home Assistant OS with persistent `/data` storage and Ingress Web UI.
- Default Antigravity CLI bridge URL updated to `http://127.0.0.1:11435/v1`.

---

## [1.1.0] — 2026-11-23

### Added
- **Antigravity CLI (`agy`) provider** — use your Antigravity session without external OpenAI/Anthropic/Google API keys, either via local subprocess or `tools/agy_bridge_server.py`.
- Fixed `manifest.json` key ordering for `hassfest` and `hacs.json` metadata.

---

## [1.0.0] — 2026-11-23

### Added
- Initial release of **Mental Load Calendar Analyzer** for Home Assistant:
  - Multi-LLM support (`openai`, `anthropic`, `mistral`, `google`, `custom_openai`, `ha_conversation`, and local `heuristic` fallback).
  - 5-factor scoring engine (pacing/context switching, social battery/profile, mobility, live HA context, and feedback calibration memory).
  - `calendar.mental_load` all-day badges and timed invisible head-task blocks (`🧠`, `🚗`, `🎒`, `📝`).
  - 5 sensors (`today`, `tomorrow`, `week_average`, `peak_day`, `hidden_tasks_today`), `todo.*` push, threshold events/notifications/reactions, and initial Lovelace card.
