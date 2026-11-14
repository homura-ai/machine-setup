#!/usr/bin/env python3
"""Verify all Git heads; exact status for the main repo and any nested trees named in
$VERIFY_EXACT_STATUS (colon-separated top-level folder names)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def manifest(root):
    root = Path(root).resolve()
    repositories = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        if '.git' in files:
            raise ValueError('Linked worktree/submodule .git file needs a self-contained copy')
        if '.git' in dirs:
            path = Path(directory)
            if (path / '.git/objects/info/alternates').exists():
                raise ValueError('External Git object store: cannot copy self-contained history')
            repositories.append(path)
            dirs.remove('.git')
        dirs[:] = [d for d in dirs if d not in {'credentials', '.ssh', '.aws', '.gnupg',
                                               '.cli-proxy-api', '.cli-proxy-api-claude'}]
    if root not in repositories:
        raise ValueError('Main repo is missing')
    result = {}
    for path in sorted(repositories):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.DEVNULL,
                                           env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        try:
            head = git('rev-parse', '--verify', 'HEAD').decode().strip()
        except subprocess.CalledProcessError:
            if path == root:
                raise
            # Some untracked research clones have no first commit yet.
            branch = git('symbolic-ref', '--quiet', 'HEAD').decode().strip()
            refs = git('for-each-ref', '--format=%(refname)', branch).decode().splitlines()
            if branch in refs:
                raise ValueError('Referenced Git objects missing')
            head = 'unborn:' + branch
        relative = path.relative_to(root)
        entry = {'head': head}
        # External clones can track .env examples, which must still be excluded.
        # The main repo and the named nested worktrees must match exactly.
        exact = {x for x in os.environ.get('VERIFY_EXACT_STATUS', '').split(':') if x}
        if path == root or relative.parts[0] in exact:
            status = git('status', '--porcelain=v1', '-z', '--untracked-files=normal')
            entry['status_sha256'] = hashlib.sha256(status).hexdigest()
        result[str(relative)] = entry
    return json.dumps(result, sort_keys=True)


if __name__ == '__main__':
    try:
        print(manifest(sys.argv[1]))
    except Exception:
        print('Repository verification failed; check self-contained Git metadata.', file=sys.stderr)
        sys.exit(1)
