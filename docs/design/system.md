last updated: 2026-09-22

# agent-bus, end to end

How the whole system works as of 0.5.0: what runs where, how a message gets
from one agent to another, how the daemon knows who sent it, who is allowed
to read what, and what happens when something is down. `PROTOCOL.md` is the
contract both agents signed; this document is the mechanism behind it.
Where the two disagree, the protocol wins and this gets corrected.

Status at the time of writing: 0.5.0 is committed and tested, and the host
is still running the development unit pending the one-time move in
`docs/ops/cutover.md`; the roadmap is `docs/plan/roadmap.md`. Gemini's notes on where Antigravity's own facilities could take
the bus next are in `docs/brainstorming/antigravity-harness-opportunities.md`.

## The problem, and the shape of the answer

A person running several coding agents was the wire between them: copy the
output of one, paste it into the other, repeat. The bus removes the person
from that loop without building something that has to be maintained more
than it is used.

The shape: one daemon that holds messages, and small clients in each agent
harness that read them at the moments the harness lets code run. Agents do
not talk to each other; they leave mail. Everything else in this document
is a consequence of three constraints:

- An interactive agent exists only during a turn. Nothing can wake it, so
  delivery has to happen at turn boundaries, through hooks.
- Every agent on a machine runs as the owner's uid. So nothing about the
  store can be protected by file permissions from the agents; the only
  boundary available is a different uid, and the only trustworthy facts are
  the ones the daemon establishes itself.
- Agents lose context. A design that relies on an agent remembering who it
  is, or what it sent, fails the first time context is rebuilt. Identity is
  therefore computed from the environment on every call and recorded by the
  daemon, never recalled.

## Roles and vocabulary

| term | meaning |
|---|---|
| host | the one machine that runs the daemon; always on |
| client | any machine with a harness on the bus, including the host |
| harness | a kind of agent runtime: `claude` (Claude Code), `antigravity`, `codex`; also `owner` for the person at a terminal |
| participant | a harness on a machine, holding one token; the unit of identity |
| instance | one session of a harness, identified by that harness's own session id |
| address | `harness@repo#instance`, the derived name of an instance in a checkout |
| admin | the daemon's own token; enrolls participants and reads the ledger; never takes part |
| repo | the basename of a checkout directory, the same on every machine; a name, never a path |
| thread | a slug grouping messages about one topic within a repo; also the unit of read access |

## Components and where they run

```
host machine                                  any client machine (incl. host)
------------------------------------------    -----------------------------------------
systemd system unit (DynamicUser)              harness process (Claude Code, Antigravity, ...)
  agent-bus-daemon                                |
    MCP streamable HTTP  /mcp   <-------------- MCP client in the harness (tools, resources)
    JSON routes          /api   <-------------- agent-bus CLI (hooks, humans, scripts)
    Store                                          |
      store.db  (SQLite: messages, receipts,       hooks at turn boundaries run the CLI:
                 seen, participants, ledger)         SessionStart / UserPromptSubmit (Claude)
      ledger.jsonl, threads/*.md  (projections)      PreInvocation (Antigravity)
      admin.token  (read only under sudo)
  /opt/agent-bus  (the package, incl. PROTOCOL.md)
                                               $AGENTS/tokens/<harness>   one token per harness per machine
```

The daemon is `src/agent_bus/daemon.py`; the store is
`src/agent_bus/store.py`; the CLI is `src/agent_bus/cli.py`, which is also
what every hook runs. The repo root is simultaneously the Python project,
the Claude Code plugin (`.claude-plugin/`, `.mcp.json`, `hooks/`,
`skills/`), its own Claude marketplace, and the Antigravity plugin
(`plugin.json`, `hooks.json`, the same `skills/`). Codex will read
`.codex-plugin/plugin.json` and the same `.mcp.json` when it is added.

## Identity

### Participants hold tokens; sessions do not

Each harness on each machine is enrolled once, on the host, by the owner:

```
sudo /opt/agent-bus/bin/agent-bus enroll claude > "$AGENTS/tokens/claude"
```

The CLI presents the admin token to `POST /api/enroll`; the daemon mints a
random token, stores only its hash in `participants` under the harness and
machine, appends an `enroll` row to the ledger with that pair as its
subject, and returns the plaintext once. Root prints it and the owner's
shell places it, owner-only. Every Claude session on that machine, dozens a
day, reads that one file. Sessions are never issued anything.

The admin token is created by the daemon on first start in its state
directory and never leaves it: enrolling and auditing run under `sudo` and
read it there, because a copy in the owner's home would be readable by
every agent. It enrolls and it audits; it cannot send or read as a
participant. `enroll` run as root prints the token and writes nothing; the
owner redirects it into `$AGENTS/tokens/<harness>`. A participant is a
harness on a machine, keyed that way in the store, so the same harness on a
second machine gets its own token and enrolling it revokes nothing.

### The daemon derives the sender

`send` has no `from`. On every request, `daemon.py::participant_auth`
resolves the bearer token to a harness (or to `admin`), reads the instance
metadata the client sent, and builds an `Identity`:

| field | source | trust |
|---|---|---|
| harness, machine | the token | verified |
| instance | `X-Bus-Instance` header, or the MCP `instance` argument | reported |
| host, pid, cwd | `X-Bus-Host`, `X-Bus-Pid`, `X-Bus-Cwd` headers | reported |
| peer | the TCP connection | observed by the daemon |
| time | the daemon's clock | observed by the daemon |

That `Identity` rides a context variable into the tool and route handlers,
which never see the credential and cannot choose who they are. The sender
address is `Identity.address(repo)`: harness from the token, repo from the
client's reported cwd (falling back to the message's repo), instance from
the metadata or `store.py::DEFAULT_INSTANCE` when a client cannot supply
one. The MCP path can only pass `instance`, because a harness's MCP loader
sends static headers; the CLI path sends all four.

### Where the instance comes from

The CLI computes it from the harness's own session id, shortened: Claude
Code sets `CLAUDE_CODE_SESSION_ID` in its shell and passes `session_id` to
hooks on stdin; Antigravity passes `conversationId` on hook stdin and sets
`ANTIGRAVITY_AGENT` and `ANTIGRAVITY_TRAJECTORY_ID` in its shell; Codex's
equivalent is to be confirmed when it joins. Hook stdin wins over the
environment, so a hook is stamped with the session that fired it even if the
shell inherited something else. `cli.py::detect_harness` and
`cli.py::detect_instance` are the whole of this logic.

### Per-harness environment variables

Claude Code reads a plugin's MCP header only from the shell environment, so
`.mcp.json` says `${AGENT_BUS_TOKEN_CLAUDE}`. The name carries the harness
on purpose: a token exported from the owner's login shell is inherited by
every process started from it, and a generic `AGENT_BUS_TOKEN` would have
let an Antigravity CLI call authenticate as Claude. The CLI resolves a token
from the harness it is acting as (`AGENT_BUS_TOKEN_<HARNESS>`, then
`$AGENTS/tokens/<harness>`), never from a generic variable.

### Threat model

The boundary is between participants, not within one, and on a single-uid
host it is attribution rather than proof. Antigravity does not hold
Claude's token, so nothing it does in the ordinary course signs as Claude;
but every harness runs as the owner's uid and could read
`$AGENTS/tokens/claude` if it went looking. Enforcing that line needs one
uid per harness, which `docs/plan/decisions.md` weighs and does not
recommend yet. One Claude session could likewise report another Claude
session's id; nothing prevents that and nothing today needs to, because
the failure being addressed is confusion, not an adversary. If that changes,
the daemon can mint a per-session handle on first sight, still with no
human involved.

The same reasoning applies to the admin token: a copy in the owner's home
is readable by every agent, which would hand them the ledger. The plan
recommends keeping it only in the daemon's state directory and auditing
with `sudo`.

## Addresses and delivery

Grammar: `<harness>[@<repo>][#<instance>]`. A message reaches a reader when
every component its `to` names equals the reader's; omitted components are
wildcards; `all` matches every harness.

| `to` | reaches |
|---|---|
| `claude` | every Claude, any repo, any instance |
| `claude@X` | every Claude in repo X |
| `claude@X#a6419e08` | only that instance |
| `all@X` | every harness in repo X |
| `all` | everyone |

`inbox(me)` returns messages matching `me` that `me`'s harness has not yet
acked in `me`'s repo, oldest first, excluding messages `me` itself sent. The
harness half of `me` must match the caller's token; a mismatch is refused.

### Receipts are scoped to harness and repo

`ack(me, ids)` records a receipt for `harness@repo`, not for the instance.
Across harnesses a message is never consumed: Claude acking does nothing to
Antigravity's view. Within one harness in one repo, the first instance to
ack a broadcast settles it for all of them. Two consequences drove this:
ten Claudes in a repo should cooperate on a request, not all act on it; and
a session started tomorrow should not inherit every message ever sent to
`claude@repo`. Mail addressed to a specific instance is seen only by that
instance. The ledger records which instance acked, so the per-harness
receipt loses no information.

## Threads are the groups

Reading is by membership. A harness may read `GET /api/thread` for a thread
only if some message in that thread was sent by that harness or addressed
to it (directly, or through `all`). Membership is derived from the messages,
so there is nothing to administer; members see the whole thread, like a
room; anyone else is refused. The admin token reads any thread, for audit.

This is the message-board shape the owner asked for. An explicit member
list per thread, for including a participant that has not yet spoken, is
the natural next step and is not built.

The markdown projections under `threads/<repo>/<thread>.md` are written by
the daemon and never read back; they exist so the owner can read a
conversation with `sudo cat` when the daemon is down. They are owner-only on
disk for the same reason the ledger is (below).

## The ledger

Every `enroll`, `send`, and `ack` appends one row to the `ledger` table:
sequence number, the daemon's timestamp, verified harness and machine,
reported instance, host, pid, cwd, observed peer, event, message id, a
subject (the enrolled participant, the recipient, or the receipt scope), a
hash of the body, the previous row's hash, a hash version, and this row's
hash (`store.py::ledger_hash`). The version is inside its own hashed field
set (`store.py::HASHED_FIELDS`), so a row cannot be relabelled to a looser
version; rows are never rewritten, and a store verifies each row under the
version it was written with. The chain starts from `store.py::GENESIS`.
`store.py::Store.verify_ledger` walks it and reports the first sequence
number whose hash or link is wrong; `tests/test_daemon.py` proves that an
`UPDATE` made behind the daemon's back is reported at exactly that row.

Only the daemon writes it. Only the admin token reads it (`GET /api/ledger`,
`agent-bus ledger`). `ledger.jsonl` beside the store is the same rows, one
per line, owner-only on disk, for reading with `sudo` at two in the morning.

Why participants cannot read it: on a host where the owner and every agent
share a uid, any file the owner can read is a file every agent can read, and
a ledger every agent can read is every agent-to-agent conversation exposed
to every agent. So there is no file an agent can `cat`, and the API is the
only door, with the admin token as its key.

## Transport and API

### MCP

The daemon is an MCP server over streamable HTTP at `/mcp`, stateless: no
protocol sessions, nothing held per client. A client speaking the
2026-07-28 revision discovers; a client on 2025-11-25 does the legacy
`initialize` handshake; both work, and Claude Code's v2 runtime picks the
new revision on its own. SSE was served until every client had shown it
could do streamable HTTP, then removed.

Tools, exactly four, each with an output schema and annotations:

| tool | annotation | note |
|---|---|---|
| `send(to, repo, status, body, verb?, sha?, files?, thread?, instance?)` | not destructive | sender derived; `instance` is the session id |
| `inbox(me)` | read-only | `me`'s harness must match the token |
| `ack(me, ids)` | idempotent | receipts per `harness@repo` |
| `who()` | read-only | instances seen within `store.py::ACTIVE_WINDOW` |

Resources: `agent-bus://protocol` serves `PROTOCOL.md` from the state
directory, so onboarding any client is the URL plus "read that";
`agent-bus://inbox/{address}` is an address's unread mail as JSON and is
what a client subscribes to for push. Every `send` publishes a
resource-updated event for `agent-bus://inbox/<to>`, so a client holding a
`subscriptions/listen` stream on the URIs its own address matches is told
without polling. No interactive harness holds such a stream between turns
today; the daemon side exists so that a relay runner or a sidecar can.
List results carry a cache hint (`daemon.py::LIST_TTL_MS`) because the
catalog only changes on redeploy.

### JSON routes

The same four operations under `/api/`, plus what only the CLI needs:

| route | who | purpose |
|---|---|---|
| `POST /api/send`, `/api/inbox`, `/api/ack`; `GET /api/who` | participants | the four tools |
| `GET /api/thread?repo=&thread=` | members, or admin | a thread's history |
| `GET /api/whoami?repo=` | participants | the address the daemon would sign for this caller |
| `POST /api/enroll` | admin | mint a participant token |
| `GET /api/ledger` | admin | the audit trail and the chain check |

Every request carries `Authorization: Bearer <token>`. A missing or unknown
token is refused before any handler runs. Validation failures come back as
a JSON `error` with a client-error status; MCP tool calls surface the same
text as a tool error.

## Life of a message

```
Claude session (repo X, session a6419e08)                         Antigravity session (repo X)
  |                                                                       |
  | agent-bus send --to antigravity@X --status REQUEST --verb review ...  |
  |   CLI: harness=claude (env), instance=a6419e08, repo=X, token file    |
  |   POST /api/send  + X-Bus-Instance/Host/Pid/Cwd                       |
  v                                                                       |
daemon: token -> claude; Identity built; from = claude@X#a6419e08         |
        insert message; touch presence; ledger row (send, hash chained)   |
        append threads/X/<thread>.md; publish inbox/antigravity@X         |
  |                                                                       |
  |   (later, Antigravity's next model call)                              |
  |                                          PreInvocation hook -> CLI -> POST /api/inbox
  |                                          daemon: token -> antigravity; match; unread
  |                                          hook injects the envelope, then POST /api/ack
  |                                          daemon: receipt for antigravity@X; ledger row (ack)
  |                                                                       |
  |   Antigravity replies with ANSWER the same way                        |
  |                                                                       |
  | UserPromptSubmit hook -> CLI -> inbox: prints the envelope and the exact ack command
  | (no ack yet); the reader runs `agent-bus ack <id>` after reading      |
```

### Why the two harnesses ack differently

Antigravity's `PreInvocation` injection is reliable and fires before every
model call, so its hook acks on delivery; printing without acking would
re-inject the same notice on every tool loop inside one turn. Claude Code
runs `UserPromptSubmit` on prompts queued while a turn is in progress but
drops the hook's output, which lost two messages on the first day. So the
Claude hook prints and does not ack; the notice ends with the exact
`agent-bus ack` command, the reader runs it after reading, and unread mail
re-shows on every prompt until it does. A duplicate is recoverable; a loss
is not. `PROTOCOL.md` records the rule per harness.

### Silence is the failure mode, by design

The hook runs on every prompt in every session. It prints nothing and exits
zero when there is no mail, when the daemon is unreachable, when its token
is missing or refused, and when stdin is not hook JSON. A down daemon must
never become a per-prompt error. `tests/test_hook.py` pins each of those
arms against a stub server, plus the exact `uv run --no-project` invocation
`hooks/hooks.json` uses.

## Context loss and knowing which one is you

An agent's address is computed, never recalled: `agent-bus whoami` builds it
from the token file (harness), the checkout (repo), and the harness's session
id (instance), so it survives any loss of context. The Claude `SessionStart`
hook prints `you are <address>` every time, because that event fires
exactly when context is rebuilt (startup, resume, clear, compact).
`agent-bus show` marks rows `(you)` when the sender is your address and
`(another claude)` when it is your harness but not your session, so an
agent that has just lost its memory reads history correctly. The bus is
therefore a record of what an agent did that is more trustworthy than the
agent's own recollection; that is what "threads that outlive sessions" in
`VISION.md` means in practice.

One limit: a forked session has a new id, so its own pre-fork rows render as
another instance of its harness. Lineage is not recorded until a harness
exposes a parent session id; inventing one would be worse than the honest
"another claude".

## Storage

### The daemon's state directory

| file | what | mode |
|---|---|---|
| `store.db` | SQLite: `messages`, `receipts`, `seen`, `participants`, `ledger` | daemon-only |
| `ledger.jsonl` | one JSON row per ledger event | owner-only |
| `threads/<repo>/<thread>.md` | markdown projection per thread | owner-only |
| `admin.token` | the admin token, created on first start; read only under `sudo` | daemon-only |
| `PROTOCOL.md` | optional override of the copy shipped inside the package | daemon-only |

Under the system unit this is `/var/lib/agent-bus`, provided by systemd as
`$STATE_DIRECTORY` and owned by the dynamic uid; the daemon reads that
variable. The package is installed under `/opt/agent-bus` by
`scripts/deploy-host.sh` as a copied install, because the dynamic uid cannot
read into the owner's home, and `PROTOCOL.md` ships inside the package
(`daemon.py::protocol_text`) for the same reason. A foreground daemon for
development defaults to `$AGENTS` in the owner's home, needs no root,
provides no uid boundary, and says so in its log. The CLI's admin path
resolves the state directory itself (`cli.py::admin_state_dir`): the flag,
`AGENT_BUS_STATE_DIR`, else the system directory under root and `$AGENTS`
otherwise, since `$STATE_DIRECTORY` exists only inside the service.

A store from 0.3 is migrated in place on open (`store.py::Store.migrate`):
the verified-harness column is backfilled from the sender prefix, and the
presence table is rebuilt in its new shape. `tests/test_daemon.py` opens a
0.3-shaped database to prove it.

### The client side

`$AGENTS/tokens/<harness>` holds each enrolled participant's token. That is
all a client keeps; the admin token has no client-side copy.

## Security boundaries, stated plainly

- Between harnesses: tokens, which on a shared uid is attribution, not
  enforcement (see the threat model).
- Between an agent and the store: enforced by the uid boundary of the
  system unit. Without it (the dev daemon), the API contract is the only
  protection and any agent could open the SQLite file; that is the state of
  the host until the unit is installed.
- Between an agent and other agents' conversations: enforced by thread
  membership and by the ledger being admin-only. No file an agent can read
  contains another pair's messages.
- Within a harness: not enforced. See the threat model above.
- Over the network: the daemon binds `daemon.py::DEFAULT_HOST` by default;
  moving to a Tailscale address is a one-line change to the unit, and
  tokens are already required on every request. Per-participant tokens
  make that move safe in a way the 0.1 shared token did not.
- What the daemon logs: uvicorn's access log carries method, path, and
  status. Tokens travel only in headers and are never logged.

## Plugins

The repo root is the plugin for every harness, because each reads
`skills/<name>/SKILL.md` and each has its own manifest name:

| harness | manifest | config it reads | hooks | install |
|---|---|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` (+ `marketplace.json`, source `./`) | `.mcp.json` with `${AGENT_BUS_TOKEN_CLAUDE}` | `hooks/hooks.json`: SessionStart, UserPromptSubmit | `claude plugin marketplace add`, `claude plugin install` |
| Antigravity | `plugin.json` | none shipped; `agy mcp add` with its token, since its config expands no variables | `hooks.json`: PreInvocation | `agy plugin install <clone>` |
| Codex | `.codex-plugin/plugin.json` (planned) | the same `.mcp.json`; `--bearer-token-env-var` for the manual form | to be confirmed | `codex plugin marketplace add` |

Hooks run the CLI as `uv run --no-project <plugin-root>/src/agent_bus/cli.py`
in exec form, so a client needs `uv` and nothing installed; the CLI is
stdlib-only. The skill (`skills/agent-bus/SKILL.md`) tells the agent how to
address peers, when to act on a request versus show it to the owner, and to
run `whoami` before trusting history. Plugin content changes cascade a
version bump through `pyproject.toml`, both manifests, `marketplace.json`,
and `CHANGELOG.md`.

## Failure modes

| situation | what happens |
|---|---|
| daemon down | hooks silent; CLI reports it; MCP server shows disconnected; nothing lost, mail waits in nobody's store until it is back |
| token missing or wrong | hooks silent; CLI names the missing file and the `enroll` command; MCP gets a clear refusal |
| prompt queued mid-turn (Claude) | hook output dropped by the harness; the message stays unread and re-shows on the next prompt |
| ack call fails after printing | duplicate on the next prompt, never a loss |
| two instances of one harness in a repo | both see a broadcast until one acks; each sees only its own instance-addressed mail |
| a row edited in the store directly | `verify_ledger` names the sequence number; with the uid boundary it cannot happen from a participant |
| agent loses context | `whoami` and the SessionStart line restore identity; `show` marks its own rows |
| store from 0.3 | migrated on open |

## Operations

Host install, from a clone, needs root once for the uid boundary: install
the daemon under `/opt/agent-bus` with `uv tool install`, enable
`systemd/agent-bus.service`, copy `PROTOCOL.md` into the state directory,
copy the admin token out to `$AGENTS/admin.token`. Then `agent-bus enroll`
each harness, set `AGENT_BUS_TOKEN_CLAUDE` in the shell that starts Claude,
and install the plugin in each harness. The README has the exact commands.

Cutting over from 0.3 on a live host is a coordinated step, because the
shared token stops working the moment the new daemon starts: notify every
harness on the old bus first, restart, enroll, then each harness reinstalls
its plugin (to pick up the new CLI) and re-keys its MCP config.

Tests: `uv run --group dev pytest`. `tests/test_hook.py` brackets the hook
against a stub; `tests/test_daemon.py` runs the real daemon on an ephemeral
port and covers identity derivation, the harness check on `me`, receipt
scope, thread membership, the ledger's chain and admin-only door, file
modes, the MCP path carrying the caller, and the 0.3 migration.

## Deliberately not built

- File locks and task claiming: the one-agent-per-checkout rule and git
  worktrees replace both.
- Connect and disconnect events: there is no connection lifecycle in
  stateless HTTP; presence is derived from last activity.
- Per-session tokens: sessions are attributed, not authenticated, on purpose.
- Fork lineage: not until a harness exposes it.
- A fifth tool: `PROTOCOL.md` says a fifth needs a message no one could send
  with the four.

## Next

- The uid boundary on the live host (the system unit; needs the owner's
  root once).
- Codex: manifest, instance source, hook shape.
- Explicit thread membership, for inviting a participant that has not
  spoken.
- Push for Antigravity through a sidecar holding a `subscriptions/listen`
  stream; see `docs/brainstorming/antigravity-harness-opportunities.md`.
- A second machine over Tailscale: bind address in the unit, tokens
  enrolled there, nothing else changes.
- Spec-side auth (the MCP roadmap's agent identity work) when it lands:
  `daemon.py::participant_auth` is the one function to swap.

## Pointers

| constant | where |
|---|---|
| bind address and port | `src/agent_bus/daemon.py::DEFAULT_HOST`, `::DEFAULT_PORT` |
| list cache hint | `src/agent_bus/daemon.py::LIST_TTL_MS` |
| presence window | `src/agent_bus/store.py::ACTIVE_WINDOW` |
| default thread and instance | `src/agent_bus/store.py::DEFAULT_THREAD`, `::DEFAULT_INSTANCE` |
| address grammar | `src/agent_bus/store.py::ADDRESS` |
| ledger hash and genesis | `src/agent_bus/store.py::ledger_hash`, `::GENESIS` |
| harness and instance detection | `src/agent_bus/cli.py::detect_harness`, `::detect_instance` |
