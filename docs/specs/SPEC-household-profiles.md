# Specification: Flexible Household & Person Profiles (`v1.10.0`)

**Source Intent:** [household-profiles.md](../intent/household-profiles.md)  
**Target Release:** `v1.10.0`

---

## 1. Objective & Scope
Replace the hardcoded school gym-bag dropdown (`sport_bag_mode`) and flat line-based child profiles (`children_profiles`) with a dynamic **Household & Persons** management flow supporting `1..N` adults (from single parents to multi-adult households) and `0..N` children, each with its own rich free-text routine/context textbox and optional calendar assignments feeding into **one shared household Mental Load score**.

### Explicitly Out of Scope
- Separate `sensor.mental_load_*` entities or Lovelace dashboards per adult.
- Hardcoded Python rules for school gym-bag bundling (`_apply_weekly_sport_bag_bundling`, `CONF_SPORT_BAG_MODE`, `WEEKLY_BAG_TAGS`).

---

## 2. Assumptions Made (For User Review)
> [!IMPORTANT]
> Please review these 5 assumptions before we move to implementation:
> 1. **Automatic Legacy Migration:** Existing `user_profile`, `children_profiles`, and `children_calendars` configs are automatically normalized via `normalize_household_members(config)` into `household_members` when loaded or edited, so no existing data is lost.
> 2. **Minimum One Adult:** Removing the last remaining adult in the UI is blocked (`cannot_remove_last_adult`); single-parent households have cleanly `1` adult and `0..N` children.
> 3. **Combined Read-Only Calendar Fetch:** Calendars assigned to any person are merged with the global household calendars (`CONF_CALENDARS`), fetched read-only, and tagged in the prompt with the member's role and name (`[Stundenplan/Kind: <Name>]` for children, `[<Name>]` for adults).
> 4. **Child Birthdate Format:** Child forms accept optional `DD.MM.YYYY` or `YYYY-MM-DD` dates (matching existing `parse_children_profiles` logic), cleanly allowing blank birthdates.
> 5. **Target Version `1.10.0`:** Removal of the temporary `sport_bag_mode` dropdown and introduction of structured `household_members` warrant minor bump **`1.10.0`**.

---

## 3. Tech Stack & Commands
- **Stack:** Python 3.12+, Home Assistant `ConfigFlow` / `OptionsFlow` / `SelectSelector` / `TextSelector(multiline=True)` / `EntitySelector`, `pytest`, `hassfest`.
- **Commands:**
  ```bash
  # Run all integration & sync unit tests
  python3 -m pytest tests services/anytype_ha_sync/tests -v

  # Validate Home Assistant manifest, translations & config_flow schema
  docker run --rm -v "$(pwd):/github/workspace" -w /github/workspace \
    ghcr.io/home-assistant/hassfest --integration-path /github/workspace/custom_components/mental_load
  ```

---

## 4. Data Model & Compatibility Contract

### `CONF_HOUSEHOLD_MEMBERS = "household_members"`
Stored as a `list[dict[str, Any]]` inside ConfigEntry `data` / `options`:

```python
{
    "id": "member_a1b2c3d4",          # stable unique ID within the entry
    "role": "adult",                  # "adult" | "child"
    "name": "Elternteil 1",           # display name shown in UI & prompts
    "birthdate": "",                  # YYYY-MM-DD or DD.MM.YYYY (used for role == "child")
    "calendars": ["calendar.work"],   # optional list[str] of HA calendar entity IDs
    "profile": "Freitext: Arbeitszeiten, Routinen, feste Aufgaben ...",
}
```

### Normalization & Legacy Migration (`normalize_household_members(config)`)
- If `config.get(CONF_HOUSEHOLD_MEMBERS)` is a non-empty list, sanitize entries (`id`, `role`, `name`, `birthdate`, `calendars`, `profile`) and ensure at least 1 adult exists.
- Otherwise migrate legacy fields:
  - Adult 1 from `CONF_USER_PROFILE` (`DEFAULT_USER_PROFILE` if missing), `name="Erwachsener 1"` (or `"Adult 1"` in English).
  - Children from `parse_children_profiles(CONF_CHILDREN_PROFILES)`: each parsed entry becomes a `role="child"` member keeping its parsed `birthdate` and `note`; legacy `CONF_CHILDREN_CALENDARS` are attached to existing children (or carried at child level) without losing any events.
- Legacy keys (`user_profile`, `children_profiles`, `children_calendars`, `sport_bag_mode`) are stripped once saved through the new options flow.

---

## 5. UI Flow (`ConfigFlow` & `OptionsFlow`)

### Setup Wizard (`MentalLoadConfigFlow`)
1. **Step `user`:** Household base calendars (`CONF_CALENDARS`), horizon, interval, language.
2. **Step `llm`:** AI provider, model, base URL / API key, temperature.
3. **Step `profile_and_reactions`:**
   - Primary adult fields directly in Step 3 (`adult_name`, optional `adult_calendars`, multiline `adult_profile`), live HA context (`context_todos`, `context_energy_sensor`, `context_weather`), display/sync toggles, and reactions.
   - Optional checkbox **`add_more_people`** (default `False`):
     - If `False` (ideal for single parents / quick setup): finishes immediately with 1 adult.
     - If `True`: transitions into the **Household Members Management** submenu (`add_adult` / `add_child` / `edit_member` / `remove_member` / `finish`) before creating the ConfigEntry.

### Options Menu (`MentalLoadOptionsFlow`)
1. `llm_settings` — AI provider & model
2. **`household_members`** *(new submenu)* — Shows current summary in step description (e.g. `Erwachsene: Anna | Kinder: Leo (8 Jahre)`):
   - `add_adult`: `name`, `calendars` (`EntitySelector(domain="calendar", multiple=True)`), `profile` (`TextSelector(multiline=True)`)
   - `add_child`: `name`, `birthdate` (`TT.MM.JJJJ` / `YYYY-MM-DD`, optional), `calendars` (`EntitySelector(domain="calendar", multiple=True)`), `profile` (`TextSelector(multiline=True)`)
   - `edit_member` (select dropdown of members) → opens pre-filled `edit_adult` or `edit_child` form
   - `remove_member` (select dropdown; rejects removing the last adult with error `cannot_remove_last_adult`)
3. `factors_and_profile` — 5 factor weights, linked To-Do lists, project baseline & step planning settings, live HA sensors (drops `sport_bag_mode`, `user_profile`, `children_profiles`, `children_calendars`)
4. `calendar_and_todo` — `CONF_CALENDARS`, horizon, interval, block display & defaults, To-Do push & external calendar sync
5. `active_reactions` — Thresholds, notify service, reaction entities

---

## 6. Prompt Contract, Calendar Tagging & Cleanup
- **`format_household_context_block(config, target_date)`** in `custom_components/mental_load/prompt_builder.py`:
  - Builds a clean `### Household Context` section rendered once in `build_system_prompt()`:
    - **Adults:** Lists every adult (`name`, optional calendars, full free-text `profile`). Explicitly instructs the AI that multiple adults share family logistics and only days where one adult is solo/unavailable increase strain.
    - **Children:** Lists every child (`name`, computed `age_display` + `stage_hint`, birthday proximity badge if `<= 14d`, optional calendars, and full free-text `profile` containing school/daycare routines such as *„Turnbeutel sonntags packen“* or *„Essen morgens zubereiten“*).
  - Per-day user prompt stays compact via `format_household_day_alerts(config, target_date)` (only birthdays `<= 14` days away).
- **Calendar Event Source Tagging** in `coordinator.py` (`_async_fetch_events_by_day()`):
  - Fetches `CONF_CALENDARS` plus all calendars attached to members in `household_members` (deduplicated).
  - Events from child calendars include `[Stundenplan/Kind: <Name>]` in `source_calendar` so consecutive school lessons do NOT inflate parental pacing stress; events from adult calendars include `[Erwachsener: <Name>]`.
- **Suppression Tags Kept:** `#no-prep`, `[ML:no-prep]`, `#noprep`, `#kein-packen` continue to suppress prep/packing/travel tasks in both LLM output (`_parse_day_payload`) and heuristic fallback (`analyze_day_heuristic`).
- **Removed:** `CONF_SPORT_BAG_MODE`, `SPORT_BAG_MODE_*`, `WEEKLY_BAG_TAGS`, `event_has_weekly_bag_tag`, and `_apply_weekly_sport_bag_bundling()`.

---

## 7. Project Structure & Code Style
```text
custom_components/mental_load/
├── const.py           # CONF_HOUSEHOLD_MEMBERS + role/default constants (removes CONF_SPORT_BAG_MODE)
├── config_flow.py     # Primary adult fields + dynamic household_members submenu
├── prompt_builder.py  # normalize_household_members(), format_household_context_block(), #no-prep
├── coordinator.py     # Member calendar merge & tagging; removes hardcoded gym-bag bundling
├── strings.json       # Updated DE base strings for hassfest
└── translations/
    ├── de.json
    └── en.json
```
- **Code Style:** Typed helpers (`list[dict[str, Any]]`), shallow early returns, pure transformation helpers in `prompt_builder.py` so unit tests run without live HA instances.

---

## 8. Testing Strategy & Boundaries
- **Testing:** Extend `tests/test_prompt_and_parser.py` (migration from legacy config, multi-adult & child free-text prompt formatting, single-parent 1-adult case, `#no-prep`) and `tests/test_coordinator_and_reactions.py` + `tests/test_calendar_and_sensors.py` (member calendar fetching/tagging, `OptionsFlow` add/edit/remove member lifecycle + last-adult protection, `setup_wizard` single-parent vs add-more path).
- **Boundaries:**
  - **Always:** Run `pytest tests services/anytype_ha_sync/tests -v` and `hassfest` after final edit; preserve Read-Only guard on source calendars; omit secrets/personal data.
  - **Ask First:** Any schema/entity breaking changes (e.g., per-person sensors — explicitly out of scope).
  - **Never:** Add new single-purpose routine dropdowns; manually copy files via Proxmox into the HA VM (`GEMINI.md`).

---

## 9. Success Criteria
1. `sport_bag_mode` dropdown is completely removed from ConfigFlow, OptionsFlow, `strings.json`, and `translations/*.json`.
2. Users can manage `1..N` adults (Name, optional Calendars, large Textbox) and `0..N` children (Name, Birthdate, optional Calendars, large Textbox) dynamically (Add / Edit / Remove) without needing any extra dropdowns.
3. Single-parent households (`1` adult, `0` children) complete setup in 3 steps without extra clicks and cannot delete their only adult.
4. Legacy configurations (`user_profile`, `children_profiles`, `children_calendars`) migrate cleanly with zero lost rules.
5. `pytest` (all suites) and `hassfest` (`Invalid integrations: 0`) pass after final edits.
