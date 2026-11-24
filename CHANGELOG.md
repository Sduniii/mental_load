# Changelog

## [1.9.4] — 2026-11-24

### Added & Changed
- **Configurable School Gym Bag Packing (`sport_bag_mode`):** Added option in *Factors & Profile* / setup wizard (`per_day` [default: pack before each gym day], `sunday_weekly` [pack once on Sunday 17:30], or `off` [disable automatic gym-bag reminders]), adapting cleanly to different schools and children's ages.
- **Per-Series `#weekly-bag` / `#sonntag-packen` Tag:** Tagging an individual school Sport/Turnen event or series with `#weekly-bag`, `[ML:weekly-bag]`, or `#sonntag-packen` bundles that child/series once on Sunday even when `per_day` is selected globally for older/other children.

---

## [1.9.3] — 2026-11-24

### Added & Fixed
- **Calendar Suppression Tags (`#no-prep`, `[ML:no-prep]`, `#noprep`, `#kein-packen`):** Marked events never generate hidden prep, packing, or travel tasks in either LLM prompts or the local heuristic fallback.
- **Weekly Sunday Gym-Bag Bundling:** Suppresses daily weekday evening-before gym-bag tasks for regular school `Sport`/`Turnen` and schedules a single Sunday evening task (`17:30–17:45`) if the next school week has gym lessons (skipping if Sunday already has a gym-bag calendar event).
- **Startup Cache Protection (`EVENT_HOMEASSISTANT_STARTED`):** Delays the initial background refresh until Home Assistant finishes starting and keeps persisted cache entries intact instead of overwriting them with `0%` if `calendar.get_events` fails temporarily.
- **120s Project Breakdown Timeout:** Extends `async_breakdown_projects()` HTTP timeout to `BATCH_REQUEST_TIMEOUT_SECONDS` (`120s`) to avoid premature timeouts with `antigravity_cli`.

---

## [1.9.2] — 2026-11-24

### Fixed
- **Self-Healing External Calendar Sync (`async_sync_to_external_calendar`):** Reconciles against actual live `[ML-BADGE-*]` and `[ML-TASK-*]` events returned by `calendar.get_events` (including dict-style `start` dates and summary-hashed dedup keys), automatically recreating missing days or badges whose summary changed at the same percentage score and pruning duplicates.

---

## [1.9.1] — 2026-11-23

### Fixed
- **Non-Blocking Startup (`async_setup_entry`):** Integration seeds sensors/calendar immediately from the persistent cache and runs the initial live refresh in the background so Home Assistant no longer stays stuck in `setup_in_progress` during slow cold refreshes.
- **Batch Timeout & Chunk Tuning for `antigravity_cli`:** Added `BATCH_REQUEST_TIMEOUT_SECONDS = 120`, reduced `antigravity_cli` batch chunk size from `7` to `4`, and improved exception logging (`TimeoutError` type name instead of empty `()`) to eliminate cascading 45s batch timeouts.
- **To-Do Push Fallback (`due_datetime` → `due_date`):** Automatically falls back to `due_date` (or no due field) when pushing hidden tasks to target `todo.*` entities that do not support `due_datetime` (e.g. `todo.zuhause`).

---

## [1.9.0] — 2026-11-23

### Performance & Token Optimization (~75–90% fewer tokens)
- **Stabilized Cache Fingerprints (`_compute_day_fingerprint`):** Weather and energy sensors are only included for today and tomorrow (`offset <= 1`) and quantized (weather condition without decimal temperature jitter; energy rounded to 10% buckets), preventing hourly cache invalidation across the 30-day horizon.
- **Multi-Day LLM Batching (`async_analyze_days_batch`):** Bundles up to 7 uncached non-empty days into a single structured request (`{"days": {...}}`), cutting redundant system-prompt input tokens by ~65–75% on cold refreshes while falling back cleanly per missing day.
- **Deduplicated Children & Defaults Context:** Moved full child profile & `[Stundenplan/Kind]` instructions + time defaults exclusively into `build_system_prompt()`, keeping only ≤14-day birthday alerts in day prompts.
- **Compact Prompts & Noise Filtering:** Removed `Gap Xm` noise for normal spacing, omitted `"Not configured"` fields, shortened event descriptions (`120` chars), trimmed project detail prefixes, and fast-pathed empty today/tomorrow days (`0` tokens).
- **Anthropic Prompt Caching:** Sends `system` with `cache_control: {"type": "ephemeral"}` alongside static system-prompt prefixes for OpenAI and Google Gemini.

---

## [1.8.0] — 2026-11-23

### Added
- **Dedicated Children & School Timetable Calendars (`children_calendars`):** Select additional read-only calendars (e.g. a child's school timetable). Events are tagged `[Stundenplan/Kind]` so consecutive lessons do not inflate parental pacing stress, while special items (Sport, Schwimmen, Ausflug, Elternabend, Ausfall) generate targeted prep/packing tasks.
- **Children Profiles & Dynamic Age Calculation (`children_profiles`):** Enter children's names and birthdates (`YYYY-MM-DD` or `DD.MM.YYYY`); the analyzer automatically computes exact age (months under age 2, years from age 2+), developmental stage context, and 14-day birthday reminders for every forecast day.

### Security & Privacy
- Removed hardcoded personal/homelab domain URLs, Space/Network/Peer IDs, maintainer email, and local filesystem paths from repository templates and documentation.

---

## [1.7.0] — 2026-11-23

### Added
- **Anytype Task Priority Support (`Priorität` / `Status: In Progress` / `Due date`):**
  - `anytype_ha_sync` now extracts Anytype's `priority` (`Prio: Hoch | Mittel | Niedrig`) and `due_date` fields into Home Assistant To-Do items.
  - Priority-weighted daily baseline load: `Hoch` (`+3%`), `Mittel`/`Normal` (`+2%`), and `Niedrig` (`+1%`) with the configurable `15%` cap still enforced.
  - 4-tier priority ranking (`high` > `medium` / `In Progress` > `normal` > `low`) ensures urgent and high-priority Hausbau projects claim the earliest calm weekend and weekday slots first while strictly respecting the Sunday quiet-only rule.

---

## [1.6.0] — 2026-11-23

### Added
- **24/7 Anytype ↔ Home Assistant Sync Worker (`services/anytype_ha_sync/`):** Headless Docker service syncing Anytype tasks (`Hausbau` space) bidirectionally into a native Home Assistant `local_todo` entity (`todo.hausbau_anytype`) via `[ANYTYPE-<id>]` markers without duplicates.
- **Capped Daily Project Baseline Load (`project_baseline_load`):** Ongoing unscheduled items from `CONF_CONTEXT_TODOS` contribute a configurable daily background baseline (`+2%` per item, capped at `15%`) across the entire 30-day horizon including empty days, surfaced in sensors and the Lovelace card (`🧱 +15% Projekt-Grundlast`).
- **Project Sub-Task Decomposition & Strict Sunday Constraint:** Large project To-Dos are broken down into 20–45 min actionable steps (`🔧 Etappe`) scheduled onto calm weekends (up to `2/day`) and free weekday afternoons (`17:15`, max `1/day`), strictly blocking closed businesses/calls and noisy work on Sundays (`weekday == 6`).
- **Automatic Stale Event Cleanup in External Calendar Sync:** `async_sync_to_external_calendar` now automatically removes outdated `[ML-BADGE-...]` and `[ML-TASK-...]` events from `calendar.mental_load_2` before writing updated analyses.

---

## [1.5.0] — 2026-11-23

### Added
- **30-Day Forecast Horizon:** Extended `horizon_days` up to **30 days** (new default: `30`) with a fast empty-day path so empty future days do not waste LLM API/CLI calls.
- **7–14 Day Lead Window for Major Events:** Big events (birthdays, gifts, vacations/trips, parties, school enrollment, presentations, hospital visits) now automatically schedule advance preparation **7–14 days ahead** onto the calmest day in that window.
- **Scrollable 30-Day Ribbon in `custom:mental-load-card`:** Smooth horizontally scrollable day selector with per-day hidden task count indicators (`• N`).

---

## [1.4.0] — 2026-11-23

### Added
- **Smart Lead-Time & Multi-Day Load-Balancing:** Preparation tasks with advance lead time (e.g. withdrawing cash, buying gifts, checking schedules, packing bags for early appointments) are automatically scheduled onto earlier days — the evening before (`19:30`) for packing/morning prep, or the calmest lowest-load day `1–4` days ahead (`17:30`) for errands.
- **Bidirectional Appointment Linking:** Advance tasks show `🔗 Für <Datum>: <Termin>` (clickable in the Lovelace card to jump straight to the appointment day), while appointment days list `🔗 Vorab an Vortagen eingeplant` with live completion status and jump links back to the prep day.
- **Dynamic Score Relief on Completion:** Checking off preparation tasks dynamically lowers the effective Mental Load score on both the prep day and the linked appointment day in real time without extra AI calls.

---

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
