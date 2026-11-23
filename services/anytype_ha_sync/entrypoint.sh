#!/bin/sh
set -eu

CONFIG_DIR="/root/.config/anytype"
DATA_DIR="/root/.anytype"
API_KEY_FILE="$DATA_DIR/.api_key"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"

if [ -n "${ANYTYPE_NETWORK_YAML:-}" ]; then
  printf '%s\n' "$ANYTYPE_NETWORK_YAML" > /app/network.yaml
fi

# One-time headless bot initialization if no credentials exist yet
if [ ! -f "$API_KEY_FILE" ] && [ -z "${ANYTYPE_API_KEY:-}" ]; then
  BOT_NAME="${ANYTYPE_BOT_NAME:-ha-mental-load-sync}"
  echo "[entrypoint] Initializing headless anytype bot account '$BOT_NAME'..."
  anytype auth create "$BOT_NAME" --network-config /app/network.yaml || true
  if [ -n "${ANYTYPE_INVITE_URL:-}" ]; then
    echo "[entrypoint] Joining space via invite URL..."
    anytype space join "$ANYTYPE_INVITE_URL" || true
  fi
  GENERATED_KEY="$(anytype auth apikey create "ha-sync" --all-spaces --read-write 2>/dev/null | grep -oE '[A-Za-z0-9._-]{24,}' | tail -n 1 || true)"
  if [ -n "$GENERATED_KEY" ]; then
    printf '%s' "$GENERATED_KEY" > "$API_KEY_FILE"
  fi
fi

if [ -z "${ANYTYPE_API_KEY:-}" ] && [ -f "$API_KEY_FILE" ]; then
  export ANYTYPE_API_KEY="$(cat "$API_KEY_FILE")"
fi

anytype serve &
ANYTYPE_PID=$!

# Wait for Anytype local API to become ready
i=0
while [ "$i" -lt 30 ]; do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:31012/v1/spaces', timeout=2)" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

python3 /app/sync.py &
SYNC_PID=$!

trap 'kill "$SYNC_PID" "$ANYTYPE_PID" 2>/dev/null || true' INT TERM
wait "$SYNC_PID"
