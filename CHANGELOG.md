# Changelog

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
