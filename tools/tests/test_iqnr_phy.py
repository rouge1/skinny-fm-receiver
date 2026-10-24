"""Roadmap Rounds 28.1 and 28.2: our own NR PBCH and SIB1-PDCCH decoders (``fm_receiver/iqnr_phy.py``,
``iqnr.type0_pdcch``) and ``iq-nr --mib`` and ``--pdcch``.

Synthetic only, with made-up values (PCI 123, SSB at 600.15 MHz). The real-air checks (every
MIB, and every SIB1 DCI, equal to the outside ``nr_si``'s on the saved NR recordings) are in
the ops folder, not here. What the checks pin:

- the four 38.212 tables: permutations of the right length, unchanged (a hash);
- CRC 24C: a block with its CRC leaves no remainder, and one flipped bit is caught;
- polar code (K 56, N 512, E 864): a noiseless codeword decodes to its bits;
- the PBCH chain on one SSB grid (encode_pbch, ssb_grid, decode_pbch): every MIB field and
  the timing bits come back, for each SSB index and both half-frames, at -2 dB per RE in 19 of
  20 draws, with a common phase; a wrong PCI or noise gives None;
- end to end: ``iqnr.ssb_search(..., mib=True)`` on a synthetic cell whose SSBs carry a real
  PBCH reads the MIB of the first burst, with that burst's SFN, at 10 dB;
- without ``mib`` the entries carry no ``mib`` key (the default output is unchanged);
- ``tools/iq-nr FILE --mib`` prints it; iqnr_phy imports numpy only;
- 38.213 tables 13-1, 13-11 and 10.1-1 unchanged (a hash); CORESET 0 from a MIB; the Type0
  occasion (n0, SFN parity, first symbol) for four table rows;
- polar N (38.212 5.3.1) and rate matching by puncturing (DCI at AL4: K 63, E 432 < N 512),
  shortening (K 63, E 100) and repetition: noiseless codewords decode;
- CCE-to-REG interleaving: each CCE one REG bundle, all bundles used once, the PCI shifting every
  CCE one bundle along; a hand-worked CCE; candidates (7 in 16 CCEs, 3 in 8, repeats dropped);
- DCI 1_0 with SI-RNTI: 39 bits in 48 RBs, 37 in 24; RIVs invert for every allocation in 24 RBs;
- the PDCCH chain on one CORESET grid: every Type0 candidate decodes at 0 dB per RE with a delay
  ramp and common phase, in 1-, 2- and 3-symbol CORESETs; a wrong PCI, another RNTI, or noise
  alone, gives nothing;
- end to end: ``iqnr.type0_pdcch`` on a synthetic cell with a real PBCH and SIB1 PDCCHs (AL8 and
  AL4, 700 Hz off, 0 dB) finds each one in the span with its bits, SFN and slot, and nothing else;
- ``tools/iq-nr FILE --pdcch`` prints them.

Run:  PYTHONPATH=tools python tools/tests/test_iqnr_phy.py   (under a minute)
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
from fm_receiver import iqnr, iqnr_phy as P  # noqa: E402

PCI = 123
FS = 20e6
CENTRE = 600.0e6
SFN0 = 517
TABLES_SHA256 = 'c5ed21d6ebb6c0e7c3fbc409941bb995812a12559fcf5e3910694595bec7a9b3'
MIB = dict(sfn=SFN0, half_frame=0, scs_common_khz=15, ssb_subcarrier_offset=22, dmrs_typeA_position=2,
           coreset0_index=8, search_space0_index=2, cell_barred=False, intra_freq_reselection='allowed')
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


def grid_of(mib, pci, i_bar):
    """The SSB as iqnr cuts it: 4 x 256 spectra, bin (k - 120) mod 256."""
    g = P.ssb_grid(P.encode_pbch(mib, pci, i_bar % 4), pci, i_bar, np.ones(127), np.ones(127))
    xc = np.zeros((4, 256), dtype=complex)
    xc[:, (np.arange(240) - 120) % 256] = g
    return xc


def same(got, want):
    return got is not None and all(got[k] == want[k] for k in want)


def check_tables():
    def perms():
        ok = (sorted(P._Q) == list(range(1024)) and sorted(P._PI_IL_MAX) == list(range(164))
              and sorted(P._P_SUB) == list(range(32)) and sorted(P._G_PBCH) == list(range(32)))
        return ok, 'a table is not a permutation of its range'
    expect("tables: Q (1024), Pi_IL (164), P (32) and G (32) are permutations", perms)

    def digest():
        got = hashlib.sha256(json.dumps([P._Q, P._PI_IL_MAX, P._P_SUB, P._G_PBCH]).encode()).hexdigest()
        return got == TABLES_SHA256, got
    expect("tables: unchanged from 38.212 V19.4.0 (sha256)", digest)


def check_codes():
    rng = np.random.default_rng(1)

    def crc():
        msg = rng.integers(0, 2, 32).astype(np.uint8)
        block = np.concatenate((msg, P.crc24c(msg)))
        flipped = block.copy()
        flipped[5] ^= 1
        return (np.array_equal(P.crc24c(block[:32]), block[32:])
                and not np.array_equal(P.crc24c(flipped[:32]), flipped[32:])), 'CRC'
    expect("CRC 24C: checks a block, catches one flipped bit", crc)

    def polar():
        bad = 0
        for _ in range(5):
            msg = rng.integers(0, 2, 32).astype(np.uint8)
            c = np.concatenate((msg, P.crc24c(msg)))
            e = P.polar_encode(c, P.N_PBCH, P.E_PBCH)
            got, _m = P.polar_decode(4.0 * (1 - 2.0 * e), P.K_PBCH, P.N_PBCH)
            bad += got is None or not np.array_equal(got, c)
        return bad == 0, f'{bad} of 5 wrong'
    expect("polar K 56, N 512, E 864: noiseless codewords decode to their bits", polar)


def check_pbch():
    rng = np.random.default_rng(2)

    def every_index():
        bad = []
        for i_bar in range(8):
            mib = dict(MIB, sfn=(SFN0 + i_bar * 37) % 1024, half_frame=i_bar // 4)
            got = P.decode_pbch(grid_of(mib, PCI, i_bar), PCI, i_bar)
            if not same(got, mib) or got['ssb_index'] != i_bar % 4:
                bad.append(i_bar)
        return not bad, f'i_bar {bad}'
    expect("PBCH: every field back for SSB index 0-3 and both half-frames", every_index)

    def low_snr():
        xc = grid_of(MIB, PCI, 0)
        ok = 0
        for _ in range(20):
            n = (rng.standard_normal(xc.shape) + 1j * rng.standard_normal(xc.shape)) * np.sqrt(10 ** 0.2 / 2)
            ok += same(P.decode_pbch(np.exp(1.1j) * xc + n, PCI, 0), MIB)
        return ok >= 19, f'{ok} of 20'
    expect("PBCH: -2 dB per RE and a common phase: 19 of 20 decode", low_snr)

    def negatives():
        xc = grid_of(MIB, PCI, 0)
        noise = (rng.standard_normal(xc.shape) + 1j * rng.standard_normal(xc.shape)) / np.sqrt(2)
        return (P.decode_pbch(xc, PCI + 1, 0) is None and P.decode_pbch(noise, PCI, 0) is None,
                'decoded what it should not')
    expect("PBCH: a wrong PCI, or noise alone, gives None", negatives)


def synth_cell(snr_db=10.0):
    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), PCI, idx)
    return nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                             bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=100, fs=FS, seed=3, snr_db=snr_db,
                             pbch=pbch)


def check_search():
    iq, _fs, truth = synth_cell()

    def end_to_end():
        found = iqnr.ssb_search(iq, FS, CENTRE, mib=True)
        e = next((x for x in found if x['pci'] == PCI), None)
        if e is None or e.get('mib') is None:
            return False, f'entries {found}'
        want = dict(MIB, sfn=(SFN0 + truth['ssb_frames'][0]) % 1024)
        return (same(e['mib'], want) and abs(e['mib']['ssb_s'] - truth['ssb_times_s'][0]) < 20e-6
                and e['mib_bursts'][0] == e['mib_bursts'][1] == len(truth['ssb_times_s'])), (e['mib'], e['mib_bursts'])
    expect("ssb_search(mib=True): the first burst's MIB and SFN, every burst decoded, at 10 dB", end_to_end)

    def default_unchanged():
        found = iqnr.ssb_search(iq, FS, CENTRE)
        return found and all('mib' not in e and 'mib_bursts' not in e for e in found), found
    expect("ssb_search without mib: no mib keys (the default output is unchanged)", default_unchanged)

    def cli():
        folder = tempfile.mkdtemp(prefix='iqnr-phy-')
        try:
            base = os.path.join(folder, 'nr')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': FS},
                           'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE}]}, fh)
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-nr'),
                                base + '.cfile', '--mib'], capture_output=True, text=True, timeout=300)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            e = (out.get('ssbs') or [{}])[0]
            return (r.returncode == 0 and (e.get('mib') or {}).get('coreset0_index') == 8,
                    r.stderr[-300:] or e)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-nr FILE --mib prints each cell's MIB", cli)

    def imports():
        code = ("import sys; sys.path.insert(0, %r)\nfrom fm_receiver import iqnr_phy\n"
                "bad = [m for m in ('scipy', 'gnuradio', 'PyQt5', 'matplotlib') if m in sys.modules]\n"
                "print(bad)" % os.path.dirname(HERE))
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
        return r.stdout.strip() == '[]', r.stdout + r.stderr
    expect("iqnr_phy imports numpy only", imports)


# ----- 28.2: CORESET 0 and SIB1's PDCCH --------------------------------------------------

TABLES_213_SHA256 = 'fec7088e2dae6b6afb335d66534ce179a0c7ba11ed497a562d3d567b9e216cc4'


def check_coreset():
    def tables():
        got = hashlib.sha256(json.dumps([P.CORESET0_15_15, P.SS0_FR1, P.TYPE0_CANDIDATES]).encode()).hexdigest()
        return got == TABLES_213_SHA256, got
    expect("38.213 tables 13-1, 13-11 and 10.1-1: unchanged (sha256)", tables)

    def from_mib():
        cs = P.coreset0(MIB)
        occ = [P.type0_occasion(*a) for a in ((2, 0, 2), (4, 0, 1), (9, 3, 2), (1, 1, 2))]
        want = [(2, 0, 0), (5, 0, 0), (1, 1, 0), (0, 0, 2)]
        return (cs == {'n_rb': 48, 'n_symb': 2, 'offset_rb': 12, 'k0': -22 - 144}
                and [(o['n0'], o['parity'], o['first_symbol']) for o in occ] == want
                and P.coreset0(dict(MIB, coreset0_index=15)) is None
                and P.coreset0(dict(MIB, scs_common_khz=30)) is None), (cs, occ)
    expect("CORESET 0 from the MIB (48 RB, 2 symbols, 12 RBs under the SSB); Type0 slots n0 and parity", from_mib)

    def interleaver():
        ok, why = True, ''
        for n_rb, n_symb in ((24, 2), (48, 1), (48, 2), (48, 3), (96, 1)):
            a, b = P.cce_rbs(n_rb, n_symb, PCI), P.cce_rbs(n_rb, n_symb, PCI + 1)
            flat = sorted(rb for c in a for rb in c)
            n_b = len(a)
            # one bundle along: CCE j of PCI + 1 sits where the next bundle of PCI's CCE j is
            moved = all(b[j][0] == (a[j][0] + 6 // n_symb) % n_rb for j in range(n_b))
            if flat != list(range(n_rb)) or not moved or any(len(c) != 6 // n_symb for c in a):
                ok, why = False, (n_rb, n_symb)
        hand = P.cce_rbs(48, 2, PCI)[:2] == [[33, 34, 35], [9, 10, 11]]   # f(0) = 11, f(1) = 8 + 11 mod 16
        cands = (P.type0_candidates(16) == [(4, 0), (4, 4), (4, 8), (4, 12), (8, 0), (8, 8), (16, 0)]
                 and P.type0_candidates(8) == [(4, 0), (4, 4), (8, 0)])
        return ok and hand and cands, why or (hand, cands)
    expect("CCE-to-REG: a bijection of bundles, the PCI shifts each CCE a bundle; candidates", interleaver)

    def rate_matching():
        rng = np.random.default_rng(4)
        bad = []
        for k, e, kind in ((63, 432, 'puncture'), (63, 100, 'shorten'), (63, 864, 'repeat'), (61, 1728, 'repeat')):
            n = P.polar_n(k, e, P.N_MAX_DCI)
            a = rng.integers(0, 2, k - 24).astype(np.uint8)
            c = np.concatenate((a, P.dci_crc(a, P.SI_RNTI)))
            x = P.polar_encode(c, n, e)

            def check(bits, a_len=k - 24):
                return np.array_equal(P.dci_crc(bits[:a_len], P.SI_RNTI), bits[a_len:])
            got, _m = P.polar_decode(4.0 * (1 - 2.0 * x), k, n, check=check)
            if len(x) != e or got is None or not np.array_equal(got, c):
                bad.append(kind)
        sizes = (P.polar_n(63, 432, 9), P.polar_n(63, 100, 9), P.polar_n(56, 864, 9), P._punctured(63, 512, 432),
                 P._punctured(63, 128, 100))
        return not bad and sizes == (512, 128, 512, True, False), (bad, sizes)
    expect("polar N and rate matching: puncturing (E 432 < N 512), shortening, repetition round trip", rate_matching)

    def dci_fields():
        sizes = (P.dci_1_0_si_bits(48), P.dci_1_0_si_bits(24))
        seen = {}
        for start in range(24):
            for length in range(1, 25 - start):
                riv = (24 * (length - 1) + start if length - 1 <= 12 else 24 * (24 - length + 1) + (23 - start))
                seen[riv] = P.riv_to_rbs(riv, 24) == (start, length)
        a = np.zeros(39, dtype=np.uint8)
        a[:11] = [int(b) for b in format(767, '011b')]
        a[21:23] = (1, 0)                                         # fda 11, tdra 4, vrb 1, mcs 5, then rv
        f = P.parse_dci_1_0_si(a, 48)
        return (sizes == (39, 37) and all(seen.values()) and len(seen) == 300
                and (f['rb_start'], f['n_rbs'], f['rv'], f['sii'], f['reserved']) == (0, 34, 2, 0, 0)), (sizes, f)
    expect("DCI 1_0 SI-RNTI: 39 / 37 bits; every RIV of 24 RBs inverts; fields parse", dci_fields)


def _pdcch_on(n_rb, n_symb, pci, slot, level, cce0, a, rnti=None):
    sym = P.encode_pdcch(a, P.SI_RNTI if rnti is None else rnti, pci, level)
    return P.coreset_grid(n_rb, n_symb, pci, slot, 0, [(level, cce0, sym)])


def check_pdcch():
    rng = np.random.default_rng(6)

    def noise(shape, snr_db=0.0):
        return (rng.standard_normal(shape) + 1j * rng.standard_normal(shape)) * np.sqrt(10 ** (-snr_db / 10) / 2)

    def every_candidate():
        bad = []
        for n_rb, n_symb in ((48, 2), (48, 1), (24, 3)):
            a_len = P.dci_1_0_si_bits(n_rb)
            n_cce = n_rb * n_symb // 6
            for level, cce0 in P.type0_candidates(n_cce):
                a = rng.integers(0, 2, a_len).astype(np.uint8)
                g = _pdcch_on(n_rb, n_symb, PCI, 3, level, cce0, a)
                ramp = np.exp(1j * (0.04 * np.arange(12 * n_rb) + 0.9))
                got = P.decode_pdcch(g * ramp + noise(g.shape), PCI, 3, 0, n_rb, n_symb)
                if [(d['level'], d['cce'], d['bits']) for d in got] != [(level, cce0, ''.join(map(str, a)))]:
                    bad.append((n_rb, n_symb, level, cce0))
        return not bad, bad
    expect("PDCCH: every Type0 candidate at 0 dB per RE (1, 2, 3 symbols), delay ramp and phase", every_candidate)

    def negatives():
        a = rng.integers(0, 2, 39).astype(np.uint8)
        g = _pdcch_on(48, 2, PCI, 3, 4, 4, a)
        other = _pdcch_on(48, 2, PCI, 3, 4, 4, a, rnti=0xFFFE)
        got = (P.decode_pdcch(g, PCI + 1, 3, 0, 48, 2), P.decode_pdcch(other, PCI, 3, 0, 48, 2),
               P.decode_pdcch(noise(g.shape, -10.0), PCI, 3, 0, 48, 2))
        return all(x == [] for x in got), got
    expect("PDCCH: a wrong PCI, another RNTI, or noise alone, gives nothing", negatives)


def synth_pdcch_cell(snr_db=0.0):
    rng = np.random.default_rng(7)
    a = rng.integers(0, 2, 39).astype(np.uint8)
    cs = P.coreset0(MIB)
    occ = P.type0_occasion(MIB['search_space0_index'], 0, cs['n_symb'])
    sent = []

    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), PCI, idx)

    def res(frame_no, slot):
        sfn = (SFN0 + frame_no) % 1024
        if sfn % 2 != occ['parity'] or slot != occ['n0'] + 1:
            return []
        level, cce0 = (4, 8) if sfn % 4 else (8, 0)
        g = _pdcch_on(cs['n_rb'], cs['n_symb'], PCI, slot, level, cce0, a)
        sent.append((sfn, slot, level, cce0))
        return [(l, cs['k0'], g[l]) for l in range(cs['n_symb'])]
    iq, _fs, truth = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20,
                                       n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=100, fs=FS, seed=3,
                                       snr_db=snr_db, cfo_hz=700.0, pbch=pbch, res=res)
    return iq, a, sent


def check_type0():
    iq, a, sent = synth_pdcch_cell()
    bits = ''.join(map(str, a))

    def end_to_end():
        cell = next((e for e in iqnr.ssb_search(iq, FS, CENTRE, mib=True) if e['pci'] == PCI), None)
        if cell is None or cell.get('mib') is None:
            return False, 'no MIB'
        got = iqnr.type0_pdcch(iq, FS, CENTRE, cell)
        found = [(d['sfn'], d['slot'], d['level'], d['cce']) for d in got['dcis']]
        # A PDCCH whose slot lies wholly inside the 100 ms span: SFNs 516 (frame -1, its end is in
        # the span) up to 524; 526 starts after it.
        inside = [s for s in sent if s[0] <= (SFN0 + 7) % 1024]
        return (found == inside and len(inside) >= 4 and all(d['bits'] == bits for d in got['dcis'])
                and got['coreset0']['n_rb'] == 48), (found, inside)
    expect("type0_pdcch: every SIB1 PDCCH in the span (AL8 and AL4, 700 Hz off, 0 dB), bits, SFN, slot", end_to_end)

    def cli():
        folder = tempfile.mkdtemp(prefix='iqnr-pdcch-')
        try:
            base = os.path.join(folder, 'nr')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': FS},
                           'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE}]}, fh)
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-nr'),
                                base + '.cfile', '--pdcch'], capture_output=True, text=True, timeout=300)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            e = (out.get('ssbs') or [{}])[0]
            dcis = (e.get('pdcch') or {}).get('dcis') or []
            return (r.returncode == 0 and e.get('mib') is not None and len(dcis) >= 4
                    and all(d['bits'] == bits for d in dcis)), r.stderr[-300:] or e.get('pdcch')
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-nr FILE --pdcch prints each cell's SIB1 DCIs", cli)


def main():
    for group in (check_tables, check_codes, check_pbch, check_search, check_coreset, check_pdcch, check_type0):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
