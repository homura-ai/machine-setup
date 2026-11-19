# t3-load-build

Loads a staged T3 Code build and replaces the hand-written `~/.local/state/t3-restart/restart-*.sh` scripts.

```sh
t3-load-build <staged-file> <sha256> <wake-message-file> [--detach] [--live PATH] [--settle SECONDS]
```
Use `--detach`: the loader stops T3, which stops the agent that started it, so it re-runs itself under `systemd-run --user`.

## Steps
1. **Refuses** (exit 3) while `~/.local/state/t3-load-build/no-restart-until`, or any file listed (colon-separated) in `T3_LOAD_GUARDS`, holds a UTC ISO time in the future. Use these markers to defer an authorized restart.
2. Verifies the staged file and its sha256, and that the wake file is readable; otherwise exit 1 and nothing is touched.
3. If `T3_LOAD_PAUSE_FILE` is set, writes now + 8 minutes there so a T3 watchdog holds off.
4. Promotes the file so the launcher picks it up. `...-next.AppImage.staged` becomes `...-x86_64.AppImage`, while other `.staged` files lose the suffix. Use `--live` to override the name. An existing destination causes exit 1 before T3 is stopped.
5. Stops T3 (TERM, 8 s, KILL), starts it with `systemd-run --user ... ~/.local/bin/t3code`, waits `--settle` seconds (90).
6. If `pgrep -x t3code` finds a process, sends the wake file's text to `T3_LOAD_WAKE_THREAD` with `t3 thread send <thread> - --mode auto` (skipped when unset) (text on stdin; one retry after 30 s). Exits 0 only if the process is running and wake delivery succeeds when configured.
7. If the process is absent or both wake attempts fail, rolls back by itself: renames the new build to `.off`, restarts T3 on the previous newest build, wakes that thread with a rollback notice. Exit 2. If T3 still does not come up it only logs; this tool sends no failure alert.

With no wake thread configured, validation checks only for a process. With
a wake thread, it also requires successful message delivery. If a watchdog manages T3, configure its pause file before restarting. The
loader writes the pause marker and leaves the watchdog running.

Log: `~/.local/state/t3-load-build/load.log` (no message text). Python 3 standard library only.

## Check and install
```sh
python3 -m unittest discover -s t3-load-build -p 'test_*.py' -v
install -m 755 t3-load-build/t3-load-build ~/.local/bin/
```
Tests use fakes only; they never stop or start T3.

`--detach` passes `T3_LOAD_GUARDS`, `T3_LOAD_PAUSE_FILE` and `T3_LOAD_WAKE_THREAD` on to
the `systemd-run` child with `--setenv`, because that child starts from the user
manager's environment. Unset, there are no extra guards, no watchdog pause and no wake.
