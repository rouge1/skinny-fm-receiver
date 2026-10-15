"""The scrambler and CRC catalogs (``fm_receiver/iqcatalog.py``) against ``knowledge/catalogs.md``.

What the checks pin:

- every CRC in the catalog reproduces its published check value (the CRC of the ASCII bytes
  ``123456789``), the catalog is the 71 width 8/16/24/32 entries of the reveng catalogue, and
  no two entries are the same algorithm;
- ``knowledge/catalogs.md`` and the module agree entry by entry (name, width, polynomial,
  init, reflections, xorout, check; scrambler mask, shift, seeds, first bytes), so one cannot
  change without the other;
- the PN9 scrambler produces the sequence Texas Instruments' DN509 prints
  (FF E1 1D 9A) and the twelve bytes after it that this module generates, and its period is 511;
- the Silver Spring scrambler equals an independent restatement of rtl_433's
  ``silver_spring_mesh_descramble`` for all 255 seeds, has period 255, and descrambling twice
  returns the input;
- the register arithmetic is linear in the data (the property the frame search relies on).

Run:  PYTHONPATH=tools python tools/tests/test_iq_catalog.py
"""

import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from fm_receiver import iqcatalog as cat  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"))


def run(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    check(name, ok, detail)


def ss_reference(buf, seed):
    """rtl_433 silver_spring_mesh_descramble, restated: tap 0x8e, output bit 7, MSB first."""
    reg, out = seed, bytearray(buf)
    for i in range(len(out)):
        k = 0
        for _ in range(8):
            k = ((k << 1) | ((reg >> 7) & 1)) & 0xFF
            t = reg & 0x8E
            t ^= t >> 4
            t ^= t >> 2
            t ^= t >> 1
            reg = ((reg << 1) | (t & 1)) & 0xFF
        out[i] ^= k
    return bytes(out)


def doc_blocks():
    text = open(os.path.join(ROOT, 'knowledge', 'catalogs.md')).read()
    blocks = re.findall(r'```json\n(.*?)\n```', text, re.S)
    return text, [json.loads(b) for b in blocks]


def t_check_values():
    bad = [s.name for s in cat.CRCS if cat.crc(b'123456789', s) != s.check]
    return not bad, f'wrong check value: {bad}'


def t_catalog_shape():
    widths = sorted({s.width for s in cat.CRCS})
    algos = {(s.width, s.poly, s.init, s.refin, s.refout, s.xorout) for s in cat.CRCS}
    names = {s.name for s in cat.CRCS}
    ok = (len(cat.CRCS) == 71 and widths == [8, 16, 24, 32] and len(algos) == 71 and len(names) == 71)
    return ok, f'{len(cat.CRCS)} entries, widths {widths}, {len(algos)} algorithms, {len(names)} names'


def t_doc_crcs():
    _, blocks = doc_blocks()
    rows = blocks[0]
    want = [dict(name=s.name, width=s.width, poly='0x%X' % s.poly, init='0x%X' % s.init, refin=s.refin,
                 refout=s.refout, xorout='0x%X' % s.xorout, check='0x%X' % s.check) for s in cat.CRCS]
    return rows == want, 'knowledge/catalogs.md CRC block differs from iqcatalog.CRCS'


def t_doc_scramblers():
    _, blocks = doc_blocks()
    rows = blocks[1]
    want = [dict(name=s.name, width=s.width, mask='0x%X' % s.mask, shift=s.shift,
                 seeds=('0x%X' % s.seeds[0] if len(s.seeds) == 1 else 'all non-zero (%d)' % len(s.seeds)),
                 first_bytes=cat.keystream(s, s.seeds[0], 8).hex(), note=s.note) for s in cat.SCRAMBLERS]
    return rows == want, 'knowledge/catalogs.md scrambler block differs from iqcatalog.SCRAMBLERS'


def t_doc_mentions():
    text, _ = doc_blocks()
    need = ['swra322', '1111 1111 / 1110 0001 / 0001 1101 / 1001 1010', 'reveng.sourceforge.io',
            'silver_spring_mesh.c']
    miss = [n for n in need if n not in text]
    return not miss, f'missing from the document: {miss}'


def t_pn9():
    pn9 = [s for s in cat.SCRAMBLERS if s.name == 'pn9'][0]
    ks = cat.keystream(pn9, 0x1FF, 16)
    ti = bytes.fromhex('ffe11d9a')
    own = bytes.fromhex('ed853324ea7ad2397097570a')
    if ks[:4] != ti:
        return False, f'first four bytes {ks[:4].hex()}, DN509 prints ffe11d9a'
    if ks[4:] != own:
        return False, f'bytes 4..15 {ks[4:].hex()} (regression value {own.hex()})'
    long = cat.keystream(pn9, 0x1FF, 200)
    bits = ''.join(format(b, '08b')[::-1] for b in long)       # LSB first = emission order
    period = next((p for p in range(1, 600) if bits[:900 - p] == bits[p:900]), None)
    return period == 511, f'period {period}'


def t_ss():
    ss = [s for s in cat.SCRAMBLERS if s.name == 'ss-lfsr8'][0]
    z = bytes(40)
    bad = [sd for sd in ss.seeds if cat.descramble(z, ss, sd) != ss_reference(z, sd)]
    if bad:
        return False, f'differs from the reference for seeds {bad[:5]}'
    if len(ss.seeds) != 255 or 0 in ss.seeds:
        return False, f'{len(ss.seeds)} seeds'
    ks = cat.keystream(ss, 1, 600)
    bits = ''.join(format(b, '08b') for b in ks)
    period = next((p for p in range(1, 1000) if bits[:2000 - p] == bits[p:2000]), None)
    twice = cat.descramble(cat.descramble(b'\x01\x02\x03\xFF' * 5, ss, 77), ss, 77)
    return period == 255 and twice == b'\x01\x02\x03\xFF' * 5, f'period {period}'


def t_linear():
    rng = np.random.default_rng(3)
    bad = []
    for spec in cat.CRCS:
        a = bytes(rng.integers(0, 256, 23, dtype=np.uint8))
        b = bytes(rng.integers(0, 256, 23, dtype=np.uint8))
        x = bytes(p ^ q for p, q in zip(a, b))
        lhs = cat.crc_update(0, x, spec)
        rhs = cat.crc_update(0, a, spec) ^ cat.crc_update(0, b, spec)
        if lhs != rhs:
            bad.append(spec.name)
    return not bad, f'not linear: {bad}'


def main():
    print('catalog checks')
    for name, fn in [('every CRC reproduces its reveng check value', t_check_values),
                     ('the catalog is the 71 distinct width 8/16/24/32 entries', t_catalog_shape),
                     ('knowledge/catalogs.md lists the same CRCs', t_doc_crcs),
                     ('knowledge/catalogs.md lists the same scramblers', t_doc_scramblers),
                     ('the document cites its sources', t_doc_mentions),
                     ('PN9 gives the DN509 sequence and has period 511', t_pn9),
                     ('ss-lfsr8 equals the rtl_433 function for all seeds, period 255', t_ss),
                     ('a CRC register is linear in the data', t_linear)]:
        run(name, fn)
    failed = [n for n, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed')
    if failed:
        print('failed:', *failed, sep='\n  ')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
