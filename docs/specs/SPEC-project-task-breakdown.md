# Spec: `project-task-breakdown`

**Module ID:** `project-task-breakdown`  
**Capability Map:** [CAPABILITY-MAP.md](./CAPABILITY-MAP.md)  
**Depends on:** `project-baseline-load`  

## Objective
Automatische Aufschlüsselung großer, unterminierter Projekt-To-dos (z. B. *OG: Fußbodenheizung*, *Sanierung Dachbalken*, *Teich*, *Gartenarbeit*, *Arzt-/Handwerker-Termine vereinbaren*) aus `CONF_CONTEXT_TODOS` in kleine, konkret ausführbare Teil-Etappen (20–45 Min. inkl. Checkliste), die gezielt auf **freie Tage im 30-Tage-Horizont (vor allem Wochenenden oder ruhige Nachmittage)** eingeplant werden. Zusätzlich automatische Bereinigung veralteter `[ML-BADGE-...]` / `[ML-TASK-...]`-Einträge in `calendar.mental_load_2`, wenn sich Tagesbewertungen ändern.

## Tech Stack
- **Integration:** Home Assistant Custom Component `custom_components/mental_load`
- **KI-Provider:** Bestehender `LLMProviderClient` (`antigravity_cli` / OpenAI / Anthropic / Gemini / Heuristic-Fallback)
- **Kalender-Sync & UI:** `coordinator.py` (`calendar.delete_event` + `calendar.create_event`), `todo.py`, `frontend/mental-load-card.js`

## Commands
```bash
python3 -m pytest tests -v
python3 -m py_compile custom_components/mental_load/*.py
node --check custom_components/mental_load/frontend/mental-load-card.js
```

## Project Structure
```text
custom_components/mental_load/
├── const.py            # CONF_PROJECT_PLANNING_ENABLED, CONF_MAX_PROJECT_STEPS_PER_DAY, Slot-Defaults
├── models.py           # HiddenTask.category="project_step", placement_mode="free_slot", source_todo_item
├── prompt_builder.py   # KI-Zerlegung offener Projekt-To-dos in kleine 20-45 Min. Arbeitspakete + Checkliste
├── coordinator.py      # Slot-Vergabe auf ruhige Tage/Wochenenden + Bereinigung alter Events in calendar.mental_load_2
├── sensor.py           # Attribute für Wochen- & Monatsübersicht ohne Überschreitung der Recorder-Limits wo nötig
└── tests/
    └── test_coordinator_and_reactions.py
```

## Code Style
- Große To-dos werden über eine eigene Projekt-Planungsstufe (`async_decompose_projects` oder Batch-Prompt pro Refresh, gecacht über den Hash der offenen `todo.*`-Items!) zerlegt — nicht 30-mal redundant pro Tag:

```python
# Slot-Kriterien für freie Tage / ruhige Nachmittage:
def _is_eligible_project_slot(day: DayLoadAnalysis, event_count: int) -> bool:
    """Only schedule project sub-tasks onto genuinely light days or weekends."""
    is_weekend = day.date.weekday() >= 5
    if is_weekend:
        return day.score < 40 and event_count <= 1
    return day.score < 35 and event_count == 0
```

- Zeitfenster:
  - Wochenende (`Sa`/`So`): vormittags (`10:00–11:30`) oder nachmittags (`14:30–16:00`), max. `2` Etappen pro Wochenende bzw. konfigurierbares Tageslimit (`DEFAULT_MAX_PROJECT_STEPS_PER_DAY = 1`).
  - Ruhige Werktage (terminfrei): später Nachmittag / Feierabend-Slot (`17:15–18:00`), damit die Kernarbeitszeit (`09:00–17:00`) geschützt bleibt.
- Jeder generierte Teil-Schritt erhält eine eigene `HiddenTask` mit `category="project_step"`, `placement_mode="free_slot"`, konkretem Titel (z. B. *„🔧 Etappe: Material- & Heizkreisliste für OG Fußbodenheizung prüfen“*) und abhakbarer Checkliste.
- **Bereinigung in `calendar.mental_load_2`:** Vor dem Anlegen neuer `[ML-BADGE-...]` / `[ML-TASK-...]`-Blöcke prüft `async_sync_to_external_calendar` existierende Events im Zielkalender innerhalb des Horizonts und löscht überholte `[ML-BADGE-...]` / `[ML-TASK-...]`-Einträge per `calendar.delete_event` (statt Duplikate anzuhäufen).

## Testing Strategy
- **Unit Tests (`pytest`):**
  - Ein großes To-do (z. B. *„OG: Fußbodenheizung“*) wird über LLM/Heuristik in kleine `project_step`-Blöcke zerlegt und **nur** auf freie Tage (`score < 35` / Wochenende) verteilt — niemals auf hochbelastete Tage (`>= 65%`).
  - Caching verhindert erneuten LLM-Aufruf für Projektzerlegung, solange `open_todos` unverändert bleiben.
  - `async_sync_to_external_calendar` löscht veraltete `[ML-BADGE-...]` / `[ML-TASK-...]`-Einträge und lässt Nicht-Mental-Load-Events unberührt.

## Boundaries
- **Always:** Quellkalender (`CONF_CALENDARS`) niemals beschreiben; maximal 1–2 Projekt-Etappen pro ruhigem Tag einplanen (keine Überfrachtung freier Tage); bestehende Checklisten-Entlastung (`_apply_dynamic_completion_relief`) unterstützt auch `project_step`-Blöcke.
- **Ask first:** Ob das Abhaken einer Teil-Etappe in `mental_load` das übergeordnete Anytype-To-do unangetastet lässt (Standard: Ja, da es nur eine Teil-Etappe ist!) bis das Projekt komplett in Anytype/HA als erledigt markiert wird.
- **Never:** Keine Einplanung von Baustellen-Tasks in geschützte Kernarbeitszeiten (`09:00–17:00` Mo–Fr) oder späte Abende nach `18:30` (Schutz des introvertierten Feierabends).

## Success Criteria
1. Große offene To-dos aus `todo.hausbau_anytype` (und optional weiteren To-do-Listen) werden automatisch in 20–45-Minuten-Schritte mit konkreter Checkliste aufgeteilt.
2. Im 30-Tage-Horizont tauchen an freien Tagen und Wochenenden 1–2 konkrete Etappen pro freiem Tag in der `mental_load`-Karte und in `calendar.mental_load_2` auf; volle Tage bleiben frei von Projekt-Blöcken.
3. Bei Neuanalysen bereinigt `async_sync_to_external_calendar` veraltete `[ML-BADGE-...]` und `[ML-TASK-...]`-Einträge automatisch aus `calendar.mental_load_2`.

## Resolved Decisions & Sonntags-Regel (`sunday_quiet_only`)
- **Tageslimit am Wochenende:** Bis zu **2 Projekt-Etappen pro Wochenendtag** (`DEFAULT_MAX_PROJECT_STEPS_WEEKEND = 2`), an ruhigen Werktagen max. **1 Etappe** nach Feierabend (`17:15–18:00`).
- **Strikte Sonntags-Regel („Sonntags alles geschlossen“):**
  - Sonntags (`weekday == 6`) sind **Baumärkte/Läden, TÜV, Werkstätten, Ärzte, Ämter und Telefon-Hotlines geschlossen** und **laute Bau-/Gartenarbeiten** (*Schleifen*, *Bohren*, *Sägen*, *Rasenmähen*, *Vertikutieren*) verboten.
  - Auf den Sonntag dürfen daher **nur ruhige Indoor-, Planungs-, Recherche-, Mess- oder Schreib-/Bestell-Etappen** (z. B. *Heizkreis-Layout planen*, *Garantiebeleg raussuchen*, *Maße aufnehmen*, *Material online recherchieren*, *leise Kleinteil-Montage*) geplant werden (`requires_open_business: false`, `is_noisy: false`).
  - Etappen, die **geöffnete Geschäfte, Fahrten zum Baumarkt/TÜV, Anrufe oder Lärm** erfordern, dürfen ausschließlich auf **Samstage (`weekday == 5`)** oder **freie Werktagsnachmittage (`Mo–Fr`)** gelegt werden.

```python
# Slot- und Sonntags-Eignung für Projekt-Etappen:
def is_step_allowed_on_day(step: dict[str, Any], target_date: datetime.date) -> bool:
    if target_date.weekday() == 6:  # Sonntag
        if step.get("requires_open_business") or step.get("is_noisy"):
            return False
    return True
```
