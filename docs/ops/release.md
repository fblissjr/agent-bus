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

## 3. Push; the tag follows

The pusher is not the author of the range.

```
git push origin main
```

A tag names the commit a release is, so the deploy script installs
something the owner has looked at rather than whatever the clone holds.
`.github/workflows/tag-release.yml` creates `v<version>` from
`pyproject.toml` on every push to main that changes that file, and never
moves a tag that exists. Nothing to run by hand; a release that was
pushed before the workflow existed is tagged once with
`git tag v<version> <commit> && git push origin v<version>`.

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
claude plugin marketplace update agent-bus && claude plugin update agent-bus@agent-bus   # Claude Code, from GitHub
git checkout "v<version>" && agy plugin install . && git checkout main               # Antigravity, from the tagged commit
```

Claude Code installs from the marketplace, so the push in step 3 has to
land and the tag exist first. Antigravity's installer copies a directory,
including untracked files, so the checkout is put at the tag before the
install and returned to main after; what it installs is then the release
and not the working tree. `agy plugin install <plugin>@<marketplace>`
after `agy plugin link` may allow a GitHub source directly; not yet
confirmed against a live install.

## Done when

- `git ls-remote --tags origin` lists `v<version>`, created by the
  workflow within a minute of the push; `git fetch --tags` brings it down.
- `agy plugin list` shows the plugin, and its copy of `plugin.json`
  carries the new version.
- A fresh session in each harness runs `agent-bus whoami` and the
  SessionStart or PreInvocation hook delivers a message sent to it.
