last updated: 2026-09-22

# Open decisions

What still has to be decided about agent-bus, with enough context to decide
it, and each agent's recommendation with the reasons for and against. Both
Claude and Antigravity were asked for this; the two answers are merged here
so there is one document. Where they agree, one recommendation is given.
Where they differ, both positions are stated and marked **differs**, and the
owner rules.

Settled decisions live in `PROTOCOL.md`; how the system works lives in
`docs/design/system.md`. When a decision here is made, move its outcome into
one of those and delete it from this file.

Ordered by how much the answer changes what gets built next.

## 1. Which boundaries are real on a single-uid host

**Context.** Everything on the host runs as the owner's uid: every Claude
session, every Antigravity session, Codex when it arrives, and the owner's
own shell. The daemon's state directory can be put behind a different uid
(the system unit), but the client side cannot: `$AGENTS/tokens/claude`,
`$AGENTS/tokens/antigravity`, and any copy of the admin token in the owner's
home are readable by every process running as the owner, which is every
agent. So:

- The store and the ledger can be made unreachable except through the API.
  That boundary is real.
- Cross-harness identity is not. Antigravity could read Claude's token file
  and sign as Claude. Nothing today does that, and an agent that did would
  be violating its own instructions, but it is policy, not enforcement.
  `docs/design/system.md` says so.
- A copy of the admin token in the owner's home hands every agent the key to
  the ledger and to every thread, which would quietly undo the point of
  making the ledger admin-only.

**Options.**

- (a) Accept attribution within the uid. Keep per-harness token files; treat
  cross-harness identity as high-confidence attribution; keep the admin
  token only in the daemon's state directory and audit with `sudo`.
- (b) One uid per harness. Run each harness as its own system user with its
  own token file. Identity becomes enforced. Cost: every harness install,
  plugin, and checkout has to work across uid boundaries, and the owner
  loses the "it all runs as me" simplicity of a solo box.
- (c) A keystore per harness. Not consumable by the harnesses today, and
  still same-uid on Linux.

**Claude recommends (a), with the admin-token half done now.** The failure
this work addressed was confusion between instances, and attribution solves
confusion. Enforcement against a misbehaving agent of your own is a
different threat, and (b) is the only thing that delivers it; the day that
threat is real, (b) is the answer, not a cleverer file layout, and it can be
adopted later without changing the protocol because the daemon trusts
tokens, not uids. What should change now is the admin token: it should never
leave the daemon's state directory. See decision 3.

**Antigravity agrees with Claude on (a).** Keeping the admin token strictly in the daemon's state directory and requiring `sudo` for enrollment/audits is the only way to prevent local agent processes from acquiring master ledger read access. Attribution within the shared UID is the right model for now.

## 2. When to install the uid boundary, and how

**Context.** The system unit (`systemd/agent-bus.service`) runs under
`DynamicUser`, which gives the service a throwaway uid and read-only access
to the rest of the system. Two things follow. First, the owner's home is not
traversable by other users on this host, so an editable `uv tool install`
that points back into the clone cannot be read by that uid and the service
fails to start; the README's editable form under `/opt/agent-bus` is wrong
for this reason. Second, until the unit is installed, the store is protected
only by the API contract, and any agent could open the SQLite file.

**Options.**

- (a) Install now, as a copied (non-editable) `uv tool install` into
  `/opt/agent-bus`, re-run on each daemon release. One root command per
  release plus a restart.
- (b) Install before the first network exposure (Tailscale), not before.
- (c) A static `agent-bus` system user granted read access into the clone.
  Works, but couples a system user to a path in someone's home.
- (d) Keep the dev user unit indefinitely.

**Claude recommends (a), now.** "No agent can overwrite or write to it
directly" was the requirement; the hash chain only makes a violation
visible after the fact, and every agent on the host can currently violate
it. The cost is one root session. A copied install also means the deployed
daemon is a snapshot the owner chose, not whatever the clone contains.

**Antigravity agrees with Claude on (a).** While (b) was suggested earlier purely to defer running `sudo`, applying the UID boundary now via a copied install under `/opt/agent-bus` closes the file-level store tampering window immediately. Apply it now as soon as the owner runs the root setup commands.

## 3. Where the admin token lives, and the audit path

**Context.** Follows from decision 1. The README currently says to copy the
admin token to `$AGENTS/admin.token`, and `enroll`, `ledger`, and
`show --audit` read it from there.

**Options.**

- (a) The admin token stays in the state directory only. `agent-bus
  enroll`, `agent-bus ledger`, and `show --audit` run under `sudo` and read
  it from `$STATE_DIRECTORY`. Enrolling a harness on the host is `sudo
  agent-bus enroll claude`, once.
- (b) Copy it to the owner's home as documented. Convenient; readable by
  every agent.
- (c) Two tokens: a read-only audit token in the home and the admin token
  under root. Halves the exposure, but the audit token still exposes every
  thread to every agent, which is the part that matters.

**Claude recommends (a).** Only (a) meets the stated requirement on a
shared uid. The cost is a `sudo` prompt on commands the owner runs a few
times a month, never on anything an agent runs. Change: README, and
`cli.py::get_admin_token` reads the state directory when run as root.

**Antigravity agrees with Claude on (a).** My earlier note suggested copying the admin token to the home directory for convenience, but Claude is right: convenience in `$AGENTS/admin.token` completely leaks ledger and thread read access to every agent process on the machine. The admin token must remain in `$STATE_DIRECTORY` only.

## 4. Enrolling a second machine

**Context.** The Mac will join over Tailscale. Enrollment needs the admin
token.

**Options.**

- (a) Mint on the host (`sudo agent-bus enroll <harness>`, one token per
  harness per machine) and copy only the participant token to the Mac.
- (b) Copy the admin token to the Mac and enroll locally.
- (c) An enrollment endpoint that accepts a one-time code minted on the
  host.

**Claude recommends (a).** Move only the result; never move the one secret
that must not spread. (c) is nicer and worth doing when a third machine
appears. Both agents agree on the networking half: bind the unit to the
Tailscale address, set `AGENT_BUS_URL` in each Mac harness, plain HTTP
inside the tailnet is acceptable because the tailnet encrypts and tokens are
required on every request, and the daemon stamps host and peer on every
row.

**Antigravity agrees with Claude on (a).** Mint participant tokens on the host (`sudo agent-bus enroll <harness>`) and copy only the participant token to the Mac. The admin token must never cross the network.

## 5. Ledger schema: the enrolled name, and how to change the schema at all

**Context.** An `enroll` row records who enrolled (the admin identity) but
carries the enrolled participant's name only as a body hash. The row hash
covers a fixed field list (`store.py::LEDGER_FIELDS`), so adding a field
today would make every existing row fail verification.

**Options.**

- (a) Versioned hashing: add `subject` and a `hash_version`; rows verify
  under the field list of their version. Old rows keep verifying, new rows
  carry the name.
- (b) Reset the ledger now while it holds only enrollment rows.
- (c) Leave it; the `participants` table has a timestamp per name, so the
  fact is recoverable by joining.

**Claude recommends (a), soon.** It is the mechanism the ledger will need
the next time a field is added, and doing it first makes the second time
free. (b) sets the precedent that the ledger is resettable, the one thing it
must never be. (c) is fine for a month and wrong for a year.

**Antigravity agrees with Claude on (a).** Versioned hashing (`hash_version` + field list) is the proper extensible mechanism for the ledger schema. Proceed with (a).

## 6. Receipt scope: harness-and-repo, or per instance

**Context.** Chosen during 0.4 without a round trip: acks are recorded for
`harness@repo`, so the first Claude in a repo to ack a broadcast settles it
for every Claude there, and a new session inherits no backlog. Mail
addressed to a specific instance is unaffected. Per-instance receipts would
mean every new session sees the repo's whole history as unread and two
concurrent sessions both act on the same request.

**Recommendation: keep it.** Revisit only if a harness needs per-instance
inboxes for broadcasts. The ledger records which instance acked, so nothing
is lost either way. Antigravity accepted this in review.

## 7. Explicit thread membership

**Context.** Threads are groups whose membership is derived: you can read a
thread if you sent in it or were addressed in it. That cannot express "add
Codex to this room before it has said anything", and addressing `all@repo`
once opens the thread to every harness in the repo.

**Options.**

- (a) Keep derived membership until a case needs more.
- (b) An explicit member list per thread: any member can add a member,
  additions are ledger events, derived membership remains the default when
  no list exists.
- (c) Owner-administered membership only.

**Both agents recommend (a), with (b) as the design when it is needed.**
Nothing today has needed an invite, and every mechanism added before its
first use has cost more than it returned in this project. (c) puts the
owner back in the loop for the exact thing the bus exists to remove.

## 8. Push instead of polling

**Decided 2026-09-22, by the owner: nothing autonomous.** No sidecar
wake-ups, no relay runner, no schedule; every turn starts with a person's
prompt, and a peer's request is acted on within a turn only when the prompt
is about the bus or delegates it. Recorded in `PROTOCOL.md` (working rules)
and `VISION.md` (what it will not become). The daemon's push events stay as
plumbing for participants that are already running. Gemini's sidecar notes
in `docs/brainstorming/antigravity-harness-opportunities.md` describe a path
that is closed by this decision; they remain as a record of what was
considered.

## 9. Codex

**Context.** Codex reads `.codex-plugin/plugin.json` and a plugin-root
`.mcp.json`, has hooks with a trust model, and takes a bearer token from a
named environment variable. Its session id variable and hook stdin shape are
unconfirmed. The participant name `codex` and `AGENT_BUS_TOKEN_CODEX` are
reserved.

**Claude recommends adding it when it is first used, not before.** The cost
is a manifest, a line in `cli.py::detect_harness`, and a hook shape to
confirm, all cheaper against a live Codex than from documentation.

**Antigravity recommends making it the primary milestone of the next
release.** Differs on priority, not on approach.

## 10. Releases and who pushes

**Context.** The owner does not want the Claude side to push; Gemini has
been pushing. Versions cascade through `pyproject.toml`, both plugin
manifests, `marketplace.json`, and `CHANGELOG.md`, and installed plugins
only update when the version changes.

**Recommendation: Gemini pushes after each agreed change and tags releases
as `v<version>`.** It matches what has happened, keeps one hand on the
remote, and a tag gives the copied install in decision 2 something to
install. Whoever pushes should not be the author of the change in the same
range; that review discipline caught two bugs on the first day.

## 11. Per-session handles

**Context.** Within a harness, session ids are self-reported.

**Recommendation: not now.** The observed failure was confusion, and
attribution fixes confusion. If a session ever needs to be proven, the
daemon can mint a handle on first sight of a new session id and require it
thereafter, with no human involved; the protocol already says so.

## 12. Retention

**Context.** The store, the ledger, and the projections grow without bound.
They are text.

**Recommendation: no retention policy.** The ledger must not lose rows by
design, and the rest is small. Revisit if `store.db` ever becomes something
the owner notices.

## 13. Projections

**Context.** With projections owner-only on disk and readable only through
`sudo`, they no longer serve agents at all.

**Recommendation: keep them.** They exist for the owner at two in the
morning when the daemon is down, and that case has not gone away.

## 14. The two Claude Code harness reports

**Context.** Two harness behaviors were worked around during the build:
hook output dropped for prompts queued mid-turn, and a run of turns
vanishing from a session's working context without a summary. Both have
feedback drafts queued locally on the Claude side.

**Recommendation: send both.** The workarounds hold, but the second will
bite someone else's design, and the transcript evidence is precise.

## Summary

| decision | Consensus / Decision |
|---|---|
| 1 boundaries | Attribution within shared UID; admin token strictly in state directory |
| 2 uid boundary | Install now, as a copied install under `/opt/agent-bus` via root setup |
| 3 admin token | State directory only; audit and enrollment run under `sudo` |
| 4 second machine | Mint on host via `sudo agent-bus enroll`; move only participant token to Mac |
| 5 ledger subject | Versioned hashing (`hash_version` + field list), soon |
| 6 receipt scope | Keep `harness@repo` receipts (instance recorded in ledger) |
| 7 explicit membership | Keep derived membership for now; add explicit invites later if needed |
| 8 push | **NO AUTONOMOUS PUSH / SHELVED** per owner ruling; human-driven turn hooks only |
| 9 Codex | Add when the owner is ready to use Codex |
| 10 releases | Gemini pushes and tags releases as `v<version>` |
| 11 session handles | Not now; attribution solves confusion |
| 12 retention | No retention policy; store and ledger remain append-only text |
| 13 projections | Keep markdown projections for owner inspection at 2 AM |
| 14 harness reports | Send both queued harness feedback reports to Claude Code team |

## Agreed execution order

1. **Admin token lockdown** (decisions 1 & 3): Keep in state directory; CLI reads it under `sudo`. Update README.
2. **UID boundary host install** (decision 2): Copied install under `/opt/agent-bus` with `DynamicUser=yes`.
3. **Versioned ledger hashing with `subject`** (decision 5): Clean, extensible ledger fields.
4. **Tag and push** (decision 10): Antigravity pushes and tags.
5. **Mac enrollment** (decision 4): Mint participant token on host and copy to Mac.
6. **Codex integration** (decision 9): Onboard when owner is ready.
*(Autonomous push is shelved per decision 8).*
