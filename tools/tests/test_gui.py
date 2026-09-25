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
why, and out of reach of the keys, the wheel and a saved setting. Part 6:
the RTL-SDR's network address box shows only with ``--rtl-address``, and
without it the RTL-SDR is this computer's.

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
    assert 'Band edge' in w.range_label.text(), w.range_label.text()
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
        w.left_scroll.ensureWidgetVisible(knob)       # a tall Receive tab scrolls
        pump(0.1)
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


def clipped_readout(w):
    """The clipped share on the status line: smoothed, in the good colour
    under 0.3%, amber to 1%, the overload warning over it - naming the
    sweep's step when a sweep clipped."""
    radio, counts = w.radio, w._clip_counts
    theme = fmapp.theme.TOKENS

    def show(*counts, ticks=12):
        w._clip_counts = lambda: counts
        for _ in range(ticks):
            w._clip_t -= 0.4                     # a status tick apart
            w._check_health()
        return w.status.text()

    try:
        radio.clip_warn = True
        w._overload_until = 0.0
        w._clip_smooth = None
        text = show('receive', 0, 1000)
        assert 'clipped 0.00%' in text and theme['good'] in text, text
        text = show('receive', 5, 1000)
        assert 'clipped 0.50%' in text and theme['warn'] in text, text
        # One tick of 5% after that: over the warning at once, and the
        # smoothed share moves only part of the way.
        text = show('receive', 50, 1000, ticks=1)
        assert 'overloaded' in text and '5.00% of samples clipped)' in text, text
        assert theme['bad'] in text, text
        assert 0.005 < w._clip_smooth < 0.05, w._clip_smooth
        # A sweep: its worst step, named.
        w._overload_until = 0.0
        text = show('sweep', 0.005, 533.5e6)
        assert 'clipped 0.50% at worst, in the step centred on 533.5 MHz' in text, text
        assert theme['warn'] in text, text
        text = show('sweep', 0.025, 533.5e6)
        assert 'overloaded' in text and theme['bad'] in text, text
        assert '2.50% of samples clipped in the step centred on 533.5 MHz' in text, text
        # Just opened: the clipped share is dropped, not shown - an RTL-SDR
        # clips for its first 0.4 s after being plugged in.
        w._clip_counts = counts
        opened_at, taken = w._opened_at, []
        if w._mode == 'receive' and w.engine.rx is not None:
            clip = w.engine.rx.clip
            real_take = clip.take
            clip.take = lambda: taken.append(1) or real_take()
            try:
                w._opened_at = time.monotonic()
                assert w._clip_counts() is None and taken, 'read and dropped'
                w._opened_at = time.monotonic() - fmapp.OPEN_CLIP_GRACE_S - 0.1
                assert w._clip_counts()[0] == 'receive'
            finally:
                del clip.take
                w._opened_at = opened_at
    finally:
        radio.clip_warn = False
        w._clip_counts = counts
        w._overload_until = 0.0
        w._clip_smooth = None
    print("clipped readout: good, amber, overloaded; a sweep names its worst step; "
          "none just after opening")


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
    clipped_readout(w)

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
    assert 'Band edge' in w.range_label.text()
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
        # What the sweep is doing: at the foot of the tab, under RF gain.
        outer = w._sweep_outer
        assert outer.indexOf(w.sweep_info) == outer.indexOf(w.gain_box) + 1
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
        self.paused = False
        self.silent = False                      # True: no data, as if unplugged
        self.gone = None                         # lost()'s reason, as if unplugged
        self.last_data = time.monotonic()
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
        if not self.silent:
            self.last_data = time.monotonic()
        return freqs, db, db, self.sweeps

    def lost(self):
        return self.gone

    def set_paused(self, paused):
        self.paused = bool(paused)

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
    has_realtime = True
    has_agc = True
    sweep_range_hz = (9e3, 6000e6)
    made = []

    def native_plan(self, start_hz, stop_hz, rbw_hz=None, **view):
        return bb60_sweep.NativeSweepPlan(start_hz, stop_hz, rbw_hz, **view)

    def native_sweeper(self, plan):
        sweeper = FakeNativeSweeper(plan)
        NativeRadio.made.append(sweeper)
        return sweeper


def device_health(w):
    """A radio reporting its health: the numbers as the status line's
    tooltip, and a warning below Signal Hound's 4.4 V."""
    radio = w.radio
    reading = {'dropped': 0, 'overload': 0, 'temp_c': 41.25, 'usb_v': 4.95, 'usb_a': 1.19}
    radio.health = lambda: dict(reading)
    try:
        w._check_health()
        assert w.status.toolTip() == f"{radio.name}: 41.2 °C, USB 4.95 V, 1.19 A", \
            w.status.toolTip()
        assert 'USB' not in w.status.text(), w.status.text()
        reading['usb_v'] = 4.31
        w._check_health()
        text = w.status.text()
        assert 'USB voltage low (4.31 V): measurements may be off' in text, text
        assert fmapp.theme.TOKENS['warn'] in text, text
    finally:
        del radio.health
    print("device health: tooltip, and the low USB voltage warning")


def realtime_polish(w):
    """Frames the window didn't draw still count: a burst only in the
    sweeper's held maximum reaches the trace and the next waterfall row.
    And the density map's persistence scales each pixel's opacity."""
    sweeper, view = w.engine.sweeper, w.rf_view
    freqs, db, _, _ = sweeper.snapshot()
    burst = db.copy()
    k = len(burst) // 3
    burst[k - 2:k + 3] = 0.0                      # far over anything else
    sweeper.take_held = lambda: burst
    try:
        w._last_wf = 0.0
        w._last_serial = -1
        w._draw_sweep()
        assert view._db[k] == 0.0, view._db[k]
        if view.wf_check.isChecked():
            assert np.nanmax(view._wf[0]) == 0.0, np.nanmax(view._wf[0])
    finally:
        del sweeper.take_held
    frame = np.zeros((256, 525), dtype=np.float32)
    frame[100, :] = 0.1
    alpha = np.zeros_like(frame)
    alpha[100, :200] = 1.0
    alpha[100, 200:] = 0.5
    view.set_density(frame, (88e6, 108e6, -120.0, -20.0), alpha)
    img = view.density_item.image
    assert img.shape == (256, 525, 4), img.shape
    full, half = int(img[100, 0, 3]), int(img[100, 300, 3])
    assert full > 0 and abs(half - full / 2) <= 1, (full, half)
    assert img[50, 0, 3] == 0                     # never hit: clear
    view.clear_density()
    print(f"real-time polish: a burst between draws reaches trace and waterfall; "
          f"persistence 0.5 halves the opacity ({full} -> {half})")


def agc(w):
    """AGC in the radio's own sweep: the slider greys and reads AGC, the plan
    leaves the gain to the device, and the Ref level knob moves to 5 dB over
    the strongest input, rounded up to 5 dB. In Receive the slider is back,
    saying why."""
    knob = w.rf_view.ref_knob
    knob.setValue(-90.0)                         # far below the -40 dBm station
    pump(0.3)
    assert not w.agc_box.isChecked() and w.agc_box.isVisible()
    assert w.gain_slider.isEnabled() and not w.engine.sweeper.plan.auto_gain
    w.agc_box.setChecked(True)
    assert not w.gain_slider.isEnabled() and w.gain_label.text() == 'AGC'
    assert pump(3, lambda: knob.value() != -90.0), 'the Ref level knob should move'
    plan = w.engine.sweeper.plan
    freqs, db, _, _ = w.engine.sweeper.snapshot()
    level = bb60_sweep.strongest_input(db, plan.bin_hz, plan.rbw)
    assert pump(1, lambda: w.engine.sweeper.plan.ref_db == knob.value())
    plan = w.engine.sweeper.plan
    assert plan.auto_gain and knob.value() % 5 == 0, (plan.auto_gain, knob.value())
    assert level + 4 < knob.value() < level + 12, (level, knob.value())
    assert 'AGC to' in plan.describe(), plan.describe()
    # A hand turn a little higher holds: nothing has risen past it.
    held = knob.value() + 5
    knob.setValue(held)
    pump(1)
    assert knob.value() == held, knob.value()
    # Receive: the window's own AGC there, the slider its ceiling (part 10).
    w.tabs.setCurrentIndex(1)
    pump(0.3)
    assert w._mode == 'receive' and w.gain_slider.isEnabled() and w._iq_agc_on()
    assert w.gain_label.text().startswith('AGC ')
    assert 'follows the gain AGC sets' in w.gain_slider.toolTip()
    w.tabs.setCurrentIndex(0)
    pump(0.3)
    assert w._mode == 'sweep' and not w.gain_slider.isEnabled()
    assert w.engine.sweeper.plan.auto_gain
    print(f"AGC: the Ref level moved from -90 to {held - 5:.0f} dBm for an input of "
          f"{level:.1f} dBm")


def part3_native_sweep():
    """A radio that sweeps itself: the whole range from 9 kHz, an RBW in
    place of the LO-hopping settings, levels in dBm, and stations listed
    only in the FM band."""
    original = fmapp.make_radio, bb60_sweep.REALTIME_OK
    fmapp.make_radio = lambda kind, *a, **k: NativeRadio()
    bb60_sweep.REALTIME_OK = True            # as on Linux, wherever this runs
    try:
        w = make_window(['--radio', 'bb60', '--mode', 'sweep', '--realtime'],
                        {'recording_dir': FOLDER})
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
        assert w.rt_btn.isEnabled() and not w.rt_btn.isHidden() and not sweeper.plan.realtime
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
        device_health(w)
        agc(w)
        realtime_polish(w)
        auto_scale(w)
        folding(w)
        level_axis(w)
        time_axis(w)
        w.close()
        saved = json.load(open(os.environ['FMRX_CONFIG']))
        # Real time left its own window on the tuner, so the band is custom.
        assert saved['sweep_rbw_khz'] == 10 and saved['sweep_band'] == 'custom', saved
        assert saved['sweep_realtime'] is True, saved
        assert saved['folded'] == {'radio': True, 'tuner': False, 'rds': True}, saved['folded']
        assert saved['gain_auto'] == {NativeRadio.kind: True}, saved['gain_auto']
        assert abs(saved['sweep_stop_mhz'] - saved['sweep_start_mhz']
                   - RT_MAX_SPAN_HZ / 1e6) < 1e-3, saved
    finally:
        fmapp.make_radio, bb60_sweep.REALTIME_OK = original
    print("part 3 passed")


def auto_scale(w):
    """A fits the scale to the trace on show: floor to peak, centred, with a
    margin; under AGC only the Range, the Ref level being the radio's."""
    view = w.rf_view
    view.set_data(np.linspace(95e6, 100e6, 1000),
                  np.where(np.arange(1000) == 500, -40.0, -100.0))
    ref = view.ref_knob.value()
    w._auto_scale()                              # AGC is on here
    assert view.ref_knob.value() == ref and view.range_knob.value() == 5 * np.ceil(
        max(2 * (ref + 70), 30) / 5), (ref, view.range_knob.value())
    assert view.auto_scale()
    top, span = view.ref_knob.value(), view.range_knob.value()
    assert span == 80 and top == -30, (top, span)       # -100..-40, 10 dB either side
    assert abs((top - span / 2) - (-70)) <= 0.5, 'the trace in the middle'


def level_axis(w):
    """The level axis: lit under the pointer, the wheel zooms the scale
    about the level under it, a middle drag moves the Ref level; the knobs
    follow."""
    w.agc_box.setChecked(False)                  # AGC would move the Ref level
    view = w.rf_view
    view.ref_knob.setValue(-20)
    view.range_knob.setValue(100)
    pump(0.2)
    port = view.plot.viewport()
    rect = view._axis_rect()
    at = view.plot.mapFromScene(QtCore.QPointF(rect.center().x(), rect.top() + rect.height() * 0.25))
    _move(port, at)
    pump(0.1)
    assert view._axis_hot, 'the axis should light under the pointer'
    vb = view.plot.getPlotItem().getViewBox()
    scene_y = view.plot.mapToScene(at).y()
    level = vb.mapSceneToView(QtCore.QPointF(vb.sceneBoundingRect().center().x(), scene_y)).y()
    _wheel(port, at, 1)                          # zoom in a notch
    pump(0.1)
    top, span = view.ref_knob.value(), view.range_knob.value()
    assert abs(span - 80) < 0.2, span
    after = vb.mapSceneToView(QtCore.QPointF(vb.sceneBoundingRect().center().x(), scene_y)).y()
    assert abs(after - level) < 0.5, ('the level under the pointer stays put', level, after)
    _send(port, QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(at),
                                  QtCore.Qt.MiddleButton, QtCore.Qt.MiddleButton,
                                  QtCore.Qt.NoModifier))
    for dy in range(5, 45, 5):                   # 40 px down
        _move(port, at + QtCore.QPoint(0, dy), QtCore.Qt.MiddleButton)
    _send(port, QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,
                                  QtCore.QPointF(at + QtCore.QPoint(0, 40)),
                                  QtCore.Qt.MiddleButton, QtCore.Qt.NoButton,
                                  QtCore.Qt.NoModifier))
    pump(0.1)
    moved = view.ref_knob.value() - top
    assert moved > 5, ('dragging down raises the Ref level', top, view.ref_knob.value())
    assert view.range_knob.value() == span, 'a drag leaves the Range'
    _move(port, QtCore.QPoint(port.width() // 2, port.height() // 2))
    pump(0.1)
    assert not view._axis_hot
    print(f"level axis: lit, wheel to {span:.0f} dB about {level:.1f}, drag moved Ref {moved:+.1f} dB")
    w.agc_box.setChecked(True)                   # as it was


def time_axis(w):
    """The waterfall's time scale: marked in time, lit under the pointer,
    and the wheel over it shows more or less of the past (kept for five
    minutes), "now" staying at the top."""
    from fm_receiver.widgets import TimeAxis, _ago
    view = w.rf_view
    assert [_ago(v) for v in (0, 5, 30, 90, 150)] == ['now', '5 s', '30 s', '1:30', '2:30']
    assert isinstance(view.wf_plot.getAxis('left'), TimeAxis)
    port = view.wf_plot.viewport()
    rect = view._time_axis_rect()
    at = view.wf_plot.mapFromScene(rect.center())
    _move(port, at)
    pump(0.1)
    assert view._time_hot, 'the time scale should light under the pointer'
    before = view.wf_span_s
    _wheel(port, at, -2)                         # two notches down: more of the past
    pump(0.1)
    assert abs(view.wf_span_s - before * 1.25 ** 2) < 1e-6, (before, view.wf_span_s)
    (_, _), (y0, y1) = view.wf_plot.getPlotItem().getViewBox().viewRange()
    assert abs(y0) < 1e-6 and abs(y1 - view.wf_span_s) < 1e-6, (y0, y1)
    _wheel(port, at, 40)                         # all the way in: the floor
    assert view.wf_span_s == view.WF_SPAN_S[0]
    view.set_wf_span(before)
    _move(port, QtCore.QPoint(port.width() // 2, port.height() // 2))
    pump(0.1)
    assert not view._time_hot
    assert view.state()['wf_span_s'] == before
    print(f"time scale: lit, the wheel showed {before * 1.25 ** 2:.2f} s, down to {view.WF_SPAN_S[0]:g} s")


def folding(w):
    """The Receive cards fold on their chevron; the Radio card keeps its
    Center in sight, the Tuner card its tuner and range."""
    radio, tuner, rds = (w._cards[k] for k in ('radio', 'tuner', 'rds'))
    w.tabs.setCurrentIndex(1)
    pump(0.3)
    full = radio.height()
    radio.chevron.click()                        # slides shut over 0.2 s
    pump(0.1)
    assert radio.is_folded() and radio.maximumHeight() < full, (radio.maximumHeight(), full)
    pump(0.4)
    assert w.rx_gain_slot.isHidden() and w.rx_rate_combo.isHidden()
    assert radio.height() < full and radio.maximumHeight() > 10000, 'the limit is let go'
    assert not w.center_entry.isHidden() and not w.recenter_btn.isHidden()
    tuner.set_folded(True)                       # the tuner and its range, no Step
    assert not w.tuner.isHidden() and not w.roller.isHidden()
    assert not w.range_label.isHidden() and w.range_label.text().startswith('\u2194')
    assert 'MHz' in w.range_label.text() and 'kHz' not in w.range_label.text()
    gap = w.tuner.geometry().top() - w.range_label.geometry().bottom()
    assert 0 < gap < 8, ('right on top of the tuner', gap)
    over = w.range_label.geometry().center().x() - w.tuner.geometry().center().x()
    assert abs(over) <= 1, ('centred over the digits', over)
    assert w.step_knob.isHidden() and w.chan_entry.isHidden()
    tuner.set_folded(False)
    assert not w.step_knob.isHidden() and not w.chan_entry.isHidden()
    assert not w.range_label.isHidden()
    rds.set_folded(True)                         # Now playing and RadioText
    assert not w.lbl['radiotext'].isHidden() and not w.lbl['nowplaying'].isHidden()
    assert w.lbl['pi'].isHidden() and w.clear_btn.isHidden()
    # RF gain is its box in the Sweep tab while sweeping, a row of the
    # Radio card in Receive, and its box under the tabs elsewhere; Audio
    # and Record are hidden in Sweep. The tabs are as tall as the page on
    # show.
    w.tabs.setCurrentIndex(0)
    pump(0.3)
    assert w.tabs.widget(0).isAncestorOf(w.gain_box) and w.gain_box.isAncestorOf(w.gain_row)
    assert not w.gain_box.isHidden()
    assert w.audio_box.isHidden() and w.record_box.isHidden()
    w.tabs.setCurrentIndex(1)
    pump(0.3)
    page = w.tabs.widget(1)
    assert page.isAncestorOf(w.gain_row) and w.gain_box.isHidden()
    assert w.rx_gain_slot.isAncestorOf(w.gain_row)     # folded away with Radio
    assert radio._form.labelForField(w.rx_gain_slot).text() == "RF gain:"
    assert not w.audio_box.isHidden() and not w.record_box.isHidden()
    page = w.tabs.widget(1)
    assert w.tabs.sizeHint().height() < page.sizeHint().height() + 80, \
        (w.tabs.sizeHint(), page.sizeHint())
    w.tabs.setCurrentIndex(0)
    pump(0.3)


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
        assert w.rt_btn.isHidden(), 'hidden without --realtime'
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
        assert radios.BB60.usable_receive_rates() == (5e6, 10e6, 20e6, 40e6)
    else:
        assert radios.BB60.unavailable_rates == {}
        assert radios.BB60.usable_receive_rates() == (2.5e6, 5e6, 10e6, 20e6, 40e6)
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


def part6_rtl_address():
    """The RTL-SDR on another computer is an advanced use: its address box
    is hidden unless the app was started with --rtl-address, and without the
    flag the radio is this computer's (blank address), whatever the box or
    the settings hold."""
    opened = []

    def fake_make_radio(kind, usrp_address='', file_path='', rtl_address=''):
        opened.append(rtl_address)
        radio = SimRadio()
        radio.kind, radio.address = 'rtlsdr', rtl_address.strip()
        return radio

    original = fmapp.make_radio
    fmapp.make_radio = fake_make_radio
    try:
        w = make_window(['--radio', 'rtlsdr', '--mode', 'receive'],
                        {'recording_dir': FOLDER, 'rtl_address': 'macmini'})
        assert w.radio_combo.currentData() == 'rtlsdr'
        assert w.rtl_edit.isHidden(), 'no box without --rtl-address'
        assert opened == [''], opened
        w.close()

        opened.clear()
        w = make_window(['--radio', 'rtlsdr', '--mode', 'receive',
                         '--rtl-address', 'macmini'])
        assert not w.rtl_edit.isHidden(), 'the box with --rtl-address'
        assert opened == ['macmini'], opened
        w.rtl_edit.setText('otherhost:1300')
        w.rtl_edit.editingFinished.emit()
        pump(0.2)
        assert opened[-1] == 'otherhost:1300', opened
        w.rtl_edit.editingFinished.emit()      # unchanged: no reopen
        pump(0.2)
        assert len(opened) == 2, opened
        w._use_radio('hackrf')
        assert w.rtl_edit.isHidden(), 'hidden for another radio'
        w.close()
    finally:
        fmapp.make_radio = original
    print("part 6 passed")


def part7_lost_radio():
    """A radio that stops sending says so in the status line, whichever
    radio it is: the samples stopping in Receive, in a LO-hopping sweep and
    in rtl_433,
    a radio's own sweep going quiet (but not while paused), and at once
    when the radio knows why (an RTL-SDR's closed connection). It clears
    when the samples come back."""
    original = fmapp.make_radio, fmapp.STALL_S, bb60_sweep.REALTIME_OK
    fmapp.STALL_S = 1.0
    lost = lambda w: 'No samples from the Simulated radio' in w.status.text()  # noqa: E731
    try:
        fmapp.make_radio = lambda kind, *a, **k: SimRadio()
        w = make_window(['--radio', 'hackrf', '--mode', 'receive', '--freq', '89.3'])
        assert w._mode == 'receive' and w.engine.running, w.status.text()
        pump(1.5)
        assert not lost(w), w.status.text()
        for mode in ('receive', 'sweep'):
            w.tabs.setCurrentIndex(fmapp.TAB_MODES.index(mode))
            assert w._mode == mode and w.engine.running, w.status.text()
            pump(1.5)
            assert not lost(w), (mode, w.status.text())
            w.radio.block.silent = True
            assert pump(4, lambda: lost(w)), (mode, w.status.text())
            assert 'is it still connected' in w.status.text()
            w.radio.block.silent = False
            assert pump(4, lambda: not lost(w)), (mode, w.status.text())
        # A radio that can tell why says so at once.
        w.radio.lost = lambda: 'rtl_tcp closed the connection.'
        assert pump(2, lambda: 'Simulated radio lost: rtl_tcp closed' in w.status.text()), \
            w.status.text()
        assert 'Stop, then Start' in w.status.text()
        w.close()

        fmapp.make_radio = lambda kind, *a, **k: NativeRadio()
        bb60_sweep.REALTIME_OK = True
        w = make_window(['--radio', 'bb60', '--mode', 'sweep'])
        sweeper = w.engine.sweeper
        assert sweeper.native, w.status.text()
        pump(1.5)
        assert not lost(w), w.status.text()
        sweeper.silent = True
        assert pump(4, lambda: lost(w)), w.status.text()
        sweeper.silent = False
        assert pump(4, lambda: not lost(w)), w.status.text()
        # Paused, a quiet sweep is expected; resumed, it is counted afresh.
        w.pause_btn.click()
        assert sweeper.paused
        sweeper.silent = True
        pump(2.5)
        assert not lost(w), w.status.text()
        sweeper.silent = False
        w.pause_btn.click()
        assert not sweeper.paused
        pump(1.5)
        assert not lost(w), w.status.text()
        # A sweep that knows its device is gone says so at once.
        sweeper.gone = "the USB stream stopped"
        assert pump(1.5, lambda: 'Simulated radio lost: the USB stream stopped' in w.status.text()), \
            w.status.text()
        sweeper.gone = None
        assert pump(1.5, lambda: 'lost' not in w.status.text()), w.status.text()
        w.close()
    finally:
        fmapp.make_radio, fmapp.STALL_S, bb60_sweep.REALTIME_OK = original
    print("part 7 passed")


def part8_reopen():
    """A lost radio seen gone from the USB, and then back, is opened again
    by itself, in the mode it was in; a first open that fails is tried
    again. One that stops sending while still plugged in is left to Stop
    and Start - opened again, it would only stop again."""
    saved = (fmapp.make_radio, fmapp.plugged_in, fmapp.STALL_S, fmapp.LOST_POLL_S,
             fmapp.BACK_SETTLE_S)
    usb = {'hackrf': True}
    opened, refuse = [], [0]

    def fake_make_radio(kind, *a, **k):
        radio = SimRadio()
        if refuse[0]:
            refuse[0] -= 1
            def fail():
                raise radios.RadioError("No HackRF One was found.")
            radio.open = fail
        opened.append(radio)
        return radio

    fmapp.make_radio = fake_make_radio
    fmapp.plugged_in = lambda kind, address='': usb.get(kind)
    fmapp.STALL_S, fmapp.LOST_POLL_S, fmapp.BACK_SETTLE_S = 1.0, 0.2, 0.5
    status = lambda w: w.status.text()  # noqa: E731
    try:
        w = make_window(['--radio', 'hackrf', '--mode', 'sweep', '--freq', '89.3'])
        assert w._mode == 'sweep' and len(opened) == 1, w.status.text()
        # Unplugged: silent, and gone from the USB.
        opened[-1].block.silent = True
        usb['hackrf'] = False
        assert pump(5, lambda: 'unplugged - plug it back in' in status(w)), status(w)
        # Back, but the first open fails (still starting up): tried again.
        refuse[0] = 1
        usb['hackrf'] = True
        assert pump(3, lambda: 'is back - opening it again' in status(w)), status(w)
        assert pump(6, lambda: len(opened) >= 3 and w.radio is opened[-1]
                    and w.engine.running), (len(opened), status(w))
        assert w._mode == 'sweep', w._mode
        pump(1.5)
        assert 'lost' not in status(w) and 'No samples' not in status(w), status(w)
        assert w._lost is None
        # Silent while still plugged in: said, but not opened again.
        n = len(opened)
        w.radio.block.silent = True
        assert pump(4, lambda: 'No samples from the Simulated radio' in status(w)), status(w)
        assert 'Stop, then Start' in status(w)
        pump(2)
        assert len(opened) == n, 'a radio that never left the USB was reopened'
        # Stop forgets the watch.
        w.run_btn.click()
        assert w._lost is None and w.radio is None
        w.close()
    finally:
        (fmapp.make_radio, fmapp.plugged_in, fmapp.STALL_S, fmapp.LOST_POLL_S,
         fmapp.BACK_SETTLE_S) = saved
    print("part 8 passed")


def part9_rtl433():
    """rtl_433 in Receive, its card (the tab it had went): off, nothing
    extra runs and the multiplex is alone under the spectrum. The 433.92
    preset sets the rate, Center and tuner for the whole band and decodes:
    six 250 kHz slices beside the station, and a devices tab beside the
    multiplex, shown. A message is shown as RDS shows a station, its level
    in dBFS, and listed. The Center moves the slices without a restart;
    one 250 kHz slice follows the tuner. Bad Options say why. Unticked,
    rtl_433 ends and the devices tab goes. (Decoding itself: test_rtl433.)"""
    original = fmapp.make_radio
    try:
        fmapp.make_radio = lambda kind, *a, **k: SimRadio()
        w = make_window(['--radio', 'hackrf', '--mode', 'receive', '--freq', '89.3'])
        e = w.engine
        card = w.rtl_lbl
        installed = fmapp.rtl433.find_program() is not None
        assert fmapp.TAB_MODES == ('sweep', 'receive', 'recordings') and w.tabs.count() == 3
        assert not w.rx_rtl_check.isChecked() and e.decoder is None
        assert card['band'].text() == 'Off', card['band'].text()
        assert w.bottom.indexOf(w.devices_table) < 0 and w.bottom.tabBar().isHidden()
        # The 433.92 MHz preset: the LO on it, the whole band (2 MS/s, 75%
        # usable) in six 250 kHz slices, one rtl_433 each.
        i = w.rtl_preset.findData(433.92e6)
        w.rtl_preset.setCurrentIndex(i)
        w._rtl_preset_chosen(i)
        assert pump(3, lambda: e.running and e.mode == 'receive' and e.decoder is not None)
        d = e.decoder
        assert w.rx_rtl_check.isChecked() and abs(e.lo_hz - 433.92e6) < 1, e.lo_hz
        assert e.rx is not None and len(d.slices) == 6 and d.predecim == 1
        assert w.rtl_width_combo.currentText() == 'Whole band - 1.50 MHz, 6 slices', \
            w.rtl_width_combo.currentText()
        assert w.bottom.currentWidget() is w.devices_table and not w.bottom.tabBar().isHidden()
        assert w.audio_box.isVisible() and w.record_box.isVisible()
        assert 'rtl_433 on 433.17 MHz to 434.67 MHz in 6 slices' in w.status.text(), \
            w.status.text()
        procs = [p.proc for p in d.procs]
        if not installed:
            assert 'not installed' in card['band'].text()
        else:
            assert len(d.procs) == 6 and e.decode_error is None, e.decode_error
            assert '6 slices of 250 kHz' in card['band'].text(), card['band'].text()
            assert 'middle' not in card['band'].text()
            # Heard later than the samples flowing now: only this block counts.
            later = time.time() + 100
            d.slices[2][1].history.append((later - 0.5, 10.0, 1e-4, 0.05))
            d.procs[2]._new.append((later, {
                'model': 'Oregon-THGR122N', 'id': 77, 'channel': 1, 'freq': 433.9,
                'temperature_C': 19.5, 'rssi': -3.0, 'snr': 20.0, 'noise': -23.0}))
            assert pump(3, lambda: card['device'].text() == 'Oregon-THGR122N'), \
                card['device'].text()
            # rtl_433's -3 dB, of samples raised 20 dB: -23 dBFS, 57 over a -80 floor.
            assert card['signal'].text().startswith('-23.0 dBFS in slice'), card['signal'].text()
            assert '57 dB' in card['signal'].text() and 'above the floor' in card['signal'].text()
            assert card['id'].text() == 'id 77, channel 1'
            assert 'temperature_C 19.5' in card['readings'].text()
            assert card['freq'].text() == '433.900 MHz' and card['devices'].text().startswith('1,')
            table = w.devices_table
            assert table.rowCount() == 1
            cells = [table.item(0, c).text() for c in range(len(fmapp.DEVICE_COLUMNS))]
            assert cells[1:5] == ['Oregon-THGR122N', '77', '1', '433.900'], cells
            assert cells[6] == '-23.0 dBFS, SNR 57', cells
            assert 'temperature_C: 19.5' in table.item(0, 0).toolTip()   # all it sent
            # The Center moves 200 kHz: the slices go with it, rtl_433 is not
            # restarted, and what it says from then on is put right.
            low = d.low_hz
            w.center_entry.setValue(e.lo_hz + 200e3, emit=True)
            assert pump(2, lambda: abs(d.low_hz - low - 200e3) < 1), d.low_hz - low
            assert e.decoder is d and all(p.poll() is None for p in procs)
            d.procs[0]._new.append((later + 1, {'model': 'Moved', 'freq': 433.5}))
            assert pump(3, lambda: card['device'].text() == 'Moved')
            assert card['freq'].text() == '433.700 MHz', card['freq'].text()
            assert w.rtl_preset.currentData() == 433.92e6       # still in the band decoded
            w._clear_devices()
            assert card['device'].text() == '-' and card['devices'].text() == 'None yet'
            assert w.devices_table.rowCount() == 0
        # One 250 kHz slice, at the tuner and following it.
        w.rtl_width_combo.setCurrentIndex(w.rtl_width_combo.findData(250e3))
        w._rtl_restart()
        assert pump(3, lambda: e.running and e.decoder is not None and e.decoder is not d)
        d = e.decoder
        if installed:
            assert all(p.poll() is not None for p in procs), "a slice's rtl_433 still runs"
            assert 'at the tuner' in card['band'].text(), card['band'].text()
        assert len(d.slices) == 1 and abs(d.freq_hz - e.station_hz) < 1
        w.tune(e.station_hz + 100e3)
        assert e.decoder is d and abs(d.freq_hz - e.station_hz) < 1, (d.freq_hz, e.station_hz)
        # Options that do not parse say so, and are left out.
        w.rtl_args.setText('-X "n=unfinished')
        w._rtl_args_changed()
        assert pump(3, lambda: e.running and e.decoder is not None and e.decoder is not d)
        if installed:
            assert pump(2, lambda: 'Options left out' in card['quality'].text()), \
                card['quality'].text()
        w.rtl_args.setText('')
        w._rtl_args_changed()
        assert pump(3, lambda: e.running and e.decoder is not None)
        # Unticked: rtl_433 ends, the devices tab goes.
        procs = [p.proc for p in e.decoder.procs]
        w.rx_rtl_check.setChecked(False)
        assert pump(3, lambda: e.running and e.mode == 'receive' and e.decoder is None)
        assert all(p.poll() is not None for p in procs), "rtl_433 still runs, unticked"
        assert w.bottom.indexOf(w.devices_table) < 0 and w.bottom.currentWidget() is w.rx_page
        assert card['band'].text() == 'Off'
        w.close()
        # --rtl433-freq: Receive, set for rtl_433 there, decoding.
        w = make_window(['--radio', 'hackrf', '--rtl433-freq', '433.92'])
        e = w.engine
        assert pump(3, lambda: e.running and e.decoder is not None), w.status.text()
        assert w._mode == 'receive' and abs(e.lo_hz - 433.92e6) < 1
        w.close()
    finally:
        fmapp.make_radio = original
    print("part 9 passed")


def iq_agc_steps():
    """IqAgc alone: down 10% once overloads show in two polls within 1.5 s, nothing
    judged within a second of a change, up 5% after a minute quiet, never
    over the slider, and a rise that brings the overload back doubles the
    wait before the next and is only undone, not stepped a whole 10% under."""
    agc = fmapp.IqAgc(60, 60)
    assert agc.update(0.0, 2) is None                     # one poll: not yet
    assert agc.update(0.4, 1) == 50                       # two: down
    assert agc.update(0.8, 3) is None                     # its own gap
    assert agc.update(1.6, 2) is None and agc.update(2.0, 0) is None    # an empty poll
    assert agc.update(2.4, 2) == 40                       # between two: still down
    one = fmapp.IqAgc(60, 60)                             # two, but 2 s apart: no
    assert one.update(0.0, 1) is None and one.update(2.0, 1) is None
    t = 2.4
    for _ in range(3):                                    # quiet from here
        t += 0.4
        assert agc.update(t, 0) is None
    assert agc.update(t + 60.0, 0) == 45                  # a minute quiet: up
    t += 60.0
    assert agc.update(t + 1.2, 1) is None and agc.update(t + 1.6, 1) == 40   # undone
    assert agc.rise_wait == 120.0, agc.rise_wait          # the rise overloaded
    assert agc.update(t + 70.0, 0) is None                # 60 s is not enough now
    assert agc.update(t + 1.6 + 121.0, 0) == 45
    top = fmapp.IqAgc(50, 48)
    top.update(0.0, 0)
    assert top.update(61.0, 0) == 50 and top.update(200.0, 0) is None   # the slider
    low = fmapp.IqAgc(5, 5)
    low.update(0.0, 1)
    assert low.update(0.4, 1) == 0 and low.update(2.0, 1) is None       # 0% is the end
    # Clipping a little, not calm: no overload, but no rise either.
    light = fmapp.IqAgc(60, 60)                           # a light overload: half a step
    light.update(0.0, 1, heavy=False)
    assert light.update(0.4, 1, heavy=False) == 55
    warm = fmapp.IqAgc(60, 40)
    for t in range(0, 300, 1):
        assert warm.update(float(t), 0, calm=False) is None
    assert warm.update(300.4, 0) is None and warm.update(361.0, 0) == 45
    print("IQ AGC: steps down on a held overload, up after quiet, capped by the "
          "slider, backs off a rise that overloads, holds while it clips a little")


class OverloadRadio(SimRadio):
    """A simulated BB60D that overloads over ``limit`` % gain: it reports an
    ADC overload each poll, and sends nothing, as the real one does."""
    kind = 'bb60'
    name = 'Simulated BB60D'
    has_agc = True
    limit = 42.0

    def open(self):
        super().open()
        self.overloads = 0
        self.applied = []

    def apply_gain(self, percent=None):
        super().apply_gain(percent)
        self.applied.append(self.gain_percent)

    def health(self):
        if self.block is not None and self.gain_percent > self.limit:
            self.overloads += 2
            self.block.silent = True
        elif self.block is not None:
            self.block.silent = False
        return {'overload': self.overloads}


class ClipRadio(SimRadio):
    """A simulated HackRF: no overload reports, its clipping counted."""
    clip_warn = True


def part10_iq_agc():
    """AGC in Receive, with rtl_433 decoding and without, on a radio that
    overloads over 42%: the gain comes down by itself until it stops, the
    slider stays the ceiling,
    the silence is called an overload rather than a lost radio, and
    unticking AGC keeps the gain it found. The slider follows AGC; where it
    was last put by hand is the limit, saved apart. The same on a HackRF from its
    clipped share, held while it clips only a little, and greyed in Sweep."""
    iq_agc_steps()
    saved = fmapp.make_radio, fmapp.STALL_S
    fmapp.STALL_S = 1.0
    try:
        fmapp.make_radio = lambda kind, *a, **k: OverloadRadio()
        for decode in (False, True):                  # and with rtl_433 decoding
            w = make_window(['--radio', 'bb60', '--mode', 'receive', '--freq', '89.3'],
                            config={'recording_dir': FOLDER, 'rx_rtl433': decode})
            r = w.radio
            assert w.agc_box.isVisible()
            w.gain_slider.setValue(60)
            w.agc_box.setChecked(True)
            assert w._iq_agc_on() and w.gain_label.text() == 'AGC 60%', w.gain_label.text()
            assert 'turn AGC off' in w.agc_box.toolTip()
            seen_agc_status = []
            ok = pump(10, lambda: (seen_agc_status.append('AGC is turning' in w.status.text())
                                   or r.gain_percent <= r.limit and w.engine.running))
            assert ok, (decode, r.gain_percent, r.applied)
            assert r.gain_percent == 40 and r.applied[-2:] == [50, 40], r.applied
            assert any(seen_agc_status), "the status line never said AGC was at work"
            # The slider follows AGC; where it was put by hand is the limit.
            assert w.gain_slider.value() == 40 and w.gain_label.text() == 'AGC 40%'
            assert w._agc_ceiling == 60 and 'up to 60%' in w.gain_slider.toolTip()
            pump(2.5)
            text = w.status.text()
            assert 'No samples' not in text and 'lost' not in text, text
            assert r.gain_percent == 40, r.applied              # settled, no hunting
            if not decode:
                # Saved apart: the slider where AGC left it, the limit where
                # it was put; a new window starts at the first, may go to the second.
                w._remember_radio_settings()
                assert w.cfg['gain']['bb60'] == 40 and w.cfg['agc_ceiling']['bb60'] == 60
                again = make_window(['--radio', 'bb60', '--mode', 'sweep', '--freq', '89.3'],
                                    config={'recording_dir': FOLDER, 'gain': {'bb60': 40},
                                            'agc_ceiling': {'bb60': 60}})
                assert again.gain_slider.value() == 40 and again._agc_ceiling == 60
                again.close()
                # A move by hand under AGC: the new limit, and the gain.
                w.gain_slider.setValue(30)
                assert w._agc_ceiling == 30 and r.gain_percent == 30
                pump(1)
                assert r.gain_percent == 30 and w.gain_slider.value() == 30
            # Off: the gain stays where AGC put it, and the slider is there.
            w.agc_box.setChecked(False)
            assert w.gain_slider.value() == r.gain_percent, (w.gain_slider.value(), r.gain_percent)
            assert w.gain_label.text() == f'{w.gain_slider.value()}%', w.gain_label.text()
            w.close()
        # A HackRF: its clipped share drives it; nothing in Sweep.
        fmapp.make_radio = lambda kind, *a, **k: ClipRadio()
        for decode in (False, True):
            w = make_window(['--radio', 'hackrf', '--mode', 'receive', '--freq', '89.3'],
                            config={'recording_dir': FOLDER, 'rx_rtl433': decode})
            r = w.radio
            share = lambda: 0.5 if r.gain_percent > 42 else 0.0           # noqa: E731
            w._clip_counts = lambda: ('receive', int(share() * 1e4), 10000)
            w.gain_slider.setValue(60)
            w.agc_box.setChecked(True)
            assert w.agc_box.isVisible() and w.agc_box.isEnabled() and w._iq_agc_on()
            tip = w.agc_box.toolTip()
            assert '5% when over 1%' in tip and 'turn AGC off' in tip, tip
            said = []
            assert pump(10, lambda: said.append('AGC is turning the gain down' in w.status.text())
                        or r.gain_percent == 40), (decode, r.gain_percent)
            assert any(said), "the status line never said AGC was at work"
            pump(2)
            assert r.gain_percent == 40 and w.gain_slider.value() == 40 and w._agc_ceiling == 60
            if not decode:
                # A light overload (3%) takes a half step: 40 to 35.
                w._iq_agc.changed_at = None
                w._clip_counts = lambda: ('receive', 300, 10000)
                assert pump(3, lambda: r.gain_percent == 35), r.gain_percent
                w._clip_counts = lambda: ('receive', 0, 10000)
                pump(1.2)
                # Clipping a little (0.5%): held, and not called an overload.
                w._iq_agc.quiet_since -= 120
                w._clip_counts = lambda: ('receive', 50, 10000)
                pump(1.5)
                assert r.gain_percent == 35, r.gain_percent
                # Sweep: the box keeps its tick, greyed; the slider is the gain.
                w.tabs.setCurrentIndex(fmapp.TAB_MODES.index('sweep'))
                pump(0.5)
                assert w.agc_box.isChecked() and not w.agc_box.isEnabled()
                assert w.gain_slider.isEnabled() and w.gain_label.text() == '35%', \
                    w.gain_label.text()
            w.close()
        # With AGC off, a silent overloaded radio still says overloaded, not lost.
        fmapp.make_radio = lambda kind, *a, **k: OverloadRadio()
        w = make_window(['--radio', 'bb60', '--mode', 'receive', '--freq', '89.3'])
        w.agc_box.setChecked(False)
        w.gain_slider.setValue(60)
        pump(4)
        text = w.status.text()
        assert 'Input overloaded - turn the RF gain down' in text and 'No samples' not in text, text
        w.close()
    finally:
        fmapp.make_radio, fmapp.STALL_S = saved
    print("part 10 passed")


if __name__ == '__main__':
    keep = '--keep' in sys.argv
    try:
        part1_file_receiver()
        part2_sweep()
        part3_native_sweep()
        part4_no_realtime()
        part5_unavailable_rate()
        part6_rtl_address()
        part7_lost_radio()
        part8_reopen()
        part9_rtl433()
        part10_iq_agc()
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
