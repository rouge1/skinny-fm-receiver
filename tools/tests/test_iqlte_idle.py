"""The PSS check on a quiet carrier: ``iqlte.fingerprint`` when most symbols are empty.

On a real carrier with little traffic the data symbols are nearly empty, so
the folded prefix profile is carried by the reference and control symbols, and
its peaks are broad humps tens of samples wide (several cells, echoes). With
the prefix peaks kept only half a prefix apart, all seven landed on two or
three symbols, the PSS symbol was never tried, and a quiet carrier came out
``possible`` with no PCI although ``cells`` found its cells (one cell or two).
The peaks are now kept half a symbol apart. Every check here fails with the
old spacing.

Signals: ``tests/lte_synth.py`` with ``idle=True`` (QPSK in the first three
symbols of each subframe only). Made-up PCIs.

Groups:
  A  the prefix peaks of an idle carrier sit at least half a symbol apart
  B  one idle cell at 10 and 0 dB: lte, pci 123
  C  two idle cells (0 and -10 dB) at one timing, and 24 samples apart (-8 dB),
     at 10 and 3 dB: lte, pci 123, and cells has both
  D  one idle cell with echoes at 20 and 40 samples, 10 dB: lte, pci 123

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_idle.py   (about 30 s)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqlte  # noqa: E402
from tests import lte_synth  # noqa: E402

PCI = 123
PCI_B = 200
FS = 20e6
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


def verdict(fp):
    pss = [c for c in fp['checks'] if c['name'] == 'pss'][0]
    return f"{fp['verdict']} pci {fp['pci']} pss chance {pss['chance']:.1e} cells {[c['pci'] for c in fp['cells']]}"


def group_peaks():
    def case():
        iq, fs = lte_synth.synth_lte_multi(
            [dict(pci=PCI, power_db=0, delay_samples=0),
             dict(pci=PCI_B, power_db=-10, delay_samples=0)], 10, 10, 10, seed=4, fs=FS, idle=True)
        block, _rate, n_fft, _fs_in = iqlte._prepare(iq, fs)
        _align, slot, cp, _n, folded = iqlte._cp_alignment(block, n_fft)
        peaks = sorted(iqlte._prefix_peaks(folded, 7, iqlte._symbol_guard(n_fft, cp)))
        gaps = [(b - a) for a, b in zip(peaks, peaks[1:] + [peaks[0] + slot])]
        return len(peaks) == 7 and min(gaps) > (n_fft + cp) // 2, f'peaks {peaks} gaps {gaps}'
    expect('A  idle carrier: seven prefix peaks, each more than half a symbol apart', case)


def group_one_cell():
    for snr in (10, 0):
        def case(snr=snr):
            iq, fs = lte_synth.synth_lte(10, 10, PCI, snr, seed=3, fs=FS, idle=True)
            fp = iqlte.fingerprint(iq, fs)
            return fp['verdict'] == 'lte' and fp['pci'] == PCI, verdict(fp)
        expect(f'B  one idle cell, {snr} dB: lte, pci {PCI}', case)


def group_two_cells():
    layouts = (('at one timing, -10 dB', -10, 0), ('24 samples apart, -8 dB', -8, 24))
    for label, power, delay in layouts:
        for snr in (10, 3):
            def case(power=power, delay=delay, snr=snr):
                cells = [dict(pci=PCI, power_db=0, delay_samples=0),
                         dict(pci=PCI_B, power_db=power, delay_samples=delay)]
                iq, fs = lte_synth.synth_lte_multi(cells, 10, 10, snr, seed=4, fs=FS, idle=True)
                fp = iqlte.fingerprint(iq, fs)
                found = {c['pci'] for c in fp['cells']}
                ok = fp['verdict'] == 'lte' and fp['pci'] == PCI and found == {PCI, PCI_B}
                return ok, verdict(fp)
            expect(f'C  two idle cells {label}, {snr} dB: lte, pci {PCI}, both in cells', case)


def group_echoes():
    def case():
        cells = [dict(pci=PCI, power_db=0, delay_samples=0),
                 dict(pci=PCI, power_db=-3, delay_samples=20),
                 dict(pci=PCI, power_db=-6, delay_samples=40)]
        iq, fs = lte_synth.synth_lte_multi(cells, 10, 10, 10, seed=4, fs=FS, idle=True)
        fp = iqlte.fingerprint(iq, fs)
        return fp['verdict'] == 'lte' and fp['pci'] == PCI, verdict(fp)
    expect(f'D  one idle cell with echoes at 20 and 40 samples, 10 dB: lte, pci {PCI}', case)


GROUPS = [group_peaks, group_one_cell, group_two_cells, group_echoes]


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
