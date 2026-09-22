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

Part 3 puts a radio that sweeps itself (as the BB60D does) behind it: the
full range, the RBW, real time and its density map. Part 4 does the same
where the library has no real time, as on a Mac. Part 5 lists a receive rate
this computer can't use (the BB60D's 2.5 MS/s on a Mac): greyed out, with
why, and out of reach of the keys, the wheel and a saved setting.

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
from PyQt5 import Qt, QtCore, QtGui, QtTest  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-gui-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import bb60_sweep  # noqa: E402
from fm_receiver.bb60_sweep import RT_MAX_SPAN_HZ  # noqa: E402
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


def _hover(widget, pos):
    """A real pointer move, through the window system, so Qt sends the
    enter and leave events a sent MouseMove does not."""
    QtTest.QTest.mouseMove(widget, pos)
    pump(0.05)


def knob_glow(w):
    """Every knob lights under the pointer - Volume too, which a click had
    left lit for good, so that it showed no change - and goes out after."""
    mid = QtCore.QPoint(23, 23)
    away = w.status
    w.left_scroll.ensureWidgetVisible(w.volume_knob)
    pump(0.1)
    for knob in (w.volume_knob, w.step_knob, w.rf_view.span_knob):
        dial = knob.dial
        _hover(away, QtCore.QPoint(2, 2))
        assert pump(1, lambda: dial.glow == 0.0), (knob.caption.text(), dial.glow)
        _hover(dial, mid)
        assert pump(1, lambda: dial.glow == 1.0), (knob.caption.text(), dial.glow)
        assert dial.graphicsEffect().isEnabled()
    # Clicked, it has the focus; the pointer gone, it goes out all the same.
    dial = w.volume_knob.dial
    _press(dial, mid, QtCore.Qt.LeftButton, True)
    _press(dial, mid, QtCore.Qt.LeftButton, False)
    assert dial.hasFocus()
    _hover(away, QtCore.QPoint(2, 2))
    assert pump(1, lambda: dial.glow == 0.0), dial.glow
    assert not dial.graphicsEffect().isEnabled()
    print("knob glow under the pointer, Volume too: ok")


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
    knob_glow(w)
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
        name = fmapp.theme.NAMES[fmapp.theme.current()]
        assert name in w.theme_disc.toolTip() and name in w.theme_word.toolTip()
        pump(0.2)
        # Walnut's wider faces once cut the left column off under the plots.
        needs = w.left_panel.sizeHint().width()
        assert w.main_split.sizes()[0] >= needs, (name, w.main_split.sizes(), needs)
    assert w.testAttribute(QtCore.Qt.WA_AlwaysShowToolTips), 'tooltips with the terminal focused'
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
    # Sweep's tuner is the same tuner: it follows one moved here.
    assert w.sweep_tuner.value() == w.tuner.value()
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
        # A new window sweeps the radio's whole range; the LO-hopping rows
        # show, the RBW (a radio's own sweep's) does not.
        assert w.preset_combo.currentText() == 'Full range of the radio'
        low, high = SimRadio().sweep_range_hz
        assert (w.sweep_start.value(), w.sweep_stop.value()) == (low, high)
        assert w.fft_combo.isVisible() and not w.rbw_combo.isVisible()
        assert w.rf_view.level_unit == 'dBFS'
        # Dragged or zoomed out (what the mouse calls), the view stops at
        # 0 Hz and 6 GHz - the waterfall's too, which pans both. The two
        # plots are lined up on screen (linked views go by that), and the
        # link rounds to a pixel.
        lo_mhz, hi_mhz = (x / 1e6 for x in fmapp.SWEEP_VIEW_HZ)
        view_boxes = [p.getPlotItem().getViewBox() for p in (w.rf_view.plot, w.rf_view.wf_plot)]
        spec, fall = (vb.sceneBoundingRect() for vb in view_boxes)
        assert (spec.left(), spec.width()) == (fall.left(), fall.width()), (spec, fall)
        for vb in view_boxes:
            vb.setXRange(90, 110, padding=0)
            vb.translateBy(x=-1000)
            for other in view_boxes:
                (x0, x1), _ = other.viewRange()
                assert abs(x0 - lo_mhz) < 1e-6 and abs(x1 - 20) < 0.1, (x0, x1)
            vb.setXRange(5900, 5990, padding=0)
            vb.translateBy(x=1000)
            for other in view_boxes:
                (x0, x1), _ = other.viewRange()
                assert abs(x1 - hi_mhz) < 1e-6 and abs(x0 - 5910) < 0.2, (x0, x1)
            vb.scaleBy(x=1000)
            for other in view_boxes:
                (x0, x1), _ = other.viewRange()
                assert x0 >= lo_mhz - 1e-6 and x1 <= hi_mhz + 1e-6, (x0, x1)
        w.preset_combo.setCurrentIndex(1)
        w._preset_chosen(1)                              # FM broadcast 87.5-108
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
        # A bound's digits re-plan the running sweep once they rest, with
        # no restart; neither bound can pass the other.
        sweeper = w.engine.sweeper
        _wheel(w.sweep_start, _digit_point(w.sweep_start, 3), 1)    # 87.5 -> 88.5
        assert pump(1, lambda: sweeper.plan.start_hz == 88.5e6), sweeper.plan.start_hz
        assert w.engine.sweeper is sweeper
        assert w.preset_combo.currentText() == 'Custom' and w.cfg['sweep_band'] == 'custom'
        w.sweep_start.setValue(200e6, emit=True)
        assert w.sweep_start.value() == 108e6 - fmapp.MIN_SWEEP_SPAN_HZ, w.sweep_start.value()
        w.sweep_start.setValue(88e6, emit=True)
        pump(0.3)
        assert sweeper.plan.start_hz == 88e6
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
        for vb in view_boxes:                            # receiving, no limits
            assert vb.state['limits']['xLimits'] == [None, None], vb.state['limits']
        radio_card(w)
        w.close()
    finally:
        fmapp.make_radio = original
    print("part 2 passed")


class FakeNativeSweeper:
    """The BB60D's own sweep, made up: a sweep of ``plan`` in dBm, with FM
    stations and, outside the FM band, a strong carrier at 900 MHz."""
    native = True
    TONES = [(95.1e6, -40.0), (101.7e6, -52.0), (900e6, -30.0)]

    def __init__(self, plan):
        self.plan = None
        self.sweeps = 0
        self.sweep_seconds = 0.23
        self.overflows = 0
        self.error = None
        self.running = True
        self.stopped = False
        self.set_plan(plan)

    def set_plan(self, plan, frames=None, settle_ms=None):
        bin_hz = plan.rbw / 3.2
        plan.set_trace(plan.start_hz, bin_hz, int((plan.stop_hz - plan.start_hz) / bin_hz) + 1)
        if plan.realtime:
            plan.poi_s = 307.2e-6
        self.plan = plan

    def density(self):
        """In real time, a map with the noise spread near the floor."""
        plan = self.plan
        if not plan.realtime:
            return None
        bottom, top = plan.ref_db - plan.scale_db, plan.ref_db
        rows = np.linspace(bottom, top, 256)
        column = np.exp(-0.5 * ((rows + 100) / 3) ** 2)
        frame = np.tile((column / column.sum())[:, None], (1, 525)).astype(np.float32)
        return frame, (plan.start_hz, plan.stop_hz, bottom, top)

    def snapshot(self):
        freqs = self.plan.freqs()
        rng = np.random.default_rng(self.sweeps)
        db = -100 + rng.normal(0, 1.5, len(freqs))
        for f, level in self.TONES:              # peaked, as an FM station is
            shape = level - 30 * np.abs(freqs - f) / 100e3
            db = np.maximum(db, shape)
        self.sweeps += 1
        return freqs, db, db, self.sweeps

    def set_paused(self, paused):
        pass

    def set_frames(self, frames):
        pass

    def set_settle_ms(self, ms):
        pass

    def stop(self):
        self.running = False
        self.stopped = True

    def detach(self):
        self.stop()


class NativeRadio(SimRadio):
    """Sweeps in the device, as the BB60D does, over 9 kHz-6 GHz; receives
    as the simulated radio."""
    native_sweep = True
    sweep_range_hz = (9e3, 6000e6)
    made = []

    def native_sweeper(self, plan):
        sweeper = FakeNativeSweeper(plan)
        NativeRadio.made.append(sweeper)
        return sweeper


def part3_native_sweep():
    """A radio that sweeps itself: the whole range from 9 kHz, an RBW in
    place of the LO-hopping settings, levels in dBm, and stations listed
    only in the FM band."""
    original = fmapp.make_radio, bb60_sweep.REALTIME_OK
    fmapp.make_radio = lambda kind, *a, **k: NativeRadio()
    bb60_sweep.REALTIME_OK = True            # as on Linux, wherever this runs
    try:
        w = make_window(['--radio', 'bb60', '--mode', 'sweep'], {'recording_dir': FOLDER})
        e = w.engine
        assert w._mode == 'sweep' and e.running and e.sweeper.native, w.status.text()
        assert (w.sweep_start.value(), w.sweep_stop.value()) == (9e3, 6000e6)
        assert w.sweep_start.text() == '0.009 MHz', w.sweep_start.text()
        assert w.rbw_combo.isVisible() and not w.fft_combo.isVisible()
        assert not w.settle_spin.isVisible() and not w.sweep_rate_combo.isVisible()
        assert w.rf_view.level_unit == 'dBm'
        assert pump(5, lambda: w.station_list.count() >= 2)
        found = [w.station_list.item(i).data(QtCore.Qt.UserRole)
                 for i in range(w.station_list.count())]
        # Within a channel: at the automatic 300 kHz RBW, points are 94 kHz apart.
        assert any(abs(f - 95.1e6) <= 100e3 for f in found), found
        assert all(f < 108.1e6 for f in found), found
        assert 'dBm' in w.station_list.item(0).text()
        pump(0.5)
        assert 'own sweep' in w.sweep_info.text() and 'GHz/s' in w.sweep_info.text()
        assert 'in the radio' in w.status.text(), w.status.text()
        # Too fine an RBW for 6 GHz is raised, and says so.
        sweeper = e.sweeper
        w.rbw_combo.setCurrentIndex(w.rbw_combo.findData(10e3))
        w._update_sweep_plan()
        assert sweeper.plan.rbw == 30e3 and sweeper.plan.raised, sweeper.plan.describe()
        w._preset_chosen(1)
        assert sweeper.plan.start_hz == 87.5e6 and sweeper.plan.rbw == 10e3
        # The channel band shows on the sweep too, on the tuner: a
        # middle-drag moves it, and that is where Listen and Real time will
        # start from. Nothing is retuned meanwhile - the radio is sweeping.
        view, port = w.rf_view, w.rf_view.plot.viewport()
        assert view.band.isVisible(), 'the channel band shows on a sweep'
        w.tune(98.7e6)
        low, high = view._band_hz
        assert abs((low + high) / 2 - 98.7e6) < 1, (low, high)
        assert abs(high - low - w.chan_entry.value()) < 1, (low, high)
        view.span_knob.setValue(4e6)
        view.set_center(98.7e6)
        pump(0.3)
        _middle_drag(port, _plot_point(view, 98.7e6), _plot_point(view, 99.21e6))
        assert abs(w.tuner.value() - 99.2e6) < 1, w.tuner.value()
        assert abs(w.sweep_tuner.value() - 99.2e6) < 1, w.sweep_tuner.value()
        assert e.mode == 'sweep' and e.rx is None, 'the radio is sweeping, not receiving'
        assert abs(e.station_hz - 99.2e6) < 1, 'but Receive starts there'
        # Zoomed out, a 200 kHz channel is thinner than a pixel: it is drawn
        # wide enough to stay a handle, without moving.
        view.span_knob.setValue(view.span_knob._max)
        pump(0.3)
        band_low, band_high = view.band.getRegion()
        assert band_high - band_low > 0.2, (band_low, band_high)
        assert abs((band_low + band_high) / 2 - 99.2) < 0.01, (band_low, band_high)
        assert view._band_hz == (99.2e6 - 100e3, 99.2e6 + 100e3), view._band_hz
        # Right click: the menu offers the tuner where the pointer is, at
        # the frequency Snap rounds to, and choosing it puts it there.
        at = _plot_point(view, 96.34e6)
        _move(port, at)
        _press(port, at, QtCore.Qt.RightButton, True)
        _press(port, at, QtCore.Qt.RightButton, False)
        pump(0.2)
        assert view._menu.isVisible(), 'the right button should offer the tuner'
        assert view._tune_action.text() == 'Tuner to 96.300 MHz', view._tune_action.text()
        assert abs(w.tuner.value() - 99.2e6) < 1, 'the menu alone moves nothing'
        view._tune_action.trigger()
        view._menu.close()
        pump(0.2)
        assert abs(w.tuner.value() - 96.3e6) < 1, w.tuner.value()
        assert abs(w.sweep_tuner.value() - 96.3e6) < 1, w.sweep_tuner.value()
        low, high = view._band_hz
        assert abs((low + high) / 2 - 96.3e6) < 1, 'the band goes with it'
        assert e.mode == 'sweep' and e.rx is None, 'still only sweeping'
        # Real time: the button drops the sweep to its 27 MHz window on the
        # tuner, with a density map behind the trace, placed by the view's
        # Ref level and Range.
        assert w.rt_btn.isEnabled() and not sweeper.plan.realtime
        w.tune(98.7e6)
        w.rt_btn.setChecked(True)
        span = (w.sweep_start.value(), w.sweep_stop.value())
        assert abs(span[1] - span[0] - RT_MAX_SPAN_HZ) < 1e3, span
        assert abs((span[0] + span[1]) / 2 - 98.7e6) < 1e3, span
        assert sweeper.plan.realtime and sweeper.plan.rbw == 10e3
        assert pump(3, lambda: w.rf_view.density_item.isVisible())
        pump(0.6)
        assert 'real time' in w.sweep_info.text() and 'real time' in w.status.text(), \
            (w.sweep_info.text(), w.status.text())
        w.rf_view.ref_knob.setValue(-30)
        assert pump(1, lambda: sweeper.plan.ref_db == -30), sweeper.plan.ref_db

        def map_db():
            item = w.rf_view.density_item
            r = item.mapRectToParent(item.boundingRect())
            return r.y(), r.y() + r.height()             # dB at its bottom and top
        assert pump(1, lambda: abs(map_db()[1] + 30) < 0.5), map_db()
        assert abs(map_db()[0] - (-30 - w.rf_view.range_knob.value())) < 0.5, map_db()
        # The tuner put outside the window moves the window, not the tuner.
        w.tune(200e6)
        span = (w.sweep_start.value(), w.sweep_stop.value())
        assert abs((span[0] + span[1]) / 2 - 200e6) < 1e3, span
        assert sweeper.plan.realtime and w.sweep_tuner.value() == 200e6
        # Widened past what real time takes, it sweeps instead and says so;
        # the button stays down, and letting it out keeps the wider span.
        w._preset_chosen(0)
        assert w.rt_btn.isEnabled() and w.rt_btn.isChecked()
        assert not sweeper.plan.realtime and sweeper.plan.too_wide
        assert not w.rf_view.density_item.isVisible()
        w.rt_btn.setChecked(False)
        assert (w.sweep_start.value(), w.sweep_stop.value()) == (9e3, 6000e6)
        # Pressed and let out again, the span it took comes back.
        w._preset_chosen(1)
        before = (w.sweep_start.value(), w.sweep_stop.value())
        w.rt_btn.setChecked(True)
        assert (w.sweep_start.value(), w.sweep_stop.value()) != before
        w.rt_btn.setChecked(False)
        assert (w.sweep_start.value(), w.sweep_stop.value()) == before
        assert not sweeper.plan.realtime
        w.tune(95.1e6)
        w.rt_btn.setChecked(True)
        assert sweeper.plan.realtime
        # To Receive: the sweep stops, levels are dBFS again; and back.
        def listed(hz):
            return [w.station_list.item(i) for i in range(w.station_list.count())
                    if abs(w.station_list.item(i).data(QtCore.Qt.UserRole) - hz) < 1]
        assert pump(5, lambda: listed(95.1e6)), 'the list fills again on the new span'
        item = listed(95.1e6)[0]
        w._station_activated(item)
        pump(0.5)
        assert w._mode == 'receive' and sweeper.stopped and w.rf_view.level_unit == 'dBFS'
        assert not w.rf_view.density_item.isVisible()
        w.tabs.setCurrentIndex(0)
        pump(0.3)
        assert w._mode == 'sweep' and e.sweeper is NativeRadio.made[-1] and e.sweeper.running
        w.close()
        saved = json.load(open(os.environ['FMRX_CONFIG']))
        # Real time left its own window on the tuner, so the band is custom.
        assert saved['sweep_rbw_khz'] == 10 and saved['sweep_band'] == 'custom', saved
        assert saved['sweep_realtime'] is True, saved
        assert abs(saved['sweep_stop_mhz'] - saved['sweep_start_mhz']
                   - RT_MAX_SPAN_HZ / 1e6) < 1e-3, saved
    finally:
        fmapp.make_radio, bb60_sweep.REALTIME_OK = original
    print("part 3 passed")


def part4_no_realtime():
    """Where Signal Hound's library has no real time (its Mac build): the
    box is clear and greyed even on the FM band with real time saved, says
    why, and the device sweeps instead."""
    original = fmapp.make_radio, bb60_sweep.REALTIME_OK
    fmapp.make_radio = lambda kind, *a, **k: NativeRadio()
    bb60_sweep.REALTIME_OK = False
    try:
        w = make_window(['--radio', 'bb60', '--mode', 'sweep'],
                        {'recording_dir': FOLDER, 'sweep_realtime': True})
        e = w.engine
        assert w._mode == 'sweep' and e.running and e.sweeper.native, w.status.text()
        w._preset_chosen(1)
        assert e.sweeper.plan.start_hz == 87.5e6
        assert not w.rt_btn.isEnabled() and not w.rt_btn.isChecked()
        assert 'Mac' in w.rt_btn.toolTip(), w.rt_btn.toolTip()
        w.rt_btn.setChecked(True)                # even if something presses it
        plan = e.sweeper.plan
        assert not plan.realtime and not plan.too_wide, plan.describe()
        pump(0.6)
        assert 'own sweep' in w.sweep_info.text(), w.sweep_info.text()
        assert not w.rf_view.density_item.isVisible()
        w.close()
    finally:
        fmapp.make_radio, bb60_sweep.REALTIME_OK = original
    print("part 4 passed")


class MacRadio(SimRadio):
    """A radio with a receive rate listed but not usable here, as the BB60D's
    2.5 MS/s on a Mac."""
    receive_rates = (1e6, 2e6)
    unavailable_rates = {1e6: "Not on a Mac: the library gives unreliable IQ at 1 MS/s."}


def part5_unavailable_rate():
    """A rate this computer can't use is listed greyed out with why; the keys
    and the wheel pass over it, and a saved choice of it gives the default.
    And the BB60D declares its 2.5 MS/s so on a Mac, and only there."""
    if sys.platform == 'darwin':
        assert 2.5e6 in radios.BB60.receive_rates
        assert 2.5e6 in radios.BB60.unavailable_rates
        assert radios.BB60.usable_receive_rates() == (5e6, 10e6)
    else:
        assert radios.BB60.unavailable_rates == {}
        assert radios.BB60.usable_receive_rates() == (2.5e6, 5e6, 10e6)
    original = fmapp.make_radio
    fmapp.make_radio = lambda kind, *a, **k: MacRadio()
    try:
        w = make_window(['--radio', 'hackrf', '--mode', 'receive'],
                        {'recording_dir': FOLDER, 'receive_rate': {'hackrf': 1e6}})
        combo = w.rx_rate_combo
        assert w._mode == 'receive' and w.engine.running, w.status.text()
        assert combo.count() == 2 and combo.itemText(0) == '1 MS/s', combo.itemText(0)
        assert not combo.model().item(0).isEnabled() and combo.model().item(1).isEnabled()
        assert 'Mac' in combo.itemData(0, QtCore.Qt.ToolTipRole)
        # Saved at the unusable rate: the default instead.
        assert combo.currentData() == 2e6 and w.engine.rate == 2e6, (combo.currentData(),
                                                                     w.engine.rate)
        combo.setFocus()
        QtTest.QTest.keyClick(combo, QtCore.Qt.Key_Up)
        QtTest.QTest.keyClick(combo, QtCore.Qt.Key_Home)
        _wheel(combo, QtCore.QPoint(10, 10), 1)
        _wheel(combo, QtCore.QPoint(10, 10), -1)
        pump(0.3)
        assert combo.currentData() == 2e6 and w.engine.rate == 2e6, combo.currentData()
        w.close()
    finally:
        fmapp.make_radio = original
    print("part 5 passed")


if __name__ == '__main__':
    keep = '--keep' in sys.argv
    try:
        part1_file_receiver()
        part2_sweep()
        part3_native_sweep()
        part4_no_realtime()
        part5_unavailable_rate()
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
