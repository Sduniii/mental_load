#!/usr/bin/env python3
"""OpenAI-compatible HTTP bridge for Antigravity CLI (`agy`).

Allows Home Assistant (including HAOS VMs) to invoke your authenticated local
`agy` CLI without needing external OpenAI/Anthropic/Google API keys.

Usage:
    python3 tools/agy_bridge_server.py --host 0.0.0.0 --port 11435
    python3 tools/agy_bridge_server.py --install-systemd-user
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\r")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 11435
DEFAULT_MODEL = "gemini-3.8-flash-medium"


def resolve_agy_binary(explicit: str | None = None) -> str:
    """Find the `agy` CLI binary."""
    if explicit:
        return explicit
    found = shutil.which("agy")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "agy"
    if candidate.is_file():
        return str(candidate)
    raise FileNotFoundError("Could not find 'agy' CLI binary in PATH or ~/.local/bin/agy")


def list_agy_models(agy_bin: str) -> list[str]:
    """Query available models via `agy models`."""
    proc = subprocess.run(
        [agy_bin, "models"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    cleaned = ANSI_RE.sub("", proc.stdout)
    models: list[str] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line or line.startswith("Fetching"):
            continue
        model_id = line.split()[0]
        if model_id and model_id not in models:
            models.append(model_id)
    return models or [DEFAULT_MODEL]


def run_agy_prompt(agy_bin: str, model: str, prompt: str, timeout: int = 90) -> str:
    """Execute a single prompt non-interactively via `agy --print`."""
    proc = subprocess.run(
        [
            agy_bin,
            "--print",
            "--disable-slash-commands",
            "--model",
            model,
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"agy failed (exit {proc.returncode}): {err[:400]}")
    return proc.stdout.strip()


def format_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten OpenAI chat messages into a single prompt for `agy --print`."""
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content", ""))
        if role == "SYSTEM":
            parts.append(content)
        else:
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


class AgyBridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler exposing OpenAI-compatible endpoints backed by `agy`."""

    agy_bin: str = "agy"
    default_model: str = DEFAULT_MODEL
    auth_token: str = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(f"[agy-bridge] {self.address_string()} - {format % args}\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _check_auth(self) -> bool:
        if not self.auth_token:
            return True
        header = self.headers.get("Authorization", "")
        if header == f"Bearer {self.auth_token}":
            return True
        self._send_json(401, {"error": {"message": "Unauthorized"}})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "provider": "antigravity_cli", "agy_bin": self.agy_bin})
            return
        if self.path in ("/v1/models", "/models"):
            if not self._check_auth():
                return
            try:
                models = list_agy_models(self.agy_bin)
                self._send_json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": m, "object": "model", "owned_by": "antigravity-cli"}
                            for m in models
                        ],
                    },
                )
            except Exception as err:  # noqa: BLE001
                self._send_json(500, {"error": {"message": str(err)}})
            return
        self._send_json(404, {"error": {"message": f"Not found: {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": {"message": f"Not found: {self.path}"}})
            return
        if not self._check_auth():
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as err:  # noqa: BLE001
            self._send_json(400, {"error": {"message": f"Invalid JSON body: {err}"}})
            return

        model = str(body.get("model") or self.default_model)
        messages = body.get("messages") or []
        prompt = format_messages_to_prompt(messages)

        try:
            reply = run_agy_prompt(self.agy_bin, model, prompt)
        except Exception as err:  # noqa: BLE001
            self._send_json(500, {"error": {"message": str(err)}})
            return

        self._send_json(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
            },
        )


def install_systemd_user_service(script_path: Path, agy_bin: str, host: str, port: int) -> None:
    """Install and enable a user systemd unit for the Antigravity CLI bridge."""
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    unit_path = service_dir / "agy-bridge.service"
    unit_content = f"""[Unit]
Description=Antigravity CLI (agy) OpenAI-Compatible Bridge for Home Assistant
After=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} {script_path.resolve()} --host {host} --port {port} --agy-bin {agy_bin}
Restart=on-failure
RestartSec=5
Environment=PATH={os.path.dirname(agy_bin)}:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
"""
    unit_path.write_text(unit_content, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "agy-bridge.service"], check=False)
    print(f"Installed and started systemd user service: {unit_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity CLI (agy) HTTP Bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--agy-bin", default=None)
    parser.add_argument("--token", default="")
    parser.add_argument("--install-systemd-user", action="store_true")
    args = parser.parse_args()

    agy_bin = resolve_agy_binary(args.agy_bin)

    if args.install_systemd_user:
        install_systemd_user_service(Path(__file__), agy_bin, args.host, args.port)
        return

    AgyBridgeHandler.agy_bin = agy_bin
    AgyBridgeHandler.default_model = args.model
    AgyBridgeHandler.auth_token = args.token

    server = ThreadingHTTPServer((args.host, args.port), AgyBridgeHandler)
    print(f"Antigravity CLI Bridge listening on http://{args.host}:{args.port}/v1 (agy: {agy_bin})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Antigravity CLI Bridge.")
        server.server_close()


if __name__ == "__main__":
    main()
