"""'lab parity': the CI-parity runs of an nvme-cli tree before a push.

Each job gets its own tree, NAME-JOB in the scratch directory of the CI
host, because scripts/build.sh owns the .build-ci of its tree. The trees
are synced, then the jobs run in parallel in nvme-cli's CI images, each
logging to LOGDIR/JOB.log. The trees stay, so the next run is
incremental; remove them with 'lab scratch HOST --rm'.

Jobs:
  deb   debian, gcc release
  tw    tumbleweed, gcc release
  muon  debian, muon
  val   debian, clang release under valgrind (-x); valgrind refuses
        docker's huge fd limit, hence the ulimit
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import ci, remote, sync
from .config import ConfigError


@dataclass(frozen=True)
class Job:
    name: str
    image: str
    command: str


JOBS = (
    Job('deb', 'debian', 'scripts/build.sh -b release -c gcc'),
    Job('tw', 'tumbleweed', 'scripts/build.sh -b release -c gcc'),
    Job('muon', 'debian', 'scripts/build.sh -m muon'),
    Job('val', 'debian',
        'ulimit -n 4096 && scripts/build.sh -b release -c clang -x'),
)

MESON_RE = re.compile(r'^(Ok|Expected Fail|Fail|Unexpected Pass|Skipped|'
                      r'Timeout):\s+(\d+)', re.M)
MUON_RE = re.compile(r'^finished (\d+) tests, (.*)$', re.M)


def select(only=None):
    """The jobs named in @only (all if empty), in table order."""
    if not only:
        return list(JOBS)
    names = {j.name for j in JOBS}
    unknown = [n for n in only if n not in names]
    if unknown:
        raise ConfigError(f"unknown job '{unknown[0]}' (jobs: "
                          f"{', '.join(j.name for j in JOBS)})")
    return [j for j in JOBS if j.name in only]


def tree_name(name, job):
    return sync.check_name(f'{name}-{job.name}')


def default_log_dir(name, env=os.environ):
    cache = env.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(cache) / 'nvme-lab' / 'parity' / sync.check_name(name)


def summarize(log):
    """The test summary of a build.sh log, as one line, or None."""
    counts = MESON_RE.findall(log)
    if counts:
        # The last summary is the one of the whole run.
        last = {}
        for key, n in counts:
            last[key] = n
        return ', '.join(f'{n} {key.lower()}' for key, n in last.items()
                         if key in ('Ok', 'Fail') or n != '0')
    m = MUON_RE.findall(log)
    if m:
        total, rest = m[-1]
        return f'{total} tests, {rest}'
    return None


def job_argv(cfg, host, name, job):
    return ci.ci_argv(cfg, host, job.image,
                      sync.scratch_dir(tree_name(name, job)), job.command)


def run(cfg, host, name, jobs, log_dir, tree='.', ref='HEAD',
        worktree=False, pull=False, log=print):
    """Sync, run @jobs in parallel, report. Returns 0 if all passed."""
    for job in jobs:
        sync.sync(cfg, host, tree=tree, name=tree_name(name, job), ref=ref,
                  worktree=worktree, log=log)

    if pull:
        for image in dict.fromkeys(j.image for j in jobs):
            log(f'pulling {ci.image_ref(image)}')
            remote.run(cfg, host, ['docker', 'pull', '-q',
                                   ci.image_ref(image)],
                       check=True, stdout=subprocess.DEVNULL)

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for job in jobs:
        path = log_dir / f'{job.name}.log'
        with open(path, 'w', encoding='utf-8') as f:
            procs.append((job, path, subprocess.Popen(
                job_argv(cfg, host, name, job), stdin=subprocess.DEVNULL,
                stdout=f, stderr=subprocess.STDOUT)))
    log(f'running {", ".join(j.name for j in jobs)}; logs in {log_dir}')

    failed = False
    for job, path, proc in procs:
        rc = proc.wait()
        text = path.read_text(encoding='utf-8', errors='replace')
        verdict = 'PASS' if rc == 0 else f'FAIL (exit {rc})'
        failed |= rc != 0
        log(f'{job.name:<5} {verdict:<14} {summarize(text) or "no summary"}'
            f'  {path}')

    return 1 if failed else 0
