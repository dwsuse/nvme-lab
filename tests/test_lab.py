"""Unit tests of the lab tool. Run: make check (or python3 -m unittest)."""

import contextlib
import io
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from nvmelab import boot, ci, cli, config, inventory, parity, remote, sync  # noqa: E402,E501
from nvmelab.config import ConfigError  # noqa: E402

LAB = """
[hosts.a]
description = "host a"
ci = true
[hosts.b]
address = "10.0.0.2"
user = "test"
"""


def write(dir, name, text):
    p = Path(dir) / name
    p.write_text(text, encoding='utf-8')
    return p


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = write(self.tmp.name, 'lab.toml', LAB)
        self.none = Path(self.tmp.name) / 'missing.toml'

    def tearDown(self):
        self.tmp.cleanup()

    def test_hosts(self):
        cfg = config.load(self.lab, self.none, env={})
        self.assertEqual(cfg.host('a').login, 'root@a.lan')
        self.assertEqual(cfg.host('b').login, 'test@10.0.0.2')
        self.assertEqual(cfg.host('10.0.0.2').name, 'b')
        self.assertEqual(cfg.ci_host().name, 'a')
        self.assertIsNone(cfg.ssh_key)

    def test_unknown_host(self):
        cfg = config.load(self.lab, self.none, env={})
        with self.assertRaisesRegex(ConfigError, "unknown host 'c'"):
            cfg.host('c')

    def test_unknown_key(self):
        lab = write(self.tmp.name, 'bad.toml', '[hosts.a]\naddres = "x"\n')
        with self.assertRaisesRegex(ConfigError, 'addres'):
            config.load(lab, self.none, env={})

    def test_no_hosts(self):
        lab = write(self.tmp.name, 'empty.toml', '')
        with self.assertRaisesRegex(ConfigError, 'no \\[hosts'):
            config.load(lab, self.none, env={})

    def test_ssh_key(self):
        user = write(self.tmp.name, 'user.toml', 'ssh_key = "/k/user"\n')
        cfg = config.load(self.lab, user, env={})
        self.assertEqual(cfg.ssh_key, '/k/user')
        cfg = config.load(self.lab, user, env={'LAB_SSH_KEY': '/k/env'})
        self.assertEqual(cfg.ssh_key, '/k/env')
        cfg = config.load(self.lab, user, env={'LAB_SSH_KEY': '~/k'})
        self.assertEqual(cfg.ssh_key, str(Path('~/k').expanduser()))

    def test_lab_config_env(self):
        cfg = config.load(None, self.none, env={'LAB_CONFIG': str(self.lab)})
        self.assertIn('b', cfg.hosts)

    def test_repo_lab_toml(self):
        cfg = config.load(config.LAB_FILE, self.none, env={})
        self.assertTrue(cfg.hosts)
        cfg.ci_host()


class RemoteTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config(ssh_key='/k')
        self.host = config.Host('a', 'a.lan')

    def test_list_command_is_quoted(self):
        argv = remote.ssh_argv(self.cfg, self.host, ['echo', 'a b', "it's"])
        self.assertEqual(argv[-2:], ['--', "echo 'a b' 'it'\"'\"'s'"])
        self.assertEqual(shlex.split(argv[-1]), ['echo', 'a b', "it's"])

    def test_string_command_is_shell_code(self):
        argv = remote.ssh_argv(self.cfg, self.host, 'ls | head')
        self.assertEqual(argv[-1], 'ls | head')

    def test_options(self):
        argv = remote.ssh_argv(self.cfg, self.host, 'true')
        self.assertIn('BatchMode=yes', argv)
        self.assertEqual(argv[argv.index('-i') + 1], '/k')
        self.assertIn('root@a.lan', argv)

    def test_login_is_interactive(self):
        argv = remote.ssh_argv(config.Config(), self.host)
        self.assertNotIn('BatchMode=yes', argv)
        self.assertNotIn('-i', argv)
        self.assertEqual(argv[-1], 'root@a.lan')


class InventoryTest(unittest.TestCase):
    OUT = 'host\tx\ndisk\tvda 1 GiB\nzzz\tlast\ndisk\tvdb 2 GiB\nbad line\n'

    def test_parse(self):
        facts = inventory.parse(self.OUT)
        self.assertEqual(facts['disk'], ['vda 1 GiB', 'vdb 2 GiB'])
        self.assertNotIn('bad line', facts)

    def test_order(self):
        facts = inventory.parse('zzz\t1\nrepo\tr\nhost\th\n')
        self.assertEqual([k for k, _ in inventory.ordered(facts)],
                         ['host', 'repo', 'zzz'])

    def test_markdown(self):
        md = inventory.render_markdown({'a': {'tools': ['x|y'],
                                              'disk': ['1', '2']}},
                                       '2026-11-05')
        self.assertIn('## a', md)
        self.assertIn('| tools | x\\|y |', md)
        self.assertIn('| disk | 1<br>2 |', md)

    def test_text(self):
        text = inventory.render_text({'a': {'error': ['timeout']}})
        self.assertIn('=== a', text)
        self.assertIn('timeout', text)

    def test_probe_out_one_line(self):
        # A value of several lines (one address per line from ip or
        # ifconfig on a host with several) is one line of the inventory.
        src = inventory.PROBE.read_text(encoding='utf-8')
        start = src.index('out()\n')
        func = src[start:src.index('\n}\n', start) + 3]
        script = func + 'out address "$(printf \'%s\\n\' 10.0.0.1 10.0.0.2)"\n'
        p = subprocess.run(['sh', '-c', script], capture_output=True,
                           encoding='utf-8', timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, 'address\t10.0.0.1 10.0.0.2\n')

    def test_probe_runs_here(self):
        # The probe is read only; it must run on any POSIX system.
        p = subprocess.run(['sh', str(inventory.PROBE)], capture_output=True,
                           encoding='utf-8', timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        facts = inventory.parse(p.stdout)
        self.assertIn('host', facts)
        self.assertIn('os', facts)
        for line in p.stdout.splitlines():
            self.assertEqual(line.count('\t'), 1, line)
            self.assertEqual(line, line.strip(), line)


class CiTest(unittest.TestCase):
    def test_image_ref(self):
        self.assertEqual(ci.image_ref('debian'),
                         'ghcr.io/linux-nvme/debian:latest')
        self.assertEqual(ci.image_ref('fedora:41'),
                         'ghcr.io/linux-nvme/fedora:41')
        self.assertEqual(ci.image_ref('docker.io/x/y:1'), 'docker.io/x/y:1')

    def test_docker_argv(self):
        argv = ci.docker_argv('debian', '/root/scratch/t', 'make', pull=True)
        self.assertEqual(argv[:5], ['docker', 'run', '--rm', '--pull',
                                    'always'])
        self.assertIn('/root/scratch/t:/src', argv)
        self.assertTrue(argv[-1].endswith('&& make'))


MESON_LOG = """\
 1/2 unit - x - a OK 0.1s
Ok:                281
Expected Fail:     10
Fail:              0
Unexpected Pass:   0
Skipped:           0
Timeout:           0
"""

MESON_TWO_LOGS = MESON_LOG + """\
Ok:                279
Expected Fail:     10
Fail:              1
Skipped:           2
"""

MUON_LOG = """\
running tests for project 'nvme-cli'
finished 138 tests, 2 expected fail, 0 fail, 2 skipped
"""


class ParityTest(unittest.TestCase):
    def test_jobs(self):
        self.assertEqual([j.name for j in parity.JOBS],
                         ['deb', 'tw', 'muon', 'val'])
        val = parity.select(['val'])[0]
        self.assertIn('ulimit -n 4096 && ', val.command)
        self.assertIn(' -x', val.command)

    def test_select(self):
        self.assertEqual(parity.select([]), list(parity.JOBS))
        self.assertEqual([j.name for j in parity.select(['val', 'deb'])],
                         ['deb', 'val'])
        with self.assertRaisesRegex(ConfigError, "unknown job 'x'"):
            parity.select(['x'])

    def test_tree_name(self):
        job = parity.select(['tw'])[0]
        self.assertEqual(parity.tree_name('pr-1', job), 'pr-1-tw')
        with self.assertRaises(ConfigError):
            parity.tree_name('../x', job)

    def test_default_log_dir(self):
        self.assertEqual(parity.default_log_dir(
            'pr-1', env={'XDG_CACHE_HOME': '/c'}),
            Path('/c/nvme-lab/parity/pr-1'))

    def test_summarize_meson(self):
        self.assertEqual(parity.summarize(MESON_LOG),
                         '281 ok, 10 expected fail, 0 fail')

    def test_summarize_last_meson_summary(self):
        self.assertEqual(parity.summarize(MESON_TWO_LOGS),
                         '279 ok, 10 expected fail, 1 fail, 2 skipped')

    def test_summarize_muon(self):
        self.assertEqual(parity.summarize(MUON_LOG),
                         '138 tests, 2 expected fail, 0 fail, 2 skipped')

    def test_summarize_none(self):
        self.assertIsNone(parity.summarize('error: no compiler\n'))


class SyncTest(unittest.TestCase):
    def test_names(self):
        for good in ('wt-4351', 'a.b_c', 'X1'):
            self.assertEqual(sync.scratch_dir(good), '/root/scratch/' + good)
        for bad in ('', '.', '..', '../etc', 'a/b', '-rf', '.git', 'a b'):
            with self.assertRaises(ConfigError, msg=bad):
                sync.scratch_dir(bad)

    def test_changes(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                subprocess.run(['git', '-C', d, '-c', 'user.name=t',
                                '-c', 'user.email=t@t', *args], check=True,
                               capture_output=True)
            git('init', '-q')
            for f in ('keep', 'mod', 'gone', 'spa ce'):
                write(d, f, f)
            write(d, '.gitignore', 'ignored\n')
            git('add', '.')
            git('commit', '-qm', 'init')
            write(d, 'mod', 'changed')
            write(d, 'spa ce', 'changed')
            write(d, 'new', 'new')
            write(d, 'staged', 'staged')
            write(d, 'ignored', 'x')
            git('add', 'staged')
            (Path(d) / 'gone').unlink()

            copy, delete = sync.changes(d)
            self.assertEqual(sorted(copy.decode().split('\0')[:-1]),
                             ['mod', 'new', 'spa ce', 'staged'])
            self.assertEqual(delete, b'gone\0')


class BootTest(unittest.TestCase):
    TARGET = ('192.0.2.30', '4421')

    def test_parse_trsvcid(self):
        self.assertEqual(boot.parse_trsvcid('port=1 trsvcid=4421\n'), '4421')
        with self.assertRaisesRegex(ConfigError, 'no trsvcid'):
            boot.parse_trsvcid('error: no such host\n')

    def test_parse_target(self):
        self.assertEqual(boot.parse_target('TRADDR=192.0.2.30\n'
                                           'TRSVCID=4421\n'), self.TARGET)
        with self.assertRaisesRegex(ConfigError, 'lab boot setup'):
            boot.parse_target('')

    def test_run_argv(self):
        argv = boot.run_argv('c1', self.TARGET, tree='wt-1',
                             module='/scratch/lab-boot/modules/c1',
                             source='static', hostonly=True,
                             append='rd.debug', timeout=60)
        self.assertEqual(argv[:4], ['docker', 'exec', 'lab-boot', 'sh'])
        self.assertEqual(argv[4], '/scratch/lab-boot/scripts/boot.sh')
        opts = argv[5:]
        self.assertEqual(opts[opts.index('--target') + 1],
                         '192.0.2.30:4421')
        self.assertEqual(opts[opts.index('--nvme-tree') + 1], '/scratch/wt-1')
        self.assertEqual(opts[opts.index('--source') + 1], 'static')
        self.assertEqual(opts[opts.index('--root-uuid') + 1], boot.ROOT_UUID)
        self.assertIn('--hostonly', opts)
        self.assertEqual(opts[opts.index('--append') + 1], 'rd.debug')
        self.assertEqual(opts[opts.index('--timeout') + 1], '60')

    def test_run_argv_defaults(self):
        opts = boot.run_argv('c1', self.TARGET)[5:]
        self.assertEqual(opts[opts.index('--source') + 1], 'cmdline')
        for flag in ('--nvme-tree', '--nvme-package', '--dracut-module',
                     '--hostonly', '--append', '--timeout'):
            self.assertNotIn(flag, opts)

    def test_run_argv_checks(self):
        for kwargs, msg in ((dict(source='static'), 'needs --hostonly'),
                            (dict(source='legacy'), 'needs --hostonly'),
                            (dict(source='bogus'), "bad --source"),
                            (dict(tree='t', package=True), 'exclude')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ConfigError, msg):
                    boot.run_argv('c1', self.TARGET, **kwargs)
        with self.assertRaisesRegex(ConfigError, 'bad scratch name'):
            boot.run_argv('../c1', self.TARGET)
        with self.assertRaisesRegex(ConfigError, 'bad scratch name'):
            boot.run_argv('c1', self.TARGET, tree='../t')

    def test_upload_argv(self):
        cfg = config.Config(hosts={}, ssh_key=None)
        host = config.Host('a', 'a.lan')
        argv = boot.upload_argv(cfg, host, '/root/scratch/lab-boot/x y')
        self.assertEqual(argv[-1], "rm -rf '/root/scratch/lab-boot/x y' && "
                         "mkdir -p '/root/scratch/lab-boot/x y' && "
                         "tar -C '/root/scratch/lab-boot/x y' -xf -")

    def test_scripts_exist(self):
        for script in ('container.sh', 'mkroot.sh', 'boot.sh'):
            self.assertTrue((boot.SCRIPTS / script).is_file(), script)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = str(write(self.tmp.name, 'lab.toml', LAB))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(['--config', self.lab, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_hosts(self):
        rc, out, _ = self.run_cli('hosts')
        self.assertEqual(rc, 0)
        self.assertIn('root@a.lan', out)
        self.assertIn('[ci]', out)

    def test_ci_dry_run_keeps_options_after_dashdash(self):
        rc, out, _ = self.run_cli('ci', 'debian', 't', '--dry-run', '--',
                                  'make', '--help')
        self.assertEqual(rc, 0)
        argv = shlex.split(out)
        self.assertEqual(argv[argv.index('--') - 1], 'root@a.lan')
        docker = shlex.split(argv[-1])
        self.assertEqual(docker[-1], 'git config --global --add '
                         'safe.directory /src && make --help')

    def test_boot_run_dry_run(self):
        rc, out, _ = self.run_cli('boot', 'run', 'c1', '--nvme', 'wt',
                                  '--dracut-module', '/tmp/m', '--source',
                                  'legacy', '--hostonly', '--dry-run')
        self.assertEqual(rc, 0)
        argv = shlex.split(out)
        self.assertEqual(argv[argv.index('--') - 1], 'root@a.lan')
        inner = shlex.split(argv[-1])
        self.assertEqual(inner[inner.index('--dracut-module') + 1],
                         '/scratch/lab-boot/modules/c1')
        self.assertEqual(inner[inner.index('--nvme-tree') + 1], '/scratch/wt')

    def test_boot_run_static_needs_hostonly(self):
        rc, _, err = self.run_cli('boot', 'run', 'c1', '--source', 'static',
                                  '--dry-run')
        self.assertEqual(rc, 2)
        self.assertIn('needs --hostonly', err)

    def test_parity_dry_run(self):
        rc, out, _ = self.run_cli('parity', 'pr-1', '--dry-run')
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual([ln.split(':')[0] for ln in lines],
                         ['deb', 'tw', 'muon', 'val'])
        for line, tree in zip(lines, ('deb', 'tw', 'muon', 'val')):
            argv = shlex.split(line.split(': ', 1)[1])
            self.assertEqual(argv[argv.index('--') - 1], 'root@a.lan')
            docker = shlex.split(argv[-1])
            self.assertIn(f'/root/scratch/pr-1-{tree}:/src', docker)
        self.assertIn('ghcr.io/linux-nvme/tumbleweed:latest',
                      shlex.split(shlex.split(
                          lines[1].split(': ', 1)[1])[-1]))

    def test_parity_only(self):
        rc, out, _ = self.run_cli('parity', 'pr-1', '--only', 'muon',
                                  '--dry-run')
        self.assertEqual(rc, 0)
        self.assertEqual([ln.split(':')[0] for ln in out.splitlines()],
                         ['muon'])

    def test_parity_unknown_job(self):
        rc, _, err = self.run_cli('parity', 'pr-1', '--only', 'nope',
                                  '--dry-run')
        self.assertEqual(rc, 2)
        self.assertIn("unknown job 'nope'", err)

    def test_ci_needs_command(self):
        rc, _, err = self.run_cli('ci', 'debian', 't', '--dry-run')
        self.assertEqual(rc, 2)
        self.assertIn('no command', err)

    def test_bad_scratch_name(self):
        rc, _, err = self.run_cli('ci', 'debian', '../x', '--', 'true')
        self.assertEqual(rc, 2)
        self.assertIn('bad scratch name', err)

    def test_unknown_host(self):
        rc, _, err = self.run_cli('ssh', 'nope', 'true')
        self.assertEqual(rc, 2)
        self.assertIn("unknown host 'nope'", err)


if __name__ == '__main__':
    unittest.main()
