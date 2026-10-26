"""Roadmap Round 29.2: our own LTE PCFICH and PDCCH decoder for SIB1's DCI
(``iqlte_phy.regs``, ``pcfich_starts``, ``phich_starts``, ``pdcch_quads``, ``decode_pcfich``,
``decode_dci``, ``sib1_dcis``) and ``iq-lte --pdcch``.

Synthetic only, with made-up values (PCI 123, random fields). The real-air checks (the Band 13
cell's 50 SIB1 DCIs equal to the outside ``pdsch_ue``'s: CFI 3, format 1A, AL4, CCE 0, its
allocation and MCS; the 700 MHz cells' SIB1 DCIs in every subframe) are in the ops folder, not
here. What the checks pin:

- DCI sizes (36.212 5.3.3.1.3-4): 1A 21/22/25/27/27/28 bits and 1C 8/9/11/13/14/15 for 6, 15, 25,
  50, 75 and 100 RB (1A grown to format 0's size and past the ambiguous sizes; 1A 27 and 1C 13
  at 50 RB as ``pdsch_ue`` prints);
- the control region at 50 RB with 4 ports: 350 REGs in three symbols; with PHICH 1, 75, 175 and
  325 left for CFI 1, 2, 3 (8, 19 and 36 CCEs, as ``pdsch_ue`` counts); the PCFICH's 4 and the
  PHICH's 21 REGs apart; the PDCCH's quadruplets one to one onto the REGs left;
- a DCI's CRC masked by the RNTI: decoded with SI-RNTI, refused with another;
- the PCFICH's CFI (1, 2, 3) through noise on 1, 2 and 4 ports;
- end to end: a synthetic 10 MHz cell (2 and 4 ports, a real PBCH) whose subframe 5 of even
  frames carries a PCFICH (CFI cycling 1, 2, 3) and a SIB1 DCI 1A at AL4 on CCE 4 (RV 0, 2, 3, 1)
  or at AL8 on CCE 0: ``sib1_dcis`` finds every one, its CFI, level, CCE and fields;
- ``tools/iq-lte FILE --pdcch`` adds ``own_pdcch``.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_pdcch.py   (about 30 s)
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

PCI = 123
SFN0 = 516
MIB = dict(dl_bandwidth=3, phich_duration=0, phich_resource=2, sib1_br=0)     # 50 RB, PHICH 1
RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"))


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


def check_pieces():
    rng = np.random.default_rng(8)

    def sizes():
        a = {n: L.dci_1a_bits(n) for n in (6, 15, 25, 50, 75, 100)}
        c = {n: L.dci_1c_bits(n) for n in (6, 15, 25, 50, 75, 100)}
        return (list(a.values()) == [21, 22, 25, 27, 27, 28] and list(c.values()) == [8, 9, 11, 13, 14, 15]), (a, c)
    expect("DCI sizes: 1A 21..28 and 1C 8..15 bits for 6..100 RB", sizes)

    def region():
        total = len(L.cell_regs(PCI, 50, 4, 3))
        counts = [len(L.pdcch_quads(PCI, 50, 4, cfi, '1')) for cfi in (1, 2, 3)]
        pc = {(0, k) for k in L.pcfich_starts(PCI, 50)}
        ph = L.phich_starts(PCI, 50, 4, '1')
        q = L.pdcch_quads(PCI, 50, 4, 3, '1')
        starts = {(l, ks[0]) for l, ks in q}
        all_starts = {(l, ks[0]) for l, _s, ks in L.cell_regs(PCI, 50, 4, 3)}
        ok = (total == 350 and counts == [75, 175, 325] and [c // 9 for c in counts] == [8, 19, 36]
              and len(pc) == 4 and len(set(ph)) == 21 and not (pc & set(ph)) and len(starts) == len(q)
              and starts == {(l, min(ks)) for l, _s, ks in L.cell_regs(PCI, 50, 4, 3)
                             if (l, _s) not in pc | set(ph)} and len(all_starts) == 350)
        return ok, (total, counts, len(ph), len(starts))
    expect("control region at 50 RB, 4 ports: 350 REGs; 75/175/325 left (8/19/36 CCEs); PCFICH, PHICH apart",
           region)

    def rnti():
        a = rng.integers(0, 2, 27).astype(np.uint8)
        e = L.encode_dci(a, L.SI_RNTI, 4)
        soft = (1 - 2.0 * e) * 3 + rng.standard_normal(len(e))
        got = L.decode_dci(soft, 27, L.SI_RNTI)
        other = L.decode_dci(soft, 27, 0x1234)
        return got is not None and np.array_equal(got, a) and other is None, (got is not None, other is not None)
    expect("a DCI's CRC masked by the RNTI: decoded with SI-RNTI, refused with another", rnti)

    def pcfich():
        bad = []
        for ports in (1, 2, 4):
            for cfi in (1, 2, 3):
                res = L.control_res(PCI, 50, ports, cfi, '1', 'normal', 5, [])
                h = (rng.standard_normal((ports, 1)) + 1j * rng.standard_normal((ports, 1))) * np.ones((ports, 16))
                r = sum(h[p] * vals for p, l, ks, vals in res if l == 0 and len(ks) == 16)
                r = r + (rng.standard_normal(16) + 1j * rng.standard_normal(16)) * 0.3
                hh = np.zeros((4, 16), dtype=complex)
                hh[:ports] = h
                got, _score = L.decode_pcfich(r, hh, 0.18, PCI, 5, ports)
                if got != cfi:
                    bad.append((ports, cfi, got))
        return not bad, bad
    expect("the PCFICH's CFI (1, 2, 3) through noise, on 1, 2 and 4 ports", pcfich)


def cell_iq(ports, level=4, cce=4, snr_db=3.0, n_frames=12):
    res_k = L.pbch_res(PCI)
    n_sc = 600
    sent = []

    def res(f):
        sfn = (SFN0 + f) % 1024
        y = L.encode_pbch(dict(MIB, sfn=sfn - sfn % 4), PCI, ports)[:, sfn % 4]
        out = [(p, 7 + l, n_sc // 2 - 36 + res_k[res_k[:, 0] == l, 1], y[p, res_k[:, 0] == l])
               for p in range(ports) for l in range(4)]
        if sfn % 2 == 0:
            cfi = 1 + (sfn // 2) % 3
            k = (sfn // 2) % 4
            rv = (0, 2, 3, 1)[k]
            a = L.dci_1a_si(50, 200, 8, rv)
            for p, l, ks, vals in L.control_res(PCI, 50, ports, cfi, '1', 'normal', 5, [(a, level, cce)]):
                out.append((p, 5 * 14 + l, ks, vals))
            sent.append((sfn, cfi, rv))
        return out
    gains = [1.0, 0.7 * np.exp(1.1j), 0.6 * np.exp(-2.0j), 0.8 * np.exp(0.4j)][:ports]
    iq, fs = lte_synth.synth_lte(bw_mhz=10, n_frames=n_frames, pci=PCI, snr_db=snr_db, seed=6, cfo_hz=250.0,
                                 ports=ports, port_gains=gains, res=res, idle=True)
    return iq, fs, sent


def check_end_to_end():
    def decode():
        out = {}
        for ports, level, cce in ((2, 4, 4), (4, 4, 4), (2, 8, 0)):
            iq, fs, sent = cell_iq(ports, level, cce)
            cell = next(c for c in iqlte.cells(iq, fs) if c['pci'] == PCI)
            pb = L.pbch(iq, fs, cell, carrier_hz=700e6)
            got = L.sib1_dcis(iq, fs, cell, pb, carrier_hz=700e6)
            dcis = {d['sfn']: d for d in got['dcis']}
            right = [s for s, cfi, rv in sent if s in dcis and dcis[s]['cfi'] == cfi and dcis[s]['rv'] == rv
                     and dcis[s]['level'] == level and dcis[s]['cce'] == cce and dcis[s]['riv'] == 200
                     and dcis[s]['mcs'] == 8 and dcis[s]['format'] == '1A']
            out[(ports, level, cce)] = (len(right), len(sent), got['subframes'])
        return all(r >= s - 1 and r >= 4 for r, s, _n in out.values()), out
    expect("SF 5 SIB1 DCIs (1A, CFI 1-3, RV cycling; AL4 CCE 4 on 2 and 4 ports, AL8 CCE 0): all found, fields right",
           decode)

    def cli():
        iq, fs, _sent = cell_iq(2, snr_db=8.0)
        folder = tempfile.mkdtemp(prefix='iqlte-pdcch-')
        try:
            base = os.path.join(folder, 'lte')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                           'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile',
                                '--pdcch', '--t1', '0.11'], capture_output=True, text=True, timeout=300)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            own = [c.get('own_pdcch') or {} for c in out.get('cells', []) if c.get('pci') == PCI]
            return (r.returncode == 0 and own and len(own[0].get('dcis') or []) >= 4
                    and all(d['riv'] == 200 for d in own[0]['dcis'])), (r.stderr[-300:], own[:1])
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-lte FILE --pdcch: each cell's own_pdcch", cli)


def main():
    for group in (check_pieces, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
