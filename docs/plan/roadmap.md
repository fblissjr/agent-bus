last updated: 2026-09-22

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

## 0.5.0 lockdown, code only

Everything the uid boundary needs, shippable and tested before anything on
the host moves. A critic pass found that the naive version breaks in places
the hooks' by-design silence would hide; each of those is a line below.

CLI (`src/agent_bus/cli.py`):

- The admin path resolves its state directory itself: `--state-dir` or
  `AGENT_BUS_STATE_DIR`, defaulting to the system state directory when run
  as root and to `$AGENTS` otherwise. `$STATE_DIRECTORY` exists only inside
  the service, never under `sudo`.
- `enroll` run as root prints the token and writes nothing; the owner
  redirects it into `$AGENTS/tokens/<name>` as themselves. Written under
  root it would land in root's home after the store had already revoked the
  owner's working token, and every hook would go silent.
- An unreadable token file is silence in the hook, not a traceback. The
  participant token is resolved lazily so `show --audit` never needs one.
- `ledger` prints the new `subject` column.

Store (`src/agent_bus/store.py`):

- Participants are keyed by name and machine, so enrolling the Mac's Claude
  does not revoke this machine's. `verify` still returns the harness; the
  machine label lands on the ledger row. Existing rows migrate with this
  host's name.
- Ledger v2: `subject` and `hash_version` columns, with the version inside
  the hashed fields so it cannot be forged; verification picks the field
  list per row so pre-0.5 rows keep verifying. Rows are never rewritten.
- The store file and the state directory are owner-only from creation, and
  the file-mode test covers both.

Daemon (`src/agent_bus/daemon.py`):

- `PROTOCOL.md` is served from package data, so the dynamic uid needs no
  copy and no symlink into the owner's home; the state-directory copy is a
  fallback only.
- A foreground daemon that falls back to `$AGENTS` says so loudly, so a
  hand-run daemon after cutover cannot silently recreate a store.

Also: the skill's `systemctl --user` becomes `systemctl` (plugin content,
so the version cascades); `scripts/deploy-host.sh` is the per-release root
step (copied `uv tool install` from the tag into `/opt/agent-bus`, with
uv's tool, bin, and python directories all under `/opt/agent-bus`, then a
service restart); `docs/ops/cutover.md` is the one-time runbook below with
a one-line rollback; README host and client sections say what the runbook
does; `docs/design/system.md` storage and identity sections follow the
code.

Tests: daemon arms for enroll-as-root printing only, lazy token on audit,
per-machine enrollment not revoking, ledger v2 chaining over a pre-0.5
fixture, store and directory modes; a hook arm for an unreadable token file.

Done when: the suite is green with those arms, the plugin validates, a
foreground daemon on a temporary state directory accepts the same harness
enrolled for two machines, and a store holding v1 and v2 ledger rows
verifies intact.

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
- Owner view, probably later: a read-only page rendered by the CLI under
  `sudo` from the audit path (threads, ledger, who). No server, no
  long-lived process holding the admin token.
- Explicit thread membership: any member can add a member, additions are
  ledger events, derived membership stays the default.
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
