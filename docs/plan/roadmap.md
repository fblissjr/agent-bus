last updated: 2026-09-23

# Roadmap

The ordered releases that turn the decisions in `decisions.md` into a system
the owner calls 1.0, with the mechanical steps each needs. Approved by the
owner on 2026-09-22. Both agents work from this file; when a phase ships,
its section is trimmed to what changed and the design doc absorbs the rest.

What shapes it: the Mac joins within weeks, so cross-machine is scheduled;
Codex joins only when first used, so it is designed but not scheduled; an
owner-facing view is probably wanted later, so it is designed but not
scheduled; and 1.0 means the host is locked down and one Mac harness has
completed a round trip. Nothing is autonomous, by the owner's ruling.

## 0.5.x lockdown, code only: shipped

0.5.0 shipped everything the uid boundary needs without touching the host:
participants keyed by harness and machine (`enroll --machine`); ledger v2
with `subject`, `machine`, and a `hash_version` inside its own hashed
fields, old rows still verifying; the admin path resolving its own state
directory and never writing under root; `PROTOCOL.md` served from the
package; owner-only store and directory modes; `scripts/deploy-host.sh`
and `docs/ops/cutover.md`. A critic pass found five ways the naive version
would have failed silently; all are in the design doc's operations section.

0.5.1 added `agent-bus register` over an admin-only `GET /api/export`, the
sudo explanation, and the identity-hardening and rooms designs. 0.5.2 made
`(you)` match on harness and session id rather than on the checkout. 0.5.3
made the daemon validate everything before the first write (sender grammar,
verb, files, acks limited to addressed mail), keep each write and its
ledger row in one transaction, exclude own mail by session, and create
every state file owner-only; the CLI requires `--thread`; the runbook and
the design doc check group membership beside the sudo prompt. 0.5.4 added
the simulator, a marked store the CLI and hooks use in-process with no
daemon, for test data on one machine while the host waits for its cutover.

The mechanism is `docs/design/system.md`; the host step is the runbook
below, still the owner's to run.

## Host cutover, a runbook the owner runs once

`docs/ops/cutover.md`, in this order:

1. Deploy 0.5.0 with `scripts/deploy-host.sh`. Do not start the system
   unit yet.
2. Stop the dev user unit and mask it, so its restart policy cannot fight
   the system unit for the port.
3. Checkpoint the store so the write-ahead log is folded in, then copy
   `store.db`, `ledger.jsonl`, and `threads/` into the state directory. Do
   not copy the admin token: the daemon mints a new one, which makes any
   copy in `$AGENTS` dead. Confirm ownership after first start.
4. Enable and start the system unit.
5. Clean `$AGENTS`: remove the admin token, the store, the ledger, and the
   projections; keep `tokens/`. Tokens survive because their hashes are in
   the copied store.
6. Every client updates its plugin, because hooks run the cached CLI.

Done when: only the system unit listens on the port; the state directory is
owned by the dynamic uid and owner-only; the ledger read under `sudo`
verifies intact and shows every pre-cutover row; the ledger read without
`sudo` is refused; a fresh Claude session's hook prints its identity line,
an old thread's history is readable by a member, and the MCP server connects
from a shell that exports the Claude token; `$AGENTS` holds only `tokens/`;
Gemini confirms `whoami` and a round trip. Rollback before step 5 is
re-enabling the user unit.

## 0.6.0 second machine

A Mac harness completes a round trip; host clients keep working.

- Exposure through `tailscale serve`, forwarding the tailnet to the daemon
  on loopback. The daemon's bind does not change, host clients are
  untouched, the port is reachable only from the tailnet, and the SDK's
  loopback DNS-rebinding protection stays active. No unit change, no
  firewall work.
- Enrollment on the host, `enroll claude --machine mac` under `sudo`, with
  only the resulting participant token moved to the Mac. The admin token
  never leaves the host.
- On the Mac: the plugin per harness, `AGENT_BUS_URL` set to the tailnet
  name, hooks verified.
- No daemon change expected; the CLI needs nothing beyond `--machine` from
  0.5.0. README client section, the design doc's networking sentence, and
  the changelog follow.

Done when: from the Mac, `whoami` resolves; a send's ledger row shows the
Mac's host, machine label, and a tailnet peer; a Mac Claude session's hook
delivers; the same repo basename resolves on both machines; host clients
are unaffected; the port is unreachable from a non-tailnet address.

## 1.0.0

No code. The tag when the cutover and 0.6.0 are both verified. The design
doc is re-read against the code once, and `decisions.md` is reduced to what
is still open.

## Designed, not scheduled

- Codex, when first used: its manifest at the repo root, `codex` in the
  CLI's harness and instance detection from a captured live environment,
  the hook shape confirmed against Codex's hook trust model, fixtures and
  hook-test arms, a README section.
- Owner view: shipped early, in 0.5.1, as `agent-bus register`, a page
  rendered by the CLI from the admin-only export into the gitignored
  `internal/register/`. No server, no long-lived process holding the admin
  token.
- Explicit thread membership (rooms): designed in
  `docs/design/groups.md`; any member can add a member, additions are
  ledger events, derived membership stays the default, and every read is
  filtered per caller in the daemon.
- Cross-harness identity as proof rather than attribution: designed in
  tiers in `docs/design/identity-hardening.md` (peer-credential ancestry
  over a Unix socket on the host; one uid per harness for enforcement).
- Per-session handles, if a session ever needs to be proven rather than
  attributed.
- Spec-side MCP auth when the agent-identity work lands;
  `src/agent_bus/daemon.py::participant_auth` is the swap point.

## Out, decided

Anything autonomous (wake-ups, sidecars acting on the bus, relay runners,
schedules); retention policies; a fifth tool; copying the admin token
anywhere.

## Rules for every phase

- Plugin content change means the version cascade (`pyproject.toml`, both
  manifests, `marketplace.json`, `CHANGELOG.md`) and every client updating
  its plugin.
- Gemini pushes and tags `v<version>` after each agreed release; the pusher
  is not the author of the range.
- One agent per checkout at a time; announce on the bus which file you are
  editing before editing it.
- `PROTOCOL.md` and `docs/design/system.md` change in the same commit as
  the behavior they describe.
