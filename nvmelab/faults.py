"""Broken paths while a 'lab boot' guest boots.

A fault names a path of setup (root, root2, extra) and what happens to
it from before the guest starts until it is off again:

    port-removed  the target port is gone (nvme-target-control
                  remove-port; added again afterwards): connects to it
                  are refused
    ana:STATE     the port's ANA group is in STATE (nvme-target-control
                  set-ana), e.g. inaccessible, non-optimized; optimized
                  afterwards
    tcp-refuse    the CI host refuses the guest's connections to the port
                  (nvmf-failover tcp-refuse, nftables reject)
    tcp-drop      the CI host drops what the port sends (nvmf-failover
                  tcp-port): connects wait for their SYN retries

The guest's connections leave QEMU's user networking on the CI host, so
the network faults run there, with the nvmf-failover checkout of the
configuration, synced to the CI host for each run.

As command line spec: KIND:PATH, e.g. port-removed:root2,
ana:inaccessible:root2.
"""

import shlex
import sys

from . import remote, sync
from .config import ConfigError
from .target import TargetControl

KINDS = ('port-removed', 'ana', 'tcp-refuse', 'tcp-drop')
ANA_STATES = ('optimized', 'non-optimized', 'inaccessible',
              'persistent-loss', 'change')
NETWORK = {'tcp-refuse': 'tcp-refuse', 'tcp-drop': 'tcp-port'}
FAILOVER_TREE = 'lab-boot-failover'


def make(kind, path, state=None):
    """A fault as a dict, checked."""
    if kind not in KINDS:
        raise ConfigError(f"bad fault '{kind}': {', '.join(KINDS)}")
    if not path:
        raise ConfigError(f'fault {kind}: no path (root, root2, extra)')
    if kind == 'ana':
        if state not in ANA_STATES:
            raise ConfigError(f"fault ana: bad state '{state}': "
                              f"{', '.join(ANA_STATES)}")
    elif state is not None:
        raise ConfigError(f'fault {kind}: takes no state')
    return {'kind': kind, 'path': path, 'state': state}


def parse(spec):
    """KIND:PATH or ana:STATE:PATH."""
    words = spec.split(':')
    if len(words) == 3 and words[0] == 'ana':
        return make('ana', words[2], words[1])
    if len(words) == 2:
        return make(words[0], words[1])
    raise ConfigError(f"bad --fault '{spec}': KIND:PATH or ana:STATE:PATH")


def failover_argv(kind, traddr, trsvcid, action):
    """The nvmf-failover command of a network fault, on the CI host."""
    tree = sync.scratch_dir(FAILOVER_TREE)
    return (f'PYTHONPATH={shlex.quote(tree)}/src python3 -m nvmf_failover.cli '
            f'fault {NETWORK[kind]} {shlex.quote(f"{traddr}:{trsvcid}")} '
            f'{action}')


def clear_network(cfg, host):
    """Remove the nftables table of nvmf-failover's TCP faults on HOST,
    e.g. after an interrupted run."""
    remote.run(cfg, host, 'command -v nft >/dev/null || exit 0; '
               'nft list table inet nvmf_failover >/dev/null 2>&1 || exit 0; '
               'nft delete table inet nvmf_failover', check=True)


class Faults:
    """Context manager: the faults SPECS down for the 'with' block, then
    up again, in reverse order, also on errors. ON_CHANGE(env) is called
    when a path came back on another port."""

    def __init__(self, cfg, host, env, specs, on_change=None):
        self.cfg, self.host, self.env = cfg, host, env
        self.specs = list(specs)
        self.on_change = on_change
        self.undo = []
        self._target = None
        self._synced = False

    def target(self):
        if self._target is None:
            self._target = TargetControl(self.cfg,
                                         self.cfg.host(self.env.target_host),
                                         self.env.traddr)
        return self._target

    def failover(self, kind, trsvcid, action):
        if not self._synced:
            sync.sync(self.cfg, self.host, self.cfg.nvmf_failover,
                      FAILOVER_TREE, worktree=True, log=lambda *a: None)
            self._synced = True
        remote.run(self.cfg, self.host,
                   failover_argv(kind, self.env.traddr, trsvcid, action),
                   check=True, capture_output=True)

    def down(self, f):
        port, trsvcid = self.env.path(f['path'])
        kind = f['kind']
        print(f"fault: {kind}{':' + f['state'] if f['state'] else ''} "
              f"on {f['path']} (port {port}, {self.env.traddr}:{trsvcid})",
              flush=True)
        if kind == 'port-removed':
            self.target().remove_port(port)
            self.undo.append(lambda: self.add_back(f['path'], port,
                                                   trsvcid))
        elif kind == 'ana':
            self.target().set_ana(port, f['state'])
            self.undo.append(lambda: self.target().set_ana(port, 'optimized'))
        else:
            self.failover(kind, trsvcid, 'down')
            self.undo.append(lambda: self.failover(kind, trsvcid, 'up'))

    def add_back(self, path, old_port, old_trsvcid):
        """Export the path's subsystem on a new port again. The target
        gives it the lowest free port ID, so usually the old port and
        service ID."""
        from .boot import EXTRA_NQN, ROOT_NQN
        nqn = EXTRA_NQN if path == 'extra' else ROOT_NQN
        port = self.target().add_port(nqn)
        trsvcid = self.target().trsvcid(port)
        if trsvcid != old_trsvcid:
            print(f'fault: {path} came back at {trsvcid}, was {old_trsvcid}',
                  file=sys.stderr)
        self.env.paths[path] = (port, trsvcid)
        if self.on_change and (port, trsvcid) != (old_port, old_trsvcid):
            self.on_change(self.env)

    def __enter__(self):
        try:
            for f in self.specs:
                self.down(f)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        errors = []
        while self.undo:
            try:
                self.undo.pop()()
            except Exception as e:  # ConfigError, CalledProcessError, ...
                errors.append(str(e))
        if errors and exc[0] is None:
            raise ConfigError('restoring the faults: ' + '; '.join(errors))
        return False
