"""The slots of 'lab boot': independent boot environments that run side
by side.

A slot has its own container on the CI host (nvme-cli installs, dracut's
module and /etc/nvme are per container), its own subsystems on the
target (two guests must not mount one root file system), its own host
identity (so the target's access lists keep the slots apart), and its
own directory for the scripts and target.env. The build directory of an
nvme-cli tree is per slot too (TREE/.lab-boot-N).
"""

import uuid
from dataclasses import dataclass

from .config import SCRATCH

BOOT_DIR = f'{SCRATCH}/lab-boot'
# /root/scratch is /scratch in the containers.
CONTAINER_DIR = '/scratch/lab-boot'
IMAGE = 'nvme-lab/lab-boot'
NQN_PREFIX = 'nqn.2026-11.io.lab'
# The container of the lab before slots
OLD_CONTAINER = 'lab-boot'
OLD_NQNS = (f'{NQN_PREFIX}:lab-boot-root', f'{NQN_PREFIX}:lab-boot-extra')

_NAMESPACE = uuid.UUID('0b5e7c6a-9d1f-4c2e-8a3b-7f6e5d4c3b2a')
# Slot 1 keeps the identity the lab had before slots
_HOST_ID_1 = '6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'


@dataclass(frozen=True)
class Slot:
    n: int = 1

    def _uuid(self, what):
        return str(uuid.uuid5(_NAMESPACE, f'{what}-{self.n}'))

    @property
    def container(self):
        return f'lab-boot-{self.n}'

    @property
    def dir(self):
        """The slot's directory on the CI host."""
        return f'{BOOT_DIR}/slot-{self.n}'

    @property
    def container_dir(self):
        """The slot's directory in the containers."""
        return f'{CONTAINER_DIR}/slot-{self.n}'

    @property
    def root_nqn(self):
        return f'{NQN_PREFIX}:lab-boot-root-{self.n}'

    @property
    def extra_nqn(self):
        return f'{NQN_PREFIX}:lab-boot-extra-{self.n}'

    @property
    def host_id(self):
        return _HOST_ID_1 if self.n == 1 else self._uuid('host')

    @property
    def host_nqn(self):
        return f'nqn.2014-08.org.nvmexpress:uuid:{self.host_id}'

    @property
    def root_subsys_uuid(self):
        return self._uuid('root')

    @property
    def extra_subsys_uuid(self):
        return self._uuid('extra')

    @property
    def roles(self):
        """{NQN: role} of the slot's subsystems, for the scenarios."""
        return {self.root_nqn: 'root', self.extra_nqn: 'extra'}

    def nqn_of(self, path):
        """The subsystem a path of setup leads to."""
        return self.extra_nqn if path == 'extra' else self.root_nqn


def slots(count):
    if count < 1:
        raise ValueError('at least one slot')
    return [Slot(n) for n in range(1, count + 1)]
