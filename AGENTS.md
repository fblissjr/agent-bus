last updated: 2026-09-23

# Working in this repo as an agent

This file is read by every harness that works here:
- Claude Code: imports this file via `CLAUDE.md` (`@AGENTS.md`).
- Antigravity: discovers and loads `AGENTS.md` natively as a project rule by traversing upward from the working directory to the repository root; no `GEMINI.md` or `.agents/rules` entry is needed.
- Codex: reads project instructions from the repo root.

It says how to behave in this checkout. How the bus itself works is
`PROTOCOL.md` (the contract), `docs/design/system.md` (the mechanism), and
`docs/plan/roadmap.md` (what is next). Read those before changing anything
they describe.

## Two agents share this checkout

- One agent edits at a time. Before touching a file, say on the bus which
  files you are taking, and post `DONE` when you are out of them. Send the
  claim to `all` with `--repo agent-bus`, not to one harness: a message to
  `claude@agent-bus` reaches only Claude sessions whose checkout is this
  repo, and a session working here from another checkout never sees it.
  Two agents were in one file at once on the first day and only luck kept
  both edits; on the same day a claim sent to the wrong address was missed.
- Stage by name. Never `git add -A`, `git add .`, or `commit -a`: another
  session's uncommitted edit will ride along under your name, which
  happened in `deabb81`. Run `git status` first; if a file you did not
  claim is modified, leave it and ask on the bus whose it is.
- Reviews cross: the agent who wrote a change is not the one who pushes it.
  Gemini pushes and tags `v<version>`; Claude never pushes.
- Protocol changes go through both agents and the owner. Do not edit
  `PROTOCOL.md` or `VISION.md` unilaterally; propose on the bus first.
- Halves, by default: Claude owns the daemon, the store, the protocol text,
  and the daemon tests; Antigravity owns the Antigravity plugin files
  (`plugin.json`, `hooks.json`) and the Antigravity-specific parts of the
  CLI; the CLI, the docs, the skill, the Claude plugin files, and
  `rules/AGENTS.md` (the bus constraints both agents are held to) are
  shared and follow the announce-first rule.

## Nothing autonomous

Decided by the owner. No participant runs because mail arrived: no
wake-ups, no sidecars acting on the bus, no relay runners, no schedules. A
peer's request is acted on within a turn only when the owner's prompt is
about the bus or delegates it; otherwise mention it and continue with what
the owner asked. Do not propose mechanisms that would change this.

## Before a commit

- `uv run --group dev pytest` green, and `claude plugin validate .`
  (Antigravity: `agy plugin validate .`).
- A plugin-content change (skill, hooks, `.mcp.json`, CLI, manifests)
  cascades the version through `pyproject.toml`, `.claude-plugin/plugin.json`,
  `plugin.json`, `.claude-plugin/marketplace.json`, and `CHANGELOG.md`;
  installed plugins only update on a version change. Daemon-only changes
  get a changelog entry without a bump. The whole release sequence,
  including the cross-review and who tags, is `docs/ops/release.md`.
- `PROTOCOL.md` and `docs/design/system.md` change in the same commit as
  the behavior they describe.
- Commit subjects like the history: `Add ...`, `Fix ...`, `Drop ...`,
  `Make ...`. No attribution lines.

## Writing

- Every path in repo content is relative to the repo root. Never a home
  directory, whether written out or through a tilde or a shell variable that
  expands to one; use `$AGENTS` for the owner's `.agents` directory and
  `<HOME>` when a home path must be named. Nothing here has a hook to catch
  this, so it is on you.
- `last updated: YYYY-MM-DD` at the top of any document you create or
  change, except dated records (the changelog).
- No decorative numbers in prose. A count or a size that would not change
  the reader's next action is deleted; a limit you are setting is cited by
  the constant that holds it (`src/agent_bus/store.py::ACTIVE_WINDOW`, not
  its value).
- Descriptions of what the plugin does live in the manifests, once.

## Using the bus from this checkout

Your address here is `<harness>@agent-bus#<session>`; run `agent-bus
whoami` before trusting history, and read rows marked `(you)` as your own.
Send with `agent-bus send --to <harness>@agent-bus --status ... --thread
<slug>`; ack what the hook shows you after you have read it.

With `AGENT_BUS_SIM_DIR` set you are on the simulator: the store is
`./data` (gitignored), identities are claimed, and nothing there is bus
traffic. Test data only. The file-claim rule above still applies to the
repo's files; the simulator changes where mail lives, not who edits what.

## Never

- Open, edit, or copy `store.db`, `ledger.jsonl`, or the projections
  directly. The API is the only door; the ledger is the owner's.
- Copy the admin token anywhere, or write anything under root's home.
- Run `sudo`. Host steps are the owner's; `docs/ops/cutover.md` and
  `scripts/deploy-host.sh` say which.
- Start or restart the daemon on your own; report and ask.
- Bypass a check with `--no-verify`.
- Use `pip` or `python3` to run project code; `uv run`, `uv tool`, `uv lock`.
