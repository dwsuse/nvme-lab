"""The lab configuration: lab.toml plus the user's settings."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB_FILE = REPO / 'lab.toml'
USER_FILE = Path('~/.config/nvme-lab/config.toml')
SCRATCH = '/root/scratch'
# The tools of 'lab boot' that live in other repos, overridden by the
# user's config.toml (target_control =, nvmf_failover =) or the
# environment (LAB_TARGET_CONTROL, LAB_NVMF_FAILOVER).
TARGET_CONTROL = '~/blktests/contrib/nvme-target-control/nvme-target-control'
NVMF_FAILOVER = '~/nvmf-failover'
# The nvme-cli checkout whose dashboard (scripts/dashboard) shows the
# results of 'lab boot test --dashboard' (nvme_cli =, LAB_NVME_CLI)
NVME_CLI = '~/nvme-cli'


class ConfigError(Exception):
    pass


@dataclass
class Host:
    name: str
    address: str
    description: str = ''
    ci: bool = False
    user: str = 'root'

    @property
    def login(self):
        return f'{self.user}@{self.address}'


@dataclass
class Config:
    hosts: dict[str, Host] = field(default_factory=dict)
    ssh_key: str | None = None
    target_control: str = str(Path(TARGET_CONTROL).expanduser())
    nvmf_failover: str = str(Path(NVMF_FAILOVER).expanduser())
    nvme_cli: str = str(Path(NVME_CLI).expanduser())

    def host(self, name):
        """The host NAME; also accepts its address."""
        if name in self.hosts:
            return self.hosts[name]
        for h in self.hosts.values():
            if name == h.address:
                return h
        raise ConfigError(f"unknown host '{name}' "
                          f"(known: {', '.join(self.hosts)})")

    def ci_host(self):
        for h in self.hosts.values():
            if h.ci:
                return h
        raise ConfigError('no host with ci = true in lab.toml')


def _read(path):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f'{path}: {e}') from None


KNOWN_HOST_KEYS = {'address', 'description', 'ci', 'user'}


def load(lab_file=None, user_file=None, env=None):
    env = os.environ if env is None else env
    lab_file = Path(lab_file or env.get('LAB_CONFIG') or LAB_FILE)
    user_file = Path(user_file or USER_FILE).expanduser()

    data = _read(lab_file)
    cfg = Config()
    for name, h in data.get('hosts', {}).items():
        unknown = set(h) - KNOWN_HOST_KEYS
        if unknown:
            raise ConfigError(f"{lab_file}: host '{name}': unknown "
                              f"key(s) {', '.join(sorted(unknown))}")
        cfg.hosts[name] = Host(name=name,
                               address=h.get('address', f'{name}.lan'),
                               description=h.get('description', ''),
                               ci=bool(h.get('ci', False)),
                               user=h.get('user', 'root'))
    if not cfg.hosts:
        raise ConfigError(f'{lab_file}: no [hosts.*]')

    user = _read(user_file) if user_file.is_file() else {}
    cfg.ssh_key = env.get('LAB_SSH_KEY') or user.get('ssh_key')
    if cfg.ssh_key:
        cfg.ssh_key = str(Path(cfg.ssh_key).expanduser())
    for key, var, default in (('target_control', 'LAB_TARGET_CONTROL',
                               TARGET_CONTROL),
                              ('nvmf_failover', 'LAB_NVMF_FAILOVER',
                               NVMF_FAILOVER),
                              ('nvme_cli', 'LAB_NVME_CLI', NVME_CLI)):
        value = env.get(var) or user.get(key) or default
        setattr(cfg, key, str(Path(value).expanduser()))

    return cfg
