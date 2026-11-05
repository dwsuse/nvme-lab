# nvme-lab

Test VMs for nvme-cli and the kernel's NVMe-oF code: `nvme` (host),
`nvmet` (target), `nvme-bsd` (nvme-cli's FreeBSD port). The VMs are
disposable; this repo makes a fresh one ready for NVMe/TCP tests, and
`bin/lab` drives them from the workstation. Throwaway test scripts
don't belong here, tools that are used again do.

## Using the lab

```
ln -s ~/nvme-lab/bin/lab ~/bin/lab
echo 'ssh_key = "~/.ssh/nvme-cli-test"' > ~/.config/nvme-lab/config.toml
lab hosts                                  # the VMs of lab.toml
lab inventory                              # what's on them (read only)
lab sync nvme -n pr-1234                   # this git tree -> /root/scratch/pr-1234
lab ci debian pr-1234 -- scripts/build.sh  # in an nvme-cli CI image
lab ssh nvme-bsd                           # log in
```

[docs/testing.md](docs/testing.md) is the guide: workstation setup,
the rules for using the VMs, and the usual runs (a branch on the host,
CI parity, FreeBSD, NVMe/TCP between `nvme` and `nvmet`).

| Path | What |
|---|---|
| `bin/lab`, `nvmelab/` | the workstation tool (Python >= 3.11, standard library only); `nvmelab/probe.sh` is the inventory probe it pipes to a VM |
| `lab.toml` | the VMs: name, address, what they are for |
| `setup/debian.sh nvme\|nvmet` | packages, newest Debian kernel, nvme-cli and its source-built dependencies, role setup |
| `setup/kernel.sh` | newest Debian kernel, or a development kernel built from a tree |
| `setup/build.sh` | clone/update and build nvme-cli, ktls-utils + mdadm (versions from nvme-cli's `scripts/ci-deps.sh`), nvmf-failover, blktests |
| `setup/freebsd.sh` | FreeBSD VM (`nvme-bsd`): packages and nvme-cli's FreeBSD build and tests, as its CI |
| `setup/packages-*.txt` | packages per VM |
| `bin/nvmet-target` | target in configfs: `up [--tls] [--no-auth]`, `add-subsys`, `del-subsys`, `status`, `down` |
| `bin/nuke-kernel` | remove development kernels that aren't packages |
| `common/` | the host identity (hostnqn, hostid) `nvmet-target` allows by default |
| `config/tlshd.conf` | tlshd with debug logging, for TLS debugging |
| `docs/` | the testing guide, the inventory of the VMs, TLS/tlshd debugging notes, the old VMs and their backup |
| `tests/`, `Makefile` | `make check`: unit tests, shell syntax, shellcheck (CI runs it) |

## A new VM

Debian 13 cloud image (the lab's template), then as root:

```
git clone https://github.com/igaw/nvme-lab /root/nvme-lab
/root/nvme-lab/setup/debian.sh nvme       # or nvmet
systemctl reboot                          # into the new kernel
```

`nvmet` needs a second disk for the default namespace (`/dev/vdb`,
`DEV=` in `nvmet-target`).

FreeBSD (`nvme-bsd`, official `BASIC-CLOUDINIT` image), as root:

```
pkg install -y git
git clone https://github.com/igaw/nvme-lab /root/nvme-lab
/root/nvme-lab/setup/freebsd.sh
```

## Secrets

No keys in this repo (`.gitignore` refuses `*keys*`). Made on the
VMs, copied between them by hand (tested 2026-11-05 with nvme-cli
master):

```
H=$(cat /etc/nvme/hostnqn)     # on nvme; nvmet-target's default HOST
# DH-CHAP: KEYS_FILE (default /root/nvmet-keys, mode 0600) on the target
HK=$(nvme keys gen-kxchap-secret -m 1); CK=$(nvme keys gen-kxchap-secret -m 1)
printf 'HOST_KEY="%s"\nCTRL_KEY="%s"\n' "$HK" "$CK" > /root/nvmet-keys
nvmet-target up
nvme connect -t tcp -a nvmet -n nqn.io-1 --kxchap-secret "$HK" --kxchap-ctrl-secret "$CK"

# TLS: the PSK file, the same on both sides; nvmet-target imports it
nvme keys gen-tls-psk -m 1 -i -n "$H" -c nqn.io-1 -f /etc/nvme/tls-keys
nvmet-target up --tls --no-auth
nvme keys import -f /etc/nvme/tls-keys    # on the host
systemctl restart tlshd                   # see below
nvme connect -t tcp -a nvmet -n nqn.io-1 --tls
```

Seen 2026-11-05 (tlshd 1.5.0, kernel 7.3): the first TLS connect right
after importing the PSK on the host hung in `connecting`; after a
restart of tlshd every connect worked. Cause not pinned down.

## Kernels

`setup/kernel.sh debian` installs the newest kernel Debian has
(experimental, else trixie-backports). `setup/kernel.sh dev [TREE]`
builds a development kernel from a tree with Debian's config. Either
way trixie's own kernel stays installed as the fallback in grub; with
the serial console in Proxmox a kernel that doesn't boot is reverted
from there. `nuke-kernel` removes old development kernels.
