# Spec: `project-baseline-load`

**Module ID:** `project-baseline-load`  
**Capability Map:** [CAPABILITY-MAP.md](./CAPABILITY-MAP.md)  
**Depends on:** `anytype-ha-sync`  

## Objective
Erweiterung der Home-Assistant-Integration `mental_load`, sodass offene, unterminierte Aufgaben und Projekte aus konfigurierten `CONF_CONTEXT_TODOS`-Listen (z. B. `todo.hausbau_anytype`, optional `todo.garten` / `todo.zuhause`) täglich eine **gedeckelte Hintergrund-Grundlast** (`project_baseline_load`) in die Mental-Load-Bewertung einbringen. So spiegeln selbst terminfreie Tage den realen mentalen Hintergrunddruck wider (z. B. `+12%` bis max. `+15%` statt irreführender `0%`), bis die offenen Punkte abgehakt sind.

## Tech Stack
- **Integration:** Home Assistant Custom Component `custom_components/mental_load` (Python `3.12+`)
- **Frontend:** Vanilla Web Component `frontend/mental-load-card.js` (ohne Build-Schritt)
- **Tests:** `pytest` mit bestehender Test-Suite unter `tests/`

## Commands
```bash
python3 -m pytest tests -v
python3 -m py_compile custom_components/mental_load/*.py
node --check custom_components/mental_load/frontend/mental-load-card.js
```

## Project Structure
```text
custom_components/mental_load/
├── const.py              # CONF_PROJECT_BASELINE_PER_ITEM, CONF_PROJECT_BASELINE_CAP, Defaults
├── models.py             # DayLoadAnalysis.project_baseline_load, open_project_count
├── config_flow.py        # Options-UI für Grundlast pro Item (%) und Deckel (%)
├── coordinator.py        # Berechnung & Anwendung der Grundlast auf alle Tage inkl. fast_empty_day
├── prompt_builder.py     # Prompt-Hinweis + badge_summary/reasoning für Grundrauschen
├── sensor.py             # Exponieren von project_baseline_load & open_project_count in Attributen
└── frontend/
    └── mental-load-card.js  # Anzeige der täglichen Projekt-Grundlast in Kachel & Detailansicht
```

## Code Style
Einheitliche, rein deterministische Berechnung der gedeckelten Grundlast aus `ha_context`:

```python
def compute_project_baseline(open_todos: list[str], config: dict[str, Any]) -> tuple[int, int]:
    """Return (capped_baseline_points, open_item_count) for ongoing unscheduled projects."""
    count = len(open_todos)
    per_item = max(0.0, float(config.get(CONF_PROJECT_BASELINE_PER_ITEM, DEFAULT_PROJECT_BASELINE_PER_ITEM)))
    cap = max(0, min(40, int(config.get(CONF_PROJECT_BASELINE_CAP, DEFAULT_PROJECT_BASELINE_CAP))))
    baseline = min(cap, round(count * per_item))
    return baseline, count
```

- Standardwerte: `DEFAULT_PROJECT_BASELINE_PER_ITEM = 2.0`, `DEFAULT_PROJECT_BASELINE_CAP = 15`.
- Integration in `coordinator._rebalance_and_score_horizon`: `project_baseline_load` wird zu `day.base_score` addiert (geklammert auf `0..100`) und im Fingerprint (`_compute_day_fingerprint`) über `open_todos` sowie die beiden Baseline-Optionen sauber berücksichtigt.
- Für `fast_empty_day` gilt: `reasoning` und `top_stressor` benennen bei `project_baseline_load > 0` transparent das offene Hintergrund-Grundrauschen (z. B. *„Hintergrund-Grundlast: 14 offene Projekt-Aufgaben (+15%)“*).

## Testing Strategy
- **Unit Tests:**
  - `compute_project_baseline` mit `0`, `3`, `8`, `30` offenen To-dos (verifiziert Deckel bei `15%`).
  - `fast_empty_day` zeigt bei `0` Terminen und `10` offenen Projekt-To-dos `score == 15` und `project_baseline_load == 15`.
  - Abhaken/Entfernen aller Projekt-To-dos senkt die Grundlast wieder auf `0`.
  - Fingerprint-Invalidierung bei Änderung von `open_todos` oder Baseline-Optionen.

## Boundaries
- **Always:** `base_score` inklusive `project_baseline_load` auf `0..100` clampen; `todo.*`-Listen bleiben für den Nutzer die einzige Wahrheit für den Projektbestand (`open_todos` vollständig zählen, auch wenn > 25 Items in `todo.hausbau_anytype` liegen — für die KI-Prompt-Vorschau sinnvoll kürzen, für die Zählung exakt ermitteln).
- **Ask first:** Änderung des Standard-Deckels (`15%`) oder Differenzierung nach Prioritäts-Tags (z. B. dringend vs. irgendwann).
- **Never:** Grundlast darf den Score niemals ohne Deckel auf 100 % treiben; bestehende `todo.*`-Listen (wie `todo.zuhause`) dürfen nicht automatisch gelöscht oder umgeschrieben werden.

## Success Criteria
1. Konfigurierte `CONF_CONTEXT_TODOS`-Listen (inkl. `todo.hausbau_anytype`) fließen mit +2 % pro offenem Task (gedeckt auf max. 15 %) in **jeden** Tag des 30-Tage-Horizonts ein.
2. Freie Tage zeigen statt `0%` bei offenen Projekten z. B. `15%` mit klarer Aufschlüsselung `project_baseline_load` und `open_project_count`.
3. Über die Integration-Optionen in Home Assistant lassen sich `% pro To-do` und `Max. Grundlast-Deckel (%)` anpassen.
4. `custom:mental-load-card` zeigt das laufende Projekt-Grundrauschen verständlich an (damit der Nutzer sofort sieht, wie viel vom Tages-Score Termine vs. Hintergrundprojekte sind).

## Resolved Decisions
- **Aktivierte To-do-Liste:** Ausschließlich **`todo.hausbau_anytype`** wird in `CONF_CONTEXT_TODOS` konfiguriert. Die Bring!-Listen (`todo.garten`, `todo.baumarkt`, `todo.zuhause`) werden ausdrücklich **nicht** in die Projekt-Grundlast einbezogen.
