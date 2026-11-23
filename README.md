# Mental Load Calendar Analyzer for Home Assistant

Eine HACS-kompatible Home-Assistant-Integration (`mental_load`), die Kalendereinträge (z. B. Google Calendar) per KI bewertet, den **täglichen Mental Load (0–100%)** bestimmt, **unsichtbare Kopf-Aufgaben** (Vorbereitung, Packen, Fahrzeit, Nachbereitung) direkt in einer eigenen `calendar.mental_load`-Entität sichtbar macht und aktive Smart-Home-Reaktionen bei Überlastung auslöst.

## Features

- **Multi-LLM Support:** OpenAI (`gpt-4o-mini`, `gpt-4o`, ...), Anthropic (`claude-3-5-haiku-latest`, `claude-3-7-sonnet-latest`, ...), Mistral (`mistral-small-latest`, `mistral-large-latest`), Google Gemini (`gemini-2.0-flash`, ...), Ollama / Custom OpenAI-URLs (`http://localhost:11434/v1`), Home Assistant Conversation Agents sowie eine Offline-Heuristik als Fallback.
- **Sichtbar direkt im HA-Kalender (`calendar.mental_load`):**
  - Ganztages-Badge pro Tag (`🟢 25%`, `🟡 52%`, `🟠 76%`, `🔴 91%`) inklusive 5-Faktoren-Analyse im Popup.
  - Zeitlich vor und nach deinen Terminen platzierte Blöcke für unsichtbare Aufgaben (`🧠 Vorbereitung`, `🚗 Fahrt & Puffer`, `🎒 Einpacken`, `📝 Nachbereitung`) inklusive Checkliste.
  - Dein originaler Google Kalender bleibt **strikt Read-Only** und wird niemals verändert.
- **Alle 5 Belastungsfaktoren (konfigurierbar & gewichtet):**
  1. Taktung, Back-to-Back-Termine & Kontextwechsel
  2. Soziale Batterie & persönliches Profil
  3. Ortswechsel & Mobilität
  4. Live-HA-Kontext (`todo.*`-Listen, Schlaf-/Energie-Sensoren, `weather.*`)
  5. Gelerntes Feedback-Gedächtnis (`mental_load.submit_feedback`)
- **To-Do-Sync & Aktive Reaktionen:**
  - Optionaler deduplizierter Push aller erkannten Kopf-Aufgaben in eine `todo.*`-Liste.
  - Schwellenwert-Aktionen (`mental_load_threshold_exceeded` Event, `notify.*`, Szenen/Skripte/Helfer).
- **Custom Lovelace Card (`custom:mental-load-card`):**
  - 7-Tage-Heatmap, 5-Faktoren-Balken, Checkliste der unsichtbaren Aufgaben und 1-Klick-Feedback.

## Installation

1. Ordner `custom_components/mental_load` nach `<config>/custom_components/mental_load` in Home Assistant kopieren (oder über HACS als Custom Repository einbinden).
2. Home Assistant neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **Mental Load Analyzer** suchen.
4. Im 3-Schritte-Assistenten Quell-Kalender, KI-Provider und Reaktionen konfigurieren.
5. Im HA-Kalender links das Häkchen bei **Mental Load** setzen.

## Tests ausführen

```bash
pytest tests -v
```
