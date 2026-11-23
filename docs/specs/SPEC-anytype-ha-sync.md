# Spec: `anytype-ha-sync`

**Module ID:** `anytype-ha-sync`  
**Capability Map:** [CAPABILITY-MAP.md](./CAPABILITY-MAP.md)  
**Depends on:** —  

## Objective
Bereitstellung eines ausfallsicheren 24/7-Sync-Dienstes auf dem Homelab-Server `homelab-server` (verwaltet über Komodo), der alle offenen Tasks aus dem selbstgehosteten Anytype-Space **„Hausbau“** (`space_test_id`, synchronisiert über `any-sync-bundle` / `anytype.example.internal`) in eine native Home-Assistant-To-do-Liste (**`todo.hausbau_anytype`**) spiegelt und abgehakte Tasks zurück nach Anytype überträgt. Dadurch stehen die Anytype-Aufgaben Home Assistant und `mental_load` rund um die Uhr zur Verfügung, unabhängig davon, ob ein Desktop-Client läuft.

## Tech Stack
- **Server / Orchestrierung:** Komodo auf `homelab-server`, Stack `anytype` (`ghcr.io/grishy/any-sync-bundle:minimal`, `mongo:4.4`, `redis`) unter `/opt/anytype`
- **Sync-Worker:** Headless `anytype-cli` (oder Anytype Local API Headless Daemon) + Python `3.12` Sync-Skript (`httpx` / `aiohttp`) als Docker-Container im Komodo-Stack (z. B. `anytype-ha-sync`)
- **Home-Assistant-Ziel:** Core-Integration `local_todo` (`todo.hausbau_anytype`), Zugriff über Home Assistant REST/WebSocket API (`/api/services/todo/*` mit Long-Lived Access Token)

## Commands
```bash
# Tests für die Sync-Logik (Mapping, Diff, Idempotenz, Mocked APIs)
python3 -m pytest tests/test_anytype_ha_sync.py -v

# Syntax- und Container-Build-Check
python3 -m py_compile services/anytype_ha_sync/sync.py
docker compose -f docker-compose.sync.yml config
```

## Project Structure
```text
services/anytype_ha_sync/
├── Dockerfile              # Minimaler Container (Python 3.12 + anytype-cli)
├── sync.py                 # Bidirektionale Sync-Engine (Anytype -> HA todo.*, HA completed -> Anytype)
└── tests/
    └── test_anytype_ha_sync.py
```
*(Deployment über Komodo auf `homelab-server` im/neben dem Stack `/opt/anytype`.)*

## Code Style
Deterministisches Mapping über stabile Anytype-Object-IDs im Beschreibungsfeld (`[ANYTYPE-<object_id>]`), sodass Umbenennungen keine Duplikate erzeugen:

```python
SYNC_TAG_PREFIX = "[ANYTYPE-"

def format_ha_description(object_id: str, details: str) -> str:
    """Embed stable Anytype object ID so updates and completions stay idempotent."""
    clean = details.strip()
    marker = f"{SYNC_TAG_PREFIX}{object_id}]"
    return f"{clean}\n\n{marker}".strip() if clean else marker
```

## Testing Strategy
- **Unit Tests (`pytest`):** Diff-Algorithmus (neu, aktualisiert, in Anytype erledigt, in HA abgehakt), Erhalt des `[ANYTYPE-<id>]`-Markers, Konfliktauflösung bei gleichzeitiger Änderung, Fehlerbehandlung bei Offline-Anytype/HA.
- **Integration Test (Live):** Manueller Lauf auf `homelab-server` gegen Space `space_test_id` und `todo.hausbau_anytype`; Abhaken eines Test-Tasks in HA und Verifikation in Anytype.

## Boundaries
- **Always:** Sync-Marker `[ANYTYPE-<object_id>]` in HA-Descriptions speichern; bei API-Timeouts sauber im nächsten Intervall (Standard: 5 Min.) erneut versuchen; Secrets (`ANYTYPE_MNEMONIC` / API-Key, `HA_TOKEN`) ausschließlich über `.env` / Komodo-Secrets verwalten.
- **Ask first:** Falls Anytype-Account-Phrase/API-Key für den Headless-Container neu verknüpft werden muss; falls Aufgaben außerhalb von Space `Hausbau` (`space_test_id`, z. B. `Training`) ebenfalls gespiegelt werden sollen.
- **Never:** Keine direkten Schreibzugriffe auf MongoDB/Redis des `any-sync-bundle` (Ende-zu-Ende-verschlüsselte CRDTs würden beschädigt); niemals Secrets in Git oder Logs ausgeben; keine unkontrollierte Löschung von Nutzer-To-dos ohne `[ANYTYPE-...]`-Marker.

## Success Criteria
1. Alle offenen Tasks aus Anytype Space `Hausbau` (`space_test_id`) erscheinen innerhalb von max. 5 Minuten in Home Assistant unter `todo.hausbau_anytype` inkl. Beschreibung/Kontext.
2. Erledigt der Nutzer einen Eintrag in Anytype oder in Home Assistant (`todo.hausbau_anytype`), wird der Status beim nächsten Sync-Zyklus in das jeweils andere System übernommen.
3. Ein Neustart von `homelab-server` oder Home Assistant erzeugt keinerlei Duplikate in `todo.hausbau_anytype`.

## Resolved Decisions
- **Synchronisierter Space:** Ausschließlich Space **„Hausbau“** (`space_test_id`) über `anytype.example.internal:33010` / `33020` (`networkId: YOUR_ANY_SYNC_NETWORK_ID`).
- **Auth-Verknüpfung auf `homelab-server`:** Headless-Login über `anytype-cli` (`ANYTYPE_ACCOUNT_KEY` / Recovery-Phrase in Komodo `.env` / Environment) mit benutzerdefinierter `network.yaml` für den Self-Hosted-`any-sync-bundle`-Server (`anytype.example.internal`).
- **Initial-Befüllung & Laufzeit-Sync:** Während des Deploys wird `todo.hausbau_anytype` (`local_todo`) sofort befüllt und der 24/7-Container auf `homelab-server` über Komodo scharfgeschaltet.
