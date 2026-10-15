"""Catalogs of CRCs and scramblers for the frame search (``iq-frames``).

Numpy only. Two tables, both mirrored in ``knowledge/catalogs.md``
(``tools/tests/test_iq_catalog.py`` checks that the document and this module agree,
and that every CRC reproduces its published check value).

CRCs: every width 8, 16, 24 and 32 entry of Greg Cook's catalogue of parametrised
CRC algorithms (https://reveng.sourceforge.io/crc-catalogue/all.htm, fetched
2026-10-15). The check value is the CRC of the ASCII bytes ``123456789``.
Rocksoft model: ``init`` is the register's start value, ``refin`` reflects each
input byte, ``refout`` reflects the register at the end, then XOR ``xorout``.

Scramblers: additive LFSRs, a keystream XORed over the bytes. Two conventions:

- ``left``:  the register shifts left, the output bit is the register's top bit, the
  new bit is the parity of ``reg & mask`` and goes in at bit 0; bits pack MSB first.
- ``right``: the register shifts right, the output bit is bit 0, the new bit is the
  parity of ``reg & mask`` and goes in at the top bit; bits pack LSB first.
"""

from typing import NamedTuple


class CrcSpec(NamedTuple):
    name: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int


_CRC_ROWS = [
    ('CRC-8/AUTOSAR', 8, 0x2F, 0xFF, False, False, 0xFF, 0xDF),
    ('CRC-8/BLUETOOTH', 8, 0xA7, 0x0, True, True, 0x0, 0x26),
    ('CRC-8/CDMA2000', 8, 0x9B, 0xFF, False, False, 0x0, 0xDA),
    ('CRC-8/DARC', 8, 0x39, 0x0, True, True, 0x0, 0x15),
    ('CRC-8/DVB-S2', 8, 0xD5, 0x0, False, False, 0x0, 0xBC),
    ('CRC-8/GSM-A', 8, 0x1D, 0x0, False, False, 0x0, 0x37),
    ('CRC-8/GSM-B', 8, 0x49, 0x0, False, False, 0xFF, 0x94),
    ('CRC-8/HITAG', 8, 0x1D, 0xFF, False, False, 0x0, 0xB4),
    ('CRC-8/I-432-1', 8, 0x7, 0x0, False, False, 0x55, 0xA1),
    ('CRC-8/I-CODE', 8, 0x1D, 0xFD, False, False, 0x0, 0x7E),
    ('CRC-8/LTE', 8, 0x9B, 0x0, False, False, 0x0, 0xEA),
    ('CRC-8/MAXIM-DOW', 8, 0x31, 0x0, True, True, 0x0, 0xA1),
    ('CRC-8/MIFARE-MAD', 8, 0x1D, 0xC7, False, False, 0x0, 0x99),
    ('CRC-8/NRSC-5', 8, 0x31, 0xFF, False, False, 0x0, 0xF7),
    ('CRC-8/OPENSAFETY', 8, 0x2F, 0x0, False, False, 0x0, 0x3E),
    ('CRC-8/ROHC', 8, 0x7, 0xFF, True, True, 0x0, 0xD0),
    ('CRC-8/SAE-J1850', 8, 0x1D, 0xFF, False, False, 0xFF, 0x4B),
    ('CRC-8/SMBUS', 8, 0x7, 0x0, False, False, 0x0, 0xF4),
    ('CRC-8/TECH-3250', 8, 0x1D, 0xFF, True, True, 0x0, 0x97),
    ('CRC-8/WCDMA', 8, 0x9B, 0x0, True, True, 0x0, 0x25),
    ('CRC-16/ARC', 16, 0x8005, 0x0, True, True, 0x0, 0xBB3D),
    ('CRC-16/CDMA2000', 16, 0xC867, 0xFFFF, False, False, 0x0, 0x4C06),
    ('CRC-16/CMS', 16, 0x8005, 0xFFFF, False, False, 0x0, 0xAEE7),
    ('CRC-16/DDS-110', 16, 0x8005, 0x800D, False, False, 0x0, 0x9ECF),
    ('CRC-16/DECT-R', 16, 0x589, 0x0, False, False, 0x1, 0x7E),
    ('CRC-16/DECT-X', 16, 0x589, 0x0, False, False, 0x0, 0x7F),
    ('CRC-16/DNP', 16, 0x3D65, 0x0, True, True, 0xFFFF, 0xEA82),
    ('CRC-16/EN-13757', 16, 0x3D65, 0x0, False, False, 0xFFFF, 0xC2B7),
    ('CRC-16/GENIBUS', 16, 0x1021, 0xFFFF, False, False, 0xFFFF, 0xD64E),
    ('CRC-16/GSM', 16, 0x1021, 0x0, False, False, 0xFFFF, 0xCE3C),
    ('CRC-16/IBM-3740', 16, 0x1021, 0xFFFF, False, False, 0x0, 0x29B1),
    ('CRC-16/IBM-SDLC', 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E),
    ('CRC-16/ISO-IEC-14443-3-A', 16, 0x1021, 0xC6C6, True, True, 0x0, 0xBF05),
    ('CRC-16/KERMIT', 16, 0x1021, 0x0, True, True, 0x0, 0x2189),
    ('CRC-16/LJ1200', 16, 0x6F63, 0x0, False, False, 0x0, 0xBDF4),
    ('CRC-16/M17', 16, 0x5935, 0xFFFF, False, False, 0x0, 0x772B),
    ('CRC-16/MAXIM-DOW', 16, 0x8005, 0x0, True, True, 0xFFFF, 0x44C2),
    ('CRC-16/MCRF4XX', 16, 0x1021, 0xFFFF, True, True, 0x0, 0x6F91),
    ('CRC-16/MODBUS', 16, 0x8005, 0xFFFF, True, True, 0x0, 0x4B37),
    ('CRC-16/NRSC-5', 16, 0x80B, 0xFFFF, True, True, 0x0, 0xA066),
    ('CRC-16/OPENSAFETY-A', 16, 0x5935, 0x0, False, False, 0x0, 0x5D38),
    ('CRC-16/OPENSAFETY-B', 16, 0x755B, 0x0, False, False, 0x0, 0x20FE),
    ('CRC-16/PROFIBUS', 16, 0x1DCF, 0xFFFF, False, False, 0xFFFF, 0xA819),
    ('CRC-16/RIELLO', 16, 0x1021, 0xB2AA, True, True, 0x0, 0x63D0),
    ('CRC-16/SPI-FUJITSU', 16, 0x1021, 0x1D0F, False, False, 0x0, 0xE5CC),
    ('CRC-16/T10-DIF', 16, 0x8BB7, 0x0, False, False, 0x0, 0xD0DB),
    ('CRC-16/TELEDISK', 16, 0xA097, 0x0, False, False, 0x0, 0xFB3),
    ('CRC-16/TMS37157', 16, 0x1021, 0x89EC, True, True, 0x0, 0x26B1),
    ('CRC-16/UMTS', 16, 0x8005, 0x0, False, False, 0x0, 0xFEE8),
    ('CRC-16/USB', 16, 0x8005, 0xFFFF, True, True, 0xFFFF, 0xB4C8),
    ('CRC-16/XMODEM', 16, 0x1021, 0x0, False, False, 0x0, 0x31C3),
    ('CRC-24/BLE', 24, 0x65B, 0x555555, True, True, 0x0, 0xC25A56),
    ('CRC-24/FLEXRAY-A', 24, 0x5D6DCB, 0xFEDCBA, False, False, 0x0, 0x7979BD),
    ('CRC-24/FLEXRAY-B', 24, 0x5D6DCB, 0xABCDEF, False, False, 0x0, 0x1F23B8),
    ('CRC-24/INTERLAKEN', 24, 0x328B63, 0xFFFFFF, False, False, 0xFFFFFF, 0xB4F3E6),
    ('CRC-24/LTE-A', 24, 0x864CFB, 0x0, False, False, 0x0, 0xCDE703),
    ('CRC-24/LTE-B', 24, 0x800063, 0x0, False, False, 0x0, 0x23EF52),
    ('CRC-24/OPENPGP', 24, 0x864CFB, 0xB704CE, False, False, 0x0, 0x21CF02),
    ('CRC-24/OS-9', 24, 0x800063, 0xFFFFFF, False, False, 0xFFFFFF, 0x200FA5),
    ('CRC-32/AIXM', 32, 0x814141AB, 0x0, False, False, 0x0, 0x3010BF7F),
    ('CRC-32/AUTOSAR', 32, 0xF4ACFB13, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0x1697D06A),
    ('CRC-32/BASE91-D', 32, 0xA833982B, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0x87315576),
    ('CRC-32/BZIP2', 32, 0x4C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF, 0xFC891918),
    ('CRC-32/CD-ROM-EDC', 32, 0x8001801B, 0x0, True, True, 0x0, 0x6EC2EDC4),
    ('CRC-32/CKSUM', 32, 0x4C11DB7, 0x0, False, False, 0xFFFFFFFF, 0x765E7680),
    ('CRC-32/ISCSI', 32, 0x1EDC6F41, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xE3069283),
    ('CRC-32/ISO-HDLC', 32, 0x4C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926),
    ('CRC-32/JAMCRC', 32, 0x4C11DB7, 0xFFFFFFFF, True, True, 0x0, 0x340BC6D9),
    ('CRC-32/MEF', 32, 0x741B8CD7, 0xFFFFFFFF, True, True, 0x0, 0xD2C22F51),
    ('CRC-32/MPEG-2', 32, 0x4C11DB7, 0xFFFFFFFF, False, False, 0x0, 0x376E6E7),
    ('CRC-32/XFER', 32, 0xAF, 0x0, False, False, 0x0, 0xBD0BE338),
]

CRCS = [CrcSpec(*row) for row in _CRC_ROWS]


def _reflect(value, width):
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


_REFLECT8 = [_reflect(i, 8) for i in range(256)]
_TABLES = {}


def crc_table(spec):
    """The 256-entry table of the non-reflected algorithm (the byte goes in at the top)."""
    key = (spec.width, spec.poly)
    table = _TABLES.get(key)
    if table is None:
        w, mask, top = spec.width, (1 << spec.width) - 1, 1 << (spec.width - 1)
        table = []
        for byte in range(256):
            reg = byte << (w - 8)
            for _ in range(8):
                reg = ((reg << 1) ^ spec.poly) & mask if reg & top else (reg << 1) & mask
            table.append(reg)
        _TABLES[key] = table
    return table


def crc_update(reg, data, spec):
    """The register after ``data`` (bytes), from register ``reg``; no final step."""
    table, w = crc_table(spec), spec.width
    mask, shift = (1 << w) - 1, w - 8
    if spec.refin:
        for b in data:
            reg = ((reg << 8) & mask) ^ table[((reg >> shift) ^ _REFLECT8[b]) & 0xFF]
    else:
        for b in data:
            reg = ((reg << 8) & mask) ^ table[((reg >> shift) ^ b) & 0xFF]
    return reg


def crc_final(reg, spec):
    """The register as the CRC value: reflect when ``refout``, then XOR ``xorout``."""
    if spec.refout:
        reg = _reflect(reg, spec.width)
    return reg ^ spec.xorout


def crc(data, spec):
    """The CRC of ``data`` (bytes or a bytes-like)."""
    return crc_final(crc_update(spec.init, bytes(data), spec), spec)


def crc_bytes(value, spec, order='big'):
    return int(value).to_bytes(spec.width // 8, order)


class ScramblerSpec(NamedTuple):
    name: str
    width: int          # register bits
    mask: int           # feedback taps over the register
    shift: str          # 'left' or 'right'
    seeds: tuple        # candidate seeds: one for a fixed seed, all non-zero for a per-frame one
    note: str


def _all_seeds(width):
    return tuple(range(1, 1 << width))


SCRAMBLERS = [
    # x^9+x^5+1, all-ones seed, LSB-first. The whitener of the CC1101 family and of IEEE
    # 802.15.4g data whitening. First bytes ff e1 1d 9a ed 85 33 24 (checked in the tests).
    ScramblerSpec('pn9', 9, 0x021, 'right', (0x1FF,), 'x^9+x^5+1, seed all ones, LSB first'),
    # x^8+x^4+x^3+x^2+1 (tap mask 0x8E), MSB-first, seed per hop, in rtl_433's
    # silver_spring_mesh.c, where a CRC-32/MPEG-2 verifies it.
    ScramblerSpec('ss-lfsr8', 8, 0x8E, 'left', _all_seeds(8),
                  'x^8+x^4+x^3+x^2+1, MSB first, seed per frame'),
]


def keystream(spec, seed, nbytes):
    """``nbytes`` of keystream as a bytes object."""
    reg, n = seed, spec.width
    full = (1 << n) - 1
    out = bytearray()
    for _ in range(nbytes):
        k = 0
        for i in range(8):
            if spec.shift == 'left':
                bit = (reg >> (n - 1)) & 1
                k = (k << 1) | bit
                new = bin(reg & spec.mask).count('1') & 1
                reg = ((reg << 1) | new) & full
            else:
                bit = reg & 1
                k |= bit << i
                new = bin(reg & spec.mask).count('1') & 1
                reg = (reg >> 1) | (new << (n - 1))
        out.append(k)
    return bytes(out)


def descramble(data, spec, seed):
    ks = keystream(spec, seed, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))
