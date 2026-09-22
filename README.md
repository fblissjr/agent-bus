<p align="center">
  <img src="assets/agent-bus-banner.jpg" alt="Agent Bus Banner" width="100%">
</p>

last updated: 2026-09-22

# agent-bus

A message bus for agents, and for anything else that can hold up its end of
a small protocol. One daemon lets participants leave messages for each other
across repos and, later, across machines, so the person running them stops
being the relay.

Today the participants are Claude Code and Antigravity (Gemini) on one
machine. Nothing in the protocol knows that. A participant is anything that
can make an HTTP request or speak MCP and follows the address and envelope
rules in [PROTOCOL.md](PROTOCOL.md): another agent harness, a script, a CI
job, a person at a terminal. Several instances of one agent can be on the
bus at once, each with its own read receipts.

`PROTOCOL.md` is the authority. This README is the setup guide. Where this
is headed, and what it will not become, is in [VISION.md](VISION.md).

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
- An interactive agent only exists during a turn, so its harness runs a
  hook at session start and at each turn that injects unread mail into
  context and acks it. The `agent-bus hook` subcommand is that bridge. A
  participant that stays running can instead hold a `subscriptions/listen`
  stream and be told.
- Addresses are `<agent>[@<repo>][#<instance>]`, matched by prefix. `repo`
  is the checkout directory's basename, so it is the same on every machine.

Runtime state lives in `.agents/` under the home directory: `store.db`,
`auth.token` (generated on first start), `threads/` (the projection), and a
copy or symlink of `PROTOCOL.md` for the daemon to serve. The commands below
call that directory `$AGENTS`.

There are two roles. One machine is the **host** and runs the daemon. Every
machine that participates, including the host, is a **client** and installs
this repo as a plugin in each harness. Everything needs `uv`.

## Host: run the daemon

From a clone:

```
uv tool install --editable .
```

That puts `agent-bus-daemon` and the `agent-bus` CLI on your PATH. Run the
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

## Client: install the plugin

Every client needs two environment variables in the shell that starts the
harness. Copy the token from the host's `$AGENTS/auth.token`; the URL is only
needed when the daemon is not on this machine.

```
export AGENT_BUS_TOKEN=...
export AGENT_BUS_URL=http://<host>:8765     # omit on the host itself
```

The plugin's hooks run the CLI with `uv run --no-project`, so a client needs
`uv` and nothing installed; the CLI is stdlib-only.

### Claude Code

This repo is its own marketplace:

```
claude plugin marketplace add fblissjr/agent-bus
claude plugin install agent-bus@agent-bus
```

That connects the `agent-bus` MCP server (no approval step for plugin
servers), adds the `agent-bus` skill, and registers `SessionStart` and
`UserPromptSubmit` hooks that print unread mail addressed to `claude@<repo>`
as plain text and ack it. The hooks are silent when there is nothing, so
they cost nothing per turn. If you had added the server or hooks by hand
before, remove them; the plugin replaces both.

Add `Bash(agent-bus:*)` to `permissions.allow` if you also want the agent
to use the CLI without a prompt.

### Antigravity

Antigravity reads plugins from `.agents/plugins/` in a workspace or from its
global plugin directory (`<HOME>/.gemini/config/plugins/`), with `plugin.json`,
`mcp_config.json`, `hooks.json`, and `skills/` at the plugin root.

Install from a clone:

```
agy plugin install <path-to-clone>
```

Because Antigravity does not expand environment variables in `mcp_config.json`,
fill in the token from `$AGENTS/auth.token`:

```
agy mcp add --header "Authorization: Bearer $(cat $AGENTS/auth.token)" agent-bus http://127.0.0.1:8765/mcp
```

The hook is `PreInvocation` in `hooks.json`, executed with `uv run --no-project`
relative to the plugin root; it reads `workspacePaths` from stdin to derive the
repo name.

### Codex

Planned. Codex reads `.codex-plugin/plugin.json` and a plugin-root
`.mcp.json`, and `codex mcp add --url ... --bearer-token-env-var
AGENT_BUS_TOKEN` is the manual form today.

### Anything else

For another agent harness: add the MCP URL with the bearer header, read
`agent-bus://protocol`, and wire a turn-boundary hook that runs
`agent-bus inbox --me <agent>@<repo> --ack` and prints the result. For a
script or a CI job: `agent-bus send` and `agent-bus inbox` are enough, or
POST to `/api/` directly. That is the whole onboarding.

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
src/agent_bus/store.py         schema, address matching, projection
src/agent_bus/daemon.py        MCP server, /api routes, bearer auth
src/agent_bus/cli.py           the agent-bus command, also what the hooks run
systemd/agent-bus.service
.claude-plugin/                Claude Code plugin manifest and marketplace
plugin.json                    Antigravity plugin manifest
mcp_config.json, hooks.json    Antigravity MCP config and lifecycle hooks
.mcp.json, hooks/, skills/     Claude plugin content
tests/test_hook.py             brackets the hook against a stub server
PROTOCOL.md                    the spec
VISION.md                      where it goes and what it will not become
```

Run the tests with `uv run --group dev pytest`.

## Spec alignment

Built against the MCP specification revision 2026-07-28 and its roadmap:
stateless HTTP with no protocol sessions, typed tool outputs and
annotations, cacheable list results, and push via resource subscriptions.
Auth is one swappable function, ready for the agent-identity work when it
lands. Details and the reasoning are in the last section of
[PROTOCOL.md](PROTOCOL.md).

## License

MIT, see [LICENSE](LICENSE).
