<p align="center">
  <img src="assets/agent-bus-banner.jpg" alt="agent-bus" width="100%">
</p>

last updated: 2026-09-22

# agent-bus

A tinkerer's repo. Nothing here has been reviewed by anyone but myself 
and the agents that use it (and the code changes so often that I wouldn't treat this anywhere
close to a high signal of confidence). Before running a single command below, secure your own
system, read what the daemon and the deploy script do, and harden to your
own standard - or don't run it at all. No guarantees. But if you see ideas or patterns in here 
you want to reuse, feel free to do so.

A message bus for coding agents. One daemon; participants (Claude Code,
Antigravity, Codex, a person, a script) leave messages for each other
across repos and machines. Four tools, `send`, `inbox`, `ack`, `who`, over
MCP streamable HTTP at `/mcp` and JSON at `/api/`.

- `PROTOCOL.md`: the contract. Addresses, envelope, delivery, identity,
  working rules.
- `docs/design/system.md`: how the daemon implements it, the security
  boundaries, the failure modes.
- `VISION.md`: what this will and will not become.
- `docs/plan/roadmap.md`: what is next.

Everything needs `uv`.

## Host

One always-on machine runs the daemon as a systemd system service under
its own uid. State lives in `/var/lib/agent-bus`. The admin token never
leaves it, so enrolling and auditing run under `sudo`.

```
sudo UV="$(command -v uv)" scripts/deploy-host.sh
sudo systemctl enable --now agent-bus
sudo -k
```

Re-run the script on each release. Moving an existing development daemon
to the system unit is `docs/ops/cutover.md`. For development,
`agent-bus-daemon` in the foreground keeps state under `$AGENTS` with no
uid boundary.

## Clients

Export `AGENTS` (default: `.agents` under your home). Enroll each harness
once per machine, on the host:

```
sudo /opt/agent-bus/bin/agent-bus enroll claude > "$AGENTS/tokens/claude" && chmod 600 "$AGENTS/tokens/claude"
sudo /opt/agent-bus/bin/agent-bus enroll antigravity > "$AGENTS/tokens/antigravity" && chmod 600 "$AGENTS/tokens/antigravity"
sudo /opt/agent-bus/bin/agent-bus enroll owner > "$AGENTS/tokens/owner" && chmod 600 "$AGENTS/tokens/owner"
```

For another machine, add `--machine <name>`, carry only that token over,
and set `AGENT_BUS_URL` there.

### Claude Code

```
export AGENT_BUS_TOKEN_CLAUDE="$(cat "$AGENTS/tokens/claude")"
export AGENT_BUS_URL=http://<host>:8765     # omit on the host
claude plugin marketplace add fblissjr/agent-bus
claude plugin install agent-bus@agent-bus
```

This adds the MCP server, the `agent-bus` skill, and SessionStart and
UserPromptSubmit hooks that print your address and any unread mail with
the ack command to run after reading. In this checkout with the plugin
installed, disable the repo's own `.mcp.json` server
(`disabledMcpjsonServers` in `.claude/settings.local.json`) or it connects
twice.

### Antigravity

```
agy plugin install <path-to-clone>
agy mcp add --header "Authorization: Bearer $(cat "$AGENTS/tokens/antigravity")" agent-bus http://127.0.0.1:8765/mcp
```

The `PreInvocation` hook in `hooks.json` delivers and acks.

### Codex

Planned. Manual form today: `codex mcp add --url ... --bearer-token-env-var AGENT_BUS_TOKEN`.

### Anything else

Add the MCP URL with the bearer header and read `agent-bus://protocol`, or
POST to `/api/` with the CLI or curl.

## CLI

```
agent-bus whoami
agent-bus send --to claude@my-repo --status REQUEST --verb review --thread <slug> "..."   # --thread required
agent-bus inbox [--ack]
agent-bus ack 12 13
agent-bus who
agent-bus show <thread>
agent-bus hook --agent claude|antigravity
sudo /opt/agent-bus/bin/agent-bus show <thread> --repo <repo> --audit
sudo /opt/agent-bus/bin/agent-bus ledger
sudo /opt/agent-bus/bin/agent-bus register            # HTML audit page into internal/register/
sudo /opt/agent-bus/bin/agent-bus enroll <harness> [--machine <name>]
```

Harness and instance are detected from the environment, or set with
`--as`. `AGENT_BUS_URL` overrides the daemon URL; `AGENT_BUS_TOKEN_<HARNESS>`
overrides the token file. For the CLI on your own PATH:
`uv tool install --editable .`.

## Layout

```
src/agent_bus/store.py         schema, identity, ledger, membership, projections
src/agent_bus/daemon.py        MCP server, /api routes, token-to-participant auth
src/agent_bus/cli.py           the agent-bus command, also what the hooks run
src/agent_bus/register.html    template for `agent-bus register`
systemd/agent-bus.service      the system unit
scripts/deploy-host.sh         the per-release root step
docs/ops/cutover.md            moving a live host to the system unit
.claude-plugin/                Claude Code plugin manifest and marketplace
plugin.json, hooks.json        Antigravity plugin manifest and hook
.mcp.json, hooks/, skills/     Claude plugin content
tests/                         hook against a stub server; daemon on a real socket
docs/design/                   system.md, identity-hardening.md, groups.md
```

## Tests

```
uv run --group dev pytest
```

## License

MIT, see [LICENSE](LICENSE).
