"""The daemon against a real socket: identity from the token, instance from the
headers, receipts scoped per harness and repo, membership on threads, the
ledger's chain and its admin-only door, and the MCP tool path carrying the
caller through the context variable."""

import json
import os
import socket
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from agent_bus.daemon import build_app
from agent_bus.store import Store

ADMIN = "admin-token-for-tests"


class Bus:
    def __init__(self, tmp):
        self.state = Path(tmp)
        (self.state / "PROTOCOL.md").write_text("# test protocol\n")
        self.store = Store(self.state)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        config = uvicorn.Config(build_app("127.0.0.1", self.store, ADMIN), host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"

    def call(self, method, path, token, body=None, **meta):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        for k, v in meta.items():
            headers[f"X-Bus-{k.capitalize()}"] = str(v)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def enroll(self, name):
        code, out = self.call("POST", "/api/enroll", ADMIN, {"name": name})
        assert code == 200, out
        return out["token"]

    def stop(self):
        self.server.should_exit = True


@pytest.fixture
def bus(tmp_path):
    b = Bus(tmp_path)
    yield b
    b.stop()


def test_no_token_and_admin_cannot_speak(bus):
    assert bus.call("GET", "/api/who", None)[0] == 401
    assert bus.call("GET", "/api/who", "not-a-token")[0] == 401
    code, out = bus.call("POST", "/api/send", ADMIN, {"to": "claude", "repo": "r", "status": "FYI", "body": "x"})
    assert code == 400 and "admin" in out["error"]


def test_sender_is_derived_from_token_and_metadata(bus):
    claude = bus.enroll("claude")
    code, msg = bus.call("POST", "/api/send", claude, {"from": "antigravity@r#spoof", "to": "antigravity@r", "repo": "r", "status": "REQUEST", "verb": "review", "body": "hi", "thread": "t"}, instance="a6419e08", host="vojo", pid=4242, cwd="/x/y/my-repo")
    assert code == 200
    assert msg["from"] == "claude@my-repo#a6419e08"
    code, me = bus.call("GET", "/api/whoami?repo=r", claude, instance="a6419e08", cwd="/x/y/my-repo")
    assert (code, me["harness"], me["address"]) == (200, "claude", "claude@my-repo#a6419e08")
    code, msg2 = bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "FYI", "body": "no metadata", "thread": "t"})
    assert msg2["from"] == "claude@r#default"


def test_inbox_requires_own_harness_and_receipts_scope_per_harness_repo(bus):
    claude, anti = bus.enroll("claude"), bus.enroll("antigravity")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "REQUEST", "verb": "review", "body": "broadcast", "thread": "t"})
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r#b2", "repo": "r", "status": "FYI", "body": "for b2 only", "thread": "t"})
    assert bus.call("POST", "/api/inbox", anti, {"me": "claude@r#a1"})[0] == 400
    code, a1 = bus.call("POST", "/api/inbox", anti, {"me": "antigravity@r#a1"})
    code, b2 = bus.call("POST", "/api/inbox", anti, {"me": "antigravity@r#b2"})
    assert [m["body"] for m in a1] == ["broadcast"]
    assert [m["body"] for m in b2] == ["broadcast", "for b2 only"]
    bus.call("POST", "/api/ack", anti, {"me": "antigravity@r#a1", "ids": [a1[0]["id"]]}, instance="a1")
    assert [m["body"] for m in bus.call("POST", "/api/inbox", anti, {"me": "antigravity@r#b2"})[1]] == ["for b2 only"]
    assert bus.call("POST", "/api/inbox", anti, {"me": "antigravity@r#c3"})[1] == []


def test_thread_membership_and_admin_audit(bus):
    claude, anti, codex = bus.enroll("claude"), bus.enroll("antigravity"), bus.enroll("codex")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "FYI", "body": "private to two", "thread": "pair"})
    assert bus.call("GET", "/api/thread?repo=r&thread=pair", claude)[0] == 200
    assert bus.call("GET", "/api/thread?repo=r&thread=pair", anti)[0] == 200
    code, out = bus.call("GET", "/api/thread?repo=r&thread=pair", codex)
    assert code == 400 and "not a participant" in out["error"]
    assert bus.call("GET", "/api/thread?repo=r&thread=pair", ADMIN)[0] == 200
    bus.call("POST", "/api/send", anti, {"to": "all@r", "repo": "r", "status": "FYI", "body": "everyone", "thread": "room"})
    assert bus.call("GET", "/api/thread?repo=r&thread=room", codex)[0] == 200


def test_ledger_is_admin_only_chained_and_tamper_evident(bus):
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "all", "repo": "r", "status": "FYI", "body": "one", "thread": "t"}, instance="s1")
    bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": [1]}, instance="s1")
    assert bus.call("GET", "/api/ledger", claude)[0] == 400
    code, out = bus.call("GET", "/api/ledger", ADMIN)
    assert code == 200 and out["first_bad_seq"] is None
    assert [r["event"] for r in out["rows"]] == ["enroll", "send", "ack"]
    assert out["rows"][2]["instance"] == "s1" and out["rows"][1]["message_id"] == 1
    assert [r["prev_hash"] for r in out["rows"]][1:] == [r["hash"] for r in out["rows"]][:-1]
    jsonl = [json.loads(l) for l in (bus.state / "ledger.jsonl").read_text().splitlines()]
    assert [r["hash"] for r in jsonl] == [r["hash"] for r in out["rows"]]
    # A row edited behind the daemon's back breaks the chain from that seq on.
    with sqlite3.connect(bus.state / "store.db") as db:
        db.execute("UPDATE ledger SET instance = 'forged' WHERE seq = 2")
    assert bus.call("GET", "/api/ledger", ADMIN)[1]["first_bad_seq"] == 2


def test_state_files_are_owner_only(bus):
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "all", "repo": "r", "status": "FYI", "body": "one", "thread": "t"})
    for p in (bus.state / "store.db", bus.state / "ledger.jsonl", bus.state / "threads" / "r" / "t.md"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    assert stat.S_IMODE(bus.state.stat().st_mode) == 0o700


def test_enrolling_the_same_harness_for_another_machine_does_not_revoke(bus):
    import socket
    here = bus.enroll("claude")
    code, out = bus.call("POST", "/api/enroll", ADMIN, {"name": "claude", "machine": "mac"})
    assert code == 200 and out["machine"] == "mac"
    mac = out["token"]
    assert bus.call("GET", "/api/whoami?repo=r", here)[1]["machine"] == socket.gethostname()
    assert bus.call("GET", "/api/whoami?repo=r", mac)[1]["machine"] == "mac"
    rows = bus.call("GET", "/api/ledger", ADMIN)[1]["rows"]
    assert [r["subject"] for r in rows] == [f"claude@{socket.gethostname()}", "claude@mac"]
    assert all(r["hash_version"] == 2 for r in rows)


def test_ledger_v2_verifies_over_a_v1_store(tmp_path):
    """A 0.4 store (participants by name, ledger version 1) opens, keeps verifying its old
    rows under the version-1 field list, and chains new version-2 rows onto them."""
    import socket
    from agent_bus.store import GENESIS, Identity, ledger_hash, token_hash
    db = sqlite3.connect(tmp_path / "store.db")
    db.executescript("""
        CREATE TABLE participants (name TEXT PRIMARY KEY, token_hash TEXT NOT NULL, ts TEXT NOT NULL);
        CREATE TABLE ledger (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, event TEXT NOT NULL, harness TEXT NOT NULL,
            instance TEXT, host TEXT, pid INTEGER, cwd TEXT, peer TEXT, message_id INTEGER, body_hash TEXT,
            prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
    """)
    db.execute("INSERT INTO participants VALUES ('claude', ?, '2026-09-22T17:22:11Z')", (token_hash("old-claude-token"),))
    prev = GENESIS
    for seq in (1, 2):
        row = {"seq": seq, "ts": "2026-09-22T17:22:11Z", "event": "enroll", "harness": "admin", "instance": None, "host": "vojo", "pid": None, "cwd": None, "peer": "127.0.0.1", "message_id": None, "body_hash": None, "prev_hash": prev}
        row["hash"] = ledger_hash(row)
        db.execute("INSERT INTO ledger VALUES (:seq, :ts, :event, :harness, :instance, :host, :pid, :cwd, :peer, :message_id, :body_hash, :prev_hash, :hash)", row)
        prev = row["hash"]
    db.commit()
    db.close()
    store = Store(tmp_path)
    assert store.verify("old-claude-token") == ("claude", socket.gethostname())
    assert store.verify_ledger() is None
    store.enroll("antigravity", "vojo", Identity("admin"))
    assert store.verify_ledger() is None
    versions = [r["hash_version"] for r in store.ledger()]
    assert versions == [1, 1, 2]
    with sqlite3.connect(tmp_path / "store.db") as raw:
        raw.execute("UPDATE ledger SET hash_version = 2 WHERE seq = 1")
    assert store.verify_ledger() == 1  # relabelling a row to another version breaks it


def cli(bus, args, home, token_env=None, stdin=b""):
    import subprocess
    env = {k: v for k, v in os.environ.items() if not (k.startswith("AGENT_BUS_") or k.startswith("CLAUDE_"))}
    env.update({"AGENT_BUS_SERVER": bus.url, "HOME": str(home), "AGENT_BUS_ADMIN_TOKEN": ADMIN})
    env.update(token_env or {})
    return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "src" / "agent_bus" / "cli.py"), *args], input=stdin, capture_output=True, env=env, timeout=30, cwd=str(home))


def test_cli_audit_needs_no_participant_token(bus, tmp_path):
    import os, sys  # noqa: F401  (used by cli())
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "FYI", "body": "audited", "thread": "t"})
    r = cli(bus, ["show", "t", "--repo", "r", "--audit"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "audited" in r.stdout.decode()
    r = cli(bus, ["ledger"], tmp_path)
    assert r.returncode == 0 and "enroll" in r.stdout.decode() and "claude@" in r.stdout.decode()


def test_cli_enroll_for_another_machine_prints_and_writes_nothing(bus, tmp_path):
    r = cli(bus, ["enroll", "claude", "--machine", "mac"], tmp_path)
    assert r.returncode == 0, r.stderr
    token = r.stdout.decode().strip()
    assert token and not (tmp_path / ".agents" / "tokens").exists()
    assert bus.call("GET", "/api/whoami?repo=r", token)[1]["machine"] == "mac"


def test_enrolled_token_destination_rules():
    from agent_bus.cli import TOKENS, enrolled_token_destination
    assert enrolled_token_destination("claude", "here", euid=0, this_host="here") is None
    assert enrolled_token_destination("claude", "mac", euid=1000, this_host="here") is None
    assert enrolled_token_destination("claude", "here", euid=1000, this_host="here") == TOKENS / "claude"


def test_migrates_a_0_3_store(tmp_path):
    """A store from before verified senders opens, keeps its messages, and serves them with a backfilled harness."""
    db = sqlite3.connect(tmp_path / "store.db")
    db.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
            to_agent TEXT NOT NULL, to_repo TEXT, to_instance TEXT, repo TEXT NOT NULL, thread TEXT NOT NULL,
            status TEXT NOT NULL, verb TEXT, sha TEXT, files TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE receipts (message_id INTEGER NOT NULL, reader TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (message_id, reader));
        CREATE TABLE seen (reader TEXT PRIMARY KEY, ts TEXT NOT NULL);
        INSERT INTO messages VALUES (1, '2026-09-22T15:08:29Z', 'antigravity@r', 'claude@r', 'claude', 'r', NULL, 'r', 'old', 'REQUEST', 'review', NULL, '[]', 'from before');
        INSERT INTO receipts VALUES (1, 'claude@r', '2026-09-22T15:26:13Z');
        INSERT INTO seen VALUES ('claude@r', '2026-09-22T15:26:13Z');
    """)
    db.close()
    store = Store(tmp_path)
    from agent_bus.store import Identity
    assert store.db.execute("SELECT harness FROM messages WHERE id = 1").fetchone() == ("antigravity",)
    assert store.who() == []  # the old presence table was rebuilt empty
    assert store.thread(Identity("antigravity"), "r", "old")[0]["body"] == "from before"
    assert store.inbox(Identity("claude", "new1"), "claude@r#new1") == []  # the 0.3 receipt still counts


def test_mcp_tools_carry_the_caller(bus):
    """The MCP path: harness from the token, instance from the tool argument, both landing in the row."""
    import asyncio
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    claude = bus.enroll("claude")

    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {claude}"}) as http:
            async with Client(streamable_http_client(bus.url + "/mcp", http_client=http), mode="auto") as c:
                r = await c.call_tool("send", {"to": "antigravity@r", "repo": "r", "status": "FYI", "body": "via mcp", "thread": "t", "instance": "m1"})
                assert r.structured_content["from"] == "claude@r#m1", r.content
                r = await c.call_tool("inbox", {"me": "antigravity@r#x"})
                assert r.is_error and "not an address of claude" in r.content[0].text
                r = await c.call_tool("send", {"to": "all", "repo": "r", "status": "FYI", "body": "no instance", "thread": "t"})
                assert r.structured_content["from"] == "claude@r#default"

    asyncio.run(go())
    code, out = bus.call("GET", "/api/ledger", ADMIN)
    assert [r["instance"] for r in out["rows"] if r["event"] == "send"] == ["m1", None]
