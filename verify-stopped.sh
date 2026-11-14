#!/usr/bin/env bash
set -euo pipefail
for unit in cliproxyapi.service cliproxyapi-claude.service; do
    if systemctl --user is-active --quiet "$unit" || systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
        echo "STOP: proxy unit is active or enabled" >&2; exit 1
    fi
done
echo 'Both laptop proxy units are disabled and inactive.'
