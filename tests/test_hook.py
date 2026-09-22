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
MESSAGE = {"id": 7, "ts": "2026-09-22T15:00:00Z", "from": "antigravity@repo#b2d1b325", "to": "claude@repo", "repo": "repo", "thread": "t", "status": "REQUEST", "verb": "review", "sha": "abc1234", "files": ["src/x.py"], "body": "Please look at x."}


class Stub:
    """Answers /api/inbox with `inbox`, records every /api/ack body and the identity headers seen."""

    def __init__(self):
        self.inbox = []
        self.acks = []
        self.headers = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.headers.append({k: v for k, v in self.headers.items() if k.startswith("X-Bus-")})
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

            def log_message(self, format, *args):
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


def run_hook(agent, url, token=TOKEN, stdin=b"", runner=None, home=None, env_extra=None):
    cmd = (runner or [sys.executable]) + [str(CLI), "hook", "--agent", agent]
    env = {k: v for k, v in os.environ.items() if not (k.startswith("AGENT_BUS_") or k.startswith("CLAUDE_"))}
    env["AGENT_BUS_SERVER"] = url
    if token is not None:
        env[f"AGENT_BUS_TOKEN_{agent.upper()}"] = token
    if home is not None:
        env["HOME"] = str(home)
    env.update(env_extra or {})
    return subprocess.run(cmd, input=stdin, capture_output=True, env=env, timeout=30)


CLAUDE_PROMPT = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "a6419e08-73f1-4550-9447-f615cd4c8ed9", "cwd": "/x/y/repo"}).encode()
CLAUDE_START = json.dumps({"hook_event_name": "SessionStart", "session_id": "a6419e08-73f1-4550-9447-f615cd4c8ed9", "cwd": "/x/y/repo"}).encode()


def test_speaks_with_mail_and_leaves_the_ack_to_the_reader(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, stdin=CLAUDE_PROMPT)
    assert r.returncode == 0
    out = r.stdout.decode()
    assert re.match(r"\[agent-bus\] You have 1 new message", out)
    assert "## 7 | antigravity@repo#b2d1b325 -> claude@repo" in out and "Please look at x." in out
    assert "Ack after reading: agent-bus ack 7 --me claude@repo#a6419e08" in out
    assert stub.acks == []


def test_you_is_the_session_not_the_checkout(stub):
    """A message this session sent from another checkout is still (you); a different session of the same harness is (another claude)."""
    mine = dict(MESSAGE, id=8, **{"from": "claude@agent-bus#a6419e08"}, to="all")
    other = dict(MESSAGE, id=9, **{"from": "claude@agent-bus#6dc4b84d"}, to="claude@repo")
    stub.inbox = [mine, other]
    out = run_hook("claude", stub.url, stdin=CLAUDE_PROMPT).stdout.decode()
    assert "## 8 | claude@agent-bus#a6419e08 -> all | 2026-09-22T15:00:00Z | REQUEST review  (you)" in out
    assert "## 9 | claude@agent-bus#6dc4b84d -> claude@repo | 2026-09-22T15:00:00Z | REQUEST review  (another claude)" in out


def test_session_start_always_says_who_you_are(stub):
    r = run_hook("claude", stub.url, stdin=CLAUDE_START)
    assert r.returncode == 0
    assert r.stdout.decode().strip() == "[agent-bus] you are claude@repo#a6419e08"


def test_identity_headers_come_from_the_session(stub):
    run_hook("claude", stub.url, stdin=CLAUDE_PROMPT)
    assert stub.headers and stub.headers[0]["X-Bus-Instance"] == "a6419e08"
    assert stub.headers[0]["X-Bus-Host"] and stub.headers[0]["X-Bus-Cwd"]


def test_silent_without_mail(stub):
    r = run_hook("claude", stub.url, stdin=CLAUDE_PROMPT)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    assert stub.acks == []


def test_silent_when_daemon_is_down():
    r = run_hook("claude", f"http://127.0.0.1:{free_port()}", stdin=CLAUDE_PROMPT)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_silent_when_token_is_refused(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, token="wrong", stdin=CLAUDE_PROMPT)
    assert r.returncode == 0 and r.stdout == b""
    assert stub.acks == []


def test_silent_without_any_token(stub, tmp_path):
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, token=None, home=tmp_path, stdin=CLAUDE_PROMPT)
    assert r.returncode == 0 and r.stdout == b""


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-0 file")
def test_silent_when_token_file_is_unreadable(stub, tmp_path):
    """A token file the owner cannot read (wrong mode, wrong owner after a sudo mistake) must not become a per-prompt traceback."""
    stub.inbox = [MESSAGE]
    tokens = tmp_path / ".agents" / "tokens"
    tokens.mkdir(parents=True)
    (tokens / "claude").write_text(TOKEN + "\n")
    (tokens / "claude").chmod(0)
    r = run_hook("claude", stub.url, token=None, home=tmp_path, stdin=CLAUDE_PROMPT)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_antigravity_acks_on_delivery_with_its_conversation_id(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("antigravity", stub.url, stdin=json.dumps({"conversationId": "b2d1b325-5033-4ccf-b7b4-136ba418c865", "workspacePaths": ["/tmp/some-repo"]}).encode())
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["injectSteps"][0]["ephemeralMessage"].startswith("[agent-bus] You have 1 new message")
    assert stub.acks == [{"me": "antigravity@some-repo#b2d1b325", "ids": [7]}]


def test_antigravity_survives_malformed_stdin(stub):
    stub.inbox = [MESSAGE]
    r = run_hook("antigravity", stub.url, stdin=b"not json at all")
    assert r.returncode == 0
    assert json.loads(r.stdout)["injectSteps"][0]["ephemeralMessage"].startswith("[agent-bus] You have 1 new message")


def test_antigravity_empty_is_valid_json(stub):
    r = run_hook("antigravity", stub.url, stdin=json.dumps({"workspacePaths": ["/tmp/some-repo"]}).encode())
    assert r.returncode == 0 and json.loads(r.stdout) == {}


@pytest.mark.skipif(shutil.which("uv") is None, reason="the hooks.json form needs uv")
def test_hooks_json_exec_form_via_uv(stub):
    """The exact invocation hooks.json uses: uv run --no-project <cli.py> hook --agent claude."""
    stub.inbox = [MESSAGE]
    r = run_hook("claude", stub.url, stdin=CLAUDE_PROMPT, runner=["uv", "run", "--no-project"])
    assert r.returncode == 0
    assert r.stdout.decode().startswith("[agent-bus] You have 1 new message")
