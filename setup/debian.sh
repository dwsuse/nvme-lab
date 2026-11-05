#!/bin/bash
#
# Make a fresh Debian 13 (trixie) VM ready for NVMe/TCP tests:
#
#     setup/debian.sh nvme      # host
#     setup/debian.sh nvmet     # target
#
# Run as root from the nvme-lab checkout (/root/nvme-lab). Re-runnable:
# a second run updates the sources and rebuilds.
#
# - packages from setup/packages-<role>.txt
# - the newest kernel Debian has (setup/kernel.sh debian); trixie's own
#   kernel stays installed as the fallback boot entry
# - nvme-cli (master) and the dependencies its CI builds from source
#   (scripts/ci-deps.sh: ktls-utils for tlshd, mdadm) into /usr
# - role: nvme gets the fixed host identity (common/), nvmf-failover and
#   blktests; nvmet gets nvmet-target and its modules
# Keys are never created here: see README, "Secrets".

set -euo pipefail

role=${1:-}
case "${role}" in
    nvme|nvmet) ;;
    *) echo "usage: $0 nvme|nvmet" >&2; exit 2 ;;
esac

here=$(cd "$(dirname "$0")/.." && pwd)
export DEBIAN_FRONTEND=noninteractive

. /etc/os-release
[[ "${VERSION_CODENAME}" == trixie ]] ||
    { echo "$0: needs Debian 13 (trixie), this is ${PRETTY_NAME}" >&2; exit 1; }

step() { echo; echo "==> $*"; }

# Backports and experimental: apt only takes packages from them when
# asked (-t), so the rest of the system stays trixie.
step "apt sources"
cat > /etc/apt/sources.list.d/nvme-lab.sources <<EOF
# nvme-lab setup/debian.sh: only for the kernel (apt -t)
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie-backports experimental
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
if [[ "${role}" == nvme ]]; then
    # cross builds against i386 (libjson-c-dev:i386)
    dpkg --add-architecture i386
fi
apt-get update -q

step "packages"
mapfile -t pkgs < <(sed 's/#.*//' "${here}/setup/packages-${role}.txt" | tr ' ' '\n' | sed '/^$/d')
apt-get install -y -q "${pkgs[@]}"
[[ "${role}" == nvme ]] && apt-get install -y -q libjson-c-dev:i386

step "kernel"
"${here}/setup/kernel.sh" debian

step "nvme-cli and its dependencies from source (ktls-utils, mdadm)"
"${here}/setup/build.sh" nvme-cli ktls-utils mdadm

step "tools"
install -m 0755 "${here}/bin/nuke-kernel" /usr/local/sbin/nuke-kernel

case "${role}" in
nvme)
    step "host identity, modules"
    install -d /etc/nvme
    install -m 0644 "${here}/common/nvme.hostnqn" /etc/nvme/hostnqn
    install -m 0644 "${here}/common/nvme.hostid" /etc/nvme/hostid
    printf '%s\n' nvme-tcp > /etc/modules-load.d/nvme-lab.conf

    step "nvmf-failover, blktests"
    "${here}/setup/build.sh" nvmf-failover blktests
    ;;
nvmet)
    step "nvmet-target, modules"
    install -m 0755 "${here}/bin/nvmet-target" /usr/local/sbin/nvmet-target
    printf '%s\n' nvmet nvmet-tcp > /etc/modules-load.d/nvme-lab.conf
    ;;
esac
modprobe -a $(cat /etc/modules-load.d/nvme-lab.conf) || true

step "done"
echo "Kernel installed: $(ls /boot/vmlinuz-* | sed 's#/boot/vmlinuz-##' | tr '\n' ' ')"
echo "Running: $(uname -r). Reboot into the new kernel: systemctl reboot"
[[ "${role}" == nvmet ]] && echo "Then: KEYS_FILE (HOST_KEY=, CTRL_KEY=) and 'nvmet-target up' (see README)"
exit 0
