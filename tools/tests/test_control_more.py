"""The measurement phase's fmctl additions (control.py) - no radio.

A window plays the synthetic station from an IQ file, with its control
socket on a path of the test's own (``FMRX_CONFIG``/``FMRX_CONTROL``).
Each new command of CONTRACTS-measure.md goes through the socket, as
``fmctl`` sends it, and is checked against the widgets it works:
``filter`` (the channel filter knob, clamped, Receive only), ``step``
(the Step knob, one of its values), ``radio`` (the radio chooser),
``start``/``stop`` (the Run button, refused while recording), ``open``
(load an IQ file, refused while recording and for missing/unreadable
files), ``marker`` (the level on the RF trace, refused off the shown
band), ``recordings-folder`` (the app's folder, via update_config),
``delete`` (the Nth recording with ``confirm``, the app's own only),
and the new ``status`` keys (``sweep``, ``file``, ``level_unit``).
``measure`` (phase m2) is checked last: refused with no capture, the card of
a capture, the seconds and ``card`` options, bad seconds, a missing file.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools \\
      /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
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
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import Qt  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-ctl-more-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')
os.environ['FMRX_CONTROL'] = os.path.join(FOLDER, 'control.sock')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import radios  # noqa: E402
from tests import signals  # noqa: E402
from tests.test_sweep import slow_radio  # noqa: E402
import numpy as np  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until else True


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
        assert reply['ok'], (line, reply)
        assert 'status' in reply, (line, reply)
        return reply['result']

    def refused(self, line, words=None):
        reply = self.ask(line)
        assert not reply['ok'], (line, reply)
        assert 'status' in reply, (line, reply)
        assert reply['error'], (line, reply)
        assert 'unknown command' not in reply['error'], (line, reply)
        if words is not None:
            assert words.lower() in reply['error'].lower(), (line, reply['error'])
        return reply['error']


FAILURES = []


def check(name, fn):
    try:
        fn()
    except Exception as exc:
        FAILURES.append(name)
        print(f"FAIL {name}: {exc}")
        traceback.print_exc()
    else:
        print(f"ok {name}")


class SimRadio(radios.Radio):
    """A radio that opens with no hardware, so `radio` can switch to it."""
    kind = 'hackrf'
    name = 'Simulated radio'
    receive_rates = (2e6,)
    sweep_rates = (10e6,)
    default_receive_rate = 2e6
    default_sweep_rate = 10e6
    settle_ms = 30.0
    clip_warn = True

    def open(self):
        self.block = slow_radio(2e6, [(95.1e6, 0.1)], latency=1000)
        self.block.center = 94.8e6

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.rate = self.rate

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center(hz)


def write_wav(path, seconds=1.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.stack([0.5 * np.sin(2 * np.pi * 1000.0 * t),
                     0.5 * np.sin(2 * np.pi * 2500.0 * t)], axis=1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return path


def numbers_in(result):
    """Every number in a reply's result, however it is shaped, including
    numeric strings such as a knob label like ``"200 kHz"``."""
    out = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, str):
            for m in re.findall(r'[-+]?\d[\d_]*(?:\.\d+)?(?:[eE][-+]?\d+)?', x):
                try:
                    out.append(float(m.replace('_', '')))
                except ValueError:
                    pass
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(result)
    return out


def strings_in(result):
    """Every string in a reply's result, however it is shaped."""
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(result)
    return out


FMCTL = os.path.join(os.path.dirname(HERE), 'fmctl')


def fmctl_in(cwd, *argv, timeout=15.0):
    """``fmctl`` as a separate process, run from ``cwd``: its own folder."""
    proc = subprocess.Popen([sys.executable, FMCTL, *argv], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert pump(timeout, lambda: proc.poll() is not None), 'fmctl hung'
    out, err = proc.communicate()
    return proc.returncode, out, err


def ensure_receive(c, w):
    c.ok('mode receive')
    assert pump(5, lambda: w._tab_mode() == 'receive'), w._tab_mode()
    if w.radio is None or not w.engine.running:
        w._use_radio('file')
    assert pump(10, lambda: w.radio is not None and w.engine.running), \
        'the radio is not running'
    assert w._tab_mode() == 'receive', w._tab_mode()


def switch_radio(c, w, kind):
    c.ok(f'radio {kind}')
    assert pump(10, lambda: w.radio is not None and w.radio.kind == kind
                and w.engine.running), (kind, getattr(w.radio, 'kind', None))


def nearest_trace_level(view, mhz, peak):
    """The trace's level at the bin nearest ``mhz`` (in the view's unit):
    the live bins, or the held peak's."""
    x = np.asarray(view._x, dtype=np.float64)
    db = np.asarray(view._peak if peak else view._db, dtype=np.float64)
    assert len(x) and len(db) == len(x), 'empty trace'
    return float(db[int(np.argmin(np.abs(x - mhz)))])


def parse_marker_reply(r, mhz):
    """(freqs, levels, units) in a marker reply, which the contract leaves
    open: the frequency in MHz or Hz, the level, and the unit if given."""
    if isinstance(r, dict):
        nums = numbers_in(r)
        freqs = [v for v in nums
                 if abs(v - mhz) < 0.1 or abs(v - mhz * 1e6) < 1e5]
        levels = []
        for k, v in r.items():
            kl = str(k).lower()
            if 'unit' in kl:
                continue
            if 'level' in kl or 'db' in kl:
                levels.extend(numbers_in(v))
        if not levels:
            levels = [v for v in nums
                      if not (abs(v - mhz) < 0.1 or abs(v - mhz * 1e6) < 1e5)]
        units = [v for k, v in r.items() if 'unit' in str(k).lower()]
        return freqs, levels, units
    return [], numbers_in(r), []


def check_marker_reply(r, mhz, peak, w, exp_before):
    exp_after = nearest_trace_level(w.rf_view, mhz, peak)
    freqs, levels, units = parse_marker_reply(r, mhz)
    if isinstance(r, dict):
        assert freqs, f"no frequency near {mhz:.3f} MHz (or Hz) in {r}"
    assert levels, f"no level in {r}"
    assert any(min(abs(v - exp_before), abs(v - exp_after)) <= 3.0 for v in levels), \
        f"level {levels} matches neither the trace then ({exp_before:.1f}) nor now ({exp_after:.1f})"
    for u in units:
        assert u == w.rf_view.level_unit, (units, w.rf_view.level_unit)


def main():
    station = signals.write_station(os.path.join(FOLDER, 'synth'), seconds=12.0)
    args = fmapp.parse_args(['--file', station, '--no-sound-card', '--no-save'])
    w = fmapp.MainWindow(args, {'recording_dir': FOLDER})
    w.show()
    pump(0.2)
    w.start_initial()
    from fm_receiver.control import ControlServer
    control = ControlServer(w)
    assert control.start(), 'no socket'
    second = signals.write_station(os.path.join(FOLDER, 'second'), seconds=6.0)
    try:
        c = Client()

        def t_status_keys():
            ensure_receive(c, w)
            s = c.ok('status')
            assert 'sweep' in s, f"status has no sweep: {sorted(s)}"
            assert 'file' in s, f"status has no file: {sorted(s)}"
            assert 'level_unit' in s, f"status has no level_unit: {sorted(s)}"
            sweep = s['sweep']
            assert abs(sweep['start_mhz'] - w.sweep_start.value() / 1e6) < 1e-6, sweep
            assert abs(sweep['stop_mhz'] - w.sweep_stop.value() / 1e6) < 1e-6, sweep
            assert s['file'] and os.path.basename(s['file']) in (
                os.path.basename(p) for p in (station, second, w.cfg.get('iq_file', ''))), s['file']
            assert os.path.abspath(s['file']) == os.path.abspath(w.cfg.get('iq_file', '')), \
                (s['file'], w.cfg.get('iq_file'))
            assert s['level_unit'] == w.rf_view.level_unit, (s['level_unit'], w.rf_view.level_unit)

        def t_status_file_none():
            original = fmapp.make_radio
            fmapp.make_radio = lambda kind, *a, **k: SimRadio() if kind == 'hackrf' else original(
                kind, *a, **k)
            try:
                ensure_receive(c, w)
                switch_radio(c, w, 'hackrf')
                s = c.ok('status')
                assert 'file' in s, sorted(s)
                known = {os.path.abspath(p) for p in
                         (station, second, w.cfg.get('iq_file', ''))}
                assert s['file'] is None or os.path.abspath(s['file']) in known, \
                    f"expected None or the opened path: {s['file']!r}"
            finally:
                fmapp.make_radio = original
                try:
                    switch_radio(c, w, 'file')
                except Exception:
                    w._use_radio('file')
                    assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_help_lists_new():
            h = c.ok('help')
            for name in ('filter', 'step', 'radio', 'start', 'stop', 'open',
                         'delete', 'recordings-folder', 'marker', 'status'):
                assert name in h, f"help lists no {name!r}: {sorted(h)}"

        def t_filter_sets():
            ensure_receive(c, w)
            r = c.ok('filter 200')
            assert abs(w.chan_knob.value() - 200e3) < 1, w.chan_knob.value()
            assert any(abs(v - 200) < 1e-6 or abs(v - 200e3) < 1 for v in numbers_in(r)), r

        def t_filter_clamps_high():
            ensure_receive(c, w)
            top = w.chan_knob._max
            r = c.ok(f'filter {(top / 1e3) + 500:g}')
            assert abs(w.chan_knob.value() - top) < 1, (w.chan_knob.value(), top)
            assert any(abs(v - top / 1e3) < 1e-6 or abs(v - top) < 1
                       for v in numbers_in(r)), r

        def t_filter_clamps_low():
            ensure_receive(c, w)
            low = w.chan_knob._min
            r = c.ok('filter 1')
            assert abs(w.chan_knob.value() - low) < 1, (w.chan_knob.value(), low)
            assert any(abs(v - low / 1e3) < 1e-6 or abs(v - low) < 1
                       for v in numbers_in(r)), r
            c.ok('filter 200')

        def t_filter_refused_outside_receive():
            ensure_receive(c, w)
            before = w.chan_knob.value()
            try:
                for mode in ('recordings', 'sweep'):
                    c.ok(f'mode {mode}')
                    assert pump(5, lambda: w._tab_mode() == mode), w._tab_mode()
                    c.refused('filter 200')
                    assert abs(w.chan_knob.value() - before) < 1, \
                        f"a refused filter moved the knob in {mode}"
            finally:
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_step_sets():
            ensure_receive(c, w)
            for v in fmapp.STEPS_KHZ:
                r = c.ok(f'step {v}')
                assert abs(w._step_hz() - float(v) * 1e3) < 1, (v, w._step_hz())
                assert abs(c.ok('status')['step_khz'] - float(v)) < 1e-6
                assert any(abs(x - float(v)) < 1e-6 or abs(x - float(v) * 1e3) < 1
                           for x in numbers_in(r)), (v, r)
            c.ok('step 100')

        def t_step_refused_unknown():
            ensure_receive(c, w)
            before = w._step_hz()
            err = c.refused('step 75')
            for v in fmapp.STEPS_KHZ:
                assert str(v) in err, f"error does not list {v}: {err}"
            assert abs(w._step_hz() - before) < 1, 'a refused step moved the knob'
            assert w.radio is not None and w.radio.kind == 'file' and w.engine.running

        def t_radio_refused_unknown():
            ensure_receive(c, w)
            err = c.refused('radio fridge')
            for kind in fmapp.RADIO_ORDER:
                assert kind in err, f"error does not list {kind!r}: {err}"
            assert w.radio is not None and w.radio.kind == 'file', \
                'a refused radio moved the chooser'
            assert w.engine.running, 'a refused radio stopped it'

        def t_radio_switches():
            original = fmapp.make_radio
            fmapp.make_radio = lambda kind, *a, **k: SimRadio() if kind == 'hackrf' else original(
                kind, *a, **k)
            try:
                ensure_receive(c, w)
                switch_radio(c, w, 'hackrf')
                switch_radio(c, w, 'file')
            finally:
                fmapp.make_radio = original
                if w.radio is None or w.radio.kind != 'file':
                    w._use_radio('file')
                    assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_stop_starts():
            ensure_receive(c, w)
            c.ok('stop')
            assert not w.engine.running, 'stop left it running'
            c.ok('start')
            assert pump(10, lambda: w.engine.running), 'start did not start'
            assert w._tab_mode() == 'receive', w._tab_mode()

        def t_stop_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record iq-channel off')
            c.ok('record iq-band off')
            c.ok('record start')
            try:
                assert w.rec_btn.isChecked(), 'not recording'
                c.refused('stop')
                assert w.rec_btn.isChecked(), 'a refused stop stopped the recording'
                assert w.engine.running, 'a refused stop stopped it'
            finally:
                c.ok('record stop')

        def t_start_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record start')
            try:
                assert w.rec_btn.isChecked(), 'not recording'
                c.refused('start')
                assert w.rec_btn.isChecked(), 'a refused start stopped the recording'
                assert w.engine.running
            finally:
                c.ok('record stop')

        def t_open_loads():
            ensure_receive(c, w)
            r = c.ok(f'open {second}')
            assert pump(10, lambda: os.path.abspath(w.cfg.get('iq_file', ''))
                        == os.path.abspath(second) and w.engine.running), \
                (w.cfg.get('iq_file'), w.engine.running)
            assert os.path.abspath(c.ok('status')['file']) == os.path.abspath(second), \
                c.ok('status')['file']
            assert os.path.basename(second) in strings_in(r), r
            c.ok(f'open {station}')
            assert pump(10, lambda: os.path.abspath(w.cfg.get('iq_file', ''))
                        == os.path.abspath(station) and w.engine.running), \
                (w.cfg.get('iq_file'), w.engine.running)

        def t_open_refused_missing():
            ensure_receive(c, w)
            before = os.path.abspath(w.cfg.get('iq_file', ''))
            missing = os.path.join(FOLDER, 'no-such-file.cfile')
            err = c.refused(f'open {missing}')
            assert 'no-such-file' in err or 'missing' in err.lower() \
                or 'not found' in err.lower() or 'no such' in err.lower() \
                or 'does not exist' in err.lower() or 'could not' in err.lower() \
                or 'unreadable' in err.lower(), err
            assert os.path.abspath(w.cfg.get('iq_file', '')) == before, \
                'a refused open moved files'
            assert w.engine.running, 'a refused open stopped it'

        def t_open_refused_unreadable():
            ensure_receive(c, w)
            bad = os.path.join(FOLDER, 'garbage.sdvr')
            with open(bad, 'wb') as fh:
                fh.write(b'not a sceptre dvr' * 256)
            dirc = os.path.join(FOLDER, 'adir.cfile')
            os.makedirs(dirc, exist_ok=True)
            before = os.path.abspath(w.cfg.get('iq_file', ''))
            c.refused(f'open {bad}')
            assert os.path.abspath(w.cfg.get('iq_file', '')) == before, \
                'a refused open moved files'
            assert w.engine.running, 'a refused open stopped it'
            c.refused(f'open {dirc}')
            assert os.path.abspath(w.cfg.get('iq_file', '')) == before, \
                'a refused open moved files'
            assert w.engine.running, 'a refused open stopped it'

        def t_open_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record start')
            try:
                assert w.rec_btn.isChecked(), 'not recording'
                before = os.path.abspath(w.cfg.get('iq_file', ''))
                c.refused(f'open {second}')
                assert os.path.abspath(w.cfg.get('iq_file', '')) == before, \
                    'a refused open moved files'
                assert w.rec_btn.isChecked(), 'a refused open stopped the recording'
            finally:
                c.ok('record stop')

        def t_marker_live():
            ensure_receive(c, w)
            if w.rf_view.peak_check.isChecked():
                c.ok('peakhold off')
            assert pump(15, lambda: w.rf_view._x is not None and len(w.rf_view._x) > 8), \
                'no spectrum yet'
            mhz = w.engine.station_hz / 1e6
            exp_before = nearest_trace_level(w.rf_view, mhz, peak=False)
            r = c.ok(f'marker {mhz:.3f}')
            check_marker_reply(r, mhz, False, w, exp_before)

        def t_marker_peak_hold():
            ensure_receive(c, w)
            c.ok('peakhold on')
            try:
                c.ok('wait 1')
                assert pump(10, lambda: w.rf_view._peak is not None
                            and w.rf_view._x is not None
                            and len(w.rf_view._peak) == len(w.rf_view._x)), 'no peak held'
                mhz = w.engine.station_hz / 1e6
                exp_before = nearest_trace_level(w.rf_view, mhz, peak=True)
                r = c.ok(f'marker {mhz:.3f}')
                check_marker_reply(r, mhz, True, w, exp_before)
            finally:
                c.ok('peakhold off')

        def t_marker_refused_off_band():
            ensure_receive(c, w)
            assert pump(15, lambda: w.rf_view._x is not None and len(w.rf_view._x) > 8), \
                'no spectrum yet'
            err = c.refused('marker 500')
            assert '500' in err or 'spectrum' in err.lower() or 'shown' in err.lower() \
                or 'outside' in err.lower() or 'off the' in err.lower(), err
            assert w.engine.running, 'a refused marker stopped it'

        def t_recordings_folder_moves():
            ensure_receive(c, w)
            newdir = tempfile.mkdtemp(prefix='fmrx-newrec-')
            marker_name = 'fm-99.10MHz-20260103-120000-audio.wav'
            write_wav(os.path.join(newdir, marker_name))
            old = w._recording_dir()
            try:
                r = c.ok(f'recordings-folder {newdir}')
                assert os.path.abspath(w._recording_dir()) == os.path.abspath(newdir), \
                    w._recording_dir()
                assert (newdir in strings_in(r)
                        or os.path.basename(newdir) in strings_in(r)), r
                cfg = json.load(open(os.environ['FMRX_CONFIG']))
                assert os.path.abspath(cfg.get('recording_dir', '')) == os.path.abspath(newdir), cfg
                c.ok('mode recordings')
                assert pump(10, lambda: any(
                    marker_name in [os.path.basename(p) for p in rec.files()]
                    for rec in w._recordings)), 'the list was not re-read'
            finally:
                c.ok(f'recordings-folder {old}')
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'
            assert os.path.abspath(w._recording_dir()) == os.path.abspath(old)
            assert os.path.abspath(json.load(open(os.environ['FMRX_CONFIG'])).get(
                'recording_dir', '')) == os.path.abspath(old)

        def t_recordings_folder_refused():
            ensure_receive(c, w)
            before = w._recording_dir()
            nope = os.path.join(FOLDER, 'no-such-dir')
            err = c.refused(f'recordings-folder {nope}')
            assert 'no-such-dir' in err or 'not a directory' in err.lower() \
                or 'no such' in err.lower() or 'not exist' in err.lower() \
                or 'not a folder' in err.lower() or 'no folder' in err.lower(), err
            assert w._recording_dir() == before, 'a refused folder moved the recordings'
            afile = os.path.join(FOLDER, 'afile.txt')
            with open(afile, 'w') as fh:
                fh.write('not a directory')
            err = c.refused(f'recordings-folder {afile}')
            assert 'directory' in err.lower() or 'folder' in err.lower(), err
            assert w._recording_dir() == before, 'a refused folder moved the recordings'
            assert w.engine.running, 'a refused folder stopped it'

        def t_delete_needs_confirm():
            c.ok('mode recordings')
            pump(0.3)
            w._refresh_library()
            n = len(c.ok('recordings')['recordings'])
            assert n > 0, 'need a recording to refuse to delete'
            err = c.refused('delete 0')
            assert 'confirm' in err.lower(), err
            assert len(c.ok('recordings')['recordings']) == n, 'a refused delete deleted'
            assert len(w._recordings) == n, 'a refused delete deleted'

        def t_delete_refused_external():
            extra = tempfile.mkdtemp(prefix='fmrx-sceptre-')
            rng = np.random.default_rng(11)
            iq = (rng.standard_normal(1 << 16) + 1j * rng.standard_normal(1 << 16)
                  ).astype(np.complex64) * np.float32(0.05)
            sdvr = os.path.join(extra, 'ext.sdvr')
            signals.write_sdvr(sdvr, iq, 2e6, 100e6)
            cdif = os.path.join(extra, 'ext.cdif')
            signals.write_blue(cdif, iq[:4096], 7e6, fmt='CF',
                               keywords={'RF_FREQ': 99.3e6})
            stray = os.path.join(extra, 'stray.wav')
            write_wav(stray)
            stray_bytes = open(stray, 'rb').read()
            c.ok('mode recordings')
            c.ok(f'folders add {extra}')
            assert pump(10, lambda: any(sdvr in rec.files() for rec in w._recordings)), \
                'the sdvr is not listed'
            assert pump(10, lambda: any(cdif in rec.files() for rec in w._recordings)), \
                'the cdif is not listed'
            try:
                assert not any(stray in rec.files() for rec in w._recordings), \
                    'a wav in an added folder is listed as a recording'
                n = len(c.ok('recordings')['recordings'])
                for path in (sdvr, cdif):
                    idx = next(i for i, rec in enumerate(w._recordings)
                               if path in rec.files())
                    err = c.refused(f'delete {idx} confirm')
                    assert 'sceptre' in err.lower(), err
                    assert os.path.exists(path), f"a refused delete removed {path}"
                assert len(c.ok('recordings')['recordings']) == n, \
                    'a refused delete deleted'
                assert open(stray, 'rb').read() == stray_bytes, 'the added folder was written'
                c.refused('delete 999 confirm')
                assert open(stray, 'rb').read() == stray_bytes, 'the added folder was written'
            finally:
                c.ok(f'folders remove {extra}')

        def t_delete_removes_own():
            own = os.path.join(w._recording_dir(), 'fm-98.70MHz-20260102-120000-audio.wav')
            write_wav(own)
            c.ok('mode recordings')
            pump(0.3)
            w._refresh_library()
            try:
                match = next((i for i, rec in enumerate(w._recordings) if own in rec.files()),
                             None)
                assert match is not None, 'the own recording is not listed'
                r = c.ok(f'delete {match} confirm')
                blob = ' '.join(strings_in(r))
                if isinstance(r, dict):
                    assert 'fm-98.70MHz-20260102-120000' in blob \
                        or any('delet' in str(k).lower() or 'remov' in str(k).lower()
                               for k in r), r
                else:
                    assert 'fm-98.70MHz-20260102-120000' in blob, r
                assert not os.path.exists(own), 'delete left the file'
                assert pump(10, lambda: all(own not in rec.files()
                                           for rec in w._recordings)), 'still listed'
            finally:
                if os.path.exists(own):
                    os.remove(own)
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_fmctl_open_relative():
            ensure_receive(c, w)
            rel = os.path.join(FOLDER, 'rel')
            os.makedirs(rel, exist_ok=True)
            relfile = signals.write_station(os.path.join(rel, 'rel-station'), seconds=6.0)
            # fmctl runs in FOLDER, the window has its own folder: the path is
            # made absolute by fmctl, so the window opens what fmctl was given.
            code, out, err = fmctl_in(FOLDER, 'open', 'rel/rel-station.cfile')
            assert code == 0, (code, out, err)
            assert pump(10, lambda: os.path.abspath(w.cfg.get('iq_file', ''))
                        == os.path.abspath(relfile) and w.engine.running), w.cfg.get('iq_file')
            assert os.path.abspath(json.loads(out)['result']['file']) == os.path.abspath(relfile), out
            assert os.path.abspath(c.ok('status')['file']) == os.path.abspath(relfile)
            c.ok(f'open {station}')
            assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_fmctl_open_spaced():
            ensure_receive(c, w)
            spaced = os.path.join(FOLDER, 'a b')
            os.makedirs(spaced, exist_ok=True)
            base = signals.write_station(os.path.join(spaced, 'my station'), seconds=6.0)
            # One argument with a space (as a shell passes a quoted path), and
            # the script form, where the path is quoted inside the command line.
            for argv in (('open', 'a b/my station.cfile'), ('open "a b/my station.cfile"',)):
                code, out, err = fmctl_in(FOLDER, *argv)
                assert code == 0, (argv, code, out, err)
                assert pump(10, lambda: os.path.abspath(w.cfg.get('iq_file', ''))
                            == os.path.abspath(base) and w.engine.running), (argv, w.cfg.get('iq_file'))
                assert os.path.abspath(json.loads(out)['result']['file']) == os.path.abspath(base), \
                    (argv, out)
            c.ok(f'open {station}')
            assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_fmctl_folders_relative():
            extra = os.path.join(FOLDER, 'extra-rel')
            os.makedirs(extra, exist_ok=True)
            code, out, err = fmctl_in(FOLDER, 'folders', 'add', 'extra-rel')
            assert code == 0, (code, out, err)
            try:
                assert os.path.abspath(extra) in json.loads(out)['result']['folders'], out
            finally:
                code, out, err = fmctl_in(FOLDER, 'folders', 'remove', 'extra-rel')
                assert code == 0 and os.path.abspath(extra) not in \
                    json.loads(out)['result']['folders'], (code, out, err)

        def t_fmctl_recordings_folder_relative():
            old = w._recording_dir()
            newdir = os.path.join(FOLDER, 'rec-rel')
            os.makedirs(newdir, exist_ok=True)
            try:
                code, out, err = fmctl_in(FOLDER, 'recordings-folder', 'rec-rel')
                assert code == 0, (code, out, err)
                assert os.path.abspath(w._recording_dir()) == os.path.abspath(newdir), \
                    w._recording_dir()
            finally:
                c.ok(f'recordings-folder {old}')
            assert os.path.abspath(w._recording_dir()) == os.path.abspath(old)

        def t_delete_returns_removed():
            own = os.path.join(w._recording_dir(), 'fm-97.30MHz-20260104-120000-audio.wav')
            write_wav(own)
            c.ok('mode recordings')
            pump(0.3)
            w._refresh_library()
            try:
                rec = next((r for r in w._recordings if own in r.files()), None)
                assert rec is not None, 'the own recording is not listed'
                gone = w._delete_recordings([rec])
                assert gone == [rec.key], gone
                assert not os.path.exists(own), 'the delete left the file'
                assert w._delete_recordings([]) == [], 'nothing to delete reported something'
            finally:
                if os.path.exists(own):
                    os.remove(own)
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_delete_returns_nothing_for_sceptre():
            extra = tempfile.mkdtemp(prefix='fmrx-sceptre-ret-')
            rng = np.random.default_rng(12)
            iq = (rng.standard_normal(1 << 16) + 1j * rng.standard_normal(1 << 16)
                  ).astype(np.complex64) * np.float32(0.05)
            sdvr = os.path.join(extra, 'ret.sdvr')
            signals.write_sdvr(sdvr, iq, 2e6, 100e6)
            c.ok('mode recordings')
            c.ok(f'folders add {extra}')
            try:
                assert pump(10, lambda: any(sdvr in rec.files() for rec in w._recordings)), \
                    'the sdvr is not listed'
                rec = next(r for r in w._recordings if sdvr in r.files())
                assert w._delete_recordings([rec]) == [], 'a Sceptre file was reported deleted'
                assert os.path.exists(sdvr), 'a Sceptre file was deleted'
            finally:
                c.ok(f'folders remove {extra}')
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def count_opens(w):
            """Count the radio's opens: every ``engine.use_radio`` the window makes."""
            opened = []
            real = w.engine.use_radio

            def counted(radio):
                opened.append(radio.kind)
                return real(radio)
            w.engine.use_radio = counted
            return opened, lambda: w.engine.__dict__.pop('use_radio', None)

        def t_radio_reopens_on_mode():
            original = fmapp.make_radio
            fmapp.make_radio = lambda kind, *a, **k: SimRadio() if kind == 'hackrf' else original(
                kind, *a, **k)
            opened, stop = count_opens(w)
            try:
                ensure_receive(c, w)
                switch_radio(c, w, 'hackrf')
                # Leaving Recordings opens the radio again; Sweep and Receive
                # share the open radio, so moving between them opens nothing.
                for mode, tab, opens in (('recordings', 'recordings', 0), ('receive', 'receive', 1),
                                         ('recordings', 'recordings', 0), ('sweep', 'sweep', 1),
                                         ('receive', 'receive', 0)):
                    before = len(opened)
                    r = c.ok(f'mode {mode}')
                    assert r['tab'] == tab, (mode, r)
                    if mode == 'recordings':
                        assert pump(5, lambda: w.radio is None), 'the radio did not close'
                    else:
                        assert pump(10, lambda: w.radio is not None and w.engine.running), \
                            (mode, 'the radio did not open again')
                    assert len(opened) == before + opens, (mode, before, opened)
                    assert all(kind == 'hackrf' for kind in opened), opened
            finally:
                stop()
                fmapp.make_radio = original
                if w.radio is None or w.radio.kind != 'file':
                    w._use_radio('file')
                    assert pump(10, lambda: w.engine.running), 'radio did not come back'

        def t_file_sweep_from_recordings_opens_once():
            ensure_receive(c, w)
            opened, stop = count_opens(w)
            try:
                c.ok('mode recordings')
                assert pump(5, lambda: w.radio is None), 'the radio did not close'
                before = len(opened)
                r = c.ok('mode sweep')
                assert r['tab'] == 'sweep', r                  # the tab asked for shows
                assert pump(10, lambda: w.engine.running and w.radio is not None), \
                    'the file did not open'
                assert len(opened) == before + 1, ('file opened twice', before, opened)
            finally:
                stop()
                c.ok('mode receive')
                assert pump(10, lambda: w.engine.running), 'radio did not come back'

        measured = {}
        measure_classes = ('noise', 'carrier', 'ook', 'ask', 'fsk', 'fm', 'am', 'unknown')

        def t_measure_refused_without_capture():
            ensure_receive(c, w)
            assert not getattr(w, '_last_capture', None), 'a capture was made before this check'
            err = c.refused('measure', 'capture')
            assert 'no capture' in err.lower(), err

        def t_measure_reads_capture():
            ensure_receive(c, w)
            cap = c.ask('capture 2 iq-band', timeout=30)
            assert cap['ok'], cap
            cfile = next(f['path'] for f in cap['result']['files']
                         if f['path'].endswith('.cfile'))
            assert os.path.isfile(cfile), cfile
            measured['cfile'] = cfile
            r = c.ask('measure', timeout=90)
            assert r['ok'], r
            card = r['result']
            assert card['schema'] == 1 and card['class'] in measure_classes, card
            assert 'card_file' not in card, card
            assert 'source' in card and 'path' in card['source'], card

        def t_measure_seconds_span():
            r = c.ask('measure 1', timeout=90)
            assert r['ok'], r
            src = r['result']['source']
            assert abs(src['t1_s'] - 1.0) < 0.05 and src['t0_s'] == 0.0, src
            assert r['result']['schema'] == 1 and r['result']['class'] in measure_classes, r

        def t_measure_card_writes_file():
            r = c.ask('measure card', timeout=90)
            assert r['ok'], r
            cfile = measured['cfile']
            card_p = os.path.splitext(cfile)[0] + '.card.json'
            assert r['result']['card_file'] == card_p, r['result']
            assert os.path.isfile(card_p), card_p
            with open(card_p) as fh:
                written = json.load(fh)
            reply_card = {k: v for k, v in r['result'].items() if k != 'card_file'}
            assert written == reply_card, 'the card file is not the reply'

        def t_measure_refused_bad_seconds():
            for bad in ('0', '-2', 'x', 'nan', 'inf'):
                err = c.refused(f'measure {bad}', 'SECONDS')
                assert 'SECONDS' in err, err
            c.refused('measure 2 3', 'usage')

        def t_measure_refused_missing_file():
            os.remove(measured['cfile'])
            err = c.refused('measure', 'missing')
            assert os.path.basename(measured['cfile']) in err, err
            assert not os.path.exists(measured['cfile'])

        checks = [
            ('status gains sweep/file/level_unit', t_status_keys),
            ('status file None without a file', t_status_file_none),
            ('help lists the new commands', t_help_lists_new),
            ('filter sets the channel filter', t_filter_sets),
            ('filter clamps above its range', t_filter_clamps_high),
            ('filter clamps below its range', t_filter_clamps_low),
            ('filter refused outside Receive', t_filter_refused_outside_receive),
            ('step sets the Step knob', t_step_sets),
            ('step refused for a value it does not have', t_step_refused_unknown),
            ('radio refused for an unknown kind', t_radio_refused_unknown),
            ('radio switches the chooser', t_radio_switches),
            ('stop then start work the Run button', t_stop_starts),
            ('stop refused while recording', t_stop_refused_while_recording),
            ('start refused while recording', t_start_refused_while_recording),
            ('open loads the file', t_open_loads),
            ('open refused for a missing file', t_open_refused_missing),
            ('open refused for an unreadable file', t_open_refused_unreadable),
            ('open refused while recording', t_open_refused_while_recording),
            ('marker reads the live trace', t_marker_live),
            ('marker reads the peak hold trace', t_marker_peak_hold),
            ('marker refused off the shown band', t_marker_refused_off_band),
            ('recordings-folder moves the folder', t_recordings_folder_moves),
            ('recordings-folder refused for no directory', t_recordings_folder_refused),
            ('delete refused without confirm', t_delete_needs_confirm),
            ('delete refused for a Sceptre file', t_delete_refused_external),
            ('delete removes the app’s own recording', t_delete_removes_own),
            ('fmctl open of a relative path from another folder', t_fmctl_open_relative),
            ('fmctl open of a path with a space from another folder', t_fmctl_open_spaced),
            ('fmctl folders add/remove of a relative path', t_fmctl_folders_relative),
            ('fmctl recordings-folder of a relative path', t_fmctl_recordings_folder_relative),
            ('_delete_recordings returns what it removed', t_delete_returns_removed),
            ('_delete_recordings returns nothing for a Sceptre file',
             t_delete_returns_nothing_for_sceptre),
            ('the radio opens again on each switch back', t_radio_reopens_on_mode),
            ('a file sweep from Recordings opens the file once',
             t_file_sweep_from_recordings_opens_once),
            ('measure refused with no capture yet', t_measure_refused_without_capture),
            ('measure gives the card of the last capture', t_measure_reads_capture),
            ('measure SECONDS measures that span', t_measure_seconds_span),
            ('measure card writes the card beside the .cfile', t_measure_card_writes_file),
            ('measure refused for bad SECONDS', t_measure_refused_bad_seconds),
            ('measure refused for a missing capture file', t_measure_refused_missing_file),
        ]
        for name, fn in checks:
            check(name, fn)
            pump(0.2)
    finally:
        try:
            if w.rec_btn.isChecked():
                w.rec_btn.setChecked(False)
        except Exception:
            pass
        control.close()
        w.close()
    if FAILURES:
        print(f"{len(FAILURES)} checks failed: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
