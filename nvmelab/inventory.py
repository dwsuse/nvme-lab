"""'lab inventory': run probe.sh on the hosts, show what it found."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import remote

PROBE = Path(__file__).with_name('probe.sh')

# The order of the facts in the text and Markdown output. Unknown keys
# come last, in the order the probe printed them.
ORDER = ['host', 'os', 'kernel', 'kernel-pkg', 'cpu', 'memory', 'address',
         'disk', 'rootfs', 'packages', 'modules', 'nvmet-subsystems',
         'nvmet-ports', 'service', 'nvme', 'etc-nvme', 'tools', 'tlshd',
         'repo', 'dir', 'scratch', 'image']


def parse(text):
    """probe.sh output -> {key: [values]}, keys in the probe's order."""
    facts = {}
    for line in text.splitlines():
        key, sep, value = line.partition('\t')
        if sep and key:
            facts.setdefault(key, []).append(value)
    return facts


def ordered(facts):
    keys = [k for k in ORDER if k in facts]
    keys += [k for k in facts if k not in ORDER]
    return [(k, facts[k]) for k in keys]


def probe(cfg, host):
    """{key: [values]} of HOST, or {'error': [message]}."""
    with open(PROBE, encoding='utf-8') as f:
        script = f.read()
    try:
        p = subprocess.run(remote.ssh_argv(cfg, host, 'sh -s'), input=script,
                           capture_output=True, encoding='utf-8', timeout=120)
    except subprocess.TimeoutExpired:
        return {'error': ['timeout']}
    if p.returncode != 0:
        err = p.stderr.strip().splitlines()
        return {'error': [err[-1] if err else f'exit {p.returncode}']}
    return parse(p.stdout)


def probe_all(cfg, hosts):
    with ThreadPoolExecutor(max_workers=max(len(hosts), 1)) as pool:
        results = pool.map(lambda h: probe(cfg, h), hosts)
        return dict(zip((h.name for h in hosts), results))


def render_text(results):
    lines = []
    for name, facts in results.items():
        lines.append(f'=== {name}')
        for key, values in ordered(facts):
            lines += [f'{key + ":":<18}{v}' for v in values]
        lines.append('')
    return '\n'.join(lines)


def _cell(s):
    return s.replace('|', '\\|')


def render_markdown(results, date):
    """The inventory as Markdown, one section per host."""
    out = [f'# Inventory of the lab VMs ({date})', '',
           'Made with `lab inventory --markdown`; regenerate it after '
           'changing a VM.', '']
    for name, facts in results.items():
        out += [f'## {name}', '', '| | |', '|---|---|']
        for key, values in ordered(facts):
            out.append(f'| {key} | {"<br>".join(_cell(v) for v in values)} |')
        out.append('')
    return '\n'.join(out)
