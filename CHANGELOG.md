# Changelog

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
