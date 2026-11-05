#!/bin/sh
# In the lab-boot container: put the test root on the NVMe/TCP namespace.
#
#   mkroot.sh TRADDR TRSVCID NQN UUID
#
# An ext4 file system with the given UUID, busybox, an os-release (systemd
# switches only into a root that has one) and an /init that prints
# "LAB-BOOT-ROOT-OK <kernel command line>" and powers the guest off.
set -eu

[ $# -eq 4 ] || { echo "usage: $0 TRADDR TRSVCID NQN UUID" >&2; exit 2; }
TRADDR=$1 TRSVCID=$2 NQN=$3 UUID=$4
MNT=/mnt/lab-boot-root

nvme disconnect -n "$NQN" >/dev/null 2>&1 || true
nvme connect -t tcp -a "$TRADDR" -s "$TRSVCID" -n "$NQN" 2>&1 \
    | grep -v libkmod || true
sleep 2
CTRL=$(nvme list-subsys -o json \
    | jq -r ".[].Subsystems[] | select(.NQN==\"$NQN\") | .Paths[0].Name")
if [ -z "$CTRL" ] || [ "$CTRL" = null ]; then
    echo "not connected" >&2
    exit 1
fi
for NS in "/sys/class/nvme/$CTRL/"nvme*n1; do NS=${NS##*/}; break; done
# With native multipath the block device is nvmeXnY, not nvmeXcZnY.
BLK=$(echo "$NS" | sed 's/c[0-9]*n/n/')
# The container's /dev does not follow devices that appear later.
if [ ! -b "/dev/$BLK" ]; then
    IFS=: read -r MAJOR MINOR < "/sys/class/block/$BLK/dev"
    mknod "/dev/$BLK" b "$MAJOR" "$MINOR"
fi

mkfs.ext4 -q -F -L lab-boot-root -U "$UUID" "/dev/$BLK"
mkdir -p "$MNT"
mount "/dev/$BLK" "$MNT"
mkdir -p "$MNT/bin" "$MNT/proc" "$MNT/sys" "$MNT/dev" "$MNT/etc" \
    "$MNT/usr/lib"
printf 'NAME=lab-boot-root\nID=lab-boot-root\n' > "$MNT/usr/lib/os-release"
ln -sf ../usr/lib/os-release "$MNT/etc/os-release"
cp /usr/bin/busybox-static "$MNT/bin/busybox"
ln -sf busybox "$MNT/bin/sh"
cat > "$MNT/init" <<'EOF'
#!/bin/sh
/bin/busybox mount -t proc proc /proc
echo "LAB-BOOT-ROOT-OK $(/bin/busybox cat /proc/cmdline)"
echo o > /proc/sysrq-trigger
/bin/busybox sleep 30
EOF
chmod +x "$MNT/init"
umount "$MNT"
nvme disconnect -n "$NQN" >/dev/null
echo "root: /dev/$BLK on $NQN, UUID $UUID"
