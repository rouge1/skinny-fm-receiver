"""The CRS check (``iqlte.crs_search`` in ``fm_receiver/iqlte.py``): which PCI an LTE block's reference
signals belong to, at the SSS timing.

Signals: ``tests/lte_synth.py`` (one port) and ``tests/lte_negatives.py``. When
srsRAN 4G's ``pdsch_enodeb`` is built under ``$FMRX_LTE_TOOLS`` (default
/data/python/lte-tools), it also makes a 10 MHz downlink of its own, 1 and 2
ports, a made-up PCI: an implementation of the CRS that shares nothing with
ours. Without it those lines say so and are not counted.

Groups:
  A  the sequences: crs_sequences agrees with lte_synth._crs (two codings of 6.10.1)
  B  one cell: 10 MHz at 20 MS/s, 20, 0 and -10 dB: pci found, chance 1e-300 at
     20 dB; the same at the other half-frame (half_frame 1); 5 and 15 MHz classes
  C  not a cell: a wrong timing (3.13 ms off, not on the symbol grid), and the five negatives (noise, tone,
     wifi_like, atsc_like, uplink_like) at two seeds and two offsets: pci None
  D  two cells at one timing (0 and -6 dB): each confirmed with ``target``; a
     PCI not on air is not
  E  fingerprint: the 'crs' check ran and passed; each cell has its own ``crs``
  F  srsRAN's own waveform (optional): 1 and 2 ports, both half-frames

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_crs.py   (about a minute)
"""

import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqlte  # noqa: E402

from tests import lte_negatives, lte_synth  # noqa: E402

PCI = 123
PCI_B = 200
FAKE_PCI = 217            # the srsRAN file's cell: made up
RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok:
        line += f'  ({detail})'
    print(line)


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def offset_of(iq, fs):
    return iqlte.sss_search(iq, fs)['frame_offset_samples']


def group_sequences():
    def same():
        for n_rb in (25, 50, 75):
            r = iqlte.crs_sequences(n_rb)
            for pci in (0, 1, 123, 503):
                for slot in (0, 7, 19):
                    for i, sym in enumerate((0, 4)):
                        _k, want = lte_synth._crs(pci, slot, sym, n_rb)
                        if not np.allclose(r[pci, slot, i], want):
                            return False, f'n_rb {n_rb} pci {pci} slot {slot} symbol {sym}'
        return True, ''
    expect('A  crs_sequences equals lte_synth._crs (3 widths, 4 PCIs, 3 slots, 2 symbols)', same)


def group_one_cell():
    for snr in (20, 0, -10):
        def case(snr=snr):
            iq, fs = lte_synth.synth_lte(10, 10, PCI, snr, seed=3, fs=20e6)
            r = iqlte.crs_search(iq, fs, offset_of(iq, fs), 1024)
            ok = r['pci'] == PCI and r['half_frame'] == 0
            if snr == 20:
                ok = ok and r['chance'] <= 1e-300
            return ok, f"{r['detail']} chance {r['chance']:.1e}"
        expect(f'B  10 MHz at 20 MS/s, {snr} dB: pci {PCI}, half_frame 0', case)

    def other_half():
        iq, fs = lte_synth.synth_lte(10, 10, PCI, 10, seed=5, fs=20e6)
        off = offset_of(iq, fs) + int(0.005 * fs)
        r = iqlte.crs_search(iq, fs, off, 1024)
        return r['pci'] == PCI and r['half_frame'] == 1, r['detail']
    expect('B  the offset 5 ms later: the same pci, half_frame 1', other_half)

    for bw, nfft in ((5, 512), (15, 1536)):
        def width(bw=bw, nfft=nfft):
            iq, fs = lte_synth.synth_lte(bw, 10, PCI, 10, seed=1)
            r = iqlte.crs_search(iq, fs, offset_of(iq, fs), nfft)
            return r['pci'] == PCI, r['detail']
        expect(f'B  {bw} MHz (nfft {nfft}) at its native rate, 10 dB: pci {PCI}', width)


def group_not_a_cell():
    def wrong_timing():
        iq, fs = lte_synth.synth_lte(10, 10, PCI, 10, seed=3, fs=20e6)
        r = iqlte.crs_search(iq, fs, offset_of(iq, fs) + int(0.0031337 * fs), 1024)
        return r['pci'] is None and r['chance'] > 1e-3, r['detail']
    expect('C  the right cell at a timing 3.13 ms off (off the symbol grid): pci None',
           wrong_timing)

    def negatives():
        bad = []
        for name in ('synth_noise', 'synth_tone', 'synth_wifi_like', 'synth_atsc_like',
                     'synth_uplink_like'):
            for seed in (0, 1):
                x, fs = getattr(lte_negatives, name)(seed=seed)
                for off in (0, 77777):
                    r = iqlte.crs_search(x, fs, off, 1024)
                    if r['pci'] is not None:
                        bad.append(f"{name} seed {seed} offset {off}: {r['detail']}")
        return not bad, '; '.join(bad)
    expect('C  five negatives x 2 seeds x 2 offsets (20 cases): pci None', negatives)


def two_cells():
    cells = [dict(pci=PCI, power_db=0, delay_samples=0),
             dict(pci=PCI_B, power_db=-6, delay_samples=0)]
    return lte_synth.synth_lte_multi(cells, 10, 10, 20, seed=4, fs=20e6)


def group_two_cells():
    iq, fs = two_cells()
    off = offset_of(iq, fs)

    def each():
        a = iqlte.crs_search(iq, fs, off, 1024, exclude=[PCI_B], target=PCI)
        b = iqlte.crs_search(iq, fs, off, 1024, exclude=[PCI], target=PCI_B)
        return (a['pci'] == PCI and b['pci'] == PCI_B and b['best_pci'] == PCI,
                f"{a['detail']} / {b['detail']}")
    expect(f'D  two cells, 0 and -6 dB: {PCI} and {PCI_B} each confirmed; best_pci {PCI}', each)

    def absent():
        r = iqlte.crs_search(iq, fs, off, 1024, target=321)
        return r['pci'] is None, r['detail']
    expect('D  a PCI that is not on air (321): pci None', absent)


def group_fingerprint():
    iq, fs = two_cells()
    fp = iqlte.fingerprint(iq, fs)

    def check():
        crs = [c for c in fp['checks'] if c['name'] == 'crs'][0]
        return crs['ran'] and crs['passed'], crs['detail']
    expect('E  fingerprint: the crs check ran and passed', check)

    def per_cell():
        got = {c['pci']: (c.get('crs') or {}).get('pci') for c in fp['cells']}
        return got == {PCI: PCI, PCI_B: PCI_B}, repr(got)
    expect('E  fingerprint cells: each cell has crs confirming its own PCI', per_cell)


def enodeb():
    """``(tools, program)`` when pdsch_enodeb is built, else None."""
    tools = os.environ.get('FMRX_LTE_TOOLS') or '/data/python/lte-tools'
    prog = os.path.join(tools, 'srsRAN_4G', 'build', 'lib', 'examples', 'pdsch_enodeb')
    return (tools, prog) if os.path.isfile(prog) else None


def make_enodeb(tools, prog, folder, ports):
    """A 10 MHz downlink from srsRAN at 15.36 MS/s, ports summed into one antenna."""
    out = os.path.join(folder, f'enb{ports}.cf32')
    conda = subprocess.run(['conda', 'info', '--base'], capture_output=True, text=True)
    base = conda.stdout.strip()
    env = dict(os.environ)
    lib = os.path.join(base, 'envs', 'srsran', 'lib')
    env['LD_LIBRARY_PATH'] = lib + ':' + os.path.join(tools, 'sctp', 'usr', 'lib',
                                                      'x86_64-linux-gnu')
    subprocess.run([prog, '-o', out, '-n', '6', '-c', str(FAKE_PCI), '-p', '50', '-Q',
                    '-x', str(ports)], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=120)
    a = np.fromfile(out, np.complex64)
    if ports > 1:
        a = a.reshape(-1, ports)
        a = a[:, 0] + a[:, 1] * np.exp(0.7j)
    return a


def group_srsran():
    found = enodeb()
    if found is None:
        print('  note  pdsch_enodeb is not built: the srsRAN waveform lines are not run')
        return
    folder = tempfile.mkdtemp(prefix='crs-enb-')
    try:
        for ports in (1, 2):
            def case(ports=ports):
                a = make_enodeb(found[0], found[1], folder, ports)
                got = []
                for off, half in ((0, 0), (76800, 1)):
                    r = iqlte.crs_search(a, 15.36e6, off, 1024)
                    got.append(r['pci'] == FAKE_PCI and r['half_frame'] == half)
                return all(got), repr(got)
            expect(f'F  srsRAN pdsch_enodeb, {ports} port(s), made-up PCI {FAKE_PCI}: '
                   'found at both half-frames', case)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


GROUPS = [group_sequences, group_one_cell, group_not_a_cell, group_two_cells,
          group_fingerprint, group_srsran]


def main():
    for group in GROUPS:
        try:
            group()
        except Exception as exc:
            report(f'{group.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    failed = [name for name, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    if failed:
        print('failed:')
        for name in failed:
            print(f'  {name}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
