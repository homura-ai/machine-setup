#!/usr/bin/env bash
# Move the agent stack (Claude Code, Codex, T3 Code, CLIProxyAPI, one repo) from this
# machine to a freshly installed Fedora machine over SSH. Run on the SOURCE machine.
# No destination agent/proxy execution, including version checks.
#   setup-new-machine.sh [--dry-run] [--host HOST] [--env FILE]
# Settings: FILE (default ./machine.env next to this script; see machine.env.example),
# overridable by environment variables of the same names.
set -euo pipefail
set +x
here=${BASH_SOURCE[0]%/*}
[[ $here != "${BASH_SOURCE[0]}" ]] || here=.
dry=false
env_file="$here/machine.env"
host_arg=''
while (($#)); do
    case $1 in
        --dry-run) dry=true; shift ;;
        --host) host_arg=${2:?--host needs a hostname}; shift 2 ;;
        --env) env_file=${2:?--env needs a file}; shift 2 ;;
        --help) printf '%s\n' 'Usage: setup-new-machine.sh [--dry-run] [--host HOST] [--env FILE]'; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
# shellcheck source=/dev/null
[[ -f $env_file ]] && source "$env_file"
host=${host_arg:-${DEST_HOST:-}}
dest_user=${DEST_USER:-$(id -un)}
dest_home=${DEST_HOME:-/home/$dest_user}
identity=${SSH_IDENTITY:-$HOME/.ssh/id_ed25519}
src_home=${SRC_HOME:-$HOME}
fedora=${FEDORA_VERSION:-45}
if $dry; then  # placeholders so a dry run works before machine.env exists
    : "${host:=example.invalid}" "${CLAUDE_VERSION:=CLAUDE_VERSION}" "${CODEX_RELEASE:=CODEX_RELEASE}"
    : "${T3_APPIMAGE:=T3-Code.AppImage}" "${REPO_SRC:=$src_home/code/REPO}"
fi
for v in host CLAUDE_VERSION CODEX_RELEASE T3_APPIMAGE REPO_SRC; do
    [[ -n ${!v:-} ]] || { echo "Set $v (in $env_file or the environment)" >&2; exit 2; }
done
[[ $host =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'Invalid hostname' >&2; exit 2; }
[[ $dest_user =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Invalid user' >&2; exit 2; }
[[ $dest_home =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo 'Invalid DEST_HOME' >&2; exit 2; }
# These become path components and part of a generated launcher script on the destination.
for v in CLAUDE_VERSION CODEX_RELEASE T3_APPIMAGE; do
    [[ ${!v} =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || { echo "Invalid $v (a file or folder name)" >&2; exit 2; }
done
repo_dest=${REPO_DEST:-$dest_home/code/$(basename "$REPO_SRC")}
# Nested repos (top-level folder names) whose Git status must also match exactly.
export VERIFY_EXACT_STATUS=${VERIFY_EXACT_STATUS:-}
# Source paths: the installed versions on this machine. Fail if missing.
claude=$src_home/.local/share/claude/versions/$CLAUDE_VERSION
codex=$src_home/.codex/packages/standalone/releases/$CODEX_RELEASE/bin
t3=$src_home/app/$T3_APPIMAGE
proxy=$src_home/.cli-proxy-api/app/cli-proxy-api
repo=$REPO_SRC
ssh_cmd=(ssh -i "$identity" -o BatchMode=yes -o StrictHostKeyChecking=yes "$dest_user@$host")
rsync_ssh="ssh -i $identity -o BatchMode=yes -o StrictHostKeyChecking=yes"
stamp=''
if ! $dry; then
    here=$(cd "$here" && pwd)
    [[ $(uname -m) == x86_64 ]] || { echo 'Run on an x86_64 source machine' >&2; exit 1; }
    for f in "$claude" "$codex/codex" "$codex/codex-exec-server" "$codex/codex-code-mode-host" "$t3" "$proxy"; do
        [[ -x $f ]] || { printf 'Missing source binary: %s\n' "$f" >&2; exit 1; }
    done
    for c in ssh rsync python3 sha256sum git; do command -v "$c" >/dev/null; done
    stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
else
    stamp=DRY-RUN
fi
show() { printf '+ '; printf '%q ' "$@"; printf '\n'; }
run() { show "$@"; if ! $dry; then "$@"; fi; }
remote_script() {
    local file=$1; shift
    show "${ssh_cmd[@]}" bash -s -- "$stamp" "$@"
    printf "<<'REMOTE_SCRIPT'\n"
    # Helpers are public source, never generated configs or credential files.
    cat "$file"
    printf '\nREMOTE_SCRIPT\n'
    if ! $dry; then "${ssh_cmd[@]}" bash -s -- "$stamp" "$@" < "$file"; fi
}
copy() {
    local src=$1 dest=$2
    # Remote backup directory is private, outside the repo. No --delete anywhere.
    run rsync -aL --checksum --backup --backup-dir="$dest_home/.local/state/laptop-setup/backups/$stamp/binaries" \
        -e "$rsync_ssh" "$src" "$dest_user@$host:$dest"
}
remote_script "$here/preflight.sh" "$dest_user" "$fedora" "$dest_home"
run "${ssh_cmd[@]}" sudo -n dnf -y install python3.12 python3 git tmux rsync jq openssl fuse-libs gtk3 nss alsa-lib libXScrnSaver mesa-libgbm
remote_script "$here/directories.sh" "$CODEX_RELEASE" "$repo_dest"
d_claude=$dest_home/.local/share/claude/versions/$CLAUDE_VERSION
d_codex=$dest_home/.codex/packages/standalone/releases/$CODEX_RELEASE/bin
d_t3=$dest_home/app/$T3_APPIMAGE
d_proxy=$dest_home/.cli-proxy-api/app/cli-proxy-api
lib=$dest_home/.local/lib/laptop-setup
copy "$claude" "$d_claude"
copy "$codex/" "$d_codex/"
copy "$t3" "$d_t3"
copy "$proxy" "$d_proxy"
copy "$here/configure.py" "$lib/configure.py"
copy "$here/proxy-common.yaml" "$lib/proxy-common.yaml"
copy "$here/verify-repo.py" "$lib/verify-repo.py"
if $dry; then
    printf '%s\n' '# configure.py runs ONLY on the destination; for each missing client-key and management-key:'
    printf '%s\n' '# openssl rand -hex 24 > ~/credentials/cliproxyapi/{client-key,management-key} (each file 0600)'
    printf '%s\n' '# openssl rand -hex 24 > ~/credentials/cliproxyapi-claude/{client-key,management-key} (each file 0600)'
fi
run "${ssh_cmd[@]}" python3 "$lib/configure.py" "$stamp" "$CLAUDE_VERSION" "$CODEX_RELEASE" "$T3_APPIMAGE"
# Compare copied bytes, never execute the copied programs.
for item in "$claude:$d_claude" "$codex/codex:$d_codex/codex" \
    "$codex/codex-exec-server:$d_codex/codex-exec-server" \
    "$codex/codex-code-mode-host:$d_codex/codex-code-mode-host" \
    "$t3:$d_t3" "$proxy:$d_proxy"; do
    src=${item%%:*}; dest=${item#*:}
    show sha256sum "$src"
    show "${ssh_cmd[@]}" sha256sum "$dest"
    if ! $dry; then
        local_sum=$(sha256sum "$src"); remote_sum=$("${ssh_cmd[@]}" sha256sum "$dest")
        [[ ${local_sum%% *} == "${remote_sum%% *}" ]] || { echo 'Binary checksum mismatch' >&2; exit 1; }
    fi
done
show python3 "$here/verify-repo.py" "$repo"
if ! $dry; then before=$(python3 "$here/verify-repo.py" "$repo"); fi
run rsync -a --safe-links --checksum --backup \
    --backup-dir="$dest_home/.local/state/laptop-setup/backups/$stamp/repo" \
    --exclude-from="$here/rsync-excludes" -e "$rsync_ssh" "$repo/" "$dest_user@$host:$repo_dest/"
show python3 "$here/verify-repo.py" "$repo"
show "${ssh_cmd[@]}" env "VERIFY_EXACT_STATUS=$VERIFY_EXACT_STATUS" python3 "$lib/verify-repo.py" "$repo_dest"
if ! $dry; then
    after=$(python3 "$here/verify-repo.py" "$repo")
    copied=$("${ssh_cmd[@]}" env "VERIFY_EXACT_STATUS=$VERIFY_EXACT_STATUS" python3 "$lib/verify-repo.py" "$repo_dest")
    [[ $before == "$after" ]] || { echo 'Source repo changed during copy; rerun before switching.' >&2; exit 1; }
    [[ $after == "$copied" ]] || { echo 'Git HEAD/status mismatch (including nested repositories); STOP.' >&2; exit 1; }
    echo 'Verified binary checksums, all Git HEADs and Git statuses.'
fi
run "${ssh_cmd[@]}" systemctl --user daemon-reload
# preflight refuses active/enabled units: this never stops an existing running service.
run "${ssh_cmd[@]}" systemctl --user disable cliproxyapi.service cliproxyapi-claude.service
remote_script "$here/verify-stopped.sh"
sed "s|@REPO_DEST@|$repo_dest|g" "$here/SWITCH-CHECKLIST.txt"
# The emergency stop is a separate repo with a per-machine config; this script never installs it.
printf '%s\n' 'Emergency stop: not installed by this script. On the new machine, install' \
    'agents-killall yourself (killall/ in the agent-tools repo; its install.sh' \
    'refuses until ~/.config/agents-killall/config.toml exists).'
