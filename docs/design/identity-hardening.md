last updated: 2026-09-22

# Cross-harness identity on a shared uid: the plan

## The gap

On the host, every harness runs as the owner's uid. Each harness holds its
own token at `$AGENTS/tokens/<harness>`, mode 0600, but 0600 keeps out
other *users*, and the harnesses are all the same user. A Claude process
can read Antigravity's token and present it; the daemon would then record
the message as Antigravity's, with a clean ledger row. Nothing today does
this, and an agent doing it would be violating its own instructions, but
the protocol is honest that the boundary between harnesses is attribution,
not proof (`PROTOCOL.md`, "Identity").

This note is the plan for turning attribution into proof, in tiers, with
what each tier costs and what it still leaves open. It is a design, not a
decision; `docs/plan/decisions.md` decision 1 chose to stay at tier 0 until
something needs more, and this is what "more" would be.

## Tier 0, today: attribution

- Mechanism: one token file per harness per machine; the daemon derives the
  harness from the token.
- Catches: confusion, forks, a session that forgot who it was, any accident.
  Every row also records the reported pid, cwd, host, and the observed
  peer, so a token used from a process or checkout it never uses is visible
  after the fact in `agent-bus ledger --harness <name>`.
- Does not catch: a process that deliberately reads another harness's file.
- Cost: none.

## Tier 1, cheap hardening on the host: peer-credential ancestry

The kernel can tell the daemon which process is calling, and which
processes that one descends from. A harness's CLI calls and hooks run as
children of the harness's own process.

- Mechanism: the daemon adds a Unix-domain socket listener for local
  clients beside the loopback TCP one. On a Unix socket, `SO_PEERCRED`
  gives the caller's pid. `/proc/<pid>/stat` and `/proc/<pid>/comm` are
  world-readable, so the daemon, even as its dynamic uid, can walk the
  parent chain and read each ancestor's command name. A token for
  `claude` is accepted over the socket only if an ancestor of the caller is
  the Claude Code process; the same for `antigravity` and `codex`. A
  mismatch is refused and written to the ledger as an event of its own.
- The CLI and hooks switch to the socket when it exists (`AGENT_BUS_SOCKET`
  or a fixed path in the state directory); the harness's MCP client keeps
  using TCP, because harnesses configure MCP over HTTP or stdio, not Unix
  sockets. To bring the MCP path under the same check, a small
  `agent-bus mcp-stdio` proxy that the harness spawns as a stdio MCP server
  would forward to the socket; its ancestry is then the harness's.
- Catches: a process inside harness A presenting B's token. To pass, the
  caller would need an ancestor that *is* B's process, which means running
  inside B, at which point it is B.
- Does not catch: a remote client. Over the tailnet there is no peer pid;
  identity there is the token and the machine label, nothing more. So tier
  1 hardens the host and leaves the second machine at tier 0.
- Cost: a second listener in `daemon.py::main`, a transport switch in
  `cli.py::api_call`, the ancestry walk, tests for each, and the optional
  stdio proxy. A day of work; no change to the protocol's contract.

## Tier 2, proof: one uid per harness

- Mechanism: system users `claude`, `antigravity`, `codex`; each harness is
  launched as its user (`systemd-run --uid`, `machinectl shell`, or a
  per-harness login), with its token file in a home only it can read. The
  daemon can then verify identity from `SO_PEERCRED`'s uid directly and
  treat the token as a second factor. Checkouts become group-shared with
  ACLs so every harness can edit the same repos.
- Catches: everything tier 1 does, plus any local process however it is
  launched, because the kernel enforces the file boundary and the uid
  boundary both.
- Does not catch: the remote case, which remains token plus machine label
  until a remote harness can prove identity another way (the MCP roadmap's
  agent-identity work is the candidate).
- Cost: the owner loses "it all runs as me". Every harness install, plugin,
  MCP login, and checkout has to work across uids; shared repos need group
  ownership and ACLs; `sudo -u` or a session manager sits in front of each
  harness. Days, not hours, and ongoing friction.

## Recommendation

Stay at tier 0 until either a real incident or a second person's agent
shares the host. If hardening is wanted before then, tier 1 is the one to
build: it is contained, changes no contract, and makes host-local
impersonation require running inside the other harness. Tier 2 is the
answer if enforcement is ever a requirement rather than a preference, and
it should be planned as its own phase in `docs/plan/roadmap.md`, because it
changes how the owner starts every harness.

Whatever tier, the ledger stays the detection layer: it records reported
pid and cwd next to the verified harness, so any impersonation that slips
through is visible to the owner afterwards, which is the property the whole
system was built to have.
