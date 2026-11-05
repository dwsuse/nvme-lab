---
name: nvme-lab
description: Run nvme-cli builds and tests on the lab VMs (nvme host, nvmet NVMe/TCP target, nvme-bsd FreeBSD) with the `lab` tool from ~/nvme-lab. Use for CI parity before pushing an nvme-cli PR (build.sh in the CI images), FreeBSD builds/tests, NVMe/TCP connect/DH-CHAP/TLS tests, blktests or nvmf-failover runs, anything that needs root or a real/emulated NVMe device, and for checking what is on the VMs.
---

# nvme-lab

The lab tool is `~/nvme-lab/bin/lab` (repo igaw/nvme-lab). The full guide
is `~/nvme-lab/docs/testing.md`; read it when a run isn't covered here.

## Commands

```
L=~/nvme-lab/bin/lab
$L hosts                                   # nvme, nvmet, nvme-bsd
$L inventory [HOST...]                     # read only; first thing in a session
$L sync HOST [-C TREE] [-n NAME] [-w]      # git tree -> /root/scratch/NAME
$L ci IMAGE NAME [--pull] -- COMMAND       # CI image on nvme, tree NAME
$L ssh HOST [COMMAND...]                   # remote shell runs the words
$L scratch HOST [--rm NAME...]             # list / remove scratch trees
```

- `sync` pushes HEAD (or `-r REF`) with git and checks it out detached:
  a real git checkout, incremental, build dir kept. `-w` also copies
  uncommitted changes. NAME defaults to the tree's directory name; use
  the branch or PR (`pr-4376`).
- `ci` images: debian, tumbleweed, fedora, ... (`ghcr.io/linux-nvme/*`).
  CI parity for an nvme-cli PR before pushing:
  `lab ci debian N -- scripts/build.sh -b release -c gcc`, same with
  `tumbleweed`, and `lab ci debian N -- scripts/build.sh -m muon`.
  `--pull` first if the image may be old. Default Docker network on
  nvme has no DNS: for apt/pip use `docker run --network host` via ssh.
- FreeBSD: `lab sync nvme-bsd -n N`, then `meson setup .build && ninja
  -C .build && meson test -C .build` in `/root/scratch/N`.
- NVMe/TCP: `nvmet-target add-subsys NQN --no-auth --host HOSTNQN` on
  nvmet (prints the port, from 4421), `nvme connect -t tcp -a nvmet -s
  PORT -n NQN` on nvme, then `disconnect` and `del-subsys`.

## Rules

- Own work only in `/root/scratch/NAME`; `/root/work` belongs to the
  setup. Remove your scratch trees when done (`lab scratch HOST --rm`);
  never remove other trees without asking the user.
- nvmet: only `add-subsys`/`del-subsys`; leave the default target alone.
- No keys or secrets in any repo; DH-CHAP/TLS keys are made on the VMs.
- Report only finished runs, with host and commit; quote the failing
  output. A wrong-looking result is investigated, not retried until green.
- `pkill -f` over ssh matches the ssh session itself: use `pkill -x` or
  a PID.
- If `lab` fails to log in, say so; the key is in
  `~/.config/nvme-lab/config.toml` (`ssh_key`).
- Tools that will be used again belong in nvme-lab (with a test, `make
  check`); one-off scripts don't.
