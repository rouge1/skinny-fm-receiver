"""Roadmap Round 28.1: our own NR PBCH decoder (``fm_receiver/iqnr_phy.py``) and ``iq-nr --mib``.

Synthetic only, with made-up values (PCI 123, SSB at 600.15 MHz). The real-air check (every
MIB equal to the outside ``nr_si``'s on the saved NR recordings) is in the ops folder, not here.
What the checks pin:

- the four 38.212 tables: permutations of the right length, unchanged (a hash);
- CRC 24C: a block with its CRC leaves no remainder, and one flipped bit is caught;
- polar code (K 56, N 512, E 864): a noiseless codeword decodes to its bits;
- the PBCH chain on one SSB grid (encode_pbch, ssb_grid, decode_pbch): every MIB field and
  the timing bits come back, for each SSB index and both half-frames, at -2 dB per RE in 19 of
  20 draws, with a common phase; a wrong PCI or noise gives None;
- end to end: ``iqnr.ssb_search(..., mib=True)`` on a synthetic cell whose SSBs carry a real
  PBCH reads the MIB of the first burst, with that burst's SFN, at 10 dB;
- without ``mib`` the entries carry no ``mib`` key (the default output is unchanged);
- ``tools/iq-nr FILE --mib`` prints it; iqnr_phy imports numpy only.

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


def main():
    for group in (check_tables, check_codes, check_pbch, check_search):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
