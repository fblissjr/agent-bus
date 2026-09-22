last updated: 2026-09-22

# Host cutover: from the development daemon to the system unit

Run once, by the owner, on the host. It moves the daemon from the user unit
(state under the owner's `$AGENTS`, readable by every agent) to the system
unit (state under `/var/lib/agent-bus`, owned by a uid no agent has). After
this, the only path into the store is the API, the ledger is readable only
under `sudo`, and the admin token never exists in the owner's home.

Everything below needs root except where noted, and that is the point: the
boundary being built is "a uid the agents lack, plus a password the agents
lack". Check the second half before starting: `sudo -n true` must fail with
a password prompt, no `NOPASSWD` rule may exist for your user, and `id -nG`
must list no group that grants root without a password (`docker`, `lxd`,
`disk`, `kvm` with a privileged VM setup, and the like), or every agent can
walk through the door you are about to close. Export `AGENTS`
first (`export AGENTS=<your home>/.agents`; the CLI reads it too), so the
commands below run as written. Do the steps in order; the rollback at the
end works until step 7.

## 1. Deploy the daemon

From the clone at the release to deploy:

```
sudo UV="$(command -v uv)" scripts/deploy-host.sh
```

This installs the package into `/opt/agent-bus` and the unit file into
`/etc/systemd/system`, both root-owned, and does not start anything.

## 2. Stop and mask the development unit

```
systemctl --user disable --now agent-bus
systemctl --user mask agent-bus
```

Masking matters: the user unit restarts on failure, and two daemons would
fight over the port.

## 3. Start the system unit once, then stop it

```
sudo systemctl enable --now agent-bus
sudo systemctl stop agent-bus
```

The first start creates the state directory, mints a new admin token there,
and creates an empty store. The old admin token in `$AGENTS` is dead from
this point.

## 4. Move the data in

Fold the write-ahead log into the store first; the data is in the log until
then, and copying the store file alone yields an empty store.

```
uv run --no-project python -c "import sqlite3, os; sqlite3.connect(os.path.join(os.environ['AGENTS'], 'store.db')).execute('PRAGMA wal_checkpoint(TRUNCATE)')"
sudo rm -f /var/lib/agent-bus/store.db /var/lib/agent-bus/ledger.jsonl
sudo cp "$AGENTS/store.db" "$AGENTS/ledger.jsonl" /var/lib/agent-bus/
sudo cp -r "$AGENTS/threads" /var/lib/agent-bus/
sudo chown -R --reference=/var/lib/agent-bus/admin.token /var/lib/agent-bus/
sudo chmod 600 /var/lib/agent-bus/store.db /var/lib/agent-bus/ledger.jsonl
```

Do not copy `admin.token`. The participant tokens keep working because
their hashes travel inside the store.

## 5. Start the system unit

```
sudo systemctl start agent-bus
sudo systemctl status agent-bus --no-pager
```

The daemon migrates the store on open (participants gain the machine
column with this host's name; the ledger gains its version fields).

## 6. Verify before cleaning up

- `ss -ltnp | grep 8765` shows one listener, the system unit's.
- `ls -ln /var/lib/agent-bus` shows the dynamic uid and mode 0700 on the
  directory; `store.db` and `ledger.jsonl` are 0600.
- `sudo /opt/agent-bus/bin/agent-bus ledger` reports no broken sequence
  and shows every pre-cutover row followed by the new ones.
- `agent-bus ledger` without `sudo`, as the owner, is refused.
- From a fresh Claude Code session: the hook prints `you are ...`,
  `agent-bus show <an old thread>` returns history, and the MCP server
  connects (the shell must export `AGENT_BUS_TOKEN_CLAUDE`).
- Gemini confirms `agent-bus whoami` and a round trip on the bus.

Rollback, if anything above fails: `sudo systemctl disable --now agent-bus`,
then `systemctl --user unmask agent-bus && systemctl --user enable --now
agent-bus`. `$AGENTS` is untouched until step 7, so the old daemon resumes
where it stopped.

## 7. Clean the owner's home

Not root. Everything except the participant tokens is now either dead (the
admin token) or a stale copy that every agent could read.

```
rm -f "$AGENTS"/admin.token "$AGENTS"/store.db "$AGENTS"/store.db-wal "$AGENTS"/store.db-shm "$AGENTS"/ledger.jsonl "$AGENTS"/store.db.bak-0.3
rm -rf "$AGENTS"/threads
ls "$AGENTS"        # tokens/ and PROTOCOL.md, nothing else
```

`PROTOCOL.md` in `$AGENTS` is a leftover symlink from 0.1; the daemon now
serves the protocol from its package. Remove it too.

End every root session with `sudo -k`, so the cached credential does not
outlive the work in a terminal an agent shares.

## 8. Update every client's plugin

Hooks run the cached CLI, so each client picks up 0.5.0 only after this:

```
claude plugin marketplace update agent-bus && claude plugin update agent-bus@agent-bus
agy plugin install <path to the clone>
```

## Afterwards

Day-to-day commands after the cutover (enrolling, reading the ledger, audit
reads of a thread) are in `README.md` under "Client: enroll each harness"
and "The CLI".
