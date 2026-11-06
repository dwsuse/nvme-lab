"""The results of 'lab boot test' on nvme-cli's dashboard.

nvme-cli's dashboard (scripts/dashboard/nvme-dashboard) builds a static
site from the records of test runs. 'lab boot test --dashboard DIR'
turns its cases into such a record: the workflow 'Boot tests', no CI
jobs, a section per nvme-cli version and a test per scenario, with the
status the dashboard knows (pass, fail, expected fail; an XPASS is a
failure) and, for every case that did not simply pass, its console, log,
dracut log and state as files of the site.

The site is built on the CI host, which has Jinja2, with the nvme-cli
checkout of the configuration (nvme_cli, LAB_NVME_CLI; default
~/nvme-cli) synced there. Its history and logs stay on the CI host
(SITE_DIR, outside of 'lab boot', so a teardown keeps them): every run
adds a record to the history of the last one. The whole site is then
copied to DIR, for a browser to open DIR/index.html.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from . import remote, sync
from .config import SCRATCH, ConfigError
from .slots import BOOT_DIR

WORKFLOW = 'Boot tests'
RECORD_VERSION = 2
DASHBOARD_DIR = f'{SCRATCH}/lab-dashboard'
TOOL_TREE = 'lab-dashboard-tool'
DEFAULT_DIR = Path('~/.cache/nvme-lab/dashboard').expanduser()
STATUS = {'PASS': 'pass', 'FAIL': 'fail', 'XFAIL': 'expected fail',
          'XPASS': 'fail'}
# The results of a case the site links to, as they are in out/NAME
LOG_FILES = (('console', 'console.txt'), ('log', 'log'),
             ('dracut', 'dracut.log'), ('state', 'state.txt'),
             ('cmdline', 'cmdline'))


def section_name(tree):
    if tree.version:
        return f'nvme-cli {tree.version}'
    return f'nvme-cli {tree.label}'


def test_item(case):
    """A case as a test of a dashboard section."""
    item = {'test': case.scenario.id, 'config': case.tree.label,
            'status': STATUS[case.result],
            'duration': f'{case.duration:.0f}s'}
    reason = []
    if case.result == 'XPASS':
        reason.append(f'passes, but is expected to fail: {case.reason}')
    elif case.result == 'XFAIL':
        reason.append(case.reason or '')
    reason += case.errors if case.result != 'XFAIL' else []
    if any(reason):
        item['reason'] = '; '.join(r for r in reason if r)
    if case.result != 'PASS':
        item['files'] = [{'href': f'logs/{case.name}/{f}', 'name': label}
                         for label, f in LOG_FILES]
    return item


def record(cases, started, note=''):
    """The record of a run of 'lab boot test' that started at STARTED
    (a UTC datetime), for nvme-dashboard site."""
    trees = list(dict.fromkeys(c.tree.label for c in cases))
    sections = []
    for label in trees:
        mine = [c for c in cases if c.tree.label == label]
        counts = {'pass': 0, 'fail': 0, 'expected fail': 0}
        for c in mine:
            counts[STATUS[c.result]] += 1
        failed = [c for c in mine if c.bad]
        problems = [f'{len(failed)} failed: '
                    f"{', '.join(c.scenario.id for c in failed)}"] \
            if failed else []
        sections.append({
            'name': section_name(mine[0].tree), 'kind': 'boot', 'job': '',
            'step': '', 'outcome': 'fail' if failed else 'pass',
            'problems': problems, 'counts': counts,
            'tests': [test_item(c) for c in mine]})
    problems = [f"{s['name']}: {'; '.join(s['problems'])}"
                for s in sections if s['problems']]
    if not cases:
        problems.append('no results')
    return {
        'version': RECORD_VERSION, 'workflow': WORKFLOW,
        'run_id': started.strftime('%Y%m%d-%H%M%S'), 'run_url': '',
        'event': 'lab', 'date': started.strftime('%Y-%m-%d %H:%M:%S'),
        'nvme_cli': '', 'conclusion': 'failure' if problems else 'success',
        'jobs': [], 'sections': sections,
        'outcome': 'fail' if problems else 'pass', 'problems': problems,
        'warnings': [note] if note else [],
    }


def describe_module(module):
    """The dracut module MODULE (a local directory) as its checkout:
    'dracut BRANCH COMMIT', with ' (modified)' for local changes there;
    the directory itself if it is no git checkout."""
    def git(*args):
        out = subprocess.run(['git', '-C', str(module), *args],
                             capture_output=True, encoding='utf-8')
        return out.stdout.strip() if out.returncode == 0 else None
    commit = git('log', '-1', '--format=%h %s')
    if not commit:
        return f'dracut module {module}'
    branch = git('rev-parse', '--abbrev-ref', 'HEAD') or ''
    dirty = git('status', '--porcelain', '--', '.')
    return (f"dracut {branch + ' ' if branch != 'HEAD' else ''}{commit}"
            f"{' (modified)' if dirty else ''}")


def parse_link(text):
    label, sep, url = text.partition('=')
    if not sep or not label or not url:
        raise ConfigError(f"bad --dashboard-link '{text}': TEXT=URL")
    return label, url


def site_argv(run_id, links=()):
    """The shell command that builds the site on the CI host."""
    tool = f'{sync.scratch_dir(TOOL_TREE)}/scripts/dashboard'
    site = f'{DASHBOARD_DIR}/site'
    argv = ['python3', f'{tool}/nvme-dashboard', 'site', '-o', site,
            '--record', f'{DASHBOARD_DIR}/records/{run_id}.json',
            '--logs', f'{DASHBOARD_DIR}/logs-new', '--primary', WORKFLOW]
    for label, url in links:
        argv += ['--link', f'{label}={url}']
    cmd = shlex.join(argv)
    history = f'{site}/history.json'
    return (f'if [ -f {history} ]; then {cmd} --history {history}; '
            f'else {cmd}; fi')


def publish(cfg, host, cases, started, dest, module=None, links=()):
    """Add the record of CASES to the dashboard on HOST and copy the site
    to DEST (True: the default directory). Returns the local site."""
    tool = Path(cfg.nvme_cli)
    if not (tool / 'scripts/dashboard/nvme-dashboard').is_file():
        raise ConfigError(f'no scripts/dashboard/nvme-dashboard in {tool} '
                          '(nvme_cli in ~/.config/nvme-lab/config.toml, or '
                          'LAB_NVME_CLI)')
    dest = DEFAULT_DIR if dest is True else Path(dest).expanduser()
    note = describe_module(module) if module else ''
    rec = record(cases, started, note)
    run_id = rec['run_id']
    d = DASHBOARD_DIR
    remote.run(cfg, host, f'mkdir -p {d}/records && rm -rf {d}/logs-new && '
               f'mkdir -p {d}/logs-new', check=True)
    remote.run(cfg, host, f'cat > {d}/records/{run_id}.json',
               input=json.dumps(rec, indent=1).encode(), check=True)
    # The logs of the cases that did not simply pass
    copies = []
    for c in cases:
        if c.result == 'PASS':
            continue
        src = f'{BOOT_DIR}/out/{c.name}'
        dst = f'{d}/logs-new/{c.name}'
        copies.append(f'mkdir -p {dst}')
        copies += [f'cp {src}/{f} {dst}/{f} 2>/dev/null || true'
                   for _, f in LOG_FILES]
    if copies:
        remote.run(cfg, host, ' && '.join(copies) + ' || true', check=True)
    sync.sync(cfg, host, tool, TOOL_TREE, worktree=True, log=lambda *a: None)
    remote.run(cfg, host, site_argv(run_id, links), check=True)

    # The site, here
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    tar = subprocess.Popen(remote.ssh_argv(cfg, host,
                                           f'tar -C {d}/site -cf - .'),
                           stdout=subprocess.PIPE)
    subprocess.run(['tar', '-C', str(dest), '-xf', '-'], stdin=tar.stdout,
                   check=True)
    tar.stdout.close()
    if tar.wait():
        raise ConfigError('copying the dashboard failed')
    return dest
