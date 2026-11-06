"""'lab boot': boot a QEMU guest from an NVMe/TCP root, with dracut.

'lab boot setup' creates the lab-boot container on the CI host
(Tumbleweed: dracut, a kernel, QEMU, nvme-cli's build tools; the host's
/dev/kvm), a subsystem on the target host for the guest's host NQN only,
and a small root file system on its namespace. 'lab boot run' builds an
initrd and boots it: the case passes when the guest's /init on the
NVMe/TCP root runs. A run can install an nvme-cli tree that 'lab sync'
put on the CI host, and overlay dracut's nvmf module with a local
directory, and give the guest an NBFT that points at the root
subsystem ('--nbft'; nbft.py writes it, QEMU passes it on as an ACPI
table). 'lab boot nbft' writes such a table to a local file. Setup also
makes a second subsystem for the guest, with no root on it, for the
cases with more than one subsystem. The root's /init reports the NVMe
controllers and the nvme-cli registry it finds after switch-root.
'lab boot teardown' removes it all.

The scripts that do the work are in boot/ of this repo.
"""

import re
import shlex
import subprocess
from pathlib import Path

from . import nbft, remote, scenario, sync
from .config import SCRATCH, ConfigError

CONTAINER = 'lab-boot'
BOOT_DIR = f'{SCRATCH}/lab-boot'
# /root/scratch is /scratch in the container.
CONTAINER_DIR = '/scratch/lab-boot'
SCRIPTS = Path(__file__).resolve().parent.parent / 'boot'

ROOT_NQN = 'nqn.2026-11.io.lab:lab-boot-root'
# A second subsystem for the guest, no root: for the cases with more than
# one subsystem ('--nbft-ssns extra', rd.nvmf.discover= of its port)
EXTRA_NQN = 'nqn.2026-11.io.lab:lab-boot-extra'
HOST_ID = '6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
HOST_NQN = f'nqn.2014-08.org.nvmexpress:uuid:{HOST_ID}'
ROOT_UUID = 'b0936c83-d08a-4c81-bb63-efc6e619581f'
SOURCES = ('cmdline', 'static', 'legacy', 'none')

# The guest's NIC and QEMU's user networking (boot.sh): the HFI of an NBFT
GUEST_MAC = '52:54:00:12:34:56'
GUEST_PCI_DEV = 3
GUEST_IP, GUEST_PREFIX, GUEST_GATEWAY = '10.0.2.15', 24, '10.0.2.2'


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


def parse_env(text):
    """The target.env that setup wrote: (TRADDR, TRSVCID) of the root
    subsystem and the TRSVCID of the extra one (None before it had
    one)."""
    values = dict(line.split('=', 1) for line in text.split() if '=' in line)
    try:
        target = values['TRADDR'], values['TRSVCID']
    except KeyError:
        raise ConfigError("no target: run 'lab boot setup' first") from None
    return target, values.get('EXTRA_TRSVCID')


def resolve_ssns_spec(spec, extra):
    """'extra[,FLAG...]' of --nbft-ssns: the extra subsystem at its port
    EXTRA."""
    name, sep, flags = spec.partition(',')
    if name != 'extra':
        return spec
    if not extra:
        raise ConfigError("no extra subsystem: run 'lab boot setup' again")
    return f'{extra}/{EXTRA_NQN}{sep}{flags}'


def nbft_table(target, root=True, ssns=(), discovery=(), extra=None):
    """The NBFT of a boot case: the guest's NIC as the HFI, the root
    subsystem (unless not ROOT) and the SSNS specs of 'lab boot run
    --nbft-ssns' as the SSNS descriptors, discovery controllers given as
    [TRADDR:]TRSVCID. EXTRA: the port of the extra subsystem."""
    traddr, trsvcid = target
    ssns = [resolve_ssns_spec(s, extra) for s in ssns]
    hfi = nbft.Hfi(mac=GUEST_MAC, ipaddr=GUEST_IP, prefix=GUEST_PREFIX,
                   gateway=GUEST_GATEWAY, pci_sbdf=GUEST_PCI_DEV << 3)
    table = nbft.Table(host_nqn=HOST_NQN, host_id=HOST_ID, hfis=[hfi])
    if root:
        table.ssns.append(nbft.Ssns(traddr, trsvcid, ROOT_NQN))
    try:
        table.ssns += [nbft.parse_ssns_spec(s, traddr) for s in ssns]
        for d in discovery:
            addr, _, port = d.rpartition(':')
            if not port.isdigit():
                raise nbft.NbftError(f"bad --nbft-discovery '{d}': "
                                     '[TRADDR:]TRSVCID')
            table.discovery.append(
                nbft.Discovery(f'nvme+tcp://{addr or traddr}:{port}/'))
        if not table.ssns and not table.discovery:
            raise nbft.NbftError('an NBFT without SSNS and discovery '
                                 'descriptors points at nothing')
        return nbft.build(table)
    except nbft.NbftError as e:
        raise ConfigError(str(e)) from None


def run_argv(name, target, tree=None, package=False, module=None,
             source='cmdline', hostonly=False, append=None, timeout=None,
             nbft_file=None):
    """The docker exec of one boot case; MODULE is the overlay's
    directory and NBFT_FILE the table in the container."""
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
    if nbft_file:
        args += ['--nbft', nbft_file]
    return exec_argv('boot.sh', *args)


def setup(cfg, host, target_host, recreate=False):
    upload(cfg, host, SCRIPTS, f'{BOOT_DIR}/scripts')
    container = ['sh', f'{BOOT_DIR}/scripts/container.sh', HOST_NQN, HOST_ID]
    if recreate:
        container.append('--recreate')
    remote.run(cfg, host, container, check=True)

    # Fresh subsystems, for the guest's host NQN only.
    def add_subsys(nqn, size):
        remote.run(cfg, target_host, ['nvmet-target', 'del-subsys', nqn],
                   capture_output=True)
        out = remote.run(cfg, target_host,
                         ['nvmet-target', 'add-subsys', nqn, '--size',
                          size, '--host', HOST_NQN, '--no-auth'],
                         check=True, capture_output=True, encoding='utf-8')
        return parse_trsvcid(out.stdout)

    trsvcid = add_subsys(ROOT_NQN, '512M')
    extra = add_subsys(EXTRA_NQN, '64M')
    # The guest's user networking goes out through the CI host: use the
    # target's address as the CI host resolves it.
    addr = remote.run(cfg, host, ['getent', 'ahostsv4', target_host.address],
                      check=True, capture_output=True, encoding='utf-8')
    traddr = addr.stdout.split()[0]
    remote.run(cfg, host, f'printf "TRADDR=%s\\nTRSVCID=%s\\n'
               f'EXTRA_TRSVCID=%s\\n" {traddr} {trsvcid} {extra} '
               f'> {BOOT_DIR}/target.env', check=True)
    remote.run(cfg, host, exec_argv('mkroot.sh', traddr, trsvcid, ROOT_NQN,
                                    ROOT_UUID), check=True)
    print(f'lab-boot: root on {ROOT_NQN} at {traddr}:{trsvcid}')
    print(f'lab-boot: extra subsystem {EXTRA_NQN} at {traddr}:{extra}')


def read_env(cfg, host):
    """parse_env() of the target.env on HOST."""
    out = remote.run(cfg, host, ['cat', f'{BOOT_DIR}/target.env'],
                     capture_output=True, encoding='utf-8')
    return parse_env(out.stdout)


def nbft_path(name):
    """The NBFT of case NAME: on the CI host, in the container."""
    name = sync.check_name(name)
    return f'{BOOT_DIR}/nbft/{name}', f'{CONTAINER_DIR}/nbft/{name}'


def run(cfg, host, name, module=None, nbft_options=None, **kwargs):
    """Boot case NAME. NBFT_OPTIONS: the dict of nbft_table()'s
    options, or None for no NBFT."""
    target, extra = read_env(cfg, host)
    overlay = table = None
    # The scripts of this checkout, not the ones of the last setup
    upload(cfg, host, SCRIPTS, f'{BOOT_DIR}/scripts')
    if module:
        dest = f'{BOOT_DIR}/modules/{sync.check_name(name)}'
        upload(cfg, host, module, dest)
        overlay = f'{CONTAINER_DIR}/modules/{name}'
    if nbft_options is not None:
        data = nbft_table(target, extra=extra, **nbft_options)
        on_host, table = nbft_path(name)
        remote.run(cfg, host, f'mkdir -p {BOOT_DIR}/nbft && '
                   f'cat > {shlex.quote(on_host)}', input=data, check=True)
    argv = run_argv(name, target, module=overlay, nbft_file=table,
                    **kwargs)
    return remote.run(cfg, host, argv).returncode


def read_state(cfg, host, name):
    """The state.txt of case NAME: what the root's /init saw."""
    out = remote.run(cfg, host, ['cat', '--', f'{BOOT_DIR}/out/'
                                 f'{sync.check_name(name)}/state.txt'],
                     capture_output=True, encoding='utf-8')
    return out.stdout


def test(cfg, host, scenarios, prefix='t', module=None, tree=None,
         package=False):
    """Run SCENARIOS as the cases PREFIX-ID; print a verdict for each.
    Returns the number of scenarios that failed."""
    target, extra = read_env(cfg, host)
    roles = {ROOT_NQN: 'root', EXTRA_NQN: 'extra'}
    failed = []
    for s in scenarios:
        name = sync.check_name(f'{prefix}-{s.id}')
        options = scenario.run_options(s, target, extra)
        print(f'--- {s.id}: {s.name}', flush=True)
        rc = run(cfg, host, name, module=module, tree=tree, package=package,
                 **options)
        if rc not in (0, 1):
            errors = [f'could not be set up (exit {rc}), see '
                      f'{BOOT_DIR}/out/{name}/log']
        else:
            state = scenario.parse_state(read_state(cfg, host, name))
            errors = scenario.verdict(s, rc == 0, state, roles)
        print(f"{'FAIL' if errors else 'PASS'} {s.id}", flush=True)
        for e in errors:
            print(f'    {e}', flush=True)
        if errors:
            failed.append(s.id)
    print(f'{len(scenarios) - len(failed)}/{len(scenarios)} scenarios '
          f"passed{': failed ' + ' '.join(failed) if failed else ''}")
    return len(failed)


def teardown(cfg, host, target_host):
    remote.run(cfg, host, ['docker', 'rm', '-f', CONTAINER],
               capture_output=True)
    for nqn in (ROOT_NQN, EXTRA_NQN):
        remote.run(cfg, target_host, ['nvmet-target', 'del-subsys', nqn],
                   capture_output=True)
    remote.run(cfg, host, ['rm', '-rf', '--', BOOT_DIR], check=True)
