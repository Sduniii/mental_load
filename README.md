# Mental Load Calendar Analyzer for Home Assistant

Eine HACS-kompatible Home-Assistant-Integration (`mental_load`), die Kalendereinträge (z. B. Google Calendar) per KI bewertet, den **täglichen Mental Load (0–100%)** über einen 30-Tage-Horizont bestimmt, **unsichtbare Kopf-Aufgaben** (Vorbereitung, Packen, Fahrzeit, Nachbereitung, Projekt-Etappen) direkt in Home Assistant sichtbar macht und aktive Smart-Home-Reaktionen bei Überlastung auslöst.

---

## Features

- **Multi-LLM Support (inkl. ohne API-Key!):**
  - **Antigravity CLI (`agy`):** Nutzt deine bestehende Antigravity-Session (`gemini-3.8-flash-medium`, `gemini-4-argon-medium`, `claude-sonnet-5-5-medium`, `claude-opus-5-5-high`, `gpt-oss-120b-medium`) – **kein separater API-Key nötig**. Läuft lokal per `agy --print` oder bei HAOS über die mitgelieferte Bridge (`antigravity_bridge/` / `tools/agy_bridge_server.py`).
  - **Cloud & Lokal:** OpenAI (`gpt-4o-mini`, `gpt-4o`, …), Anthropic inkl. ephemeralem System-Prompt-Caching (`claude-3-5-haiku-latest`, `claude-3-7-sonnet-latest`, …), Mistral (`mistral-small-latest`, `mistral-large-latest`), Google Gemini (`gemini-2.0-flash`, …), Ollama / Custom OpenAI URLs (`http://localhost:11434/v1`), Home Assistant Conversation Agents sowie eine deterministische Offline-Heuristik als automatischer Fallback.
- **Sichtbar direkt im HA-Kalender (`calendar.mental_load`) & Externer Sync:**
  - Ganztages-Badge pro Tag (`🟢`, `🟡`, `🟠`, `🔴` inkl. Score, Begründung und Aufgabenanzahl).
  - Zeitlich platzierte Blöcke (`🧠 Vorbereitung`, `🚗 Fahrt & Puffer`, `🎒 Einpacken`, `📝 Nachbereitung`, `🔧 Etappe`) inklusive abhakbarer Checklisten.
  - Deine **Quell-Kalender bleiben strikt Read-Only** und werden niemals verändert.
  - Optionaler **selbstheilender Sync** in einen dedizierten externen Ziel-Kalender (gleicht live gegen `calendar.get_events` ab, entfernt veraltete/doppelte Einträge und erstellt fehlende Tage automatisch nach) sowie ein **iOS/CalDAV-Kalenderabo** über `/api/mental_load/calendar.ics`.
- **30-Tage-Horizont, Vorlauf-Planung & Live-Entlastung:**
  - Große Termine (Geburtstage, Feiern, Reisen, Einschulung, Vorträge) werden **7–14 Tage vorher** auf ruhige Tage vorgezogen; Besorgungen/Packen wandern per Load-Balancing automatisch auf ruhigere Vortage.
  - Abgehakte Vorbereitungen in `todo.mental_load_aufgaben` oder in der Dashboard-Card reduzieren den Mental Load am Ziel-Termin sofort in Echtzeit.
- **Kinder- & Stundenplan-Kalender + Sonntags-Turnbeutel-Bündelung:**
  - Zusätzliche Kinder-/Stundenplan-Kalender lassen sich rein lesend einbinden (`[Stundenplan/Kind]`). Schulstunden erzeugen keinen elterlichen Terminstress.
  - **Kinder-Profile:** Name und Geburtsdatum (`TT.MM.JJJJ` oder `YYYY-MM-DD`) berechnen Alter (inkl. Monate unter 2 Jahren), Entwicklungsstufe und 14-Tage-Geburtstagserinnerungen automatisch.
  - **Automatische Sonntags-Bündelung:** Regulärer Schulsport (`Sport`/`Turnen`) erzeugt werktags keine täglichen Turnbeutel-Packaufgaben mehr, sondern einmalig **sonntags (`17:30–17:45`)** eine gebündelte Aufgabe *„Turnbeutel für die ganze Woche packen“* (sofern am Sonntag nicht ohnehin schon ein entsprechender Kalendereintrag existiert). Besondere Events wie `Schwimmen` oder `Ausflug` behalten ihre Vortags-Vorbereitung.
- **Kalender-Steuer-Tags (`#no-prep`):**
  - Mit **`#no-prep`**, **`[ML:no-prep]`**, **`#noprep`** oder **`#kein-packen`** im Titel oder in der Beschreibung eines Kalendereintrags werden Vorbereitungs-, Pack- und Fahrt-Aufgaben für diesen Termin sowohl im KI-Prompt als auch in der lokalen Heuristik zuverlässig unterdrückt.
- **Projekt-Grundlast, Prioritäten & Sonntagsregel:**
  - Offene Dauerprojekte (`todo.*`, optional mit Anytype-Sync-Worker unter `services/anytype_ha_sync/`) erzeugen eine gedeckelte tägliche Grundlast (`+1%` Niedrig, `+2%` Mittel/Normal, `+3%` Hoch; standardmäßig bei `15%` gedeckelt) und werden nach Priorität (`Hoch` > `In Progress` > `Normal` > `Niedrig`) als 20–45-Min.-Etappen auf ruhige Feierabende und Wochenenden geplant – sonntags strikt ohne laute Arbeiten oder Behörden-/Laden-Erledigungen.
- **Alle 5 gewichteten Belastungsfaktoren & Feedback-Gedächtnis:**
  1. Taktung, Back-to-Back-Termine & Kontextwechsel
  2. Soziale Batterie & persönliches Profil
  3. Ortswechsel & Mobilität
  4. Live-HA-Kontext (`todo.*`-Listen, Schlaf-/Energie-Sensoren, `weather.*`)
  5. Gelerntes Feedback-Gedächtnis (`mental_load.submit_feedback`)
- **Performance-, Startup- & Token-Optimierung (`v1.9.x`):**
  - **Stabile Cache-Fingerprints:** Wetter und Energie-Sensoren invalidieren nur heute/morgen (`offset <= 1`) mit quantisierten Stufen; Zukunftstage sparen überflüssige Re-Runs.
  - **0-Token Fast-Path** für leere Tage und **Multi-Day-Batching** (`4` Tage pro Batch bei `antigravity_cli`, `7` Tage bei Cloud-APIs, `120s` Batch-/Breakdown-Timeout).
  - **Non-Blocking Startup & Boot-Cache-Schutz:** Seeded beim HA-Start sofort aus dem gespeicherten Cache, wartet mit dem frischen Abruf auf `EVENT_HOMEASSISTANT_STARTED` und überschreibt gecachte Werte bei temporären Kalender-Fehlern nicht mit `0%`.
- **To-Do-Sync, aktive Reaktionen & Custom Lovelace Card:**
  - Native abhakbare To-Do-Liste `todo.mental_load_aufgaben` sowie optionaler Push externer Aufgaben in beliebige `todo.*`-Listen (automatischer Fallback von `due_datetime` auf `due_date`).
  - Schwellenwert-Aktionen (`mental_load_threshold_exceeded` Event, `notify.*`, Szenen/Skripte/Helfer).
  - Custom Lovelace Card (`custom:mental-load-card`) mit 30-Tage-Übersicht, 5-Faktoren-Balken, Checklisten und 1-Klick-Feedback.

---

## Antigravity CLI App direkt in Home Assistant betreiben (empfohlen)

Dieses Repository ist gleichzeitig ein **Home Assistant App-Repository** (`antigravity_bridge/`). Damit läuft `agy` dauerhaft direkt auf deiner Home-Assistant-Instanz:

1. In Home Assistant unter **Einstellungen → Apps → App Store → ⋮ → Repositories** hinzufügen:
   `https://github.com/Sduniii/mental_load`
2. Die App **Antigravity CLI Bridge** installieren und starten.
3. In der App-Oberfläche (**Seitenleiste → Antigravity AI** bzw. Web-UI öffnen) entweder über **Login starten** anmelden oder die bestehende Session vom PC direkt pushen:
   ```bash
   python3 tools/agy_bridge_server.py --push-session http://<HA-IP>:11435
   ```
4. In der Integration **Mental Load Analyzer** lautet die Standard-Bridge-URL dann `http://127.0.0.1:11435/v1` (Standardwert, wenn das Feld leer bleibt).

### Alternative: Bridge auf externem Rechner starten

Falls die Bridge auf einem separaten Rechner laufen soll:

```bash
python3 tools/agy_bridge_server.py --host 0.0.0.0 --port 11435
# Oder dauerhaft als systemd-User-Service:
python3 tools/agy_bridge_server.py --install-systemd-user
```

In Home Assistant wählst du dann als Provider **Antigravity CLI (agy – kein API-Key nötig)** und trägst als Base-URL `http://<IP-deines-Rechners>:11435/v1` ein.

---

## Installation (HACS empfohlen)

### Per HACS
1. In Home Assistant unter **HACS → ⋮ → Benutzerdefinierte Repositories** hinzufügen:
   - Repository: `https://github.com/Sduniii/mental_load`
   - Kategorie: **Integration**
2. **Mental Load Analyzer** über HACS installieren und Home Assistant neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **Mental Load Analyzer** suchen und den 3-Schritte-Assistenten abschließen.
4. Im HA-Kalender links **Mental Load** aktivieren.

### Lovelace Card einbinden

Die Karte `custom:mental-load-card` wird automatisch registriert:

```yaml
type: custom:mental-load-card
title: Mental Load & Kopfarbeit
```

### Verfügbare Services
- `mental_load.refresh` – Aktualisiert den Prognose-Horizont (optional `clear_cache: true`).
- `mental_load.submit_feedback` – Speichert gefühlten Score (`much_lower` … `much_higher`) für zukünftige Prompts.
- `mental_load.push_hidden_tasks` – Überträgt offene Kopf-Aufgaben in eine Ziel-`todo.*`-Liste.
- `mental_load.toggle_checklist_item` – Hakt einzelne Checklisten-Schritte ab und berechnet Live-Entlastung.
- `mental_load.sync_external_calendar` – Synchronisiert Badges & Blöcke in einen dedizierten Ziel-Kalender.

---

## Tests ausführen

```bash
python3 -m pytest tests services/anytype_ha_sync/tests -v
```

Siehe [CHANGELOG.md](CHANGELOG.md) für alle Änderungen pro Version.
