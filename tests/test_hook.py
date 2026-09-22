"""Bracket the hook. It runs on every prompt in every session, so it is the least-watched
code in the plugin: correct when written, silently wrong after the first edit. Each arm
pins one rot mode. A stub HTTP server stands in for the daemon so the arms need nothing
running and can make the daemon disappear."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "src" / "agent_bus" / "cli.py"
TOKEN = "test-token"
MESSAGE = {"id": 7, "ts": "2026-09-22T15:00:00Z", "from": "antigravity@repo", "to": "claude@repo", "repo": "repo", "thread": "t", "status": "REQUEST", "verb": "review", "sha": "abc1234", "files": ["src/x.py"], "body": "Please look at x."}


class Stub:
    """Answers /api/inbox with `inbox` and records every /api/ack body."""

    def __init__(self):
        self.inbox = []
        self.acks = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self.reply(401, {"error": "bad token"})
                if self.path == "/api/inbox":
                    return self.reply(200, stub.inbox)
                if self.path == "/api/ack":
                    stub.acks.append(body)
                    return self.reply(200, {"acked": len(body["ids"])})
                self.reply(404, {"error": "no route"})

            def reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_hook(agent, url, token=TOKEN, stdin=b"", runner=None, home=None):
    cmd = (runner or [sys.executable]) + [str(CLI), "hook", "--agent", agent]
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BUS_")}
    env["AGENT_BUS_SERVER"] = url
    if token is not None:
        env["AGENT_BUS_TOKEN"] = token
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(cmd, input=stdin, capture_output=True, env=env, timeout=30)


def test_speaks_with_mail_and_acks_what_it_printed(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url)
    assert r.returncode == 0
    out = r.stdout.decode()
    assert re.match(r"\[agent-bus\] You have 1 new message", out)
    assert "## 7 | antigravity@repo -> claude@repo" in out and "Please look at x." in out
    assert len(stub.acks) == 1 and stub.acks[0]["ids"] == [7] and stub.acks[0]["me"].startswith("claude@")


def test_silent_without_mail(stub):
    r = run_hook("claude", stub.url)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    assert stub.acks == []


def test_silent_when_daemon_is_down():
    r = run_hook("claude", f"http://127.0.0.1:{free_port()}")
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_silent_when_token_is_refused(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, token="wrong")
    assert r.returncode == 0 and r.stdout == b""
    assert stub.acks == []


def test_silent_without_any_token(stub, tmp_path):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, token=None, home=tmp_path)
    assert r.returncode == 0 and r.stdout == b""


def test_antigravity_survives_malformed_stdin(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("antigravity", stub.url, stdin=b"not json at all")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["injectSteps"][0]["ephemeralMessage"].startswith("[agent-bus] You have 1 new message")
    assert stub.acks[0]["me"].startswith("antigravity@") and stub.acks[0]["ids"] == [7]


def test_antigravity_empty_is_valid_json(stub):
    r = run_hook("antigravity", stub.url, stdin=json.dumps({"workspacePaths": ["/tmp/some-repo"]}).encode())
    assert r.returncode == 0 and json.loads(r.stdout) == {}


@pytest.mark.skipif(shutil.which("uv") is None, reason="the hooks.json form needs uv")
def test_hooks_json_exec_form_via_uv(stub):
    """The exact invocation hooks.json uses: uv run --no-project <cli.py> hook --agent claude."""
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, runner=["uv", "run", "--no-project"])
    assert r.returncode == 0
    assert r.stdout.decode().startswith("[agent-bus] You have 1 new message")
