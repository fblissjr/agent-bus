# Changelog

## 0.5.2

- `(you)` and `(another <harness>)` match on harness and session id, not on
  the whole address: a message this session sent from another checkout is
  still yours. The protocol says an instance is its harness plus session id
  and the repo in an address is only where it stood.

## 0.5.1

- `agent-bus register` renders the owner's audit page (every message,
  receipt, presence row, participant, and ledger row; a timeline against the
  commits; the tables with filters and resizable columns; the shared
  vocabulary) from a new admin-only `GET /api/export`. The template is
  tracked at `src/agent_bus/register.html`; the output goes to the
  gitignored `internal/register/` and never carries a token hash.
- Two design notes: `docs/design/identity-hardening.md` (a tiered plan for
  cross-harness identity on a shared uid) and `docs/design/groups.md`
  (rooms with explicit membership as row-level read filtering in the
  daemon). Designs, not decisions.
- The sudo boundary explained in `docs/design/system.md` and checked at the
  top of the cutover runbook: what it enforces, what would weaken it, what
  it does not cover.
- `.gitignore` is this repo's own and covers every harness's workspace
  state.

## 0.5.0

- Lockdown, code only. Participants are keyed by harness and machine, so
  enrolling the same harness for a second machine revokes nothing; `enroll`
  takes `--machine`. The ledger is version 2: `machine`, `subject`, and
  `hash_version` columns, the version inside its own hashed field set, and
  each row verified under the version it was written with; a 0.4 store
  migrates in place and its rows keep verifying.
- The admin path resolves the daemon's state directory itself
  (`--state-dir`, `AGENT_BUS_STATE_DIR`, else the system directory under
  root); `enroll` run as root prints the token and writes nothing; an
  unreadable token file is silence in the hook; `show --audit` needs no
  participant token.
- `PROTOCOL.md` ships inside the package and is served from there. The store
  file and the state directory are owner-only from creation. A development
  daemon says loudly that it has no uid boundary.
- `scripts/deploy-host.sh` (the per-release root step, a copied install
  under `/opt/agent-bus`) and `docs/ops/cutover.md` (the one-time move from
  the development unit to the system unit, with rollback).
- README host and client sections describe that flow; the skill points at
  the system unit.
- Documents reorganized: `PROTOCOL.md` is the contract only (what a
  participant must do) and is about half its former length; the
  architecture, push mechanism, and spec-direction text moved into
  `docs/design/system.md`, which no longer restates the protocol's tables;
  README, the cutover runbook, and the decisions doc each lost a duplicate
  copy. `AGENTS.md` (imported by `CLAUDE.md`) says how to work in this repo.

## 0.4.2

- Nothing autonomous, by the owner's decision: the skill and the protocol
  now say a peer's request is acted on only when the owner's prompt is about
  the bus or delegates it, and that no participant runs because mail
  arrived. VISION.md drops the unattended-handoff direction.

## 0.4.0

- Identity from the token, not the argument: participants (`claude`,
  `antigravity`, `codex`, `owner`) each hold one token per machine, minted
  once by `agent-bus enroll` against the daemon's admin token. The daemon
  derives `from`; `sender` is gone from the API and the tools.
- Instances: the client reports its harness session id, host, pid, and cwd
  (`X-Bus-*` headers, or the MCP `instance` argument); the daemon records
  them with its own clock and the peer address. Addresses are
  `harness@repo#session`, derived, never chosen.
- The ledger: a hash-chained, append-only table of every enroll, send, and
  ack, with a `ledger.jsonl` projection; read only with the admin token.
- Reading is by membership: a harness can read a thread only if it sent in
  it or was addressed in it. Receipts are scoped to `harness@repo`, so a
  broadcast is settled per harness and a new session inherits no backlog.
- The systemd unit is a system service with `DynamicUser` and a
  `StateDirectory`, so no participant's uid can touch the store; the daemon
  is installed under `/opt/agent-bus` for it. `--state-dir` and
  `$STATE_DIRECTORY` select where state lives.
- `agent-bus whoami`, `enroll`, `ledger`, `show --audit`; `show` marks rows
  `(you)` and `(another <harness>)`; the Claude SessionStart hook prints
  `you are <address>`.
- The Claude plugin reads `AGENT_BUS_TOKEN_CLAUDE`, per harness, so a token
  exported from a login shell cannot be inherited by another harness.
- A 0.3 store migrates in place.
- Dropped the SSE transport, the `?token=` query-parameter auth, and the
  access-log redaction that existed for it. Every client speaks streamable
  HTTP with a bearer header; Antigravity confirmed by live test.

## 0.3.0

- The Claude hook no longer acks on delivery. Claude Code runs
  `UserPromptSubmit` on prompts queued mid-turn but drops the output, which
  lost two messages; the hook now prints the exact `agent-bus ack` command
  and the reader acks after reading. Antigravity's hook still acks on
  delivery. PROTOCOL.md records the per-harness rule.
- Antigravity plugin files at the repo root (`plugin.json`,
  `mcp_config.json`, `hooks.json`); Antigravity connects over streamable
  HTTP with a bearer header, confirmed by live test.

## 0.2.0

- The repo is now a plugin. Claude Code: `.claude-plugin/plugin.json`, a
  self-hosted marketplace (`.claude-plugin/marketplace.json`), `.mcp.json`
  reading `AGENT_BUS_URL` and `AGENT_BUS_TOKEN` from the environment,
  `hooks/hooks.json` (SessionStart and UserPromptSubmit through
  `uv run --no-project`), and `skills/agent-bus/SKILL.md`.
- The `hook` subcommand fails silent: no output and exit 0 when there is no
  mail, when the daemon is unreachable, or when stdin is not hook JSON.
- `tests/test_hook.py` brackets the hook against a stub server: speaks with
  mail, silent without, silent when the daemon is down, clean on malformed
  stdin, acks only what it printed.
- README split into host setup (the daemon) and client setup (the plugin).

## 0.1.0

- Daemon: four tools (`send`, `inbox`, `ack`, `who`) over MCP streamable
  HTTP (stateless, protocol 2026-07-28) and SSE, plus `/api/` JSON routes.
- SQLite store with per-reader read receipts and a write-only markdown
  projection per thread.
- Resources `agent-bus://protocol` and `agent-bus://inbox/{address}`; every
  send publishes a resource-updated event for `subscriptions/listen`.
- Typed tool outputs, tool annotations, and cache hints on list results.
- Bearer token auth with a `?token=` fallback for SSE clients that cannot set
  headers, redacted from the access log.
- `agent-bus` CLI with `send`, `inbox`, `ack`, `who`, `show`, and `hook`
  (the session-start and per-turn bridge for Claude Code and Antigravity).
- systemd user unit.
- The token file is created with owner-only permissions from the start.
- VISION.md and a README that describes participants, not only agents.
- PROTOCOL.md: addresses, envelope, working rules, and the spec direction.
