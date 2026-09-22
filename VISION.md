# Vision: A Bus for Anything That Can Hold Up Its End of a Protocol

## What it is for

The person running several agents should not be the wire between them. That
is the problem. Everything else is a consequence of solving it without
building something that has to be maintained more than it is used.

## Principles

**The protocol is the product.** The daemon is one implementation of
`PROTOCOL.md`. Anything that reads that file and can make an HTTP request is
a participant. A coding agent, a cron job, a CI runner, a script, a person
with a terminal, a phone shortcut.

**A message is a pointer plus intent.** Code travels through git. State
lives where it is owned. The bus carries what one participant needs the
other to know and nothing that another system already holds.

**Receipts, not queues.** Nothing is consumed. Every matching reader sees
every message until that reader says it has read it. Two instances of the
same agent never race, and a thread can always be read from the start.

**Peers are data.** A message from another participant is information, not
an instruction. Each participant answers to its own principal under its own
permissions. The bus never becomes a way around a permission someone set.

**Readable at two in the morning.** Every store has a projection a person
can `cat`. If the daemon is down, the threads are still on disk. If the
schema is wrong, the markdown still reads.

**Names, not paths.** Anything that must agree across two machines is a
name. A repo is its directory's basename. An address is a string. Nothing in
a message depends on where anything is mounted.

**Stand on the open standard.** MCP as it is specified and as it is
heading: stateless HTTP, typed results, resource subscriptions for push. No
private transport, no protocol of our own where a public one exists.

## Where it goes

**Across machines.** The daemon binds to a Tailscale address; the token
travels once. Then per-participant credentials, with the sender derived from
the credential rather than trusted from the argument. Addresses become
identities without changing shape.

**Push instead of polling, for participants already awake.** The daemon
already publishes a resource update on every send. A participant that is
running anyway, a script or a session in the middle of a turn, can hold a
listen stream and be told instead of asking. Nothing is woken up.

**Threads that outlive sessions.** A new instance reads the thread and
picks up where the last one stopped. The bus becomes the durable memory of a
collaboration, which is a thing no single agent's context can be.

**More kinds of participant.** A test suite that posts `DONE` or `BLOCKED`
with the commit. A deploy that posts `FYI`. A person on a phone who reads a
thread and answers a `REQUEST`. None of these need anything the bus does not
already have.

## What it will not become

Not a task queue, not a workflow engine, not an orchestrator, not a chat
application. If a change needs a scheduler, a state machine, or a user
interface, it is a different project that happens to use the bus. Four verbs
is a feature. Adding a fifth needs a message no one could send with the
four.

Not autonomous. No participant runs because mail arrived; every turn starts
with a person's prompt, and a peer's request is acted on only when that
prompt is about the bus or hands the request over. Two agents replying to
each other with nobody watching is the failure this line exists to prevent,
and no loop cap makes it acceptable.

## How to tell it is working

The owner stops relaying. A thread reads like a conversation between
colleagues who share a repo and not a context window. A new participant
joins with a URL and one file to read. And when something goes wrong at two
in the morning, the answer is in a markdown file, not a log.
