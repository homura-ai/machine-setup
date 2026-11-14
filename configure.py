#!/usr/bin/env python3
"""Destination-only file setup. Never launches an agent, proxy, or login."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def configure(home, stamp, claude_version, codex_release, t3_appimage):
    os.umask(0o077)
    if not re.fullmatch(r'[A-Za-z0-9-]+', stamp):
        raise ValueError('Invalid backup identifier')
    backups = home / '.local/state/laptop-setup/backups' / stamp / 'configs'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)

    def backup(path):
        if path.exists() or path.is_symlink():
            target = backups / path.relative_to(home)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not target.exists() and not target.is_symlink():
                shutil.copy2(path, target, follow_symlinks=False)

    def write(path, text, mode=0o600):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_file() and not path.is_symlink() and path.read_text() == text:
            path.chmod(mode)
            return
        backup(path)
        # Atomic replacement; never follow an existing target symlink.
        temporary = path.with_name(path.name + '.laptop-setup-new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        temporary.replace(path)

    def link(path, target):
        if path.is_symlink() and os.readlink(path) == str(target):
            return
        backup(path)
        temporary = path.with_name(path.name + '.laptop-setup-new')
        temporary.symlink_to(target)
        temporary.replace(path)

    def key(provider, filename):
        directory = home / 'credentials' / provider
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / filename
        if path.is_symlink():
            raise ValueError('Refusing symlink at credential destination')
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            # OpenSSL writes directly into a 600 file: no key in argv, logs or stdout.
            with os.fdopen(fd, 'wb') as stream:
                subprocess.run(['openssl', 'rand', '-hex', '24'], stdout=stream,
                               stderr=subprocess.DEVNULL, check=True)
        path.chmod(0o600)
        value = path.read_text().strip()
        if not re.fullmatch('[0-9a-f]{48}', value):
            raise ValueError('Invalid existing laptop key; refusing to rotate silently')
        return value

    common = (Path(__file__).parent / 'proxy-common.yaml').read_text()
    clients = {}
    for name, port, root in [('cliproxyapi', 8317, '.cli-proxy-api'),
                             ('cliproxyapi-claude', 8318, '.cli-proxy-api-claude')]:
        directory = home / root
        auth = directory / 'auth'
        auth.mkdir(parents=True, exist_ok=True, mode=0o700)
        auth.chmod(0o700)
        client = key(name, 'client-key')
        management = key(name, 'management-key')
        clients[port] = client
        # Same non-secret v8 settings; fresh credentials, separate empty OAuth dirs.
        config = common + f'''access:
  api-keys:
    - "{client}"
server:
  host: 127.0.0.1
  port: {port}
  tls:
    enable: false
    cert: ''
    key: ''
  commercial-mode: false
  discovery:
    service-type: _ai-gateway._tcp
    subtypes: []
management:
  allow-remote: false
  secret-key: "{management}"
  disable-control-panel: false
  panel-github-repository: https://github.com/router-for-me/Cli-Proxy-API-Management-Center
oauth:
  auth-dir: "{auth}"
  providers:
    antigravity:
      antigravity-credits: true
    aistudio:
      ws-auth: true
'''
        write(directory / 'config.yaml', config)
        write(home / '.config/systemd/user' / (name + '.service'), f'''[Unit]
Description=CLIProxyAPI {name} (manually activated after machine switch)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/{root}
ExecStart=%h/.cli-proxy-api/app/cli-proxy-api -config %h/{root}/config.yaml
Restart=always
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
''', 0o644)
    standalone = home / '.codex/packages/standalone'
    link(standalone / 'current', f'releases/{codex_release}')
    link(home / '.local/bin/claude', home / '.local/share/claude/versions' / claude_version)
    link(home / '.local/bin/codex', standalone / 'current/bin/codex')
    write(home / '.claude/settings.json', json.dumps({
        'model': 'opus', 'effortLevel': 'medium',
        'env': {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8318',
                'ANTHROPIC_AUTH_TOKEN': clients[8318]},
    }, indent=2) + '\n')
    write(home / '.codex/config.toml', f'''model = "gpt-6.1-sol"
model_reasoning_effort = "high"
model_provider = "cliproxy"

[model_providers.cliproxy]
name = "CLIProxyAPI"
base_url = "http://127.0.0.1:8317/v1"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = false
experimental_bearer_token = "{clients[8317]}"
''')
    # T3 inherits PATH and uses these native CLI config files, as on the source machine.
    # Do not copy userdata, provider sessions, ~/.codex/auth.json or Claude OAuth.
    write(home / '.local/bin/t3-code', f'''#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$PATH"
exec "{home}/app/{t3_appimage}" "$@"
''', 0o755)
    write(home / '.local/share/applications/com.t3tools.T3Code.desktop', f'''[Desktop Entry]
Type=Application
Name=T3 Code (Beta)
Exec={home}/.local/bin/t3-code %U
Terminal=false
Categories=Development;
''', 0o644)
    print('Fresh laptop configuration written; no agents, proxies or logins started.')


if __name__ == '__main__':
    try:
        configure(Path.home(), *sys.argv[1:5])
    except Exception:
        # Never include generated config or a credential value in exception output.
        print('Configuration failed; inspect paths/permissions locally (no keys printed).', file=sys.stderr)
        sys.exit(1)
