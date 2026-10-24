"""The card's ``lte`` and ``nr`` sections and their classes (CONTRACTS-measure.md, 2026-10-24).

Every signal is synthetic and seeded (``lte_synth``, ``nr_synth``, ``lte_negatives``), with
made-up cells: PCI 123 (LTE) and PCI 321 (NR), around 600 MHz. What the checks pin:

- the classes: 'lte' for an LTE downlink, 'nr' for an NR SSB, 'lte+nr' for both in one span,
  and neither for noise or an ATSC-like signal (their sections are searched and empty);
- a found cell beats the noise rule (a band of carriers measures under 6 dB of SNR);
- the LTE carrier is looked for at the station of the ``.sigmf-meta`` (an off-centre
  Receive-tab recording), else at the occupied centre on the 100 kHz raster;
- the gates: under 1.92 MS/s, or an occupied width under 1 MHz, nothing is searched and the
  reason says which;
- only the first 50 ms is searched, whatever the span;
- after a cell search iqmeasure still imports none of gnuradio, PyQt5, scipy, library,
  radios, dsp (a clean subprocess);
- ``iq-picture --cells``: ``cell_boxes`` puts an LTE box on the synthesiser's PSS and SSS
  (within one symbol, modulo 5 ms) and an NR box on its SSB (within 20 us), at the right
  frequencies; the tool writes a PNG for a cell and for noise.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure_cells.py
(about a minute)
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_negatives, lte_synth, nr_synth  # noqa: E402
from fm_receiver import iqmeasure  # noqa: E402

FS = 20e6
CENTRE = 600.0e6
LTE_PCI = 123
NR_PCI = 321
BANNED = ['gnuradio', 'PyQt5', 'scipy', 'library', 'radios', 'dsp']
FOLDER = None
RESULTS = []
MEMO = {}


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok:
        line += f"  ({detail})"
    print(line)


def expect(name, test):
    """Run ``test()``, which returns ``(ok, detail)``. An exception is a failure."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


def memo(key, make):
    if key not in MEMO:
        MEMO[key] = make()
    return MEMO[key]


def shift(iq, hz, rate=FS):
    return (iq * np.exp(2j * np.pi * hz * np.arange(len(iq)) / rate)).astype(np.complex64)


def write(name, iq, station=None):
    """``iq`` as a .cfile at FS, centre CENTRE, with its .sigmf-meta (and station, if given)."""
    base = os.path.join(FOLDER, name)
    iq.astype(np.complex64).tofile(base + '.cfile')
    glob = {'core:datatype': 'cf32_le', 'core:sample_rate': FS}
    if station is not None:
        glob['fmrx:station_frequency'] = float(station)
    with open(base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': glob, 'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE}],
                   'annotations': []}, fh)
    return base + '.cfile'


def lte_iq(bw=10, frames=10, pci=LTE_PCI, seed=0):
    iq, _ = lte_synth.synth_lte(bw_mhz=bw, n_frames=frames, pci=pci, snr_db=20.0, seed=seed, fs=FS)
    return iq


def nr_iq(bw=5, offset=0.15e6, seed=1):
    """An NR cell whose SSB is ``offset`` above its carrier's centre."""
    iq, _, _ = nr_synth.synth_nr(pci=NR_PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                 period_ms=20, n_hf=0, bw_mhz=bw, ssb_offset_hz=offset,
                                 dur_ms=100, fs=FS, seed=seed)
    return iq


def card(key):
    def make():
        if key == 'lte':
            return iqmeasure.measure(lte_iq(), FS, CENTRE)
        if key == 'nr':
            return iqmeasure.measure(nr_iq(bw=20), FS, CENTRE)
        if key == 'both':
            # LTE 5 MHz at 595.0 MHz (the station), NR 5 MHz at 604.8 MHz, its SSB at 604.95
            # MHz (a sync-raster point), each 10 dB over its own noise, summed.
            lte = shift(lte_iq(bw=5), -5.0e6)
            nr = shift(nr_iq(bw=5), 4.8e6)
            n = min(len(lte), len(nr))
            return iqmeasure.measure_file(write('both', lte[:n] + nr[:n], station=595.0e6))
        if key == 'station':
            # Recorded 300 kHz under the station, as the Receive tab records.
            return iqmeasure.measure_file(write('station', shift(lte_iq(), 0.3e6), station=600.3e6))
        if key == 'raster':
            return iqmeasure.measure_file(write('raster', shift(lte_iq(), 0.3e6)))
        if key == 'noise':
            iq, _ = lte_negatives.synth_noise(seed=0, fs=FS, dur=0.1)
            return iqmeasure.measure(iq, FS, CENTRE)
        if key == 'atsc':
            iq, _ = lte_negatives.synth_atsc_like(seed=0, fs=FS, dur=0.1)
            return iqmeasure.measure(iq, FS, CENTRE)
        if key == 'slow':
            rate = 1e6
            rng = np.random.default_rng(3)
            iq = rng.standard_normal(100000) + 1j * rng.standard_normal(100000)
            return iqmeasure.measure(iq.astype(np.complex64), rate, CENTRE)
        if key == 'narrow':
            t = np.arange(int(0.05 * FS)) / FS
            rng = np.random.default_rng(4)
            iq = np.exp(2j * np.pi * 1e6 * t) + 0.01 * (rng.standard_normal(t.size)
                                                       + 1j * rng.standard_normal(t.size))
            return iqmeasure.measure(iq.astype(np.complex64), FS, CENTRE)
        if key == 'long':
            return iqmeasure.measure(lte_iq(frames=30), FS, CENTRE)
        raise KeyError(key)
    return memo(key, make)


def lte_pcis(c):
    return sorted(x['pci'] for x in c['lte'].get('cells') or [])


def nr_pcis(c):
    return sorted(x['pci'] for x in c['nr'].get('ssbs') or [])


def check_classes():
    expect("an LTE downlink: class 'lte', PCI 123",
           lambda: (card('lte')['class'] == 'lte' and lte_pcis(card('lte')) == [LTE_PCI],
                    f"class {card('lte')['class']!r}, cells {lte_pcis(card('lte'))}"))
    expect("an NR SSB at 600.15 MHz: class 'nr', PCI 321",
           lambda: (card('nr')['class'] == 'nr' and nr_pcis(card('nr')) == [NR_PCI]
                    and abs(card('nr')['nr']['ssbs'][0]['ssb_hz'] - 600.15e6) < 1e3,
                    f"class {card('nr')['class']!r}, ssbs {card('nr')['nr']['ssbs']}"))
    expect("LTE and NR in one span: class 'lte+nr', both PCIs",
           lambda: (card('both')['class'] == 'lte+nr' and lte_pcis(card('both')) == [LTE_PCI]
                    and nr_pcis(card('both')) == [NR_PCI],
                    f"class {card('both')['class']!r}, lte {lte_pcis(card('both'))}, "
                    f"nr {nr_pcis(card('both'))}"))
    expect("the evidence names the PCIs it found",
           lambda: ('PCI 123' in card('both')['evidence'][0] and 'PCI 321' in card('both')['evidence'][0],
                    card('both')['evidence']))
    expect("a found cell beats the noise rule (an NR carrier filling the span: SNR under 6 dB)",
           lambda: (card('nr')['level']['snr_db'] < 6 and card('nr')['class'] == 'nr',
                    f"class {card('nr')['class']!r}, snr {card('nr')['level']['snr_db']}"))


def check_negatives():
    for key in ('noise', 'atsc'):
        expect(f"{key}: searched, no cell, class not lte or nr",
               lambda k=key: (card(k)['lte']['searched'] and card(k)['nr']['searched']
                              and not lte_pcis(card(k)) and not nr_pcis(card(k))
                              and card(k)['class'] not in ('lte', 'nr', 'lte+nr'),
                              f"class {card(k)['class']!r}, lte {card(k)['lte']}, nr {card(k)['nr']}"))


def check_centring():
    expect("an off-centre recording: the LTE carrier is looked for at the station (600.3 MHz)",
           lambda: (card('station')['lte']['block_center_hz'] == 600.3e6
                    and card('station')['class'] == 'lte',
                    f"block {card('station')['lte']['block_center_hz']}, class {card('station')['class']!r}"))
    expect("without a station: at the occupied centre on the 100 kHz raster (600.3 MHz)",
           lambda: (card('raster')['lte']['block_center_hz'] == 600.3e6
                    and card('raster')['class'] == 'lte',
                    f"block {card('raster')['lte']['block_center_hz']}, class {card('raster')['class']!r}"))


def check_gates():
    expect("under 1.92 MS/s: not searched, the reason names the rate",
           lambda: (card('slow')['lte'] == card('slow')['nr'] and not card('slow')['lte']['searched']
                    and 'MS/s' in card('slow')['lte']['reason'], card('slow')['lte']))
    expect("an occupied width under 1 MHz: not searched, the reason says so",
           lambda: (not card('narrow')['lte']['searched'] and not card('narrow')['nr']['searched']
                    and 'width' in card('narrow')['lte']['reason'], card('narrow')['lte']))
    expect("a 300 ms span: only the first 50 ms is searched",
           lambda: (abs(card('long')['lte']['span_s'] - 0.05) < 1e-6
                    and abs(card('long')['nr']['span_s'] - 0.05) < 1e-6
                    and card('long')['class'] == 'lte',
                    f"spans {card('long')['lte']['span_s']}, {card('long')['nr']['span_s']}"))
    expect("the card stays JSON-safe with the cell sections",
           lambda: (json.loads(json.dumps(card('both'), allow_nan=False)) == card('both'), 'round trip'))


def check_imports():
    def run():
        path = write('imports', lte_iq(frames=5))
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from fm_receiver import iqmeasure\n"
                "c = iqmeasure.measure_file(%r)\n"
                "assert c['lte']['searched'], c['lte']\n"
                "print(sorted(m for m in %r if m in sys.modules or any(k.startswith(m + '.') "
                "for k in sys.modules)))\n" % (os.path.dirname(HERE), path, BANNED))
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=300)
        got = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-300:]
        return got == '[]', got
    expect("after a cell search iqmeasure imports none of " + ', '.join(BANNED), run)


def picture():
    loader = importlib.machinery.SourceFileLoader(
        'iq_picture', os.path.join(os.path.dirname(HERE), 'iq-picture'))
    spec = importlib.util.spec_from_loader('iq_picture', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def check_picture():
    pic = memo('picture', picture)

    def lte_box():
        path = memo('lte-file', lambda: write('lte-file', lte_iq()))
        _iq, native, offset, _rng = lte_synth._cell_native(10, 10, LTE_PCI, 0)
        lte, nr = iqmeasure.cells_file(path)
        boxes = [b for b in pic.cell_boxes(lte, nr, 0.0, 0.02) if b['kind'] == 'lte']
        want = (offset / native + 10976 / 30.72e6) % 5e-3
        got = [b['t0'] % 5e-3 for b in boxes]
        err = min((min(abs(g - want), 5e-3 - abs(g - want)) for g in got), default=None)
        freq = all(abs((b['f_lo'] + b['f_hi']) / 2 - CENTRE) < 1 and abs(b['f_hi'] - b['f_lo'] - 1.08e6) < 1
                   for b in boxes)
        return (len(boxes) == 4 and err is not None and err < 71e-6 and freq,
                f"{len(boxes)} boxes, timing error {err}, want {want}, got {got[:2]}")
    expect("LTE: a box on the PSS and SSS every 5 ms, within a symbol, 1.08 MHz wide", lte_box)

    def nr_box():
        iq, _, truth = nr_synth.synth_nr(pci=NR_PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                         period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                         dur_ms=100, fs=FS, seed=0)
        path = write('nr-file', iq)
        lte, nr = iqmeasure.cells_file(path)
        boxes = [b for b in pic.cell_boxes(lte, nr, 0.0, 0.05) if b['kind'] == 'nr']
        want = [t for t in truth['ssb_times_s'] if t < 0.05]
        ok = (len(boxes) == len(want) and all(abs(b['t0'] - t) < 20e-6 for b, t in zip(boxes, want))
              and all(abs(b['f_lo'] - (600.15e6 - 1.8e6)) < 1e3 and abs(b['f_hi'] - (600.15e6 + 1.8e6)) < 1e3
                      for b in boxes))
        return ok, f"boxes {[(round(b['t0'], 6), b['f_lo'], b['f_hi']) for b in boxes]}, truth {want}"
    expect("NR: a box on each SSB, within 20 us, 240 subcarriers around SS_REF", nr_box)

    def renders():
        out = []
        for name in ('lte-file', 'noise-file'):
            path = memo(name, lambda n=name: write(n, lte_iq() if n == 'lte-file' else
                                                   lte_negatives.synth_noise(seed=0, fs=FS, dur=0.1)[0]))
            png = os.path.join(FOLDER, name + '.png')
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-picture'),
                                path, png, '--cells'], capture_output=True, text=True, timeout=300)
            with open(png, 'rb') as fh:
                head = fh.read(8) if r.returncode == 0 else b''
            out.append((r.returncode, head == b'\x89PNG\r\n\x1a\n', r.stderr[-200:]))
        return all(code == 0 and png for code, png, _ in out), out
    expect("iq-picture --cells writes a PNG for a cell and for noise", renders)


GROUPS = [check_classes, check_negatives, check_centring, check_gates, check_imports, check_picture]


def main():
    global FOLDER
    FOLDER = tempfile.mkdtemp(prefix='iqmeasure-cells-')
    try:
        for group in GROUPS:
            try:
                group()
            except Exception as exc:
                report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(FOLDER, ignore_errors=True)
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
