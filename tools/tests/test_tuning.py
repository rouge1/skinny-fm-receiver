"""Tuning, with no radio: where the tuner may go, the digit entry's carries,
and no ghost of the last band in the spectrum after the LO moves.

- ``Radio.clamp_offset``: the tuner stops at the band's edge and at the
  radio's range, and passes over a HackRF's DC spike the way it is going;
  the BB60D has no spike to avoid.
- ``on_raster``: a 200 kHz step lands on the Americas' odd-tenth channels.
- The BB60 module's load, as opening a BB60D does it, leaves the other
  SoapySDR drivers (the HackRF's) loaded.
- ``DigitEntry``: the wheel over a digit carries (90 -> 100 MHz on the tens,
  99 -> 100 on the ones), stops at the range, and typing sets the value.
- The ghost: a simulated radio that is slow to retune, a tone in the old
  band, the LO moved away. Every spectrum after the move must be clean of
  where the tone sat relative to the old LO - and, with the stale-sample
  discard switched off, is not.

Run:  python tools/tests/test_tuning.py        (a few seconds)
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt, QtCore, QtGui  # noqa: E402

from fm_receiver import radios  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.sweep import SweepPlan, to_db  # noqa: E402
from fm_receiver.widgets import DigitEntry, on_raster  # noqa: E402
from tests.test_sweep import slow_radio  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])


def test_clamp_offset():
    hack = radios.HackRF()                      # 2 MS/s: 75% usable, less 150 kHz
    assert hack.max_offset(2e6) == 600e3
    assert hack.max_offset(2.5e6 * 1.0) == 787e3, 'to the whole kHz'
    lo = 100e6
    assert hack.clamp_offset(900e3, 2e6, lo) == 600e3
    assert hack.clamp_offset(-2e6, 2e6, lo) == -600e3
    assert hack.clamp_offset(250e3, 2e6, lo) == 250e3
    # Into the DC keep-out: over it the way the tuner is going, else the
    # nearer side.
    assert hack.clamp_offset(30e3, 2e6, lo, direction=-1) == -100e3
    assert hack.clamp_offset(30e3, 2e6, lo, direction=+1) == 100e3
    assert hack.clamp_offset(-30e3, 2e6, lo) == -100e3
    # The radio's own range: a HackRF stops at 1 MHz.
    assert hack.clamp_offset(-500e3, 2e6, 1.2e6) == -200e3
    bb = radios.BB60()
    assert bb.min_offset_hz == 0 and bb.clamp_offset(0.0, 10e6, lo) == 0.0
    assert bb.max_offset(10e6) == 3600e3
    # tune_plan keeps the LO for a station that fits, else moves it.
    assert hack.tune_plan(100.4e6, 2e6, lo) == (lo, 400e3)
    assert hack.tune_plan(101e6, 2e6, lo) == (101e6 - 300e3, 300e3)
    assert bb.tune_plan(100.0e6, 10e6, lo) == (lo, 0.0)


def test_raster():
    # 200 kHz: the Americas' channels, on the odd tenths.
    assert abs(on_raster(99.13e6, 200e3) - 99.1e6) < 1
    assert abs(on_raster(107.5e6 + 200e3, 200e3) - 107.7e6) < 1
    assert abs(on_raster(88.0e6 + 60e3, 200e3) - 88.1e6) < 1
    # 100 kHz and the rest: multiples of the step.
    assert abs(on_raster(99.13e6, 100e3) - 99.1e6) < 1
    assert abs(on_raster(99.16e6, 100e3) - 99.2e6) < 1
    assert abs(on_raster(99.137e6, 10e3) - 99.14e6) < 1


def _wheel(widget, place, notches):
    for p, rect in widget._layout()[0]:
        if p == place:
            pos = rect.center()
            break
    QAPP.sendEvent(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, pos, QtCore.Qt.NoButton,
                                             QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    QAPP.sendEvent(widget, QtGui.QWheelEvent(pos, pos, QtCore.QPoint(),
                                             QtCore.QPoint(0, 120 * notches),
                                             QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                             QtCore.Qt.NoScrollPhase, False))


def _key(widget, key, text=''):
    QAPP.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key,
                                           QtCore.Qt.NoModifier, text))


def test_digit_entry():
    got = []
    entry = DigitEntry('MHz', 1e6, 4, 3, minimum_hz=1e6, maximum_hz=6000e6,
                       value_hz=90e6)
    entry.valueChanged.connect(got.append)
    entry.show()
    assert entry.text() == '90.000 MHz'
    _wheel(entry, 4, 1)                          # the tens of MHz
    assert entry.value() == 100e6 and got == [100e6], got
    entry.setValue(99e6)
    _wheel(entry, 3, 1)                          # the ones of MHz
    assert entry.value() == 100e6
    _wheel(entry, 2, -3)                         # 100 kHz, three notches down
    assert abs(entry.value() - 99.7e6) < 1
    _wheel(entry, 6, 9)                          # the thousands: stops at 6 GHz
    assert entry.value() == 6000e6
    entry.setValue(0.5e6)                        # below the range: clamped
    assert entry.value() == 1e6
    # A smooth wheel's half notches add up to one.
    entry.setValue(98.7e6)
    _wheel(entry, 2, 0)
    for _ in range(2):
        pos = QtCore.QPointF(5, 5)
        QAPP.sendEvent(entry, QtGui.QWheelEvent(pos, pos, QtCore.QPoint(), QtCore.QPoint(0, 60),
                                                QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                                QtCore.Qt.NoScrollPhase, False))
    assert abs(entry.value() - 98.8e6) < 1, entry.value()
    # Typing: a digit opens the editor with it, Enter sets the value.
    entry.setFocus()
    _key(entry, QtCore.Qt.Key_1, '1')
    editor = entry._editor
    assert editor is not None and editor.text() == '1'
    editor.setText('101.1')
    _key(editor, QtCore.Qt.Key_Return)
    assert entry._editor is None and abs(entry.value() - 101.1e6) < 1, entry.value()
    # Escape leaves it as it was.
    entry.edit()
    entry._editor.setText('88')
    _key(entry._editor, QtCore.Qt.Key_Escape)
    assert entry._editor is None and abs(entry.value() - 101.1e6) < 1
    entry.close()
    # The channel filter's: three digits of kHz.
    chan = DigitEntry('kHz', 1e3, 3, 0, minimum_hz=60e3, maximum_hz=236e3, value_hz=200e3)
    _wheel(chan, 1, 5)
    assert chan.value() == 236e3
    _wheel(chan, 0, -6)
    assert chan.value() == 230e3


class SlowRadio(radios.Radio):
    """Tones, and a retune that takes ``LATENCY`` samples to show. Its
    settling time is set to cover that, as a real radio's is."""
    kind = 'hackrf'
    name = 'Slow radio'
    receive_rates = (2e6,)
    default_receive_rate = 2e6
    LATENCY = 150000                              # 75 ms at 2 MS/s
    settle_ms = 80.0
    TONES = [(98.0e6, 0.5)]

    def open(self):
        self.block = slow_radio(2e6, self.TONES, latency=self.LATENCY)
        self.block.center = 97.7e6

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center(hz)


def ghost_after_move(discard=True):
    """The worst level seen where the tone sat, relative to the old LO, in
    the spectra after the LO moves 2.3 MHz away from it."""
    radio = SlowRadio()
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        tb.start_receive(98.0e6, 2e6, center_hz=97.7e6)
        rx = tb.rx
        probe = rx.rf_probe
        probe.set_alpha(1.0)
        deadline = time.time() + 10
        while probe.frames < 8 and time.time() < deadline:
            time.sleep(0.01)
        snap = probe.snapshot()
        n = len(snap)
        bin_hz = 2e6 / n
        k = n // 2 + int(round(300e3 / bin_hz))  # +300 kHz from the LO
        near = slice(k - 3, k + 4)
        before = to_db(snap)[near].max()
        if not discard:
            rx.discard_stale = lambda *a, **k: None
        tb.set_center(100.0e6)
        assert abs(tb.station_hz - 99.4e6) < 1, tb.station_hz
        worst = -300.0
        floor = []
        start = probe.frames
        deadline = time.time() + 10
        while probe.frames < start + 12 and time.time() < deadline:
            snap = probe.snapshot()
            if snap is not None:
                db = to_db(snap)
                worst = max(worst, db[near].max())
                floor.append(np.median(db))
            time.sleep(0.002)
        return before, worst, float(np.median(floor))
    finally:
        tb.close()


def test_retired_sweep_lets_go_of_the_radio():
    """A stopped sweep, kept referenced by the engine, must not keep the
    radio's block: on a HackRF that kept the device busy to other programs
    after Stop (found off air, 2026-09-22)."""
    radio = SlowRadio()
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        block = radio.block
        plan = SweepPlan(87.5e6, 108e6, 2e6, 1024, 0.75)
        tb.start_sweep(plan, frames=2, settle_ms=0)
        sweeper = tb.sweeper
        assert sweeper._source is block
        time.sleep(0.2)
        tb.start_receive(98.0e6, 2e6)
        assert sweeper in tb._retired and sweeper._source is None
        sweeper._tune(99e6)                       # harmless once detached
    finally:
        tb.close()
    assert radio.block is None


_MODULES_AFTER_BB60_LOAD = r'''
import SoapySDR
from fm_receiver import radios
radios._load_bb60_module()
# SoapySDR's own load, as the HackRF's open would do it. A driver no
# module has, so no device is looked for.
SoapySDR.Device.enumerate('driver=fmrx_no_such_driver')
# "" is a module loaded only now: both loads had left it out.
left_out = [p for p in SoapySDR.listModules() if SoapySDR.loadModule(p) == ""]
print(left_out)
sys.exit(1 if left_out else 0)
'''


def test_bb60_load_keeps_the_other_drivers():
    """Loading the BB60 module, as opening a BB60D does, must leave every
    other SoapySDR driver loadable: loading it by hand in a fresh process
    was once the only load there was, and a HackRF chosen after a BB60D
    that was not plugged in could not be found (2026-09-22). A fresh
    process, because SoapySDR loads its modules once per process.

    The check comes after an enumerate, the load the HackRF's open would
    get, so it fails only where that fault was: on Linux, where the BB60
    module is outside SoapySDR's own folder. On the Mac it is inside it,
    the old code loaded nothing by hand, and SoapySDR's load found
    everything."""
    import subprocess
    tools = os.path.dirname(HERE)
    proc = subprocess.run(
        [sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1])\n'
         + _MODULES_AFTER_BB60_LOAD, tools],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"SoapySDR modules left out: {proc.stdout.strip()} {proc.stderr[-500:]}"


def test_no_ghost_after_lo_move():
    before, worst, floor = ghost_after_move(discard=True)
    assert before > floor + 60, (before, floor)
    assert worst < floor + 20, f"ghost of the old band: {worst:.1f} dB, floor {floor:.1f}"
    # And the check has teeth: without the discard the old band shows.
    _, worst0, floor0 = ghost_after_move(discard=False)
    assert worst0 > floor0 + 40, f"expected a ghost without the discard: {worst0:.1f} vs {floor0:.1f}"
    print(f"after the LO move: {worst - floor:.1f} dB over the floor where the tone was "
          f"(without the discard: {worst0 - floor0:.1f} dB)")


if __name__ == '__main__':
    test_clamp_offset()
    test_raster()
    test_digit_entry()
    test_retired_sweep_lets_go_of_the_radio()
    test_bb60_load_keeps_the_other_drivers()
    test_no_ghost_after_lo_move()
    print('tuning: all checks passed')
