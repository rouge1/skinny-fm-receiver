"""The running window's control socket: another program - ``tools/fmctl``,
or Claude through it - works the window as a click would.

While the window is open it listens on a local socket
(``config.control_path``: a Unix socket in the session's runtime folder,
the user's only, never the network). A client sends one command a line and
gets one JSON line back::

    tune 99.1                     plain words, or
    {"cmd": "tune", "args": [99.1]}

    {"ok": true, "result": {...}, "status": "Receiving 99.100 MHz ..."}
    {"ok": false, "error": "...", "status": "..."}

Every command runs on the Qt main thread and **works the widgets a click
does** - the tuner's digits, the gain slider, the tabs - so the window moves
in front of the user, the settings are saved as usual, and the window and
the API never disagree. A command the window would refuse (gain with AGC
on, the Center while sweeping) is refused the same way, as an error. The
status line comes back with every reply, so a radio's complaint is seen.

Commands run one at a time, from all clients in turn: opening a radio pumps
Qt's events, and a second command must not run inside the first. ``wait``
holds the queue for its seconds without blocking the window.
"""

import json
import os
import shlex
import sys

from PyQt5 import Qt, QtCore, QtNetwork  # type: ignore

from .config import control_path

#: A command line longer than this is refused, and the client dropped.
MAX_LINE = 64 * 1024
#: The longest path a Unix socket takes: 104 bytes on macOS, 108 on Linux,
#: less the terminating nul.
MAX_PATH = 103
#: The longest ``wait``.
MAX_WAIT_S = 120.0


class CommandError(Exception):
    """A command the window would refuse: said as an error, not a crash."""


class Later:
    """A reply that comes after ``seconds``, from ``then()``."""

    def __init__(self, seconds, then):
        self.seconds = seconds
        self.then = then


def _plain(html_text):
    doc = Qt.QTextDocument()
    doc.setHtml(html_text)
    return doc.toPlainText().strip()


def _mhz(hz):
    return None if hz is None else round(hz / 1e6, 6)


def _number(word, what):
    try:
        return float(word)
    except (TypeError, ValueError):
        raise CommandError(f"{what}: expected a number, got {word!r}") from None


def _on_off(word, what):
    word = str(word).lower()
    if word in ('on', '1', 'true', 'yes'):
        return True
    if word in ('off', '0', 'false', 'no'):
        return False
    raise CommandError(f"{what}: expected on or off, got {word!r}")


def _args(args, low, high, usage):
    if not low <= len(args) <= high:
        raise CommandError(f"usage: {usage}")
    return args


# ============================================================== commands
# Each takes the window and the command's words, and returns what the reply
# carries as its result. HELP holds each one's usage and what it does.

HELP = {}
COMMANDS = {}


def command(name, usage, text):
    def register(fn):
        COMMANDS[name] = fn
        HELP[name] = (usage, text)
        return fn
    return register


@command('help', 'help', "These commands.")
def cmd_help(w, args):
    return {name: f"{usage} - {text}" for name, (usage, text) in HELP.items()}


@command('status', 'status', "What the window shows: radio, tab, frequencies, gain, "
         "audio, stereo, RDS, signal, clipping, recording.")
def cmd_status(w, args):
    e = w.engine
    radio = w.radio
    out = {
        'radio': radio.kind if radio is not None else None,
        'radio_name': radio.describe() if radio is not None else None,
        'running': bool(e.running),
        'tab': w._tab_mode(),
        'mode': w._mode,
        'tuner_mhz': _mhz(w.tuner.value()),
        'tuner_range_mhz': [_mhz(w.tuner.minimum()), _mhz(w.tuner.maximum())],
        'center_mhz': _mhz(e.lo_hz if w._mode == 'receive' else w.center_entry.value()),
        'station_mhz': _mhz(e.station_hz) if w._mode in ('receive', 'playback') else None,
        'rate_msps': round(e.rate / 1e6, 6) if e.rate else None,
        'gain_percent': w.gain_slider.value(),
        'agc': bool(w.agc_box.isVisible() and w.agc_box.isChecked()),
        'agc_available': bool(w.agc_box.isVisible() and w.agc_box.isEnabled()),
        'volume': round(w.volume_knob.value()),
        'muted': w.mute_btn.isChecked(),
        'channel_filter_khz': round(w.chan_entry.value() / 1e3, 3),
        'step_khz': w._step_hz() / 1e3,
        'clipped_percent': (round(100 * w._clip_smooth, 3)
                            if w._clip_smooth is not None else None),
        'recording': w.rec_btn.isChecked(),
        'recording_text': w.rec_label.text() or None,
    }
    rx = e.rx
    if rx is not None and w._mode in ('receive', 'playback'):
        snap = rx.rds.snapshot()
        snr = w._snr_db()
        out['signal'] = {
            'channel_dbfs': round(float(rx.channel_power_db()), 1),
            'snr_db': round(snr, 1) if snr is not None else None,
        }
        out['stereo'] = {'enabled': w.stereo_check.isChecked(),
                         'pilot_locked': bool(rx.pilot_locked())}
        good = (None if not snap['blocks_seen']
                else round(100 * (1 - (snap['block_error_rate'] or 0)), 1))
        out['rds'] = {
            'pi': snap['pi_hex'] or None,
            'callsign': snap['callsign_confirmed'] or snap['callsign'] or None,
            'ps': snap['ps'] or None,
            'station': snap['station_short'] or snap['station_name'] or None,
            'radiotext': snap['radiotext'] or None,
            'pty': snap['pty'] or None,
            'groups': snap['groups'],
            'blocks_good_percent': good,
        }
    return out


@command('tune', 'tune MHZ', "The tuner, as its digits do. Outside the band around "
         "the Center, the Center moves first, as Center on tuner does.")
def cmd_tune(w, args):
    mhz = _number(_args(args, 1, 1, 'tune MHZ')[0], 'tune')
    hz = mhz * 1e6
    moved = False
    radio = w.radio

    def inside():
        return w.tuner.minimum() <= hz <= w.tuner.maximum()

    # An IQ file's band is where it was recorded: its Center can't move.
    if (not inside() and w._tab_mode() == 'receive' and w._mode == 'receive'
            and radio is not None and radio.kind != 'file'
            and radio.freq_range_hz[0] <= hz <= radio.freq_range_hz[1]):
        w.center_entry.setValue(hz - radio.lo_offset_hz, emit=True)
        moved = True
    # Checked first: the digits would clamp it to the edge, and tune there.
    if not inside():
        raise CommandError(
            f"{mhz:g} MHz is outside the tuner's range, "
            f"{w.tuner.minimum() / 1e6:.3f}-{w.tuner.maximum() / 1e6:.3f} MHz")
    w.tuner.setValue(hz, emit=True)
    return {'tuner_mhz': _mhz(w.tuner.value()), 'center_moved': moved,
            'center_mhz': _mhz(w.engine.lo_hz) if w._mode == 'receive' else None}


@command('center', 'center MHZ', "The Radio box's Center (Receive only).")
def cmd_center(w, args):
    mhz = _number(_args(args, 1, 1, 'center MHZ')[0], 'center')
    if w._tab_mode() != 'receive' or w._mode != 'receive' or w.engine.rx is None:
        raise CommandError("the Center is Receive's: switch with 'mode receive' first")
    w.center_entry.setValue(mhz * 1e6, emit=True)
    return {'center_mhz': _mhz(w.engine.lo_hz), 'tuner_mhz': _mhz(w.tuner.value())}


@command('gain', 'gain PERCENT', "The RF gain slider, 0-100.")
def cmd_gain(w, args):
    value = _number(_args(args, 1, 1, 'gain PERCENT')[0], 'gain')
    slider = w.gain_slider
    if not slider.isEnabled():
        raise CommandError("the gain slider is greyed out: "
                           + ("AGC is on ('agc off' first)" if w._agc_on()
                              else "this radio has no gain"))
    slider.setValue(int(round(min(max(value, 0), 100))))
    return {'gain_percent': slider.value()}


@command('agc', 'agc on|off', "The AGC box beside the gain.")
def cmd_agc(w, args):
    on = _on_off(_args(args, 1, 1, 'agc on|off')[0], 'agc')
    box = w.agc_box
    if not (box.isVisible() and box.isEnabled()):
        raise CommandError("AGC isn't offered here"
                           + (f": {box.toolTip()}" if box.toolTip() else
                              " (not for this radio or this tab)"))
    box.setChecked(on)
    return {'agc': box.isChecked()}


@command('mode', 'mode sweep|receive|recordings', "The tabs.")
def cmd_mode(w, args):
    from .app import TAB_MODES
    mode = _args(args, 1, 1, 'mode sweep|receive|recordings')[0].lower()
    if mode not in TAB_MODES:
        raise CommandError(f"mode: one of {', '.join(TAB_MODES)}")
    w.tabs.setCurrentIndex(TAB_MODES.index(mode))
    return {'tab': w._tab_mode(), 'mode': w._mode}


@command('volume', 'volume PERCENT', "The Volume knob, 0-100.")
def cmd_volume(w, args):
    value = _number(_args(args, 1, 1, 'volume PERCENT')[0], 'volume')
    w.volume_knob.setValue(min(max(value, 0), 100))
    return {'volume': round(w.volume_knob.value())}


@command('mute', 'mute on|off', "The Mute button.")
def cmd_mute(w, args):
    w.mute_btn.setChecked(_on_off(_args(args, 1, 1, 'mute on|off')[0], 'mute'))
    return {'muted': w.mute_btn.isChecked()}


@command('screenshot', 'screenshot PATH.png', "The window, grabbed, as the user sees it.")
def cmd_screenshot(w, args):
    path = _args(args, 1, 1, 'screenshot PATH.png')[0]
    if not os.path.isabs(path):
        raise CommandError("screenshot: give an absolute path (fmctl does)")
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise CommandError(f"screenshot: no folder {folder}")
    if not w.grab().save(path):
        raise CommandError(f"screenshot: could not write {path}")
    return {'path': path}


@command('wait', 'wait SECONDS', "Reply after this long, the window running meanwhile "
         f"(at most {MAX_WAIT_S:.0f} s). Other commands wait too.")
def cmd_wait(w, args):
    seconds = _number(_args(args, 1, 1, 'wait SECONDS')[0], 'wait')
    if not 0 <= seconds <= MAX_WAIT_S:
        raise CommandError(f"wait: 0 to {MAX_WAIT_S:.0f} seconds")
    return Later(seconds, lambda: {'waited_s': seconds})


def parse(line):
    """(name, args) from a line: JSON ``{"cmd", "args"}`` or plain words."""
    line = line.strip()
    if line.startswith('{'):
        try:
            msg = json.loads(line)
        except ValueError as exc:
            raise CommandError(f"bad JSON: {exc}") from None
        if not isinstance(msg, dict) or not isinstance(msg.get('cmd'), str):
            raise CommandError('JSON commands are {"cmd": NAME, "args": [...]}')
        args = msg.get('args', [])
        if not isinstance(args, list):
            args = [args]
        return msg['cmd'].lower(), [str(a) for a in args]
    try:
        words = shlex.split(line)
    except ValueError as exc:
        raise CommandError(f"bad quoting: {exc}") from None
    if not words:
        raise CommandError("empty command")
    return words[0].lower(), words[1:]


# ================================================================ server

class ControlServer(QtCore.QObject):
    """The socket, its clients, and the one queue their commands run in."""

    def __init__(self, window, path=None):
        super().__init__(window)
        self.window = window
        self.path = path or control_path()
        self.server = None
        self._buffers = {}                      # socket -> bytes not yet a line
        self._queue = []                        # (socket, line)
        self._busy = False

    def start(self):
        """Listen; False, and said on stderr, if another window already is."""
        if len(os.fsencode(self.path)) > MAX_PATH:
            # Qt says only "Name error".
            print(f"FM receiver: no control socket: {self.path} is longer than a "
                  f"Unix socket's {MAX_PATH} bytes (FMRX_CONTROL names a shorter one)",
                  file=sys.stderr)
            return False
        probe = QtNetwork.QLocalSocket()
        probe.connectToServer(self.path)
        if probe.waitForConnected(200):
            probe.disconnectFromServer()
            print(f"FM receiver: another window has the control socket {self.path}; "
                  "this one has none", file=sys.stderr)
            return False
        # Left behind by a window that died: nobody answers on it.
        QtNetwork.QLocalServer.removeServer(self.path)
        server = QtNetwork.QLocalServer(self)
        server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
        if not server.listen(self.path):
            print(f"FM receiver: no control socket ({self.path}): "
                  f"{server.errorString()}", file=sys.stderr)
            return False
        server.newConnection.connect(self._accept)
        self.server = server
        return True

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None
        for sock in list(self._buffers):
            sock.abort()
        self._buffers.clear()
        self._queue.clear()

    def _accept(self):
        while self.server is not None and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b''
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._gone(s))

    def _gone(self, sock):
        self._buffers.pop(sock, None)
        self._queue = [(s, line) for s, line in self._queue if s is not sock]
        sock.deleteLater()

    def _read(self, sock):
        if sock not in self._buffers:
            return
        data = self._buffers[sock] + bytes(sock.readAll())
        *lines, rest = data.split(b'\n')
        if len(rest) > MAX_LINE:
            self._send(sock, {'ok': False, 'error': 'line too long'})
            sock.disconnectFromServer()
            return
        self._buffers[sock] = rest
        for line in lines:
            if line.strip():
                self._queue.append((sock, line.decode('utf-8', 'replace')))
        self._pump()

    def _pump(self):
        while self._queue and not self._busy:
            sock, line = self._queue.pop(0)
            self._busy = True
            reply = self._run(line)
            if isinstance(reply, Later):
                later = reply
                QtCore.QTimer.singleShot(int(later.seconds * 1000),
                                         lambda s=sock, l=later: self._finish(s, l))
                return
            self._busy = False
            self._send(sock, reply)

    def _finish(self, sock, later):
        try:
            reply = self._reply(later.then())
        except Exception as exc:
            reply = self._error(exc)
        self._busy = False
        self._send(sock, reply)
        self._pump()

    def _run(self, line):
        try:
            name, args = parse(line)
            fn = COMMANDS.get(name)
            if fn is None:
                raise CommandError(f"unknown command {name!r} ('help' lists them)")
            result = fn(self.window, args)
            return result if isinstance(result, Later) else self._reply(result)
        except Exception as exc:
            return self._error(exc)

    def _status(self):
        return _plain(self.window.status.text())

    def _reply(self, result):
        return {'ok': True, 'result': result, 'status': self._status()}

    def _error(self, exc):
        text = str(exc) if isinstance(exc, CommandError) else f"{type(exc).__name__}: {exc}"
        return {'ok': False, 'error': text, 'status': self._status()}

    def _send(self, sock, reply):
        if sock not in self._buffers or sock.state() != QtNetwork.QLocalSocket.ConnectedState:
            return
        sock.write((json.dumps(reply, default=str) + '\n').encode())
        sock.flush()
