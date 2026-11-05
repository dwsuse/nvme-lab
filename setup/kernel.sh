#!/bin/bash
#
# Kernels for the test VMs. The distribution kernel the VM came with
# (trixie's) always stays installed: it's the fallback in grub when a
# newer or a development kernel doesn't boot (serial console in Proxmox).
#
#     setup/kernel.sh debian            # newest Debian kernel: experimental,
#                                       # else trixie-backports
#     setup/kernel.sh dev [TREE]        # build TREE (default /root/linux)
#                                       # with Debian's config as a package
#                                       # (-nvmelab suffix) and install it
#
# Old development kernels go with nuke-kernel (bin/).

set -euo pipefail

# The generic flavour, as in the Debian image the VMs come from; unlike
# cloud-amd64 it has rdma_rxe (soft RoCE) for NVMe/RDMA tests.
flavour=amd64

debian_kernel() {
    local suite
    # Newer kernels pre-depend on a newer linux-base than trixie has
    # (7.3 from experimental: >= 4.17, in trixie-backports); apt -t
    # experimental doesn't look in backports for it by itself.
    apt-get install -y -q -t trixie-backports linux-base
    # experimental can still need something trixie doesn't have; apt
    # refuses then, and backports is next.
    for suite in experimental trixie-backports; do
        echo "trying linux-image-${flavour} from ${suite}"
        if apt-get install -y -q -t "${suite}" "linux-image-${flavour}"; then
            echo "kernel from ${suite}: $(dpkg-query -W -f='${Version}' "linux-image-${flavour}")"
            return 0
        fi
    done
    echo "no newer Debian kernel installed; trixie's stays" >&2
    return 1
}

dev_kernel() {
    local tree=${1:-/root/linux} jobs
    [[ -d "${tree}/.git" ]] || { echo "no kernel tree at ${tree}" >&2; exit 1; }
    jobs=$(nproc)
    cd "${tree}"
    # Debian's config of the running kernel, without Debian's signing
    # keys (not in the tree) and without debug info (build time, size).
    cp "/boot/config-$(uname -r)" .config
    scripts/config \
        --set-str SYSTEM_TRUSTED_KEYS "" \
        --set-str SYSTEM_REVOCATION_KEYS "" \
        --disable DEBUG_INFO_DWARF5 --disable DEBUG_INFO_DWARF4 \
        --disable DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
        --enable DEBUG_INFO_NONE --disable DEBUG_INFO_BTF \
        --set-str LOCALVERSION "-nvmelab"
    make olddefconfig
    make -j"${jobs}" bindeb-pkg
    # bindeb-pkg leaves the packages next to the tree
    local deb
    deb=$(ls -t ../linux-image-*-nvmelab_*.deb | grep -v dbg | head -1)
    dpkg -i "${deb}"
    echo "installed ${deb}; reboot to use it, trixie's kernel stays in grub"
}

case "${1:-}" in
    debian) debian_kernel ;;
    dev) shift; dev_kernel "$@" ;;
    *) echo "usage: $0 debian | dev [TREE]" >&2; exit 2 ;;
esac
