#!/usr/bin/env python3
"""Local tests only: fake transport and fake OpenSSL, never contact any host."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
os.environ['VERIFY_EXACT_STATUS'] = 'research-a:research-b'
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SetupTests(unittest.TestCase):
    def test_dry_run_never_executes_transport_or_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'CALLED'
            for name in ['ssh', 'rsync', 'openssl', 'claude', 'codex', 'systemctl',
                         'dnf', 'git', 'sha256sum', 'uname', 'date', 'python3']:
                stub = root / name
                stub.write_text('#!/bin/sh\nprintf called >> "$SETUP_TEST_MARKER"\nexit 99\n')
                stub.chmod(0o755)
            result = subprocess.run(['/bin/bash', str(HERE / 'setup-new-machine.sh'), '--dry-run',
                                     '--host', 'example.invalid'], check=True, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'],
                                         'SETUP_TEST_MARKER': str(marker)})
            self.assertFalse(marker.exists())
            self.assertIn('@example.invalid', result.stdout)
            self.assertIn('python3.12', result.stdout)
            self.assertIn('STOP', result.stdout)
            self.assertIn('-claude-login', result.stdout)
            # Enable/start and login commands occur only in the printed checklist.
            before_stop = result.stdout.split('STOP — PREPARED ONLY.', 1)[0]
            self.assertNotIn('enable --now', before_stop)
            self.assertNotIn(' -claude-login', before_stop)
            self.assertNotIn(' -codex-login', before_stop)
            # The emergency stop is pointed to, never installed or run.
            self.assertIn('install\nagents-killall from its standalone repo', result.stdout)
            self.assertNotIn('install.sh refuses', before_stop)
            self.assertFalse((HERE / 'killall/agents-killall').exists())

    def test_routes_both_clients_and_preserves_config_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude = home / '.claude/settings.json'
            codex = home / '.codex/config.toml'
            for path in (claude, codex):
                path.parent.mkdir()
            claude.write_text(json.dumps({'env': {
                'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8318',
                'ANTHROPIC_AUTH_TOKEN': 'old-claude-value',
            }}))
            codex.write_text(
                'base_url = "http://127.0.0.1:8317/v1"\n'
                'model_catalog_url = "http://127.0.0.1:8317/v1/models"\n'
                'experimental_bearer_token = "old-codex-value"\n'
            )
            originals = {path: path.read_text() for path in (claude, codex)}
            ssh = home / 'ssh'
            ssh.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = "-N" ]; then\n'
                '  [ "$*" = "-N -o ExitOnForwardFailure=yes -L 127.0.0.1:8318:127.0.0.1:8318'
                ' -L 127.0.0.1:8317:127.0.0.1:8317 tester@proxy.example.net" ]\n'
                '  exit $?\n'
                'fi\n'
                '[ "$1" = "tester@proxy.example.net" ] || exit 1\n'
                'case "$5" in\n'
                '  credentials/cliproxyapi-claude/client-key) echo fixture-claude ;;\n'
                '  credentials/cliproxyapi/client-key) echo fixture-codex ;;\n'
                '  *) exit 1 ;;\n'
                'esac\n'
            )
            ssh.chmod(0o755)
            result = subprocess.run(
                ['bash', str(HERE / 'route-via-proxy.sh')],
                env={**os.environ, 'HOME': str(home), 'PATH': f"{home}:{os.environ['PATH']}",
                     'PROXY_HOST': 'proxy.example.net', 'PROXY_SSH_USER': 'tester'},
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(claude.read_text())['env'], {
                'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8318',
                'ANTHROPIC_AUTH_TOKEN': 'fixture-claude',
            })
            self.assertIn('base_url = "http://127.0.0.1:8317/v1"', codex.read_text())
            self.assertIn('model_catalog_url = "http://127.0.0.1:8317/v1/models"', codex.read_text())
            self.assertIn('experimental_bearer_token = "fixture-codex"', codex.read_text())
            self.assertNotIn('fixture-', result.stdout + result.stderr)
            for path, original in originals.items():
                self.assertEqual(path.with_suffix(path.suffix + '.bak').read_text(), original)

    def test_build_loader_preserves_existing_file_and_rolls_back_if_wake_cli_fails(self):
        module = load('loader', 't3-load-build/t3-load-build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.STATE = root
            module.LOG = root / 'load.log'
            module.GUARDS = ()
            module.PAUSE_FILE = root / 'pause'
            module.WAKE_THREAD = 'test-thread'
            staged = root / 'new.AppImage.staged'
            staged.write_bytes(b'new build')
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            live = module.live_name(staged)
            live.write_bytes(b'existing build')
            wake = root / 'wake'
            wake.write_text('test message')
            deps = Mock()
            deps.now.side_effect = module.Real.now
            deps.sleep.return_value = None
            deps.t3_running.return_value = True
            deps.send.side_effect = module.Real.send

            self.assertEqual(module.load(staged, digest, wake, deps=deps, settle=0), 1)
            deps.stop_t3.assert_not_called()
            self.assertEqual(live.read_bytes(), b'existing build')
            self.assertTrue(staged.is_file())
            self.assertFalse(module.PAUSE_FILE.exists())

            replacement = root / 'new-version.AppImage'
            with patch.object(module.subprocess, 'run', side_effect=[
                subprocess.TimeoutExpired('t3', 30), FileNotFoundError(),
                subprocess.CompletedProcess([], 0),
            ]) as wake_calls:
                self.assertEqual(module.load(staged, digest, wake, deps=deps, live=replacement, settle=0), 2)
            self.assertEqual(wake_calls.call_count, 3)
            self.assertEqual(deps.stop_t3.call_count, 2)
            self.assertFalse(replacement.exists())
            self.assertEqual(replacement.with_suffix('.AppImage.off').read_bytes(), b'new build')
            self.assertEqual(live.read_bytes(), b'existing build')

    def test_configuration_keys_permissions_backups_and_idempotence(self):
        module = load('configure', 'configure.py')
        calls = []

        def fake_rand(command, stdout, **kwargs):
            self.assertEqual(command, ['openssl', 'rand', '-hex', '24'])
            calls.append(command)
            # Public test fixtures, never real client/OAuth keys.
            stdout.write((str(len(calls)) * 48 + '\n').encode())

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for relative in ['.local/bin', '.codex/packages/standalone', '.claude']:
                (home / relative).mkdir(parents=True)
            original = home / '.claude/settings.json'
            original.write_text('{"model":"old"}\n')
            with patch.object(module.subprocess, 'run', side_effect=fake_rand), contextlib.redirect_stdout(io.StringIO()):
                module.configure(home, 'first', '2.1.319', '0.167.0-x86_64-unknown-linux-musl', 'T3-Code-0.1.7-x86_64.AppImage')
                states = {p: p.read_bytes() for p in home.rglob('*') if p.is_file()}
                module.configure(home, 'second', '2.1.319', '0.167.0-x86_64-unknown-linux-musl', 'T3-Code-0.1.7-x86_64.AppImage')
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(p.read_bytes() == value for p, value in states.items()))
            backup = home / '.local/state/laptop-setup/backups/first/configs/.claude/settings.json'
            self.assertEqual(backup.read_text(), '{"model":"old"}\n')
            claude = json.loads(original.read_text())
            self.assertEqual(claude['env']['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:8318')
            self.assertEqual(claude['env']['ANTHROPIC_AUTH_TOKEN'], '3' * 48)
            codex = (home / '.codex/config.toml').read_text()
            self.assertIn('experimental_bearer_token = "' + '1' * 48 + '"', codex)
            for port, root, key in [(8317, '.cli-proxy-api', '1'), (8318, '.cli-proxy-api-claude', '3')]:
                config = home / root / 'config.yaml'
                text = config.read_text()
                self.assertIn('port: ' + str(port), text)
                self.assertIn('"' + key * 48 + '"', text)
                self.assertIn('oauth:\n  auth-dir:', text)
                self.assertEqual(list((home / root / 'auth').iterdir()), [])
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            for p in (home / 'credentials').rglob('*'):
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600)

    @unittest.skipUnless(shutil.which('rsync'), 'rsync unavailable')
    def test_rsync_keeps_history_nested_repos_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp) / 'source', Path(tmp) / 'dest'
            safe = ['.git/objects/ab/object', '.git/refs/heads/main', 'tracked.txt',
                    'research-a/.git/objects/ab/object', 'research-a/research.md',
                    'research-b/.git/objects/ab/object', 'research-b/research.md']
            excluded = ['.env', '.env.local', 'a/.env', 'a/keys.key', 'credentials/x',
                        '.git/config', 'research-b/.git/config', '.codex/auth.json',
                        '.claude/.credentials.json', 'a/auth.json', 'a/.aws/credentials']
            for relative in safe + excluded:
                p = source / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('public fixture')
            source.joinpath('outside-link').symlink_to('/outside/workspace/private')
            subprocess.run(['rsync', '-a', '--safe-links', '--exclude-from=' + str(HERE / 'rsync-excludes'),
                            str(source) + '/', str(dest) + '/'], check=True, stdout=subprocess.DEVNULL)
            for relative in safe:
                self.assertTrue((dest / relative).is_file(), relative)
            for relative in excluded:
                self.assertFalse((dest / relative).exists(), relative)
            self.assertFalse((dest / 'outside-link').is_symlink())

    def test_manifest_includes_nested_status_and_rejects_alternates(self):
        module = load('verify', 'verify-repo.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'research-b/.git').mkdir(parents=True)

            def git(command, **kwargs):
                self.assertEqual(kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
                if 'rev-parse' in command:
                    return b'abc123\n'
                self.assertIn('-z', command)
                return b' M example\0'

            with patch.object(module.subprocess, 'check_output', side_effect=git):
                result = json.loads(module.manifest(root))
                self.assertEqual(set(result), {'.', 'research-b'})
                self.assertEqual(result['.']['head'], 'abc123')
                info = root / '.git/objects/info'
                info.mkdir(parents=True)
                (info / 'alternates').write_text('/external/store')
                with self.assertRaises(ValueError):
                    module.manifest(root)

    def test_manifest_handles_unborn_external_clone(self):
        module = load('verify_unborn', 'verify-repo.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            nested = root / 'external/git/empty'
            (nested / '.git').mkdir(parents=True)

            def git(command, **kwargs):
                if command[2] == str(nested):
                    if 'rev-parse' in command:
                        raise subprocess.CalledProcessError(128, command)
                    if 'symbolic-ref' in command:
                        return b'refs/heads/main\n'
                    if 'for-each-ref' in command:
                        return b''
                    self.fail('External status must not require excluded .env files')
                return b'abc123\n' if 'rev-parse' in command else b''

            with patch.object(module.subprocess, 'check_output', side_effect=git):
                result = json.loads(module.manifest(root))
                self.assertEqual(result['external/git/empty'], {'head': 'unborn:refs/heads/main'})

    def test_active_service_verification_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / 'systemctl'
            stub.write_text('#!/bin/sh\nexit 0\n')
            stub.chmod(0o755)
            result = subprocess.run(['/bin/bash', str(HERE / 'verify-stopped.sh')],
                                    env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH']},
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
