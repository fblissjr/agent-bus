#!/usr/bin/env python3
"""agent-bus CLI: thin client for the agent-bus daemon (/api routes).

Conforms to <HOME>/.agents/PROTOCOL.md.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_SERVER = os.environ.get("AGENT_BUS_SERVER") or os.environ.get("AGENT_BUS_URL") or "http://127.0.0.1:8765"
TOKEN_PATH = Path.home() / ".agents" / "auth.token"


class BusError(Exception):
    """A request that could not be made or was refused. main() reports it; the hook swallows it."""


def get_token():
    env_token = os.environ.get("AGENT_BUS_TOKEN")
    if env_token:
        return env_token.strip()
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text().strip()
    raise BusError(f"auth token not found at {TOKEN_PATH} and AGENT_BUS_TOKEN not set")


def api_call(endpoint, method="GET", payload=None):
    url = f"{DEFAULT_SERVER}{endpoint}"
    headers = {
        "Authorization": f"Bearer {get_token()}",
        "User-Agent": "agent-bus-cli/1.0",
    }
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


def detect_repo():
    cur = Path.cwd()
    for p in [cur] + list(cur.parents):
        if (p / ".git").exists():
            return p.name
    return cur.name


def detect_sha():
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return None


def format_envelope(msg):
    head = f"## {msg['id']} | {msg['from']} -> {msg['to']} | {msg['ts']} | {msg['status']}"
    if msg.get("verb"):
        head += f" {msg['verb']}"
    lines = [head, f"repo: {msg['repo']}" + (f" @ {msg['sha']}" if msg.get("sha") else "")]
    if msg.get("files"):
        files = msg["files"] if isinstance(msg["files"], list) else json.loads(msg["files"])
        if files:
            lines.append("files: " + ", ".join(files))
    lines += ["", msg["body"].rstrip(), ""]
    return "\n".join(lines)


def cmd_send(args):
    repo = args.repo or detect_repo()
    sender = args.sender or os.environ.get("AGENT_BUS_ME") or "owner"
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
            sys.exit("Error: body is required (pass argument, --body, or pipe via stdin)")

    if not body:
        sys.exit("Error: body cannot be empty")

    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []

    payload = {
        "from": sender,
        "to": args.to,
        "repo": repo,
        "status": args.status,
        "body": body,
        "verb": args.verb,
        "sha": sha,
        "files": files,
        "thread": thread,
    }

    res = api_call("/api/send", method="POST", payload=payload)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"Sent message #{res['id']} to {res['to']} ({res['repo']}/{res['thread']})")


def cmd_inbox(args):
    me = args.me or os.environ.get("AGENT_BUS_ME") or f"owner@{detect_repo()}"
    msgs = api_call("/api/inbox", method="POST", payload={"me": me})

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
                print(format_envelope(m))

    if args.ack and msgs:
        ids = [m["id"] for m in msgs]
        api_call("/api/ack", method="POST", payload={"me": me, "ids": ids})
        if not args.quiet and args.format != "json":
            print(f"Acked {len(ids)} message(s).")


def cmd_ack(args):
    me = args.me or os.environ.get("AGENT_BUS_ME") or f"owner@{detect_repo()}"
    ids = [int(i) for i in args.ids]
    res = api_call("/api/ack", method="POST", payload={"me": me, "ids": ids})
    print(f"Acked {res.get('acked', len(ids))} message(s) for {me}.")


def cmd_who(args):
    readers = api_call("/api/who", method="GET")
    if args.json:
        print(json.dumps(readers, indent=2))
    else:
        if not readers:
            print("No active readers seen recently.")
        else:
            print(f"{'READER':<40} {'LAST SEEN (UTC)':<25}")
            print("-" * 65)
            for r in readers:
                print(f"{r['reader']:<40} {r['last_seen']:<25}")


def cmd_show(args):
    repo = args.repo or detect_repo()
    thread = args.thread or "general"
    query = urllib.parse.urlencode({"repo": repo, "thread": thread})
    msgs = api_call(f"/api/thread?{query}", method="GET")
    if args.json:
        print(json.dumps(msgs, indent=2))
    else:
        if not msgs:
            print(f"Thread {repo}/{thread} is empty.")
        else:
            for m in msgs:
                print(format_envelope(m))


def hook_notice(me):
    """Unread mail for `me`, formatted for injection, or None. Any failure is None: a
    hook runs on every prompt, and a down daemon must not become a per-prompt error.
    Print before ack so a failed ack means a repeat next turn, never a lost message."""
    try:
        msgs = api_call("/api/inbox", method="POST", payload={"me": me})
        if not msgs:
            return None
        formatted = "\n---\n".join(format_envelope(m) for m in msgs)
        return f"[agent-bus] You have {len(msgs)} new message(s) on the bus:\n\n{formatted}", [m["id"] for m in msgs]
    except (BusError, ValueError, KeyError, TypeError):
        return None


def hook_ack(me, ids):
    try:
        api_call("/api/ack", method="POST", payload={"me": me, "ids": ids})
    except BusError:
        pass


def cmd_hook(args):
    agent = args.agent.lower()
    if agent == "antigravity":
        # PreInvocation stdin carries workspacePaths; anything else falls back to cwd.
        repo = None
        try:
            if not sys.stdin.isatty():
                hook_in = json.loads(sys.stdin.read() or "{}")
                ws_paths = hook_in.get("workspacePaths") or []
                if ws_paths:
                    repo = Path(ws_paths[0]).name
        except (ValueError, AttributeError, TypeError, OSError):
            pass
        me = f"antigravity@{repo or detect_repo()}"
        found = hook_notice(me)
        if found:
            print(json.dumps({"injectSteps": [{"ephemeralMessage": found[0]}]}))
            hook_ack(me, found[1])
        else:
            print(json.dumps({}))

    elif agent == "claude":
        # No ack here: Claude Code runs this hook on prompts queued mid-turn but drops the
        # output, so an ack on delivery can lose mail. The reader acks after reading, and
        # unread mail re-shows on every prompt until it does.
        me = f"claude@{detect_repo()}"
        found = hook_notice(me)
        if found:
            ids = " ".join(str(i) for i in found[1])
            print(f"{found[0]}\n\nAck after reading: agent-bus ack {ids} --me {me}")
    else:
        raise BusError(f"unknown agent for hook: {agent}")


def main():
    parser = argparse.ArgumentParser(prog="agent-bus", description="Inter-agent message bus CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # send
    p_send = subparsers.add_parser("send", help="Send a message")
    p_send.add_argument("body", nargs="?", help="Message body (or pass via stdin / --body)")
    p_send.add_argument("--body", dest="body_flag", help="Message body")
    p_send.add_argument("--to", required=True, help="Recipient address, e.g. claude@my-repo")
    p_send.add_argument("--from", dest="sender", help="Sender address (default: $AGENT_BUS_ME or 'owner')")
    p_send.add_argument("--repo", help="Repo name (default: current repo basename)")
    p_send.add_argument("--status", default="REQUEST", choices=["REQUEST", "ANSWER", "DONE", "BLOCKED", "FYI"])
    p_send.add_argument("--verb", choices=["review", "implement", "test", "answer"], help="Action verb for REQUEST")
    p_send.add_argument("--sha", help="Git commit SHA (default: HEAD)")
    p_send.add_argument("--files", help="Comma-separated repo-relative file paths")
    p_send.add_argument("--thread", help="Thread slug (prompts if interactive, default: general)")
    p_send.add_argument("--json", action="store_true", help="Output JSON response")
    p_send.set_defaults(func=lambda a: (setattr(a, "body", a.body_flag or a.body), cmd_send(a)))

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="Check unacked messages")
    p_inbox.add_argument("--me", help="Caller address (default: $AGENT_BUS_ME or owner@<repo>)")
    p_inbox.add_argument("--ack", action="store_true", help="Ack returned messages on delivery")
    p_inbox.add_argument("--format", default="text", choices=["text", "json", "ids"], help="Output format")
    p_inbox.add_argument("--quiet", "-q", action="store_true", help="Suppress empty inbox notice")
    p_inbox.set_defaults(func=cmd_inbox)

    # ack
    p_ack = subparsers.add_parser("ack", help="Ack messages by ID")
    p_ack.add_argument("ids", nargs="+", help="Message IDs to ack")
    p_ack.add_argument("--me", help="Caller address (default: $AGENT_BUS_ME or owner@<repo>)")
    p_ack.set_defaults(func=cmd_ack)

    # who
    p_who = subparsers.add_parser("who", help="List active readers")
    p_who.add_argument("--json", action="store_true", help="Output JSON")
    p_who.set_defaults(func=cmd_who)

    # show
    p_show = subparsers.add_parser("show", help="Show a thread")
    p_show.add_argument("thread", nargs="?", default="general", help="Thread slug (default: general)")
    p_show.add_argument("--repo", help="Repo name (default: current repo basename)")
    p_show.add_argument("--json", action="store_true", help="Output JSON")
    p_show.set_defaults(func=cmd_show)

    # hook
    p_hook = subparsers.add_parser("hook", help="Hook handler for agent session start / turn")
    p_hook.add_argument("--agent", required=True, choices=["claude", "antigravity"], help="Target agent")
    p_hook.set_defaults(func=cmd_hook)

    args = parser.parse_args()
    try:
        args.func(args)
    except BusError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
