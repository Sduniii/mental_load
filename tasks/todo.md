# Tasks: `mental_load` v1.6.0 & `anytype-ha-sync`

## Phase 1: `anytype-ha-sync`

## Task 1: HA `local_todo` einrichten & Sync-Engine implementieren
**Description:** Anlegen von `todo.hausbau_anytype` in Home Assistant über `local_todo`, sowie Implementierung der idempotenten bidirektionalen Sync-Logik (`[ANYTYPE-<object_id>]`-Tagging) inkl. Extraktion von Aufgaben-Details und Checklisten aus Anytype Space `space_test_id`.
**Acceptance criteria:**
- [x] `todo.hausbau_anytype` existiert und ist in Home Assistant aktiv.
- [x] `services/anytype_ha_sync/sync.py` synchronisiert neue/geänderte/erledigte Tasks ohne Duplikate und überträgt in HA abgehakte Items zurück nach Anytype.
- [x] `services/anytype_ha_sync/tests/test_anytype_ha_sync.py` deckt Erstellen, Aktualisieren, Abhaken in beide Richtungen und Offline-Fehler ab.
**Verification:**
- [x] Tests pass: `python3 -m pytest services/anytype_ha_sync/tests/test_anytype_ha_sync.py -v`
- [x] Manual check: `ha_get_state("todo.hausbau_anytype")` liefert gültigen Zustand.
**Dependencies:** None
**Files likely touched:**
- `services/anytype_ha_sync/sync.py` (neu)
- `services/anytype_ha_sync/tests/test_anytype_ha_sync.py` (neu)
**Estimated scope:** Small (2 files)

## Task 2: Container & Self-Hosted `any-sync` Netzwerk-Konfiguration vorbereiten
**Description:** Dockerfile und Start-Skript für den Headless-`anytype-cli`-Container auf `homelab-server` erstellen (inkl. `network.yaml` für `anytype.example.internal:33010/33020` und `networkId`) sowie Image in die lokale Registry / Komodo-Konfiguration einbinden.
**Acceptance criteria:**
- [x] Container baut fehlerfrei und startet `anytype-cli` headless gegen `anytype.example.internal` mithilfe von `ANYTYPE_ACCOUNT_KEY` + `HA_TOKEN`.
- [x] Compose-Konfiguration enthält keine hartcodierten Secrets.
**Verification:**
- [x] Build succeeds: `python3 -m py_compile services/anytype_ha_sync/sync.py` und `docker build`/`docker compose config`
**Dependencies:** Task 1
**Files likely touched:**
- `services/anytype_ha_sync/Dockerfile` (neu)
- `services/anytype_ha_sync/entrypoint.sh` (neu)
- `services/anytype_ha_sync/network.yaml` (neu)
- `services/anytype_ha_sync/requirements.txt` (neu)
**Estimated scope:** Medium (4 files)

## Task 3: Komodo-Deployment auf `homelab-server` & Live-Sync nach `todo.hausbau_anytype`
**Description:** Nach Hinterlegung von `ANYTYPE_ACCOUNT_KEY` und `HA_TOKEN` den Sync-Service in Komodo auf `homelab-server` deployen und prüfen, dass alle offenen Tasks aus Space `Hausbau` (`space_test_id`) sauber in `todo.hausbau_anytype` erscheinen.
**Acceptance criteria:**
- [ ] Container läuft stabil auf `homelab-server` (`state: running`) ohne Login-/Netzwerkfehler.
- [ ] `todo.hausbau_anytype` enthält die offenen Anytype-Hausbau-Tasks ohne Duplikate.
**Verification:**
- [ ] Manual check: `ha_call_service("todo", "get_items", entity_id="todo.hausbau_anytype", return_response=True)` zeigt erwartete Items inkl. `[ANYTYPE-...]`-Marker.
**Dependencies:** Task 2 (und einmalige Bereitstellung `ANYTYPE_ACCOUNT_KEY` durch Benutzer)
**Files likely touched:**
- Komodo Stack `anytype` auf `homelab-server`
**Estimated scope:** Small (1 stack)

## Checkpoint: Nach Tasks 1–3 (`anytype-ha-sync`)
- [ ] `pytest` für `anytype_ha_sync` bestanden
- [ ] `todo.hausbau_anytype` ist live mit den „Hausbau“-Tasks gefüllt
- [ ] Abhaken eines Test-Items funktioniert ohne Duplikate

---

## Phase 2: `project-baseline-load` (`mental_load` Integration & Karte)

## Task 4: Konstanten, Modelle & Config-Flow für Projekt-Grundlast und Slot-Planung
**Description:** Hinzufügen der neuen Konfigurationsschlüssel (`CONF_PROJECT_BASELINE_PER_ITEM`, `CONF_PROJECT_BASELINE_CAP`, `CONF_PROJECT_PLANNING_ENABLED`, `CONF_MAX_PROJECT_STEPS_WEEKEND`, `CONF_MAX_PROJECT_STEPS_WEEKDAY`) in `const.py`, Erweiterung von `DayLoadAnalysis` (`project_baseline_load`, `open_project_count`) in `models.py` und `HiddenTask` (`requires_open_business`, `is_noisy`), sowie Options-Felder in `config_flow.py` / `translations/`.
**Acceptance criteria:**
- [x] `DayLoadAnalysis.to_dict()` / `from_dict()` rundtrippen `project_baseline_load` und `open_project_count` rückwärtskompatibel.
- [x] Options-Flow erlaubt Anpassung von `% pro To-do` (Default `2.0`), `Deckel %` (Default `15`), `Projekt-Zerlegung aktiv` (Default `True`) und Wochenend-/Werktags-Limits (`2` / `1`).
**Verification:**
- [x] Tests pass: `python3 -m pytest tests -v`
- [x] Build succeeds: `python3 -m py_compile custom_components/mental_load/*.py`
**Dependencies:** None (Schnittstelle ist `todo.*`)
**Files likely touched:**
- `custom_components/mental_load/const.py`
- `custom_components/mental_load/models.py`
- `custom_components/mental_load/config_flow.py`
- `custom_components/mental_load/strings.json`
- `custom_components/mental_load/translations/de.json`
- `custom_components/mental_load/translations/en.json`
**Estimated scope:** Medium (5–6 files, je nach Übersetzungs-Sync)

## Task 5: Gedeckelte tägliche Projekt-Grundlast in Coordinator, Sensoren & Lovelace-Card
**Description:** Vollständiges Zählen aller offenen `CONF_CONTEXT_TODOS`-Items in `_async_collect_ha_context` (`open_todos_count` unbeschnitten + Vorschau), Berechnung von `compute_project_baseline` (`+2%` pro Item, Deckel `15%`), Aufrechnung auf jeden Tag im 30-Tage-Horizont inkl. `fast_empty_day`, Übertragung in `sensor.py` (`project_baseline_load`, `open_project_count`, `daily_scores`) sowie Darstellung des Grundrauschens in `frontend/mental-load-card.js`.
**Acceptance criteria:**
- [x] Bei 0 Terminen und 10 offenen Hausbau-To-dos liefert `fast_empty_day` einen Score von `15` (Deckel) und `project_baseline_load == 15`.
- [x] Sind 0 To-dos offen, fällt `project_baseline_load` auf `0`.
- [x] Lovelace-Karte zeigt das laufende Projekt-Grundrauschen (z. B. `+15% Grundlast · 18 offene Projekte`) klar an.
**Verification:**
- [x] Tests pass: `python3 -m pytest tests -v`
- [x] Build succeeds: `node --check custom_components/mental_load/frontend/mental-load-card.js`
**Dependencies:** Task 4
**Files likely touched:**
- `custom_components/mental_load/coordinator.py`
- `custom_components/mental_load/prompt_builder.py`
- `custom_components/mental_load/sensor.py`
- `custom_components/mental_load/frontend/mental-load-card.js`
- `tests/test_coordinator_and_reactions.py`
**Estimated scope:** Medium (5 files)

## Checkpoint: Nach Tasks 4–5 (`project-baseline-load`)
- [x] Alle bestehenden + neuen Baseline-Unit-Tests grün
- [x] `node --check` und `py_compile` fehlerfrei

---

## Phase 3: `project-task-breakdown` & Google-Kalender-Bereinigung

## Task 6: KI-/Heuristik-Zerlegung großer To-dos mit Sonntags-Regel & Slot-Verteilung
**Description:** Gecachte Projekt-Aufschlüsselung (`_async_build_project_steps`) für offene Items aus `CONF_CONTEXT_TODOS`: zerlegt große To-dos in 20–45-Min.-Etappen (`category="project_step"`, `placement_mode="free_slot"`) inkl. Checkliste sowie Flags `requires_open_business` und `is_noisy` (+ deterministischer Keyword-Filter für Sonntag). Plant am Wochenende bis zu 2 Etappen (`10:00` / `14:30`, sonntags nur ruhige Indoor/Planungs-Etappen!) und an freien Werktagen (`score < 35`, `events == 0`) 1 Etappe nach Feierabend (`17:15`) ein.
**Acceptance criteria:**
- [x] Große To-dos wie *OG: Fußbodenheizung* oder *Hänger TÜV* werden in konkrete Teil-Etappen zerlegt; der LLM-Call wird über den Hash der offenen To-dos gecacht.
- [x] Am Sonntag (`weekday() == 6`) werden **niemals** Aufgaben mit `requires_open_business=True`, `is_noisy=True` oder Keywords wie `TÜV`, `Baumarkt`, `einkaufen`, `Schleifen`, `Bohren`, `Vertikutieren`, `anrufen` geplant.
- [x] Samstags werden bis zu `2` Etappen (auch Besorgungen/Lärm/TÜV) auf ruhige Tage (`score < 40`, `<= 1` Event) verteilt; volle Tage bleiben unberührt.
**Verification:**
- [x] Tests pass: `python3 -m pytest tests -v` (inkl. explizitem Sonntags-Test mit TÜV/Baumarkt/Schleifen vs. Planung)
**Dependencies:** Task 5
**Files likely touched:**
- `custom_components/mental_load/prompt_builder.py`
- `custom_components/mental_load/coordinator.py`
- `tests/test_coordinator_and_reactions.py`
**Estimated scope:** Small (3 files)

## Task 7: Automatische Bereinigung veralteter Sync-Events in `calendar.mental_load_2`
**Description:** Erweiterung von `async_sync_to_external_calendar` in `coordinator.py`, sodass beim Sync vor dem Erstellen neuer Events alle vorhandenen `[ML-BADGE-...]`- und `[ML-TASK-...]`-Einträge im Zielkalender geprüft und veraltete/überholte Einträge automatisch per `calendar.delete_event` entfernt werden. Nicht-Mental-Load-Events bleiben unberührt.
**Acceptance criteria:**
- [x] Ändert sich der Score eines Tages oder verschieben sich Tasks, wird der alte Badge bzw. Task aus `calendar.mental_load_2` gelöscht, bevor der neue Stand synchronisiert wird.
- [x] Events ohne `[ML-BADGE-` / `[ML-TASK-`-Marker werden niemals angefasst.
**Verification:**
- [x] Tests pass: `python3 -m pytest tests -v`
**Dependencies:** Task 6
**Files likely touched:**
- `custom_components/mental_load/coordinator.py`
- `tests/test_coordinator_and_reactions.py`
**Estimated scope:** Small (2 files)

## Checkpoint: Nach Tasks 6–7 (`v1.6.0` Code & Tests)
- [x] Alle Unit-Tests bestanden (`python3 -m pytest tests -v`)
- [ ] `manifest.json`, `const.py`, `CHANGELOG.md`, `info.md` auf `1.6.0` aktualisiert, Tag `1.6.0` erstellt und zu GitHub gepusht

---

## Phase 4: Rollout & End-to-End-Verifikation

## Task 8: HACS Update auf `1.6.0`, Options-Aktivierung (`todo.hausbau_anytype`) & Live-Check
**Description:** Update über Marketplace/HACS auf `1.6.0` herunterladen, HA neu starten, `todo.hausbau_anytype` als einzige Liste in `CONF_CONTEXT_TODOS` aktivieren, 30-Tage-Auswertung abwarten und Sensor-/Kalender-/Karten-Ergebnisse prüfen (inkl. Sonntags-Check im 30-Tage-Horizont).
**Acceptance criteria:**
- [ ] `mental_load` `1.6.0` läuft im Status `loaded` mit `horizon_days: 30` und `context_todos: ["todo.hausbau_anytype"]`.
- [ ] Freie Tage zeigen die gedeckelte Projekt-Grundlast (`<= 15%`), Samstage enthalten max. 2 konkrete Etappen, Sonntage enthalten **keine** lauten/geschäftsabhängigen Etappen.
- [ ] `calendar.mental_load_2` ist frei von doppelten/veralteten Einträgen.
**Verification:**
- [ ] Manual check: `ha_get_state` auf `sensor.mental_load_today`, `sensor.mental_load_week_average` und `calendar.get_events` auf `calendar.mental_load_2`.
**Dependencies:** Task 3, Task 7
**Files likely touched:**
- Live HA Integration Config (`01M83SK46B6TG35JMEDFCJ87X5`)
**Estimated scope:** Small (Config + Verification)
