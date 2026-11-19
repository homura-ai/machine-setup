#!/usr/bin/env bash
# Forward this machine's loopback proxy ports to another machine over SSH.
set -euo pipefail
umask 077

: "${PROXY_HOST:?Set PROXY_HOST to the remote proxy hostname}"
PROXY_SSH_USER=${PROXY_SSH_USER:-$(id -un)}
if [[ ! $PROXY_HOST =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ || ! $PROXY_SSH_USER =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
    echo 'Set PROXY_HOST and PROXY_SSH_USER to a hostname and SSH username.' >&2
    exit 2
fi

claude_key=$(ssh "$PROXY_SSH_USER@$PROXY_HOST" head -n 1 credentials/cliproxyapi-claude/client-key)
codex_key=$(ssh "$PROXY_SSH_USER@$PROXY_HOST" head -n 1 credentials/cliproxyapi/client-key)
if [[ ! $claude_key =~ ^[A-Za-z0-9._~-]+$ || ! $codex_key =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo 'Expected a nonempty URL-safe client key for each proxy.' >&2
    exit 2
fi

claude_config=$HOME/.claude/settings.json
codex_config=$HOME/.codex/config.toml
cp -p "$claude_config" "$claude_config.bak"
cp -p "$codex_config" "$codex_config.bak"

printf '%s\n' "s|\"ANTHROPIC_AUTH_TOKEN\": *\"[^\"]*\"|\"ANTHROPIC_AUTH_TOKEN\": \"$claude_key\"|" |
    sed -i -f - "$claude_config"
printf '%s\n' "s|^experimental_bearer_token = .*|experimental_bearer_token = \"$codex_key\"|" |
    sed -i -f - "$codex_config"

exec ssh -N -o ExitOnForwardFailure=yes \
    -L 127.0.0.1:8318:127.0.0.1:8318 \
    -L 127.0.0.1:8317:127.0.0.1:8317 \
    "$PROXY_SSH_USER@$PROXY_HOST"
