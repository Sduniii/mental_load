# Confirmed Statement of Intent: Anytype-Projekt-Sync & Tägliche Grundlast in Mental Load

**Datum:** 2026-11-23  
**Initiative:** `mental_load` v1.6.0 + Anytype HA Sync (`homelab-server`)  
**Status:** Bestätigt durch Benutzer

---

- **Outcome:**
  1. **24/7 Anytype-Sync auf `homelab-server` (Komodo):** Ein Headless-Sync-Service auf `homelab-server` spiegelt alle offenen Tasks aus dem Anytype-Space **„Hausbau“** (`<anytype-space-id>`, über `any-sync-bundle` / `anytype.example.internal`) automatisch in eine native Home-Assistant-To-do-Liste (**`todo.hausbau_anytype`**).
  2. **Gedeckelte tägliche Grundlast in `mental_load`:** Offene Projekte/Tasks aus den hinterlegten To-do-Listen (`todo.hausbau_anytype` u. a.) fließen jeden Tag als unterbewusstes Grundrauschen (+1–2 % pro offenem Task, **gedeckelt auf max. 15 %**) in den täglichen Mental-Load-Score ein, bis sie erledigt sind.
  3. **Große To-dos aufschlüsseln & auf freie Tage verteilen:** Die KI bricht große Baustellen (z. B. *OG: Fußbodenheizung*, *Dachbalken*, *Teich*, *Gartenarbeit*, *Termine vereinbaren*) in kleine, machbare Teil-Tasks (20–45 Min. mit Checkliste) herunter und plant davon gezielt 1–2 Etappen an **freien Tagen (Wochenenden oder ruhigen Nachmittagen)** im 30-Tage-Horizont ein.
- **User:** Der Haushalt / Nutzer im Alltag über die Lovelace-Karte (`custom:mental-load-card`), die Home-Assistant-To-do-Listen und den synchronisierten Google-/iOS-Kalender (`calendar.mental_load_2`).
- **Why now:** Kalenderfreie Tage werden aktuell mit `0–10 %` grün angezeigt, obwohl offene Hausbau-, Garten- und Terminplanungs-To-dos aus Anytype unterbewusst täglich mitlaufen und ohne aktive Einplanung auf ruhigen Tagen liegen bleiben.
- **Success:**
  1. `todo.hausbau_anytype` in Home Assistant enthält automatisch die offenen Tasks aus dem Anytype-Space „Hausbau“ (24/7 synchronisiert über `homelab-server`).
  2. Auch terminfreie Tage zeigen die reale, gedeckelte Projekt-Grundlast im Score und auf der Karte.
  3. An freien Wochenendtagen und ruhigen Nachmittagen erscheinen im Kalender und auf der Karte konkrete, heruntergebrochene Teil-Aufgaben der großen Hausbau-/Garten-To-dos.
- **Constraint:** Die tägliche Projekt-Grundlast ist gedeckelt (max. 15 %, damit offene Langzeitprojekte den Score nicht dauerhaft auf 100 % treiben); bereits volle Tage werden nicht mit Projekt-Tasks überladen; Quellkalender (`calendar.mut`) bleiben strikt schreibgeschützt.
- **Out of scope:** Kein Umweg über Google Tasks; keine mehrjährige Bauzeiten- oder Gantt-Projektplanung außerhalb des 30-Tage-Horizonts.
