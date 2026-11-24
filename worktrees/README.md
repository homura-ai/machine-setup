# Worktree helpers

`wt-new <repo> <dest> [branch] [base]` creates a Git worktree. The branch
defaults to `wt/<destination name>`, and a new branch starts at `HEAD` by
default. When the filesystem supports reflinks, the checkout shares identical
tracked file data and ignored `node_modules` with the source. Run the project's
package install if the target branch needs other dependencies.

`wt-reap [--apply] [--days N] [root...]` scans worktrees one level below each
given root. The default roots are the project directories under
`~/.t3/worktrees/`. It previews by default. A checkout is eligible after seven
days without changes outside `node_modules` if a remote-tracking ref contains
its HEAD, Git reports no changes or untracked files, ignored entries match the
build output allowlist, there are no mounts in the checkout, and `lsof` finds no open files. Git retains the branch.
Removals are logged in `wt-reap/log` under the XDG state directory, which
defaults to `~/.local/state`.

## Install

Requires Bash, Git, GNU coreutils and findutils, util-linux (`findmnt`), and `lsof`. With systemd user
services, install the timer to run cleanup daily after 05:30.

```bash
install -Dm755 worktrees/wt-new ~/.local/bin/wt-new
install -Dm755 worktrees/wt-reap ~/.local/bin/wt-reap
install -Dm644 worktrees/wt-reap.service ~/.config/systemd/user/wt-reap.service
install -Dm644 worktrees/wt-reap.timer ~/.config/systemd/user/wt-reap.timer
systemctl --user daemon-reload
wt-reap
systemctl --user enable --now wt-reap.timer
```

To stop automated cleanup, run `systemctl --user disable --now wt-reap.timer`.
Test both helpers against disposable repositories with
`bash worktrees/test-worktrees.sh`.
