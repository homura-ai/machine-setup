# machine-setup

Moves an agent workstation (Claude Code, Codex, T3 Code, CLIProxyAPI and one
project repo) from this machine to a freshly installed Fedora machine over SSH.
Built while running many coding agents at once on one laptop, to switch to a new
laptop without copying any credentials.

Run `setup-new-machine.sh` on the **old** machine. Over SSH it:

1. checks the new machine (`preflight.sh`): the right user, home folder,
   Fedora version and x86_64, passwordless `sudo -n`, no proxy units enabled,
   and no agent, proxy or T3 process running;
2. installs the packages it needs with `dnf`;
3. creates the folders (`directories.sh`), all private (mode 700);
4. copies the Claude Code, Codex, T3 Code and CLIProxyAPI binaries and the
   helper scripts;
5. on the new machine, writes fresh proxy keys into `~/credentials/`, two
   CLIProxyAPI configs (`proxy-common.yaml` plus the keys and ports), their
   systemd user units, and the Claude Code, Codex and T3 launcher configs
   (`configure.py`);
6. compares the binaries' checksums, never running them;
7. copies the repo with its Git history and nested repos (`rsync-excludes`
   keeps out credentials, `.env` files, keys, auth stores and `.git/config`),
   then checks every Git HEAD and the status of the main repo and selected
   nested repos (`verify-repo.py`);
8. leaves both proxy units disabled (`verify-stopped.sh`) and prints the manual
   switch checklist (`SWITCH-CHECKLIST.txt`).

It never starts an agent, a proxy or a sign-in on the new machine. Every account
is signed in again by hand, following the checklist.

## Status

Partial. It was used for one machine move. The full install has not been tested
end to end from these scripts; only the `--dry-run` path and the unit tests
below have run.

## Settings

Copy `machine.env.example` to `machine.env` next to the script and edit it:
destination host and user, SSH identity, Fedora version, the installed Claude
Code, Codex and T3 Code versions to copy (the folder and file names on this
machine), and the repo to copy. Settings can also come from environment
variables of the same name, but assignments in the sourced env file take
precedence; `--host` overrides both. `VERIFY_EXACT_STATUS` lists nested repos
(colon-separated top-level folder names) whose Git status must match exactly,
not just their HEAD.

## Run

```bash
bash setup-new-machine.sh --dry-run          # prints every command, runs none
bash setup-new-machine.sh                    # the real run
bash setup-new-machine.sh --host HOST --env other.env
```

Read every command in the dry run before the real run. Establish SSH host-key
trust with the new machine first: the script never accepts unknown keys.
Reruns keep existing keys and back up replaced files under
`~/.local/state/laptop-setup/backups/` on the new machine. There is nothing to
install; to undo, restore replaced files from those backups and remove only
files newly created by the setup on the new machine.

## Tests

Offline, with fakes and temporary folders only; they never contact a host:

```bash
python3 -m unittest test_setup     # dry run runs no tools, configure.py, rsync excludes, verify-repo
```

## Dependencies

bash, Python 3.10+, ssh, rsync, git and sha256sum on the old machine. The new
machine needs Fedora (the `dnf` package list), x86_64 and passwordless
`sudo -n` for the package step.

## Limits

- It copies one repo and the agent stack named above.
- It never copies OAuth or auth files, so every account must be signed in again
  by hand (`SWITCH-CHECKLIST.txt`).
- It is Fedora- and x86_64-only, and assumes this home layout:
  `~/.local/share/claude/versions`, `~/.codex/packages/standalone`, `~/app`
  for the T3 Code AppImage, and `~/.cli-proxy-api` for CLIProxyAPI.
- It does not install an emergency stop for agents; it only reminds you to.
