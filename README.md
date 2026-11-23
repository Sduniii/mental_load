# Mental Load Calendar Analyzer for Home Assistant

Eine HACS-kompatible Home-Assistant-Integration (`mental_load`), die Kalendereinträge (z. B. Google Calendar) per KI bewertet, den **täglichen Mental Load (0–100%)** bestimmt, **unsichtbare Kopf-Aufgaben** (Vorbereitung, Packen, Fahrzeit, Nachbereitung) direkt in einer eigenen `calendar.mental_load`-Entität sichtbar macht und aktive Smart-Home-Reaktionen bei Überlastung auslöst.

## Features

- **Multi-LLM Support (inkl. ohne API-Key!):**
  - **Antigravity CLI (`agy`)**: Nutzt deine bestehende Antigravity-Session (`gemini-3.8-flash-medium`, `gemini-4-argon-medium`, `claude-sonnet-5-5-medium`, `claude-opus-5-5-high`, `gpt-oss-120b-medium`) – **kein separater API-Key nötig!** Läuft entweder direkt lokal über `agy --print` oder bei HAOS-VMs über die mitgelieferte Bridge `tools/agy_bridge_server.py`.
  - **Cloud & Lokal:** OpenAI (`gpt-4o-mini`, `gpt-4o`, ...), Anthropic (`claude-3-5-haiku-latest`, `claude-3-7-sonnet-latest`, ...), Mistral (`mistral-small-latest`, `mistral-large-latest`), Google Gemini (`gemini-2.0-flash`, ...), Ollama / Custom OpenAI-URLs (`http://localhost:11434/v1`), Home Assistant Conversation Agents sowie eine Offline-Heuristik als Fallback.

## Antigravity CLI Bridge starten (für HAOS / Docker)

Wenn dein Home Assistant auf einer separaten VM (z. B. Home Assistant OS) läuft, starte die mitgelieferte Bridge auf dem Rechner, auf dem `agy` eingeloggt ist:

```bash
python3 tools/agy_bridge_server.py --host 0.0.0.0 --port 11435
# Oder dauerhaft als systemd-User-Service:
python3 tools/agy_bridge_server.py --install-systemd-user
```

In Home Assistant wählst du dann als Provider **Antigravity CLI (agy – kein API-Key nötig)** und trägst als Base-URL `http://<IP-deines-Rechners>:11435/v1` ein.
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
