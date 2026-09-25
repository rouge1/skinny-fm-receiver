"""rtl_433 on the radio's samples: the ISM-band sensors around - weather
stations, tyre pressure, doorbells, remotes - decoded by rtl_433
(https://github.com/merbanan/rtl_433), for whichever radio is open.

::

    IQ (rate R, LO) ─┬─ shift the frequency to 0 Hz, low-pass, decimate
                     │    to about the width ─ pipe ─ rtl_433 -r cf32:- -F json
                     ├─ RF spectrum tap
                     └─ clip probe

    or, for the whole band:

    IQ ─ DC blocker ─ shift W/2 ─ channelizer, M slices W apart ─┬─ pipe ─ rtl_433
         (a spike's)              (each 2W wide, at 2W)          ├─ pipe ─ rtl_433
                                                                 └─ ... one each

**The whole band, in slices.** rtl_433 decodes anything in what it is
given, but best at about 250 kS/s, its default. So for the whole band a
polyphase channelizer cuts the radio's band into slices ``W`` apart, about
250 kHz (``rate / M``), and each gets its own rtl_433 (:func:`band_plan`).
The band is shifted by half a slice first, so the LO falls on the boundary
between two slices rather than in the middle of one. Each slice is
sampled at ``2W`` and passes 0.8 ``W`` either side of its centre, so the
slices overlap: a sensor up to 0.6 ``W`` wide (150 kHz: a wide FSK one)
lies whole in at least one. What two slices both hear is kept once, if it
comes within ``DUP_S`` (:meth:`DecodeChain.take`). Measured with tones
(2.4 MS/s, M = 10): each slice on channel ``k mod M``, centred to 0 Hz.
The LO falls inside the two slices either side of it, so a radio with a
DC spike has its mean taken away first.

**The frequency sits clear of the LO.** A HackRF's DC spike in the middle of
what rtl_433 sees would read as a carrier: the LO is put ``width/2 +
min_offset_hz`` below the frequency (:func:`plan`), at the radio's lowest
receive rate that holds that, so the spike is outside the band passed on.

**The noise is lifted to an RTL-SDR's level.** rtl_433 takes its samples
at an 8-bit RTL-SDR's scale: a burst at 0.003 of full scale, 40 dB over the
noise, was not decoded at all (rtl_433 23.11, 2026-09-24), where the same
burst at 0.3 was. The BB60D's noise sits about 60 dB under an RTL-SDR's, so
:class:`pipe_sink` raises the samples until the noise is ``NOISE_LEVEL``
(a slow gain, from the quieter blocks of the last second or two), but never
past full scale - block by block, the peak sets the most it may be raised
(rtl_433 lost an on-off burst at 30x full scale, and an FSK one clipped to
it) - and never turns anything down.

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
#: The Width that means the whole band, in slices of about ``SLICE`` Hz: at
#: most ``MAX_SLICES`` of them, one rtl_433 each. A message from two slices
#: this close together in time is one burst, heard across their boundary.
WHOLE_BAND = 0.0
SLICE = 250e3
MAX_SLICES = 12
DUP_S = 1.0
#: Each slice's rate over its spacing ``W``: 2, so each can pass more than
#: ``W`` and the slices overlap (see ``slice_taps``).
OVERSAMPLE = 2
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
LEVEL_STRIDE = 16
#: The most any sample's I or Q is raised to.
PEAK = 0.9
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


def band_plan(radio, freq_hz):
    """(radio rate, LO, M, W, slice centres from the LO) for the whole band
    around ``freq_hz``: the widest receive rate whose usable band holds no
    more than ``MAX_SLICES`` slices (the lowest, cut to that many, if
    none does). ``M`` is the channelizer's size, ``rate / M = W`` about
    ``SLICE``; the slices sit either side of the LO, the LO on a boundary.
    An IQ recording's band is the one it has. ValueError if the radio
    cannot reach it."""
    low, high = radio.freq_range_hz
    rates = sorted(radio.usable_receive_rates()) or [radio.default_receive_rate]
    if radio.kind == 'file':
        rates = rates[-1:]
    best = None
    for rate in rates:
        m = max(2, int(round(rate / SLICE)))
        m += m % 2                               # the channelizer's /2
        w = rate / m
        per_side = int(rate * radio.usable_fraction(rate) / 2 // w)
        if 2 * per_side <= MAX_SLICES:
            best = (rate, m, w, max(1, per_side))
        elif best is None:
            best = (rate, m, w, MAX_SLICES // 2)
            break
        else:
            break
    rate, m, w, per_side = best
    lo = radio.center_hz if radio.kind == 'file' else float(freq_hz)
    if radio.kind != 'file' and not low <= lo <= high:
        raise ValueError(f"{lo / 1e6:.3f} MHz is outside the {radio.name}'s "
                         f"range ({low / 1e6:g} to {high / 1e6:g} MHz)")
    centres = [(k + 0.5) * w for k in range(-per_side, per_side)]
    return rate, lo, m, w, centres


def slice_taps(rate, w):
    """Each slice ``w`` from the next, but passing 0.8 ``w`` either side of
    its centre (6 dB down at 0.9, stopped by ``w``, its Nyquist at ``2w``):
    the slices overlap, so a signal up to 0.6 ``w`` wide lies whole in one
    of them. Slices that only met (passing 0.5 ``w``) lost a Bresser 5-in-1
    (FSK, 150 kHz wide) on a boundary and 75 kHz off a centre."""
    return firdes.low_pass(1.0, rate, 0.9 * w, 0.2 * w, window.WIN_HAMMING)


def payload(msg):
    """What a message says, without where or how strongly it was heard:
    the same from two slices is one burst."""
    return json.dumps({k: v for k, v in msg.items()
                       if k in ('model', 'id', 'channel') or k not in META_KEYS},
                      sort_keys=True, default=str)


def decimation(rate, width_hz):
    """The radio's rate over this, to the nearest whole number below, is
    the rate rtl_433 gets: 250 kHz from 2 MS/s, 267 kHz from 2.4."""
    return max(1, int(rate // width_hz))


def band_taps(rate, out_rate):
    """Pass 90% of what rtl_433 gets (6 dB down at 0.45 of its rate either
    side), stop by its edge, as an RTL-SDR at that rate would give it:
    passing 80%, a Bresser 5-in-1 (tones -89 and +24 kHz) was not decoded."""
    return firdes.low_pass(1.0, rate, 0.45 * out_rate, 0.1 * out_rate, window.WIN_HAMMING)


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


def command(path, rate, freq_hz, extra_args=()):
    """rtl_433's command line for one slice. **-f before -s**: over 800
    MHz a -f after it sets the rate back to rtl_433's own there (1024k),
    and nothing at 868 or 915 MHz decoded - a Bresser 5-in-1's recording,
    0 decodes that way round, 1 this (2026-09-24)."""
    return [path, '-r', 'cf32:-', '-f', str(int(round(freq_hz))),
            '-s', str(int(round(rate))), '-F', 'json',
            '-M', 'level', '-M', 'protocol', '-M', 'time:iso:usec:tz', *extra_args]


class Pipe:
    """One slice's samples, raised to rtl_433's level, to a file descriptor
    (its rtl_433's stdin) that must not block the flowgraph: see the module
    notes. ``gain`` is the factor it is moving towards, ``applied`` the one
    the last block got. :meth:`feed` is called from :class:`pipe_sink`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fd = None
        self._pending = b''
        self._rms = collections.deque(maxlen=NOISE_BLOCKS)
        self.gain = None                          # to put the noise at NOISE_LEVEL
        self.applied = None                       # used on the last block: PEAK-limited
        self.sent = 0
        self.dropped = 0
        self.broken = False

    def level(self, x):
        """``x`` times the gain, with the gain moved a step towards what
        puts the noise at ``NOISE_LEVEL``, but no more than keeps the
        block's peak at ``PEAK``: never past full scale (rtl_433 lost an
        on-off burst at 30x full scale). A few passes over the samples,
        the noise judged from every ``LEVEL_STRIDE``-th."""
        sub = x[::LEVEL_STRIDE]
        rms = float(np.sqrt(np.vdot(sub, sub).real / len(sub))) if len(sub) else 0.0
        if rms > 0:
            self._rms.append(rms)
        if self._rms:
            # The 20th percentile, picked in Python: numpy's took 50 us a
            # call, most of the cost at a few thousand samples a block.
            ranked = sorted(self._rms)
            noise = ranked[len(ranked) * NOISE_PERCENTILE // 100]
            want = min(max(NOISE_LEVEL / noise, 1.0), MAX_GAIN) if noise > 0 else 1.0
            # A fifth of the way a block, in dB: steady within a second.
            self.gain = want if self.gain is None else self.gain * (want / self.gain) ** 0.2
        gain = self.gain or 1.0
        iq = x.view(np.float32)
        peak = max(float(iq.max()), -float(iq.min())) if len(iq) else 0.0
        if peak * gain > PEAK:
            gain = max(1.0, PEAK / peak)
        self.applied = gain
        y = x * np.float32(gain)
        if peak * gain > 1.0:                     # a radio at full scale: as it is
            view = y.view(np.float32)
            np.clip(view, -1.0, 1.0, out=view)
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

    def feed(self, x):
        n = len(x)
        with self._lock:
            if self._fd is None or not n:
                return
            if self._pending:
                self._flush()
            if self._pending:
                self.dropped += n
            else:
                self._pending = memoryview(self.level(x)).cast('B')
                self.sent += n
                self._flush()


class pipe_sink(gr.sync_block):
    """``n`` slices into their :class:`Pipe`, in one Python block: a
    dozen blocks, one a slice, each in its own thread, took twice the CPU
    fighting over Python's lock (70% of a core against 140%, 12 slices of
    a 4 MS/s band, 2026-09-24)."""

    def __init__(self, n=1):
        gr.sync_block.__init__(self, name='rtl433_pipes', in_sig=[np.complex64] * n,
                               out_sig=None)
        self.pipes = [Pipe() for _ in range(n)]
        # Bigger blocks, fewer calls: each costs about 10 us whatever its size.
        self.set_min_noutput_items(4096)

    def work(self, input_items, output_items):
        for pipe, x in zip(self.pipes, input_items):
            pipe.feed(x)
        return len(input_items[0])


class Rtl433:
    """The rtl_433 process: started on the pipe, its JSON read on a thread.
    ``take()`` hands over what was decoded since it was last asked."""

    def __init__(self, path, rate, freq_hz, extra_args=()):
        self.path = path
        self.args = command(path, rate, freq_hz, extra_args)
        self._lock = threading.Lock()
        self._new = collections.deque(maxlen=QUEUE)
        self.messages = collections.deque(maxlen=20)   # its stderr
        self.decoded = 0
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
            with self._lock:
                self._new.append((time.time(), msg))
                self.decoded += 1

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

    def exit_code(self):
        return self.proc.poll()

    def close(self):
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
    """Builds the chain in the module notes into top block ``tb`` and starts
    rtl_433 on it: one slice ``width_hz`` wide at ``offset_hz`` from the LO,
    or with ``band`` (``(M, W, centres)`` from :func:`band_plan`) the whole
    band in slices, one rtl_433 each. Every block is kept here while the
    flowgraph may run (an RF bench toolkit rule). ``program`` None builds
    the chain with nothing on the pipes: the spectrum still shows.
    ``dc_notch_hz``: the radio's DC spike, blocked before the channelizer."""

    RF_FFT = 4096

    def __init__(self, tb, source, rate, lo_hz, program, extra_args=(), *,
                 offset_hz=0.0, width_hz=DEFAULT_WIDTH, band=None, dc_notch_hz=0.0):
        self.rate = float(rate)
        self.lo_hz = float(lo_hz)
        self._keep = []
        self.slices = []                          # [(centre Hz, pipe_sink)]
        if band is None:
            self.width = float(width_hz)
            self.decim = decimation(self.rate, self.width)
            self.out_rate = self.rate / self.decim
            self.band = filter.freq_xlating_fir_filter_ccf(
                self.decim, band_taps(self.rate, self.out_rate), float(offset_hz), self.rate)
            self.sink = pipe_sink(1)
            tb.connect(source, self.band, self.sink)
            self.slices.append((self.lo_hz + offset_hz, self.sink.pipes[0]))
            self.low_hz = self.lo_hz + offset_hz - self.width / 2
            self.high_hz = self.lo_hz + offset_hz + self.width / 2
        else:
            m, w, centres = band
            self.width = float(w)
            self.out_rate = OVERSAMPLE * w
            head = source
            if dc_notch_hz:
                # The spike's mean taken away: a DC blocker cost 23-43% of a
                # core at 4 MS/s, this a few.
                self.dc_mean = filter.single_pole_iir_filter_cc(2 * np.pi * 1e3 / self.rate)
                self.dc = blocks.sub_cc()
                tb.connect(head, (self.dc, 0))
                tb.connect(head, self.dc_mean, (self.dc, 1))
                head = self.dc
            self.shift = blocks.rotator_cc(-2 * np.pi * (w / 2) / self.rate)
            from gnuradio.filter import pfb  # type: ignore
            self.channelizer = pfb.channelizer_ccf(m, slice_taps(self.rate, w),
                                                   float(OVERSAMPLE))
            tb.connect(head, self.shift, self.channelizer)
            used = {int(round(c / w - 0.5)) % m: c for c in centres}
            order = sorted(used, key=lambda i: used[i])
            self.sink = pipe_sink(len(order))
            for port, i in enumerate(order):
                tb.connect((self.channelizer, i), (self.sink, port))
                self.slices.append((self.lo_hz + used[i], self.sink.pipes[port]))
            for i in range(m):
                if i not in used:
                    null = blocks.null_sink(gr.sizeof_gr_complex)
                    tb.connect((self.channelizer, i), null)
                    self._keep.append(null)
            self.low_hz = self.slices[0][0] - w / 2
            self.high_hz = self.slices[-1][0] + w / 2
        self.freq_hz = (self.low_hz + self.high_hz) / 2
        self.rf_probe = spectrum_tap(tb, self._keep, source, self.rate, self.RF_FFT, True)
        size = self.RF_FFT
        self.clip_keep = blocks.keep_m_in_n(gr.sizeof_gr_complex, size,
                                            size * spectrum_frames_per_s(self.rate, size), 0)
        self.clip = clip_probe()
        tb.connect(source, self.clip_keep, self.clip)
        self.procs = []
        self._recent = {}                         # payload -> (heard, slice)
        self.duplicates = 0                       # dropped by take(): see DUP_S
        self._log = None
        self.log_path = None
        self.log_error = None
        if program:
            try:
                for centre, pipe in self.slices:
                    proc = Rtl433(program, self.out_rate, centre, extra_args)
                    self.procs.append(proc)
                    pipe.set_fd(proc.stdin_fd)
            except Exception:
                self.close()
                raise

    @property
    def proc(self):
        """The first rtl_433, or None: what the window asks its version."""
        return self.procs[0] if self.procs else None

    @property
    def decoded(self):
        return sum(p.decoded for p in self.procs)

    @property
    def dropped(self):
        return sum(pipe.dropped for _, pipe in self.slices)

    @property
    def gain(self):
        """The most any slice's samples are raised (None before the first)."""
        gains = [pipe.gain for _, pipe in self.slices if pipe.gain]
        return max(gains) if gains else None

    def take(self):
        """[(unix time heard, message)] since the last call, from every
        slice, oldest first; the same message from another slice within
        ``DUP_S`` dropped. Logged, when a log is set."""
        got = []
        for i, proc in enumerate(self.procs):
            got.extend((heard, i, msg) for heard, msg in proc.take())
        got.sort(key=lambda g: g[0])
        out = []
        for heard, i, msg in got:
            key = payload(msg)
            seen = self._recent.get(key)
            if seen is not None and seen[1] != i and heard - seen[0] < DUP_S:
                self.duplicates += 1              # the next slice heard it too
                continue
            self._recent[key] = (heard, i)
            out.append((heard, msg))
        if got:
            newest = got[-1][0]
            self._recent = {k: v for k, v in self._recent.items() if newest - v[0] < DUP_S}
        if out and self._log is not None:
            try:
                for heard, msg in out:
                    self._log.write(json.dumps({'heard': round(heard, 3), **msg}) + '\n')
                self._log.flush()
            except OSError as exc:
                self.log_error = str(exc)
        return out

    def set_log(self, path):
        """Append every message to ``path`` from now on (None: stop)."""
        if self._log is not None:
            self._log.close()
            self._log = None
        self.log_path, self.log_error = path, None
        if path:
            try:
                self._log = open(path, 'a', encoding='utf-8')
            except OSError as exc:
                self.log_path, self.log_error = None, str(exc)

    def problem(self):
        """Why rtl_433 is not decoding, once one has stopped, else None."""
        for proc, (centre, pipe) in zip(self.procs, self.slices):
            code = proc.exit_code()
            if code is None and not pipe.broken:
                continue
            said = [m for m in proc.messages if m.strip()]
            why = said[-1] if said else f"exit code {code}"
            where = f" (the slice at {centre / 1e6:.3f} MHz)" if len(self.slices) > 1 else ""
            return f"rtl_433 stopped{where}: {why}"
        return None

    def close(self):
        self.set_log(None)
        for _, pipe in self.slices:
            pipe.set_fd(None)
        for proc in self.procs:
            proc.close()
