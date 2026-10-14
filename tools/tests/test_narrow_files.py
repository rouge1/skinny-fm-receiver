"""Narrow IQ files and the spectrum marker, in the Recordings tab - no radio.

Synthetic files only, written to a temporary folder. The window is driven as
``fmctl`` drives it (over its control socket), and the mouse as a user's is.

Part 0 - the tuner's range (``radios.Radio.max_offset``): a file of R samples
a second lets the tuner go LO +/- max(0, R/2 * usable - min(150 kHz, R/8)).
So a 54.7 kS/s file can be tuned inside its own band, and from 1.2 MS/s up
every radio's margin is still the 150 kHz it always was.

Part 1 - a 54,687.5 S/s BLUE file (a CW at +3 kHz) played in Recordings: the
tuner takes distinct places inside the band and spans its range, typed and
dragged (the channel band, middle button); the channel filter shown and the
band drawn are min(200 kHz, band), the band centred on the tuner; the Span
dial runs from 500 Hz to the band (a 250 kS/s file from 1.25 kHz, a 20 MS/s
file keeps 50 kHz); ``view rf span 5`` is a 5 kHz window centred on the
tuner, ``view rf span full`` the band.

Part 2 - the spectrum marker in Recordings: on a ``.fft`` and a sweep
``.sdvr`` a right click offers "Marker to", choosing it places the marker, so
does a left click, a drag moves it, the readout gives the frequency and the
level at that bin, and ``fmctl marker FREQ`` places and reads it; an IQ
track's menu still reads "Tuner to", moves the tuner and leaves the marker.

Run:  python tools/tests/test_narrow_files.py     (about 5 s)
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt, QtCore, QtGui  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-narrow-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')
os.environ['FMRX_CONTROL'] = os.path.join(FOLDER, 'control.sock')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import radios  # noqa: E402
from fm_receiver.control import ControlServer  # noqa: E402
from tests import signals  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
FMCTL = os.path.join(os.path.dirname(HERE), 'fmctl')

#: The narrow file: a BLUE IQ export at 54,687.5 S/s, centred on 100.1 MHz.
LO = 100.1e6
LO_MHZ = 100.1
NARROW_RATE = 54687.5
BAND = NARROW_RATE                        # an IQ recording's band is its rate
#: The wide one: 250 kS/s, centred on 98.5 MHz.
WIDE_RATE = 250e3
WIDE_LO_MHZ = 98.5
#: A band wider than 10 MHz: 20 MS/s, centred on 90 MHz (a short file).
HUGE_RATE = 20e6
HUGE_LO_MHZ = 90.0
#: The narrow file's channel filter as the app's dial starts it.
FILTER_HZ = 200e3
#: A tolerance on MHz values that a float or a 1 kHz tuner step leaves.
EPS = 1e-6

#: The spectrum files: as test_sceptre_spectra writes them.
SPEC_BINS, SPEC_TILES, SPEC_SWEEPS, SPEC_INTERVAL = 4096, 8, 16, 0.2325
FFT_ROWS = 12

WINDOWS = []
RESULTS = []
#: Exceptions the window raised in a slot: PyQt5 would abort on them, so
#: they are caught here, and a check that saw one fails.
SLOT_ERRORS = []


def _excepthook(kind, value, tb):
    SLOT_ERRORS.append(f"{kind.__name__}: {value}")
    traceback.print_exception(kind, value, tb)


sys.excepthook = _excepthook


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def send(widget, event):
    QAPP.sendEvent(widget, event)
    QAPP.processEvents()


def move(widget, pos, buttons=QtCore.Qt.NoButton):
    # pyqtgraph drops moves closer together than its rate limit (10 ms).
    time.sleep(0.012)
    send(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(pos),
                                   QtCore.Qt.NoButton, buttons, QtCore.Qt.NoModifier))


def press(widget, pos, button, down):
    kind = QtCore.QEvent.MouseButtonPress if down else QtCore.QEvent.MouseButtonRelease
    send(widget, QtGui.QMouseEvent(kind, QtCore.QPointF(pos), button,
                                   button if down else QtCore.Qt.NoButton,
                                   QtCore.Qt.NoModifier))


def click(port, at, button=QtCore.Qt.LeftButton):
    move(port, at)
    press(port, at, button, True)
    press(port, at, button, False)
    pump(0.2)


def drag(port, start, end, steps=8):
    move(port, start)
    press(port, start, QtCore.Qt.LeftButton, True)
    for i in range(1, steps + 1):
        f = i / steps
        at = QtCore.QPoint(int(round(start.x() + (end.x() - start.x()) * f)), start.y())
        move(port, at, QtCore.Qt.LeftButton)
    press(port, end, QtCore.Qt.LeftButton, False)
    pump(0.2)


def middle_drag(port, start, end, steps=8):
    """The channel band's drag: hover first, which claims the drag."""
    move(port, start)
    press(port, start, QtCore.Qt.MiddleButton, True)
    for i in range(1, steps + 1):
        f = i / steps
        at = QtCore.QPoint(int(round(start.x() + (end.x() - start.x()) * f)), start.y())
        move(port, at, QtCore.Qt.MiddleButton)
    press(port, end, QtCore.Qt.MiddleButton, False)
    pump(0.2)


def frame_at(view, frac):
    """A frequency across the whole of the view's band, in its own unit."""
    low, high = view.full
    return (low + frac * (high - low)) / view.scale


def point(view, value, y_frac=0.5):
    """Where ``value`` (the view's unit) is on the spectrum, in the viewport's pixels."""
    vb = view.plot.getPlotItem().getViewBox()
    (_, _), (y0, y1) = vb.viewRange()
    scene = vb.mapViewToScene(QtCore.QPointF(value, y0 + (y1 - y0) * y_frac))
    return view.plot.mapFromScene(scene)


def value_at(view, pos):
    vb = view.plot.getPlotItem().getViewBox()
    return vb.mapSceneToView(view.plot.mapToScene(pos)).x()


def settle(view, limit=3.0):
    """Pump until the plot's width has held still for 0.3 s: the map column slides
    in when a recording is chosen (240 ms), and a pixel maps to another frequency
    until it has. Raises if the layout is still moving after ``limit`` seconds."""
    port = view.plot.viewport()
    end = time.time() + limit
    last, since = port.width(), time.time()
    while time.time() < end:
        QAPP.processEvents()
        time.sleep(0.02)
        if port.width() != last:
            last, since = port.width(), time.time()
        elif time.time() - since >= 0.3:
            return
    raise AssertionError(f"the plot is still moving after {limit:.0f} s (now {port.width()} px)")


def bin_mhz(view):
    return float(np.median(np.diff(view._x)))


def tolerance(view):
    """One bin of the view, and never less than the tuner's 1 kHz step."""
    return max(bin_mhz(view), 0.0005)


def band_mhz():
    half = BAND / 2e6
    return LO_MHZ - half, LO_MHZ + half


def file_offset(rate):
    """The spec's tuner offset for an IQ file of ``rate``: LO +/- this."""
    usable = radios.IQFile().usable_fraction(rate)
    return max(0.0, rate * usable / 2 - min(radios.Radio.EDGE_MARGIN_HZ, rate / 8))


class Client:
    """A client on the socket, the window's events pumped while it waits."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(os.environ['FMRX_CONTROL'])
        self.sock.setblocking(False)
        self.data = b''

    def send(self, line):
        self.sock.sendall(line.encode() + b'\n')

    def reply(self, timeout=10.0):
        def got():
            try:
                self.data += self.sock.recv(65536)
            except BlockingIOError:
                pass
            return b'\n' in self.data
        assert pump(timeout, got), 'no reply'
        line, self.data = self.data.split(b'\n', 1)
        return json.loads(line)

    def ask(self, line, timeout=10.0):
        self.send(line)
        return self.reply(timeout)

    def ok(self, line):
        reply = self.ask(line)
        assert reply['ok'], f"{line!r} failed: {reply.get('error')}"
        return reply['result']


def fmctl(*argv, timeout=15.0):
    proc = subprocess.Popen([sys.executable, FMCTL, *argv], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    assert pump(timeout, lambda: proc.poll() is not None), 'fmctl hung'
    out, err = proc.communicate()
    return proc.returncode, out, err


def tune_ok(c, mhz):
    reply = c.ask(f'tune {mhz}')
    assert reply['ok'], f"tune {mhz} was refused: {reply.get('error')}"
    return reply['result']['tuner_mhz']


def find_row(c, station_mhz, kind_word):
    for r in c.ok('recordings')['recordings']:
        if r['station_mhz'] is not None and abs(r['station_mhz'] - station_mhz) < 1e-6 \
                and kind_word in r['kinds']:
            return r
    return None


def marker_of(c):
    s = c.ok('status')
    assert 'marker_mhz' in s, 'status has no marker_mhz'
    return s['marker_mhz']


def own(name):
    return signals.ensure_dir(os.path.join(FOLDER, 'own-' + name))


def open_window(folder, name, station):
    args = fmapp.parse_args(['--file', station, '--no-sound-card'])
    # Snap off: the menu's frequency is the pointer's, not the tuner step's.
    w = fmapp.MainWindow(args, {'recording_dir': own(name), 'library_folders': [folder],
                                'snap': False})
    WINDOWS.append(w)
    # A plot wide enough to read a bin by the pointer (offscreen it defaults small).
    w.resize(1200, 800)
    w.show()
    pump(0.2)
    w.start_initial()
    control = ControlServer(w)
    assert control.start(), 'no control socket: another window has it'
    return w, control, Client()


def close_session(S):
    """Pause what the window plays and let go of its socket. The window itself
    stays open (its timers would call a closed window's pool), and main closes
    it at the end."""
    try:
        if 'w' in S:
            S['w'].play_btn.setChecked(False)
        if 'control' in S:
            S['control'].close()
    except Exception:
        traceback.print_exc()


def close_windows():
    for w in WINDOWS:
        try:
            w.close()
        except Exception:
            traceback.print_exc()


def run(name, fn, *args):
    """One named check. What it raises is its failure; the run goes on."""
    before = len(SLOT_ERRORS)
    msg = None
    try:
        fn(*args)
    except AssertionError as exc:
        msg = str(exc) or 'assertion failed'
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    if len(SLOT_ERRORS) > before and msg is None:
        msg = f"the window raised {SLOT_ERRORS[before]}"
    RESULTS.append((name, msg))
    if msg is None:
        print(f"PASS  {name}", flush=True)
    else:
        print(f"FAIL  {name}\n      {msg}", flush=True)


def need(S):
    if not S.get('ready'):
        raise AssertionError('not run: the window or its setup failed before this')


# ============================================================ part 0

def check_margin():
    """An IQ file's tuner offset: max(0, R/2 * usable - min(150 kHz, R/8))."""
    for rate in (NARROW_RATE, WIDE_RATE):
        got = radios.IQFile().max_offset(rate)
        want = file_offset(rate)
        assert abs(got - want) <= 1e3, (
            f"IQFile.max_offset({rate:g}) is {got:.0f} Hz, want {want:.0f} Hz: "
            "R/2 x usable less min(150 kHz, R/8)")


def check_unchanged():
    """From 1.2 MS/s up every radio's margin is still the 150 kHz."""
    for radio in (radios.IQFile(), radios.HackRF(), radios.USRP(), radios.BB60(),
                  radios.RTLSDR()):
        for rate in (1.2e6, 2e6, 10e6):
            want = rate * radio.usable_fraction(rate) / 2 - 150e3
            got = radio.max_offset(rate)
            assert abs(got - want) <= 1e3, (
                f"{type(radio).__name__}.max_offset({rate:g}) is {got:.0f} Hz, want "
                f"{want:.0f} Hz (the 150 kHz margin)")


# ============================================================ part 1

def write_blue_files(folder):
    t = np.arange(int(4.4 * NARROW_RATE)) / NARROW_RATE
    cw = (0.5 * np.exp(2j * np.pi * 3000.0 * t)).astype(np.complex64)
    signals.write_blue(os.path.join(folder, 'narrow-54k.cdif'), cw, rate=NARROW_RATE,
                       fmt='CF', keywords={'RF_FREQ': LO})
    t = np.arange(int(2.0 * WIDE_RATE)) / WIDE_RATE
    cw = (0.5 * np.exp(2j * np.pi * 10e3 * t)).astype(np.complex64)
    signals.write_blue(os.path.join(folder, 'wide-250k.cdif'), cw, rate=WIDE_RATE,
                       fmt='CF', keywords={'RF_FREQ': WIDE_LO_MHZ * 1e6})
    # 50 ms at 20 MS/s: a band wider than 10 MHz, and quick to write.
    huge = np.zeros(int(0.05 * HUGE_RATE), dtype=np.complex64)
    signals.write_blue(os.path.join(folder, 'huge-20m.cdif'), huge, rate=HUGE_RATE,
                       fmt='CF', keywords={'RF_FREQ': HUGE_LO_MHZ * 1e6})


def setup_narrow(S):
    c = S['c']
    c.ok('filter 200')                    # Receive's: the filter wide, before the file
    c.ok('mode recordings')
    pump(10, lambda: find_row(c, LO_MHZ, 'BLUE') is not None)
    row = find_row(c, LO_MHZ, 'BLUE')
    assert row is not None, (
        f"no BLUE recording at {LO_MHZ} MHz; the list has "
        f"{[r['station_mhz'] for r in c.ok('recordings')['recordings']]}")
    c.ok(f"recording {row['index']}")
    assert c.ok('status')['playback']['kind'] == 'cdif', 'the BLUE file is not the track'
    c.ok('play on')
    assert pump(15, lambda: c.ok('status')['running']), 'the file did not start playing'
    S['view'] = S['w'].rf_view
    S['ready'] = True


def check_a_distinct(S):
    need(S)
    lo, hi = band_mhz()
    got = [tune_ok(S['c'], mhz) for mhz in (100.1015, 100.09, 100.11)]
    for v in got:
        assert lo - EPS <= v <= hi + EPS, (
            f"tuner_mhz {v} is outside the file's band {lo:.6f}-{hi:.6f} MHz")
    assert len(set(got)) == 3, f"three tune values gave the tuner {got}: not three places"


def check_a_range(S):
    need(S)
    lo, hi = S['c'].ok('status')['tuner_range_mhz']
    r = file_offset(NARROW_RATE) / 1e6
    assert abs(lo - (LO_MHZ - r)) <= 0.001 and abs(hi - (LO_MHZ + r)) <= 0.001, (
        f"tuner_range_mhz is {lo}-{hi}, want LO +/- {r * 1e3:.2f} kHz "
        f"({LO_MHZ - r:.6f}-{LO_MHZ + r:.6f})")


def sweep_grid(S):
    """Request the grid once; the checks on it read what it found."""
    if 'grid' in S:
        return
    c = S['c']
    S['range'] = tuple(c.ok('status')['tuner_range_mhz'])
    grid = []
    for k in range(-50, 51):                      # 2 kHz steps, 100 kHz either side of LO
        f = round(LO_MHZ + k * 0.002, 6)
        reply = c.ask(f'tune {f}')
        grid.append((f, reply['result']['tuner_mhz'] if reply['ok'] else None))
    S['grid'] = grid


def check_b_band(S):
    need(S)
    sweep_grid(S)
    lo, hi = band_mhz()
    bad = [(f, t) for f, t in S['grid'] if t is not None and not lo - EPS <= t <= hi + EPS]
    assert not bad, (f"{len(bad)} requests put the tuner outside the file's band "
                     f"{lo:.6f}-{hi:.6f} MHz, e.g. request {bad[0][0]} placed at {bad[0][1]}")
    t = S['c'].ok('status')['tuner_mhz']
    assert lo - EPS <= t <= hi + EPS, f"the tuner stands at {t} MHz, outside the band"


def check_b_moves(S):
    need(S)
    sweep_grid(S)
    rlo, rhi = S['range']
    placed = [t for f, t in S['grid'] if t is not None and rlo <= f <= rhi]
    assert placed, f"no request inside the tuner range {rlo}-{rhi} MHz was placed"
    spread = max(placed) - min(placed)
    assert spread >= 0.8 * (rhi - rlo), (
        f"the tuner's placed positions span {spread * 1e3:.1f} kHz of its "
        f"{(rhi - rlo) * 1e3:.1f} kHz range: the tuner is pinned")


def check_b_placed(S):
    need(S)
    sweep_grid(S)
    rlo, rhi = S['range']
    assert rhi > rlo, (f"the tuner range {rlo}-{rhi} MHz is empty: no request inside the "
                       "band can be placed")
    # Requests within 2 kHz of an edge are left out: the edge is a clamp.
    inside = [(f, t) for f, t in S['grid'] if rlo + 0.002 <= f <= rhi - 0.002]
    assert inside, f"no request lies in the tuner's range {rlo}-{rhi} MHz"
    missed = [(f, t) for f, t in inside if t is None or abs(t - f) > 0.001 + EPS]
    assert not missed, (f"{len(missed)} of {len(inside)} requests inside the tuner's range "
                        f"{rlo}-{rhi} MHz were not placed within 1 kHz, e.g. {missed[0]}")


def drawn_state(S):
    """(the channel band drawn, the tuner in MHz) now."""
    band = S['view']._band_hz
    assert band is not None, 'no channel band is drawn in Recordings'
    return band, S['c'].ok('status')['tuner_mhz']


def check_b_drag(S):
    need(S)
    view, c = S['view'], S['c']
    before = c.ok('status')['tuner_mhz']
    target = LO_MHZ - 0.008                       # 8 kHz down: inside the range
    middle_drag(view.plot.viewport(), point(view, before), point(view, target))
    got = c.ok('status')['tuner_mhz']
    lo, hi = band_mhz()
    assert abs(got - target) <= 0.002, (
        f"dragging the channel band from {before} to {target} MHz left the tuner at {got}")
    assert lo - EPS <= got <= hi + EPS, f"the tuner is at {got}, outside the band"


def check_c_shown(S):
    """The filter shown is min(200 kHz, band), at the start, after a tune, after span full."""
    need(S)
    c = S['c']
    want = min(FILTER_HZ, BAND) / 1e3
    seen = [('now', c.ok('status')['channel_filter_khz'])]
    c.ask(f'tune {LO_MHZ}')                       # a refusal is check a's to report
    seen.append(('after a tune', c.ok('status')['channel_filter_khz']))
    c.ok('view rf span full')
    seen.append(('after view rf span full', c.ok('status')['channel_filter_khz']))
    for when, khz in seen:
        assert abs(khz - want) <= 0.001, (
            f"{when}: channel_filter_khz is {khz}, want min(200, band) = {want:.3f} kHz")


def check_c_drawn(S):
    """The band drawn is min(200 kHz, band) wide and centred on the tuner."""
    need(S)
    c = S['c']
    want = min(FILTER_HZ, BAND)
    steps = [('now', None), ('after a tune', ('tune', LO_MHZ)),
             ('after view rf span full', ('span', None))]
    for when, action in steps:
        if action and action[0] == 'tune':
            c.ask(f'tune {action[1]}')
        elif action and action[0] == 'span':
            c.ok('view rf span full')
        band, tuner = drawn_state(S)
        width = band[1] - band[0]
        assert abs(width - want) <= 1, (
            f"{when}: the channel band is drawn {width / 1e3:.2f} kHz wide, want "
            f"min(200 kHz, band) = {want / 1e3:.2f} kHz")
        centre = (band[0] + band[1]) / 2
        assert abs(centre - tuner * 1e6) <= 1e3, (
            f"{when}: the channel band is centred on {centre / 1e6:.6f} MHz, the tuner "
            f"is at {tuner} MHz")


def check_c_knob(S):
    need(S)
    knob = S['w'].chan_knob.value()
    assert abs(knob - FILTER_HZ) < 1, (f"the Channel filter dial is {knob / 1e3:.1f} kHz: "
                                       "the 200 kHz the app saves was changed")


def check_d_range(S):
    need(S)
    span = S['view'].span_knob
    lo, hi = span._min, span._max
    assert abs(lo - 500) <= 1 and abs(hi - BAND) <= 1, (
        f"the Span range is {lo:.1f}-{hi:.1f} Hz, want 500 to {BAND:.1f} Hz "
        "(max(500 Hz, band/200) to the band)")


def check_d_span5(S):
    need(S)
    c = S['c']
    c.ask('tune 100.1015')                        # a refusal is check a's to report
    tuner = c.ok('status')['tuner_mhz']
    r = c.ok('view rf span 5')
    assert abs(r['span'] - 0.005) < 1e-6, f"view rf span 5 gives span {r['span']} MHz, want 0.005"
    x0, x1 = r['shown']
    assert abs((x1 - x0) - 0.005) < 1e-4, (
        f"the shown window is {(x1 - x0) * 1e3:.2f} kHz wide, want 5 kHz")
    centre = (x0 + x1) / 2
    assert abs(centre - tuner) <= 0.0005, (
        f"the 5 kHz window is centred on {centre} MHz, {abs(centre - tuner) * 1e3:.2f} kHz "
        f"from the tuner at {tuner} MHz")


def check_d_full(S):
    need(S)
    # 'full' is the span's word in control.py's view command ('view rf span full').
    r = S['c'].ok('view rf span full')
    assert abs(r['span'] - BAND / 1e6) < 1e-5, (
        f"view rf span full gives span {r['span']} MHz, want {BAND / 1e6:.6f} (the band)")
    lo, hi = band_mhz()
    low, high = r['shown']
    assert abs(low - lo) <= 0.001 and abs(high - hi) <= 0.001, (
        f"view rf span full shows {low}-{high} MHz, want the band {lo:.6f}-{hi:.6f}")


def check_d_wide(S):
    need(S)
    c = S['c']
    row = find_row(c, WIDE_LO_MHZ, 'BLUE')
    assert row is not None, 'the 250 kS/s BLUE file is not listed'
    c.ok(f"recording {row['index']}")
    span = S['w'].rf_view.span_knob
    lo, hi = span._min, span._max
    assert abs(lo - 1250) <= 1 and abs(hi - WIDE_RATE) <= 1, (
        f"the 250 kS/s file's Span range is {lo:.1f}-{hi:.1f} Hz, want 1250 to 250000 Hz")


def check_d_huge(S):
    """A band wider than 10 MHz keeps the 50 kHz minimum span."""
    need(S)
    c = S['c']
    pump(10, lambda: find_row(c, HUGE_LO_MHZ, 'BLUE') is not None)
    row = find_row(c, HUGE_LO_MHZ, 'BLUE')
    assert row is not None, 'the 20 MS/s BLUE file is not listed'
    c.ok(f"recording {row['index']}")
    span = S['w'].rf_view.span_knob
    lo, hi = span._min, span._max
    assert abs(lo - 50e3) <= 1 and abs(hi - HUGE_RATE) <= 1, (
        f"the 20 MS/s file's Span range is {lo:.1f}-{hi:.1f} Hz, want 50000 to 20000000 Hz "
        "(bands over 10 MHz keep the 50 kHz minimum)")


def part1_narrow(folder, station):
    S = {'station': station}
    run('setup: the window opens onto the BLUE files', open_into, S, folder, 'narrow')
    try:
        run('setup: the 54.7 kS/s BLUE is chosen in Recordings and plays', setup_narrow, S)
        run('a: three tune values give three distinct tuner places inside the band',
            check_a_distinct, S)
        run('a: the tuner range is LO +/- the file offset (about 17.8 kHz), not a sliver',
            check_a_range, S)
        run('b: no request from 100 kHz below to 100 kHz above LO puts the tuner outside the band',
            check_b_band, S)
        run('b: the tuner moves across its whole range (not pinned)', check_b_moves, S)
        run('b: requests away from the range edges are placed within 1 kHz',
            check_b_placed, S)
        run('b: a middle-drag of the channel band moves the tuner, inside the band',
            check_b_drag, S)
        run('c: the channel filter shown is min(200 kHz, band), re-read after a tune and span full',
            check_c_shown, S)
        run('c: the drawn channel band is min(200 kHz, band) wide, centred on the tuner',
            check_c_drawn, S)
        run('c: the Channel filter dial keeps its 200 kHz', check_c_knob, S)
        run('d: the Span range is 500 Hz to the band', check_d_range, S)
        run('d: view rf span 5 is a 5 kHz window centred on the tuner', check_d_span5, S)
        run('d: view rf span full shows the band', check_d_full, S)
        run("d: a 250 kS/s BLUE file's Span range is 1.25 kHz to 250 kHz", check_d_wide, S)
        run('d: a band wider than 10 MHz (20 MS/s) keeps a 50 kHz minimum span', check_d_huge, S)
    finally:
        close_session(S)


def open_into(S, folder, name):
    """Open a window on ``folder`` into S, started on the station file."""
    S['w'], S['control'], S['c'] = open_window(folder, name, S['station'])


# ============================================================ part 2

def write_spectra(folder):
    k = np.arange(SPEC_BINS)
    db = -95.0 + 6.0 * np.sin(k / 97.0)
    for bin_, level in ((600, -65.0), (1500, -70.0), (3000, -75.0)):
        db[bin_] = level
    levels = np.clip(db, -105.0, -62.0)
    signals.write_sweep_sdvr(os.path.join(folder, 'sweep.sdvr'), tiles=SPEC_TILES,
                             bins=SPEC_BINS, sweeps=SPEC_SWEEPS, interval=SPEC_INTERVAL,
                             dbm=levels, age=60.0)
    data = (-100.0 + np.arange(FFT_ROWS)[:, None] + 0.01 * np.arange(2048)[None, :])
    signals.write_blue(os.path.join(folder, '2026-09-29_23-24-27_3000004883hz_spec.fft'),
                       data.astype('<f4'), rate=1.0 / 0.2322876, fmt='SF', subsize=2048,
                       keywords={'RF_FREQ': 3.0e9, 'TIME_EPOCH': '2026-09-29T23:24:27Z'})
    rng = np.random.default_rng(1)
    iq = (0.05 * (rng.standard_normal(1 << 17) + 1j * rng.standard_normal(1 << 17))).astype(np.complex64)
    signals.write_sdvr(os.path.join(folder, 'iq.sdvr'), iq, 2.5e6, 98.4e6, age=60.0)


def choose(S, kind, play):
    """Recordings, the first recording of ``kind`` chosen; played once to draw it."""
    w, c = S['w'], S['c']
    c.ok('mode recordings')
    assert pump(10, lambda: any(r.tracks[0].kind == kind for r in w._recordings)), \
        f"no {kind} recording in the list"
    index = next(i for i, r in enumerate(w._recordings) if r.tracks[0].kind == kind)
    c.ok(f'recording {index}')
    S['view'] = w.rf_view
    if play:
        c.ok('play on')
        assert pump(15, lambda: w.rf_view._x is not None), 'the spectrum was not drawn'
        c.ok('play off')
    settle(w.rf_view)
    S['ready'] = True


def check_no_marker(S):
    need(S)
    s = S['c'].ok('status')
    assert 'marker_mhz' in s, 'status has no marker_mhz'
    assert s['marker_mhz'] is None, f"marker_mhz is {s['marker_mhz']} before any marker is placed"


def check_menu(S):
    need(S)
    view = S['view']
    settle(view)
    at = point(view, frame_at(view, 0.25))
    move(view.plot.viewport(), at)
    f_pointer = value_at(view, at)              # before the press: the layout is settled
    press(view.plot.viewport(), at, QtCore.Qt.RightButton, True)
    press(view.plot.viewport(), at, QtCore.Qt.RightButton, False)
    pump(0.2)
    text = view._tune_action.text()
    view._menu.close()
    assert text.startswith('Marker to'), f"the right-click menu reads {text!r}, want 'Marker to <f> MHz'"
    found = re.search(r'(-?\d+\.\d+) ' + re.escape(view.unit), text)
    assert found, f"the menu {text!r} has no frequency"
    f_menu = float(found.group(1))
    # The menu shows the pointer's frequency to 3 places: one display step (1 kHz).
    assert abs(f_menu - f_pointer) <= 0.001, (
        f"the menu offers {f_menu} {view.unit} for a pointer on {f_pointer:.4f}")
    S['menu_mhz'] = f_menu


def check_menu_places(S):
    need(S)
    S['view']._tune_action.trigger()
    pump(0.2)
    got = marker_of(S['c'])
    assert got is not None and abs(got - S['menu_mhz']) <= tolerance(S['view']), (
        f"choosing 'Marker to' put the marker at {got} MHz, want {S['menu_mhz']}")


def check_click(S):
    need(S)
    view = S['view']
    settle(view)
    at = point(view, frame_at(view, 0.6))
    move(view.plot.viewport(), at)
    want = value_at(view, at)                   # before the press
    press(view.plot.viewport(), at, QtCore.Qt.LeftButton, True)
    press(view.plot.viewport(), at, QtCore.Qt.LeftButton, False)
    pump(0.2)
    got = marker_of(S['c'])
    assert got is not None and abs(got - want) <= tolerance(view), (
        f"a left click on {want:.4f} {view.unit} left the marker at {got}")


def check_drag(S):
    need(S)
    view, c = S['view'], S['c']
    before = marker_of(c)
    assert before is not None, 'no marker to drag'
    settle(view)
    start = point(view, before)
    end = point(view, frame_at(view, 0.35))
    expect = value_at(view, end)                # before the drag
    drag(view.plot.viewport(), start, end)
    got = marker_of(c)
    assert got is not None and abs(got - expect) <= tolerance(view), (
        f"dragging the marker from {before:.4f} to {expect:.4f} {view.unit} left it at {got}")


def check_readout(S):
    need(S)
    view, port = S['view'], S['view'].plot.viewport()
    assert view.level_unit == 'dBm', (
        f"the level unit is {view.level_unit}: a calibrated spectrum reads dBm")
    assert port.width() >= 400, f"the plot is {port.width()} px wide: too narrow to read a bin"
    marker = marker_of(S['c'])
    assert marker is not None, 'no marker to read'
    settle(view)
    at = point(view, marker)
    move(port, at)
    pump(0.1)
    text = view.readout.text()
    found = re.search(r'(-?\d+\.\d+) ' + re.escape(view.unit) + r'\s+(-?\d+\.\d+) '
                      + re.escape(view.level_unit), text)
    assert found, f"the readout reads {text!r}: no frequency and level in {view.level_unit}"
    f_r, level = float(found.group(1)), float(found.group(2))
    x, db = view._x, view._db
    i = int(np.argmin(np.abs(x - f_r)))
    # The pointer is a whole pixel, so the readout's bin is one bin and a pixel from the marker.
    px = (x[-1] - x[0]) / port.width()
    assert abs(f_r - marker) <= tolerance(view) + px, (
        f"the readout's frequency {f_r} is not at the marker {marker}")
    near = [db[j] for j in (i - 1, i, i + 1) if 0 <= j < len(db)]
    assert min(abs(level - v) for v in near) <= 0.1, (
        f"the readout's level {level} is not the level at that bin ({db[i]:.1f})")


def fmctl_marker(S):
    """``fmctl marker FREQ`` at 70% across the band; its reply, parsed."""
    if 'marker_reply' not in S:
        view = S['view']
        freq = round(float(view._x[0] + 0.7 * (view._x[-1] - view._x[0])), 6)
        code, out, err = fmctl(f'marker {freq:.6f}')
        assert code == 0, f"fmctl marker {freq} failed: {out or err}"
        S['marker_freq'] = freq
        S['marker_reply'] = json.loads(out)['result']
    return S['marker_reply']


def check_fmctl_reply(S):
    need(S)
    view = S['view']
    assert view.level_unit == 'dBm', f"the level unit is {view.level_unit}, want dBm"
    reply = fmctl_marker(S)
    assert reply.get('unit') == 'dBm', f"fmctl marker replied unit {reply.get('unit')!r}, want dBm"
    x, db = view._x, view._db
    i = int(np.argmin(np.abs(x - S['marker_freq'])))
    want = float(db[i])
    assert isinstance(reply.get('level_db'), (int, float)) and abs(reply['level_db'] - want) <= 0.1, (
        f"fmctl marker {S['marker_freq']} replied {reply.get('level_db')} dBm, the bin at "
        f"{x[i]:.4f} MHz reads {want:.1f} dBm")


def check_fmctl_places(S):
    need(S)
    fmctl_marker(S)
    freq = S['marker_freq']
    got = marker_of(S['c'])
    assert got is not None and abs(got - freq) <= tolerance(S['view']), (
        f"fmctl marker {freq} left marker_mhz at {got}")


def spectrum_window(kind, label, folder, station):
    S = {'station': station}
    run(f'f: {label} opens', open_into, S, folder, f'spectra-{kind}')
    try:
        run(f'f: {label} is chosen in Recordings and drawn', choose, S, kind, True)
        run(f'f: {label}: marker_mhz is None before a marker is placed', check_no_marker, S)
        run(f"f: {label}: the right-click menu reads 'Marker to <f> MHz'", check_menu, S)
        run(f'f: {label}: choosing the menu places the marker (status.marker_mhz)',
            check_menu_places, S)
        run(f'f: {label}: a left click places the marker', check_click, S)
        run(f'f: {label}: dragging the marker moves it', check_drag, S)
        run(f'f: {label}: the readout gives the frequency and the dBm level at that bin',
            check_readout, S)
        run(f'f: {label}: fmctl marker FREQ replies the dBm level at that bin',
            check_fmctl_reply, S)
        run(f'f: {label}: fmctl marker FREQ places the marker (marker_mhz within a bin)',
            check_fmctl_places, S)
    finally:
        close_session(S)


def check_marker_key_receive(S):
    """Receive, nothing placed: status carries marker_mhz, None."""
    s = S['c'].ok('status')
    assert 'marker_mhz' in s, 'status has no marker_mhz in Receive'
    assert s['marker_mhz'] is None, f"marker_mhz is {s['marker_mhz']} in Receive, nothing placed"


def iq_menu(S):
    need(S)
    view = S['view']
    settle(view)
    at = point(view, frame_at(view, 0.3))
    click(view.plot.viewport(), at, QtCore.Qt.RightButton)
    text = view._tune_action.text()
    view._menu.close()
    assert text.startswith('Tuner to'), f"an IQ track's menu reads {text!r}, want 'Tuner to <f> MHz'"
    found = re.search(r'(-?\d+\.\d+) MHz', text)
    assert found, f"the menu {text!r} has no frequency"
    S['menu_mhz'] = float(found.group(1))


def iq_tuner(S):
    need(S)
    S['view']._tune_action.trigger()
    pump(0.2)
    got = S['c'].ok('status')['tuner_mhz']
    assert abs(got - S['menu_mhz']) <= 0.001 + EPS, (
        f"choosing 'Tuner to' on an IQ track put the tuner at {got}, want {S['menu_mhz']}")


def check_iq_marker(S):
    need(S)
    got = marker_of(S['c'])
    assert got is None, f"an IQ track's 'Tuner to' placed a marker at {got}: want None"


def iq_window(folder, station):
    S = {'station': station}
    run('f: the IQ recording opens', open_into, S, folder, 'spectra-iq')
    try:
        run('f: status.marker_mhz exists and is None in Receive', check_marker_key_receive, S)
        run('f: the IQ recording is chosen in Recordings', choose, S, 'dvr-iq', False)
        run("f: an IQ track's right-click menu still reads 'Tuner to <f> MHz'", iq_menu, S)
        run("f: choosing an IQ track's 'Tuner to' moves the tuner there", iq_tuner, S)
        run('f: an IQ track leaves marker_mhz None', check_iq_marker, S)
    finally:
        close_session(S)


# ============================================================ main

def summary(started):
    failed = [(name, msg) for name, msg in RESULTS if msg is not None]
    print()
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed in "
          f"{time.time() - started:.0f} s")
    for name, msg in failed:
        print(f"  FAIL  {name}: {msg}")
    return 0 if not failed else 1


def run_all():
    try:
        return main()
    finally:
        close_windows()


def main():
    started = time.time()
    run('e: an IQ file\'s tuner offset is max(0, R/2 x usable - min(150 kHz, R/8)) '
        'at 54.7 kS/s and 250 kS/s', check_margin)
    run('e: every radio\'s offset is unchanged at 1.2, 2 and 10 MS/s', check_unchanged)

    station = signals.write_station(os.path.join(FOLDER, 'station'), seconds=2.0)
    narrow = signals.ensure_dir(os.path.join(FOLDER, 'narrow'))
    write_blue_files(narrow)
    part1_narrow(narrow, station)

    spectra = signals.ensure_dir(os.path.join(FOLDER, 'spectra'))
    write_spectra(spectra)
    spectrum_window('fft', 'the .fft', spectra, station)
    spectrum_window('dvr-sweep', 'the sweep .sdvr', spectra, station)
    iq_window(spectra, station)
    return summary(started)


if __name__ == '__main__':
    sys.exit(run_all())
