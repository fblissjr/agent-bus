last updated: 2026-09-23

# Releasing

Every plugin-content change is a release, because installed plugins only
update on a version change. The steps, in order, and who does each. The
one-time host move is `docs/ops/cutover.md`, not this.

## 1. The author, before committing

- Cascade the version through `pyproject.toml`,
  `.claude-plugin/plugin.json`, `plugin.json`,
  `.claude-plugin/marketplace.json`, and a `CHANGELOG.md` entry; then
  `uv lock`, never by editing `uv.lock`. A daemon-only change gets a
  changelog entry without a bump.
- `uv run --group dev pytest` green and `claude plugin validate .` (or
  `agy plugin validate .`) passing.
- `PROTOCOL.md` and `docs/design/system.md` in the same commit as the
  behavior they describe. Stage by name.

## 2. The other agent, before anything is pushed

The author sends a `REQUEST review` on the bus naming the commit. The
other agent reads the diff, runs the tests, and answers `agree` or the
exact lines it objects to. Nothing is pushed until that answer exists; if
something was pushed early, the review still happens and objections are
fixed forward in a new commit. On the simulator this works with no daemon.

## 3. Tag and push

The pusher is not the author of the range. A tag names the commit a
release is, so the deploy script installs something the owner has looked
at rather than whatever the clone holds.

```
git tag v<version> <commit>
git push origin main --tags
```

## 4. The host

After the cutover, on the host, from the tagged commit:

```
sudo UV="$(command -v uv)" scripts/deploy-host.sh
sudo -k
```

Until the cutover there is no daemon to deploy; skip this step.

## 5. Every client

Hooks run the cached CLI, so each harness picks up the release only after
this, and only after a restart of the harness so the new hook and skill
load:

```
agy plugin install .                      # Antigravity, from the checkout root
claude plugin marketplace update agent-bus && claude plugin update agent-bus@agent-bus   # Claude Code, from the pushed repo
```

Antigravity's install copies the whole clone into its plugin directory,
including untracked files, so it is local to that machine and nothing to
share. Claude Code installs from the marketplace, so the push in step 3
has to land first.

## Done when

- `git tag --contains <commit>` names the release, locally and on origin.
- `agy plugin list` shows the plugin, and its copy of `plugin.json`
  carries the new version.
- A fresh session in each harness runs `agent-bus whoami` and the
  SessionStart or PreInvocation hook delivers a message sent to it.
