"""rtl_433 on the radio's samples: the ISM-band sensors around - weather
stations, tyre pressure, doorbells, remotes - decoded by rtl_433
(https://github.com/merbanan/rtl_433), for whichever radio is open.

::

    IQ (rate R, LO) ─┬─ shift the frequency to 0 Hz, low-pass, decimate
                     │    to about the width ─ pipe ─ rtl_433 -r cf32:- -F json
                     ├─ RF spectrum tap
                     └─ clip probe

**The window keeps the radio; rtl_433 only reads samples.** It runs as a
separate program reading complex floats from its stdin (``-r cf32:-``), so
the BB60D, HackRF, RTL-SDR and an IQ recording all work the same, the
spectrum and waterfall stay up, and nothing has to hand the device over.
Its JSON lines come back on its stdout, one per decoded message.

**The frequency sits clear of the LO.** A HackRF's DC spike in the middle of
what rtl_433 sees would read as a carrier: the LO is put ``width/2 +
min_offset_hz`` below the frequency (:func:`plan`), at the radio's lowest
receive rate that holds that, so the spike is outside the band passed on.

**The noise is lifted to an RTL-SDR's level.** rtl_433 takes its samples
at an 8-bit RTL-SDR's scale: a burst at 0.003 of full scale, 40 dB over the
noise, was not decoded at all (rtl_433 23.11, 2026-09-24), where the same
burst at 0.3 was. The BB60D's noise sits about 60 dB under an RTL-SDR's, so
:class:`pipe_sink` raises the samples until the noise is ``NOISE_LEVEL``
(a slow gain, from the quieter blocks of the last second or two), limits
each sample to full scale, and never turns anything down.

**The pipe never holds up the radio.** A write to rtl_433 that would block
is left pending; while one is pending, whole blocks of samples are dropped
(and counted) rather than part of one, so rtl_433's samples stay aligned.
"""

import collections
import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np  # type: ignore
from gnuradio import blocks, filter, gr  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore

from .dsp import clip_probe, spectrum_frames_per_s, spectrum_tap

PROGRAM = 'rtl_433'
#: (Hz, what is there) - the bands rtl_433's sensors use.
PRESETS = ((315e6, "315 MHz - Americas: remotes, tyre pressure"),
           (433.92e6, "433.92 MHz - weather stations, remotes"),
           (868.3e6, "868.3 MHz - Europe: sensors, smart meters"),
           (915e6, "915 MHz - Americas: sensors, smart meters"))
#: The bandwidth passed to rtl_433. 250 kHz is its own default, what its
#: decoders are tuned on; 1 MHz holds the wider FSK sensors of 868/915 MHz.
WIDTHS = (250e3, 1e6)
DEFAULT_HZ = 433.92e6
DEFAULT_WIDTH = 250e3
#: Fields rtl_433 adds to every message (``-M level``, ``-M protocol``; the
#: time is the wall clock's, ``-M time:iso``: from a pipe it would be the
#: samples' count), shown in their own columns rather than among the readings.
META_KEYS = ('time', 'model', 'id', 'channel', 'protocol', 'mod', 'freq', 'freq1',
             'freq2', 'rssi', 'snr', 'noise', 'mic')
#: The noise's RMS, of full scale, that the samples are raised to: an
#: RTL-SDR's noise is a few steps of its 8 bits.
NOISE_LEVEL = 0.03
#: The most they are raised, 100 dB; the noise is judged from this many
#: blocks (about a second or two), at this percentile, so bursts don't count.
MAX_GAIN = 1e5
NOISE_BLOCKS = 64
NOISE_PERCENTILE = 20
#: Messages kept for the window between its looks: rtl_433 can repeat one
#: burst a dozen times, and a busy band is a few a second.
QUEUE = 2000


def find_program():
    """rtl_433's path, or None."""
    return shutil.which(PROGRAM)


def install_hint():
    return ("brew install rtl_433" if sys.platform == 'darwin'
            else "sudo apt install rtl-433")


_versions = {}


def program_version(path):
    """'23.11', or '' if it would not say."""
    if path not in _versions:
        try:
            out = subprocess.run([path, '-V'], capture_output=True, text=True,
                                 timeout=5)
            words = (out.stdout + out.stderr).split()
            _versions[path] = words[words.index('version') + 1] if 'version' in words else ''
        except Exception:
            _versions[path] = ''
    return _versions[path]


def plan(radio, freq_hz, width_hz):
    """(radio rate, LO, offset of the frequency from the LO). The
    frequency is ``width/2 + min_offset_hz`` above the LO, at the lowest
    receive rate whose usable band holds the width there; failing that the
    widest, with the offset as large as fits. An IQ recording's LO is where
    it was made. Raises ValueError if the frequency is outside what the
    radio can reach."""
    low, high = radio.freq_range_hz
    rates = sorted(radio.usable_receive_rates()) or [radio.default_receive_rate]
    if radio.kind == 'file':
        rate, lo = rates[-1], radio.center_hz
        offset = freq_hz - lo
        if abs(offset) > rate * radio.usable_fraction(rate) / 2:
            raise ValueError(f"{freq_hz / 1e6:.3f} MHz is not in this recording "
                             f"({(lo - rate / 2) / 1e6:.3f} to {(lo + rate / 2) / 1e6:.3f} MHz)")
    else:
        if not low <= freq_hz <= high:
            raise ValueError(f"{freq_hz / 1e6:.3f} MHz is outside the {radio.name}'s "
                             f"range ({low / 1e6:g} to {high / 1e6:g} MHz)")
        want = width_hz / 2 + radio.min_offset_hz
        for rate in rates:
            if want + width_hz / 2 <= rate * radio.usable_fraction(rate) / 2:
                offset = want
                break
        else:
            rate = rates[-1]
            offset = max(0.0, min(want, rate * radio.usable_fraction(rate) / 2 - width_hz / 2))
        lo = freq_hz - offset
        if lo < low:                            # the bottom of its range
            lo, offset = low, freq_hz - low
    return rate, lo, offset


def decimation(rate, width_hz):
    """The radio's rate over this, to the nearest whole number below, is
    the rate rtl_433 gets: 250 kHz from 2 MS/s, 267 kHz from 2.4."""
    return max(1, int(rate // width_hz))


def band_taps(rate, out_rate):
    """Pass 80% of what rtl_433 gets, stop by its edge."""
    return firdes.low_pass(1.0, rate, 0.4 * out_rate, 0.2 * out_rate, window.WIN_HAMMING)


def device_key(msg):
    """What tells one sensor from another: its model, and its id and
    channel where it sends them."""
    return (str(msg.get('model', '?')), str(msg.get('id', '')), str(msg.get('channel', '')))


def readings_text(msg):
    """A message's own fields, as 'temperature_C 21.3, humidity 45'."""
    parts = []
    for key, value in msg.items():
        if key in META_KEYS:
            continue
        if isinstance(value, float):
            value = f"{value:.6g}"
        parts.append(f"{key} {value}")
    return ", ".join(parts)


class pipe_sink(gr.sync_block):
    """Complex samples, raised to rtl_433's level, to a file descriptor
    (rtl_433's stdin) that must not block the flowgraph: see the module
    notes. ``gain`` is the factor in force."""

    def __init__(self):
        gr.sync_block.__init__(self, name='rtl433_pipe', in_sig=[np.complex64], out_sig=None)
        self._lock = threading.Lock()
        self._fd = None
        self._pending = b''
        self._rms = collections.deque(maxlen=NOISE_BLOCKS)
        self.gain = None
        self.sent = 0
        self.dropped = 0
        self.broken = False

    def level(self, x):
        """``x`` times the gain, with the gain moved a step towards what
        puts the noise at ``NOISE_LEVEL``; no sample past full scale."""
        rms = float(np.sqrt(np.mean(x.real ** 2 + x.imag ** 2)))
        if rms > 0:
            self._rms.append(rms)
        if self._rms:
            noise = float(np.percentile(self._rms, NOISE_PERCENTILE))
            want = min(max(NOISE_LEVEL / noise, 1.0), MAX_GAIN) if noise > 0 else 1.0
            # A fifth of the way a block, in dB: steady within a second.
            self.gain = want if self.gain is None else self.gain * (want / self.gain) ** 0.2
        y = x * np.float32(self.gain or 1.0)
        mag = np.abs(y)
        over = mag > 0.99
        if over.any():
            y[over] *= (0.99 / mag[over]).astype(np.float32)
        return y

    def set_fd(self, fd):
        with self._lock:
            self._fd = fd
            self._pending = b''
            self.broken = False

    def _flush(self):
        try:
            while self._pending:
                n = os.write(self._fd, self._pending)
                self._pending = self._pending[n:]
        except BlockingIOError:
            pass
        except OSError:                          # rtl_433 has gone
            self._fd = None
            self._pending = b''
            self.broken = True

    def work(self, input_items, output_items):
        x = input_items[0]
        n = len(x)
        with self._lock:
            if self._fd is None or not n:
                return n
            if self._pending:
                self._flush()
            if self._pending:
                self.dropped += n
            else:
                self._pending = memoryview(self.level(x).astype(np.complex64).tobytes())
                self.sent += n
                self._flush()
        return n


class Rtl433:
    """The rtl_433 process: started on the pipe, its JSON read on a thread.

    ``take()`` hands the window what was decoded since it last asked;
    ``log_path``, when set, gets every line as it comes (JSON lines).
    """

    def __init__(self, path, rate, freq_hz, extra_args=()):
        self.path = path
        self.args = [path, '-r', 'cf32:-', '-s', str(int(round(rate))),
                     '-f', str(int(round(freq_hz))), '-F', 'json',
                     '-M', 'level', '-M', 'protocol', '-M', 'time:iso:usec:tz',
                     *extra_args]
        self._lock = threading.Lock()
        self._new = collections.deque(maxlen=QUEUE)
        self.messages = collections.deque(maxlen=20)   # its stderr
        self.decoded = 0
        self._log = None
        self.log_path = None
        self.log_error = None
        self.proc = subprocess.Popen(self.args, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     bufsize=0)
        fd = self.proc.stdin.fileno()
        os.set_blocking(fd, False)
        if hasattr(fcntl, 'F_SETPIPE_SZ'):
            # Linux: 1 MB of samples waiting rather than 64 kB.
            try:
                fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, 1 << 20)
            except OSError:
                pass
        self._threads = [threading.Thread(target=self._read_out, daemon=True),
                         threading.Thread(target=self._read_err, daemon=True)]
        for t in self._threads:
            t.start()

    @property
    def stdin_fd(self):
        return self.proc.stdin.fileno()

    def _read_out(self):
        for raw in iter(self.proc.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('{'):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            heard = time.time()
            with self._lock:
                self._new.append((heard, msg))
                self.decoded += 1
                log = self._log
                if log is not None:
                    try:
                        log.write(json.dumps({'heard': round(heard, 3), **msg}) + '\n')
                        log.flush()
                    except OSError as exc:
                        self.log_error = str(exc)

    def _read_err(self):
        for raw in iter(self.proc.stderr.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            if line:
                self.messages.append(line)

    def take(self):
        """[(unix time heard, message)] since the last call."""
        with self._lock:
            out = list(self._new)
            self._new.clear()
        return out

    def set_log(self, path):
        """Append every message to ``path`` from now on (None: stop)."""
        with self._lock:
            if self._log is not None:
                self._log.close()
                self._log = None
            self.log_path, self.log_error = path, None
            if path:
                try:
                    self._log = open(path, 'a', encoding='utf-8')
                except OSError as exc:
                    self.log_path, self.log_error = None, str(exc)

    def exit_code(self):
        return self.proc.poll()

    def close(self):
        self.set_log(None)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(1.0)


class DecodeChain:
    """Builds the chain in the module notes into top block ``tb``, and
    starts rtl_433 on it. Every block is kept here while the flowgraph
    may run (an RF bench toolkit rule). ``program`` None builds the chain
    with nothing on the pipe: the spectrum still shows."""

    RF_FFT = 4096

    def __init__(self, tb, source, rate, offset_hz, width_hz, freq_hz, program,
                 extra_args=()):
        self.rate = float(rate)
        self.width = float(width_hz)
        self.decim = decimation(self.rate, self.width)
        self.out_rate = self.rate / self.decim
        self.freq_hz = float(freq_hz)
        self._keep = []
        self.band = filter.freq_xlating_fir_filter_ccf(
            self.decim, band_taps(self.rate, self.out_rate), float(offset_hz), self.rate)
        self.pipe = pipe_sink()
        tb.connect(source, self.band, self.pipe)
        self.rf_probe = spectrum_tap(tb, self._keep, source, self.rate, self.RF_FFT, True)
        size = self.RF_FFT
        self.clip_keep = blocks.keep_m_in_n(gr.sizeof_gr_complex, size,
                                            size * spectrum_frames_per_s(self.rate, size), 0)
        self.clip = clip_probe()
        tb.connect(source, self.clip_keep, self.clip)
        self.proc = None
        if program:
            self.proc = Rtl433(program, self.out_rate, self.freq_hz, extra_args)
            self.pipe.set_fd(self.proc.stdin_fd)

    def take(self):
        return self.proc.take() if self.proc is not None else []

    def problem(self):
        """Why rtl_433 is not decoding, once it has stopped, else None."""
        if self.proc is None:
            return None
        code = self.proc.exit_code()
        if code is None and not self.pipe.broken:
            return None
        said = [m for m in self.proc.messages if m.strip()]
        why = said[-1] if said else f"exit code {code}"
        return f"rtl_433 stopped: {why}"

    def close(self):
        self.pipe.set_fd(None)
        if self.proc is not None:
            self.proc.close()
