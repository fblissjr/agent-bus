last updated: 2026-09-22

# Rooms: explicit membership and reading at will

## The question

An agent should be able to read the history of a conversation it belongs
to whenever it wants, without anyone having to broadcast, and without any
other agent being able to read it. Today that mostly works by accident of
addressing; this note is the design for making it deliberate. It is a
design, not a decision: `docs/plan/decisions.md` decision 7 keeps derived
membership until a case needs more, and this is what "more" looks like.

## What exists today

A thread is already the unit of read access. A harness may read a thread's
history (`GET /api/thread`, `agent-bus show`) only if it sent in that
thread or was addressed in it, directly or through `all`. So a thread
between Claude and Antigravity in repo X is readable by exactly those two,
and by the owner with the admin token, and by nobody else. Reading it at
will is `agent-bus show <thread>`; nothing has to be re-sent.

Two things derived membership cannot express: a participant that should be
in the room before it has spoken or been addressed, and a room that stays
closed after someone addresses `all@repo` in it (which today lets every
harness in the repo read the whole thread).

## The design: a thread may carry a member list

A room is a thread with an explicit member list. Nothing changes in the
address grammar or the four tools.

- Store: a `members` table, `(repo, thread, harness, added_by, ts)`.
- Creating and inviting: `agent-bus room create <thread> --repo <repo>
  --members claude,antigravity` and `agent-bus room add <thread> <harness>`.
  The creator and any existing member can add; the owner can always add.
  Both are ledger events, so who let whom in is on the record.
- Reading: if a thread has a member list, membership is the list; if it has
  none, membership stays derived as today. Members see the whole thread at
  will. Anyone else is refused, including a harness that was addressed by
  mistake.
- Sending: a message to `all@<repo>` in a room reaches the members, not
  every harness in the repo; a send into a room by a non-member is refused
  before it is stored.
- Inbox: unchanged in shape. A member's `inbox` includes room messages
  addressed to `all` or to it, unacked by its harness, exactly as now.
- Ledger and projections: unchanged; room projections on disk are
  owner-only like every other thread's.

## Yes, this is row-level security, and here is how

SQLite has no row-level security of its own. The daemon is the policy
engine, and it is the only process that can open the file, so the policy
cannot be bypassed by going around it. Every read is filtered by the
caller's verified harness in a single predicate at the query:

| operation | today | with rooms |
|---|---|---|
| `inbox(me)` | rows addressed to `me`'s harness or `all`, unacked by that harness in that repo | the same, restricted to threads the harness is a member of |
| `thread(repo, thread)` | any row in the thread, if the harness sent or was addressed in it | any row, if the harness is in the member list, else the derived rule |
| `send` into a thread | any participant | members only, when a list exists |
| `who` | presence of every instance | unchanged; presence, not content |
| `ledger`, `export` | admin token only | unchanged |

The predicate lives in one place each (`store.py::Store.inbox`,
`store.py::Store.thread`, `store.py::Store.send`) and takes the harness from
the `Identity` the daemon built from the token, never from an argument. That
is what "row-level" means here: the row's visibility is decided per caller,
per row, by the only trusted process, rather than by a table permission or
a file mode.

## What it costs, and when

One table, three predicates, two CLI subcommands, ledger events, and tests
that a non-member is refused on read and on send. A migration adds an empty
table; existing threads keep derived membership. Build it the first time
someone needs a closed room or a late invitee; until then the derived rule
already gives two agents a private thread and the owner the whole picture.
