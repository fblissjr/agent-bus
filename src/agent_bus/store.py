"""SQLite store for the bus, plus the write-only markdown projection of each thread.

Address grammar is `<agent>[@<repo>][#<instance>]`. A message reaches a reader
when every component the message's `to` names equals the reader's component;
components the `to` leaves out are wildcards. `all` as the agent matches every
agent. So `to=claude@X` reaches `claude@X` and `claude@X#review`, while
`to=claude@X#review` reaches only that one.
"""

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

AGENTS_DIR = Path.home() / ".agents"
DB_PATH = AGENTS_DIR / "store.db"
THREADS_DIR = AGENTS_DIR / "threads"

STATUSES = ("REQUEST", "ANSWER", "DONE", "BLOCKED", "FYI")
DEFAULT_THREAD = "general"
# A reader counts as active in who() if it called any tool within this window.
ACTIVE_WINDOW = timedelta(minutes=15)

# repo and thread become path components under THREADS_DIR.
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ADDRESS = re.compile(r"^(?P<agent>[a-z][a-z0-9_-]*)(?:@(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*))?(?:#(?P<instance>[A-Za-z0-9][A-Za-z0-9._-]*))?$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    sender TEXT NOT NULL,
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
    reader TEXT PRIMARY KEY,
    ts TEXT NOT NULL
);
"""

COLUMNS = ("id", "ts", "from", "to", "repo", "thread", "status", "verb", "sha", "files", "body")
SELECT = "SELECT id, ts, sender, recipient, repo, thread, status, verb, sha, files, body FROM messages"

# Functional form because `from` is a keyword. This is the tools' output schema.
Message = TypedDict("Message", {"id": int, "ts": str, "from": str, "to": str, "repo": str, "thread": str, "status": str, "verb": str | None, "sha": str | None, "files": list[str], "body": str})


class Reader(TypedDict):
    reader: str
    last_seen: str


def inbox_uri(address):
    """Resource URI for an inbox. `#` is a URI fragment delimiter, so the instance label is percent-encoded."""
    return "agent-bus://inbox/" + quote(address, safe="@")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_address(text):
    m = ADDRESS.match(text or "")
    if m is None:
        raise ValueError(f"bad address {text!r}: expected <agent>[@<repo>][#<instance>]")
    return m["agent"], m["repo"], m["instance"]


def check_slug(kind, text):
    if not SLUG.match(text or ""):
        raise ValueError(f"bad {kind} {text!r}: letters, digits, '.', '_', '-' only")
    return text


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


def locked(fn):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Store:
    def __init__(self, db_path=DB_PATH, threads_dir=THREADS_DIR):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.threads_dir = threads_dir
        # MCP tools run in worker threads and the /api routes on the event loop;
        # one connection behind one lock serves both and keeps projection appends ordered.
        self.db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def touch(self, reader):
        self.db.execute("INSERT INTO seen (reader, ts) VALUES (?, ?) ON CONFLICT(reader) DO UPDATE SET ts = excluded.ts", (reader, now()))

    @locked
    def send(self, sender, to, repo, status, body, verb=None, sha=None, files=None, thread=None):
        parse_address(sender)
        to_agent, to_repo, to_instance = parse_address(to)
        check_slug("repo", repo)
        thread = check_slug("thread", thread or DEFAULT_THREAD)
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}: one of {', '.join(STATUSES)}")
        if not body or not body.strip():
            raise ValueError("body is empty")
        files = list(files or [])
        ts = now()
        cur = self.db.execute(
            "INSERT INTO messages (ts, sender, recipient, to_agent, to_repo, to_instance, repo, thread, status, verb, sha, files, body)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, sender, to, to_agent, to_repo, to_instance, repo, thread, status, verb, sha, json.dumps(files), body),
        )
        self.touch(sender)
        msg = self._get(cur.lastrowid)
        self.project(msg)
        return msg

    def _get(self, message_id):
        row = self.db.execute(SELECT + " WHERE id = ?", (message_id,)).fetchone()
        return row_to_message(row) if row else None

    @locked
    def inbox(self, me):
        agent, repo, instance = parse_address(me)
        self.touch(me)
        rows = self.db.execute(
            SELECT + " m WHERE (to_agent = ? OR to_agent = 'all')"
            " AND (to_repo IS NULL OR to_repo = ?)"
            " AND (to_instance IS NULL OR to_instance = ?)"
            " AND sender != ?"
            " AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.message_id = m.id AND r.reader = ?)"
            " ORDER BY id",
            (agent, repo, instance, me, me),
        ).fetchall()
        return [row_to_message(r) for r in rows]

    @locked
    def ack(self, me, ids):
        parse_address(me)
        self.touch(me)
        ts = now()
        self.db.executemany("INSERT OR IGNORE INTO receipts (message_id, reader, ts) VALUES (?, ?, ?)", [(int(i), me, ts) for i in ids])
        return len(ids)

    @locked
    def who(self):
        cutoff = (datetime.now(timezone.utc) - ACTIVE_WINDOW).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rows = self.db.execute("SELECT reader, ts FROM seen WHERE ts >= ? ORDER BY ts DESC", (cutoff,)).fetchall()
        return [{"reader": r, "last_seen": ts} for r, ts in rows]

    @locked
    def thread(self, repo, thread):
        check_slug("repo", repo)
        check_slug("thread", thread)
        rows = self.db.execute(SELECT + " WHERE repo = ? AND thread = ? ORDER BY id", (repo, thread)).fetchall()
        return [row_to_message(r) for r in rows]

    def project(self, msg):
        path = self.threads_dir / msg["repo"] / f"{msg['thread']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            if f.tell() == 0:
                f.write(f"# {msg['repo']} / {msg['thread']}\n\nProjection written by agent-bus. Do not edit; send with the CLI or MCP tools.\n\n")
            f.write(format_message(msg) + "\n")
