#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
# Args: STAMP CODEX_RELEASE REPO_DEST
install -d -m 700 "$HOME/.local/state/laptop-setup/backups/$1" \
    "$HOME/credentials/cliproxyapi" "$HOME/credentials/cliproxyapi-claude" \
    "$HOME/.cli-proxy-api/auth" "$HOME/.cli-proxy-api/app" "$HOME/.cli-proxy-api-claude/auth" \
    "$HOME/.config/systemd/user" "$HOME/.claude" "$HOME/.codex" \
    "$HOME/.local/lib/laptop-setup" "$HOME/.local/bin" "$HOME/.local/share/applications" \
    "$HOME/.local/share/claude/versions" "$HOME/app" "$3" \
    "$HOME/.codex/packages/standalone/releases/$2/bin"
chmod 700 "$HOME/credentials" "$HOME/.local/state/laptop-setup" "$HOME/.local/state/laptop-setup/backups"
