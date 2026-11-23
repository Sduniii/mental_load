#!/usr/bin/env python3
"""OpenAI-compatible HTTP bridge + Ingress Web UI for Antigravity CLI (`agy`).

Runs either locally on a workstation or inside the Home Assistant
`antigravity_bridge` App (Add-on).

Usage:
    python3 tools/agy_bridge_server.py --host 0.0.0.0 --port 11435
    python3 tools/agy_bridge_server.py --install-systemd-user
    python3 tools/agy_bridge_server.py --export-session
    python3 tools/agy_bridge_server.py --push-session http://10.0.107.108:11435
"""
from __future__ import annotations

import argparse
import base64
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import termios
import threading
import time
import urllib.request
import uuid
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\r")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 11435
DEFAULT_MODEL = "gemini-3.8-flash-medium"

SESSION_FILES = [
    ".gemini/google_accounts.json",
    ".gemini/antigravity-cli/jetski_state.pbtxt",
    ".gemini/antigravity-cli/installation_id",
    ".gemini/antigravity-cli/settings.json",
    ".config/antigravity/credential-user-scope",
]


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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        timeout=20,
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
            "--disable-slash-commands",
            "--model",
            model,
            "--print",
            prompt,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
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


def export_session_bundle(home_dir: Path | None = None) -> str:
    """Export local Antigravity CLI session files into a base64 tar.gz bundle."""
    base = home_dir or Path.home()
    buf = io.BytesIO()
    added = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in SESSION_FILES:
            p = base / rel
            if p.is_file():
                tar.add(p, arcname=rel)
                added += 1
    if added == 0:
        raise FileNotFoundError(f"No session files found under {base}")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def import_session_bundle(bundle_b64: str, home_dir: Path | None = None) -> list[str]:
    """Import a base64 session tar.gz bundle safely into HOME."""
    base = (home_dir or Path.home()).resolve()
    raw = base64.b64decode(bundle_b64.strip())
    imported: list[str] = []
    allowed = set(SESSION_FILES)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            norm = member.name.removeprefix("./").lstrip("/")
            if norm not in allowed or not member.isfile():
                continue
            target = (base / norm).resolve()
            if not str(target).startswith(str(base)):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            target.write_bytes(src.read())
            os.chmod(target, 0o600)
            imported.append(norm)
    return imported


def extract_oauth_url(text: str) -> str:
    """Extract wrapped Google OAuth URL from agy TUI box output."""
    lines = text.splitlines()
    collecting = False
    chunks: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not collecting:
            idx = stripped.find("https://accounts.google.com/o/oauth2/auth")
            if idx != -1:
                collecting = True
                chunks.append(stripped[idx:])
        else:
            if not stripped or stripped.startswith("─") or stripped.startswith("Copy "):
                break
            chunks.append(stripped)
    return "".join(chunks)


class InteractiveLoginManager:
    """Manages an interactive PTY session of `agy` for browser-based OAuth login."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._output: str = ""

    def start(self, agy_bin: str) -> str:
        with self._lock:
            self.stop()
            master_fd, slave_fd = pty.openpty()
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 240, 0, 0))
            self._master_fd = master_fd
            self._output = ""
            env = {
                **os.environ,
                "TERM": "xterm-256color",
                "SSH_CONNECTION": "127.0.0.1 12345 127.0.0.1 22",
            }
            self._proc = subprocess.Popen(
                [agy_bin],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=tempfile.gettempdir(),
                close_fds=True,
            )
            os.close(slave_fd)
            threading.Thread(target=self._reader_loop, daemon=True).start()

        # Wait for login menu and automatically select "1. Google OAuth"
        time.sleep(2.0)
        with self._lock:
            if self._master_fd is not None and "Select login method" in self._output:
                os.write(self._master_fd, b"\r")
        time.sleep(2.2)
        return self.read_output()

    def _reader_loop(self) -> None:
        fd = self._master_fd
        if fd is None:
            return
        while True:
            try:
                rlist, _, _ = select.select([fd], [], [], 0.2)
                if fd in rlist:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    with self._lock:
                        self._output = (self._output + chunk.decode("utf-8", errors="replace"))[-16000:]
                elif self._proc and self._proc.poll() is not None:
                    break
            except OSError:
                break

    def send_input(self, text: str) -> str:
        with self._lock:
            if self._master_fd is not None:
                os.write(self._master_fd, (text.strip() + "\r").encode("utf-8"))
        time.sleep(2.0)
        return self.read_output()

    def read_output(self) -> str:
        with self._lock:
            return ANSI_RE.sub("", self._output)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        self._proc = None
        self._master_fd = None


LOGIN_MANAGER = InteractiveLoginManager()

INGRESS_HTML = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Antigravity CLI Bridge</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
  .wrap { max-width: 860px; margin: 0 auto; display: grid; gap: 16px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px; }
  h1, h2 { margin-top: 0; }
  pre { background: #020617; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; max-height: 260px; font-size: 12px; }
  textarea, input { width: 100%; box-sizing: border-box; background: #0f172a; color: #f8fafc; border: 1px solid #475569; border-radius: 8px; padding: 10px; margin: 8px 0; }
  button, a.btn { display: inline-block; text-decoration: none; background: #3b82f6; color: #fff; border: 0; border-radius: 8px; padding: 9px 14px; cursor: pointer; font-weight: 600; margin-right: 8px; font-size: 14px; }
  a.btn-green { background: #16a34a; }
  button.sec { background: #475569; }
  .ok { color: #4ade80; }
  .muted { color: #94a3b8; font-size: 0.9rem; }
  #oauth-box { margin: 12px 0; padding: 12px; background: #052e16; border: 1px solid #16a34a; border-radius: 8px; display: none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>🧠 Antigravity CLI Bridge (Home Assistant App)</h1>
    <div id="status" class="muted">Lade Status...</div>
    <p class="muted">Bridge-URL für <strong>Mental Load Analyzer</strong>: <code>http://127.0.0.1:11435/v1</code></p>
    <button onclick="loadStatus()">↻ Status prüfen</button>
    <button onclick="testPrompt()">⚡ Live-Test ("BEREIT")</button>
  </div>

  <div class="card">
    <h2>Anmeldung bei Google Antigravity (einmalig)</h2>
    <p class="muted">1. Klicke auf <strong>Login starten</strong> → 2. Öffne den angezeigten Google-Link → 3. Kopiere den Code zurück in das Eingabefeld.</p>
    <button onclick="startLogin()">🔑 Login starten</button>
    <button class="sec" onclick="pollLogin()">🔄 Aktualisieren</button>
    <button class="sec" onclick="stopLogin()">⏹ Beenden</button>

    <div id="oauth-box">
      <a id="oauth-link" class="btn btn-green" href="#" target="_blank" rel="noopener noreferrer">🔗 Google-Anmeldelink im Browser öffnen</a>
      <div class="muted" style="margin-top:6px;">Danach den angezeigten Autorisierungscode hier einfügen:</div>
    </div>

    <input id="pty-in" placeholder="Authorization Code hier einfügen..." />
    <button onclick="sendLoginInput()">✅ Code absenden</button>
    <pre id="pty-out">(Noch nicht gestartet)</pre>
  </div>

  <div class="card">
    <h2>Alternative: Session-Bundle importieren</h2>
    <textarea id="bundle" rows="3" placeholder="Base64 Session-Bundle hier einfügen..."></textarea>
    <button onclick="importBundle()">📥 Importieren</button>
    <div id="import-res" class="muted"></div>
  </div>
</div>
<script>
let pollTimer = null;
function applyLoginData(d) {
  document.getElementById('pty-out').textContent = d.output || '';
  const box = document.getElementById('oauth-box');
  const link = document.getElementById('oauth-link');
  if (d.oauth_url) {
    link.href = d.oauth_url;
    box.style.display = 'block';
  }
}
async function loadStatus() {
  const r = await fetch('api/status');
  const d = await r.json();
  document.getElementById('status').innerHTML =
    `<span class="ok">● Aktiv</span> | Binary: <code>${d.agy_bin}</code> | Modelle: ${d.models.slice(0,5).join(', ')} (${d.models.length} gesamt)`;
}
async function testPrompt() {
  document.getElementById('status').textContent = 'Teste agy --print...';
  const r = await fetch('api/test', { method: 'POST' });
  const d = await r.json();
  document.getElementById('status').innerHTML = d.ok
    ? `<span class="ok">✅ Test erfolgreich:</span> <code>${d.reply}</code>`
    : `❌ Fehler: ${d.error}`;
}
async function startLogin() {
  document.getElementById('pty-out').textContent = 'Starte agy Google OAuth...';
  const r = await fetch('api/login/start', { method: 'POST' });
  applyLoginData(await r.json());
  if (!pollTimer) pollTimer = setInterval(pollLogin, 2000);
}
async function pollLogin() {
  const r = await fetch('api/login/output');
  applyLoginData(await r.json());
}
async function sendLoginInput() {
  const val = document.getElementById('pty-in').value;
  const r = await fetch('api/login/input', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ text: val })
  });
  applyLoginData(await r.json());
  document.getElementById('pty-in').value = '';
  setTimeout(loadStatus, 1500);
}
async function stopLogin() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  await fetch('api/login/stop', { method: 'POST' });
  document.getElementById('pty-out').textContent += '\\n[Beendet]';
}
async function importBundle() {
  const bundle = document.getElementById('bundle').value;
  const r = await fetch('api/import-session', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ bundle })
  });
  const d = await r.json();
  document.getElementById('import-res').textContent = d.ok
    ? `✅ Importiert: ${d.imported.join(', ')}`
    : `❌ Fehler: ${d.error}`;
}
loadStatus();
</script>
</body>
</html>
"""


class AgyBridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler exposing OpenAI-compatible endpoints & Ingress UI."""

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

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _check_auth(self) -> bool:
        if not self.auth_token:
            return True
        # Trust HA Ingress requests from Supervisor proxy (172.30.32.2)
        if self.client_address[0] == "172.30.32.2":
            return True
        header = self.headers.get("Authorization", "")
        if header == f"Bearer {self.auth_token}":
            return True
        self._send_json(401, {"error": {"message": "Unauthorized"}})
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_html(INGRESS_HTML)
            return
        if path == "/health":
            self._send_json(200, {"status": "ok", "provider": "antigravity_cli", "agy_bin": self.agy_bin})
            return
        if path == "/api/status":
            try:
                models = list_agy_models(self.agy_bin)
            except Exception:  # noqa: BLE001
                models = [self.default_model]
            self._send_json(200, {"agy_bin": self.agy_bin, "models": models})
            return
        if path == "/api/login/output":
            out = LOGIN_MANAGER.read_output()
            self._send_json(200, {"output": out, "oauth_url": extract_oauth_url(out)})
            return
        if path in ("/v1/models", "/models"):
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
        path = self.path.split("?")[0]
        if path == "/api/test":
            try:
                reply = run_agy_prompt(self.agy_bin, self.default_model, "Antworte nur mit: BEREIT", timeout=45)
                self._send_json(200, {"ok": True, "reply": reply})
            except Exception as err:  # noqa: BLE001
                self._send_json(200, {"ok": False, "error": str(err)})
            return
        if path == "/api/login/start":
            out = LOGIN_MANAGER.start(self.agy_bin)
            self._send_json(200, {"output": out, "oauth_url": extract_oauth_url(out)})
            return
        if path == "/api/login/input":
            body = self._read_json_body()
            out = LOGIN_MANAGER.send_input(str(body.get("text", "")))
            self._send_json(200, {"output": out, "oauth_url": extract_oauth_url(out)})
            return
        if path == "/api/login/stop":
            LOGIN_MANAGER.stop()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/import-session":
            if not self._check_auth():
                return
            try:
                body = self._read_json_body()
                imported = import_session_bundle(str(body.get("bundle", "")))
                self._send_json(200, {"ok": True, "imported": imported})
            except Exception as err:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(err)})
            return

        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": {"message": f"Not found: {self.path}"}})
            return
        if not self._check_auth():
            return

        try:
            body = self._read_json_body()
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
    parser.add_argument("--export-session", action="store_true")
    parser.add_argument("--push-session", default="", help="Push local session bundle to bridge URL, e.g. http://10.0.107.108:11435")
    args = parser.parse_args()

    if args.export_session:
        print(export_session_bundle())
        return

    if args.push_session:
        bundle = export_session_bundle()
        url = args.push_session.rstrip("/") + "/api/import-session"
        headers = {"Content-Type": "application/json"}
        if args.token:
            headers["Authorization"] = f"Bearer {args.token}"
        req = urllib.request.Request(
            url,
            data=json.dumps({"bundle": bundle}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(resp.read().decode("utf-8"))
        return

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
