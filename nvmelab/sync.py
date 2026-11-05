"""'lab sync': put a local git tree on a host, as a git checkout in
/root/scratch/NAME.

The commit is pushed with git (fast, only what's new), then checked
out detached. With --worktree, the uncommitted changes go on top:
changed and untracked files (not the ignored ones) are copied with
tar, deleted ones are deleted. The build directory on the host stays,
so a rebuild after a sync is incremental.
"""

import os
import re
import shlex
import subprocess
from pathlib import Path

from . import remote
from .config import SCRATCH, ConfigError

NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def check_name(name):
    if not NAME_RE.match(name):
        raise ConfigError(f"bad scratch name '{name}': letters, digits, "
                          "'.', '_', '-'; not starting with '.' or '-'")
    return name


def scratch_dir(name):
    return f'{SCRATCH}/{check_name(name)}'


def git(tree, *args, **kwargs):
    return subprocess.run(['git', '-C', str(tree), *args], check=True,
                          capture_output=True, **kwargs).stdout


def git_text(tree, *args):
    return git(tree, *args, encoding='utf-8').strip()


def changes(top):
    """(changed or untracked files, deleted files) relative to HEAD."""
    changed = git(top, 'diff', '-z', '--name-only', '--diff-filter=d',
                  'HEAD')
    untracked = git(top, 'ls-files', '-z', '-o', '--exclude-standard')
    deleted = git(top, 'diff', '-z', '--name-only', '--diff-filter=D',
                  'HEAD')
    return (changed + untracked), deleted


def sync(cfg, host, tree='.', name=None, ref='HEAD', worktree=False,
         log=print):
    try:
        top = git_text(tree, 'rev-parse', '--show-toplevel')
        sha = git_text(top, 'rev-parse', '--verify', '--end-of-options',
                       ref + '^{commit}')
    except subprocess.CalledProcessError as e:
        raise ConfigError(e.stderr.decode().strip() or str(e)) from None
    if worktree and ref != 'HEAD':
        raise ConfigError('--worktree only goes with the checked out '
                          'commit (no --ref)')
    dest = scratch_dir(name or Path(top).name)
    q = shlex.quote(dest)

    remote.run(cfg, host, f'mkdir -p {q} && cd {q} && '
               '{ [ -d .git ] || git init -q; }', check=True)
    env = dict(os.environ, GIT_SSH_COMMAND=remote.git_ssh_command(cfg))
    subprocess.run(['git', '-C', top, 'push', '-q', '--force',
                    f'ssh://{host.login}{dest}', f'{sha}:refs/lab/sync'],
                   check=True, env=env)
    remote.run(cfg, host, f'git -C {q} checkout -q -f --detach {sha}',
               check=True)
    log(f'{host.name}:{dest} at {sha[:12]} ({ref})')

    if worktree:
        copy, delete = changes(top)
        if copy:
            files = subprocess.Popen(['tar', '--null', '-T', '-', '-cf',
                                      '-'], cwd=top, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)
            put = subprocess.Popen(remote.ssh_argv(cfg, host,
                                                   f'tar -C {q} -xf -'),
                                   stdin=files.stdout)
            files.stdout.close()
            files.communicate(copy)
            if put.wait() or files.returncode:
                raise ConfigError('copying the worktree changes failed')
        if delete:
            remote.run(cfg, host, f'cd {q} && xargs -0 rm -f --',
                       input=delete, check=True)
        n_copy = copy.count(b'\0')
        n_del = delete.count(b'\0')
        log(f'  + worktree: {n_copy} file(s) copied, {n_del} deleted')

    return dest
