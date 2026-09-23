"""The simulator: the CLI and the hooks against a marked store in this process, with
no daemon. Pins the marker deciding which door a directory has, the round trip and its
stamping, the admin path without a token, both hook arms, many writer processes on one
chain, and that a daemon that is down never turns into a store opened directly."""

import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_bus.store import SIM_MACHINE, SIM_MARKER, SIM_PEER, Store, init_sim

CLI = Path(__file__).resolve().parents[1] / "src" / "agent_bus" / "cli.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(args, sim, cwd, harness=None, instance=None, stdin=b"", env_extra=None):
    """The CLI as a subprocess with the daemon URL pointing at nothing, so any success came from the store."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith("AGENT_BUS_") or k.startswith("CLAUDE_"))}
    env["AGENT_BUS_SERVER"] = f"http://127.0.0.1:{free_port()}"
    if sim is not None:
        env["AGENT_BUS_SIM_DIR"] = str(sim)
    if harness:
        env["AGENT_BUS_HARNESS"] = harness
    if instance:
        env["AGENT_BUS_INSTANCE"] = instance
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True, env=env, timeout=60, cwd=str(cwd))


@pytest.fixture
def sim(tmp_path):
    """A marked directory and a checkout named `repo` to run from."""
    repo = tmp_path / "repo"
    repo.mkdir()
    data = tmp_path / "data"
    r = run(["sim", "init", str(data)], None, repo)
    assert r.returncode == 0, r.stderr
    assert (data / SIM_MARKER).exists() and (data / "store.db").exists()
    assert f"export AGENT_BUS_SIM_DIR={data.resolve()}" in r.stdout.decode()
    assert "claimed" in r.stderr.decode()
    return data, repo


def send(sim, harness, instance, to, body, thread="t"):
    data, repo = sim
    r = run(["send", "--to", to, "--status", "REQUEST", "--verb", "review", "--thread", thread, body], data, repo, harness, instance)
    assert r.returncode == 0, r.stderr
    return r


def test_marker_decides_the_door(tmp_path):
    unmarked = tmp_path / "plain"
    with pytest.raises(ValueError, match="sim init"):
        Store(unmarked, sim=True)
    assert not unmarked.exists()  # refused before anything is created
    Store(unmarked)  # the daemon's door opens it
    marked = tmp_path / "marked"
    assert init_sim(marked).read_text().startswith("agent-bus simulator")
    with pytest.raises(ValueError, match="cannot be served"):
        Store(marked)
    Store(marked, sim=True)


def test_round_trip_without_a_daemon(sim):
    data, repo = sim
    assert run(["whoami"], data, repo, "claude", "a1a1a1a1").stdout.decode().strip() == "claude@repo#a1a1a1a1"
    send(sim, "claude", "a1a1a1a1", "antigravity@repo", "look at x")
    out = run(["inbox"], data, repo, "antigravity", "b2b2b2b2").stdout.decode()
    assert "## 1 | claude@repo#a1a1a1a1 -> antigravity@repo" in out and "look at x" in out
    assert run(["inbox", "-q"], data, repo, "claude", "c3c3c3c3").stdout == b""  # not addressed to claude
    r = run(["ack", "1"], data, repo, "antigravity", "b2b2b2b2")
    assert r.returncode == 0, r.stderr
    assert "Inbox empty" in run(["inbox"], data, repo, "antigravity", "b2b2b2b2").stdout.decode()
    who = run(["who", "--json"], data, repo, "owner").stdout
    assert {w["address"] for w in json.loads(who)} >= {"claude@repo#a1a1a1a1", "antigravity@repo#b2b2b2b2"}
    # A member reads the thread; a stranger is refused; the projection is on disk and readable.
    assert "look at x" in run(["show", "t", "--repo", "repo"], data, repo, "claude", "zz").stdout.decode()
    r = run(["show", "t", "--repo", "repo"], data, repo, "codex", "zz")
    assert r.returncode != 0 and "not a participant" in r.stderr.decode()
    assert "look at x" in (data / "threads" / "repo" / "t.md").read_text()


def test_every_row_says_it_was_claimed(sim):
    data, repo = sim
    send(sim, "claude", "a1a1a1a1", "antigravity@repo", "stamped")
    run(["ack", "1"], data, repo, "antigravity", "b2b2b2b2")
    rows = json.loads(run(["ledger", "--json"], data, repo).stdout)
    assert rows["first_bad_seq"] is None
    assert [(r["event"], r["machine"], r["peer"]) for r in rows["rows"]] == [("send", SIM_MACHINE, SIM_PEER), ("ack", SIM_MACHINE, SIM_PEER)]
    assert all(r["host"] and r["pid"] and r["cwd"] for r in rows["rows"])
    jsonl = [json.loads(line) for line in (data / "ledger.jsonl").read_text().splitlines()]
    assert [r["hash"] for r in jsonl] == [r["hash"] for r in rows["rows"]]


def test_admin_path_needs_no_token_and_takes_no_part(sim, monkeypatch):
    data, repo = sim
    send(sim, "claude", "a1a1a1a1", "antigravity@repo", "audited")
    assert "audited" in run(["show", "t", "--repo", "repo", "--audit"], data, repo).stdout.decode()
    assert "send   claude@sim#a1a1a1a1" in run(["ledger"], data, repo).stdout.decode()
    r = run(["register", "--out", str(data.parent / "reg.html")], data, repo)
    assert r.returncode == 0, r.stderr
    assert "audited" in (data.parent / "reg.html").read_text()
    r = run(["enroll", "claude"], data, repo)
    assert r.returncode != 0 and "no tokens" in r.stderr.decode()
    # In process: a participant cannot audit, and admin cannot speak, exactly as on the daemon.
    monkeypatch.setenv("AGENT_BUS_SIM_DIR", str(data))
    from agent_bus.cli import BusError, direct_call
    with pytest.raises(BusError, match="admin token only"):
        direct_call("/api/ledger", "claude")
    with pytest.raises(BusError, match="cannot send"):
        direct_call("/api/send", "admin", "POST", {"to": "all", "repo": "repo", "status": "FYI", "body": "x", "thread": "t"})


CLAUDE_PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "a6419e08-73f1-4550-9447-f615cd4c8ed9"}
CLAUDE_START = dict(CLAUDE_PROMPT, hook_event_name="SessionStart")


def test_claude_hook_delivers_from_the_simulator_and_leaves_the_ack(sim):
    data, repo = sim
    stdin = json.dumps(dict(CLAUDE_PROMPT, cwd=str(repo))).encode()
    r = run(["hook", "--agent", "claude"], data, repo, stdin=stdin)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""  # no mail: silent
    send(sim, "antigravity", "b2d1b325", "claude@repo", "Please look at x.")
    r = run(["hook", "--agent", "claude"], data, repo, stdin=json.dumps(dict(CLAUDE_START, cwd=str(repo))).encode())
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode()
    assert out.startswith("[agent-bus] you are claude@repo#a6419e08")
    assert "You have 1 new message" in out and "## 1 | antigravity@repo#b2d1b325 -> claude@repo" in out
    assert "Ack after reading: agent-bus ack 1 --me claude@repo#a6419e08" in out
    # Not acked: it re-shows on the next prompt until the reader acks.
    assert "You have 1 new message" in run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout.decode()
    assert run(["ack", "1"], data, repo, "claude", "a6419e08").returncode == 0
    assert run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout == b""


def test_antigravity_hook_acks_on_delivery(sim):
    data, repo = sim
    send(sim, "claude", "a6419e08", "antigravity@repo", "for gemini")
    stdin = json.dumps({"conversationId": "b2d1b325-5033-4ccf-b7b4-136ba418c865", "workspacePaths": [str(repo)]}).encode()
    r = run(["hook", "--agent", "antigravity"], data, repo, stdin=stdin)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "for gemini" in out["injectSteps"][0]["ephemeralMessage"]
    assert json.loads(run(["hook", "--agent", "antigravity"], data, repo, stdin=stdin).stdout) == {}
    assert json.loads(run(["ledger", "--json"], data, repo).stdout)["rows"][-1]["subject"] == "antigravity@repo"


def test_hook_is_silent_when_the_directory_is_not_a_simulator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    nope = tmp_path / "nope"
    stdin = json.dumps(dict(CLAUDE_PROMPT, cwd=str(repo))).encode()
    r = run(["hook", "--agent", "claude"], nope, repo, stdin=stdin)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    assert not nope.exists()
    r = run(["hook", "--agent", "antigravity"], nope, repo, stdin=b"{}")
    assert r.returncode == 0 and json.loads(r.stdout) == {}
    r = run(["send", "--to", "all", "--status", "FYI", "--thread", "t", "hello"], nope, repo, "claude", "a1")
    assert r.returncode != 0 and "sim init" in r.stderr.decode()


def test_many_writer_processes_keep_one_chain(sim):
    data, repo = sim
    n = 12
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda i: run(["send", "--to", "all", "--status", "FYI", "--thread", "t", f"m{i}"], data, repo, "claude", f"s{i}"), range(n)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results if r.returncode]
    store = Store(data, sim=True)
    assert store.verify_ledger() is None
    assert [r["seq"] for r in store.ledger(limit=n + 1)] == list(range(1, n + 1))
    assert len(store.db.execute("SELECT id FROM messages").fetchall()) == n
    assert len((data / "ledger.jsonl").read_text().splitlines()) == n


def test_no_fallback_when_the_daemon_is_down(sim):
    data, repo = sim
    r = run(["send", "--to", "all", "--status", "FYI", "--thread", "t", "hello"], None, repo, "claude", "a1", env_extra={"AGENT_BUS_TOKEN_CLAUDE": "t"})
    assert r.returncode != 0 and "cannot reach" in r.stderr.decode()
    assert Store(data, sim=True).db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
