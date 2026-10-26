"""Roadmap Round 29.5: a stronger LTE cell cancelled under a weaker one (``iqlte_phy.known_cell``,
``pbch``, ``sib1_dcis`` and ``sib1`` with ``known=``) and ``iq-lte``'s cells read strongest first.

Synthetic only, with made-up values (PCIs 123 and 321, random transport blocks). The real-air
checks (the 700 MHz pair's weaker cell: SIB1 alone in every copy once the stronger is cancelled,
none before; the other 700 MHz carrier's second cell, 9 dB down: its MIB, DCIs and SIB1 only with
the stronger cancelled) are in the ops folder, not here. What the checks pin:

- two sectors of one site, frames aligned, 2 ports each, SIB1 on the same 2 PRBs in the same
  subframes, the weaker 6 dB down: without cancellation the weaker cell's SIB1 copies mostly fail
  alone; with the stronger cell known (its CRS, PSS, SSS, PBCH, PCFICH, SIB1 DCI and SIB1 PDSCH
  rebuilt and fitted) every copy decodes alone, byte-equal to what it sent; the stronger cell's own
  decode is the same either way;
- a known cell on another carrier is not taken out, and ``cancel=False`` reads the grid as it is
  (one of another timing on the same carrier is: ``test_iqlte_timing.py``);
- ``tools/iq-lte FILE --pdsch`` reads the cells strongest first, each with those before it known.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_cancel.py   (about 10 s)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_synth  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

STRONG, WEAK = 123, 321
SFN0 = 516
MIB = dict(dl_bandwidth=3, phich_duration=0, phich_resource=2, sib1_br=0)     # 50 RB, PHICH 1
RIV = 50 * 1 + 10                                                             # 2 PRBs from 10
WEAK_DB = -6.0
SNR_DB = 12.0
RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ''), flush=True)


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:                                 # a crash is a failure, with its reason
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def sib1_dci(sfn):
    rv = (0, 2, 3, 1)[(sfn // 2) % 4]
    a = L.dci_1a_si(50, RIV, 6, rv, 3, 0)
    return a, L.parse_dci_1a(a, 50), rv


def cell_res(pci, tb, ports=2):
    """A cell's MIB in every frame and, in subframe 5 of even frames, its PCFICH (CFI cycling),
    its SIB1 DCI (AL4, CCE 4) and its SIB1 ``tb``."""
    res_k = L.pbch_res(pci)

    def res(f):
        sfn = (SFN0 + f) % 1024
        y = L.encode_pbch(dict(MIB, sfn=sfn - sfn % 4), pci, ports)[:, sfn % 4]
        out = [(p, 7 + l, 300 - 36 + res_k[res_k[:, 0] == l, 1], y[p, res_k[:, 0] == l])
               for p in range(ports) for l in range(4)]
        if sfn % 2 == 0:
            cfi = 1 + (sfn // 2) % 3
            a, fields, rv = sib1_dci(sfn)
            for p, l, ks, vals in L.control_res(pci, 50, ports, cfi, '1', 'normal', 5, [(a, 4, 4)]):
                out.append((p, 70 + l, ks, vals))
            for p, ls, ks, vals in L.pdsch_res_values(tb, pci, 50, ports, cfi, 5, L.si_allocation(fields, 50), rv):
                for l in np.unique(ls):
                    out.append((p, 70 + int(l), ks[ls == l], vals[ls == l]))
        return out
    return res


def pair_iq(tbs, weak_db=WEAK_DB, n_frames=18):
    gains = [[1.0, 0.7 * np.exp(1.1j)], [0.9 * np.exp(0.5j), 0.6 * np.exp(-2.0j)]]
    cells = [dict(pci=pci, power_db=db, delay_samples=0, ports=2, port_gains=g, res=cell_res(pci, tb))
             for pci, db, g, tb in ((STRONG, 0.0, gains[0], tbs[0]), (WEAK, weak_db, gains[1], tbs[1]))]
    return lte_synth.synth_lte_multi(cells, bw_mhz=10, n_frames=n_frames, snr_db=SNR_DB, seed=4, cfo_hz=250.0,
                                     idle=True)


def decode(iq, fs, cell, known=None):
    pb = L.pbch(iq, fs, cell, carrier_hz=700e6, known=known)
    if not pb.get('mib'):
        return pb, None, None
    d = L.sib1_dcis(iq, fs, cell, pb, carrier_hz=700e6, known=known)
    return pb, d, L.sib1(iq, fs, cell, pb, d, carrier_hz=700e6, known=known)


def check_pair():
    rng = np.random.default_rng(21)
    tbs = [rng.integers(0, 2, 256).astype(np.uint8) for _ in range(2)]
    hexes = [np.packbits(t).tobytes().hex() for t in tbs]
    iq, fs = pair_iq(tbs)
    cells = {c['pci']: c for c in iqlte.cells(iq, fs)}
    state = {}

    def found():
        return (STRONG in cells and WEAK in cells and cells[STRONG]['frame_offset_samples']
                == cells[WEAK]['frame_offset_samples']), sorted((p, round(c['relative_power_db'], 1))
                                                               for p, c in cells.items())
    expect("both sectors found, frames aligned", found)

    def strong():
        pb, d, s = decode(iq, fs, cells[STRONG])
        state['strong'] = (pb, d, s)
        return (s is not None and s['decoded'] == s['tried'] >= 8 and s['sib1'] == hexes[0]), \
            (s and (s['decoded'], s['tried']))
    expect("the stronger cell's SIB1 alone in every copy", strong)

    def without():
        pb, d, s = decode(iq, fs, cells[WEAK])
        state['without'] = s
        return (s is None or s['decoded'] <= s['tried'] // 2), s and (s['decoded'], s['tried'])
    expect("the weaker cell's SIB1 without cancellation: most copies fail alone", without)

    def cancelled():
        pb0, d0, s0 = state['strong']
        kc = L.known_cell(fs, cells[STRONG], pb0, d0, s0, carrier_hz=700e6)
        state['known'] = kc
        pb, d, s = decode(iq, fs, cells[WEAK], known=[kc])
        hexes_got = {b['hex'] for b in s['blocks'] if b.get('hex')} if s else set()
        return (pb.get('mib') and pb['ports'] == 2 and d and len(d['dcis']) == d['subframes'] >= 8
                and s['decoded'] == s['tried'] and hexes_got == {hexes[1]}), \
            (pb.get('decoded'), d and (len(d['dcis']), d['subframes']), s and (s['decoded'], s['tried']))
    expect("with the stronger cell known: the weaker cell's MIB, every DCI and every SIB1 copy alone, its own bytes",
           cancelled)

    def strong_same():
        kc = L.known_cell(fs, cells[WEAK], *decode(iq, fs, cells[WEAK], known=[state['known']]),
                          carrier_hz=700e6)
        pb, d, s = decode(iq, fs, cells[STRONG], known=[kc])
        return s['decoded'] == s['tried'] and s['sib1'] == hexes[0], (s['decoded'], s['tried'])
    expect("the stronger cell read with the weaker one known: unchanged", strong_same)

    def other_carrier():
        pb0, d0, s0 = state['strong']
        far = dict(cells[STRONG], frame_offset_samples=cells[STRONG]['frame_offset_samples'] + 400)
        kc = L.known_cell(fs, far, pb0, d0, s0, center_offset_hz=1e6, carrier_hz=700e6)
        reader = L._cell_frames(fs, cells[WEAK], pb0, 0.0, 700e6, [kc])
        g_plain = L._cell_frames(fs, cells[WEAK], pb0, 0.0, 700e6).subframe(iq, 2, 5, 50)
        g_raw = L._cell_frames(fs, cells[WEAK], pb0, 0.0, 700e6, [state['known']]).subframe(iq, 2, 5, 50, cancel=False)
        return np.array_equal(reader.subframe(iq, 2, 5, 50), g_plain) and np.array_equal(g_raw, g_plain), ''
    expect("a known cell on another carrier is not taken out; subframe(cancel=False) reads the samples as they are",
           other_carrier)
    return iq, fs, hexes


def check_cli(iq, fs, hexes):
    def cli():
        folder = tempfile.mkdtemp(prefix='iqlte-cancel-')
        try:
            base = os.path.join(folder, 'lte')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                           'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
            env = dict(os.environ, FMRX_LTE_TOOLS=folder)              # no pycrate: bytes only
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile',
                                '--pdsch', '--t1', '0.18'], capture_output=True, text=True, timeout=600, env=env)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            got = {c.get('pci'): c for c in out.get('cells', [])}
            sib = {p: (got.get(p, {}).get('own_sib1') or {}) for p in (STRONG, WEAK)}
            return (r.returncode == 0 and sib[STRONG].get('sib1') == hexes[0] and sib[WEAK].get('sib1') == hexes[1]
                    and got[WEAK].get('own_known') == [STRONG] and not got[STRONG].get('own_known')), \
                (r.stderr[-300:], {p: (s.get('decoded'), s.get('tried')) for p, s in sib.items()})
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-lte FILE --pdsch: cells strongest first, the weaker read with the stronger known (own_known)", cli)


def main():
    iq, fs, hexes = check_pair()
    check_cli(iq, fs, hexes)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} ok, {len(failed)} FAIL of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
