"""lab: drive the nvme-cli test VMs from a workstation.

Hosts are the [hosts.*] of lab.toml. The ssh key comes from
LAB_SSH_KEY or ~/.config/nvme-lab/config.toml (ssh_key = "..."), else
from ssh's own config.
"""

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys

from . import ci, config, inventory, remote, sync
from .config import SCRATCH, ConfigError


def cmd_hosts(cfg, args):
    for h in cfg.hosts.values():
        flags = ' [ci]' if h.ci else ''
        print(f'{h.name:<10} {h.login:<22} {h.description}{flags}')
    return 0


def cmd_inventory(cfg, args):
    hosts = [cfg.host(n) for n in args.host] or list(cfg.hosts.values())
    results = inventory.probe_all(cfg, hosts)
    if args.json:
        print(json.dumps(results, indent=2))
    elif args.markdown:
        print(inventory.render_markdown(results, datetime.date.today()))
    else:
        print(inventory.render_text(results), end='')
    return 1 if any('error' in r for r in results.values()) else 0


def cmd_ssh(cfg, args):
    host = cfg.host(args.host)
    # Like ssh: the words of the command are joined with spaces and run
    # by the remote shell.
    command = ' '.join(args.command) if args.command else None
    argv = remote.ssh_argv(cfg, host, command)
    if command is None:
        os.execvp(argv[0], argv)
    return subprocess.run(argv).returncode


def cmd_sync(cfg, args):
    sync.sync(cfg, cfg.host(args.host), tree=args.tree, name=args.name,
              ref=args.ref, worktree=args.worktree)
    return 0


def cmd_ci(cfg, args):
    host = cfg.host(args.host) if args.host else cfg.ci_host()
    src = sync.scratch_dir(args.name)
    command = ' '.join(args.command)
    if not command:
        raise ConfigError('no command (lab ci IMAGE NAME -- COMMAND)')
    argv = ci.ci_argv(cfg, host, args.image, src, command, pull=args.pull)
    if args.dry_run:
        print(shlex.join(argv))
        return 0
    return subprocess.run(argv).returncode


def cmd_scratch(cfg, args):
    host = cfg.host(args.host)
    if args.rm:
        dirs = [sync.scratch_dir(n) for n in args.rm]
        return remote.run(cfg, host, ['rm', '-rf', '--', *dirs]).returncode
    return remote.run(cfg, host, f'cd {SCRATCH} 2>/dev/null && '
                      'du -sh -- * 2>/dev/null || true').returncode


def parser():
    p = argparse.ArgumentParser(
        prog='lab', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', help='lab.toml to use (default: the one of '
                   'this repo, or LAB_CONFIG)')
    sub = p.add_subparsers(dest='cmd', required=True, metavar='COMMAND')

    s = sub.add_parser('hosts', help='list the hosts')
    s.set_defaults(fn=cmd_hosts)

    s = sub.add_parser('inventory', help='show what is on the hosts '
                       '(read only)')
    s.add_argument('host', nargs='*', help='default: all')
    fmt = s.add_mutually_exclusive_group()
    fmt.add_argument('--json', action='store_true')
    fmt.add_argument('--markdown', action='store_true',
                     help='for docs/inventory.md')
    s.set_defaults(fn=cmd_inventory)

    s = sub.add_parser('ssh', help='log in, or run a command')
    s.add_argument('host')
    s.add_argument('command', nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_ssh)

    s = sub.add_parser('sync', help=f'put a git tree on a host, in '
                       f'{SCRATCH}/NAME',
                       description=sync.__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument('host')
    s.add_argument('-C', '--tree', default='.',
                   help='the local git tree (default: .)')
    s.add_argument('-n', '--name', help='scratch name (default: the '
                   "tree's directory name)")
    s.add_argument('-r', '--ref', default='HEAD',
                   help='commit to put there (default: HEAD)')
    s.add_argument('-w', '--worktree', action='store_true',
                   help='also the uncommitted changes')
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser('ci', help="run a command in an nvme-cli CI image "
                       "on a synced tree",
                       usage='lab ci [-h] [--host HOST] [--pull] [--dry-run] '
                       'IMAGE NAME -- COMMAND...',
                       description=ci.__doc__ + '\nCOMMAND is shell code, '
                       'run in the tree. Example:\n  lab ci debian wt-4351 '
                       '-- scripts/build.sh -b release -c gcc',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument('image', help='debian, tumbleweed, fedora, ... or a '
                   'full image ref')
    s.add_argument('name', help=f'the tree, {SCRATCH}/NAME')
    s.set_defaults(command=[])
    s.add_argument('--host', help='default: the host with ci = true')
    s.add_argument('--pull', action='store_true',
                   help='pull the newest image first')
    s.add_argument('--dry-run', action='store_true',
                   help='print the ssh command, run nothing')
    s.set_defaults(fn=cmd_ci)

    s = sub.add_parser('scratch', help=f'list or remove the trees in '
                       f'{SCRATCH}')
    s.add_argument('host')
    s.add_argument('--rm', nargs='+', metavar='NAME',
                   help='remove these trees')
    s.set_defaults(fn=cmd_scratch)

    return p


def main(argv=None):
    # Everything after the first '--' is the command, argparse doesn't
    # see it: 'lab ci debian x --dry-run -- cmd --help' works.
    argv = sys.argv[1:] if argv is None else list(argv)
    tail = None
    if '--' in argv:
        i = argv.index('--')
        argv, tail = argv[:i], argv[i + 1:]
    args = parser().parse_args(argv)
    if tail is not None:
        args.command = (getattr(args, 'command', None) or []) + tail
    try:
        cfg = config.load(args.config)
        return args.fn(cfg, args)
    except ConfigError as e:
        print(f'lab: {e}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f'lab: failed: {shlex.join(map(str, e.cmd))}', file=sys.stderr)
        return e.returncode or 1
    except KeyboardInterrupt:
        return 130
