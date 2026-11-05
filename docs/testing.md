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

### A quick look

```
lab inventory nvme               # what's there, read only
lab ssh nvme-bsd                 # interactive login
lab ssh nvme dmesg -T \| tail    # the remote shell runs the words
```
