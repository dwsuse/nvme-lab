# The old VMs (inventory of 2026-11-05)

Taken before the move to beryllium. All three ran on neon in main.

| VM | OS | Address | Disk | Notes |
|---|---|---|---|---|
| nvme (VM 1026) | Debian 12, kernel 6.12.95 (bpo) | 192.168.30.31 | 100 GB, 52 GB used | host; docker, cross compilers |
| nvmet | Debian 13, kernel 6.19.10 from debian-fastforward (`ffwd13`) | 192.168.30.30 | 5 GB (79 %) + vdb, vdc 1 GB (namespaces) | target; scripts address it as `.30.30` |
| freebsd (VM 105) | FreeBSD 15.1, pkgbase | 192.168.30.172 | 4.3 GB ZFS (91 %), QEMU NVMe 1 GB | nvme-cli FreeBSD port |

## Built from source (not packages)

- nvme: nvme-cli 3.2 into `/usr` (overrides the distro's), `/opt/nvme-privsep`
  (privsep build), `/opt/libnvme-1.16` (old libnvme for libblockdev),
  `tlshd` from ktls-utils, `nvmetcli` in `/usr/local/sbin`.
- nvmet: nvme-cli 3.2 into `/usr`, `tlshd` from ktls-utils, `nvmetcli`
  (hreinecke `restapi` branch) in `/usr/sbin` plus `/root/nvmetcli-venv`;
  `tlshd.service` and `rtslib-fb-targetctl` enabled.
- freebsd: nvme-cli from `/root/nvme-cli` (branch `freebsd`).

## Host identity

The target's default host (`nvmet-target`, `nvmet-setup`, nvmetcli JSON)
is `nqn.2014-08.org.nvmexpress:uuid:befdec4c-2234-11b2-a85c-ca77c773af36`;
the host scripts pass it with `--hostnqn`. `/etc/nvme/hostnqn` on the VMs
themselves differs (`common/*.hostnqn`).

## Not carried over (dropped, user 2026-11-05: too specific)

Scripts of single tests and old target setups (privsep ps4/ps5, ANA,
bsc1285034, TLS key parsing, libblockdev, block/unblock, nvmet-setup,
nvmetcli JSON configs, keylookup.c, `old/`) and the nvmf-failover
campaigns t01–t03: copies in the backup, `scripts/`.


Build and copy trees on nvme: `cross-*`, `ci-*`, `pr-4351`, `wt-4351`,
`nvme-cli-{campaign,fix,i386,privsep}`, `wiki-test-3.2`, `ana-src`,
`tlstest/{old,new}`, `work/bd-*`, `work/libblockdev-baseline`,
`work/libnvme-1.16.2`, `site-shot`, `linux` (b6.13.8), `nvmf-failover-ci`
(older than GitHub). On nvmet: `wiki-*.img`, `nvmetcli-venv`, `update/`.
On freebsd: `nvme-cli-ps4`, `nvme-fix`, `nvme-hw`, `freebsd.bundle`.

## Backup

On nitrogen, `~/Litterbox/homes/ai/nvme-lab-backup/` (not in this repo):
a `git bundle --all` per repo with local-only work, stashes and
uncommitted changes as `refs/backup/{stash-N,worktree}`, untracked
files as `*.untracked.tar`; freebsd's repo is shallow, so its `.git`
as a tar. Restore: `git clone <bundle>` or `git fetch <bundle>
'+refs/*:refs/*'`. Each restored and fsck-checked 2026-11-05.

| VM | Repo | Local-only |
|---|---|---|
| nvme | `work/nvme-cli` | 52 branches, 2 stashes, uncommitted changes |
| nvme | `work/libblockdev`, `work/libblockdev-dwsuse` | libnvme3 (4 commits), uncommitted |
| nvme | `work/nvmetcli` | fix-debian-tests, setup-pyproject, 1 stash |
| nvme | `rp`, `wt-4351` | local repos without remote |
| nvmet | `nvmetcli`, `ktls-utils` | 1 stash each |
| freebsd | `nvme-cli` | `freebsd` (122 commits), `master` (117), 2 rebase branches, 1 stash |

`test-keys.txt` there holds the keys that were in the scripts.
