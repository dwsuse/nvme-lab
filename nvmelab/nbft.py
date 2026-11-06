"""Write an NVMe Boot Firmware Table (NBFT) for a QEMU guest.

The NBFT is the ACPI table in which the pre-OS driver hands its NVMe-oF
boot connections to the OS (NVM Express Boot Specification, revision 1.4:
Figure 9 ff.). QEMU passes any table to the guest with '-acpitable
file=FILE', so the OS side of an NBFT boot (dracut's nvmf module, 'nvme
connect-all --nbft', nm-initrd-generator) can be tested without firmware
that boots from NVMe/TCP.

The table holds what libnvme reads: the host descriptor, one TCP HFI
descriptor (with its transport info) per interface, one SSNS descriptor
per subsystem namespace and optional discovery descriptors. There are no
security profiles and no extended info descriptors. Layout as the tables
of real firmware: header, control, host, the descriptor lists, the heap.

The host ID is stored byte for byte as the UUID reads, which is how
libnvme turns it into the host ID of its connections.
"""

import ipaddress
import struct
import uuid
from dataclasses import dataclass, field

# Descriptor types (Figure 6) and transport type (Figure 8)
DESC_CONTROL, DESC_HOST, DESC_HFI, DESC_SSNS = 1, 2, 3, 4
DESC_DISCOVERY, DESC_HFI_TRINFO = 6, 7
TRTYPE_TCP = 3

DISCOVERY_NQN = 'nqn.2014-08.org.nvmexpress.discovery'

# struct.pack formats, little endian; a heap object reference is 'IH'
HEADER = '<4sIBB6s8sIIIIIIHB13s'
CONTROL = '<BBBBHBBIHBBIHBBIHBBIHBBIHBB16s'
HOST = '<BB16sIH8s'
HFI = '<BBBB12sIH10s'
HFI_TCP = '<BBBBHBI6sHB16sB16sBH16s16s16sIHIHB11s'
SSNS = '<BHHBHBBIHIHHIB16sBBBIHIHIH62s'
DISCOVERY = '<BBBBBBIHIH14s'
SIZES = {HEADER: 64, CONTROL: 64, HOST: 32, HFI: 32, HFI_TCP: 128,
         SSNS: 128, DISCOVERY: 32}

# SSNS flags (Figure 18) by the names 'lab boot run --nbft-ssns' takes
SSNS_VALID = 1 << 0
SSNS_FLAGS = {
    'nonboot': 1 << 1,          # Non-bootable Entry Flag
    'separate-dc': 1 << 5,      # Separate Discovery Controller Flag
    'discovered': 1 << 6,       # Discovered Namespace Flag
    'available': 0x0080,        # Unavailable Namespace Flag: available
    'unavailable': 0x0100,      # Unavailable Namespace Flag: unavailable
}
# Transport specific flags (Figure 19)
TRFLAG_VALID, TRFLAG_HDGST, TRFLAG_DDGST = 1 << 0, 1 << 1, 1 << 2
# HFI transport info flags (Figure 14)
HFI_TCP_VALID, HFI_TCP_GLOBAL_ROUTE, HFI_TCP_DHCP_OVERRIDE = 1, 2, 4
# IP Origin: NL_PREFIX_ORIGIN of the Win32 API
ORIGIN_MANUAL, ORIGIN_DHCP = 1, 3


class NbftError(ValueError):
    pass


@dataclass
class Hfi:
    """A host fabric interface: the guest NIC with the given MAC."""
    mac: str
    ipaddr: str
    prefix: int
    gateway: str | None = None
    dhcp_server: str | None = None  # set: the address came from DHCP
    vlan: int = 0
    pci_sbdf: int = 0               # segment<<16 | bus<<8 | dev<<3 | fn
    dns: tuple = ()
    host_name: str | None = None
    default_route: bool = True


@dataclass
class Ssns:
    """A subsystem namespace the pre-OS driver connected to."""
    traddr: str
    trsvcid: str
    nqn: str
    nsid: int = 1
    hfi: int = 1                    # index of the primary HFI, from 1
    secondary_hfis: tuple = ()
    flags: tuple = ()               # names of SSNS_FLAGS
    discovery: int = 0              # index of the discovery descriptor
    nid_type: int = 0               # 1: EUI-64, 2: NGUID, 3: UUID
    nid: bytes = b''
    hdgst: bool = False
    ddgst: bool = False


@dataclass
class Discovery:
    """A discovery controller, as an nvme+tcp:// URI."""
    uri: str
    nqn: str = DISCOVERY_NQN
    hfi: int = 1


@dataclass
class Table:
    host_nqn: str
    host_id: str
    hfis: list = field(default_factory=list)
    ssns: list = field(default_factory=list)
    discovery: list = field(default_factory=list)


def pack(fmt, *values):
    data = struct.pack(fmt, *values)
    assert len(data) == SIZES[fmt], fmt
    return data


def ip16(addr):
    """ADDR as the 16 bytes of the NBFT: IPv6, or IPv4 mapped into IPv6.
    No address is the mapped 0.0.0.0, as firmware writes it."""
    if not addr:
        addr = '0.0.0.0'
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        raise NbftError(f'bad IP address: {addr}') from None
    if ip.version == 4:
        return b'\0' * 10 + b'\xff\xff' + ip.packed
    return ip.packed


def mac6(mac):
    try:
        data = bytes(int(x, 16) for x in mac.split(':'))
    except ValueError:
        data = b''
    if len(data) != 6:
        raise NbftError(f'bad MAC address: {mac}')
    return data


def ssns_flags(names):
    flags = SSNS_VALID
    for name in names:
        if name not in SSNS_FLAGS:
            raise NbftError(f"bad SSNS flag '{name}': "
                            f"{', '.join(SSNS_FLAGS)}")
        flags |= SSNS_FLAGS[name]
    if 'available' in names and 'unavailable' in names:
        raise NbftError('available and unavailable exclude each other')
    return flags


class Heap:
    """The heap at the end of the table; objects are referenced by their
    offset from the start of the table."""

    def __init__(self, offset):
        self.offset = offset
        self.data = bytearray()

    def add(self, data):
        ref = (self.offset + len(self.data), len(data))
        self.data += data
        return ref

    def add_str(self, text):
        """A string: its length does not count the NUL that ends it."""
        if text is None:
            return (0, 0)
        offset, _ = self.add(text.encode() + b'\0')
        return (offset, len(text.encode()))


def build(table, oem_id='NVMLAB', oem_table_id='NBFT'):
    """The NBFT for TABLE, as bytes."""
    if not table.hfis:
        raise NbftError('an NBFT needs an HFI')
    if not 1 <= len(table.hfis) <= 255 or len(table.ssns) > 255 \
            or len(table.discovery) > 255:
        raise NbftError('at most 255 descriptors of a kind')
    for s in table.ssns:
        for i in (s.hfi, *s.secondary_hfis):
            if not 1 <= i <= len(table.hfis):
                raise NbftError(f'SSNS {s.nqn}: no HFI {i}')
        if s.discovery and not 1 <= s.discovery <= len(table.discovery):
            raise NbftError(f'SSNS {s.nqn}: no discovery descriptor '
                            f'{s.discovery}')
    for d in table.discovery:
        if not 1 <= d.hfi <= len(table.hfis):
            raise NbftError(f'discovery {d.uri}: no HFI {d.hfi}')
    try:
        host_id = uuid.UUID(table.host_id).bytes
    except ValueError:
        raise NbftError(f'bad host ID: {table.host_id}') from None

    host_off = 64 + 64
    hfi_off = host_off + 32
    ssns_off = hfi_off + 32 * len(table.hfis)
    disc_off = ssns_off + 128 * len(table.ssns)
    heap = Heap(disc_off + 32 * len(table.discovery))

    host = pack(HOST, DESC_HOST, 0x7, host_id,  # valid, ID and NQN set
                *heap.add_str(table.host_nqn), b'')

    hfis = b''
    for i, h in enumerate(table.hfis, 1):
        dns = [*h.dns, None, None][:2]
        flags = HFI_TCP_VALID
        if h.default_route:
            flags |= HFI_TCP_GLOBAL_ROUTE
        if h.dhcp_server:
            flags |= HFI_TCP_DHCP_OVERRIDE
        trinfo = pack(HFI_TCP, DESC_HFI_TRINFO, 1, TRTYPE_TCP, 1, i, flags,
                      h.pci_sbdf, mac6(h.mac), h.vlan,
                      ORIGIN_DHCP if h.dhcp_server else ORIGIN_MANUAL,
                      ip16(h.ipaddr), h.prefix, ip16(h.gateway), 0, 0,
                      ip16(dns[0]), ip16(dns[1]), ip16(h.dhcp_server),
                      *heap.add_str(h.host_name), 0, 0, 0, b'')
        hfis += pack(HFI, DESC_HFI, i, 1, TRTYPE_TCP, b'',
                     *heap.add(trinfo), b'')

    ssns = b''
    for i, s in enumerate(table.ssns, 1):
        trflags = TRFLAG_VALID
        if s.hdgst:
            trflags |= TRFLAG_HDGST
        if s.ddgst:
            trflags |= TRFLAG_DDGST
        if len(s.nid) > 16:
            raise NbftError(f'SSNS {s.nqn}: NID longer than 16 bytes')
        # The secondary HFI list of firmware starts with the primary HFI
        # (one byte for a single HFI): libnvme 1 counts every byte as an
        # HFI and crashes on an index that names none.
        secondary = bytes(dict.fromkeys((s.hfi, *s.secondary_hfis)))
        ssns += pack(SSNS, DESC_SSNS, i, ssns_flags(s.flags), TRTYPE_TCP,
                     trflags, s.discovery, 0, *heap.add(ip16(s.traddr)),
                     *heap.add_str(str(s.trsvcid)), 0, s.nsid, s.nid_type,
                     s.nid.ljust(16, b'\0'), 0, s.hfi, 0,
                     *heap.add(secondary), *heap.add_str(s.nqn), 0, 0, b'')

    disc = b''
    for i, d in enumerate(table.discovery, 1):
        disc += pack(DISCOVERY, DESC_DISCOVERY, 1, i, d.hfi, 0, 0,
                     *heap.add_str(d.uri), *heap.add_str(d.nqn), b'')

    def counted(off, size, items):
        return (off, size, 1, len(items)) if items else (0, 0, 0, 0)

    control = pack(CONTROL, DESC_CONTROL, 1, 0, 0, 64, 1, 0,
                   host_off, 32, 1, 0,
                   *counted(hfi_off, 32, table.hfis),
                   *counted(ssns_off, 128, table.ssns),
                   0, 0, 0, 0,
                   *counted(disc_off, 32, table.discovery), b'')

    length = heap.offset + len(heap.data)
    header = pack(HEADER, b'NBFT', length, 1, 0,
                  oem_id.encode().ljust(6)[:6],
                  oem_table_id.encode().ljust(8)[:8], 1,
                  int.from_bytes(b'LAB ', 'little'), 1,
                  heap.offset, len(heap.data), 0, 0, 0, b'')
    data = bytearray(header + control + host + hfis + ssns + disc
                     + heap.data)
    data[9] = (-sum(data)) & 0xff
    return bytes(data)


def parse_ssns_spec(spec, traddr):
    """'lab boot run --nbft-ssns': [TRADDR:]TRSVCID/NQN[,FLAG...]."""
    try:
        addr, rest = spec.split('/', 1)
    except ValueError:
        raise NbftError(f"bad --nbft-ssns '{spec}': "
                        '[TRADDR:]TRSVCID/NQN[,FLAG...]') from None
    nqn, *flags = rest.split(',')
    if ':' in addr:
        traddr, trsvcid = addr.rsplit(':', 1)
    else:
        trsvcid = addr
    if not trsvcid.isdigit() or not nqn:
        raise NbftError(f"bad --nbft-ssns '{spec}': "
                        '[TRADDR:]TRSVCID/NQN[,FLAG...]')
    ssns_flags(flags)
    return Ssns(traddr=traddr, trsvcid=trsvcid, nqn=nqn, flags=tuple(flags))
