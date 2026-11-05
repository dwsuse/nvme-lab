"""The lab configuration: lab.toml plus the user's settings."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB_FILE = REPO / 'lab.toml'
USER_FILE = Path('~/.config/nvme-lab/config.toml')
SCRATCH = '/root/scratch'


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

    if user_file.is_file():
        cfg.ssh_key = _read(user_file).get('ssh_key')
    cfg.ssh_key = env.get('LAB_SSH_KEY') or cfg.ssh_key
    if cfg.ssh_key:
        cfg.ssh_key = str(Path(cfg.ssh_key).expanduser())

    return cfg
