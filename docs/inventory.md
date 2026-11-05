# Inventory of the lab VMs (2026-11-05)

Made with `lab inventory --markdown`; regenerate it after changing a VM.

## nvme

| | |
|---|---|
| host | nvme |
| os | Debian GNU/Linux 13 (trixie) |
| kernel | 7.3-amd64 |
| kernel-pkg | 6.12.115+deb13.1-amd64<br>7.3-amd64 |
| cpu | 16 x AMD Ryzen 7 3700X 8-Core Processor |
| memory | 15.8 GiB |
| address | 192.168.60.31 |
| disk | vda 60 GiB |
| rootfs | 59G (16% used) |
| modules | nvme_auth nvme_core nvme_fabrics nvme_keyring nvme_tcp |
| service | tlshd<br>docker |
| nvme | /usr/sbin/nvme: nvme version 3.2 (git v3.2-53-g43b0ee2)<br>/root/work/nvme-cli/.build/nvme: nvme version 3.2 (git v3.2-53-g43b0ee2) |
| etc-nvme | hostid hostnqn nvme-discoverd.conf.sample nvme-fabrics.conf.sample |
| tools | git tlshd nvmf-failover mdadm gcc clang meson docker python3 |
| tlshd | tlshd, built from ktls-utils 1.5.0 on Nov 5 2026 09:59:48 |
| repo | /root/nvme-lab main d3a9a3e 2026-11-05 https://github.com/igaw/nvme-lab.git<br>/root/work/blktests master a65cf6b 2026-10-30 https://github.com/linux-blktests/blktests.git<br>/root/work/ktls-utils HEAD d0b4de1 2026-09-23 https://github.com/oracle/ktls-utils.git<br>/root/work/mdadm HEAD 8b414a0d 2026-03-16 https://git.kernel.org/pub/scm/utils/mdadm/mdadm.git<br>/root/work/nvme-cli master 43b0ee22d 2026-11-05 https://github.com/linux-nvme/nvme-cli.git<br>/root/work/nvmf-failover master db144f1 2026-10-28 https://github.com/igaw/nvmf-failover.git |
| image | ghcr.io/linux-nvme/debian:latest<br>python:3.11-slim<br>koalaman/shellcheck:stable |

## nvmet

| | |
|---|---|
| host | nvmet |
| os | Debian GNU/Linux 13 (trixie) |
| kernel | 7.3-amd64 |
| kernel-pkg | 6.12.115+deb13.1-amd64<br>7.3-amd64 |
| cpu | 4 x AMD Ryzen 7 3700X 8-Core Processor |
| memory | 7.8 GiB |
| address | 192.168.60.30 |
| disk | vda 20 GiB<br>vdb 2 GiB<br>vdc 2 GiB |
| rootfs | 20G (12% used) |
| modules | nvme_auth nvme_core nvme_keyring nvmet nvmet_tcp |
| service | tlshd |
| nvme | /usr/sbin/nvme: nvme version 3.2 (git v3.2-53-g43b0ee2)<br>/root/work/nvme-cli/.build/nvme: nvme version 3.2 (git v3.2-53-g43b0ee2) |
| etc-nvme | nvme-discoverd.conf.sample nvme-fabrics.conf.sample |
| tools | git tlshd mdadm gcc meson python3 |
| tlshd | tlshd, built from ktls-utils 1.5.0 on Nov 5 2026 09:52:18 |
| repo | /root/nvme-lab main d3a9a3e 2026-11-05 https://github.com/igaw/nvme-lab.git<br>/root/work/ktls-utils HEAD d0b4de1 2026-09-23 https://github.com/oracle/ktls-utils.git<br>/root/work/mdadm HEAD 8b414a0d 2026-03-16 https://git.kernel.org/pub/scm/utils/mdadm/mdadm.git<br>/root/work/nvme-cli master 43b0ee22d 2026-11-05 https://github.com/linux-nvme/nvme-cli.git |

## nvme-bsd

| | |
|---|---|
| host | nvme-bsd |
| os | FreeBSD 15.1-RELEASE-p6 |
| kernel | 15.1-RELEASE-p6 |
| cpu | 16 x AMD Ryzen 7 3700X 8-Core Processor |
| memory | 16.0 GiB |
| address | 192.168.60.32 |
| disk | vtbd0 30 GiB<br>nda0 1 GiB QEMU NVMe Ctrl |
| rootfs | 28G (14% used) |
| packages | 553 |
| nvme | /root/work/nvme-cli/.build/nvme: nvme version 3.2 (git v3.2-53-g43b0ee2) |
| tools | git clang meson python3 |
| repo | /root/nvme-lab main 031876e 2026-11-05 https://github.com/igaw/nvme-lab<br>/root/work/nvme-cli master 43b0ee22d 2026-11-05 https://github.com/linux-nvme/nvme-cli.git |

