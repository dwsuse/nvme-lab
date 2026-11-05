# nvme-lab

Test VMs for nvme-cli and the kernel's NVMe-oF code: `nvme` (host),
`nvmet` (target), `freebsd` (nvme-cli's FreeBSD port). The VMs are
disposable; this repo makes a fresh one ready for NVMe/TCP tests.
Throwaway test scripts don't belong here, tools that are used again do.

| Path | What |
|---|---|
| `setup/debian.sh nvme\|nvmet` | packages, newest Debian kernel, nvme-cli and its source-built dependencies, role setup |
| `setup/kernel.sh` | newest Debian kernel, or a development kernel built from a tree |
| `setup/build.sh` | clone/update and build nvme-cli, ktls-utils + mdadm (versions from nvme-cli's `scripts/ci-deps.sh`), nvmf-failover, blktests |
| `setup/packages-*.txt` | packages per VM |
| `bin/nvmet-target` | target in configfs: `up [--tls] [--no-auth]`, `add-subsys`, `status`, `down` |
| `bin/nuke-kernel` | remove development kernels that aren't packages |
| `common/` | the host identity (hostnqn, hostid) `nvmet-target` allows by default |
| `config/tlshd.conf` | tlshd with debug logging, for TLS debugging |
| `docs/` | TLS/tlshd debugging notes, inventory of the old VMs |

## A new VM

Debian 13 cloud image (the lab's template), then as root:

```
git clone https://github.com/igaw/nvme-lab /root/nvme-lab
/root/nvme-lab/setup/debian.sh nvme       # or nvmet
systemctl reboot                          # into the new kernel
```

`nvmet` needs a second disk for the default namespace (`/dev/vdb`,
`DEV=` in `nvmet-target`).

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
