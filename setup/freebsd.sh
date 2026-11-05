#!/bin/sh
#
# Make a fresh FreeBSD VM ready for nvme-cli's FreeBSD port: the steps of
# nvme-cli's .github/workflows/build-freebsd.yml (packages, meson build,
# meson test). Run as root from the nvme-lab checkout; re-runnable (the
# tree is fetched, not reset).
#
#     pkg install -y git
#     git clone https://github.com/igaw/nvme-lab /root/nvme-lab
#     /root/nvme-lab/setup/freebsd.sh

set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
src=/root/work

echo "==> packages"
pkgs=$(sed 's/#.*//' "${here}/setup/packages-freebsd.txt" | xargs)
# shellcheck disable=SC2086 # one package per word
env ASSUME_ALWAYS_YES=yes pkg install -y ${pkgs}

echo "==> nvme-cli"
mkdir -p "${src}"
if [ -d "${src}/nvme-cli/.git" ]; then
    git -C "${src}/nvme-cli" fetch -q origin
else
    git clone -q https://github.com/linux-nvme/nvme-cli.git "${src}/nvme-cli"
fi
cd "${src}/nvme-cli"
[ -d .build ] || meson setup .build
ninja -C .build
meson test -C .build

echo "==> done: ${src}/nvme-cli/.build/nvme"
