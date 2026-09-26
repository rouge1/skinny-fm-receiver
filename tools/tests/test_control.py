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
from fm_receiver.control import ControlServer  # noqa: E402
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
                             '--no-audio', '--no-save'])
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
        print("retuning radio: Center moved by tune, AGC with the Radio box folded")
    finally:
        control.close()
        w.close()
        fmapp.make_radio = original


def main():
    station = signals.write_station(os.path.join(FOLDER, 'synth'), seconds=12.0)
    args = fmapp.parse_args(['--file', station, '--no-audio', '--no-save'])
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
    print("control socket and fmctl: OK")
    return 0


if __name__ == '__main__':
    sys.exit(main())
