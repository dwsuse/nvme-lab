"""'lab boot': boot a QEMU guest from an NVMe/TCP root, with dracut.

'lab boot setup' creates the lab-boot container on the CI host
(Tumbleweed: dracut, a kernel, QEMU, nvme-cli's build tools; the host's
/dev/kvm), a subsystem on the target host for the guest's host NQN only,
and a small root file system on its namespace. 'lab boot run' builds an
initrd and boots it: the case passes when the guest's /init on the
NVMe/TCP root runs. A run can install an nvme-cli tree that 'lab sync'
put on the CI host, and overlay dracut's nvmf module with a local
directory. 'lab boot teardown' removes it all.

The scripts that do the work are in boot/ of this repo.
"""

import re
import shlex
import subprocess
from pathlib import Path

from . import remote, sync
from .config import SCRATCH, ConfigError

CONTAINER = 'lab-boot'
BOOT_DIR = f'{SCRATCH}/lab-boot'
# /root/scratch is /scratch in the container.
CONTAINER_DIR = '/scratch/lab-boot'
SCRIPTS = Path(__file__).resolve().parent.parent / 'boot'

ROOT_NQN = 'nqn.2026-11.io.lab:lab-boot-root'
HOST_ID = '6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
HOST_NQN = f'nqn.2014-08.org.nvmexpress:uuid:{HOST_ID}'
ROOT_UUID = 'b0936c83-d08a-4c81-bb63-efc6e619581f'
SOURCES = ('cmdline', 'static', 'legacy', 'none')


def upload_argv(cfg, host, dest):
    """ssh argv that unpacks a tar from stdin into DEST on HOST."""
    return remote.ssh_argv(cfg, host, f'rm -rf {shlex.quote(dest)} && '
                           f'mkdir -p {shlex.quote(dest)} && '
                           f'tar -C {shlex.quote(dest)} -xf -')


def upload(cfg, host, src, dest):
    """Copy the directory SRC to DEST on HOST, with tar (scp flattens)."""
    if not Path(src).is_dir():
        raise ConfigError(f'not a directory: {src}')
    tar = subprocess.Popen(['tar', '-C', str(src), '-cf', '-', '.'],
                           stdout=subprocess.PIPE)
    subprocess.run(upload_argv(cfg, host, dest), stdin=tar.stdout,
                   check=True)
    tar.stdout.close()
    if tar.wait():
        raise ConfigError(f'tar of {src} failed')


def exec_argv(script, *args):
    """The docker exec that runs SCRIPT of boot/ in the container."""
    return ['docker', 'exec', CONTAINER, 'sh',
            f'{CONTAINER_DIR}/scripts/{script}', *args]


def parse_trsvcid(output):
    """The TCP port of 'nvmet-target add-subsys' ("port=1 trsvcid=4421")."""
    m = re.search(r'\btrsvcid=(\d+)', output)
    if not m:
        raise ConfigError(f'no trsvcid in the add-subsys output:\n{output}')
    return m.group(1)


def parse_target(text):
    """TRADDR, TRSVCID of the target.env that setup wrote."""
    values = dict(line.split('=', 1) for line in text.split() if '=' in line)
    try:
        return values['TRADDR'], values['TRSVCID']
    except KeyError:
        raise ConfigError("no target: run 'lab boot setup' first") from None


def run_argv(name, target, tree=None, package=False, module=None,
             source='cmdline', hostonly=False, append=None, timeout=None):
    """The docker exec of one boot case; MODULE is the overlay's
    directory in the container."""
    if source not in SOURCES:
        raise ConfigError(f"bad --source '{source}': {', '.join(SOURCES)}")
    if source in ('static', 'legacy') and not hostonly:
        raise ConfigError(f'--source {source} needs --hostonly: only a '
                          "hostonly initrd carries the host's /etc/nvme")
    if tree and package:
        raise ConfigError('--nvme and --nvme-package exclude each other')
    args = ['--name', sync.check_name(name), '--target', ':'.join(target),
            '--root-uuid', ROOT_UUID, '--source', source]
    if tree:
        args += ['--nvme-tree', f'/scratch/{sync.check_name(tree)}']
    if package:
        args.append('--nvme-package')
    if module:
        args += ['--dracut-module', module]
    if hostonly:
        args.append('--hostonly')
    if append:
        args += ['--append', append]
    if timeout:
        args += ['--timeout', str(timeout)]
    return exec_argv('boot.sh', *args)


def setup(cfg, host, target_host, recreate=False):
    upload(cfg, host, SCRIPTS, f'{BOOT_DIR}/scripts')
    container = ['sh', f'{BOOT_DIR}/scripts/container.sh', HOST_NQN, HOST_ID]
    if recreate:
        container.append('--recreate')
    remote.run(cfg, host, container, check=True)

    # A fresh subsystem, for the guest's host NQN only.
    remote.run(cfg, target_host, ['nvmet-target', 'del-subsys', ROOT_NQN],
               capture_output=True)
    out = remote.run(cfg, target_host,
                     ['nvmet-target', 'add-subsys', ROOT_NQN, '--size',
                      '512M', '--host', HOST_NQN, '--no-auth'],
                     check=True, capture_output=True, encoding='utf-8')
    trsvcid = parse_trsvcid(out.stdout)
    # The guest's user networking goes out through the CI host: use the
    # target's address as the CI host resolves it.
    addr = remote.run(cfg, host, ['getent', 'ahostsv4', target_host.address],
                      check=True, capture_output=True, encoding='utf-8')
    traddr = addr.stdout.split()[0]
    remote.run(cfg, host, f'printf "TRADDR=%s\\nTRSVCID=%s\\n" '
               f'{traddr} {trsvcid} > {BOOT_DIR}/target.env', check=True)
    remote.run(cfg, host, exec_argv('mkroot.sh', traddr, trsvcid, ROOT_NQN,
                                    ROOT_UUID), check=True)
    print(f'lab-boot: root on {ROOT_NQN} at {traddr}:{trsvcid}')


def read_target(cfg, host):
    out = remote.run(cfg, host, ['cat', f'{BOOT_DIR}/target.env'],
                     capture_output=True, encoding='utf-8')
    return parse_target(out.stdout)


def run(cfg, host, name, module=None, **kwargs):
    target = read_target(cfg, host)
    overlay = None
    if module:
        dest = f'{BOOT_DIR}/modules/{sync.check_name(name)}'
        upload(cfg, host, module, dest)
        overlay = f'{CONTAINER_DIR}/modules/{name}'
    argv = run_argv(name, target, module=overlay, **kwargs)
    return remote.run(cfg, host, argv).returncode


def teardown(cfg, host, target_host):
    remote.run(cfg, host, ['docker', 'rm', '-f', CONTAINER],
               capture_output=True)
    remote.run(cfg, target_host, ['nvmet-target', 'del-subsys', ROOT_NQN],
               capture_output=True)
    remote.run(cfg, host, ['rm', '-rf', '--', BOOT_DIR], check=True)
