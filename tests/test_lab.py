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

from nvmelab import ci, cli, config, inventory, remote, sync  # noqa: E402
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
