#!/usr/bin/env bash
set -euo pipefail
set +x
# Args: STAMP USER FEDORA_VERSION HOME
# HOME must match: the source copies to DEST_HOME paths, the helper scripts use $HOME.
[[ $(id -un) == "$2" && $HOME == "$4" && $(uname -m) == x86_64 ]] || { echo "Expected user $2 with HOME $4 on x86_64" >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == fedora && $VERSION_ID == "$3" ]] || { echo "Expected Fedora $3 on the destination" >&2; exit 1; }
sudo -n true
for unit in cliproxyapi.service cliproxyapi-claude.service; do
    if systemctl --user is-active --quiet "$unit" || systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
        echo 'STOP: destination proxies must already be disabled and inactive.' >&2; exit 1
    fi
done
# Names only; no command lines, environments, config values or auth files.
if ps -u "$(id -u)" -o comm= | grep -iEq '^(claude|codex.*|cli-proxy-api|t3code.*|T3 Code.*)$'; then
    echo 'STOP: an agent, proxy or T3 is already running on the destination.' >&2; exit 1
fi
# Do not auto-connect to unknown host keys; the installer must establish SSH trust first.
