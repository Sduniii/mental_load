# Mental Load Calendar Analyzer

Analysiere deinen Home-Assistant-Kalender (z. B. Google Calendar) mit dem KI-Modell deiner Wahl, berechne den täglichen **Mental Load (0–100%)**, mache **unsichtbare Kopf-Aufgaben** direkt in `calendar.mental_load` sichtbar und löse aktive Smart-Home-Reaktionen aus.

## Neu in v1.9.x

- ⚡ **75–90% weniger Tokens & Multi-Day-Batching:** Stabile Cache-Fingerprints, 0-Token Fast-Path für leere Tage, gebündelte Batch-Analyse (`4` Tage bei `antigravity_cli`, `7` Tage bei Cloud-APIs) sowie Anthropic System-Prompt-Caching.
- 🚀 **Non-Blocking Startup & Boot-Cache-Schutz:** Sofortiger Start aus dem gespeicherten Cache, frischer Abruf erst nach `EVENT_HOMEASSISTANT_STARTED`, `120s`-Timeout für Batches/Projekt-Breakdown und automatischer To-Do-Fallback (`due_datetime` → `due_date`).
- 🔁 **Selbstheilender Kalender-Sync (`calendar.mental_load_2`):** Gleicht gegen tatsächlich vorhandene Kalender-Einträge ab und stellt fehlende oder geänderte Tages-Badges automatisch wieder her.
- 🎒 **Konfigurierbarer Turnbeutel-Modus (`sport_bag_mode`) & Kalender-Tags:** Wähle zwischen `Vor jedem Sporttag (Standard)`, `Einmalig sonntags` (`17:30`) oder `Aus`; zusätzlich steuerbar pro Serie mit `#weekly-bag` / `#sonntag-packen` sowie `#no-prep` / `#kein-packen` im Termin.

## Neu in v1.8.0

- 🎒 **Kinder- & Stundenplan-Kalender:** Zusätzliche Kalender (z. B. Schul-Stundenplan) rein lesend anbinden — Schulstunden erzeugen keinen eigenen Terminstress, aber Sport-/Schwimm-/Ausflugs-Checklisten und Logistik werden automatisch berücksichtigt.
- 🎂 **Kinder-Profile mit Geburtstag:** Alter (inkl. Monate bei Babys/Kleinkindern), Betreuungsstufe und nahende Geburtstage werden für die KI automatisch tagesaktuell berechnet.

## Neu in v1.7.0

- 🚦 **Prioritäten aus Anytype (`Priorität`, `In Progress`, Fälligkeit):** `Hoch` (+3%), `Mittel`/`Normal` (+2%) und `Niedrig` (+1%) gewichten die gedeckelte Projekt-Grundlast (max. `15%`) und steuern die Reihenfolge der Etappenplanung — wichtige und aktive Projekte landen automatisch in den ersten freien Slots (unter Beibehaltung der Sonntagsregel).

## Neu in v1.6.0

- 🧱 **Gedeckelte Projekt-Grundlast:** Offene Dauerprojekte (`todo.hausbau_anytype` o. ä.) erzeugen jeden Tag eine konfigurierbare Grundlast (`+2%` pro offenem Punkt, gedeckelt auf max. `15%`) — auch an terminfreien Tagen.
- 🔧 **Projekt-Etappen auf ruhige Tage & Sonntags-Regel:** Große To-Dos werden in 20–45-Min.-Schritte auf ruhige Werktags-Feierabende (`17:15`) und Wochenenden (max. `2/Tag`) verteilt — sonntags ausschließlich ruhige Planungs-/Indoor-Aufgaben ohne geöffnete Läden, TÜV, Telefonate oder Lärm.
- 🧹 **Auto-Bereinigung im Google-Kalender:** Veraltete Mental-Load-Einträge in `calendar.mental_load_2` werden beim Sync automatisch gelöscht.

## Neu in v1.5.0

- 📅 **30-Tage-Horizont & 7–14 Tage Vorlauf für große Termine:** Geburtstage, Geschenke, Reisen, Einschulung oder Feiern werden 7–14 Tage vorher auf einen ruhigen Tag vorgezogen; leere Zukunftstage sparen sich überflüssige KI-Aufrufe.
- 📱 **Scrollbare 30-Tage-Leiste in der Dashboard-Card** mit Aufgaben-Punkten pro Tag.

## Neu in v1.4.0

- 📆 **Vorlauf-Planung & Load-Balancing:** Besorgungen (Bargeld, Geschenke, Dokumente) und Vorabend-Packen werden automatisch auf ruhige Vortage (z. B. deinen 3%-Tag) vorgezogen statt 20 Min vor Termin zu erscheinen.
- 🔗 **Bidirektionale Verknüpfung:** Aufgaben zeigen ihren Ziel-Termin (und umgekehrt) — inklusive Klick-Sprung zwischen Vorlauf-Tag und Termin-Tag in der Card.
- 📉 **Live-Entlastung:** Abgehakte Vorbereitungen senken den Mental Load am Termin-Tag sofort in Echtzeit.

## Neu in v1.3.0

- ✅ **Echte abhakbare To-Dos (`todo.mental_load_aufgaben`):** Alle Checklisten-Schritte sind direkt im HA To-Do-Tab und in der Dashboard-Card abhakbar.
- 📱 **iOS & Multi-Device Sync:** Sync in einen separaten Google Kalender **oder** direktes iOS-Kalenderabo über `/api/mental_load/calendar.ics`.
- 🎨 **Dynamische Multi-Device Custom Card:** Klickbare 7-Tage-Timeline, interaktive Checkboxen, Fortschrittsanzeige und Quick-Sync-Buttons.

## Highlights

- **Multi-LLM Support (inkl. ohne API-Key):** **Antigravity CLI (`agy`)** (direkt oder über `tools/agy_bridge_server.py`), OpenAI, Anthropic, Mistral, Google Gemini, Ollama / Custom OpenAI URLs, Home Assistant Conversation Agent sowie lokale Offline-Heuristik als Fallback.
- **Direkt im HA-Kalender (`calendar.mental_load`):**
  - Ganztages-Badges (`🟢`/`🟡`/`🟠`/`🔴` + Score & Kurzbegründung)
  - Zeitlich vor & nach Terminen platzierte Blöcke (`🧠 Vorbereitung`, `🚗 Fahrt & Puffer`, `🎒 Einpacken`, `📝 Nachbereitung`) mit Checklisten
  - Dein originaler Google Kalender bleibt **strikt Read-Only**.
- **Alle 5 Mental-Load-Faktoren:**
  1. Taktung, Back-to-Back-Termine & Kontextwechsel
  2. Soziale Batterie & persönliches Profil
  3. Ortswechsel & Mobilität
  4. Live-HA-Kontext (`todo.*`-Listen, Schlaf-/Energie-Sensoren, `weather.*`)
  5. Gelerntes Feedback-Gedächtnis (`mental_load.submit_feedback`)
- **To-Do-Sync & Aktive Reaktionen:** Deduplizierter Push in `todo.*` sowie automatische HA-Events, Benachrichtigungen und Szenen/Skripte bei Schwellenwert-Überschreitung.

## Custom Lovelace Card

Die Karte `custom:mental-load-card` wird automatisch registriert:

```yaml
type: custom:mental-load-card
title: Mental Load & Kopfarbeit
```
