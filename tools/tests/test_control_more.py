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
The ``measure`` command is phase m2 and is NOT tested here.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools \\
      /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
"""

import json
import os
import socket
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
    """Every float/int in a reply's result, however it is shaped."""
    out = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(result)
    return out


def ensure_receive(c, w):
    c.ok('mode receive')
    pump(0.5)
    assert w._tab_mode() == 'receive', w._tab_mode()
    if w.radio is None or not w.engine.running:
        w._use_radio('file')
        pump(1.0)
    assert w.engine.running, 'the radio is not running'


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
                c.ok('radio hackrf')
                pump(1.0)
                assert w.radio is not None and w.radio.kind == 'hackrf', \
                    getattr(w.radio, 'kind', None)
                s = c.ok('status')
                assert 'file' in s, sorted(s)
                assert s['file'] is None, f"no file loaded, expected None: {s['file']!r}"
            finally:
                fmapp.make_radio = original
                try:
                    c.ok('radio file')
                except Exception:
                    w._use_radio('file')
                pump(1.0)

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
            assert r is not None

        def t_filter_clamps_low():
            ensure_receive(c, w)
            low = w.chan_knob._min
            c.ok('filter 1')
            assert abs(w.chan_knob.value() - low) < 1, (w.chan_knob.value(), low)
            c.ok('filter 200')

        def t_filter_refused_outside_receive():
            ensure_receive(c, w)
            before = w.chan_knob.value()
            c.ok('mode recordings')
            try:
                c.refused('filter 200', 'receive')
                assert abs(w.chan_knob.value() - before) < 1, 'a refused filter moved the knob'
            finally:
                c.ok('mode receive')
                pump(0.5)

        def t_step_sets():
            ensure_receive(c, w)
            r = c.ok('step 50')
            assert abs(w._step_hz() - 50e3) < 1, w._step_hz()
            assert abs(c.ok('status')['step_khz'] - 50) < 1e-6
            assert any(abs(v - 50) < 1e-6 or abs(v - 50e3) < 1 for v in numbers_in(r)), r
            c.ok('step 100')

        def t_step_refused_unknown():
            ensure_receive(c, w)
            before = w._step_hz()
            err = c.refused('step 75')
            for v in fmapp.STEPS_KHZ:
                assert str(v) in err, f"error does not list {v}: {err}"
            assert abs(w._step_hz() - before) < 1, 'a refused step moved the knob'

        def t_radio_refused_unknown():
            ensure_receive(c, w)
            err = c.refused('radio fridge')
            for kind in fmapp.RADIO_ORDER:
                assert kind in err, f"error does not list {kind!r}: {err}"

        def t_radio_switches():
            original = fmapp.make_radio
            fmapp.make_radio = lambda kind, *a, **k: SimRadio() if kind == 'hackrf' else original(
                kind, *a, **k)
            try:
                ensure_receive(c, w)
                c.ok('radio hackrf')
                pump(1.0)
                assert w.radio is not None and w.radio.kind == 'hackrf'
                assert w.engine.running
                c.ok('radio file')
                pump(1.0)
                assert w.radio is not None and w.radio.kind == 'file', \
                    getattr(w.radio, 'kind', None)
                assert w.engine.running
            finally:
                fmapp.make_radio = original
                if w.radio is None or w.radio.kind != 'file':
                    w._use_radio('file')
                    pump(1.0)

        def t_stop_starts():
            ensure_receive(c, w)
            c.ok('stop')
            assert not w.engine.running, 'stop left it running'
            assert w.run_btn.text() == 'Start', w.run_btn.text()
            c.ok('start')
            assert pump(5, lambda: w.engine.running), 'start did not start'
            ensure_receive(c, w)

        def t_stop_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record iq-channel off')
            c.ok('record iq-band off')
            c.ok('record start')
            try:
                assert w.rec_btn.isChecked(), 'not recording'
                c.refused('stop', 'recording')
                assert w.engine.running, 'a refused stop stopped it'
            finally:
                c.ok('record stop')

        def t_start_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record start')
            try:
                c.refused('start', 'recording')
                assert w.engine.running
            finally:
                c.ok('record stop')

        def t_open_loads():
            ensure_receive(c, w)
            r = c.ok(f'open {second}')
            pump(1.0)
            assert os.path.abspath(w.cfg.get('iq_file', '')) == os.path.abspath(second), \
                w.cfg.get('iq_file')
            assert os.path.abspath(c.ok('status')['file']) == os.path.abspath(second), \
                c.ok('status')['file']
            assert w.engine.running
            assert r is not None
            c.ok(f'open {station}')
            pump(1.0)

        def t_open_refused_missing():
            ensure_receive(c, w)
            before = os.path.abspath(w.cfg.get('iq_file', ''))
            missing = os.path.join(FOLDER, 'no-such-file.cfile')
            err = c.refused(f'open {missing}')
            assert 'no-such-file' in err or 'missing' in err.lower() or 'not' in err.lower() \
                or 'could' in err.lower() or 'no ' in err.lower(), err
            assert os.path.abspath(w.cfg.get('iq_file', '')) == before, 'a refused open moved files'
            assert w.engine.running

        def t_open_refused_unreadable():
            ensure_receive(c, w)
            bad = os.path.join(FOLDER, 'unreadable.cfile')
            with open(bad, 'wb') as fh:
                fh.write(np.zeros(4096, np.complex64).tobytes())
            before = os.path.abspath(w.cfg.get('iq_file', ''))
            c.refused(f'open {bad}')
            assert os.path.abspath(w.cfg.get('iq_file', '')) == before
            assert w.engine.running

        def t_open_refused_while_recording():
            ensure_receive(c, w)
            c.ok('record audio on')
            c.ok('record start')
            try:
                before = os.path.abspath(w.cfg.get('iq_file', ''))
                c.refused(f'open {second}', 'recording')
                assert os.path.abspath(w.cfg.get('iq_file', '')) == before
            finally:
                c.ok('record stop')

        def t_marker_live():
            ensure_receive(c, w)
            assert pump(15, lambda: w.rf_view._x is not None and len(w.rf_view._x) > 8), \
                'no spectrum yet'
            mhz = w.engine.station_hz / 1e6
            r = c.ok(f'marker {mhz:.3f}')
            assert isinstance(r, dict), r
            freq_keys = [k for k in r if 'freq' in k.lower() or 'mhz' in k.lower()]
            assert freq_keys, f"no frequency in {r}"
            assert any(abs(float(r[k]) - mhz) < 0.2 for k in freq_keys), r
            level_keys = [k for k in r if 'level' in k.lower() or 'db' in k.lower()]
            assert level_keys, f"no level in {r}"
            assert all(np.isfinite(float(r[k])) for k in level_keys), r
            unit_keys = [k for k in r if 'unit' in k.lower()]
            assert unit_keys and any(r[k] == w.rf_view.level_unit for k in unit_keys), r

        def t_marker_peak_hold():
            ensure_receive(c, w)
            c.ok('peakhold on')
            try:
                c.ok('wait 1')
                mhz = w.engine.station_hz / 1e6
                r = c.ok(f'marker {mhz:.3f}')
                unit_keys = [k for k in r if 'unit' in k.lower()]
                assert unit_keys and any(r[k] == w.rf_view.level_unit for k in unit_keys), r
            finally:
                c.ok('peakhold off')

        def t_marker_refused_off_band():
            ensure_receive(c, w)
            assert pump(15, lambda: w.rf_view._x is not None and len(w.rf_view._x) > 8), \
                'no spectrum yet'
            err = c.refused('marker 500')
            assert any(word in err.lower() for word in ('off', 'band', 'shown', 'spectrum',
                                                       'view', 'range')), err

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
                assert r is not None
                cfg = json.load(open(os.environ['FMRX_CONFIG']))
                assert os.path.abspath(cfg.get('recording_dir', '')) == os.path.abspath(newdir), cfg
                c.ok('mode recordings')
                pump(0.5)
                w._refresh_library()
                names = [os.path.basename(p) for rec in w._recordings for p in rec.files()]
                assert marker_name in names, names
            finally:
                c.ok(f'recordings-folder {old}')
                c.ok('mode receive')
                pump(0.5)
            assert os.path.abspath(w._recording_dir()) == os.path.abspath(old)
            assert os.path.abspath(json.load(open(os.environ['FMRX_CONFIG'])).get(
                'recording_dir', '')) == os.path.abspath(old)

        def t_recordings_folder_refused():
            ensure_receive(c, w)
            before = w._recording_dir()
            nope = os.path.join(FOLDER, 'no-such-dir')
            err = c.refused(f'recordings-folder {nope}')
            assert any(word in err.lower() for word in ('director', 'folder', 'not', 'no ')), err
            assert w._recording_dir() == before, 'a refused folder moved the recordings'

        def t_delete_needs_confirm():
            c.ok('mode recordings')
            pump(0.3)
            w._refresh_library()
            n = len(c.ok('recordings')['recordings'])
            assert n > 0, 'need a recording to refuse to delete'
            err = c.refused('delete 0')
            assert 'confirm' in err.lower(), err
            assert len(c.ok('recordings')['recordings']) == n, 'a refused delete deleted'

        def t_delete_refused_external():
            extra = tempfile.mkdtemp(prefix='fmrx-sceptre-')
            rng = np.random.default_rng(11)
            iq = (rng.standard_normal(1 << 16) + 1j * rng.standard_normal(1 << 16)
                  ).astype(np.complex64) * np.float32(0.05)
            sdvr = os.path.join(extra, 'ext.sdvr')
            signals.write_sdvr(sdvr, iq, 2e6, 100e6)
            c.ok('mode recordings')
            c.ok(f'folders add {extra}')
            pump(0.5)
            w._refresh_library()
            try:
                listed = c.ok('recordings')['recordings']
                idx = next((r['index'] for r in listed
                            if 'sceptre' in r['kinds'].lower() or 'dvr' in r['kinds'].lower()),
                           None)
                assert idx is not None, f"no Sceptre recording listed: {[r['kinds'] for r in listed]}"
                err = c.refused(f'delete {idx} confirm')
                assert any(word in err.lower() for word in ('sceptre', 'left', 'extern',
                                                           'delet', 'not')), err
                assert os.path.exists(sdvr), 'a refused delete removed a Sceptre file'
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
                assert r is not None
                assert not os.path.exists(own), 'delete left the file'
                pump(0.3)
                w._refresh_library()
                assert all(own not in rec.files() for rec in w._recordings), 'still listed'
            finally:
                if os.path.exists(own):
                    os.remove(own)
                c.ok('mode receive')
                pump(0.5)

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
