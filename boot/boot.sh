#!/bin/sh
# In the lab-boot container: build an initrd with dracut and boot a QEMU
# guest from the NVMe/TCP root that mkroot.sh made.
#
#   boot.sh --name NAME --target TRADDR:TRSVCID --root-uuid UUID
#           [--nvme-tree DIR | --nvme-package] [--dracut-module DIR]
#           [--source cmdline|static|legacy|none] [--hostonly]
#           [--append ARGS] [--timeout SECONDS]
#
# --nvme-tree      build the nvme-cli tree in DIR and install it
#                  (default: keep the nvme that is installed)
# --nvme-package   reinstall the distribution's nvme-cli
# --dracut-module  use DIR as dracut's 74nvmf module (default: the
#                  package's)
# --source         where the initrd learns the target from:
#   cmdline  rd.nvmf.discover= on the kernel command line (default)
#   static   /etc/nvme/nvme-fabrics.conf (nvme-cli 3), needs --hostonly
#   legacy   /etc/nvme/discovery.conf (nvme-cli 2), needs --hostonly
#   none     nothing: give it with --append
# --hostonly       a hostonly initrd: it carries the container's
#                  /etc/nvme files. Otherwise the host identity goes on
#                  the kernel command line (rd.nvmf.hostnqn/hostid).
#
# Results in /scratch/lab-boot/out/NAME: log, dracut.log, lsinitrd.txt,
# console.txt. Prints RESULT=PASS or RESULT=FAIL; exits 0 on PASS, 1 on
# FAIL, 2 when the case could not be set up.
set -u

NAME='' TARGET='' UUID='' TREE='' PACKAGE='' MODULE='' HOSTONLY='' APPEND=''
SOURCE=cmdline TIMEOUT=240
usage() { sed -n '4,9p' "$0" >&2; exit 2; }
while [ $# -gt 0 ]; do
    case $1 in
        --name) NAME=$2; shift ;;
        --target) TARGET=$2; shift ;;
        --root-uuid) UUID=$2; shift ;;
        --nvme-tree) TREE=$2; shift ;;
        --nvme-package) PACKAGE=1 ;;
        --dracut-module) MODULE=$2; shift ;;
        --source) SOURCE=$2; shift ;;
        --hostonly) HOSTONLY=1 ;;
        --append) APPEND=$2; shift ;;
        --timeout) TIMEOUT=$2; shift ;;
        *) usage ;;
    esac
    shift
done
if [ -z "$NAME" ] || [ -z "$TARGET" ] || [ -z "$UUID" ]; then
    usage
fi
TRADDR=${TARGET%:*} TRSVCID=${TARGET##*:}

OUT=/scratch/lab-boot/out/$NAME
rm -rf "$OUT"
mkdir -p "$OUT"
fail() { echo "SETUP FAILED: $*" | tee -a "$OUT/log" >&2; exit 2; }

{
    if [ -n "$TREE" ]; then
        # The tree belongs to root on the host; meson asks git for the
        # version. sysconfdir: Tumbleweed's meson defaults to /usr/etc,
        # dracut installs /etc/nvme.
        git config --global --add safe.directory '*'
        if [ ! -f "$TREE/.lab-boot/build.ninja" ]; then
            meson setup "$TREE/.lab-boot" "$TREE" --prefix=/usr \
                --sysconfdir=/etc --wrap-mode=default \
                --buildtype=release -Ddocs=false || exit 2
        fi
        ninja -C "$TREE/.lab-boot" >/dev/null || exit 2
        meson install -C "$TREE/.lab-boot" --no-rebuild --quiet || exit 2
        ldconfig
    elif [ -n "$PACKAGE" ]; then
        zypper -q --non-interactive install -y -f nvme-cli || exit 2
    fi
    nvme version
} > "$OUT/log" 2>&1 || fail "installing nvme-cli, see $OUT/log"

rm -rf /usr/lib/dracut/modules.d/74nvmf
cp -a "${MODULE:-/root/74nvmf.package}" /usr/lib/dracut/modules.d/74nvmf \
    || fail "no dracut module ${MODULE:-/root/74nvmf.package}"

rm -rf /etc/nvme/discovery.conf /etc/nvme/config.json \
    /etc/nvme/nvme-fabrics.conf /etc/nvme/nvme-fabrics.conf.d \
    /etc/nvme/*.converted
# The container has one kernel, the one of the kernel-default package.
for KVER in /lib/modules/*; do KVER=${KVER##*/}; break; done
CMDLINE="console=ttyS0 net.ifnames=0 root=UUID=$UUID rw init=/init panic=-1"
CMDLINE="$CMDLINE rd.retry=10 rd.neednet=1"
CMDLINE="$CMDLINE ip=10.0.2.15::10.0.2.2:255.255.255.0:guest:eth0:none"
case $SOURCE in
    cmdline)
        CMDLINE="$CMDLINE rd.nvmf.discover=tcp,$TRADDR,,$TRSVCID" ;;
    static)
        printf '[Discovery Controller]\ncontroller = transport=tcp;traddr=%s;trsvcid=%s\n' \
            "$TRADDR" "$TRSVCID" > /etc/nvme/nvme-fabrics.conf ;;
    legacy)
        echo "--transport=tcp --traddr=$TRADDR --trsvcid=$TRSVCID" \
            > /etc/nvme/discovery.conf ;;
    none) ;;
    *) usage ;;
esac
if [ -n "$HOSTONLY" ]; then
    HO="--hostonly --hostonly-cmdline"
else
    HO="--no-hostonly"
    CMDLINE="$CMDLINE rd.nvmf.hostnqn=$(cat /etc/nvme/hostnqn)"
    CMDLINE="$CMDLINE rd.nvmf.hostid=$(cat /etc/nvme/hostid)"
fi
CMDLINE="$CMDLINE $APPEND"
echo "$CMDLINE" > "$OUT/cmdline"

# shellcheck disable=SC2086
dracut --force $HO --add "nvmf systemd-networkd" \
    --add-drivers "nvme_tcp nvme_fabrics virtio_net virtio_pci ext4" \
    --kver "$KVER" "$OUT/initrd.img" > "$OUT/dracut.log" 2>&1 \
    || fail "dracut, see $OUT/dracut.log"
lsinitrd "$OUT/initrd.img" > "$OUT/lsinitrd.txt" 2>&1

timeout "$TIMEOUT" qemu-system-x86_64 -enable-kvm -m 1024 -smp 2 \
    -nographic -no-reboot -kernel "/boot/vmlinuz-$KVER" \
    -initrd "$OUT/initrd.img" -append "$CMDLINE" \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
    > "$OUT/console.txt" 2>&1

if grep -a -q LAB-BOOT-ROOT-OK "$OUT/console.txt"; then
    echo "RESULT=PASS $NAME"
    exit 0
fi
echo "RESULT=FAIL $NAME, see $OUT/console.txt"
exit 1
