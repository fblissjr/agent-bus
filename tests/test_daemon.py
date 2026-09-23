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
    code, msg = bus.call("POST", "/api/send", claude, {"from": "antigravity@r#spoof", "to": "antigravity@r", "repo": "r", "status": "REQUEST", "verb": "review", "body": "hi", "thread": "t"}, instance="a6419e08", host="host1", pid=4242, cwd="/x/y/my-repo")
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
        row = {"seq": seq, "ts": "2026-09-22T17:22:11Z", "event": "enroll", "harness": "admin", "instance": None, "host": "host1", "pid": None, "cwd": None, "peer": "127.0.0.1", "message_id": None, "body_hash": None, "prev_hash": prev}
        row["hash"] = ledger_hash(row)
        db.execute("INSERT INTO ledger VALUES (:seq, :ts, :event, :harness, :instance, :host, :pid, :cwd, :peer, :message_id, :body_hash, :prev_hash, :hash)", row)
        prev = row["hash"]
    db.commit()
    db.close()
    store = Store(tmp_path)
    assert store.verify("old-claude-token") == ("claude", socket.gethostname())
    assert store.verify_ledger() is None
    store.enroll("antigravity", "host1", Identity("admin"))
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


def test_export_is_admin_only_and_never_carries_token_hashes(bus):
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "FYI", "body": "exported", "thread": "t"}, instance="s1")
    assert bus.call("GET", "/api/export", claude)[0] == 400
    code, out = bus.call("GET", "/api/export", ADMIN)
    assert code == 200 and out["first_bad_seq"] is None
    assert set(out["schema"]) == {"messages", "receipts", "seen", "participants", "ledger"}
    assert out["messages"][0]["body"] == "exported" and out["messages"][0]["files"] == []
    assert out["participants"] and "token_hash" not in out["participants"][0]
    assert "token_hash" not in json.dumps(out)
    assert [r["event"] for r in out["ledger"]] == ["enroll", "send"]


def test_cli_register_renders_the_page_from_the_packaged_template(bus, tmp_path):
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "REQUEST", "verb": "review", "body": "on the page", "thread": "t"})
    r = cli(bus, ["register", "--out", str(tmp_path / "out" / "r.html")], tmp_path)
    assert r.returncode == 0, r.stderr
    page = (tmp_path / "out" / "r.html").read_text()
    assert "__DATA__" not in page and "on the page" in page and "agent-bus Register" in page
    start = page.index('<script id="data" type="application/json">') + len('<script id="data" type="application/json">')
    embedded = json.loads(page[start:page.index("</script>", start)].replace("<\\/", "</"))
    assert len(embedded["messages"]) == 1 and "commits" in embedded
    assert cli(bus, ["register"], tmp_path).returncode == 0
    assert list((tmp_path / "internal" / "register").glob("*-register.html"))


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


def test_sender_components_must_fit_the_address_grammar(bus):
    """A sender is always reachable and parseable: a checkout name or a reported session id
    that would not fit the grammar is refused before anything is stored."""
    claude = bus.enroll("claude")
    for meta in ({"cwd": "/Users/me/My Repo"}, {"cwd": "/x/a@b"}, {"instance": "a#b c"}):
        code, out = bus.call("POST", "/api/send", claude, {"to": "all", "repo": "r", "status": "FYI", "body": "x", "thread": "t"}, **meta)
        assert code == 400 and "bad" in out["error"], (meta, out)
    assert bus.call("GET", "/api/thread?repo=r&thread=t", ADMIN)[1] == []
    assert [r["event"] for r in bus.call("GET", "/api/ledger", ADMIN)[1]["rows"]] == ["enroll"]


def test_refused_send_stores_nothing(bus):
    """Validation runs before the first write, so a rejected send leaves no message, no
    ledger row, and no projection."""
    claude = bus.enroll("claude")
    bad = (
        {"files": ["src/x.py", 42]},
        {"files": ["/etc/passwd"]},
        {"status": "REQUEST", "verb": "deploy"},
        {"body": "   "},
    )
    for extra in bad:
        payload = {"to": "all", "repo": "r", "status": "FYI", "body": "partial", "thread": "t", **extra}
        assert bus.call("POST", "/api/send", claude, payload)[0] == 400, extra
    assert bus.call("GET", "/api/thread?repo=r&thread=t", ADMIN)[1] == []
    assert [r["event"] for r in bus.call("GET", "/api/ledger", ADMIN)[1]["rows"]] == ["enroll"]
    assert not (bus.state / "threads" / "r" / "t.md").exists()


def test_ack_only_what_exists_and_is_addressed_to_you(bus):
    claude, anti = bus.enroll("claude"), bus.enroll("antigravity")
    code, private = bus.call("POST", "/api/send", anti, {"to": "codex@r", "repo": "r", "status": "FYI", "body": "not for claude", "thread": "t"})
    code, mine = bus.call("POST", "/api/send", anti, {"to": "claude@r", "repo": "r", "status": "FYI", "body": "for claude", "thread": "t"})
    code, out = bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": [999]})
    assert code == 400 and "999" in out["error"]
    code, out = bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": [private["id"]]})
    assert code == 400 and str(private["id"]) in out["error"]
    code, out = bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": [mine["id"], private["id"]]})
    assert code == 400  # one refused id refuses the whole call; nothing is half-acked
    assert bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": [mine["id"]]}) == (200, {"acked": 1})
    assert bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": ["x"]})[0] == 400
    assert bus.call("POST", "/api/ack", claude, {"me": "claude@r#s1", "ids": []})[0] == 400
    acks = [r for r in bus.call("GET", "/api/ledger", ADMIN)[1]["rows"] if r["event"] == "ack"]
    assert [r["message_id"] for r in acks] == [mine["id"]]


def test_own_mail_is_excluded_by_session_not_checkout(bus):
    """A session that sends to its own harness from one checkout does not find that message
    waiting for it in another checkout; a different session of the same harness does."""
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "claude", "repo": "agent-bus", "status": "FYI", "body": "from A", "thread": "x"}, instance="s1", cwd="/w/agent-bus")
    assert bus.call("POST", "/api/inbox", claude, {"me": "claude@other#s1"}, instance="s1", cwd="/w/other")[1] == []
    assert [m["body"] for m in bus.call("POST", "/api/inbox", claude, {"me": "claude@other#s2"}, instance="s2", cwd="/w/other")[1]] == ["from A"]


def test_admin_token_takes_no_part_in_presence(bus):
    code, out = bus.call("GET", "/api/who", ADMIN)
    assert code == 400 and "admin" in out["error"]


def test_sqlite_side_files_are_owner_only(bus):
    claude = bus.enroll("claude")
    bus.call("POST", "/api/send", claude, {"to": "all", "repo": "r", "status": "FYI", "body": "one", "thread": "t"})
    for name in ("store.db-wal", "store.db-shm"):
        p = bus.state / name
        assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600, name


def test_cli_send_needs_a_thread_when_not_interactive(bus, tmp_path):
    claude = bus.enroll("claude")
    r = cli(bus, ["send", "--to", "all", "--status", "FYI", "hello"], tmp_path, token_env={"AGENT_BUS_TOKEN_OWNER": claude, "AGENT_BUS_HARNESS": "claude", "AGENT_BUS_TOKEN_CLAUDE": claude})
    assert r.returncode != 0 and "--thread" in r.stderr.decode()
    assert bus.call("GET", "/api/ledger", ADMIN)[1]["rows"][-1]["event"] == "enroll"
    r = cli(bus, ["send", "--to", "all", "--status", "FYI", "--thread", "t", "hello"], tmp_path, token_env={"AGENT_BUS_HARNESS": "claude", "AGENT_BUS_TOKEN_CLAUDE": claude})
    assert r.returncode == 0, r.stderr


def test_messages_is_admin_only_filtered_and_carries_receipts(bus, tmp_path):
    claude, antigravity = bus.enroll("claude"), bus.enroll("antigravity")
    bus.call("POST", "/api/send", claude, {"to": "antigravity@r", "repo": "r", "status": "REQUEST", "verb": "review", "body": "first", "thread": "a"}, instance="s1")
    bus.call("POST", "/api/send", antigravity, {"to": "claude@r", "repo": "r", "status": "ANSWER", "body": "second", "thread": "a"}, instance="s2")
    bus.call("POST", "/api/send", claude, {"to": "all", "repo": "r", "status": "FYI", "body": "third", "thread": "b"}, instance="s1")
    bus.call("POST", "/api/ack", antigravity, {"me": "antigravity@r#s2", "ids": [1]}, instance="s2")
    assert bus.call("GET", "/api/messages", claude)[0] == 400  # a participant reads a thread it belongs to, never the lot
    code, out = bus.call("GET", "/api/messages", ADMIN)
    assert code == 200 and [m["body"] for m in out] == ["first", "second", "third"]
    assert [a["reader"] for a in out[0]["acked"]] == ["antigravity@r"] and out[0]["acked"][0]["ts"] and out[1]["acked"] == []
    ids = lambda query: [m["id"] for m in bus.call("GET", f"/api/messages?{query}", ADMIN)[1]]
    assert ids("thread=b") == [3]
    assert ids("from=antigravity") == [2] and ids("from=claude@r%23s1") == [1, 3]
    assert ids("to=claude@r") == [2] and ids("to=all") == [3]
    assert ids("status=FYI") == [3]
    assert ids("since=2999-01-01") == [] and ids("until=2999-01-01") == [1, 2, 3]
    assert ids("limit=1") == [3]  # the latest, still oldest first
    assert bus.call("GET", "/api/messages?status=NOPE", ADMIN)[0] == 400
    r = cli(bus, ["messages", "--brief"], tmp_path)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.decode().splitlines()
    assert len(lines) == 3 and lines[0].endswith("acked: antigravity@r") and lines[1].endswith("acked: -")
    out = cli(bus, ["messages", "--thread", "a"], tmp_path).stdout.decode()
    assert "first" in out and "second" in out and "third" not in out and "acked: antigravity@r at " in out
    assert cli(bus, ["messages", "--from", "codex"], tmp_path).stdout.decode().strip() == "No messages match."
