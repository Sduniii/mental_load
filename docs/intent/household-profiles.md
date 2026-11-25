# Confirmed Statement of Intent: Flexible Household & Person Profiles (`mental_load`)

**Date:** 2026-11-25  
**Status:** Confirmed by user via `/agent-skills:interview-me`

## Statement of Intent
- **Outcome:** Statt starrer Einzel-Dropdowns (`sport_bag_mode`) und zeilenweiser Kinder-Textfelder gibt es in `mental_load` ein dynamisches Personen-Menü (*Erwachsene & Kinder hinzufügen / bearbeiten / entfernen*) mit eigener Freitext-Textbox pro Person, deren Alltagsregeln komplett von der KI ausgewertet werden.
- **User:** Haushalte jeder Größe — von Alleinerziehenden (`1` Erwachsener) bis zu Familien mit `2+` Erwachsenen und `0..N` Kindern unterschiedlicher Schulen und Alter.
- **Why now:** Hartcodierte Einzelfall-Regeln wie der Turnbeutel-Modus passen nicht zu unterschiedlichen Schulen, Altern und individuellen Alltagsroutinen (z. B. *Turnbeutel sonntags*, *Essen morgens zubereiten*, Aufgabenverteilung zwischen zwei Erwachsenen).
- **Success:**
  - Pro Erwachsenem gibt es **Name**, **optional zugeordnete Kalender** und eine **große Freitext-Textbox**.
  - Pro Kind gibt es **Name**, **Geburtsdatum** (für tagesgenaue Alters- & Geburtstagsberechnung), **optional zugeordnete Kalender** und eine **große Freitext-Textbox**.
  - Alle Personen fließen strukturiert in den KI-Prompt für **einen gemeinsamen Haushalts-Mental-Load-Score** ein.
  - Kalender-Unterdrückungs-Tags (`#no-prep`, `[ML:no-prep]`, `#noprep`, `#kein-packen`) bleiben für Einzeltermine erhalten.
  - Bestehende Profile werden rückwärtskompatibel übernommen.
- **Constraint:** Keine neuen Einzelfall-Dropdowns oder hartcodierten Turnbeutel-Sonderregeln im Code; Release wie gewohnt über Git/HACS (kein manueller Proxmox-Transfer).
- **Out of scope:**
  - Getrennte Mental-Load-Sensoren oder Dashboards pro Erwachsenem.
  - Hartcodierte Sonntags-Turnbeutel-Bündelung im Code (`_apply_weekly_sport_bag_bundling()`).
  - `#weekly-bag`-Spezialtag und `sport_bag_mode`-Dropdown.

## Key Decisions Locked During Interview
1. **Haushaltsmodell:** Ein gemeinsamer Haushalts-Score; mehrere Erwachsene helfen der KI realistisch bei der Einschätzung, wie Termine und Aufgaben auf die Schultern verteilt sind.
2. **UI-Verwaltung:** Dynamisches Personen-Menü (*hinzufügen / bearbeiten / entfernen*) statt starrer Anzahl-Eingabe oder Zeilen-Syntax; Alleinerziehende bleiben bei nur `1` Erwachsenem ohne Zusatz-Ballast.
3. **Felder je Person:** Name + (bei Kindern) Geburtsdatum + optional zugeordnete Kalender + große Freitext-Textbox (`multiline: true`).
4. **Regel-Auswertung:** Komplett durch die KI anhand der Personen-Textboxen (plus deterministisches `#no-prep` / `[ML:no-prep]` für Einzeltermine).
