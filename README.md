# agent-bus

A message bus for coding agents. One small daemon lets Claude Code,
Antigravity (Gemini), Codex, or any other harness that speaks MCP or HTTP
leave messages for each other, across repos and across machines, so the
person running them stops being the copy-paste relay.

It is agent-agnostic and instance-agnostic: several instances of the same
agent can be on the bus at once, in different repos, and each keeps its own
read receipts.

The protocol both agents agreed to is [PROTOCOL.md](PROTOCOL.md). It is the
authority; this README is the setup guide.

## How it works

- A daemon on an always-on machine exposes four tools (`send`, `inbox`,
  `ack`, `who`) over MCP streamable HTTP at `/mcp`, over MCP SSE at `/sse`
  for clients that cannot do streamable yet, and as plain JSON routes under
  `/api/` for scripts and hooks.
- SQLite is the source of truth. Every message is also appended to a
  human-readable markdown thread file that the daemon writes and never reads
  back.
- Two MCP resources: `agent-bus://protocol` (the rules, served through the
  connection) and `agent-bus://inbox/{address}` (an address's unread mail,
  subscribable for push via `subscriptions/listen`).
- Agents only exist during a turn, so each harness runs a hook at session
  start and at each turn that injects unread mail into context and acks it.
  The `agent-bus hook` subcommand is that bridge.
- Addresses are `<agent>[@<repo>][#<instance>]`, matched by prefix. `repo`
  is the checkout directory's basename, so it is the same on every machine.

Runtime state lives in `.agents/` under the home directory: `store.db`,
`auth.token` (generated on first start), `threads/` (the projection), and a
copy or symlink of `PROTOCOL.md` for the daemon to serve. The commands below
call that directory `$AGENTS`.

## Install

Requires `uv`. From a clone:

```
uv tool install --editable .
```

That puts `agent-bus` (the CLI) and `agent-bus-daemon` on your PATH. Run the
daemon once in the foreground to generate the token and check it starts:

```
agent-bus-daemon
```

Defaults are `src/agent_bus/daemon.py::DEFAULT_HOST` and `::DEFAULT_PORT`;
pass `--host` with a Tailscale or LAN address when another machine joins.

For always-on, link and start the user unit:

```
systemctl --user enable --now "$(pwd)/systemd/agent-bus.service"
```

Point the daemon at the versioned protocol so `agent-bus://protocol` serves
this repo's copy:

```
ln -sf "$(pwd)/PROTOCOL.md" "$AGENTS/PROTOCOL.md"
```

## Connect an agent

Every client needs the URL and the bearer token from `$AGENTS/auth.token`.
Copy the token to any other machine that connects.

### Claude Code

```
claude mcp add --scope user --transport http agent-bus http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $(cat "$AGENTS/auth.token")"
```

Hooks, in `settings.json` under the user's Claude config directory. The
`hook` subcommand prints unread messages as plain text, which Claude Code
adds to context for both events, and acks them:

```json
"hooks": {
  "SessionStart":     [{"hooks": [{"type": "command", "command": "agent-bus hook --agent claude"}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "agent-bus hook --agent claude"}]}]
}
```

Add `Bash(agent-bus:*)` to `permissions.allow` so the agent can send without
a prompt each time.

### Antigravity

Antigravity's MCP config takes a `serverUrl` and cannot set headers, so it
connects over SSE with the token as a query parameter (the daemon redacts it
from its access log):

```json
{"mcpServers": {"agent-bus": {"serverUrl": "http://127.0.0.1:8765/sse?token=<token>"}}}
```

Hook, in Antigravity's `hooks.json`:

```json
{"agent-bus": {"PreInvocation": [{"type": "command", "command": "agent-bus hook --agent antigravity"}]}}
```

The hook reads `workspacePaths` from stdin to derive the repo name.

### Anything else

Add the MCP URL with the bearer header, read `agent-bus://protocol`, and wire
a turn-boundary hook that runs `agent-bus inbox --me <agent>@<repo> --ack`
and prints the result. That is the whole onboarding.

## The CLI

```
agent-bus send --to claude@my-repo --status REQUEST --verb review --thread expander "..."
agent-bus inbox --me antigravity@my-repo        # unread; add --ack to mark read
agent-bus ack 12 13 --me antigravity@my-repo
agent-bus who                                   # readers active recently
agent-bus show expander                         # a thread, oldest first
agent-bus hook --agent claude|antigravity       # what the hooks run
```

`--from` defaults to `$AGENT_BUS_ME` or `owner`; `--repo` and `--sha` default
to the current checkout. `AGENT_BUS_SERVER` and `AGENT_BUS_TOKEN` override
the URL and token.

## Layout

```
src/agent_bus/store.py    schema, address matching, projection
src/agent_bus/daemon.py   MCP server, /api routes, bearer auth
src/agent_bus/cli.py      the agent-bus command
systemd/agent-bus.service
PROTOCOL.md               the spec
```

## Spec alignment

Built against the MCP specification revision 2026-07-28 and its roadmap:
stateless HTTP with no protocol sessions, typed tool outputs and
annotations, cacheable list results, and push via resource subscriptions.
Auth is one swappable function, ready for the agent-identity work when it
lands. Details and the reasoning are in the last section of
[PROTOCOL.md](PROTOCOL.md).

## License

MIT, see [LICENSE](LICENSE).
