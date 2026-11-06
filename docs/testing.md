# Testing nvme-cli with the lab

How the lab VMs are used for nvme-cli work, by people and by coding
agents alike. Everything is driven from the workstation with `bin/lab`;
nothing needs to be installed on the VMs beyond what `setup/` does.

## The VMs

| Host | What it is for |
|---|---|
| `nvme` | NVMe-oF host: nvme-cli master in `/usr`, blktests, nvmf-failover, tlshd. Docker with nvme-cli's CI images (`lab ci`). |
| `nvmet` | NVMe/TCP target: `nvmet-target`, namespaces on `vdb`/`vdc`. |
| `nvme-bsd` | FreeBSD 15: nvme-cli's FreeBSD port; has a QEMU NVMe disk (`nda0`), so real ioctls work. |

`lab hosts` lists them, `docs/inventory.md` has the details
(`lab inventory --markdown > docs/inventory.md` after changing a VM).

## Workstation setup

```
ln -s ~/nvme-lab/bin/lab ~/bin/lab           # or put bin/ on PATH
mkdir -p ~/.config/nvme-lab
echo 'ssh_key = "~/.ssh/nvme-cli-test"' > ~/.config/nvme-lab/config.toml
lab inventory                                # checks every login
```

The key's public half goes into `/root/.ssh/authorized_keys` on each
VM. `LAB_SSH_KEY` overrides the config file for one shell. Python
3.11 or newer, standard library only.

## Rules

- **Own trees go to `/root/scratch/NAME`** (`lab sync`), one per
  branch or PR, e.g. `/root/scratch/pr-4376`. `/root/work` belongs
  to the setup (`setup/build.sh` fetches there); don't develop in it.
- **Remove scratch trees when done**: `lab scratch HOST` lists them,
  `lab scratch HOST --rm NAME` removes one. Don't remove trees of
  others without asking.
- **The target**: change it only with `nvmet-target add-subsys` /
  `del-subsys` (a scratch subsystem on its own port); the default
  target (`nvmet-target up`) is shared, leave it as it is.
- **No secrets in git.** DH-CHAP and TLS keys are made on the VMs (see
  README, "Secrets").
- **Kernels**: a new kernel only with `setup/kernel.sh`; trixie's
  kernel stays as the fallback in grub.
- **Report what ran**: a result in a PR or issue comes from a finished
  run on a named VM and commit, not from an expectation.

## Typical runs

### A branch on the host

```
cd ~/nvme-cli                    # or a worktree of the branch
lab sync nvme -n pr-4376         # HEAD; -w adds uncommitted changes
lab ssh nvme 'cd /root/scratch/pr-4376 && meson setup .build && \
    meson compile -C .build && meson test -C .build'
```

`lab sync` pushes the commit with git and checks it out detached, so
the tree on the VM is a real git checkout (scripts that call git
work) and a second sync is incremental; the build directory stays.

### CI parity before a push

nvme-cli's CI runs in its own images (`ghcr.io/linux-nvme/*`); `lab ci`
runs a command in one of them on a synced tree:

```
lab ci debian pr-4376 -- scripts/build.sh -b release -c gcc
lab ci tumbleweed pr-4376 -- scripts/build.sh -b release -c gcc
lab ci debian pr-4376 -- scripts/build.sh -m muon
lab ci debian pr-4376 -- scripts/update-docs.sh    # man/html pages
```

All of nvme-cli's CI-parity runs at once, each in its own tree
(`build.sh` owns the `.build-ci` of its tree):

```
lab parity pr-4376                 # in the nvme-cli tree
lab parity pr-4376 -r my-branch    # a branch that isn't checked out
lab parity pr-4376 --only val      # just the valgrind job
```

It syncs `pr-4376-deb`, `-tw`, `-muon` and `-val` and runs, in parallel,
debian and tumbleweed `build.sh -b release -c gcc`, debian `build.sh -m
muon`, and debian `build.sh -b release -c clang -x` under valgrind (with
`ulimit -n 4096`: valgrind refuses docker's huge fd limit). It prints one
PASS/FAIL line per job with the test counts, and keeps the full logs in
`~/.cache/nvme-lab/parity/pr-4376/` and the trees for the next run.

`--pull` gets the newest image first (CI always uses the newest);
`--dry-run` prints the command. `build.sh` removes its build directory
first, so these runs don't disturb a `.build` of your own. Containers
on the default Docker network have no DNS on `nvme`: a command that
needs the network (apt, pip) runs with `lab ssh nvme 'docker run
--network host ...'`.

### FreeBSD

```
lab sync nvme-bsd -n pr-4376
lab ssh nvme-bsd 'cd /root/scratch/pr-4376 && meson setup .build && \
    ninja -C .build && meson test -C .build && ./.build/nvme list'
```

This is what nvme-cli's `build-freebsd.yml` does; `nda0` is a real
(emulated) NVMe controller for ioctl tests.

### NVMe/TCP between `nvme` and `nvmet`

```
lab ssh nvmet nvmet-target add-subsys nqn.pr-4376 --no-auth \
    --host "$(lab ssh nvme cat /etc/nvme/hostnqn)"
lab ssh nvmet nvmet-target status        # shows the port, from 4421
lab ssh nvme nvme connect -t tcp -a nvmet -s 4421 -n nqn.pr-4376
...
lab ssh nvme nvme disconnect -n nqn.pr-4376
lab ssh nvmet nvmet-target del-subsys nqn.pr-4376
```

DH-CHAP and TLS: README, "Secrets". blktests (`/root/work/blktests`)
and nvmf-failover (`nvmf-failover`) are installed on `nvme`.

### Booting from NVMe/TCP (dracut)

`lab boot` boots a QEMU guest whose root file system is on an
NVMe/TCP namespace of `nvmet`, through an initrd built by dracut: the
whole pre-OS path of nvme-cli (dracut's nvmf module, `nvme connect-all`,
the host identity) on a real kernel, with no hardware.

```
lab boot setup [--slots 4]          # once: containers, subsystems, roots
lab sync nvme -n pr-4384            # an nvme-cli tree to test
lab boot test --nvme pr-4384 --dracut-module ~/dracut/modules.d/74nvmf
lab boot run v2-legacy --nvme v216 --source legacy --hostonly
lab boot run v3-static --nvme pr-4384 --source static --hostonly \
    --dracut-module ~/dracut/modules.d/74nvmf
lab boot run v3-cmdline --nvme pr-4384 --hostonly \
    --dracut-module ~/dracut/modules.d/74nvmf
lab boot teardown                   # removes it all
```

`setup` creates the container `lab-boot` on `nvme` (openSUSE
Tumbleweed with dracut, a kernel and QEMU; `nvme`'s `/dev/kvm`), two
subsystems on `nvmet` that let in only the guest's host NQN, and a root
file system on the namespace of the first one (`root`). The root
subsystem is exported on two ports, the paths `root` and `root2` (native
multipath); the second subsystem (`extra`, no root on it, path `extra`)
is for the cases with more than one subsystem. setup prints their
ports. The target is set up with nvme-target-control (blktests,
`contrib/nvme-target-control`), from `target_control` in
`~/.config/nvme-lab/config.toml` or `LAB_TARGET_CONTROL` (default: the
checkout in `~/blktests`); its ports have service IDs from 4430 on. After switch-root, the root's `/init` reports
the NVMe controllers and the nvme-cli registry entries it finds (the
registry is in `/run`, which moves along) and powers the guest off.

Each `run` uploads the scripts of `boot/`, installs the nvme-cli tree
(built in `TREE/.lab-boot` with `--sysconfdir=/etc`, which all slots
share, one at a time), puts dracut's nvmf module in place (the
package's, or `--dracut-module DIR`), builds the initrd and boots it.
A boot that fails powers off (`rd.shell=0 rd.emergency=poweroff`)
instead of waiting in dracut's emergency shell until the timeout. It prints `RESULT=PASS` and the root's report, or
`RESULT=FAIL`, and leaves the initrd, `lsinitrd`, dracut's log, the
console and the report (`state.txt`) in
`/root/scratch/lab-boot/out/NAME`. A tree's install first removes
what the last tree installed (its meson install log), so files of an
earlier tree, e.g. a dracut configuration, don't reach the initrd.

`--source` is where the initrd learns the target from: `cmdline`
(`rd.nvmf.discover=`, the default), `static` (`nvme-fabrics.conf`,
nvme-cli 3), `legacy` (`discovery.conf`, nvme-cli 2) or `none`
(`--append` gives it). `static` and `legacy` need `--hostonly`: only a
hostonly initrd carries the host's `/etc/nvme`. Without it, the host
identity goes on the kernel command line (`rd.nvmf.hostnqn=`), unless
`--no-host-identity`, and nowhere else: dracut runs with
`--no-hostonly-cmdline` too, because Tumbleweed turns
`hostonly_cmdline` on, and dracut would then write the container's
identity into the initrd's `etc/cmdline.d` anyway. dracut's
nvmf module up to at least dracut 112 can't write that (no `/etc/nvme`
in a non-hostonly initrd), and only reads `nvme-fabrics.conf` with the
patches of dracut's `nvmf-nvme-cli-3` branch, hence the module overlay
in the examples. With nvme-cli 3 up to 3.2, the stock module's
`rd.nvmf.discover=` fails too: it writes `--host-traddr=none` to
`discovery.conf`, and the `nvme-fabrics.conf` that nvme-cli converts it
to doesn't parse.

#### Slots

`setup --slots N` makes N boot environments that run side by side:
each slot has its own container (`lab-boot-N`; the first is built, the
others start from a copy of it, the image `nvme-lab/lab-boot`), its own
root and extra subsystems with their own root file system (two guests
must not mount one ext4), its own host identity (the target's access
lists keep the slots apart; slot 1 keeps the identity of the lab before
slots), and its own paths and `target.env`. `lab boot test -j N` boots N
scenarios at a time, each in a free slot, the slowest first, and prints
each scenario's output as one block when it is done. `lab boot run
--slot N` and `lab boot nbft --slot N` pick a slot. A slot's directory
is `/root/scratch/lab-boot/slot-N`; the results stay in `out/NAME` (the
slot in `out/NAME/slot`). The CI host carries 8 slots (a guest takes 2
CPUs and 1 GB; 16 CPUs, 15 GB): the CPU only runs out while the trees
are built.

`--nvme` of `lab boot test` can be repeated: a matrix, every scenario
with every tree, in one run. The trees are built first, once each and
side by side; a scenario tagged for one nvme-cli major version
(`nvme-cli-2`, `nvme-cli-3`) only runs with the trees of that version.
`lab sync` records the version: the tag a tree is at and the latest one
before it (`.git/lab-describe` of the synced tree; the tags themselves
aren't synced). The cases are `PREFIX-TREE-SCENARIO`, the verdicts name
the tree, and the summary has a line per tree.

#### NBFT boot

`--nbft` gives the guest an NBFT, the ACPI table in which boot firmware
hands its NVMe-oF connections to the OS. No firmware writes it: lab
builds the table (`nvmelab/nbft.py`) and QEMU passes it on with
`-acpitable`. So this tests the OS side of an NBFT boot (dracut's NBFT
parsing, `nvme connect-all --nbft`), not firmware booting from NVMe/TCP.

```
lab boot run nbft --nvme pr-4384 --dracut-module ~/dracut/modules.d/74nvmf \
    --nbft
lab boot run nbft-extra --nvme pr-4384 --nbft-ssns extra,nonboot
lab boot nbft /tmp/t/NBFT --nbft-ssns 4499/NQN,unavailable
nvme nbft show --nbft-path /tmp/t -s --hfi -d    # what libnvme reads
```

The table has the guest's NIC as its HFI (MAC 52:54:00:12:34:56, PCI
slot 3, 10.0.2.15/24 via 10.0.2.2) and the root subsystem as its first
SSNS. The SSNS of the subsystems of setup carry their namespace's UUID
as NID, and every SSNS lists its HFI as firmware does (libnvme 1, i.e.
nvme-cli 2, crashes on an SSNS whose HFI list names no HFI). The kernel command line then has no `ip=` and the source is
`none`: the network and the target come from the NBFT. `--source` still
adds another one. More descriptors:

- `--nbft-ssns [TRADDR:]TRSVCID/NQN[,FLAG...]`: another SSNS, on the
  root's target address by default; `extra[,FLAG...]` is the extra
  subsystem of setup. FLAGs (SSNS flags of the Boot Specification):
  `nonboot`, `discovered`, `separate-dc`, `available`, `unavailable`.
  Any other subsystem must exist for the guest's host NQN
  (`nvmet-target add-subsys NQN --host HOSTNQN --no-auth`) unless the
  case is about one that doesn't.
- `--nbft-discovery [TRADDR:]TRSVCID`: a discovery descriptor.
- `--nbft-no-root`: no SSNS for the root, e.g. to find it through a
  discovery descriptor.

The table of a run is in `out/NAME/NBFT`. `lab boot nbft FILE` writes
the same table to a local file (name it `NBFT` for `nvme nbft show
--nbft-path`); `--target-addr` stands in for `lab boot setup`'s target.
The host descriptor has the guest's host NQN and ID, so the target lets
in the connections of `connect-all --nbft`.

#### Faults

`--fault KIND:PATH` (repeatable) breaks a path from before the guest
starts until it is off again, and puts it back afterwards, also when the
run fails:

| Fault | What | Done by |
|---|---|---|
| `port-removed:PATH` | the target port is gone, connects are refused | nvme-target-control `remove-port`, `add-ports` afterwards |
| `ana:STATE:PATH` | the port's ANA group is in STATE (`inaccessible`, `non-optimized`, ...) | nvme-target-control `set-ana`, `optimized` afterwards |
| `tcp-refuse:PATH` | the CI host refuses the guest's connections to the port (TCP reset) | nvmf-failover `tcp-refuse` |
| `tcp-drop:PATH` | the CI host drops what the port sends; connects wait for their SYN retries | nvmf-failover `tcp-port` |

PATH is `root`, `root2` or `extra`. The guest's connections leave QEMU's
user networking on the CI host, so the network faults run there, with
nftables, from the nvmf-failover checkout of `nvmf_failover` in the
config or `LAB_NVMF_FAILOVER` (default `~/nvmf-failover`), synced to
the CI host for the run. setup and teardown remove what an interrupted
run left (the `inet nvmf_failover` table).

#### Scenarios

`lab boot test` runs boot scenarios and checks what the booted root
sees. A scenario (`boot/scenarios/*.toml`) is a boot case (the options
of `lab boot run`, and faults) and what to expect: whether the root's
`/init` runs, which subsystems have controllers after switch-root and
how many, and the registry owner, `ctrl_loss_tmo` and host NQN of their
controllers. A scenario can be an expected failure (`xfail`, with the
reason): XFAIL while it fails, XPASS, a failure, once it passes;
`xfail_on` does the same for given nvme-cli releases, e.g. one with a
bug fixed later (`nbft-hostnqn` on 3.0 to 3.2). The
format is in `nvmelab/scenario.py` (and `lab boot test --help`).

```
lab boot test --list                          # the scenarios and tags
lab boot test --nvme pr-4384 --dracut-module ~/dracut/modules.d/74nvmf
lab boot test --nvme pr-4384 --tag nbft --skip-tag pre-os
lab boot test --nvme v216 cmdline legacy-hostonly nbft-nvme-cli-2
```

Each scenario boots as the case `PREFIX-SCENARIO` (`--prefix`, default
`t`) and gets a PASS, FAIL, XFAIL or XPASS line with what differed, then
a summary; the exit status is 0 unless one failed or passed unexpectedly.
Tags say what a scenario is about or needs: `multipath`, `fault`,
`hostnqn`, `nvme-cli-2`, `nvme-cli-3`, or `pre-os`, the registry owners
and `ctrl_loss_tmo` of the pre-OS environment (dracut's
`nvmf-registry-pre-os` prototype, nvme-cli#4328). Pick with `--tag`,
`--skip-tag` or by name.

The scenarios cover the corner cases of nvme-cli#4328 that the initrd
decides: a multipath root (one of its paths refused, removed or
inaccessible at boot), the only root path gone, the NBFT's host identity
against the system's (#4384), an initrd without a host identity, the
NBFT's non-bootable, unavailable and missing entries, `rd.nvmf.nonbft`,
nvme-cli 2 without a registry, and the registry owners and
`ctrl_loss_tmo` of the pre-OS connections. What nvme-discoverd does
after switch-root (adoption, reconnects, shutdown) needs the OS, which
the busybox root doesn't run: nvme-cli's mock scenarios cover that.

#### Dashboard

`lab boot test --dashboard [DIR]` adds the results to nvme-cli's
dashboard (`scripts/dashboard/nvme-dashboard` of the checkout in
`nvme_cli` of the config or `LAB_NVME_CLI`, default `~/nvme-cli`) and
puts the site into DIR (default `~/.cache/nvme-lab/dashboard`): open
`DIR/index.html` in a browser. The run is a record of the workflow
"Boot tests", a section per nvme-cli version, a test per scenario
(XFAIL as "failed as expected", XPASS as failed); a case that did not
simply pass links its console, log, dracut log and state. The site is
built on the CI host, which has Jinja2; its history and logs stay there,
in `/root/scratch/lab-dashboard` (not removed by `lab boot teardown`), so
every run adds to the last ones. `--dashboard-link TEXT=URL` puts a link
on the page, e.g. to the dracut pull request tested.

```
lab boot test -j 8 --nvme master --nvme v216 \
    --dracut-module ~/dracut/modules.d/74nvmf --skip-tag pre-os \
    --dashboard --dashboard-link 'dracut #2820=https://github.com/dracut-ng/dracut/pull/2820'
```

#### nvme-cli versions

dracut gets nvme-cli from the distribution, so a change to dracut's
nvmf module is tested against the releases distributions ship that also
get a current dracut (decided 2026-11-06, from repology):

| nvme-cli | Why | Scenarios |
|---|---|---|
| master, 3.2 | current; Tumbleweed, Arch | all (`pre-os` with the prototype) |
| 2.16 | last 2.x: RHEL 9/10, Fedora 43/44, Ubuntu 26.04, SLFO | all but `nvme-cli-3`, `pre-os` |
| 2.13 | Debian 13 | the same |
| 2.8 | oldest 2.x in a supported LTS: Ubuntu 24.04, SLE 15 SP6 | the same |

```
lab boot setup --slots 8
for v in v3.2 v2.16 v2.13 v2.8; do lab sync nvme -C ~/nvme-cli -r $v -n ${v/./}; done
lab sync nvme -C ~/nvme-cli -r upstream/master -n master
lab boot test -j 8 --nvme master --nvme v32 --nvme v216 --nvme v213 \
    --nvme v28 --dracut-module ~/dracut/modules.d/74nvmf
```

Not tested: 1.x and 2.x before 2.5 (RHEL 8, Ubuntu 22.04, Debian 12),
which also keep their old dracut. The module's version boundaries are
2.5 (NBFT support, `connect-all --nbft`, the long option names), 2.7 (the
NetApp rule's name) and 3.0 (`nvme-fabrics.conf`, the registry).

What it can't do: firmware that boots from NVMe/TCP, security profiles
(DH-CHAP/TLS) and extended info descriptors in the NBFT, and the OS
after switch-root (the root is busybox). The initrd uses systemd and
`systemd-networkd` with a static `ip=`: Tumbleweed has no `dhclient`
for dracut's `network-legacy`.

### A quick look

```
lab inventory nvme               # what's there, read only
lab ssh nvme-bsd                 # interactive login
lab ssh nvme dmesg -T \| tail    # the remote shell runs the words
```
