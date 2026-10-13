"""Phase 3: spectrum recordings - a sweep DVR (``.sdvr`` of tiles) and a
Sceptre ``.fft`` - in the Recordings tab, played without sound.

Synthetic files only. It must:

- list them from the folders added, under their own title, one recording each
  (an IQ DVR is still one IQ track, not also a spectrum one);
- draw them with ``library.render`` in dBm, as ``spectra`` reads them, NaN
  outside the file;
- in the window: play with no engine and no radio - the playhead on a timer,
  the waterfall running, the level in dBm - pause, seek, and Loop;
- never write beside them or delete them.

Run:  python tools/tests/test_sceptre_spectra.py     (about 30 s)
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-spectra-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import library, spectra  # noqa: E402
from fm_receiver.widgets import HEADER_ROLE  # noqa: E402
from tests import signals  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
BINS, TILES, SWEEPS, INTERVAL = 4096, 8, 16, 0.2325
FFT_ROWS = 12                                     # 2.8 s: short enough to loop in a test


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def spectrum():
    k = np.arange(BINS)
    db = -95.0 + 6.0 * np.sin(k / 97.0)
    for bin_, level in ((600, -65.0), (1500, -70.0), (3000, -75.0)):
        db[bin_] = level
    return np.clip(db, -105.0, -62.0)


def build(folder):
    signals.ensure_dir(folder)
    sweep = os.path.join(folder, 'sweep.sdvr')
    signals.write_sweep_sdvr(sweep, tiles=TILES, bins=BINS, sweeps=SWEEPS, interval=INTERVAL,
                             dbm=spectrum(), age=60.0)
    data = (-100.0 + np.arange(FFT_ROWS)[:, None] + 0.01 * np.arange(2048)[None, :]).astype('<f4')
    fft = os.path.join(folder, '2026-09-29_23-24-27_3000004883hz_spec.fft')
    signals.write_blue(fft, data, rate=1.0 / 0.2322876, fmt='SF', subsize=2048,
                       keywords={'RF_FREQ': 3.0e9, 'TIME_EPOCH': '2026-09-29T23:24:27Z'})
    rng = np.random.default_rng(1)
    iq = (0.05 * (rng.standard_normal(1 << 17) + 1j * rng.standard_normal(1 << 17))).astype(np.complex64)
    iqdvr = os.path.join(folder, 'iq.sdvr')
    signals.write_sdvr(iqdvr, iq, 2.5e6, 98.4e6, age=60.0)
    return sweep, fft, iqdvr, data


def pieces(folder):
    sweep, fft, iqdvr, data = build(folder)
    before = {n: os.stat(os.path.join(folder, n)).st_mtime_ns for n in os.listdir(folder)}
    recs = library.scan_external([folder])
    names = sorted(r.name for r in recs)
    assert len(recs) == 3, names
    by = {}
    for r in recs:
        assert len(r.tracks) == 1, (r.name, [t.kind for t in r.tracks])
        by[r.tracks[0].kind] = r
    assert sorted(by) == ['dvr-iq', 'dvr-sweep', 'fft'], sorted(by)       # an IQ DVR: IQ only
    s, f = by['dvr-sweep'], by['fft']
    st, ft = s.tracks[0], f.tracks[0]
    assert st.is_spectrum and ft.is_spectrum and not st.is_iq and not ft.is_iq
    assert not by['dvr-iq'].tracks[0].is_spectrum
    assert st.error is None and ft.error is None
    assert s.external and f.external
    assert s.group == f.group == 'Sceptre spectrum', (s.group, f.group)
    assert library.GROUPS.index('Sceptre spectrum') > library.GROUPS.index('Sceptre BLUE IQ')
    assert abs(st.seconds - TILES * SWEEPS * INTERVAL) < 1e-6, st.seconds
    assert abs(ft.seconds - FFT_ROWS * 0.2322876) < 1e-4, ft.seconds
    assert abs(s.started.timestamp() - (1.7907e9 + 0.25)) < 3600 * 24 * 365
    assert s.files() == [sweep] and st.sidecars() == []
    assert s.kinds_text() == 'Sceptre sweep DVR' and f.kinds_text() == 'Sceptre FFT', \
        (s.kinds_text(), f.kinds_text())
    low, high = library.extent(st)
    assert abs(low - 4882.8125) < 1e-3 and abs(high - (4882.8125 + (BINS - 1) * 4882.8125)) < 1e-3

    # drawn in dBm, as spectra reads it: exactly, one sweep to a row
    ref = spectra.open_spectra(sweep)
    _, _, whole = ref.read()
    img, freqs = library.render(st, 0.0, st.seconds, TILES * SWEEPS, None)
    assert img.shape == (TILES * SWEEPS, BINS) and img.dtype == np.float32, img.shape
    assert np.allclose(img, whole, atol=1e-4), np.nanmax(np.abs(img - whole))
    assert abs(freqs[0] - 4882.8125) < 1e-3 and len(freqs) == BINS
    # a band of it, at its own columns: the part asked for, from the file's bins
    lo_hz, hi_hz = freqs[1000], freqs[1999]
    part, pf = library.render(st, 0.0, st.seconds, 8, 500, band=(lo_hz, hi_hz))
    assert part.shape == (8, 500) and lo_hz - 10 * 4882.8125 <= pf[0] and pf[-1] <= hi_hz + 10 * 4882.8125, \
        (part.shape, pf[0], pf[-1])
    assert np.nanmax(part) < -60.0 and np.isfinite(part).all()
    # squeezed: the loudest bin of each column, and the carrier survives
    small, sf = library.render(st, 0.0, st.seconds, 8, 64)
    assert small.shape == (8, 64) and len(sf) == 64
    assert np.nanmax(small) > -66.0, 'the -65 dBm carrier was averaged away'
    # outside the file: NaN rows, and a slot of its own to each row
    wide, _ = library.render(st, -2.0, st.seconds + 2.0, 20, 64)
    assert np.isnan(wide[0]).all() and np.isnan(wide[-1]).all() and np.isfinite(wide[10]).all()
    ff, fq = library.render(ft, 0.0, ft.seconds, FFT_ROWS, None)
    assert ff.shape == (FFT_ROWS, 2048) and np.allclose(ff, data), np.nanmax(np.abs(ff - data))

    # not deleted, nothing written
    failed = library.delete(s)
    assert failed and os.path.exists(sweep), failed
    assert {n: os.stat(os.path.join(folder, n)).st_mtime_ns for n in os.listdir(folder)} == before
    print("part 1 passed")


def window(folder, own):
    station = signals.write_station(os.path.join(FOLDER, 'station'), seconds=2.0)
    args = fmapp.parse_args(['--file', station, '--no-sound-card'])
    w = fmapp.MainWindow(args, {'recording_dir': own, 'library_folders': [folder]})
    w.show()
    pump(0.2)
    w.start_initial()
    return w


def pick(w, kind):
    row = next(i for i, r in enumerate(w._recordings) if r.tracks[0].kind == kind)
    w.rec_list.clearSelection()
    w.rec_list.setCurrentItem(w._rec_items[row])
    w._rec_items[row].setSelected(True)
    assert pump(5, lambda: w._track is not None and w._track.kind == kind), kind
    assert pump(8, lambda: w.play_btn.isEnabled()), 'Play stays disabled'


def gui(folder):
    own = signals.ensure_dir(os.path.join(FOLDER, 'own'))
    w = window(folder, own)
    w.tabs.setCurrentIndex(2)
    assert pump(10, lambda: len(w._recordings) == 3), len(w._recordings)
    lst = w.rec_list
    heads = [lst.item(i).text() for i in range(lst.count()) if lst.item(i).data(HEADER_ROLE)]
    assert heads == ['Sceptre IQ DVR', 'Sceptre spectrum'], heads
    count = [lst.item(i).data(HEADER_ROLE) for i in range(lst.count())
             if lst.item(i).data(HEADER_ROLE)]
    assert count == [1, 2], count
    text = ' '.join(it.text() for it in w._rec_items)
    assert 'Sceptre sweep DVR' in text and 'Sceptre FFT' in text, text
    assert 'sweep.sdvr' not in text and '.fft' not in text, text

    # a slow file: a spinner says so on the plot, then "Press Play" comes back
    real_render = library.render

    def slow(*a, **k):
        time.sleep(1.2)
        return real_render(*a, **k)

    library.render = slow
    try:
        w.rec_list.clearSelection()
        row = next(i for i, r in enumerate(w._recordings) if r.tracks[0].kind == 'fft')
        w.rec_list.setCurrentItem(w._rec_items[row])
        w._rec_items[row].setSelected(True)
        assert pump(5, lambda: 'Loading' in w.rf_view.message.toPlainText()), \
            repr(w.rf_view.message.toPlainText())
        assert pump(8, lambda: w.rf_view.message.toPlainText() == 'Press Play'), \
            repr(w.rf_view.message.toPlainText())
    finally:
        library.render = real_render

    # the sweep DVR: drawn on choosing it, in dBm, with the map beside it
    pick(w, 'dvr-sweep')
    view = w.rf_view
    assert pump(8, lambda: view._hist is not None and view._hist.n > 0), 'no waterfall from the file'
    assert view.level_unit == 'dBm', view.level_unit
    # opened readable: the Ref level fitted to the rows (the saved -10 dBFS
    # would leave a -100 dBm floor off the bottom), with no key pressed
    assert pump(5, lambda: view.ref_knob.value() < -40), view.ref_knob.value()
    assert -135 < view.ref_knob.value() - view.range_knob.value() < -95, \
        (view.ref_knob.value(), view.range_knob.value())
    total = w._track.seconds
    assert abs(view.minimap.duration - total) < 1e-6, view.minimap.duration
    # opened on the whole file, and not to be zoomed out past it
    lo_f, hi_f = library.extent(w._track)
    assert abs(view.span_knob.value() - (hi_f - lo_f)) < 1.0, view.span_knob.value()
    vbx = view.plot.getPlotItem().getViewBox()
    vbx.setXRange(-80 * (hi_f - lo_f) / view.scale, 80 * (hi_f - lo_f) / view.scale, padding=0)
    pump(0.3)
    x0, x1 = vbx.viewRange()[0]
    assert x0 * view.scale >= lo_f - 1.0 and x1 * view.scale <= hi_f + 1.0, (x0, x1, lo_f, hi_f)
    view.set_center((lo_f + hi_f) / 2)
    # zoomed into a sweep wider than the view's columns, it is read for the
    # window on show: the waterfall is redrawn on that band's own columns
    w.BAND_COLS = 256                            # this file's 4096 bins are "wide" now
    full_x = view._wf_extent
    view.span_knob.setValue((view.full[1] - view.full[0]) / 10, emit=False)
    view.set_center(view.full[0] + 0.3 * (view.full[1] - view.full[0]))
    assert pump(8, lambda: w._spec_band is not None and view._wf_extent is not None
                and (view._wf_extent[1] - view._wf_extent[0]) < 0.5 * (full_x[1] - full_x[0])), \
        (w._spec_band, view._wf_extent, full_x)
    low_hz = view.full[0] + 0.3 * (view.full[1] - view.full[0])
    assert w._spec_band[0] <= low_hz <= w._spec_band[1]

    # playing: no engine, no radio, the playhead on a timer
    w.play_btn.setChecked(True)
    assert pump(5, lambda: w._play is not None), 'no playback'
    assert not w.engine.running and w.engine.radio is None, 'a spectrum recording opened the radio'
    assert w.play_btn.text() == 'Pause'
    assert pump(6, lambda: w._playhead() > 0.6), w._playhead()
    t1 = w._playhead()
    assert 0.0 < t1 < total
    n1 = view._hist.n
    assert pump(3, lambda: view._hist.n > n1), 'the waterfall did not run'
    assert view.level_unit == 'dBm'
    # not faster than real time, and not stuck
    a = w._playhead()
    pump(1.0)
    rate = w._playhead() - a
    assert 0.6 < rate < 1.5, ('playhead moved', rate, 's in 1 s')

    # paused: it holds
    w.play_btn.setChecked(False)
    assert w.play_btn.text() == 'Play'
    held = w._playhead()
    pump(0.8)
    assert abs(w._playhead() - held) < 0.05, (held, w._playhead())
    # seek, paused and playing
    w._seek_to(total * 0.5)
    pump(0.5)
    assert abs(w._playhead() - total * 0.5) < 0.5, (w._playhead(), total * 0.5)
    w.play_btn.setChecked(True)
    assert pump(3, lambda: w._playhead() > total * 0.5 + 0.3), w._playhead()
    assert not w.engine.running

    # at the end it stops, with Loop off, on the end; with it on, it goes round
    w.loop_check.setChecked(False)
    w._seek_to(total - 1.0)
    assert pump(6, lambda: not w.play_btn.isChecked()), 'did not stop at the end'
    assert w.play_btn.text() == 'Play' and not w.engine.running
    w.loop_check.setChecked(True)
    w._seek_to(total - 0.8)
    w.play_btn.setChecked(True)
    pump(2.5)
    assert w.play_btn.isChecked(), 'Loop stopped at the end'
    assert w._playhead() < total - 0.5 and w._playhead() > 0.0, w._playhead()
    w.play_btn.setChecked(False)

    # a .fft the same way, and the other files untouched
    pick(w, 'fft')
    assert pump(8, lambda: view._hist is not None and view._hist.n > 0)
    w.play_btn.setChecked(True)
    assert pump(5, lambda: w._play is not None and w._playhead() > 0.3), w._playhead()
    assert not w.engine.running and w.engine.radio is None
    w.play_btn.setChecked(False)

    # an IQ DVR still plays through the radio, as before
    pick(w, 'dvr-iq')
    w.play_btn.setChecked(True)
    assert pump(15, lambda: w._play is not None and w.engine.running
                and w.engine.radio is not None), 'the IQ DVR did not play'
    w.play_btn.setChecked(False)

    # not deleted from the list
    keys = [r.key for r in w._recordings if r.external]
    w._delete_keys(keys)
    assert all(os.path.exists(k) for k in keys)
    w.close()
    print("part 2 passed")


def main():
    folder = os.path.join(FOLDER, 'sceptre')
    pieces(folder)
    gui(folder)
    print("Sceptre spectra: all checks passed")


if __name__ == '__main__':
    main()
