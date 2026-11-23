# Mental Load Calendar Analyzer

Analysiere deinen Home-Assistant-Kalender (z. B. Google Calendar) mit dem KI-Modell deiner Wahl, berechne den täglichen **Mental Load (0–100%)**, mache **unsichtbare Kopf-Aufgaben** direkt in `calendar.mental_load` sichtbar und löse aktive Smart-Home-Reaktionen aus.

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
