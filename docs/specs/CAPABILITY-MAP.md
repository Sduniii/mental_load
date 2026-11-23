# Capability Map: Anytype-Projekt-Sync & Tägliche Grundlast (`v1.6.0`)

**Initiative:** Integration von Anytype-Langzeitprojekten („Hausbau“) und offenen Dauer-To-dos in die tägliche Mental-Load-Bewertung und Wochenend-/Freizeitplanung.  
**Intent-Referenz:** [anytype-todo-mental-load.md](../intent/anytype-todo-mental-load.md)

| Module id | Responsibility | Depends on |
|---|---|---|
| `anytype-ha-sync` | Headless 24/7-Synchronisation der offenen Tasks (inkl. Kontext/Unterseiten) aus dem Anytype-Space „Hausbau“ (`space_test_id` via `any-sync-bundle` / `anytype.example.internal`) in Home Assistant (`todo.hausbau_anytype`, bidirektionaler Erledigt-Abgleich). | — |
| `project-baseline-load` | Berechnung einer konfigurierbaren, gedeckelten täglichen Projekt-Grundlast (`project_baseline_load`, Standard: +2 % pro offenem Item, Deckel 15 %) aus `CONF_CONTEXT_TODOS` über den gesamten 30-Tage-Horizont (inkl. `fast_empty_day`), inkl. Sensor-/Karten-Anzeige. | `anytype-ha-sync` |
| `project-task-breakdown` | KI-/Heuristik-Aufschlüsselung großer Projekt-To-dos in kleine 20–45-Minuten-Etappen (`category="project_step"`, `placement_mode="free_slot"`), automatische Einplanung von 1–2 Etappen auf ruhigen Tagen / Wochenenden (< 35 % Score) sowie bereinigende Synchronisation in `calendar.mental_load_2`. | `project-baseline-load` |

**Build order:** `anytype-ha-sync` → `project-baseline-load` → `project-task-breakdown`

## Modulgrenzen (Interfaces)
- `anytype-ha-sync` → `project-baseline-load`: Vertrag ist eine Standard-Home-Assistant-`todo.*`-Entität (`todo.hausbau_anytype`) mit Item-`summary`, optionaler `description` (Details aus Anytype) und `status` (`needs_action` / `completed`). `mental_load` spricht **nicht** direkt mit Anytype.
- `project-baseline-load` → `project-task-breakdown`: `DayLoadAnalysis` stellt `project_baseline_load`, `open_project_count` sowie die im Rebalancing ermittelten Tages-Restkapazitäten (`score < 35`, `events_count <= 1`) bereit, auf deren Basis `project-task-breakdown` freie Wochenend- und Nachmittags-Slots belegt.
