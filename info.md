# Mental Load Calendar Analyzer

Analysiere deinen Home-Assistant-Kalender (z. B. Google Calendar) mit dem KI-Modell deiner Wahl, berechne den täglichen **Mental Load (0–100%)**, mache **unsichtbare Kopf-Aufgaben** direkt in `calendar.mental_load` sichtbar und löse aktive Smart-Home-Reaktionen aus.

## Highlights

- **Multi-LLM Support:** OpenAI, Anthropic, Mistral, Google Gemini, Ollama / Custom OpenAI URLs, Home Assistant Conversation Agent sowie lokale Offline-Heuristik als Fallback.
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
