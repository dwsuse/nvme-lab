#!/bin/bash
#
# Clone (or update) and build the upstream trees of setup/repos.txt:
#
#     setup/build.sh nvme-cli ktls-utils mdadm nvmf-failover blktests
#
# nvme-cli first: ktls-utils and mdadm take their versions from it.
#
# A tree that is already there is fetched, not reset: local branches
# and changes stay. Builds install into /usr (nvme-cli, tlshd), like on
# the old VMs, so they replace distribution packages of the same name.

set -euo pipefail

src=/root/work

clone() {
    local url=$1 dir=$2 branch=$3
    if [[ -d "${dir}/.git" ]]; then
        git -C "${dir}" fetch -q origin
    else
        git clone -q -b "${branch}" "${url}" "${dir}"
    fi
}

build_nvme_cli() {
    local d=${src}/nvme-cli
    clone https://github.com/linux-nvme/nvme-cli.git "${d}" master
    cd "${d}"
    [[ -d .build ]] || meson setup .build --prefix=/usr --sysconfdir=/etc
    ninja -C .build
    ninja -C .build install
    ldconfig
    nvme version
}

# nvme-cli's external dependencies that distributions ship too old:
# the same list and versions as nvme-cli's CI (scripts/ci-deps.sh, with
# the reasons), read from the nvme-cli tree so both stay in step.
ci_dep_version() {
    local var=$1 v
    v=$(sed -n "s/^${var}=//p" "${src}/nvme-cli/scripts/ci-deps.sh")
    [[ -n "${v}" ]] || { echo "no ${var} in nvme-cli's scripts/ci-deps.sh" >&2; exit 1; }
    echo "${v}"
}

checkout_tag() {
    local url=$1 dir=$2 tag=$3
    [[ -d "${dir}/.git" ]] || git clone -q "${url}" "${dir}"
    git -C "${dir}" fetch -q --tags origin
    git -C "${dir}" checkout -q "${tag}"
}

build_ktls_utils() {
    local d=${src}/ktls-utils v
    v=$(ci_dep_version KTLS_UTILS_VERSION)
    checkout_tag https://github.com/oracle/ktls-utils.git "${d}" "ktls-utils-${v}"
    cd "${d}"
    ./autogen.sh
    # DANE isn't used by NVMe (as in ci-deps.sh)
    ./configure --prefix=/usr --sysconfdir=/etc --without-dane --with-systemd
    make -j"$(nproc)"
    make install
    systemctl daemon-reload
    systemctl enable tlshd
    systemctl restart tlshd
    echo "tlshd ${v}"
}

build_mdadm() {
    local d=${src}/mdadm v
    v=$(ci_dep_version MDADM_VERSION)
    checkout_tag https://git.kernel.org/pub/scm/utils/mdadm/mdadm.git "${d}" "mdadm-${v}"
    make -C "${d}" -j"$(nproc)" BINDIR=/usr/sbin
    make -C "${d}" install BINDIR=/usr/sbin UDEVDIR=/usr/lib/udev
    mdadm --version
}

build_nvmf_failover() {
    local d=${src}/nvmf-failover
    clone https://github.com/igaw/nvmf-failover.git "${d}" master
    python3 -m venv /root/venvs/nvmf-failover
    /root/venvs/nvmf-failover/bin/pip install -q -e "${d}"
    ln -sf /root/venvs/nvmf-failover/bin/nvmf-failover /usr/local/bin/nvmf-failover
}

build_blktests() {
    local d=${src}/blktests
    clone https://github.com/linux-blktests/blktests.git "${d}" master
    make -C "${d}"
    # NVMe/TCP against a local target (nvmet on the same VM). The
    # two-VM setup (nvme → nvmet) uses nvmf-failover or the scripts.
    [[ -f "${d}/config" ]] || cat > "${d}/config" <<'EOF'
# nvme-lab: run with ./check nvme
NVMET_TRTYPES="tcp"
EOF
}

[[ $# -gt 0 ]] || { echo "usage: $0 nvme-cli|ktls-utils|mdadm|nvmf-failover|blktests..." >&2; exit 2; }
mkdir -p "${src}"
for t in "$@"; do
    echo "--- ${t}"
    "build_${t//-/_}"
done
