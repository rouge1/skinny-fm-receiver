"""Roadmap Round 28.3: our own NR PDSCH and DL-SCH decoder (``fm_receiver/iqnr_ldpc.py``, the PDSCH
part of ``iqnr_phy``, ``iqnr.type0_pdcch(pdsch=True)``, ``iqnr.other_si``) and ``iq-nr --pdsch``.

Synthetic only, with made-up values (PCI 123, SSB at 600.15 MHz, random payloads, an invented SI
configuration). The real-air checks (every SIB1 and other SI block equal to the outside
``nr_si``'s bytes on the saved NR recordings) are in the ops folder, not here. What the checks pin:

- tables: 38.212 5.3.2-1 to 5.3.2-3 (lifting sizes, base graphs 1 and 2: 316 and 197 entries, the
  identity extension from row 4) and the 38.214 / 38.211 tables (default time allocation A, MCS
  table 1, TBS, DM-RS positions) unchanged (hashes);
- CRCs 16, 24A and 24B: the CRC catalogue's check values for "123456789" (CRC-16/XMODEM 0x31c3,
  CRC-24/LTE-A 0xcde703, CRC-24/LTE-B 0x23ef52; 24A and 24B never ran on air);
- base graph choice and segmentation, hand-worked; TBS for two hand-worked allocations, and
  above 3824 the transport block plus CRC filling whole bytes in each code block;
- LDPC encoding: every parity check holds, both base graphs, every set index;
- rate matching: a round trip at every redundancy version where E > N (and RVs 0, 2, 3 of a
  BG1 block where E < N); min-sum does not decode a copy that
  holds only parity (RV 1 here), and RVs 1 + 2 + 3 summed in the buffer do; an all-zero block
  is refused (it passes the CRC);
- 16QAM and 64QAM soft bits against symbols built here from 38.211 5.1;
- VRB-to-PRB interleaving is a bijection; SLIVs invert; allocations for both DM-RS positions;
- the PDSCH chain on one slot grid: SIB1 (DM-RS from CORESET 0) and other SI (from CRB 0) round
  trip with a delay ramp and phase, QPSK at 3 dB per RE;
- end to end: a synthetic cell (real PBCH, SIB1 PDCCH and PDSCH, an other-SI PDCCH with AL2
  candidates and its PDSCH, 38.211 5.4's per-symbol phase, 700 Hz off, 3 dB) gives the SIB1
  bytes from ``type0_pdcch(pdsch=True)`` and the other SI from ``other_si``;
- ``tools/iq-nr FILE --pdsch`` gives ``own.sib1_hex`` without pycrate; iqnr_ldpc imports numpy only.

Run:  PYTHONPATH=tools python tools/tests/test_iqnr_pdsch.py   (about a minute)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import nr_synth  # noqa: E402
from fm_receiver import iqnr, iqnr_ldpc as L, iqnr_phy as P  # noqa: E402

PCI = 123
FS = 20e6
CENTRE = 600.0e6
SFN0 = 517
LDPC_SHA256 = 'a195c4cc2e2991f80cd632076b288f055de2a83cd6c15da5302fbfa0ed0dce80'
PDSCH_SHA256 = '5da3ceeb9be97cd252a90f8e30b5ee5abe41e346cfc88d6f6a6ad6cebed5db6f'
MIB = dict(sfn=SFN0, half_frame=0, scs_common_khz=15, ssb_subcarrier_offset=22, dmrs_typeA_position=2,
           coreset0_index=8, search_space0_index=2, cell_barred=False, intra_freq_reselection='allowed')
#: An invented other-SI configuration (what ``iqnr_decode.si_config`` gives from a SIB1).
CONFIG = {'offset_to_point_a': 14, 'tdra': [(0, 'A', 2, 12), (0, 'A', 2, 10)], 'window_slots': 20,
          'periods_rf': [8], 'search': {'counts': ((1, 0), (2, 4), (4, 2), (8, 0), (16, 0)),
                                        'first_symbols': (0,), 'period_slots': 1, 'offset_slots': 0}}
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


def digest(obj):
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


def dci_bits(n_rb, rb_start, n_rbs, tdra=0, mcs=0, rv=0, sii=0):
    """A DCI 1_0 SI-RNTI payload (38.212 7.3.1.2.1) with these fields; the RIV of 38.214 5.1.2.2.2."""
    riv = n_rb * (n_rbs - 1) + rb_start if n_rbs - 1 <= n_rb // 2 else n_rb * (n_rb - n_rbs + 1) + (n_rb - 1 - rb_start)
    width = (n_rb * (n_rb + 1) // 2 - 1).bit_length()
    fields = ((riv, width), (tdra, 4), (0, 1), (mcs, 5), (rv, 2), (sii, 1), (0, 15))
    return np.array([(v >> (w - 1 - i)) & 1 for v, w in fields for i in range(w)], dtype=np.uint8)


def noisy(g, snr_db, rng):
    return g + (rng.standard_normal(g.shape) + 1j * rng.standard_normal(g.shape)) * np.sqrt(10 ** (-snr_db / 10) / 2)


# ----- tables and codes -------------------------------------------------------------------

def check_tables():
    def ldpc():
        rows1, rows2 = {r for r, _c, _v in L._BG1}, {r for r, _c, _v in L._BG2}
        ext = (all((r, 22 + r) in {(a, b) for a, b, _v in L._BG1} for r in range(4, 46))
               and all((r, 10 + r) in {(a, b) for a, b, _v in L._BG2} for r in range(4, 42)))
        ok = (len(L._BG1) == 316 and len(L._BG2) == 197 and len(rows1) == 46 and len(rows2) == 42 and ext
              and digest([L.LIFT_SETS, L._BG1, L._BG2]) == LDPC_SHA256)
        return ok, digest([L.LIFT_SETS, L._BG1, L._BG2])
    expect("38.212 tables 5.3.2-1 to -3: 316 and 197 entries, identity extension, unchanged (sha256)", ldpc)

    def pdsch():
        got = digest([P.PDSCH_TDRA_A, P.MCS_TABLE_1, P.TBS_TABLE, sorted(P._DMRS_A_POS2.items()),
                      sorted(P._DMRS_B_POS2.items())])
        return got == PDSCH_SHA256 and len(P.TBS_TABLE) == 93 and len(P.MCS_TABLE_1) == 29, got
    expect("38.214 time allocation A, MCS table 1, TBS and 38.211 DM-RS positions: unchanged (sha256)", pdsch)

    def crcs():
        bits = np.unpackbits(np.frombuffer(b'123456789', dtype=np.uint8))
        got = {k: int(''.join(map(str, L.crc(bits, k))), 2) for k in ('16', '24A', '24B')}
        return got == {'16': 0x31C3, '24A': 0xCDE703, '24B': 0x23EF52}, {k: hex(v) for k, v in got.items()}
    expect("CRC 16, 24A, 24B: the catalogue's check values for '123456789'", crcs)

    def sizes():
        got = [(L.base_graph(a, r), {k: v for k, v in L.segment(a + c, L.base_graph(a, r)).items()})
               for a, r, c in ((888, 0.117, 16), (120, 0.663, 16), (4000, 0.44, 24), (9000, 0.6, 24))]
        want = [(2, {'C': 1, 'L': 0, 'k_prime': 904, 'K': 960, 'Zc': 96, 'i_ls': 1}),
                (2, {'C': 1, 'L': 0, 'k_prime': 136, 'K': 240, 'Zc': 24, 'i_ls': 1}),
                (1, {'C': 1, 'L': 0, 'k_prime': 4024, 'K': 4224, 'Zc': 192, 'i_ls': 1}),
                (1, {'C': 2, 'L': 24, 'k_prime': 4536, 'K': 4576, 'Zc': 208, 'i_ls': 6})]
        tbs = [P.tbs_of(108 * 34, 120 / 1024, 2), P.tbs_of(96, 679 / 1024, 2)]
        big = all(((t := P.tbs_of(n, r, q)) + 24) % (8 * max(1, -(-(t + 24) // (3816 if r <= 0.25 else 8424)))) == 0
                  for n in (4000, 9000, 20000, 40000) for r, q in ((0.2, 2), (0.5, 4), (0.85, 6)))
        return got == want and tbs == [888, 120] and big, (got, tbs, big)
    expect("base graph, segmentation (hand-worked); TBS 888 and 120; whole bytes per block above 3824", sizes)


def _syndrome(w, bg, zc, i_ls):
    for cols, shifts in L._graph(bg, zc, i_ls):
        acc = np.zeros(zc, dtype=np.uint8)
        for col, p in zip(cols, shifts):
            acc ^= np.roll(w[col * zc:(col + 1) * zc], -int(p))
        if acc.any():
            return False
    return True


def check_ldpc():
    rng = np.random.default_rng(1)

    def parity():
        bad = []
        for bg, kb in ((1, 22), (2, 10)):
            for i_ls, sizes in enumerate(L.LIFT_SETS):
                zc = sizes[1]
                c = rng.integers(0, 2, kb * zc).astype(np.uint8)
                d = L.encode(c, bg, zc, i_ls)
                w = np.concatenate((c[:2 * zc], d))
                if not np.array_equal(w[:kb * zc], c) or not _syndrome(w, bg, zc, i_ls):
                    bad.append((bg, i_ls))
        return not bad, bad
    expect("LDPC encoding: every parity check holds, base graphs 1 and 2, set indices 0-7", parity)

    def rvs():
        a, e_len = 120, 192
        tb = rng.integers(0, 2, a).astype(np.uint8)
        b = np.concatenate((tb, L.crc(tb, '16')))
        s = L.segment(len(b), 2)
        c = np.zeros(s['K'], dtype=np.uint8)
        c[:s['k_prime']] = b
        d = L.encode(c, 2, s['Zc'], s['i_ls'])

        def check(x):
            return np.array_equal(L.crc(x[:a], '16'), x[a:])

        def soft(rv, buf=None, snr=30.0):
            f = L.rate_match(d, e_len, rv, 2, 2, s['Zc'], s['k_prime'], s['K'])
            sd = np.sqrt(10 ** (-snr / 10))
            llr = 2 * ((1 - 2.0 * f) + sd * rng.standard_normal(e_len)) / sd ** 2
            return L.rate_recover(llr, rv, 2, 2, s['Zc'], s['k_prime'], s['K'], buf)
        dec = {rv: L.decode(soft(rv), 2, s['Zc'], s['i_ls'], s['k_prime'], s['K'], check=check)[0] for rv in range(4)}
        buf = None
        for rv in (1, 2, 3):
            buf = soft(rv, buf)
        combined = L.decode(buf, 2, s['Zc'], s['i_ls'], s['k_prime'], s['K'], check=check)[0]
        ok = (dec[0] is not None and np.array_equal(dec[0], b) and dec[1] is None
              and combined is not None and np.array_equal(combined, b))
        big = []
        # E > N (888 bits): every RV holds every bit. BG1, 4000 bits into 9000: RV 1 starts a
        # quarter in and misses most systematic bits, so only 0, 2 and 3 (min-sum's limit again).
        for a2, e2, rv_list in ((888, 7344, (0, 1, 2, 3)), (4000, 9000, (0, 2, 3))):
            bg = L.base_graph(a2, a2 / e2)
            kind = '16'
            tb2 = rng.integers(0, 2, a2).astype(np.uint8)
            b2 = np.concatenate((tb2, L.crc(tb2, kind)))
            s2 = L.segment(len(b2), bg)
            c2 = np.zeros(s2['K'], dtype=np.uint8)
            c2[:s2['k_prime']] = b2
            d2 = L.encode(c2, bg, s2['Zc'], s2['i_ls'])
            for rv in rv_list:
                f = L.rate_match(d2, e2, rv, 2, bg, s2['Zc'], s2['k_prime'], s2['K'])
                got, _it = L.decode(L.rate_recover(8.0 * (1 - 2.0 * f), rv, 2, bg, s2['Zc'], s2['k_prime'], s2['K']),
                                    bg, s2['Zc'], s2['i_ls'], s2['k_prime'], s2['K'],
                                    check=lambda x, a2=a2: np.array_equal(L.crc(x[:a2], kind), x[a2:]))
                big.append(got is not None and np.array_equal(got, b2))
        return ok and all(big), ({rv: v is not None for rv, v in dec.items()}, combined is not None, big)
    expect("rate matching: every RV round trips; parity-only RV 1 does not, RVs 1+2+3 combined do", rvs)

    def zero_block():
        setup = P.dlsch_setup(120, 679 / 1024)
        g = 192
        bits = P.encode_dlsch(bytes(15), setup, 0, 2, g)
        return P.dlsch_decode(P.dlsch_soft(8.0 * (1 - 2.0 * bits), setup, 0, 2), setup) is None, 'decoded zeros'
    expect("an all-zero transport block (it passes the CRC) is refused", zero_block)


def check_mapping():
    rng = np.random.default_rng(2)

    def qam():
        bad = []
        for qm in (4, 6):
            bits = rng.integers(0, 2, 600 * qm).astype(np.uint8)
            b = bits.reshape(-1, qm).astype(float)
            s = 1 - 2 * b
            if qm == 4:          # 38.211 5.1.4
                x = (s[:, 0] * (2 - s[:, 2]) + 1j * s[:, 1] * (2 - s[:, 3])) / np.sqrt(10)
            else:                # 38.211 5.1.5
                x = (s[:, 0] * (4 - s[:, 2] * (2 - s[:, 4])) + 1j * s[:, 1] * (4 - s[:, 3] * (2 - s[:, 5]))) / np.sqrt(42)
            h = 0.7 * np.exp(0.4j) * np.ones(len(x))
            y = h * x + (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * 0.01
            llr = P._qam_llr(y * np.conj(h), np.abs(h) ** 2, 2e-4, qm)
            if not np.array_equal((llr < 0).astype(np.uint8), bits):
                bad.append(qm)
        return not bad, bad
    expect("16QAM and 64QAM soft bits: hard decisions return the bits of 38.211 5.1 symbols", qam)

    def allocation():
        perm = all(sorted(P.vrb_to_prb(range(n), n, True)) == list(range(n)) for n in (24, 47, 48, 96))
        sliv = all(P.sliv_to_sl(14 * (l - 1) + s if l - 1 <= 7 else 14 * (14 - l + 1) + 13 - s) == (s, l)
                   for s in range(14) for l in range(1, 15 - s))
        a2 = P.pdsch_alloc(dict(mcs=0, tdra=0, rb_start=0, n_rbs=34), 2, 48)
        a3 = P.pdsch_alloc(dict(mcs=9, tdra=0, rb_start=5, n_rbs=1), 3, 48)
        b2 = P.pdsch_alloc(dict(mcs=4, tdra=8, rb_start=3, n_rbs=8), 2, 48)     # row 9: type B, S 5, L 2
        ok = (a2['dmrs'] == (2, 7, 11) and a2['tbs'] == 888 and a3['dmrs'] == (3, 7, 11) and a3['tbs'] == 120
              and b2['mapping'] == 'B' and b2['shared_dmrs'] and b2['data_symbols'] == (5, 6)
              and P.pdsch_alloc(dict(mcs=29, tdra=0, rb_start=0, n_rbs=4), 2, 48) is None)
        return perm and sliv and ok, (perm, sliv, a2, a3)
    expect("VRB-to-PRB interleaving a bijection; SLIVs invert; allocations, DM-RS and TBS", allocation)

    def grid_chain():
        bad = []
        for dci, pos, k_ref, snr in ((dict(mcs=0, tdra=0, rb_start=0, n_rbs=34), 2, 0, 3.0),
                                     (dict(mcs=2, tdra=0, rb_start=7, n_rbs=12), 3, 24, 3.0),
                                     (dict(mcs=13, tdra=0, rb_start=2, n_rbs=20), 2, 12, 14.0),
                                     (dict(mcs=20, tdra=1, rb_start=4, n_rbs=24), 2, 12, 22.0),
                                     (dict(mcs=1, tdra=0, rb_start=0, n_rbs=48, vrb_to_prb='interleaved'), 2, 0, 3.0)):
            al = P.pdsch_alloc(dci, pos, 48)
            setup = P.dlsch_setup(al['tbs'], al['rate'])
            data = bytes(rng.integers(0, 256, al['tbs'] // 8, dtype=np.uint8))
            g = P.pdsch_grid(data, setup, al, PCI, 3, 0, 48, k_ref=k_ref)
            ramp = np.exp(1j * (0.03 * np.arange(576) + 0.4))
            llr, _snr, _slope = P.pdsch_llrs(noisy(g * ramp, snr, rng), PCI, 3, al, k_ref=k_ref)
            if P.dlsch_decode(P.dlsch_soft(llr, setup, 0, al['qm']), setup) != data:
                bad.append(dci['mcs'])
        return not bad, bad
    expect("PDSCH grid round trip: QPSK at 3 dB, 16/64QAM, DM-RS from CORESET 0 and CRB 0, interleaved", grid_chain)


# ----- end to end ---------------------------------------------------------------------------

def synth_cell(snr_db=3.0):
    rng = np.random.default_rng(7)
    cs = P.coreset0(MIB)
    occ = P.type0_occasion(MIB['search_space0_index'], 0, cs['n_symb'])
    sib1 = dict(rb_start=0, n_rbs=34, mcs=0)
    a1 = P.pdsch_alloc(dict(sib1, tdra=0), 2, 48)
    s1 = P.dlsch_setup(a1['tbs'], a1['rate'])
    data1 = bytes(rng.integers(0, 256, a1['tbs'] // 8, dtype=np.uint8))
    osi = dict(rb_start=4, n_rbs=8, mcs=2)
    a2 = P.pdsch_alloc(dict(osi, tdra=1), 2, 48, tdra=CONFIG['tdra'])
    s2 = P.dlsch_setup(a2['tbs'], a2['rate'])
    data2 = bytes(rng.integers(1, 256, a2['tbs'] // 8, dtype=np.uint8))
    k_ref = 12 * (CONFIG['offset_to_point_a'] - cs['offset_rb'])
    sent = {'sib1': [], 'osi': []}

    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), PCI, idx)

    def res(frame_no, slot):
        sfn = (SFN0 + frame_no) % 1024
        if sfn % 2 == occ['parity'] and slot == occ['n0'] + 1:
            a = dci_bits(48, tdra=0, sii=0, **sib1)
            g = P.pdsch_grid(data1, s1, a1, PCI, slot, 0, 48)
            g[:2] = P.coreset_grid(48, 2, PCI, slot, 0, [(4, 0, P.encode_pdcch(a, P.SI_RNTI, PCI, 4))])
            sent['sib1'].append((sfn, slot))
        elif sfn % 8 == 0 and slot == 4:
            a = dci_bits(48, tdra=1, sii=1, **osi)
            g = P.pdsch_grid(data2, s2, a2, PCI, slot, 0, 48, k_ref=k_ref)
            g[:2] = P.coreset_grid(48, 2, PCI, slot, 0, [(2, 4, P.encode_pdcch(a, P.SI_RNTI, PCI, 2))])
            sent['osi'].append((sfn, slot))
        else:
            return []
        return [(l, cs['k0'], g[l]) for l in range(14)]
    iq, _fs, _truth = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20,
                                        n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=100, fs=FS, seed=3,
                                        snr_db=snr_db, cfo_hz=700.0, pbch=pbch, res=res, rf_hz=CENTRE)
    return iq, data1, data2, sent


def check_end_to_end():
    iq, data1, data2, sent = synth_cell()
    holder = {}

    def sib1():
        cell = next((e for e in iqnr.ssb_search(iq, FS, CENTRE, mib=True) if e['pci'] == PCI), None)
        if cell is None or cell.get('mib') is None:
            return False, 'no MIB'
        holder['cell'] = cell
        got = iqnr.type0_pdcch(iq, FS, CENTRE, cell, pdsch=True)
        blocks = [d['pdsch']['hex'] for d in got['dcis'] if d.get('pdsch')]
        return (len(blocks) >= 4 and all(h == data1.hex() for h in blocks)), (len(blocks), blocks[:1], sent['sib1'])
    expect("type0_pdcch(pdsch=True): every SIB1 PDSCH in the span, its bytes (3 dB, 700 Hz off, symbol phase)", sib1)

    def phase():
        cell = holder.get('cell')
        if cell is None:
            return False, 'no cell'
        got = iqnr.type0_pdcch(iq, FS, CENTRE, cell)
        cs = P.coreset0(cell['mib'])
        tm = iqnr._Timing(FS, CENTRE, cell, cs)
        al = P.pdsch_alloc(got['dcis'][0], 2, 48)
        g = tm.grid(iq, got['dcis'][0]['t_s'], range(al['S'], al['S'] + al['L']))
        dm_k = np.array([12 * p + 2 * j for p in al['prbs'] for j in range(6)])
        z = {l: np.vdot(P.pdsch_dmrs(PCI, got['dcis'][0]['slot'], l, 576)[dm_k // 2], g[l, dm_k]) for l in al['dmrs']}
        ls = sorted(z)
        hz = [np.angle(z[b] / z[a]) / (2 * np.pi * (b - a) / 14e3) for a, b in zip(ls, ls[1:])]
        left = 700.0 - float(cell['cfo_hz'])      # the offset's estimation error, all that should turn it
        return abs(left) > 50 and all(abs(f - left) < 40 for f in hz), ([round(f) for f in hz], round(left))
    expect("38.211 5.4's phase at the cell's own carrier: SIB1's DM-RS turns only by the offset's estimation error",
           phase)

    def other():
        cell = holder.get('cell')
        if cell is None:
            return False, 'no cell'
        got = iqnr.other_si(iq, FS, CENTRE, cell, CONFIG)
        blocks = [(b['sfn'], b['slot'], b['level'], b['cce'], (b['pdsch'] or {}).get('hex')) for b in got['blocks']]
        inside = [s for s in sent['osi'] if s[0] in ((SFN0 + 3) % 1024, (SFN0 + 11) % 1024)]
        return ([(s, sl) for s, sl, *_ in blocks] == inside and len(inside) >= 1
                and all(lv == 2 and cce == 4 and h == data2.hex() for _s, _sl, lv, cce, h in blocks)), (blocks, inside)
    expect("other_si: the SI window's PDCCH (AL2 candidates) and PDSCH (DM-RS from CRB 0), its bytes", other)

    def cli():
        folder = tempfile.mkdtemp(prefix='iqnr-pdsch-')
        try:
            base = os.path.join(folder, 'nr')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': FS},
                           'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE}]}, fh)
            env = dict(os.environ, FMRX_LTE_TOOLS=os.path.join(folder, 'no-tools'))
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-nr'),
                                base + '.cfile', '--pdsch'], capture_output=True, text=True, timeout=300, env=env)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            own = (out.get('ssbs') or [{}])[0].get('own') or {}
            return (r.returncode == 0 and own.get('sib1_hex') == data1.hex() and own.get('sib1') is None
                    and own.get('reason')), r.stderr[-300:] or own
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-nr FILE --pdsch: SIB1's bytes without pycrate (and a reason for no parse)", cli)

    def imports():
        code = ("import sys; sys.path.insert(0, %r)\nfrom fm_receiver import iqnr_ldpc\n"
                "bad = [m for m in ('scipy', 'gnuradio', 'PyQt5', 'matplotlib') if m in sys.modules]\n"
                "print(bad)" % os.path.dirname(HERE))
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
        return r.stdout.strip() == '[]', r.stdout + r.stderr
    expect("iqnr_ldpc imports numpy only", imports)


def main():
    for group in (check_tables, check_ldpc, check_mapping, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
