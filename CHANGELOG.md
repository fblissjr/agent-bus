# Changelog

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
- PROTOCOL.md: addresses, envelope, working rules, and the spec direction.
