"""TDD in the LTE fingerprint: ``iqlte.cells`` places the SSS three symbols before
the PSS when the FDD position gives nothing, and ``fingerprint`` reports ``duplex``.

Signals: ``tests/lte_synth.py`` with ``duplex='tdd'`` (uplink-downlink
configuration 1, special subframe 7: the uplink and guard symbols silent) and
``tests/lte_negatives.py``. srsRAN's ``pdsch_enodeb`` has no TDD mode, so there is
no second implementation here: the frame timing is checked against the FDD
signal of the same seed (same frame offset) and by the CRS, which only lines up
at the right slot numbers.

Groups:
  A  one TDD cell, 10 MHz at 20 MS/s, 20 and 0 dB: cells gives pci 123, duplex
     TDD, the FDD signal's frame offset; the CRS confirms it at that timing
  B  fingerprint on TDD: duplex 'TDD', pci 123, the duplex and crs checks passed;
     on FDD: duplex 'FDD' and every cell FDD
  C  two TDD cells at one timing (0 and -6 dB): both found, both TDD
  D  5 and 20 MHz TDD at their native rates: pci 123, TDD
  E  negatives (noise, tone, wifi_like, atsc_like, uplink_like): no cells, duplex None
  F  tools/iq-lte on a TDD recording: the card says TDD, and --decode refuses it
     with the reason (no outside tools are called)

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_tdd.py   (about a minute)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqlte  # noqa: E402
from tests import lte_negatives, lte_synth  # noqa: E402

PCI = 123
PCI_B = 200
FS = 20e6
CENTRE = 751.0e6
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


def summary(found):
    return [(c['pci'], c.get('duplex'), c['frame_offset_samples']) for c in found]


def group_one_cell():
    for snr in (20, 0):
        def case(snr=snr):
            tdd, fs = lte_synth.synth_lte(10, 10, PCI, snr, seed=3, fs=FS, duplex='tdd')
            fdd, _fs = lte_synth.synth_lte(10, 10, PCI, snr, seed=3, fs=FS)
            want = iqlte.cells(fdd, fs)[0]['frame_offset_samples']
            got = iqlte.cells(tdd, fs)
            if not got:
                return False, 'no cells'
            top = got[0]
            crs = iqlte.crs_search(tdd, fs, top['frame_offset_samples'], 1024, target=PCI)
            ok = (top['pci'] == PCI and top['duplex'] == 'TDD'
                  and top['frame_offset_samples'] == want
                  and crs['pci'] == PCI and crs['half_frame'] == 0)
            return ok, f'{summary(got)}; FDD offset {want}; crs {crs["detail"]}'
        expect(f'A  TDD 10 MHz, {snr} dB: pci {PCI}, TDD, the FDD frame offset, CRS agrees', case)


def group_fingerprint():
    def tdd():
        iq, fs = lte_synth.synth_lte(10, 10, PCI, 10, seed=2, fs=FS, duplex='tdd')
        fp = iqlte.fingerprint(iq, fs)
        checks = {c['name']: c for c in fp['checks']}
        ok = (fp['duplex'] == 'TDD' and fp['pci'] == PCI
              and checks['duplex']['passed'] and checks['crs']['passed'])
        return ok, (f"duplex {fp['duplex']} pci {fp['pci']} duplex check "
                    f"{checks['duplex']['detail']}; crs {checks['crs']['detail']}")
    expect(f"B  fingerprint on TDD: duplex 'TDD', pci {PCI}, duplex and crs checks passed", tdd)

    def fdd():
        iq, fs = lte_synth.synth_lte(10, 10, PCI, 10, seed=2, fs=FS)
        fp = iqlte.fingerprint(iq, fs)
        ok = (fp['duplex'] == 'FDD' and fp['pci'] == PCI
              and all(c.get('duplex') == 'FDD' for c in fp['cells']))
        return ok, f"duplex {fp['duplex']} cells {summary(fp['cells'])}"
    expect("B  fingerprint on FDD: duplex 'FDD', every cell FDD", fdd)


def group_two_cells():
    def case():
        cells = [dict(pci=PCI, power_db=0, delay_samples=0),
                 dict(pci=PCI_B, power_db=-6, delay_samples=0)]
        iq, fs = lte_synth.synth_lte_multi(cells, 10, 10, 20, seed=4, fs=FS, duplex='tdd')
        got = iqlte.cells(iq, fs)
        pcis = {c['pci'] for c in got}
        ok = pcis == {PCI, PCI_B} and all(c['duplex'] == 'TDD' for c in got)
        return ok, repr(summary(got))
    expect(f'C  two TDD cells (0 and -6 dB): {PCI} and {PCI_B}, both TDD', case)


def group_widths():
    for bw in (5, 20):
        def case(bw=bw):
            iq, fs = lte_synth.synth_lte(bw, 10, PCI, 10, seed=1, duplex='tdd')
            got = iqlte.cells(iq, fs)
            ok = bool(got) and got[0]['pci'] == PCI and got[0]['duplex'] == 'TDD'
            return ok, repr(summary(got))
        expect(f'D  TDD {bw} MHz at its native rate: pci {PCI}, TDD', case)


def group_negatives():
    def case():
        bad = []
        for name in ('synth_noise', 'synth_tone', 'synth_wifi_like', 'synth_atsc_like',
                     'synth_uplink_like'):
            x, fs = getattr(lte_negatives, name)(seed=0)
            fp = iqlte.fingerprint(x, fs)
            if fp['cells'] or fp['duplex'] is not None:
                bad.append(f"{name}: duplex {fp['duplex']} cells {summary(fp['cells'])}")
        return not bad, '; '.join(bad)
    expect('E  five negatives: no cells, duplex None', case)


def group_tool():
    folder = tempfile.mkdtemp(prefix='lte-tdd-')
    try:
        iq, fs = lte_synth.synth_lte(10, 10, PCI, 15, seed=6, fs=FS, duplex='tdd')
        path = os.path.join(folder, 'tdd.cfile')
        iq.tofile(path)
        tools = os.path.join(folder, 'no-tools')
        os.makedirs(tools)
        env = dict(os.environ, FMRX_LTE_TOOLS=tools, TMPDIR=folder)
        cmd = [sys.executable, os.path.join(ROOT, 'tools', 'iq-lte'), path,
               '--rate', str(int(FS)), '--center', str(int(CENTRE)), '--t1', '0.1', '--decode']
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)

        def card():
            c = json.loads(proc.stdout)
            return c.get('duplex') == 'TDD' and c.get('pci') == PCI, \
                f"exit {proc.returncode} duplex {c.get('duplex')} pci {c.get('pci')}"
        expect(f"F  iq-lte on a TDD recording: duplex 'TDD', pci {PCI}", card)

        def refused():
            c = json.loads(proc.stdout)
            dec = c.get('decode') or {}
            return (proc.returncode == 0 and dec.get('ran') is False
                    and 'TDD' in str(dec.get('reason'))), f"exit {proc.returncode} decode {dec}"
        expect('F  iq-lte --decode on TDD: not run, the reason names TDD, exit 0', refused)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


GROUPS = [group_one_cell, group_fingerprint, group_two_cells, group_widths,
          group_negatives, group_tool]


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
