"""The target of 'lab boot', through nvme-target-control.

nvme-target-control (blktests, contrib/nvme-target-control) sets up and
tears down subsystems on a remote target, exports them on ports, removes
ports and sets ANA states; for nvmet over ssh, for an ONTAP SVM through
its REST API. lab runs it locally with a configuration of its own: the
target host's ssh login, and the address the guest's connections use.

Ports get service IDs from TRSVID on (the port ID N gets TRSVID + N - 1),
away from the default target's 4420 and the 4421+ of 'nvmet-target
add-subsys'.
"""

import os
import subprocess
from pathlib import Path

from . import remote
from .config import ConfigError

TRSVID = '4430'
CACHE = Path('~/.cache/nvme-lab/lab-boot').expanduser()


def toml_str(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def config_text(cfg, host, traddr):
    """nvme-target-control's configuration for the nvmet target HOST,
    which the guest reaches at TRADDR."""
    opts = ', '.join(toml_str(o) for o in remote.ssh_options(cfg))
    return ('# written by lab boot (nvmelab/target.py)\n'
            '[target]\n'
            'type = "nvmet"\n'
            f'remote = {toml_str(host.login)}\n'
            f'ssh_options = [{opts}]\n'
            f'traddr = {toml_str(traddr)}\n'
            f'trsvid = {toml_str(TRSVID)}\n'
            'img_size = "1G"\n')


class TargetControl:
    def __init__(self, cfg, host, traddr, cache=CACHE):
        self.tool = cfg.target_control
        if not Path(self.tool).is_file():
            raise ConfigError(f'no nvme-target-control at {self.tool} '
                              '(target_control in ~/.config/nvme-lab/'
                              'config.toml, or LAB_TARGET_CONTROL)')
        cache.mkdir(parents=True, exist_ok=True)
        self.config = cache / f'target-control-{host.name}.toml'
        self.config.write_text(config_text(cfg, host, traddr),
                               encoding='utf-8')

    def argv(self, *args):
        return [self.tool, *args]

    def run(self, *args, check=True):
        """Run the tool with ARGS; its stdout as lines."""
        env = dict(os.environ, NVME_TARGET_CONTROL_CONFIG=str(self.config))
        out = subprocess.run(self.argv(*args), env=env, encoding='utf-8',
                             capture_output=True)
        if check and out.returncode:
            raise ConfigError(f"nvme-target-control {' '.join(args)}: "
                              f'{out.stderr.strip() or out.returncode}')
        return out.stdout.split()

    def setup(self, nqn, uuid, hostnqn, ports=1):
        self.run('setup', '--subsysnqn', nqn, '--subsys-uuid', uuid,
                 '--hostnqn', hostnqn, '--ports', str(ports))

    def cleanup(self, nqn, check=True):
        self.run('cleanup', '--subsysnqn', nqn, check=check)

    def list_ports(self, nqn):
        return self.run('list-ports', '--subsysnqn', nqn)

    def trsvcid(self, port):
        """The service ID of PORT ('show-port': trtype traddr trsvcid)."""
        return self.run('show-port', '--port', port)[2]

    def ports(self, nqn):
        """[(port ID, service ID)] of the ports exporting NQN."""
        return [(p, self.trsvcid(p)) for p in sorted(self.list_ports(nqn),
                                                    key=int)]

    def add_port(self, nqn):
        """Export NQN on one new port; its ID."""
        ids = self.run('add-ports', '--subsysnqn', nqn, '--ports', '1')
        if len(ids) != 1:
            raise ConfigError(f'add-ports printed {ids}, expected one port')
        return ids[0]

    def remove_port(self, port):
        self.run('remove-port', '--port', port)

    def set_ana(self, port, state, grpid=1):
        self.run('set-ana', '--port', port, '--grpid', str(grpid),
                 '--state', state)
