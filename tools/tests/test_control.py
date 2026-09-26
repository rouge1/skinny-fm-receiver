"""The control socket (``control.py``) and ``fmctl`` - no radio.

A window plays the synthetic station from an IQ file, with its control
socket on a path of the test's own (``FMRX_CONTROL``). Each command goes
through the socket, as ``fmctl`` sends it, and is checked against the
widgets it works: status (RDS and all), tune (and a tune outside the band),
the Center, gain and AGC refused where the window refuses them, volume,
mute, the tabs, a screenshot, wait holding a second client's command
behind it, JSON commands, bad commands, a second window finding the socket
taken, and ``fmctl`` itself, as a separate process.

Part 2 is a simulated radio that can retune, its Radio box folded: a tune
outside the band around the Center moves the Center (the tuner's digits
span the whole radio there, so the band is the engine's), the range and
AGC reported as the window has them, and AGC switched with its row hidden
by the fold. Both went wrong off air on the BB60D before this part.

The measuring commands too: ``peaks`` (the peak finder alone on a trace
with known signals, then on the station with Peak hold), ``rate``,
``record`` and ``capture`` (the files, their sizes, the Record box's ticks
put back); in part 2, ``sweep`` over a span, and ``peaks`` finding the
simulated radio's tones in it.

Part 3 is ``--no-audio``: the window starts muted, says so, and unmutes
from ``mute off``; the flag's mute is never saved, so the next run starts
as the settings say.

Run:  python tools/tests/test_control.py     (about half a minute)
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import Qt  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-ctl-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')
os.environ['FMRX_CONTROL'] = os.path.join(FOLDER, 'control.sock')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import radios  # noqa: E402
from fm_receiver.control import ControlServer, find_peaks  # noqa: E402
import numpy as np  # noqa: E402
from tests import signals  # noqa: E402
from tests.test_sweep import slow_radio  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
FMCTL = os.path.join(os.path.dirname(HERE), 'fmctl')


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
        return reply['result']

    def refused(self, line, words):
        reply = self.ask(line)
        assert not reply['ok'], (line, reply)
        assert words in reply['error'], (line, reply['error'])
        return reply['error']


def fmctl(*argv, timeout=15.0):
    proc = subprocess.Popen([sys.executable, FMCTL, *argv], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    assert pump(timeout, lambda: proc.poll() is not None), 'fmctl hung'
    out, err = proc.communicate()
    return proc.returncode, out, err


class SimRadio(radios.Radio):
    """A radio of tones that can retune and whose clipping is counted, so
    the window offers AGC (as test_gui's)."""
    kind = 'hackrf'
    name = 'Simulated radio'
    receive_rates = (2e6,)
    sweep_rates = (10e6,)
    default_receive_rate = 2e6
    default_sweep_rate = 10e6
    settle_ms = 30.0
    clip_warn = True

    def open(self):
        self.block = slow_radio(2e6, [(95.1e6, 0.1), (101.7e6, 0.05)], latency=1000)
        self.block.center = 94.8e6

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.rate = self.rate

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center(hz)


def part2_retuning_radio():
    original = fmapp.make_radio
    fmapp.make_radio = lambda kind, *a, **k: SimRadio()
    args = fmapp.parse_args(['--radio', 'hackrf', '--mode', 'receive', '--freq', '95.1',
                             '--no-sound-card', '--no-save'])
    w = fmapp.MainWindow(args, {'recording_dir': FOLDER, 'folded': {'radio': True}})
    w.show()
    pump(0.2)
    w.start_initial()
    control = ControlServer(w)
    assert control.start()
    try:
        c = Client()
        e = w.engine
        assert e.running and w._mode == 'receive', w.status.text()
        s = c.ok('status')
        low, high = s['tuner_range_mhz']
        assert high - low < 2, f"the range is the band around the Center, not {low}-{high}"
        assert s['agc_available'] and not s['agc'], s
        # Far outside the band: the Center moves, as Center on tuner does.
        r = c.ok('tune 101.7')
        assert r['center_moved'] and abs(e.station_hz - 101.7e6) < 1, (r, e.station_hz)
        assert abs(e.lo_hz - (101.7e6 - w.radio.lo_offset_hz)) < 1, e.lo_hz
        assert abs(w.center_entry.value() - e.lo_hz) < 1
        r = c.ok('tune 95.1')
        assert r['center_moved'] and abs(e.station_hz - 95.1e6) < 1, r
        # Inside the band: the Center stays.
        lo = e.lo_hz
        assert not c.ok('tune 95.2')['center_moved'] and e.lo_hz == lo
        c.refused('tune 9000', 'outside')
        assert abs(e.station_hz - 95.2e6) < 1, 'a refused tune moved the station'
        # AGC with the gain row folded away, as the window has it.
        assert not w.agc_box.isVisible()
        assert c.ok('agc on')['agc'] and w.agc_box.isChecked() and c.ok('status')['agc']
        assert not c.ok('agc off')['agc'] and not w.agc_box.isChecked()
        assert c.ok('gain 30')['gain_percent'] == 30 and w.radio.gain_percent == 30
        # Sweep a span: the tab switches, its digits move, and peaks finds the
        # simulated radio's two tones in the held trace.
        r = c.ok('sweep 90 105')
        assert r == {'start_mhz': 90.0, 'stop_mhz': 105.0}, r
        assert w.tabs.currentIndex() == 0 and w._mode == 'sweep'
        c.ok('peakhold on')
        assert pump(10, lambda: len(c.ok('peaks 15')['peaks']) >= 2), c.ok('peaks 15')
        found = [p['freq_mhz'] for p in c.ok('peaks 15')['peaks']]
        for tone in (95.1, 101.7):
            assert any(abs(f - tone) < 0.1 for f in found), (tone, found)
        c.refused('sweep 105 90', 'above START')
        c.ok('peakhold off')
        print("retuning radio: Center moved by tune, AGC with the Radio box folded")
    finally:
        control.close()
        w.close()
        fmapp.make_radio = original


def part3_no_audio():
    path = os.environ['FMRX_CONFIG']
    station = os.path.join(FOLDER, 'synth.cfile')
    for saved_muted in (False, True):
        with open(path, 'w') as fh:
            json.dump({'muted': saved_muted}, fh)
        args = fmapp.parse_args(['--file', station, '--no-audio', '--no-sound-card'])
        w = fmapp.MainWindow(args, {'recording_dir': FOLDER, 'muted': saved_muted})
        w.show()
        pump(0.2)
        w.start_initial()
        control = ControlServer(w)
        assert control.start()
        try:
            c = Client()
            assert w.mute_btn.isChecked() and c.ok('status')['muted']
            assert w.engine.rx.muted, 'the flowgraph plays'
            assert w._flag_muted
            w.save_settings()                   # still muted by the flag: not saved
            assert json.load(open(path))['muted'] is saved_muted
            assert not c.ok('mute off')['muted']
            assert not w._flag_muted and not w.engine.rx.muted
            w.save_settings()                   # unmuted by hand: that is saved
            assert json.load(open(path))['muted'] is False
        finally:
            control.close()
            w.close()
    print("--no-audio: muted at start, not saved, unmuted by mute off")


def peak_finder():
    freqs = np.linspace(400e6, 450e6, 5001)                # 10 kHz bins
    db = np.full(len(freqs), -100.0)
    db[1000] = -60.0                                       # 410 MHz, one bin
    db[2500:2521] = -70.0                                  # 425.0-425.2 MHz, 210 kHz
    db[2510] = -65.0
    db[2523] = -75.0                                       # 3 bins on: the same signal
    db[4000] = -95.0                                       # 5 dB up: under the threshold
    floor, peaks = find_peaks(freqs, db, 10.0)
    assert floor == -100.0 and len(peaks) == 2, peaks
    assert peaks[0]['freq_mhz'] == 410.0 and peaks[0]['above_floor_db'] == 40.0
    assert peaks[1]['freq_mhz'] == 425.1 and peaks[1]['level_db'] == -65.0, peaks[1]
    assert abs(peaks[1]['width_khz'] - 240.0) < 0.1, peaks[1]
    assert find_peaks(freqs[:4], db[:4])[1] == []
    print("peak finder: ok")


def main():
    peak_finder()
    station = signals.write_station(os.path.join(FOLDER, 'synth'), seconds=12.0)
    args = fmapp.parse_args(['--file', station, '--no-sound-card', '--no-save'])
    w = fmapp.MainWindow(args, {'recording_dir': FOLDER})
    w.show()
    pump(0.2)
    w.start_initial()
    control = ControlServer(w)
    assert control.start(), 'no socket'
    mode = os.stat(control.path).st_mode & 0o777
    assert mode & 0o077 == 0, f"the socket is open to others: {oct(mode)}"
    try:
        c = Client()

        # Status: the file radio receiving, and RDS once decoded.
        s = c.ok('status')
        assert s['radio'] == 'file' and s['tab'] == 'receive' and s['running'], s
        assert abs(s['tuner_mhz'] - 98.7) < 1e-6, s
        assert pump(15, lambda: c.ok('status').get('rds', {}).get('station') == 'TEST FM'), \
            c.ok('status')
        s = c.ok('status')
        assert s['rds']['pi'] == '0x1234' and s['rds']['ps'].strip() == 'TEST FM', s['rds']
        assert s['signal']['snr_db'] is not None and s['signal']['snr_db'] > 15, s['signal']
        assert s['stereo']['enabled'], s['stereo']
        print(f"status: {s['radio_name']}, {s['tuner_mhz']} MHz, PS {s['rds']['ps']!r}, "
              f"PI {s['rds']['pi']}, SNR {s['signal']['snr_db']} dB")

        # Tune: the digits move, and the engine with them.
        r = c.ok('tune 98.8')
        assert abs(w.tuner.value() - 98.8e6) < 1 and abs(w.engine.station_hz - 98.8e6) < 1, r
        c.ok('tune 98.7')
        assert abs(w.engine.station_hz - 98.7e6) < 1
        # A recording's band is fixed: outside it the tune is refused, and
        # the tuner stays inside.
        c.refused('tune 105', 'outside')
        assert abs(w.tuner.value() - 98.7e6) < 1 and abs(w.engine.station_hz - 98.7e6) < 1, \
            'a refused tune moved the tuner'
        c.refused('tune abc', 'expected a number')
        c.refused('tune', 'usage')

        # A file has no gain, nor AGC: refused as the greyed controls are.
        c.refused('gain 40', 'greyed out')
        c.refused('agc on', "isn't offered")

        # Audio.
        assert c.ok('volume 30')['volume'] == 30 and round(w.volume_knob.value()) == 30
        assert c.ok('mute on')['muted'] and w.mute_btn.isChecked()
        assert not c.ok('mute off')['muted'] and not w.mute_btn.isChecked()
        c.refused('mute maybe', 'on or off')

        # The tabs: Sweep, then back to Receive, still on the station.
        c.ok('mode sweep')
        assert w.tabs.currentIndex() == 0
        c.refused('center 98.5', "Receive's")
        c.ok('mode receive')
        assert w.tabs.currentIndex() == 1 and w._mode == 'receive' and w.engine.running
        assert abs(w.engine.station_hz - 98.7e6) < 1, w.engine.station_hz
        c.refused('mode upside-down', 'one of')

        # A screenshot, as the user sees the window.
        shot = os.path.join(FOLDER, 'shot.png')
        c.ok(f'screenshot {shot}')
        assert os.path.getsize(shot) > 1000
        with open(shot, 'rb') as fh:
            assert fh.read(8) == b'\x89PNG\r\n\x1a\n'
        c.refused('screenshot shot.png', 'absolute')

        # wait holds the queue: a second client's command answers after it.
        other = Client()
        t0 = time.time()
        c.send('wait 0.6')
        other.send('status')
        assert other.reply()['ok'] and time.time() - t0 > 0.55, 'wait did not hold'
        assert c.reply()['result'] == {'waited_s': 0.6}
        c.refused('wait 1000', 'seconds')

        # Peaks: the station on the spectrum, with Peak hold and without.
        assert c.ok('peakhold on')['peak_hold'] and w.rf_view.peak_check.isChecked()
        c.ok('wait 1')
        p = c.ok('peaks')
        assert p['trace'] == 'peak hold' and p['unit'] == 'dBFS', p
        top = p['peaks'][0]
        assert abs(top['freq_mhz'] - 98.7) < 0.15 and top['above_floor_db'] > 20, p
        inside = c.ok('peaks 10 98.5 98.9')
        assert inside['span_mhz'][0] >= 98.5 and inside['peaks'], inside
        assert c.ok('peaks 200')['peaks'] == []             # nothing that loud
        c.refused('peaks 10 150 160', 'not on the spectrum')
        c.refused('peaks 10 98.5', 'usage')
        c.ok('peakhold clear')
        assert not c.ok('peakhold off')['peak_hold']
        assert c.ok('peaks')['trace'] == 'live'

        # Rate: a file has the one rate it was recorded at.
        assert c.ok('rate 2.5')['rate_msps'] == 2.5 and w.engine.running
        c.refused('rate 7', 'offers 2.5')

        # Record and capture: files named and sized, the ticks put back.
        before = {k: getattr(w, n).isChecked() for k, n in
                  (('audio', 'rec_audio'), ('iq-channel', 'rec_channel'), ('iq-band', 'rec_band'))}
        cap = c.ask('capture 1 iq-channel', timeout=15)
        assert cap['ok'], cap
        cap = cap['result']
        paths = [f['path'] for f in cap['files']]
        cfile = [f for f in cap['files'] if f['path'].endswith('.cfile')]
        assert len(cfile) == 1 and 'iq-channel' in cfile[0]['path'], paths
        assert 2e6 < cfile[0]['bytes'] < 8e6, cfile          # ~1 s at 500 kS/s, 8 bytes
        assert any(p.endswith('.sigmf-meta') for p in paths), paths
        assert cap['kind'] == 'iq-channel' and abs(cap['station_mhz'] - 98.7) < 1e-6, cap
        after = c.ok('record iq-band off')
        assert {k: after[k] for k in before} == {**before, 'iq-band': False}, (before, after)
        assert not w.rec_btn.isChecked()
        c.refused('capture 1000', 'seconds')
        c.refused('capture 1 wav', 'one of')
        c.ok('record audio off')
        c.ok('record iq-channel off')
        c.refused('record start', 'Tick something')
        c.ok('record audio on')
        assert c.ok('record start')['recording'] and w.rec_btn.isChecked()
        c.refused('record audio off', 'record stop')
        c.refused('capture 1', 'already recording')
        c.ok('wait 0.5')
        done = c.ok('record stop')
        assert not done['recording'] and done['files'], done
        assert done['files'][0]['path'].endswith('-audio.wav') and done['files'][0]['bytes'] > 44
        c.refused('record stop', 'not recording')

        # JSON commands, bad ones, unknown ones; help lists them all.
        r = c.ask(json.dumps({'cmd': 'volume', 'args': [45]}))
        assert r['ok'] and r['result']['volume'] == 45, r
        c.refused('{"cmd": ', 'bad JSON')
        c.refused('{"verb": "status"}', 'JSON commands')
        c.refused('fly 3', 'unknown command')
        assert set(c.ok('help')) >= {'status', 'tune', 'center', 'gain', 'agc', 'mode',
                                     'volume', 'mute', 'screenshot', 'wait'}
        assert 'status' in c.ask('status')     # every reply carries the status line

        # A second window finds the socket taken, and leaves it be.
        second = ControlServer(w)
        assert not second.start(), 'two windows on one socket'
        assert c.ok('status')['running']

        # fmctl itself, as a separate process: several commands, then errors.
        code, out, err = fmctl('tune 98.9; status')
        assert code == 0, (code, out, err)
        assert abs(w.engine.station_hz - 98.9e6) < 1
        assert '"tuner_mhz": 98.9' in out, out
        code, out, _ = fmctl('fly')
        assert code == 1 and 'unknown command' in out, out
        shot2 = os.path.join(FOLDER, 'shot2.png')
        code, out, err = fmctl('screenshot', shot2)
        assert code == 0 and os.path.exists(shot2), (code, out, err)
    finally:
        control.close()
        w.close()
    code, _, err = fmctl('status')
    assert code == 2 and 'no FM receiver window' in err, (code, err)
    part2_retuning_radio()
    part3_no_audio()
    print("control socket and fmctl: OK")
    return 0


if __name__ == '__main__':
    sys.exit(main())
