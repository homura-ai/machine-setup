# machine-setup

Tools for moving an agent workstation (Claude Code, Codex, T3 Code, CLIProxyAPI
and one project repo) to a fresh Fedora machine, and for stopping or containing
agents on it. Each piece stands alone.

| Tool | What it does |
| --- | --- |
| `setup-new-machine.sh` | Prepares a Fedora workstation from an existing one over SSH. |
| `killall/` | Points to the standalone agents-killall tool and documents its config. |
| `spawn-cap/` | Limits concurrent agent runs per thread subtree through a Claude Code or Codex hook. |
| `t3-load-build/` | Loads a staged T3 Code build and rolls back if startup or a configured wake message fails. |
| `gnome/` | Example GNOME preferences in dconf format. |

## Move to a new machine

Run `setup-new-machine.sh` on the old machine. It checks the new machine's
user and Fedora version and verifies that agents and proxies are stopped.
It installs OS packages, copies the agent binaries, writes new proxy keys and
CLI configs, and verifies binary checksums without running the binaries. It
copies one project repo, including nested repos and Git history.
`rsync-excludes` keeps out credentials, `.env` files, keys, auth stores and `.git/config`.
`verify-repo.py` compares every Git HEAD and checks the status of the main
repo and selected nested repos. The script leaves both proxy units disabled
and prints `SWITCH-CHECKLIST.txt` for the manual switch.

Copy `machine.env.example` to `machine.env`. Set the destination host and
user, SSH identity, Fedora version, installed tool versions and project repo.
Assignments in the sourced env file override environment variables.
`--host` overrides both. Set `VERIFY_EXACT_STATUS` to the colon-separated
names of top-level nested repos whose Git status must also match exactly.

```bash
bash setup-new-machine.sh --dry-run
bash setup-new-machine.sh
bash setup-new-machine.sh --host new-laptop --env other.env
```

Read the dry run before running the migration. Establish SSH host-key trust
with the destination first. Reruns keep existing keys and back up replaced
files under `~/.local/state/laptop-setup/backups/` on the new machine. To undo
the migration, restore those backups and remove files created by the setup.

## Route existing clients through another machine

On the client machine, configure both clients for the loopback proxies and
free local ports 8317 and 8318. Then run:

```bash
PROXY_HOST=proxy.example.net PROXY_SSH_USER=your-user ./route-via-proxy.sh
```

`PROXY_SSH_USER` defaults to your local username. The script reads both
client keys over SSH and updates the tokens in `~/.claude/settings.json` and
`~/.codex/config.toml`. It runs an SSH tunnel in the foreground so the
clients can reach the remote proxies through their existing loopback URLs.
Keep it running while you use the proxies. Stop the tunnel and copy each
`.bak` file back to restore the prior configuration. A rerun overwrites
those backups.

## Tests

The migration has been checked with dry runs and offline tests. A full
migration from these scripts has yet to be verified.

```bash
python3 -m unittest test_setup                                   # 6: dry run runs no tools, configure.py, rsync excludes, verify-repo
(cd t3-load-build && python3 -m unittest test_load)               # 9
(cd spawn-cap && python3 -m unittest test_spawn_cap)              # 22
bash setup-new-machine.sh --dry-run --host example.invalid       # prints every command, runs none
```

## Dependencies

bash, Python 3.10+, ssh, rsync, git and sha256sum on the old machine. The new
machine needs Fedora (the dnf package list), x86_64 and passwordless `sudo -n`
for the package step.

See each helper's README for install and uninstall instructions.

## Limits

- The migration copies one repo and the specific agent stack named above. It
  never copies OAuth or auth files, so every account must be signed in again
  by hand (`SWITCH-CHECKLIST.txt`).
- It is Fedora- and x86_64-only, and assumes the Linux home layout used here
  (`~/.local/share/claude/versions`, `~/.codex/packages/standalone`, `~/app`).
