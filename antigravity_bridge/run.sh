#!/usr/bin/env bash
set -euo pipefail

export HOME="/data"
export PATH="/data/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p /data/.local/bin /data/.gemini/antigravity-cli

if [ ! -x /data/.local/bin/agy ]; then
  echo "[antigravity_bridge] Installing Antigravity CLI (agy) into /data/.local/bin..."
  curl -fsSL https://antigravity.google/cli/install.sh | bash
fi

DEFAULT_MODEL="gemini-3.8-flash-medium"
AUTH_TOKEN=""

if [ -f /data/options.json ]; then
  DEFAULT_MODEL="$(jq -r '.default_model // "gemini-3.8-flash-medium"' /data/options.json)"
  AUTH_TOKEN="$(jq -r '.auth_token // ""' /data/options.json)"
fi

echo "[antigravity_bridge] Starting Antigravity CLI Bridge on 0.0.0.0:11435 (default model: ${DEFAULT_MODEL})"
if [ -n "$AUTH_TOKEN" ]; then
  exec python3 /app/agy_bridge_server.py --host 0.0.0.0 --port 11435 --model "$DEFAULT_MODEL" --token "$AUTH_TOKEN" --agy-bin /data/.local/bin/agy
else
  exec python3 /app/agy_bridge_server.py --host 0.0.0.0 --port 11435 --model "$DEFAULT_MODEL" --agy-bin /data/.local/bin/agy
fi
