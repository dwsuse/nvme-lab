"""Commands on a lab host, over ssh."""

import shlex
import subprocess


def ssh_options(cfg, batch=True):
    opts = ['-o', 'ConnectTimeout=5']
    if batch:
        opts += ['-o', 'BatchMode=yes']
    if cfg.ssh_key:
        opts += ['-i', cfg.ssh_key, '-o', 'IdentitiesOnly=yes']
    return opts


def ssh_argv(cfg, host, command=None, tty=False):
    """argv of ssh running COMMAND (a list, quoted for the remote shell,
    or a string, taken as shell code) on HOST; no COMMAND: a login."""
    argv = ['ssh'] + ssh_options(cfg, batch=command is not None)
    if tty:
        argv.append('-t')
    argv.append(host.login)
    if command is not None:
        if not isinstance(command, str):
            command = shlex.join(command)
        argv += ['--', command]
    return argv


def git_ssh_command(cfg):
    """GIT_SSH_COMMAND for git over ssh to the lab hosts."""
    return shlex.join(['ssh'] + ssh_options(cfg))


def run(cfg, host, command, **kwargs):
    return subprocess.run(ssh_argv(cfg, host, command), **kwargs)
