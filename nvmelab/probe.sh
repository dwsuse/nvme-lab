#!/bin/sh
# Inventory probe, piped to a lab VM by 'lab inventory' and run there as
# root. POSIX sh, so it runs on a fresh Debian or FreeBSD VM before any
# setup: it needs nothing that isn't in the base system.
#
# Prints one "key<TAB>value" line per fact; a key may repeat (disk,
# repo, ...). Read only: changes nothing, never prints keys or secrets,
# only the names of the files in /etc/nvme.

set -u

have()
{
	command -v "$1" >/dev/null 2>&1
}

# out KEY VALUE...: one line, words squeezed, nothing for an empty value
out()
{
	key=$1
	shift
	val=$(echo "$*" | tr -s ' \t' '  ' | sed 's/^ //; s/ $//')
	[ -n "${val}" ] && printf '%s\t%s\n' "${key}" "${val}"
}

# names DIR: the entries of DIR on one line
names()
{
	n=""
	for e in "$1"/*; do
		[ -e "${e}" ] && n="${n} ${e##*/}"
	done
	echo "${n}"
}

repo()
{
	d=$1
	if ! git -C "${d}" rev-parse --git-dir >/dev/null 2>&1; then
		out dir "${d}"
		return
	fi
	changes=""
	[ -n "$(git -C "${d}" status --porcelain 2>/dev/null)" ] &&
		changes=" (changes)"
	out repo "${d} $(git -C "${d}" rev-parse --abbrev-ref HEAD)" \
		"$(git -C "${d}" log -1 --format='%h %cs')" \
		"$(git -C "${d}" remote get-url origin 2>/dev/null)${changes}"
}

linux()
{
	# shellcheck source=/dev/null
	. /etc/os-release
	out os "${PRETTY_NAME}"
	out kernel "$(uname -r)"
	have dpkg && dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' \
		'linux-image-[0-9]*' 2>/dev/null |
		awk '$1 == "ii" { print $2 }' | while read -r k; do
			out kernel-pkg "${k#linux-image-}"
		done
	out cpu "$(nproc) x" \
		"$(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -1)"
	out memory "$(awk '/^MemTotal/ { printf "%.1f GiB", $2 / 1048576 }' \
		/proc/meminfo)"
	if have hostname; then
		out address "$(hostname -I)"
	else
		out address "$(ip -4 -o addr show scope global 2>/dev/null |
			awk '{ sub(/\/.*/, "", $4); print $4 }')"
	fi
	lsblk -dbno NAME,SIZE,MODEL 2>/dev/null | while read -r n s m; do
		case ${n} in sr*) continue ;; esac
		out disk "${n} $((s / 1073741824)) GiB ${m}"
	done
	out rootfs "$(df -h / | awk 'NR == 2 { print $2 " (" $5 " used)" }')"
	out modules "$(lsmod | awk '/^nvme/ { print $1 }' | sort | xargs)"
	if [ -d /sys/kernel/config/nvmet ]; then
		out nvmet-subsystems \
			"$(names /sys/kernel/config/nvmet/subsystems)"
		out nvmet-ports "$(names /sys/kernel/config/nvmet/ports)"
	fi
	for s in tlshd docker nvmf-autoconnect nvme-discoverd; do
		systemctl is-active -q "${s}" 2>/dev/null && out service "${s}"
	done
	have docker && docker images --format '{{.Repository}}:{{.Tag}}' \
		2>/dev/null | while read -r i; do out image "${i}"; done
}

freebsd()
{
	out os "FreeBSD $(freebsd-version -u)"
	out kernel "$(freebsd-version -k)"
	out cpu "$(sysctl -n hw.ncpu) x $(sysctl -n hw.model)"
	out memory "$(sysctl -n hw.physmem |
		awk '{ printf "%.1f GiB", $1 / 1073741824 }')"
	out address "$(ifconfig | awk '/inet / && $2 != "127.0.0.1" { print $2 }')"
	geom disk list | awk '
		/Geom name:/ { n = $3 }
		/Mediasize:/ { s = $2 }
		/descr:/ { sub(/.*descr: */, "")
			if (n !~ /^cd/)
				printf "%s %d GiB %s\n", n, s / 1073741824, $0 }' |
		while read -r l; do out disk "${l}"; done
	out rootfs "$(df -h / | awk 'NR == 2 { print $2 " (" $5 " used)" }')"
	out packages "$(pkg info -q 2>/dev/null | wc -l)"
}

out host "$(uname -n)"
case $(uname -s) in
Linux) linux ;;
FreeBSD) freebsd ;;
*) out os "$(uname -sr)" ;;
esac

have nvme && out nvme "$(command -v nvme): $(nvme version 2>/dev/null | head -1)"
b=/root/work/nvme-cli/.build/nvme
[ -x "${b}" ] && out nvme "${b}: $("${b}" version 2>/dev/null | head -1)"
[ -d /etc/nvme ] && out etc-nvme "$(names /etc/nvme)"
tools=""
for t in git tlshd nvmetcli nvmf-failover mdadm gcc clang meson muon \
	docker python3; do
	have "${t}" && tools="${tools} ${t}"
done
out tools "${tools}"
have tlshd && out tlshd "$(tlshd --version 2>&1 | head -1)"

[ -d /root/nvme-lab ] && repo /root/nvme-lab
for d in /root/work/*; do
	[ -d "${d}" ] && repo "${d}"
done
for d in /root/scratch/*; do
	[ -d "${d}" ] && out scratch "${d}"
done
exit 0
