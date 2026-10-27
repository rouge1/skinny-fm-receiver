"""The SI messages after SIB1 by our own decoder (roadmap Round 29.6): ``iqlte_phy.si_window``,
``si_rv`` and ``si_messages``.

Synthetic only, made-up values (PCI 123, random transport blocks). One 10 MHz cell, 2 ports,
sending its MIB, SIB1 (subframe 5 of even frames, as ``test_iqlte_cancel``) and two SI messages
in 20 ms windows (36.331 5.2.3): message 1 every 8 frames in format 1C, copies at window
subframes 0 (subframe 0: around the PBCH and the synchronisation signals), 4 and 11; message 2
every 16 frames in format 1A, copies at 2 and 9. What the checks pin:

- ``si_window``: each message's window by the formula, the SFN wrap, subframe 5 of even frames
  left out; ``si_rv``: 0, 2, 3, 1 by the subframe's place in its window (36.321 5.3.1);
- at 12 dB every copy of both messages decoded alone, each its own bytes and in its own window
  and place; SIB1 still decodes in every copy;
- copies too weak alone: summed within a window (one transport block a window), each sum its
  message's bytes, a stray DCI of another size first in message 2's windows (a CRC passing by
  chance, as on air) summed apart; noise summed gives nothing;
- ``tools/iq-lte FILE --own`` on a cell sending a SIB1 and an SI message pycrate encoded
  (``test_iqlte_si``'s, made-up values: SIB5 and SIB16 in message 2): ``own`` has the MIB, SIB1's
  fields, its schedule, SIB5 and SIB16 heard and the network time; without pycrate, the MIB
  and a reason for SIB1's fields and the SI. Skipped without a Python that imports pycrate.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_own_si.py   (about two minutes)
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_synth, test_iqlte_cancel as C, test_iqlte_si as SI  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

PCI = 123
N_RB = 50
WINDOW = 20
MESSAGES = [{'si': 1, 'period_frames': 8, 'sibs': ['sib2']}, {'si': 2, 'period_frames': 16, 'sibs': ['sib3']}]
COPIES = {1: (0, 4, 11), 2: (2, 9)}                   # each message's copies, by place in its window
RIV_1C, TBS_INDEX = 5, 7                              # message 1: 1C
RIV_1A, MCS = 120, 4                                  # message 2: 1A on 3 PRBs from 20, N_PRB^1A 3
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


def dci(si, i):
    """The DCI payload and fields of message ``si``'s copy at window place ``i``."""
    if si == 1:
        a = L.dci_1c_si(N_RB, RIV_1C, TBS_INDEX)
        return a, L.parse_dci_1c(a, N_RB), L.si_rv(i)
    rv = (0, 2)[COPIES[2].index(i)]
    a = L.dci_1a_si(N_RB, RIV_1A, MCS, rv, 3, 0)
    return a, L.parse_dci_1a(a, N_RB), rv


def blocks(seed=3):
    rng = np.random.default_rng(seed)
    out = {}
    for si in (1, 2):
        size = L.si_tbs(dci(si, COPIES[si][0])[1])
        out[si] = rng.integers(0, 2, size).astype(np.uint8)
    return out, {si: np.packbits(tb).tobytes().hex() for si, tb in out.items()}


STRAY = 1                                              # message 2's window place of the stray DCI


def cell_res(sib1_tb, si_tbs, stray=False):
    """``test_iqlte_cancel``'s cell (MIB, SIB1) and the SI messages' copies in their windows; with
    ``stray``, at place ``STRAY`` of message 2's windows a 1C DCI of another size and no PDSCH (what a
    16-bit CRC passing by chance looks like)."""
    base = C.cell_res(PCI, sib1_tb)
    other = L.dci_1c_si(N_RB, RIV_1C, 3)

    def res(f):
        out = base(f)
        sfn = (C.SFN0 + f) % 1024
        for sf in range(10):
            hit = L.si_window(MESSAGES, WINDOW, sfn, sf)
            if hit is None or not (hit[1] in COPIES[hit[0]] or (stray and hit[:2] == (2, STRAY))):
                continue
            si, i, _start = hit
            a, fields, rv = dci(si, i) if i in COPIES[si] else (other, L.parse_dci_1c(other, N_RB), L.si_rv(i))
            cfi = 2
            for p, l, ks, vals in L.control_res(PCI, N_RB, 2, cfi, '1', 'normal', sf, [(a, 4, 0)]):
                out.append((p, 14 * sf + l, ks, vals))
            if i not in COPIES[si]:
                continue
            for p, ls, ks, vals in L.pdsch_res_values(si_tbs[si], PCI, N_RB, 2, cfi, sf,
                                                      L.si_allocation(fields, N_RB), rv):
                for l in np.unique(ls):
                    out.append((p, 14 * sf + int(l), ks[ls == l], vals[ls == l]))
        return out
    return res


def capture(snr_db, n_frames=36, seed=4, stray=False):
    rng = np.random.default_rng(11)
    sib1_tb = rng.integers(0, 2, 256).astype(np.uint8)
    si_tbs, hexes = blocks()
    cell = dict(pci=PCI, power_db=0.0, delay_samples=0, ports=2,
                port_gains=[1.0, 0.7 * np.exp(1.1j)], res=cell_res(sib1_tb, si_tbs, stray))
    iq, fs = lte_synth.synth_lte_multi([cell], bw_mhz=10, n_frames=n_frames, snr_db=snr_db, seed=seed,
                                       cfo_hz=250.0, idle=True)
    return iq, fs, np.packbits(sib1_tb).tobytes().hex(), hexes


def read(iq, fs):
    c = [c for c in iqlte.cells(iq, fs) if c['pci'] == PCI][0]
    pb, d, s = C.decode(iq, fs, c)
    return pb, s, L.si_messages(iq, fs, c, pb, MESSAGES, WINDOW, carrier_hz=700e6)


def check_window():
    def where():
        want = {(0, 0): (1, 0, 0), (1, 9): (1, 19, 0), (2, 0): (2, 0, 20), (8, 3): (1, 3, 80), (2, 1): (2, 1, 20),
                (3, 9): (2, 19, 20), (18, 4): (2, 4, 180), (4, 2): None, (0, 5): None, (1, 5): (1, 15, 0),
                (1023, 0): None, (1016, 0): (1, 0, 10160)}
        bad = {k: L.si_window(MESSAGES, WINDOW, *k) for k in want if L.si_window(MESSAGES, WINDOW, *k) != want[k]}
        return not bad, bad
    expect("si_window: each message's window and place (36.331 5.2.3), subframe 5 of even frames out", where)

    def rvs():
        got = [L.si_rv(i) for i in range(10)]
        return got == [0, 2, 3, 1, 0, 2, 3, 1, 0, 2], got
    expect("si_rv: 0, 2, 3, 1 by the subframe's place in the window (36.321 5.3.1)", rvs)


def check_alone():
    iq, fs, sib1_hex, hexes = capture(12.0)
    state = {}

    def every():
        pb, s, si = read(iq, fs)
        state['si'] = si
        got = [(b['si'], b['i'], b.get('hex') == hexes[b['si']]) for b in si['blocks']]
        # 36 frames from SFN 516: message 1's windows at 520, 528, 536, 544, message 2's at 530, 546.
        ok = (si['decoded'] == si['tried'] == len(got) == 4 * 3 + 2 * 2
              and all(ok and i in COPIES[m] for m, i, ok in got)
              and s['decoded'] == s['tried'] >= 8 and s['sib1'] == sib1_hex)
        return ok, (si['decoded'], si['tried'], got, s and (s['decoded'], s['tried']))
    expect("12 dB: every SI copy decoded alone, its own bytes, window and place; SIB1 in every copy", every)

    def subframe0():
        b = [b for b in state['si']['blocks'] if b['subframe'] == 0]
        return bool(b) and all(x['crc'] for x in b) and {x['format'] for x in state['si']['blocks']} == {'1A', '1C'}, b
    expect("a copy in subframe 0 (around the PBCH) decodes; formats 1A and 1C both read", subframe0)


def check_summed():
    iq, fs, _sib1_hex, hexes = capture(-9.0, n_frames=36, stray=True)

    def summed():
        _pb, _s, si = read(iq, fs)
        alone = [b for b in si['blocks'] if b['crc']]
        sums = si['combined']
        strays = [b for b in si['blocks'] if b['si'] == 2 and b['i'] == STRAY]
        two = {c['subframes'][0] for c in sums if c['si'] == 2}
        ok = (bool(sums) and all(c['hex'] == hexes[c['si']] for c in sums)
              and len(alone) < si['tried'] and {c['si'] for c in sums} == {1, 2}
              and strays and not any(b['crc'] for b in strays) and len(two) == 2)
        return ok, (si['decoded'], si['tried'], len(strays), [(c['si'], c['subframes']) for c in sums])
    expect("-9 dB: copies that fail alone summed in their window, each sum its message's bytes, "
           "a stray DCI of another size first in the window summed apart", summed)

    def noise():
        rng = np.random.default_rng(5)
        x = (rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)).astype(np.complex64)
        c = [c for c in iqlte.cells(iq, fs) if c['pci'] == PCI][0]
        pb = L.pbch(iq, fs, c, carrier_hz=700e6)
        si = L.si_messages(x, fs, c, pb, MESSAGES, WINDOW, carrier_hz=700e6)
        return si['decoded'] == 0 and not si['combined'], (si['decoded'], si['combined'])
    expect("noise read at the same timing: no SI block, alone or summed", noise)


def padded(hx, size):
    """``hx``'s bits, zeros after them up to ``size`` (UPER ignores what follows)."""
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(hx), dtype=np.uint8))
    return np.concatenate([bits, np.zeros(size - bits.size, dtype=np.uint8)])


def cli_capture(sib1_hex, si_hex):
    """40 frames of the cell sending ``sib1_hex`` and, in message 2's windows (copies at 2 and 9,
    format 1A on 8 PRBs), ``si_hex``; SIB2's message 1 is scheduled and not sent."""
    need = len(si_hex) * 4
    mcs = next(m for m in range(27) if L.TBS_1A[3][m] >= need)

    def dci2(i):
        rv = (0, 2)[COPIES[2].index(i)]
        a = L.dci_1a_si(N_RB, 7 * N_RB + 20, mcs, rv, 3, 0)
        return a, L.parse_dci_1a(a, N_RB), rv
    si_tb = padded(si_hex, L.TBS_1A[3][mcs])
    schedule = [{'si': 1, 'period_frames': 8}, {'si': 2, 'period_frames': 16}]
    base = C.cell_res(PCI, padded(sib1_hex, 256))

    def res(f):
        out = base(f)
        sfn = (C.SFN0 + f) % 1024
        for sf in range(10):
            hit = L.si_window(schedule, WINDOW, sfn, sf)
            if hit is None or hit[0] != 2 or hit[1] not in COPIES[2]:
                continue
            a, fields, rv = dci2(hit[1])
            for p, l, ks, vals in L.control_res(PCI, N_RB, 2, 2, '1', 'normal', sf, [(a, 4, 0)]):
                out.append((p, 14 * sf + l, ks, vals))
            for p, ls, ks, vals in L.pdsch_res_values(si_tb, PCI, N_RB, 2, 2, sf, L.si_allocation(fields, N_RB), rv):
                for l in np.unique(ls):
                    out.append((p, 14 * sf + int(l), ks[ls == l], vals[ls == l]))
        return out
    cell = dict(pci=PCI, power_db=0.0, delay_samples=0, ports=2, port_gains=[1.0, 0.7 * np.exp(1.1j)], res=res)
    return lte_synth.synth_lte_multi([cell], bw_mhz=10, n_frames=40, snr_db=12.0, seed=4, cfo_hz=250.0, idle=True)


def run_own(iq, fs, tools):
    folder = tempfile.mkdtemp(prefix='iqlte-own-')
    try:
        base = os.path.join(folder, 'lte')
        iq.astype(np.complex64).tofile(base + '.cfile')
        with open(base + '.sigmf-meta', 'w') as fh:
            json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                       'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile', '--own'],
                           capture_output=True, text=True, timeout=900, env=dict(os.environ, FMRX_LTE_TOOLS=tools))
        return (json.loads(r.stdout) if r.returncode == 0 else {}), r.stderr[-300:]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_cli():
    py = SI.pycrate_python()
    if py is None:
        print('  SKIP iq-lte --own end to end (no Python that imports pycrate)')
        return
    tools = tempfile.mkdtemp(prefix='iqlte-own-tools-')
    try:
        proc = subprocess.run([py, '-c', SI.ENCODER, str(SI.UTC_10MS)], capture_output=True, timeout=60)
        if proc.returncode != 0:
            report('encode SIB1 and an SI message with pycrate', False, proc.stderr.decode(errors='replace')[-300:])
            return
        sib1_hex, si_hex = json.loads(proc.stdout)
        os.makedirs(os.path.join(tools, 'pyc', 'bin'))
        wrapper = os.path.join(tools, 'pyc', 'bin', 'python')          # a wrapper: a venv finds its packages
        with open(wrapper, 'w') as fh:
            fh.write('#!/bin/sh\nexec %s "$@"\n' % shlex.quote(py))
        os.chmod(wrapper, 0o755)
        iq, fs = cli_capture(sib1_hex, si_hex)

        def own():
            card, err = run_own(iq, fs, tools)
            o = card.get('own') or {}
            si = o.get('si') or {}
            ok = (o.get('ran') and (o.get('mib') or {}).get('pci') == PCI and (o.get('sib1') or {}).get('tac') == 0x1234
                  and [m['sibs'] for m in si.get('schedule') or []] == [['sib2'], ['sib5', 'sib16']]
                  and set(si.get('sibs') or {}) == {'sib5', 'sib16'} and si['sibs']['sib16']['count'] >= 4
                  and (si.get('time') or {}).get('utc') == SI.UTC_WANT and si.get('value_tag') == 3)
            return ok, (err, {k: o.get(k) for k in ('mib', 'si_reason', 'sib1_reason')},
                        si and {k: si.get(k) for k in ('blocks', 'blocks_failed', 'time_reason')})
        expect("iq-lte --own: the MIB, SIB1's fields and schedule, SIB5 and SIB16 heard, the network time", own)

        def without():
            card, err = run_own(iq, fs, os.path.join(tools, 'none'))
            o = card.get('own') or {}
            return ((o.get('mib') or {}).get('pci') == PCI and o.get('sib1') is None and o.get('si') is None
                    and 'pycrate' in str(o.get('sib1_reason')) and 'pycrate' in str(o.get('si_reason'))), (err, o)
        expect("iq-lte --own without pycrate: the MIB, and a reason for SIB1's fields and the SI", without)
    finally:
        shutil.rmtree(tools, ignore_errors=True)


def main():
    check_window()
    check_alone()
    check_summed()
    check_cli()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} ok, {len(failed)} FAIL of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
