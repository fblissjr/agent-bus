#!/usr/bin/env python3
"""agent-bus CLI: thin client for the agent-bus daemon (/api routes).

Conforms to PROTOCOL.md. Identity is computed from the environment on every
call and never remembered: the harness from which token file is used, the
instance from the harness's own session id, the repo from the checkout.

The admin path (enroll, ledger, show --audit) reads the admin token from the
daemon's state directory. Under sudo that is the system state directory;
$STATE_DIRECTORY exists only inside the service. Nothing is ever written
under root's home: enroll as root prints the token and the owner places it.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_SERVER = os.environ.get("AGENT_BUS_SERVER") or os.environ.get("AGENT_BUS_URL") or "http://127.0.0.1:8765"
# The client-side directory: participant tokens live here. `AGENTS` is also the name
# the docs use, so an exported AGENTS makes every documented command run as written.
AGENTS = Path(os.environ.get("AGENTS") or (Path.home() / ".agents"))
TOKENS = AGENTS / "tokens"
SYSTEM_STATE_DIR = Path("/var/lib/agent-bus")
HARNESSES = ("claude", "antigravity", "codex", "owner")


class BusError(Exception):
    """A request that could not be made or was refused. main() reports it; the hook swallows it."""


# identity

def detect_harness():
    """Which participant this process speaks for, from the harness's own environment."""
    if os.environ.get("AGENT_BUS_HARNESS"):
        return os.environ["AGENT_BUS_HARNESS"]
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    if os.environ.get("ANTIGRAVITY_AGENT") or os.environ.get("ANTIGRAVITY_TRAJECTORY_ID") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID"):
        return "antigravity"
    if os.environ.get("CODEX_SESSION_ID"):
        return "codex"
    return "owner"


def detect_instance(harness, hook_in=None):
    """The harness's session id, shortened. Hook stdin wins; then the environment; else None (daemon uses 'default')."""
    raw = None
    if hook_in:
        raw = hook_in.get("session_id") or hook_in.get("conversationId")
    raw = raw or os.environ.get("AGENT_BUS_INSTANCE") or {
        "claude": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "antigravity": os.environ.get("ANTIGRAVITY_TRAJECTORY_ID") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID"),
        "codex": os.environ.get("CODEX_SESSION_ID"),
    }.get(harness)
    return raw[:8] if raw else None


def detect_repo(start=None):
    cur = Path(start or Path.cwd())
    for p in [cur] + list(cur.parents):
        if (p / ".git").exists():
            return p.name
    return cur.name


def detect_sha():
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def address(harness, repo, instance):
    return f"{harness}@{repo}#{instance or 'default'}"


def get_token(harness):
    env = os.environ.get(f"AGENT_BUS_TOKEN_{harness.upper()}")
    if env:
        return env.strip()
    path = TOKENS / harness
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        raise BusError(f"no token for {harness}: run 'agent-bus enroll {harness}' or set AGENT_BUS_TOKEN_{harness.upper()}")
    except OSError as e:
        raise BusError(f"cannot read {path}: {e.strerror}")


def admin_state_dir(explicit=None):
    """Where the daemon keeps admin.token: the flag, the environment, else the system directory under root and the development one otherwise."""
    if explicit:
        return Path(explicit)
    if os.environ.get("AGENT_BUS_STATE_DIR"):
        return Path(os.environ["AGENT_BUS_STATE_DIR"])
    return SYSTEM_STATE_DIR if os.geteuid() == 0 else AGENTS


def get_admin_token(state_dir):
    env = os.environ.get("AGENT_BUS_ADMIN_TOKEN")
    if env:
        return env.strip()
    path = Path(state_dir) / "admin.token"
    try:
        return path.read_text().strip()
    except OSError as e:
        raise BusError(f"no admin token at {path} ({e.strerror}); the admin path runs where the daemon keeps its state, under sudo on the host")


def enrolled_token_destination(name, machine, euid=None, this_host=None):
    """Where an enrolled token is written, or None to print it instead. Never under root's
    home, and never for another machine: the owner moves those by hand."""
    euid = os.geteuid() if euid is None else euid
    this_host = this_host or socket.gethostname()
    if euid == 0 or machine != this_host:
        return None
    return TOKENS / name


# transport

def api_call(endpoint, token, method="GET", payload=None, instance=None):
    url = f"{DEFAULT_SERVER}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "agent-bus-cli/2",
        "X-Bus-Host": socket.gethostname(),
        "X-Bus-Pid": str(os.environ.get("CLAUDE_PID") or os.getppid()),
        "X-Bus-Cwd": os.getcwd(),
    }
    if instance:
        headers["X-Bus-Instance"] = instance
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            err_msg = json.loads(err_msg).get("error", err_msg)
        except ValueError:
            pass
        raise BusError(f"({e.code}) {err_msg}")
    except urllib.error.URLError as e:
        raise BusError(f"cannot reach agent-bus daemon at {DEFAULT_SERVER}: {e.reason}")


class Caller:
    """Everything a command needs to speak as one participant instance. The token is
    read only when a call is made, so audit commands never need a participant token."""

    def __init__(self, harness=None, hook_in=None):
        self.harness = harness or detect_harness()
        self.instance = detect_instance(self.harness, hook_in)
        self.repo = detect_repo(hook_in.get("cwd") if hook_in and hook_in.get("cwd") else None)
        self._token = None

    @property
    def token(self):
        if self._token is None:
            self._token = get_token(self.harness)
        return self._token

    @property
    def me(self):
        return address(self.harness, self.repo, self.instance)

    def call(self, endpoint, method="GET", payload=None):
        return api_call(endpoint, self.token, method, payload, self.instance)


def format_envelope(msg, me=None):
    head = f"## {msg['id']} | {msg['from']} -> {msg['to']} | {msg['ts']} | {msg['status']}"
    if msg.get("verb"):
        head += f" {msg['verb']}"
    if me:
        head += "  (you)" if msg["from"] == me else (f"  (another {me.split('@')[0]})" if msg["from"].split("@")[0] == me.split("@")[0] else "")
    lines = [head, f"repo: {msg['repo']}" + (f" @ {msg['sha']}" if msg.get("sha") else "")]
    if msg.get("files"):
        lines.append("files: " + ", ".join(msg["files"]))
    lines += ["", msg["body"].rstrip(), ""]
    return "\n".join(lines)


# commands

def cmd_send(args):
    c = Caller(args.harness)
    repo = args.repo or c.repo
    sha = args.sha if args.sha is not None else detect_sha()
    thread = args.thread
    if not thread:
        if sys.stdin.isatty():
            try:
                thread = input("Thread slug [general]: ").strip() or "general"
            except (EOFError, KeyboardInterrupt):
                sys.exit(1)
        else:
            thread = "general"
    body = args.body
    if not body:
        if not sys.stdin.isatty():
            body = sys.stdin.read().strip()
        else:
            raise BusError("body is required (pass argument, --body, or pipe via stdin)")
    if not body:
        raise BusError("body cannot be empty")
    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
    payload = {"to": args.to, "repo": repo, "status": args.status, "body": body, "verb": args.verb, "sha": sha, "files": files, "thread": thread}
    res = c.call("/api/send", method="POST", payload=payload)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"Sent message #{res['id']} from {res['from']} to {res['to']} ({res['repo']}/{res['thread']})")


def cmd_inbox(args):
    c = Caller(args.harness)
    me = args.me or c.me
    msgs = c.call("/api/inbox", method="POST", payload={"me": me})
    if args.format == "json":
        print(json.dumps(msgs, indent=2))
    elif args.format == "ids":
        for m in msgs:
            print(m["id"])
    else:
        if not msgs:
            if not args.quiet:
                print(f"Inbox empty for {me}.")
        else:
            for m in msgs:
                print(format_envelope(m, me))
    if args.ack and msgs:
        ids = [m["id"] for m in msgs]
        c.call("/api/ack", method="POST", payload={"me": me, "ids": ids})
        if not args.quiet and args.format != "json":
            print(f"Acked {len(ids)} message(s).")


def cmd_ack(args):
    c = Caller(args.harness)
    me = args.me or c.me
    ids = [int(i) for i in args.ids]
    res = c.call("/api/ack", method="POST", payload={"me": me, "ids": ids})
    print(f"Acked {res.get('acked', len(ids))} message(s) for {me}.")


def cmd_who(args):
    c = Caller(args.harness)
    readers = c.call("/api/who", method="GET")
    if args.json:
        print(json.dumps(readers, indent=2))
    elif not readers:
        print("No active instances seen recently.")
    else:
        print(f"{'ADDRESS':<48} {'HOST':<12} {'LAST SEEN (UTC)':<22}")
        print("-" * 84)
        for r in readers:
            print(f"{r['address']:<48} {(r.get('host') or ''):<12} {r['last_seen']:<22}")


def cmd_show(args):
    c = Caller(args.harness)
    repo = args.repo or c.repo
    thread = args.thread or "general"
    query = urllib.parse.urlencode({"repo": repo, "thread": thread})
    token = get_admin_token(admin_state_dir(args.state_dir)) if args.audit else c.token
    msgs = api_call(f"/api/thread?{query}", token, instance=c.instance)
    if args.json:
        print(json.dumps(msgs, indent=2))
    elif not msgs:
        print(f"Thread {repo}/{thread} is empty.")
    else:
        for m in msgs:
            print(format_envelope(m, c.me))


def cmd_whoami(args):
    c = Caller(args.harness)
    res = c.call(f"/api/whoami?{urllib.parse.urlencode({'repo': c.repo})}")
    print(res["address"] if not args.json else json.dumps(res, indent=2))


def cmd_enroll(args):
    admin = get_admin_token(admin_state_dir(args.state_dir))
    machine = args.machine or socket.gethostname()
    res = api_call("/api/enroll", admin, method="POST", payload={"name": args.name, "machine": machine})
    path = enrolled_token_destination(args.name, machine)
    if path is None:
        # Printed, not written: under root the file would land in root's home, and a token
        # for another machine is carried there by the owner.
        print(res["token"])
        print(f"enrolled {args.name} on {machine}; place the token above at $AGENTS/tokens/{args.name} on that machine, mode 600", file=sys.stderr)
        return
    TOKENS.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(res["token"] + "\n")
    print(f"Enrolled {args.name} on {machine}; token written to {path}")


def cmd_ledger(args):
    admin = get_admin_token(admin_state_dir(args.state_dir))
    query = urllib.parse.urlencode({k: v for k, v in (("limit", args.limit), ("harness", args.harness_filter)) if v})
    res = api_call(f"/api/ledger?{query}", admin)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    if res["first_bad_seq"] is not None:
        print(f"WARNING: ledger chain broken at seq {res['first_bad_seq']}")
    for r in res["rows"]:
        who = f"{r['harness']}@{r.get('machine') or '-'}#{r['instance'] or 'default'}"
        print(f"{r['seq']:>6}  {r['ts']}  {r['event']:<6} {who:<34} {(r.get('subject') or ''):<36} msg={r['message_id'] or '-'}")


def git_log():
    """The checkout's commits, oldest first, for the register's timeline; empty outside a repo."""
    try:
        res = subprocess.run(["git", "log", "--reverse", "--date=iso-strict", "--format=%h|%ad|%s"], capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [dict(zip(("sha", "ts", "subject"), line.split("|", 2))) for line in res.stdout.splitlines() if line]


def cmd_register(args):
    """Render the owner's audit page: every message, receipt, presence row, participant, and
    ledger row through the admin export, into a self-contained HTML file. The template ships
    with the package; the output carries the data and is never repo content."""
    admin = get_admin_token(admin_state_dir(args.state_dir))
    data = api_call("/api/export", admin)
    data["commits"] = git_log()
    template = Path(__file__).with_name("register.html").read_text()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    stamp = data["generated"][:10]
    out = Path(args.out) if args.out else Path("internal") / "register" / f"{stamp}-register.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace("__DATA__", blob))
    print(f"{out}  ({len(data['messages'])} messages, {len(data['ledger'])} ledger rows)")


# hooks

def read_hook_stdin():
    try:
        if sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def hook_notice(c, me):
    """Unread mail for `me`, formatted for injection, or None. Any failure is None: a
    hook runs on every prompt, and a down daemon must not become a per-prompt error."""
    try:
        msgs = c.call("/api/inbox", method="POST", payload={"me": me})
        if not msgs:
            return None
        formatted = "\n---\n".join(format_envelope(m, me) for m in msgs)
        return f"[agent-bus] You have {len(msgs)} new message(s) on the bus:\n\n{formatted}", [m["id"] for m in msgs]
    except (BusError, ValueError, KeyError, TypeError):
        return None


def hook_ack(c, me, ids):
    try:
        c.call("/api/ack", method="POST", payload={"me": me, "ids": ids})
    except BusError:
        pass


def cmd_hook(args):
    agent = args.agent.lower()
    hook_in = read_hook_stdin()
    if agent == "antigravity":
        ws = hook_in.get("workspacePaths") or []
        hook_in = dict(hook_in, cwd=ws[0]) if ws else hook_in
    c = Caller(agent, hook_in)
    me = c.me
    if agent == "antigravity":
        # Antigravity's PreInvocation injection is reliable and fires per model call: ack on delivery.
        found = hook_notice(c, me)
        if found:
            print(json.dumps({"injectSteps": [{"ephemeralMessage": found[0]}]}))
            hook_ack(c, me, found[1])
        else:
            print(json.dumps({}))
    elif agent == "claude":
        # SessionStart fires exactly when context is rebuilt, so it always says who you are.
        # No ack here: Claude Code runs UserPromptSubmit on prompts queued mid-turn but drops
        # the output, so the reader acks after reading and unread mail re-shows until then.
        lines = []
        if hook_in.get("hook_event_name") == "SessionStart":
            lines.append(f"[agent-bus] you are {me}")
        found = hook_notice(c, me)
        if found:
            ids = " ".join(str(i) for i in found[1])
            lines.append(f"{found[0]}\n\nAck after reading: agent-bus ack {ids} --me {me}")
        if lines:
            print("\n".join(lines))
    else:
        raise BusError(f"unknown agent for hook: {agent}")


def main():
    parser = argparse.ArgumentParser(prog="agent-bus", description="Inter-agent message bus CLI")
    parser.add_argument("--as", dest="harness", choices=HARNESSES, help="Speak as this harness (default: detected from the environment)")
    parser.add_argument("--state-dir", help="The daemon's state directory, for the admin path (default: the system directory under root, else the development one)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_send = subparsers.add_parser("send", help="Send a message")
    p_send.add_argument("body", nargs="?", help="Message body (or pass via stdin / --body)")
    p_send.add_argument("--body", dest="body_flag", help="Message body")
    p_send.add_argument("--to", required=True, help="Recipient address, e.g. claude@my-repo")
    p_send.add_argument("--repo", help="Repo the message is about (default: current checkout)")
    p_send.add_argument("--status", default="REQUEST", choices=["REQUEST", "ANSWER", "DONE", "BLOCKED", "FYI"])
    p_send.add_argument("--verb", choices=["review", "implement", "test", "answer"], help="Action verb for REQUEST")
    p_send.add_argument("--sha", help="Git commit SHA (default: HEAD)")
    p_send.add_argument("--files", help="Comma-separated repo-relative file paths")
    p_send.add_argument("--thread", help="Thread slug (prompts if interactive, default: general)")
    p_send.add_argument("--json", action="store_true", help="Output JSON response")
    p_send.set_defaults(func=lambda a: (setattr(a, "body", a.body_flag or a.body), cmd_send(a)))

    p_inbox = subparsers.add_parser("inbox", help="Check unacked messages")
    p_inbox.add_argument("--me", help="Address to read as (default: whoami)")
    p_inbox.add_argument("--ack", action="store_true", help="Ack returned messages on delivery")
    p_inbox.add_argument("--format", default="text", choices=["text", "json", "ids"], help="Output format")
    p_inbox.add_argument("--quiet", "-q", action="store_true", help="Suppress empty inbox notice")
    p_inbox.set_defaults(func=cmd_inbox)

    p_ack = subparsers.add_parser("ack", help="Ack messages by ID")
    p_ack.add_argument("ids", nargs="+", help="Message IDs to ack")
    p_ack.add_argument("--me", help="Address to ack as (default: whoami)")
    p_ack.set_defaults(func=cmd_ack)

    p_who = subparsers.add_parser("who", help="List active instances")
    p_who.add_argument("--json", action="store_true", help="Output JSON")
    p_who.set_defaults(func=cmd_who)

    p_show = subparsers.add_parser("show", help="Show a thread you are part of")
    p_show.add_argument("thread", nargs="?", default="general", help="Thread slug (default: general)")
    p_show.add_argument("--repo", help="Repo name (default: current repo basename)")
    p_show.add_argument("--audit", action="store_true", help="Read with the admin token (owner only)")
    p_show.add_argument("--json", action="store_true", help="Output JSON")
    p_show.set_defaults(func=cmd_show)

    p_whoami = subparsers.add_parser("whoami", help="Print the address this process speaks as")
    p_whoami.add_argument("--json", action="store_true", help="Output JSON")
    p_whoami.set_defaults(func=cmd_whoami)

    p_enroll = subparsers.add_parser("enroll", help="Mint a participant token with the admin token (once per harness per machine)")
    p_enroll.add_argument("name", choices=HARNESSES, help="Participant to enroll")
    p_enroll.add_argument("--machine", help="The machine the token is for (default: this one)")
    p_enroll.set_defaults(func=cmd_enroll)

    p_ledger = subparsers.add_parser("ledger", help="The audit trail (admin token only)")
    p_ledger.add_argument("--limit", type=int, default=200)
    p_ledger.add_argument("--harness", dest="harness_filter", help="Only this harness's rows")
    p_ledger.add_argument("--json", action="store_true", help="Output JSON")
    p_ledger.set_defaults(func=cmd_ledger)

    p_register = subparsers.add_parser("register", help="Render the owner's audit page from the admin export (admin token only)")
    p_register.add_argument("--out", help="Where to write the page (default: internal/register/<date>-register.html under the current directory)")
    p_register.set_defaults(func=cmd_register)

    p_hook = subparsers.add_parser("hook", help="Hook handler for agent session start / turn")
    p_hook.add_argument("--agent", required=True, choices=["claude", "antigravity"], help="Target agent")
    p_hook.set_defaults(func=cmd_hook)

    args = parser.parse_args()
    try:
        args.func(args)
    except BusError as e:
        if args.command == "hook":
            # A hook must never turn a missing or unreadable token into a per-prompt error.
            print(json.dumps({})) if args.agent == "antigravity" else None
            return
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
