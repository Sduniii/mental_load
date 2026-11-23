# Implementation Plan: Anytype-Sync, Projekt-Grundlast & Freizeit-Aufschlüsselung (`v1.6.0`)

**Specs:**
- [CAPABILITY-MAP.md](../docs/specs/CAPABILITY-MAP.md)
- [SPEC-anytype-ha-sync.md](../docs/specs/SPEC-anytype-ha-sync.md)
- [SPEC-project-baseline-load.md](../docs/specs/SPEC-project-baseline-load.md)
- [SPEC-project-task-breakdown.md](../docs/specs/SPEC-project-task-breakdown.md)

## Overview
Erweiterung des Systems in zwei klar getrennten Bausteinen:
1. **`anytype-ha-sync` (Homelab / Komodo auf `homelab-server`):** Headless-Container neben `/opt/anytype`, der Space **„Hausbau“** (`space_test_id`) 24/7 bidirektional in Home Assistant (`todo.hausbau_anytype` über `local_todo`) spiegelt.
2. **`mental_load` v1.6.0 (Home Assistant Integration & Card):**
   - Konfigurierbare, gedeckelte tägliche Projekt-Grundlast (`+2%` pro offenem Item in `CONF_CONTEXT_TODOS`, Deckel `15%`) auf alle 30 Tage inkl. `fast_empty_day` und Anzeige in Sensoren + Karte.
   - Gecachte KI-/Heuristik-Aufschlüsselung großer Projekt-To-dos in 20–45-Min.-Etappen (`project_step`, `placement_mode="free_slot"`) mit bis zu **2 Etappen/Tag am Wochenende** und **strikter Sonntags-Regel** (keine Läden, kein TÜV, keine Telefonate, kein Lärm am Sonntag – nur ruhige Planung/Indoor).
   - Automatische Bereinigung veralteter `[ML-BADGE-...]` / `[ML-TASK-...]`-Events in `calendar.mental_load_2`.

## Architecture Decisions
- **Home Assistant `local_todo` als stabile Grenze:** `mental_load` kommuniziert niemals direkt mit Anytype, sondern liest `todo.hausbau_anytype`. Dadurch funktioniert `mental_load` weiter, falls Anytype vorübergehend offline ist.
- **Account-Key / Recovery-Phrase über Komodo-`.env`:** Der `anytype-ha-sync`-Container erhält das `network.yaml` für `anytype.example.internal` und authentifiziert sich per `ANYTYPE_ACCOUNT_KEY` (Secret nicht in Git). Für die sofortige Erstbefüllung kann `todo.hausbau_anytype` optional zusätzlich einmalig über die bestehende Anytype-Session befüllt werden.
- **Sonntags-Metadaten pro Etappe:** Jede generierte `project_step`-Etappe trägt `requires_open_business: bool` und `is_noisy: bool` (plus deterministische deutsche Keyword-Absicherung gegen *TÜV, Baumarkt, Einkauf, Anruf, Amt, Arzt, Schleifen, Bohren, Sägen, Vertikutieren, Mähen*), sodass der Slot-Scheduler diese niemals auf einen Sonntag (`weekday == 6`) legt.
- **Recorder-Schutz:** Große `days_detail`-Blöcke werden für 30 Tage kompakt gehalten bzw. `project_baseline_load` schlank in `daily_scores` integriert, ohne Funktionalität der Karte einzuschränken.

## Dependency Graph
```text
todo.hausbau_anytype (HA local_todo)
   ▲
   │ [Task 1-3] anytype-ha-sync (homelab-server / Komodo)
   │
   ▼
models.py + const.py + config_flow.py [Task 4]
   │
   ├──► project-baseline-load (coordinator + prompt_builder + sensor + card) [Task 5]
   │
   └──► project-task-breakdown (project decomposition + Sunday rule + calendar cleanup) [Task 6-7]
           │
           ▼
        Live Deploy, HACS 1.6.0, Config Update & 30-Tage-Verifikation [Task 8]
```

## Task List

### Phase 1: `anytype-ha-sync` (`homelab-server` ↔ Home Assistant)
- [ ] Task 1: HA `local_todo` (`todo.hausbau_anytype`) einrichten & Anytype Sync-Engine (`services/anytype_ha_sync/sync.py` + Unit Tests) bauen
- [ ] Task 2: Container (`Dockerfile`, `docker-compose` Erweiterung) bauen & testen
- [ ] Checkpoint: Sync-Logik & Container-Build verifizieren (mit Nutzer für `ANYTYPE_ACCOUNT_KEY` auf `homelab-server`)
- [ ] Task 3: Stack auf `homelab-server` über Komodo deployen & Live-Sync nach `todo.hausbau_anytype` prüfen

### Phase 2: `project-baseline-load` (`mental_load` Integration & Karte)
- [ ] Task 4: Konstanten, Datenmodelle & Options-Flow für gedeckelte Projekt-Grundlast + Wochenend-/Sonntagsregeln
- [ ] Task 5: Gedeckelte tägliche Grundlast (`+2%` / Deckel `15%`) in Coordinator, `fast_empty_day`, Sensoren & Lovelace-Card
- [ ] Checkpoint: `pytest` + `node --check` für Baseline-Grundlast & Karten-Anzeige

### Phase 3: `project-task-breakdown` & Kalender-Bereinigung
- [ ] Task 6: Projekt-Zerlegung in 20–45 Min. Teil-Etappen inkl. Sonntags-Regel & Slot-Verteilung (2/Tag Wochenende, 1/Tag ruhiger Nachmittag)
- [ ] Task 7: Automatische Bereinigung veralteter `[ML-BADGE-...]` / `[ML-TASK-...]`-Events in `async_sync_to_external_calendar`
- [ ] Checkpoint: Volle Test-Suite (`pytest`), Release `v1.6.0` (Tag + GitHub Push)

### Phase 4: Rollout & End-to-End-Verifikation
- [ ] Task 8: HACS Update auf `1.6.0`, HA Restart, `todo.hausbau_anytype` in `CONF_CONTEXT_TODOS` aktivieren & 30-Tage-Auswertung inkl. `calendar.mental_load_2` prüfen

*(Historie v1.0.0–v1.5.0 vollständig abgeschlossen.)*
