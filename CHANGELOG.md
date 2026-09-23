# Changelog

## 0.5.7

- The skill: a request with several asks gets an answer per ask, done,
  not done, or `BLOCKED`, so a thread never leaves a reader guessing which
  parts are open. From the first live review on the simulator, where one
  of three asks was answered and the other two went unmentioned.
- `docs/ops/release.md`, the per-release runbook, and
  `.github/workflows/tag-release.yml`, which tags `v<version>` from
  `pyproject.toml` on a push to main and never moves an existing tag.
- Antigravity installs the plugin from GitHub
  (`agy plugin install https://github.com/fblissjr/agent-bus`): a clean
  clone with no untracked files, confirmed against a live install. README,
  the cutover runbook, and the release runbook say so.
- The design doc, roadmap, and cutover runbook are synced.

## 0.5.6

- `agent-bus messages`, the sibling of `ledger`: every message across
  threads with the receipts that acked it, oldest first, as envelopes,
  one line each (`--brief`), or JSON. Structured filters (`--repo`,
  `--thread`, `--from`, `--to`, `--status`, `--since`, `--until`,
  `--limit`); sender and recipient match by prefix. Admin token on the
  host, no token on the simulator, refused to participants, who read a
  thread they belong to with `show`. Backed by a new admin-only
  `GET /api/messages`.

## 0.5.5

- `PROTOCOL.md` states its own scope: a store carrying the simulator marker
  is not the bus. It names `GET /api/whoami` beside the thread route, and
  keeps only the rule for who acks and for identity within a harness; the
  reasons stay in the design doc, which already carried them. Reviewed and
  agreed by Antigravity over the simulator, the first cross-harness round
  trip made without a daemon.

## 0.5.4

- The simulator: `agent-bus sim init [dir]` marks a directory (`data/`
  under the checkout by default) as simulator state, and with
  `AGENT_BUS_SIM_DIR` exported every CLI command, and so every hook, opens
  that store in-process instead of calling the daemon. The same store code
  runs, so addressing, receipts, thread membership, the ledger chain, and
  the projections behave as behind the daemon; identity is claimed from the
  environment or `--as`, and every ledger row written that way is stamped
  with `sim` as its machine and `direct` as its peer. `ledger`, `register`,
  and `show --audit` need no admin token there; `enroll` is refused. The
  switch is the variable alone: a daemon that is down never becomes a store
  opened directly, and an unmarked directory leaves the hook silent.
- The daemon refuses to serve a directory that carries the simulator
  marker, before it listens.
- Store writes take the lock at `BEGIN IMMEDIATE` and wait for another
  process's transaction, so many CLI processes writing one simulator store
  keep one ledger chain. No effect behind the daemon.
- `tests/test_sim.py`; the design doc's simulator section; README, the
  skill, `AGENTS.md`, and the Claude hook description say when you are on
  the simulator and what it does not prove.

## 0.5.3

- The daemon validates every argument before its first write, on the MCP
  and `/api/` paths alike: the sender's checkout name and session id must
  fit the address grammar like a recipient's, `verb` is one of the four,
  `files` are repo-relative strings, and an `ack` is accepted only for
  messages that exist and are addressed to the reader. A refused call
  stores nothing, ledgers nothing, and projects nothing.
- A message, a receipt, or an enrollment and its ledger row land in one
  transaction. A database error inside a call is a JSON error or a tool
  error, never a traceback.
- Own mail is excluded from the inbox by harness and session id, so a
  message a session sent from one checkout does not wait for it in another.
- The admin token takes no part in presence. Every file the daemon creates,
  including SQLite's write-ahead log and shared-memory files, is owner-only.
- `agent-bus send` requires `--thread`; a terminal is prompted, a process is
  refused, so nothing lands in `general` by omission.
- The cutover runbook and the design doc's sudo section check the owner's
  group membership beside the sudo prompt; README describes matching by
  component and the per-harness ack rule.
- The system unit is installed by `scripts/deploy-host.sh` as a root-owned
  copy under `/etc/systemd/system`, never enabled by a path into a
  checkout, and confines the daemon with systemd's sandboxing directives
  and an owner-only umask. The runbook ends each root session with
  `sudo -k`.

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
