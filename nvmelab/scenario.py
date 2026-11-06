"""Boot scenarios: a boot case and what the booted root must see.

A scenario is a TOML file in boot/scenarios/:

    name = "NBFT boot, a non-bootable second subsystem"
    tags = ["nbft"]

    [boot]                      # the options of 'lab boot run'
    source = "none"             # cmdline, static, legacy, none
    hostonly = false
    nbft = true
    nbft_ssns = ["extra,nonboot"]
    nbft_discovery = []
    nbft_no_root = false
    append = ["rd.nvmf.discover=tcp,{traddr},,{extra}"]
    timeout = 240

    [expect]
    result = "pass"             # pass: the root's /init ran; or fail
    controllers = ["root", "extra"]
    owners = { root = "pre-os", extra = "" }

In append, nbft_ssns and nbft_discovery, {traddr} and {trsvcid} are the
root subsystem's target, {extra} is the port of the extra subsystem.
source defaults to cmdline, or to none with an NBFT.

expect.controllers is the set of subsystems that have a controller after
switch-root: root, extra (the subsystems of 'lab boot setup') or an NQN.
expect.owners is the nvme-cli registry owner of every controller of a
subsystem, "" for unowned. Both are checked only when given, against the
state the root's /init reports (state.txt, see boot/mkroot.sh).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError

DIR = Path(__file__).resolve().parent.parent / 'boot' / 'scenarios'

BOOT_KEYS = {'source': str, 'hostonly': bool, 'nbft': bool,
             'nbft_ssns': list, 'nbft_discovery': list,
             'nbft_no_root': bool, 'append': list, 'timeout': int}
EXPECT_KEYS = {'result': str, 'controllers': list, 'owners': dict}


@dataclass
class Scenario:
    id: str                     # the file name without .toml
    name: str
    tags: list = field(default_factory=list)
    boot: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)


def check_table(path, table, keys, what):
    for key, value in table.items():
        if key not in keys:
            raise ConfigError(f"{path}: unknown key '{key}' in {what}")
        if not isinstance(value, keys[key]):
            raise ConfigError(f"{path}: {what}.{key} must be a "
                              f'{keys[key].__name__}')


def load(path):
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f'{path}: {e}') from None
    unknown = set(data) - {'name', 'tags', 'boot', 'expect'}
    if unknown:
        raise ConfigError(f"{path}: unknown key '{sorted(unknown)[0]}'")
    s = Scenario(id=path.stem, name=data.get('name', path.stem),
                 tags=data.get('tags', []), boot=data.get('boot', {}),
                 expect=data.get('expect', {}))
    check_table(path, s.boot, BOOT_KEYS, 'boot')
    check_table(path, s.expect, EXPECT_KEYS, 'expect')
    if s.expect.get('result', 'pass') not in ('pass', 'fail'):
        raise ConfigError(f'{path}: expect.result must be pass or fail')
    return s


def find(names=(), tags=()):
    """The scenarios NAMES (ids or paths; default: all of DIR) that have
    one of TAGS (default: any)."""
    if names:
        paths = [Path(n) if n.endswith('.toml') else DIR / f'{n}.toml'
                 for n in names]
    else:
        paths = sorted(DIR.glob('*.toml'))
    scenarios = [load(p) for p in paths]
    if tags:
        scenarios = [s for s in scenarios if set(tags) & set(s.tags)]
    return scenarios


def expand(value, values):
    """VALUE with the placeholders of VALUES filled in."""
    try:
        return value.format(**values)
    except (KeyError, IndexError) as e:
        raise ConfigError(f"unknown placeholder {e} in '{value}'") from None


def run_options(s, target, extra):
    """The options of boot.run() for scenario S: run_argv()'s, plus
    nbft_options."""
    values = {'traddr': target[0], 'trsvcid': target[1],
              'extra': extra or ''}
    if '{extra}' in str(s.boot) and not extra:
        raise ConfigError("no extra subsystem: run 'lab boot setup' again")
    b = s.boot
    nbft = None
    if b.get('nbft') or b.get('nbft_ssns') or b.get('nbft_discovery') \
            or b.get('nbft_no_root'):
        nbft = dict(root=not b.get('nbft_no_root', False),
                    ssns=[expand(v, values) for v in b.get('nbft_ssns', [])],
                    discovery=[expand(v, values)
                               for v in b.get('nbft_discovery', [])])
    append = ' '.join(expand(v, values) for v in b.get('append', []))
    return dict(source=b.get('source',
                             'none' if nbft is not None else 'cmdline'),
                hostonly=b.get('hostonly', False), append=append or None,
                timeout=b.get('timeout'), nbft_options=nbft)


def parse_state(text):
    """state.txt: {controller: (subsysnqn, owner or None)}; None: no
    registry entry or no owner."""
    ctrls, owners = {}, {}
    for line in text.splitlines():
        words = line.split()
        if len(words) == 4 and words[0] == 'ctrl':
            ctrls[words[1]] = words[3]
        elif len(words) == 3 and words[0] == 'registry' \
                and words[2].startswith('owner='):
            owners[words[1]] = words[2][len('owner='):] or None
    return {c: (nqn, owners.get(c)) for c, nqn in ctrls.items()}


def verdict(s, passed, state, roles):
    """What is wrong with the run of S (empty: nothing). PASSED: the
    root's /init ran; STATE: parse_state(); ROLES: {nqn: role}."""
    errors = []
    want = s.expect.get('result', 'pass')
    if (want == 'pass') != passed:
        errors.append(f"boot {'passed' if passed else 'failed'}, "
                      f'expected {want}')
    if not passed:
        return errors
    by_subsys = {}
    for ctrl, (nqn, owner) in sorted(state.items()):
        by_subsys.setdefault(roles.get(nqn, nqn), []).append((ctrl, owner))
    if 'controllers' in s.expect:
        want = sorted(set(s.expect['controllers']))
        if sorted(by_subsys) != want:
            errors.append(f'controllers of {sorted(by_subsys)}, expected '
                          f'{want}')
    for subsys, owner in s.expect.get('owners', {}).items():
        for ctrl, got in by_subsys.get(subsys, []):
            if (got or '') != owner:
                errors.append(f"{subsys} {ctrl}: owner '{got or ''}', "
                              f"expected '{owner}'")
    return errors
