"""The simulator: the CLI and the hooks against a marked store in this process, with
no daemon. Pins the marker deciding which door a directory has, the round trip and its
stamping, the admin path without a token, both hook arms, many writer processes on one
chain, and that a daemon that is down never turns into a store opened directly."""

import json
import os
import shutil
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
    assert f"Ack after reading: AGENT_BUS_SIM_DIR={data.resolve()} uv run --no-project {CLI.resolve()} ack 1 --me claude@repo#a6419e08" in out
    # Not acked: it re-shows on the next prompt until the reader acks.
    assert "You have 1 new message" in run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout.decode()
    assert run(["ack", "1"], data, repo, "claude", "a6419e08").returncode == 0
    assert run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout == b""


@pytest.mark.skipif(shutil.which("uv") is None, reason="the printed command runs under uv")
def test_the_printed_ack_command_works_from_another_workspace(sim, tmp_path):
    """The reader runs exactly what the hook printed, from a checkout that is not agent-bus
    and with no simulator variable of its own, and the mail is acked."""
    data, repo = sim
    send(sim, "antigravity", "b2d1b325", "claude@repo", "ack me from elsewhere")
    stdin = json.dumps(dict(CLAUDE_PROMPT, cwd=str(repo))).encode()
    out = run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout.decode()
    command = out.split("Ack after reading: ", 1)[1].strip()
    elsewhere = tmp_path / "other-checkout"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if not (k.startswith("AGENT_BUS_") or k.startswith("CLAUDE_") or k == "VIRTUAL_ENV")}
    # Another workspace has none of this project's environment: drop its venv from PATH, where pytest put `agent-bus`.
    venv = Path(sys.prefix).resolve()
    env["PATH"] = os.pathsep.join(d for d in env.get("PATH", "").split(os.pathsep) if d and not Path(d).resolve().is_relative_to(venv))
    env["AGENT_BUS_HARNESS"] = "claude"
    r = subprocess.run(command, shell=True, cwd=elsewhere, env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert run(["hook", "--agent", "claude"], data, repo, stdin=stdin).stdout == b""


def test_antigravity_hook_acks_on_delivery(sim):
    data, repo = sim
    send(sim, "claude", "a6419e08", "antigravity@repo", "for gemini")
    stdin = json.dumps({"conversationId": "b2d1b325-5033-4ccf-b7b4-136ba418c865", "workspacePaths": [str(repo)]}).encode()
    r = run(["hook", "--agent", "antigravity"], data, repo, stdin=stdin)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "for gemini" in out["injectSteps"][0]["ephemeralMessage"]
    assert "now acked for antigravity" in out["injectSteps"][0]["ephemeralMessage"] and "(threads: t)" in out["injectSteps"][0]["ephemeralMessage"]
    assert json.loads(run(["hook", "--agent", "antigravity"], data, repo, stdin=stdin).stdout) == {}
    assert json.loads(run(["ledger", "--json"], data, repo).stdout)["rows"][-1]["subject"] == "antigravity@repo"


def test_a_linked_worktree_reads_its_main_checkouts_mail(sim, tmp_path):
    """A seat working in <elsewhere>/<seat>-<topic> is still in repo `repo`: same address, same mail."""
    data, repo = sim
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run(git[:5] + ["init", "-q", str(repo)], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "init"], check=True)
    wt = tmp_path / "worktrees" / "mrlol-topic"
    subprocess.run(git + ["worktree", "add", "-q", "-b", "topic", str(wt)], check=True)
    assert run(["whoami"], data, wt, "claude", "mrlol").stdout.decode().strip() == "claude@repo#mrlol"
    send(sim, "antigravity", "b2d1b325", "claude@repo", "reaches the worktree")
    stdin = json.dumps(dict(CLAUDE_PROMPT, cwd=str(wt))).encode()
    assert "reaches the worktree" in run(["hook", "--agent", "claude"], data, wt, stdin=stdin).stdout.decode()


def test_a_seat_label_is_held_by_one_session(sim):
    """An AGENT_BUS_INSTANCE inherited by a second session must not put two sessions in one
    seat: the second speaks as its own session id, says so, and never sees the seat's mail."""
    data, repo = sim
    a = {"CLAUDE_CODE_SESSION_ID": "aaaa1111-0000"}
    b = {"CLAUDE_CODE_SESSION_ID": "bbbb2222-0000"}
    assert run(["whoami"], data, repo, "claude", "mrtall", env_extra=a).stdout.decode().strip() == "claude@repo#mrtall"
    r = run(["whoami"], data, repo, "claude", "mrtall", env_extra=b)
    assert r.stdout.decode().strip() == "claude@repo#bbbb2222"
    assert "seat held: claude@repo#mrtall is held by session aaaa1111" in r.stderr.decode()
    send(sim, "antigravity", "b2d1b325", "claude@repo#mrtall", "for the seat")
    start = lambda sid: json.dumps(dict(CLAUDE_START, session_id=sid, cwd=str(repo))).encode()
    out_b = run(["hook", "--agent", "claude"], data, repo, stdin=start("bbbb2222-0000"), env_extra={"AGENT_BUS_INSTANCE": "mrtall"}).stdout.decode()
    assert out_b.startswith("[agent-bus] you are claude@repo#bbbb2222")
    assert "seat held:" in out_b and "for the seat" not in out_b
    out_a = run(["hook", "--agent", "claude"], data, repo, stdin=start("aaaa1111-0000"), env_extra={"AGENT_BUS_INSTANCE": "mrtall"}).stdout.decode()
    assert out_a.startswith("[agent-bus] you are claude@repo#mrtall") and "for the seat" in out_a and "seat held:" not in out_a
    # Once the holder has been quiet for the window, the seat frees.
    Store(data, sim=True).db.execute("UPDATE seats SET ts = '2000-01-01T00:00:00Z'")
    assert run(["whoami"], data, repo, "claude", "mrtall", env_extra=b).stdout.decode().strip() == "claude@repo#mrtall"


def test_antigravity_commands_and_hook_share_one_instance(sim):
    """The hook is handed the conversation id; a command in the same session must pick the same one."""
    data, repo = sim
    env = {"ANTIGRAVITY_CONVERSATION_ID": "e33feb45-0000", "ANTIGRAVITY_TRAJECTORY_ID": "8e852f69-0000"}
    out = run(["whoami"], data, repo, "antigravity", env_extra=env).stdout.decode().strip()
    assert out == "antigravity@repo#e33feb45"


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


def test_messages_across_threads_need_no_token(sim):
    data, repo = sim
    send(sim, "claude", "a1", "antigravity@repo", "one", thread="x")
    send(sim, "antigravity", "b2", "claude@repo", "two", thread="y")
    assert run(["ack", "1"], data, repo, "antigravity", "b2").returncode == 0
    r = run(["messages", "--brief"], data, repo)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.decode().splitlines()
    assert len(lines) == 2 and lines[0].endswith("acked: antigravity@repo") and lines[1].endswith("acked: -")
    out = json.loads(run(["messages", "--thread", "y", "--json"], data, repo).stdout)
    assert [m["body"] for m in out] == ["two"] and out[0]["acked"] == []
    r = run(["messages", "--status", "NOPE"], data, repo)
    assert r.returncode != 0 and "invalid choice" in r.stderr.decode()
