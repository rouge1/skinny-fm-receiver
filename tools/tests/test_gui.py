"""The window, driven the way a user would - offscreen, no radio, no sound.

Part 1 plays the synthetic station from an IQ file and works the controls:
RDS on screen, mute and volume on the running flowgraph, the view's Span /
Ref / Range / Average dials and peak hold, the region (de-emphasis) and
stereo switches, the channel filter, recording (audio, channel IQ, band IQ)
across a retune, theme switching, Stop and Start, settings saved.

Part 2 puts a simulated wideband radio behind the Sweep tab (tones at known
frequencies, slow to retune) and checks the sweep finds them, the station
list fills, Pause holds it, a span edit re-plans it without a restart, and
double-clicking a station switches to Receive tuned to it.

Run:  python tools/tests/test_gui.py [--keep]     (about 40 s)
"""

import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt, QtCore, QtGui  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-gui-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import radios  # noqa: E402
from tests import signals  # noqa: E402
from tests.test_sweep import slow_radio  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


WINDOWS = []


def make_window(argv, config=None):
    args = fmapp.parse_args(argv + ['--no-audio'])
    window = fmapp.MainWindow(args, config or {'recording_dir': FOLDER})
    WINDOWS.append(window)
    window.show()
    pump(0.2)
    window.start_initial()
    return window


# ------------------------------------------------------------- the mouse

def _send(widget, event):
    QAPP.sendEvent(widget, event)
    QAPP.processEvents()


def _plot_point(view, hz, y_frac=0.5):
    """Where ``hz`` is on the RF plot, in its viewport's pixels."""
    vb = view.plot.getPlotItem().getViewBox()
    (_, _), (y0, y1) = vb.viewRange()
    scene = vb.mapViewToScene(QtCore.QPointF(hz / 1e6, y0 + (y1 - y0) * y_frac))
    return view.plot.mapFromScene(scene)


def _move(widget, pos, buttons=QtCore.Qt.NoButton):
    # pyqtgraph drops moves closer together than its rate limit (10 ms).
    time.sleep(0.012)
    _send(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(pos),
                                    QtCore.Qt.NoButton, buttons, QtCore.Qt.NoModifier))


def _wheel(widget, pos, notches=1, modifiers=QtCore.Qt.NoModifier):
    posf = QtCore.QPointF(pos)
    _send(widget, QtGui.QWheelEvent(posf, QtCore.QPointF(widget.mapToGlobal(pos)),
                                    QtCore.QPoint(), QtCore.QPoint(0, 120 * notches),
                                    QtCore.Qt.NoButton, modifiers,
                                    QtCore.Qt.NoScrollPhase, False))


def _middle_drag(widget, start, end, steps=8):
    _move(widget, start)                       # hover first: that claims the drag
    _send(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(start),
                                    QtCore.Qt.MiddleButton, QtCore.Qt.MiddleButton,
                                    QtCore.Qt.NoModifier))
    for i in range(1, steps + 1):
        f = i / steps
        at = QtCore.QPoint(int(start.x() + (end.x() - start.x()) * f), start.y())
        _move(widget, at, QtCore.Qt.MiddleButton)
    _send(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(end),
                                    QtCore.Qt.MiddleButton, QtCore.Qt.NoButton,
                                    QtCore.Qt.NoModifier))


def _digit_point(entry, place):
    for p, rect in entry._layout()[0]:
        if p == place:
            return rect.center().toPoint()
    raise AssertionError(place)


def spectrum_mouse(w):
    """The wheel and the middle button on the channel band, the digit
    tuner and the roller - driven by real mouse events. The file's band
    reaches 97.425-99.375 MHz; the view is zoomed to 400 kHz."""
    e, view, port = w.engine, w.rf_view, w.rf_view.plot.viewport()
    assert abs(e.station_hz - 98.7e6) < 1
    lo, hi = e.tuner_range()
    assert abs(lo - 97.425e6) < 1 and abs(hi - 99.375e6) < 1, (lo, hi)
    # The wheel over the band widens it; with Shift, by a kHz.
    band = _plot_point(view, 98.7e6)
    _move(port, band)
    assert view.band.hovered, 'the band should light up under the pointer'
    _wheel(port, band, 1)
    assert abs(w.chan_entry.value() - 205e3) < 1 and abs(e.rx.channel_bw - 205e3) < 1
    _wheel(port, band, -2, QtCore.Qt.ShiftModifier)
    assert abs(w.chan_entry.value() - 203e3) < 1, w.chan_entry.value()
    # Anywhere else the wheel still zooms, and the filter stays.
    span = view.span_knob.value()
    away = _plot_point(view, 98.55e6)
    _move(port, away)
    _wheel(port, away, 1)
    assert view.span_knob.value() < span * 0.99 and abs(w.chan_entry.value() - 203e3) < 1
    view.span_knob.setValue(400e3)
    view.set_center(98.7e6)
    (x0, x1), _ = view.plot.getPlotItem().getViewBox().viewRange()
    # Middle-drag the band 100 kHz up: the tuner follows, snapped to the
    # step, and the view does not move under the pointer.
    _middle_drag(port, _plot_point(view, 98.7e6), _plot_point(view, 98.81e6))
    assert abs(e.station_hz - 98.8e6) < 1, e.station_hz
    assert abs(w.tuner.value() - 98.8e6) < 1
    assert view.plot.getPlotItem().getViewBox().viewRange()[0] == [x0, x1]
    # Dragged past the edge of the band, it stops there - and says so.
    view.span_knob.setValue(view.span_knob._max)
    _middle_drag(port, _plot_point(view, 98.8e6), _plot_point(view, 100.2e6))
    assert abs(e.station_hz - 99.375e6) < 1, e.station_hz
    assert 'edge of the band' in w.range_label.text(), w.range_label.text()
    # The tuner's digits: the wheel over the 100 kHz digit.
    w.tune(98.7e6)
    t = w.tuner
    at = _digit_point(t, 2)
    _move(t, at)
    _wheel(t, at, 1)
    assert abs(e.station_hz - 98.8e6) < 1, e.station_hz
    t.setFocus()
    _send(t, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Down, QtCore.Qt.NoModifier))
    assert abs(e.station_hz - 98.7e6) < 1, e.station_hz
    # The roller: one step down, then one up.
    _wheel(w.roller, QtCore.QPoint(10, 10), -1)
    assert abs(e.station_hz - 98.6e6) < 1, e.station_hz
    _wheel(w.roller, QtCore.QPoint(10, 10), 1)
    assert abs(e.station_hz - 98.7e6) < 1, e.station_hz
    view.span_knob.setValue(400e3)
    view.set_center(98.7e6)
    w.chan_entry.setValue(200e3, emit=True)
    print("spectrum mouse, digits and roller: ok")


def _press(widget, pos, button, down):
    kind = QtCore.QEvent.MouseButtonPress if down else QtCore.QEvent.MouseButtonRelease
    _send(widget, QtGui.QMouseEvent(kind, QtCore.QPointF(pos), button,
                                    button if down else QtCore.Qt.NoButton,
                                    QtCore.Qt.NoModifier))


def knobs_and_fades(w):
    """Knobs turn under the pointer with the wheel, no click first; the Step
    knob, the channel filter's arrows and its reach to 400 kHz; and the
    tuner's marker, hidden but for tuning or holding the band."""
    e, view = w.engine, w.rf_view
    mid = QtCore.QPoint(23, 23)
    for knob, notches, expect in ((w.volume_knob, 1, lambda v: v + 2),
                                  (view.ref_knob, 1, lambda v: v + 2),
                                  (view.range_knob, -1, lambda v: v - 5),
                                  (view.span_knob, -1, lambda v: v / 1.25)):
        before = knob.value()
        _move(knob.dial, mid)
        _wheel(knob.dial, mid, notches)
        assert abs(knob.value() - expect(before)) < 1e-6 * max(1, before), (knob, knob.value())
        knob.setValue(before)
    assert abs(w.engine.rx.vol_l.k() - 1.5 * (w.volume_knob.value() / 100) ** 2) < 1e-6
    _wheel(w.step_knob.dial, mid, 1)
    assert w._step_hz() == 200e3 and view.snap_hz == 200e3
    _wheel(w.step_knob.dial, mid, -1)
    assert w._step_hz() == 100e3
    _wheel(w.chan_roller, QtCore.QPoint(10, 5), 1)
    assert abs(e.rx.channel_bw - 205e3) < 1, e.rx.channel_bw
    w.chan_entry.setValue(400e3, emit=True)
    lo, hi = view.band.getRegion()
    assert abs(e.rx.channel_bw - 400e3) < 1 and abs((hi - lo) - 0.4) < 1e-6
    w.chan_entry.setValue(200e3, emit=True)
    # The marker: gone once the tuner is still, back while it moves.
    assert view.marker_auto
    assert pump(3, lambda: view.marker.opacity() < 0.01), view.marker.opacity()
    w._step(1)
    assert pump(0.5, lambda: view.marker.opacity() > 0.99), view.marker.opacity()
    assert pump(3, lambda: view.marker.opacity() < 0.01), 'the marker should fade once settled'
    w._step(-1)
    pump(3, lambda: view.marker.opacity() < 0.01)
    # Holding the band with the middle button shows it, for as long as held.
    port = view.plot.viewport()
    at = _plot_point(view, e.station_hz)
    _move(port, at)
    _press(port, at, QtCore.Qt.MiddleButton, True)
    assert pump(0.5, lambda: view.marker.opacity() > 0.99), view.marker.opacity()
    pump(1.6)
    assert view.marker.opacity() > 0.99, 'held, the marker stays'
    _press(port, at, QtCore.Qt.MiddleButton, False)
    assert pump(3, lambda: view.marker.opacity() < 0.01), 'let go, it fades'
    print("knobs, step, channel arrows and the marker fade: ok")


def part1_file_receiver():
    path = signals.write_station(os.path.join(FOLDER, 'synth'), seconds=12.0)
    w = make_window(['--file', path])
    e = w.engine
    assert e.running and w._mode == 'receive', w.status.text()
    assert not w.tabs.isTabEnabled(0), 'a recording cannot sweep'
    ok = pump(15, lambda: w.lbl['station_name'].text() == 'TEST FM')
    assert ok, f"RDS never showed: {w.lbl['station_name'].text()!r}"
    assert 'Stereo' in w.stereo_label.text(), w.stereo_label.text()
    print("RDS on screen:", w.lbl['station_name'].text(), w.lbl['pi'].text(),
          '|', w.lbl['radiotext'].text())

    # Mute and volume act on the running chain.
    rx = e.rx
    w.mute_btn.click()
    assert rx.vol_l.k() == 0 and rx.vol_r.k() == 0 and w.mute_btn.text() == 'Muted'
    w.mute_btn.click()
    w.volume_knob.setValue(40)
    assert abs(rx.vol_l.k() - 1.5 * 0.16) < 1e-6, rx.vol_l.k()
    pump(0.5)
    assert max(w.meter._rms) > -30, w.meter._rms

    # The view's dials.
    view = w.rf_view
    view.span_knob.setValue(400e3)
    (x0, x1), (y0, y1) = view.plot.getPlotItem().getViewBox().viewRange()
    assert abs((x1 - x0) - 0.4) < 1e-3, (x0, x1)
    assert abs((x0 + x1) / 2 - 98.7) < 0.01, 'the span is centred on the station'
    view.ref_knob.setValue(-30)
    view.range_knob.setValue(60)
    (x0, x1), (y0, y1) = view.plot.getPlotItem().getViewBox().viewRange()
    assert abs(y1 + 30) < 0.5 and abs(y0 + 90) < 0.5, (y0, y1)

    # Average dial -> the probe's averaging; peak hold draws a second trace.
    view.avg_knob.setValue(10)
    assert abs(rx.rf_probe.alpha - 0.1) < 1e-9, rx.rf_probe.alpha
    view.peak_check.setChecked(True)
    pump(0.5)
    assert view._peak is not None and len(view._peak) == 4096
    held = view._peak.copy()
    pump(0.3)
    assert np.all(view._peak >= held - 1e-9), 'peak hold only rises'
    view.peak_check.setChecked(False)
    assert view._peak is None

    # Region: de-emphasis taps change on the running filters, RDS restarts.
    b75 = rx.mid_de
    w.region_combo.setCurrentIndex(1)
    w._region_changed(1)
    assert rx.region == 'RDS' and w.lbl['pi'].text() == '-'
    w.region_combo.setCurrentIndex(0)
    w._region_changed(0)
    assert rx.region == 'RBDS' and rx.mid_de is b75

    # Stereo off: mono at once, pilot or not.
    w.stereo_check.setChecked(False)
    assert rx.update_stereo()[0] is False and rx.side_gain.k() == 0.0
    w.stereo_check.setChecked(True)
    assert rx.update_stereo()[0] is True and rx.side_gain.k() == 2.0

    # Channel filter: new taps on the running filter.
    before = len(rx.stage2.taps())
    w.chan_entry.setValue(120e3, emit=True)
    assert len(rx.stage2.taps()) != before or rx.channel_bw == 120e3
    lo, hi = view.band.getRegion()
    assert abs((hi - lo) - 0.12) < 1e-6, (lo, hi)
    w.chan_entry.setValue(200e3, emit=True)

    spectrum_mouse(w)
    knobs_and_fades(w)

    # Record all three, retune part way, stop.
    w.rec_audio.setChecked(True)
    w.rec_channel.setChecked(True)
    w.rec_band.setChecked(True)
    w.rec_btn.click()
    assert w._wav is not None and len(w._iq_recs) == 2, w.rec_label.text()
    pump(2.0)
    w._step(1)                                        # 98.8 MHz
    assert abs(e.station_hz - 98.8e6) < 1
    pump(1.5)
    w.rec_btn.click()
    saved = sorted(os.listdir(FOLDER))
    wavs = [f for f in saved if f.endswith('.wav')]
    chans = [f for f in saved if 'iq-channel' in f and f.endswith('.cfile')]
    bands = [f for f in saved if 'iq-band' in f and f.endswith('.cfile')]
    print("recorded:", wavs, chans, bands)
    assert len(wavs) == 1 and len(chans) == 2 and len(bands) == 1, saved
    import wave
    with wave.open(os.path.join(FOLDER, wavs[0])) as wf:
        seconds = wf.getnframes() / wf.getframerate()
    assert 2.5 < seconds < 5.0, seconds
    part2 = [c for c in chans if 'part2' in c][0]
    meta = json.load(open(os.path.join(FOLDER, part2[:-6] + '.json')))
    assert abs(meta['station_hz'] - 98.8e6) < 1 and meta['rate'] == 500e3, meta
    assert 'Saved' in w.rec_label.text()

    # Themes, then Stop (releases the radio) and Start.
    first = fmapp.theme.current()
    seen = set()
    for _ in fmapp.theme.NAMES:
        w.theme_disc.click()
        seen.add(fmapp.theme.current())
        assert w.cfg['theme'] == fmapp.theme.current()
        assert fmapp.theme.NAMES[fmapp.theme.current()] in w.theme_disc.toolTip()
        pump(0.2)
    assert seen == set(fmapp.theme.NAMES) and fmapp.theme.current() == first, seen
    w.run_btn.click()
    assert not e.running and w.radio is None and w.run_btn.text() == 'Start'
    w.run_btn.click()
    assert w.engine.running and w.radio is not None
    pump(1.0)
    shot = os.path.join(FOLDER, 'receive.png')
    w.grab().save(shot)
    w.close()
    saved = json.load(open(os.environ['FMRX_CONFIG']))
    assert saved['radio'] == 'file' and abs(saved['frequency_mhz'] - 98.8) < 1e-6, saved
    assert saved['view_receive']['ref_db'] == -30, saved['view_receive']
    print("part 1 passed")


class SimRadio(radios.Radio):
    """A wideband radio made of tones, slow to retune - see test_sweep."""
    kind = 'hackrf'
    name = 'Simulated radio'
    receive_rates = (2e6,)
    sweep_rates = (10e6,)
    default_receive_rate = 2e6
    default_sweep_rate = 10e6
    settle_ms = 30.0
    TONES = [(89.3e6, 0.3), (95.1e6, 0.1), (101.7e6, 0.05)]

    def open(self):
        self.block = slow_radio(10e6, self.TONES, latency=150000)
        self.block.center = 98e6

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.rate = self.rate

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center(hz)


def radio_card(w):
    """The Radio card on a radio that can retune: the Center moves the LO
    and the yellow line, the tuner stops at the band's edge and keeps off
    the DC spike, and peak hold starts again when the LO moves. The band
    is 2 MS/s, 75% usable, less 150 kHz: the LO +-600 kHz."""
    e, view = w.engine, w.rf_view
    assert abs(w.center_entry.value() - 94.8e6) < 1
    assert view.center_line.isVisible() and abs(view.center_line.value() - 94.8) < 1e-6
    assert view.center_line.opacity() > 0.99
    # Moving the Center: its line fades out of the way, and back once settled.
    w.center_entry.setValue(94.9e6, emit=True)
    assert pump(0.5, lambda: view.center_line.opacity() < 0.01), view.center_line.opacity()
    assert pump(2, lambda: view.center_line.opacity() > 0.99), view.center_line.opacity()
    w.center_entry.setValue(94.8e6, emit=True)
    w.tune(96.0e6)                                    # past the top edge
    assert abs(e.station_hz - 95.4e6) < 1 and abs(e.lo_hz - 94.8e6) < 1, e.station_hz
    assert 'edge of the band' in w.range_label.text()
    w.tune(94.83e6)                                   # into the DC keep-out, going down
    assert abs(e.station_hz - 94.7e6) < 1, e.station_hz
    view.peak_check.setChecked(True)
    pump(0.3)
    assert view._peak is not None
    w.center_entry.setValue(97.0e6, emit=True)        # the band moves; the tuner is pulled in
    assert abs(e.lo_hz - 97.0e6) < 1 and abs(w.radio.center_hz - 97.0e6) < 1
    assert abs(e.station_hz - 96.4e6) < 1, e.station_hz
    assert abs(w.tuner.value() - 96.4e6) < 1
    assert abs(view.center_line.value() - 97.0) < 1e-6
    assert view._peak is None, 'peak hold must start again when the LO moves'
    w._center_on_tuner()
    assert abs(e.lo_hz - 96.1e6) < 1 and abs(e.station_hz - 96.4e6) < 1, e.lo_hz
    # A 200 kHz step keeps to the Americas' odd tenths.
    w.step_knob.setValue(fmapp.STEPS_KHZ.index(200))
    w.tune(96.3e6)
    w._step(1)
    assert abs(e.station_hz - 96.5e6) < 1, e.station_hz
    assert abs(w.rf_view.snap_hz - 200e3) < 1
    w.step_knob.setValue(fmapp.STEPS_KHZ.index(100))
    view.peak_check.setChecked(False)
    print("radio card: ok")


def part2_sweep():
    original = fmapp.make_radio
    fmapp.make_radio = lambda kind, *a, **k: SimRadio()
    try:
        w = make_window(['--radio', 'hackrf', '--mode', 'sweep'],
                        {'recording_dir': FOLDER, 'sweep_frames': 4})
        assert w._mode == 'sweep' and w.engine.running, w.status.text()
        assert not w.rf_view.marker_auto and w.rf_view.marker.opacity() > 0.99
        assert w.engine.sweeper.plan.steps == 3, w.engine.sweeper.plan.describe()
        ok = pump(20, lambda: w.station_list.count() >= 3)
        found = [w.station_list.item(i).data(QtCore.Qt.UserRole)
                 for i in range(w.station_list.count())]
        print("stations found:", [f / 1e6 for f in found], '|', w.sweep_info.text())
        assert ok, found
        for f, _ in SimRadio.TONES:
            assert any(abs(f - g) < 1 for g in found), (f, found)
        assert not w.rec_btn.isEnabled(), 'recording is for Receive'
        # Pause stops the sweep advancing; Resume starts it again.
        w.pause_btn.click()
        n = w.engine.sweeper.sweeps
        pump(0.6)
        assert w.engine.sweeper.sweeps == n and w.pause_btn.text() == 'Resume'
        w.pause_btn.click()
        assert pump(3, lambda: w.engine.sweeper.sweeps > n)
        # A span edit re-plans the running sweep without a restart.
        sweeper = w.engine.sweeper
        w.start_spin.setValue(88.0)
        assert w.engine.sweeper is sweeper and sweeper.plan.start_hz == 88e6
        assert w.preset_combo.currentText() == 'Custom'
        shot = os.path.join(FOLDER, 'sweep.png')
        w.grab().save(shot)
        # Double-click the 95.1 station: Receive, tuned to it.
        item = [w.station_list.item(i) for i in range(w.station_list.count())
                if abs(w.station_list.item(i).data(QtCore.Qt.UserRole) - 95.1e6) < 1][0]
        w._station_activated(item)
        pump(0.5)
        assert w._mode == 'receive' and w.tabs.currentIndex() == 1
        assert abs(w.engine.station_hz - 95.1e6) < 1, w.engine.station_hz
        assert abs(w.engine.lo_hz - (95.1e6 - 300e3)) < 1
        radio_card(w)
        w.close()
    finally:
        fmapp.make_radio = original
    print("part 2 passed")


if __name__ == '__main__':
    keep = '--keep' in sys.argv
    try:
        part1_file_receiver()
        part2_sweep()
        print("GUI: all checks passed")
    finally:
        # A failed check must not leave a flowgraph running into interpreter
        # shutdown: collecting its Python blocks under it aborts the process.
        for window in WINDOWS:
            window.close()
        if keep:
            print(f"files kept in {FOLDER}")
        else:
            shutil.rmtree(FOLDER, ignore_errors=True)
