"""SQLite store for the bus: messages, receipts, participants, and the ledger, plus
the write-only projections (markdown threads and ledger.jsonl).

Address grammar is `<harness>[@<repo>][#<instance>]`. A message reaches a reader
when every component the message's `to` names equals the reader's component;
components the `to` leaves out are wildcards. `all` as the harness matches every
harness. So `to=claude@X` reaches `claude@X#a1` and `claude@X#b2`, while
`to=claude@X#a1` reaches only that instance.

A participant is a harness on a machine and holds one token. The harness and
machine halves of a sender are verified from that token; the instance, host,
pid, and cwd are reported by the client and recorded as such. Receipts are
scoped to `harness@repo`, so a broadcast to `claude@X` is settled for every
Claude in X by the first one to ack it, and a new session does not inherit the
backlog. The ledger records which instance did what.

Threads are the groups. A harness may read a thread only if it sent in it or
was addressed in it; the ledger is read only by the owner. Nothing here is
readable by another uid: projections exist for the owner with sudo, not for
participants."""

import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

DEFAULT_STATE_DIR = Path.home() / ".agents"

STATUSES = ("REQUEST", "ANSWER", "DONE", "BLOCKED", "FYI")
VERBS = ("review", "implement", "test", "answer")
DEFAULT_THREAD = "general"
DEFAULT_INSTANCE = "default"
# An instance counts as active in who() if it called any tool within this window.
ACTIVE_WINDOW = timedelta(minutes=15)

# repo, thread, and machine become path components or address parts.
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HARNESS = re.compile(r"^[a-z][a-z0-9_-]*$")
ADDRESS = re.compile(r"^(?P<agent>[a-z][a-z0-9_-]*)(?:@(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*))?(?:#(?P<instance>[A-Za-z0-9][A-Za-z0-9._-]*))?$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    sender TEXT NOT NULL,
    harness TEXT NOT NULL,
    recipient TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    to_repo TEXT,
    to_instance TEXT,
    repo TEXT NOT NULL,
    thread TEXT NOT NULL,
    status TEXT NOT NULL,
    verb TEXT,
    sha TEXT,
    files TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    reader TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (message_id, reader)
);
CREATE TABLE IF NOT EXISTS seen (
    address TEXT PRIMARY KEY,
    harness TEXT NOT NULL,
    host TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS participants (
    name TEXT NOT NULL,
    machine TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (name, machine)
);
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    harness TEXT NOT NULL,
    instance TEXT,
    host TEXT,
    pid INTEGER,
    cwd TEXT,
    peer TEXT,
    message_id INTEGER,
    body_hash TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    machine TEXT,
    subject TEXT,
    hash_version INTEGER NOT NULL DEFAULT 1
);
"""

COLUMNS = ("id", "ts", "from", "to", "repo", "thread", "status", "verb", "sha", "files", "body")
SELECT = "SELECT id, ts, sender, recipient, repo, thread, status, verb, sha, files, body FROM messages"
GENESIS = "0" * 64
# What each hash version covers. The version is inside its own field set so a row
# cannot be re-labelled to a looser version. Rows are never rewritten; a store
# holds rows of every version it has ever written, each verified under its own.
LEDGER_FIELDS_V1 = ("seq", "ts", "event", "harness", "instance", "host", "pid", "cwd", "peer", "message_id", "body_hash", "prev_hash")
LEDGER_FIELDS_V2 = LEDGER_FIELDS_V1 + ("machine", "subject", "hash_version")
HASHED_FIELDS = {1: LEDGER_FIELDS_V1, 2: LEDGER_FIELDS_V2}
LEDGER_VERSION = 2
LEDGER_FIELDS = LEDGER_FIELDS_V2

# Functional form because `from` is a keyword. This is the tools' output schema.
Message = TypedDict("Message", {"id": int, "ts": str, "from": str, "to": str, "repo": str, "thread": str, "status": str, "verb": str | None, "sha": str | None, "files": list[str], "body": str})


class Reader(TypedDict):
    address: str
    harness: str
    host: str | None
    last_seen: str


@dataclass(frozen=True)
class Identity:
    """Who is calling: `harness` and `machine` verified from the token, the rest reported by the client."""
    harness: str
    instance: str | None = None
    host: str | None = None
    pid: int | None = None
    cwd: str | None = None
    peer: str | None = None
    machine: str | None = None

    def address(self, repo):
        """The derived sender address. The caller's own checkout wins over the message's repo.
        Every component obeys the address grammar, so a sender is always reachable and parseable:
        a checkout name or a reported session id that would not fit is refused, not stored."""
        own = check_slug("checkout name", Path(self.cwd).name) if self.cwd else repo
        return f"{self.harness}@{own}#{check_slug('instance', self.instance or DEFAULT_INSTANCE)}"


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_address(text):
    m = ADDRESS.match(text or "")
    if m is None:
        raise ValueError(f"bad address {text!r}: expected <harness>[@<repo>][#<instance>]")
    return m["agent"], m["repo"], m["instance"]


def receipt_scope(me):
    agent, repo, _ = parse_address(me)
    return f"{agent}@{repo}" if repo else agent


def check_slug(kind, text):
    if not SLUG.match(text or ""):
        raise ValueError(f"bad {kind} {text!r}: letters, digits, '.', '_', '-' only")
    return text


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def inbox_uri(address):
    """Resource URI for an inbox. `#` is a URI fragment delimiter, so the instance label is percent-encoded."""
    return "agent-bus://inbox/" + quote(address, safe="@")


def row_to_message(row):
    msg = dict(zip(COLUMNS, row))
    msg["files"] = json.loads(msg["files"])
    return msg


def format_message(msg):
    head = f"## {msg['id']} | {msg['from']} -> {msg['to']} | {msg['ts']} | {msg['status']}"
    if msg["verb"]:
        head += f" {msg['verb']}"
    lines = [head, f"repo: {msg['repo']}" + (f" @ {msg['sha']}" if msg["sha"] else "")]
    if msg["files"]:
        lines.append("files: " + ", ".join(msg["files"]))
    lines += ["", msg["body"].rstrip(), ""]
    return "\n".join(lines)


def ledger_hash(row):
    fields = HASHED_FIELDS[row.get("hash_version") or 1]
    return hashlib.sha256(json.dumps({k: row.get(k) for k in fields}, sort_keys=True).encode()).hexdigest()


def locked(fn):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Store:
    def __init__(self, state_dir=DEFAULT_STATE_DIR):
        # Everything this process creates is owner-only from the first byte, including the
        # SQLite write-ahead log and shared-memory files, which SQLite creates with the umask.
        os.umask(0o077)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.threads_dir = self.state_dir / "threads"
        self.ledger_path = self.state_dir / "ledger.jsonl"
        db_path = self.state_dir / "store.db"
        # MCP tools run in worker threads and the /api routes on the event loop;
        # one connection behind one lock serves both and keeps projection appends ordered.
        self.db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        os.chmod(db_path, 0o600)
        self.lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.migrate()

    def migrate(self):
        """Older stores open in place. 0.3: senders were unverified and presence had a
        different shape. 0.4: participants were keyed by name alone and the ledger was
        version 1; existing participants belong to this machine, and existing ledger
        rows keep verifying under the version-1 field list."""
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}
        if "harness" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN harness TEXT NOT NULL DEFAULT ''")
            self.db.execute("UPDATE messages SET harness = substr(sender, 1, instr(sender || '@', '@') - 1) WHERE harness = ''")
        if "address" not in {r[1] for r in self.db.execute("PRAGMA table_info(seen)")}:
            self.db.execute("DROP TABLE seen")
            self.db.execute("CREATE TABLE seen (address TEXT PRIMARY KEY, harness TEXT NOT NULL, host TEXT, ts TEXT NOT NULL)")
        if "machine" not in {r[1] for r in self.db.execute("PRAGMA table_info(participants)")}:
            self.db.executescript("""
                ALTER TABLE participants RENAME TO participants_v1;
                CREATE TABLE participants (name TEXT NOT NULL, machine TEXT NOT NULL, token_hash TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (name, machine));
            """)
            self.db.execute("INSERT INTO participants SELECT name, ?, token_hash, ts FROM participants_v1", (socket.gethostname(),))
            self.db.execute("DROP TABLE participants_v1")
        ledger_columns = {r[1] for r in self.db.execute("PRAGMA table_info(ledger)")}
        for column, decl in (("machine", "TEXT"), ("subject", "TEXT"), ("hash_version", "INTEGER NOT NULL DEFAULT 1")):
            if column not in ledger_columns:
                self.db.execute(f"ALTER TABLE ledger ADD COLUMN {column} {decl}")

    # participants

    @locked
    def enroll(self, name, machine, identity):
        """Mint a participant token, replacing any earlier one for that harness on that machine. Returns the plaintext once."""
        if not HARNESS.match(name or "") or name in ("all", "admin"):
            raise ValueError(f"bad participant name {name!r}")
        check_slug("machine", machine)
        token = secrets.token_urlsafe(32)
        self.db.execute("BEGIN")
        try:
            self.db.execute("INSERT INTO participants (name, machine, token_hash, ts) VALUES (?, ?, ?, ?) ON CONFLICT(name, machine) DO UPDATE SET token_hash = excluded.token_hash, ts = excluded.ts", (name, machine, token_hash(token), now()))
            self.record("enroll", identity, subject=f"{name}@{machine}")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return token

    @locked
    def verify(self, token):
        """The (harness, machine) a token belongs to, or None."""
        row = self.db.execute("SELECT name, machine FROM participants WHERE token_hash = ?", (token_hash(token),)).fetchone()
        return (row[0], row[1]) if row else None

    # messages

    def touch(self, identity, address):
        self.db.execute("INSERT INTO seen (address, harness, host, ts) VALUES (?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET host = excluded.host, ts = excluded.ts", (address, identity.harness, identity.host, now()))

    def own_address(self, identity, me):
        agent, _, _ = parse_address(me)
        if agent != identity.harness:
            raise ValueError(f"{me!r} is not an address of {identity.harness}")

    @locked
    def send(self, identity, to, repo, status, body, verb=None, sha=None, files=None, thread=None):
        # Everything is checked before the first write: a refused send stores nothing,
        # ledgers nothing, and projects nothing.
        to_agent, to_repo, to_instance = parse_address(to)
        check_slug("repo", repo)
        thread = check_slug("thread", thread or DEFAULT_THREAD)
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}: one of {', '.join(STATUSES)}")
        if verb is not None and verb not in VERBS:
            raise ValueError(f"bad verb {verb!r}: one of {', '.join(VERBS)}")
        if sha is not None and not isinstance(sha, str):
            raise ValueError("sha must be a string")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("body is empty")
        files = list(files or [])
        for f in files:
            if not isinstance(f, str) or not f.strip() or f.startswith("/") or f.startswith("~") or "\n" in f:
                raise ValueError(f"bad file path {f!r}: repo-relative paths only")
        sender = identity.address(repo)
        self.db.execute("BEGIN")
        try:
            cur = self.db.execute(
                "INSERT INTO messages (ts, sender, harness, recipient, to_agent, to_repo, to_instance, repo, thread, status, verb, sha, files, body)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), sender, identity.harness, to, to_agent, to_repo, to_instance, repo, thread, status, verb, sha, json.dumps(files), body),
            )
            self.touch(identity, sender)
            msg = self._get(cur.lastrowid)
            self.record("send", identity, message_id=msg["id"], body=body, subject=to)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.project(msg)
        return msg

    def _get(self, message_id):
        row = self.db.execute(SELECT + " WHERE id = ?", (message_id,)).fetchone()
        return row_to_message(row) if row else None

    @locked
    def inbox(self, identity, me):
        self.own_address(identity, me)
        agent, repo, instance = parse_address(me)
        self.touch(identity, me)
        rows = self.db.execute(
            SELECT + " m WHERE (to_agent = ? OR to_agent = 'all')"
            " AND (to_repo IS NULL OR to_repo = ?)"
            " AND (to_instance IS NULL OR to_instance = ?)"
            " AND NOT (harness = ? AND substr(sender, instr(sender, '#') + 1) = ?)"
            " AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.message_id = m.id AND r.reader = ?)"
            " ORDER BY id",
            (agent, repo, instance, agent, instance or DEFAULT_INSTANCE, receipt_scope(me)),
        ).fetchall()
        return [row_to_message(r) for r in rows]

    @locked
    def ack(self, identity, me, ids):
        """Record receipts for `me`. Only messages that exist and are addressed to `me` can be
        acked; anything else is refused before any write, so a receipt is always a real read."""
        self.own_address(identity, me)
        agent, repo, instance = parse_address(me)
        try:
            ids = sorted({int(i) for i in ids})
        except (TypeError, ValueError):
            raise ValueError("ids must be integers")
        if not ids:
            raise ValueError("no message ids given")
        marks = ", ".join("?" * len(ids))
        addressed = {r[0] for r in self.db.execute(
            f"SELECT id FROM messages WHERE id IN ({marks}) AND (to_agent = ? OR to_agent = 'all')"
            " AND (to_repo IS NULL OR to_repo = ?) AND (to_instance IS NULL OR to_instance = ?)",
            (*ids, agent, repo, instance),
        )}
        refused = [i for i in ids if i not in addressed]
        if refused:
            raise ValueError(f"not addressed to {me}, or no such message: {', '.join(map(str, refused))}")
        self.touch(identity, me)
        ts = now()
        scope = receipt_scope(me)
        self.db.execute("BEGIN")
        try:
            self.db.executemany("INSERT OR IGNORE INTO receipts (message_id, reader, ts) VALUES (?, ?, ?)", [(i, scope, ts) for i in ids])
            for i in ids:
                self.record("ack", identity, message_id=i, subject=scope)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return len(ids)

    @locked
    def who(self):
        cutoff = (datetime.now(timezone.utc) - ACTIVE_WINDOW).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rows = self.db.execute("SELECT address, harness, host, ts FROM seen WHERE ts >= ? ORDER BY ts DESC", (cutoff,)).fetchall()
        return [{"address": a, "harness": h, "host": host, "last_seen": ts} for a, h, host, ts in rows]

    @locked
    def thread(self, identity, repo, thread, audit=False):
        """A thread is a group: readable by a harness that sent in it or was addressed in it, and by the owner's audit token."""
        check_slug("repo", repo)
        check_slug("thread", thread)
        if not audit:
            member = self.db.execute(
                "SELECT 1 FROM messages WHERE repo = ? AND thread = ? AND (harness = ? OR to_agent = ? OR to_agent = 'all') LIMIT 1",
                (repo, thread, identity.harness, identity.harness),
            ).fetchone()
            if member is None:
                raise ValueError(f"{identity.harness} is not a participant in {repo}/{thread}")
        rows = self.db.execute(SELECT + " WHERE repo = ? AND thread = ? ORDER BY id", (repo, thread)).fetchall()
        return [row_to_message(r) for r in rows]

    # ledger

    def record(self, event, identity, message_id=None, body=None, subject=None):
        """Append one hash-chained row. Callers hold the lock and an open transaction, so seq
        and prev_hash are consistent and the row lands with the event it records or not at all.
        The jsonl projection is appended inside that window; a crash between the two leaves the
        table authoritative and the projection one row short, never the reverse."""
        last = self.db.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        row = {
            "seq": (last[0] + 1) if last else 1, "ts": now(), "event": event, "harness": identity.harness, "instance": identity.instance,
            "host": identity.host, "pid": identity.pid, "cwd": identity.cwd, "peer": identity.peer,
            "message_id": message_id, "body_hash": hashlib.sha256(body.encode()).hexdigest() if body else None, "prev_hash": last[1] if last else GENESIS,
            "machine": identity.machine, "subject": subject, "hash_version": LEDGER_VERSION,
        }
        row["hash"] = ledger_hash(row)
        self.db.execute(
            f"INSERT INTO ledger ({', '.join(LEDGER_FIELDS)}, hash) VALUES ({', '.join(':' + f for f in LEDGER_FIELDS)}, :hash)",
            row,
        )
        with self.ledger_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        os.chmod(self.ledger_path, 0o600)

    @locked
    def ledger(self, limit=200, harness=None):
        where, args = ("WHERE harness = ?", [harness]) if harness else ("", [])
        rows = self.db.execute(f"SELECT {', '.join(LEDGER_FIELDS)}, hash FROM ledger {where} ORDER BY seq DESC LIMIT ?", args + [int(limit)]).fetchall()
        return [dict(zip(LEDGER_FIELDS + ("hash",), r)) for r in reversed(rows)]

    @locked
    def verify_ledger(self):
        """Walk the chain from genesis, each row under its own hash version; returns the first bad seq, or None when intact."""
        prev = GENESIS
        for r in self.db.execute(f"SELECT {', '.join(LEDGER_FIELDS)}, hash FROM ledger ORDER BY seq"):
            row = dict(zip(LEDGER_FIELDS + ("hash",), r))
            if row["prev_hash"] != prev or ledger_hash(row) != row["hash"]:
                return row["seq"]
            prev = row["hash"]
        return None

    @locked
    def export(self):
        """Everything the owner's register needs, in one admin-only read: the live schema and
        every row of every table. Token hashes are never included."""
        def rows(query):
            cur = self.db.execute(query)
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        tables = ("messages", "receipts", "seen", "participants", "ledger")
        schema = {t: [{"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])} for c in self.db.execute(f"PRAGMA table_info({t})") if c[1] != "token_hash"] for t in tables}
        messages = rows("SELECT * FROM messages ORDER BY id")
        for m in messages:
            m["files"] = json.loads(m["files"])
        participant_cols = [c["name"] for c in schema["participants"]]
        return {
            "schema": schema,
            "messages": messages,
            "receipts": rows("SELECT * FROM receipts ORDER BY ts, message_id"),
            "seen": rows("SELECT * FROM seen ORDER BY ts"),
            "participants": rows(f"SELECT {', '.join(participant_cols)} FROM participants ORDER BY ts"),
            "ledger": rows("SELECT * FROM ledger ORDER BY seq"),
        }

    def project(self, msg):
        path = self.threads_dir / msg["repo"] / f"{msg['thread']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            if f.tell() == 0:
                f.write(f"# {msg['repo']} / {msg['thread']}\n\nProjection written by agent-bus. Do not edit; send with the CLI or MCP tools.\n\n")
            f.write(format_message(msg) + "\n")
        os.chmod(path, 0o600)
