"""Roadmap Round 29.3: our own LTE PDSCH and DL-SCH decoder for SIB1 (``iqlte_phy.turbo_encode``,
``turbo_decode``, ``tc_rate_match``, ``segmentation``, ``si_allocation``, ``si_tbs``, ``pdsch_res``,
``dlsch_decode``, ``sib1``) and ``iq-lte --pdsch``.

Synthetic only, with made-up values (PCI 123, random transport blocks). The real-air checks (the
Band 13 cell's SIB1 in all 50 subframes, byte-equal to the outside ``pdsch_ue``'s; the 700 MHz
cells' SIB1 where ``pdsch_ue`` reads none) are in the ops folder, not here. What the checks pin:

- the turbo interleaver (36.212 table 5.1.3-3): 188 block sizes, each a permutation;
- the turbo code: clean round trip, and through noise at Eb/N0 about 0.8 dB (rate 1/3, K 1024),
  which takes the iterations: one pass of each decoder is not enough;
- rate matching: the circular buffer holds each coded bit once; each redundancy version
  recovered alone;
- segmentation (36.212 5.1.2): sizes and filler; a two-block transport block round trip;
- the TBS tables (36.213 7.1.7.2): 1A's columns 2 and 3 and 1C's table;
- type 2 allocation: every RIV back to its start and length; distributed VRBs onto distinct
  PRBs in both slots for every bandwidth and gap;
- the PDSCH's REs: counts with 2 and 4 ports, CFI 1 and 3, the central PRBs of subframe 5
  without the synchronisation signals;
- end to end: a synthetic 10 MHz cell sending SIB1 in subframe 5 of even frames (1A localized
  and distributed on 1, 2 and 4 ports, RV cycling; 1C): ``sib1`` decodes every one, byte-equal;
- ``tools/iq-lte FILE --pdsch`` adds ``own_sib1``.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_pdsch.py   (about 10 s)
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


def crc_ok(x):
    return np.array_equal(L._crc(x[:-24], '24A'), x[-24:])


def check_coding():
    rng = np.random.default_rng(11)

    def interleaver():
        ks = L.turbo_k()
        bad = [int(k) for k in ks if len(set(L.qpp(int(k)).tolist())) != k]
        return len(ks) == 188 and ks[0] == 40 and ks[-1] == 6144 and not bad, (len(ks), bad[:4])
    expect("turbo interleaver: 188 sizes 40..6144, each a permutation", interleaver)

    def noise():
        out = {}
        for snr_db, iterations in ((-1.0, 8), (-1.0, 1)):
            ok = 0
            for _ in range(8):
                tb = rng.integers(0, 2, 1000).astype(np.uint8)
                e = L.dlsch_encode(tb, 3072, 0)
                s2 = 10 ** (-snr_db / 10)
                llr = 2 * ((1 - 2.0 * e) + rng.standard_normal(len(e)) * np.sqrt(s2)) / s2
                bits, _it, passed = L.turbo_decode(L.tc_rate_recover(llr, 1024, 0), 1024, crc_ok, iterations)
                ok += passed and np.array_equal(bits[:-24], tb)
            out[iterations] = ok
        clean = rng.integers(0, 2, 40).astype(np.uint8)
        d = L.turbo_encode(clean)
        back, _it, _p = L.turbo_decode(4.0 * (1 - 2.0 * d), 40)
        return out[8] == 8 and out[1] <= 4 and np.array_equal(back, clean), out
    expect("turbo code: clean round trip; K 1024 at Eb/N0 ~0.8 dB 8/8 with iterations, not with one", noise)

    def buffer():
        bad = []
        for k in (40, 280, 1024, 6144):
            w = L._tc_buffer(k)
            if sorted(w[w >= 0].tolist()) != list(range(3 * (k + 4))) or len(w) % 96:
                bad.append(k)
        tb = rng.integers(0, 2, 256).astype(np.uint8)
        rvs = []
        for rv in range(4):
            e = L.dlsch_encode(tb, 1200, rv, ports=2)
            got, _b = L.dlsch_decode(2.0 * (1 - 2.0 * e) + rng.standard_normal(len(e)), 256, rv, ports=2)
            rvs.append(got is not None and np.array_equal(got, tb))
        return not bad and all(rvs), (bad, rvs)
    expect("rate matching: each coded bit once in the circular buffer; each RV decoded alone", buffer)

    def segments():
        c1 = L.segmentation(6200)
        c2 = L.segmentation(30)
        tb = rng.integers(0, 2, 7000).astype(np.uint8)
        e = L.dlsch_encode(tb, 20000, 1)
        got, _b = L.dlsch_decode(3.0 * (1 - 2.0 * e), 7000, 1)
        return (c1 == (2, [3136, 3136], 24, 24) and c2 == (1, [40], 10, 0) and got is not None
                and np.array_equal(got, tb)), (c1, c2, got is not None)
    expect("segmentation: 6200 bits two blocks of 3136 (24 filler), 30 bits one of 40; two blocks round trip",
           segments)

    def tbs():
        a = [L.si_tbs(dict(format='1A', mcs=m, n_prb_1a=n)) for m, n in ((0, 2), (6, 3), (8, 2), (26, 3))]
        c = [L.si_tbs(dict(format='1C', tbs_index=i)) for i in (0, 7, 31)]
        mono = all(np.all(np.diff(v) > 0) for v in (*L.TBS_1A.values(), L.TBS_1C))
        return a == [32, 256, 256, 2216] and c == [40, 208, 1736] and mono and len(L.TBS_1C) == 32, (a, c)
    expect("TBS: 1A N_PRB^1A columns 2 and 3, 1C table", tbs)


def check_mapping():
    def riv():
        bad = []
        for n in (6, 25, 50, 100):
            for start in range(n):
                for length in range(1, n - start + 1):
                    v = n * (length - 1) + start if length - 1 <= n // 2 else n * (n - length + 1) + (n - 1 - start)
                    if L.riv_decode(v, n) != (start, length):
                        bad.append((n, start, length))
        c = L.si_allocation(dict(format='1C', riv=0, gap2=False), 50)
        return not bad and len(c[0]) == 4 and len(set(c[0])) == 4, (bad[:3], c)
    expect("type 2 allocation: every RIV back to start and length; 1C RIV 0 at 50 RB 4 PRBs", riv)

    def distributed():
        bad = []
        for n_rb in (6, 15, 25, 50, 75, 100):
            for gap2 in ((False, True) if n_rb >= 50 else (False,)):
                nv = L.n_vrb(n_rb, gap2)
                for slot in (0, 1):
                    p = [L.distributed_prb(v, n_rb, gap2, slot) for v in range(nv)]
                    if len(set(p)) != nv or min(p) < 0 or max(p) >= n_rb:
                        bad.append((n_rb, gap2, slot))
        moved = L.distributed_prb(0, 50, False, 0) != L.distributed_prb(0, 50, False, 1)
        return not bad and moved, bad
    expect("distributed VRBs onto distinct PRBs, both slots, every bandwidth and gap; slot 1 hops", distributed)

    def res():
        n = {}
        for ports, cfi in ((2, 3), (4, 1)):
            ls, ks = L.pdsch_res(PCI, 50, ports, cfi, 5, ([0], [0]))
            n[(ports, cfi)] = len(ls)
        ls, ks = L.pdsch_res(PCI, 50, 2, 3, 5, ([24], [24]))           # central: no PSS/SSS symbols
        n['central'] = len(ls)
        ls0, _k = L.pdsch_res(PCI, 50, 2, 3, 0, ([24], [24]))          # subframe 0: the PBCH too
        n['pbch'] = len(ls0)
        return n == {(2, 3): 120, (4, 1): 136, 'central': 96, 'pbch': 52}, n
    expect("PDSCH REs per PRB: 120 (2 ports, CFI 3), 136 (4 ports, CFI 1); central 96, subframe 0 52", res)


def cell_iq(ports, dcis, tb, snr_db=3.0, n_frames=12):
    """A synthetic cell whose subframe 5 of even frames carries ``dcis(sfn)``'s DCI and ``tb``."""
    res_k = L.pbch_res(PCI)

    def res(f):
        sfn = (SFN0 + f) % 1024
        y = L.encode_pbch(dict(MIB, sfn=sfn - sfn % 4), PCI, ports)[:, sfn % 4]
        out = [(p, 7 + l, 300 - 36 + res_k[res_k[:, 0] == l, 1], y[p, res_k[:, 0] == l])
               for p in range(ports) for l in range(4)]
        if sfn % 2 == 0:
            cfi = 1 + (sfn // 2) % 3
            a, fields, rv = dcis(sfn)
            for p, l, ks, vals in L.control_res(PCI, 50, ports, cfi, '1', 'normal', 5, [(a, 4, 4)]):
                out.append((p, 70 + l, ks, vals))
            for p, ls, ks, vals in L.pdsch_res_values(tb, PCI, 50, ports, cfi, 5, L.si_allocation(fields, 50), rv):
                for l in np.unique(ls):
                    out.append((p, 70 + int(l), ks[ls == l], vals[ls == l]))
        return out
    gains = [1.0, 0.7 * np.exp(1.1j), 0.6 * np.exp(-2.0j), 0.8 * np.exp(0.4j)][:ports]
    return lte_synth.synth_lte(bw_mhz=10, n_frames=n_frames, pci=PCI, snr_db=snr_db, seed=6, cfo_hz=250.0,
                               ports=ports, port_gains=gains, res=res, idle=True)


def dci_1a(riv, mcs, distributed):
    def make(sfn):
        rv = (0, 2, 3, 1)[(sfn // 2) % 4]
        a = L.dci_1a_si(50, riv, mcs, rv, 3, distributed)
        return a, L.parse_dci_1a(a, 50), rv
    return make


def dci_1c(riv, tbs_index):
    def make(sfn):
        a = L.dci_1c_si(50, riv, tbs_index)
        return a, L.parse_dci_1c(a, 50), L.sib1_rv(sfn)
    return make


def check_end_to_end():
    rng = np.random.default_rng(12)

    def decode():
        out = {}
        for name, ports, dcis, tbs in (('1A loc 2', 2, dci_1a(850, 6, 0), 256), ('1A dist 4', 4, dci_1a(850, 6, 1), 256),
                                       ('1A loc 1', 1, dci_1a(200, 6, 0), 256), ('1C 2', 2, dci_1c(1, 7), 208)):
            tb = rng.integers(0, 2, tbs).astype(np.uint8)
            iq, fs = cell_iq(ports, dcis, tb)
            cell = next(c for c in iqlte.cells(iq, fs) if c['pci'] == PCI)
            pb = L.pbch(iq, fs, cell, carrier_hz=700e6)
            got = L.sib1(iq, fs, cell, pb, carrier_hz=700e6)
            want = np.packbits(tb).tobytes().hex()
            out[name] = (got['decoded'], got['tried'], got['sib1'] == want,
                         sorted({b['rv'] for b in got['blocks'] if b['crc']}))
        return all(d >= t - 1 and t >= 5 and same and rvs == [0, 1, 2, 3] for d, t, same, rvs in out.values()), out
    expect("SF 5 SIB1 (1A localized/distributed on 1, 2, 4 ports, 1C; RV cycling): decoded, byte-equal", decode)

    def cli():
        tb = rng.integers(0, 2, 256).astype(np.uint8)
        iq, fs = cell_iq(2, dci_1a(200, 6, 0), tb, snr_db=8.0)
        folder = tempfile.mkdtemp(prefix='iqlte-pdsch-')
        try:
            base = os.path.join(folder, 'lte')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                           'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
            env = dict(os.environ, FMRX_LTE_TOOLS=folder)              # no pycrate: bytes only
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile',
                                '--pdsch', '--t1', '0.11'], capture_output=True, text=True, timeout=300, env=env)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            own = [c.get('own_sib1') or {} for c in out.get('cells', []) if c.get('pci') == PCI]
            want = np.packbits(tb).tobytes().hex()
            return (r.returncode == 0 and own and own[0].get('sib1') == want and own[0].get('decoded', 0) >= 4
                    and own[0].get('fields') is None and own[0].get('fields_reason')), (r.stderr[-300:], own[:1])
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-lte FILE --pdsch: each cell's own_sib1 (its bytes; no pycrate, a reason)", cli)


def main():
    for group in (check_coding, check_mapping, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
