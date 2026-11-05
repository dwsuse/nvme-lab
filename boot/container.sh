#!/bin/sh
# Create the lab-boot container on the CI host: openSUSE Tumbleweed with
# dracut, a kernel, QEMU and the tools to build nvme-cli. It boots a QEMU
# guest from an NVMe/TCP root (see boot.sh), with the host's /dev/kvm.
#
#   container.sh HOSTNQN HOSTID [--recreate]
#
# HOSTNQN/HOSTID become the container's /etc/nvme/hostnqn and hostid:
# the identity dracut puts into a hostonly initrd, and the one the target
# lets in. /root/scratch is /scratch in the container.
set -eu

NAME=lab-boot
IMAGE=ghcr.io/linux-nvme/tumbleweed:latest
# systemd-networkd: Tumbleweed has no dhclient, so dracut's network-legacy
# can't be used; the initrd uses systemd and networkd with a static ip=.
PACKAGES="kernel-default dracut qemu-x86 busybox-static nvme-cli \
e2fsprogs jq iproute2 kmod systemd-networkd meson ninja gcc git \
pkgconf-pkg-config"

[ $# -ge 2 ] || { echo "usage: $0 HOSTNQN HOSTID [--recreate]" >&2; exit 2; }
HOSTNQN=$1
HOSTID=$2

if [ "${3:-}" = --recreate ]; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

if ! docker inspect "$NAME" >/dev/null 2>&1; then
    docker run -d --name "$NAME" --privileged --network host \
        --device /dev/kvm -v /root/scratch:/scratch "$IMAGE" \
        sleep infinity >/dev/null
    # The image has busybox-gawk, which conflicts with the gawk that
    # dracut needs: --force-resolution replaces it.
    # shellcheck disable=SC2086
    docker exec "$NAME" sh -c "zypper -q --non-interactive refresh && \
        zypper -q --non-interactive install -y --no-recommends \
        --force-resolution $PACKAGES" >/dev/null
    # The package's nvmf module, for the runs without an overlay.
    docker exec "$NAME" cp -a /usr/lib/dracut/modules.d/74nvmf \
        /root/74nvmf.package
fi

docker exec "$NAME" sh -c "mkdir -p /etc/nvme && \
    echo '$HOSTNQN' > /etc/nvme/hostnqn && echo '$HOSTID' > /etc/nvme/hostid"
docker exec "$NAME" sh -c 'echo "kernel: $(ls /lib/modules)"; \
    rpm -q dracut nvme-cli qemu-x86'
