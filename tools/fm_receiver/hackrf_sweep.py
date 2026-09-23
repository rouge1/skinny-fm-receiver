"""The HackRF's own sweep: its firmware's sweep mode, through libhackrf.

FM receiver's own, not from the RF bench toolkit. Hopping the IQ stream's
LO from Python, as :mod:`sweep` does, takes about 400 steps and tens of
seconds over the HackRF's 1 MHz-6 GHz. In sweep mode the firmware retunes
by itself, 20 MHz at a time, and hands over one block per tuning: a 10-byte
header (0x7F 0x7F, then the tuning's frequency, 64 bits little-endian) and
8-bit samples at 20 MS/s. The method is ``hackrf_sweep``'s, the HackRF
project's own tool: an FFT of each block's last samples, keeping the two
5 MHz quarters 2.5-7.5 MHz either side of the LO, clear of its DC spike and
its 15 MHz filter's edges; ``INTERLEAVED`` tuning fills in between.

Done here rather than by running ``hackrf_sweep``, so the samples are to
hand: the levels are dBFS on the same scale as the rest of the window (the
same scaling as :func:`sweep.power_spectrum`), and clipping is counted per tuning, as
the LO-hopping sweep counts it (:meth:`hackrf_sweeper.clip_report`).

**The device is the IQ stream's, taken in turn.** SoapySDR's HackRF module
holds it while receiving, so :meth:`radios.HackRF.native_sweeper` closes
that first and this opens it; stopping closes it again for Receive. Both
use the one ``libhackrf`` in the process. ``hackrf_exit`` is never called:
it would end the USB context the SoapySDR module shares.
"""

import ctypes
import math
import os
import sys
import threading
import time
from ctypes import POINTER, byref, c_double, c_int, c_uint8, c_uint16, c_uint32, c_void_p

import numpy as np  # type: ignore

from .sweep import full_scale_count, to_db

#: The same library the SoapySDR module loaded, by its soname.
LIBRARY = 'libhackrf.0.dylib' if sys.platform == 'darwin' else 'libhackrf.so.0'

#: hackrf.h: bytes per block (one tuning), blocks per USB transfer, sweep
#: styles, and at most this many frequency ranges.
BYTES_PER_BLOCK = 16384
HEADER_BYTES = 10
LINEAR, INTERLEAVED = 0, 1
MAX_SWEEP_RANGES = 10
#: hackrf_sweep's: 20 MS/s, a 15 MHz baseband filter, 20 MHz a step, the
#: LO 7.5 MHz above each step's frequency.
RATE_HZ = 20e6
FILTER_HZ = 15_000_000
STEP_HZ = 20_000_000
OFFSET_HZ = 7_500_000
#: The FFT's size: a multiple of 8, so the kept quarters fall on whole bins,
#: and no more samples than a block holds after its header.
MIN_FFT, MAX_FFT = 8, 8184
#: FFTs averaged per tuning, from the block's second half (hackrf_sweep
#: takes the last samples, clear of the retune): one FFT at 264 points -
#: the whole range's auto bins - scattered the floor over 30 dB.
SAMPLES_PER_BLOCK = (BYTES_PER_BLOCK - HEADER_BYTES) // 2
MAX_FRAMES = 32


def frames_for(n):
    """FFTs of ``n`` points to average from each block's second half."""
    return max(1, min(MAX_FRAMES, (SAMPLES_PER_BLOCK // 2) // n))


#: What the firmware tunes: whole MHz, 16 bits.
MIN_HZ, MAX_HZ = 0.0, 7250e6
MIN_SPAN_HZ = 1e6
#: "Auto" keeps a sweep near this many points, as the BB60D's does.
AUTO_POINTS = 80000


class HackRFError(RuntimeError):
    pass


_LIB = None
_CALLBACK = ctypes.CFUNCTYPE(c_int, c_void_p)


class _Transfer(ctypes.Structure):
    _fields_ = [('device', c_void_p), ('buffer', POINTER(c_uint8)),
                ('buffer_length', c_int), ('valid_length', c_int),
                ('rx_ctx', c_void_p), ('tx_ctx', c_void_p)]


def _load():
    """By soname - the SoapySDR module's copy, once it is loaded - then in
    the environment: dyld on macOS doesn't search it for a plain name."""
    try:
        return ctypes.CDLL(LIBRARY)
    except OSError:
        return ctypes.CDLL(os.path.join(sys.prefix, 'lib', LIBRARY))


def _lib():
    global _LIB
    if _LIB is None:
        lib = _load()
        for name, args in (
                ('hackrf_init', []),
                ('hackrf_open', [POINTER(c_void_p)]),
                ('hackrf_close', [c_void_p]),
                ('hackrf_stop_rx', [c_void_p]),
                ('hackrf_is_streaming', [c_void_p]),
                ('hackrf_set_sample_rate', [c_void_p, c_double]),
                ('hackrf_set_baseband_filter_bandwidth', [c_void_p, c_uint32]),
                ('hackrf_set_amp_enable', [c_void_p, c_uint8]),
                ('hackrf_set_lna_gain', [c_void_p, c_uint32]),
                ('hackrf_set_vga_gain', [c_void_p, c_uint32]),
                ('hackrf_init_sweep', [c_void_p, POINTER(c_uint16), c_int, c_uint32,
                                       c_uint32, c_uint32, c_int]),
                ('hackrf_start_rx_sweep', [c_void_p, _CALLBACK, c_void_p])):
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = c_int
        lib.hackrf_error_name.argtypes = [c_int]
        lib.hackrf_error_name.restype = ctypes.c_char_p
        _LIB = lib
    return _LIB


def _check(status, what):
    if status != 0:
        name = _lib().hackrf_error_name(status)
        raise HackRFError(f"HackRF {what}: {name.decode() if name else status}")
    return status


def fft_size(rbw_hz):
    """The FFT size for a bin width near ``rbw_hz``: a multiple of 8."""
    n = int(round(RATE_HZ / float(rbw_hz) / 8.0)) * 8
    return min(max(n, MIN_FFT), MAX_FFT)


class HackRFSweepPlan:
    """A span for the firmware to sweep, and the bin width (None: about
    :data:`AUTO_POINTS` points). The firmware tunes whole MHz in 20 MHz
    steps, so it covers a little more than asked; the trace is cut to the
    span."""

    native = True
    realtime = False
    too_wide = False
    auto_gain = False
    unit = 'dBFS'

    def __init__(self, start_hz, stop_hz, rbw_hz=None, **_view):
        start_hz = max(float(start_hz), MIN_HZ)
        stop_hz = min(float(stop_hz), MAX_HZ)
        if stop_hz - start_hz < MIN_SPAN_HZ:
            raise ValueError(f"the sweep must span at least {MIN_SPAN_HZ / 1e6:g} MHz")
        self.start_hz, self.stop_hz = start_hz, stop_hz
        self.asked_rbw = rbw_hz
        want = float(rbw_hz) if rbw_hz else (stop_hz - start_hz) / AUTO_POINTS
        self.fft_size = fft_size(want)
        self.rbw = self.bin_hz = RATE_HZ / self.fft_size
        self.raised = bool(rbw_hz) and self.rbw > float(rbw_hz) * 1.05
        # The firmware's range, in whole MHz and whole steps.
        self.low_mhz = int(math.floor(start_hz / 1e6))
        steps = max(1, math.ceil((stop_hz / 1e6 - self.low_mhz) / (STEP_HZ / 1e6)))
        self.high_mhz = self.low_mhz + steps * int(STEP_HZ / 1e6)
        self.steps = steps
        # The panorama is on the firmware's grid; the trace is its part in
        # the span.
        self.total_bins = int(round((self.high_mhz - self.low_mhz) * 1e6 / self.bin_hz))
        first = int(math.ceil((start_hz - self.low_mhz * 1e6) / self.bin_hz))
        last = int(math.floor((stop_hz - self.low_mhz * 1e6) / self.bin_hz))
        self.keep = slice(first, min(last + 1, self.total_bins))
        self.points = self.keep.stop - self.keep.start
        self.poi_s = None
        self.trace_span = None

    def freqs(self):
        return self.low_mhz * 1e6 + np.arange(self.keep.start, self.keep.stop) * self.bin_hz

    def describe(self):
        rbw = f"bins {self.rbw / 1e3:.4g} kHz" + ("" if self.asked_rbw else " (auto)")
        if self.raised:
            rbw += " - the finest it takes"
        k = frames_for(self.fft_size)
        avg = f", {k} FFTs a tuning" if k > 1 else ""
        return (f"The HackRF's own sweep, {rbw}, {self.points:,} points, "
                f"{self.steps} steps of 20 MHz{avg}")


class hackrf_sweeper:
    """The firmware's sweep on a device of its own, the last whole sweep
    kept. Stands in for :class:`sweep.sweep_sink` in the window, as
    :class:`bb60_sweep.bb60_sweeper` does. Blocks arrive on libhackrf's USB
    thread; each transfer's are transformed together there."""

    native = True
    #: A narrow span sweeps far faster than the screen is drawn; each
    #: transfer past this rate is dropped rather than transformed.
    MAX_SWEEPS_PER_S = 30.0

    def __init__(self, plan, gain_plan, gain_percent):
        self.plan = plan
        self._gain_plan = gain_plan
        self.gain_percent = float(gain_percent)
        self.paused = False
        self.sweeps = 0
        self.sweep_seconds = None
        self.overflows = 0
        self.error = None
        self._lock = threading.Lock()
        self._device = None
        # Kept referenced while the device may call it (a toolkit rule).
        self._callback = _CALLBACK(self._on_transfer)
        self._streaming = False
        self._completed = None
        self._completed_serial = 0
        self._clip_last = None
        self._held = None
        self._t_sweep = time.monotonic()
        self._reset()

    # -- control, from the Qt thread
    def start(self):
        lib = _lib()
        _check(lib.hackrf_init(), 'init')
        device = c_void_p()
        # The SoapySDR module's device is closed just before; libusb can
        # take a moment to let it go.
        for attempt in range(20):
            status = lib.hackrf_open(byref(device))
            if status == 0:
                break
            time.sleep(0.05)
        _check(status, 'open')
        self._device = device
        try:
            _check(lib.hackrf_set_sample_rate(device, RATE_HZ), 'sample rate')
            _check(lib.hackrf_set_baseband_filter_bandwidth(device, FILTER_HZ), 'filter')
            self._apply_gain()
            self._start_sweep()
        except Exception:
            self.stop()
            raise

    def stop(self):
        """Stop and close the device, so the IQ stream can open it."""
        device, self._device = self._device, None
        if device is None:
            return
        lib = _lib()
        with self._lock:
            self._streaming = False
        lib.hackrf_stop_rx(device)
        lib.hackrf_close(device)

    @property
    def running(self):
        return self._device is not None and self._streaming

    def set_plan(self, plan, frames=None, settle_ms=None):
        with self._lock:
            self.plan = plan
            self._completed = None
            self._held = None
            self._clip_last = None
            self.sweep_seconds = None           # the old plan's pace
            self._t_sweep = time.monotonic()
            self._reset()
        if self._device is not None:
            lib = _lib()
            with self._lock:
                self._streaming = False
            lib.hackrf_stop_rx(self._device)
            self._start_sweep()

    def set_gain_percent(self, percent):
        self.gain_percent = float(percent)
        if self._device is not None:
            self._apply_gain()

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)

    def set_frames(self, _frames):
        pass

    def set_settle_ms(self, _ms):
        pass

    def detach(self):
        self.stop()

    def snapshot(self):
        with self._lock:
            plan = self.plan
            done = self._completed
            serial = self._completed_serial
        freqs = plan.freqs()
        if done is None or len(done) != len(freqs):
            return freqs, np.full(len(freqs), -200.0), None, serial
        return freqs, done, done, serial

    def take_held(self):
        """As :meth:`bb60_sweep.bb60_sweeper.take_held`: the most each point
        reached in every sweep since the last call - the FM band sweeps 30
        times a second, the window draws 15."""
        with self._lock:
            held, self._held = self._held, None
        return held

    def density(self):
        return None

    def clip_report(self):
        """As :meth:`sweep.sweep_sink.clip_report`: the last whole sweep's
        worst tuning, (share of its samples at full scale, its LO in Hz)."""
        with self._lock:
            return self._clip_last

    # -- the device
    def _apply_gain(self):
        lib, g = _lib(), self._gain_plan(self.gain_percent)
        _check(lib.hackrf_set_amp_enable(self._device, 1 if g['AMP'] > 0 else 0), 'amp')
        _check(lib.hackrf_set_lna_gain(self._device, int(g['LNA'])), 'LNA gain')
        _check(lib.hackrf_set_vga_gain(self._device, int(g['VGA'])), 'VGA gain')

    def _start_sweep(self):
        lib, plan = _lib(), self.plan
        freqs = (c_uint16 * 2)(plan.low_mhz, plan.high_mhz)
        _check(lib.hackrf_init_sweep(self._device, freqs, 1, BYTES_PER_BLOCK,
                                     STEP_HZ, OFFSET_HZ, INTERLEAVED), 'sweep setup')
        with self._lock:
            self._streaming = True
        _check(lib.hackrf_start_rx_sweep(self._device, self._callback, None), 'sweep start')

    def _reset(self):
        """A new panorama for the plan (with the lock held, or before any
        thread)."""
        plan = self.plan
        n = plan.fft_size
        self._window = np.hanning(n).astype(np.float32)
        self._panorama = np.full(plan.total_bins, np.nan)
        self._clip_steps = {}
        self._started = False
        self._next_start = 0.0
        # The kept quarters, as indices of the fftshifted FFT: 2.5-7.5 MHz
        # below the LO and above it.
        q = n // 8                                  # 2.5 MHz in bins
        self._lower = slice(n // 2 - 3 * q, n // 2 - q)
        self._upper = slice(n // 2 + q, n // 2 + 3 * q)
        self._t_last = 0.0

    def _on_transfer(self, transfer_p):
        """libhackrf's USB thread: one transfer, several tunings. Returns 0
        to go on, as libhackrf expects; nothing may raise out of here."""
        try:
            t = ctypes.cast(transfer_p, POINTER(_Transfer)).contents
            if not self._streaming:
                return 0
            raw = np.ctypeslib.as_array(t.buffer, shape=(t.valid_length,))
            blocks = raw[:t.valid_length // BYTES_PER_BLOCK * BYTES_PER_BLOCK] \
                .reshape(-1, BYTES_PER_BLOCK)
            with self._lock:
                if self.paused:
                    return 0
                self._take(blocks)
        except Exception as exc:                    # say so, and carry on
            self.error = str(exc)
        return 0

    def _take(self, blocks):
        plan = self.plan
        n = plan.fft_size
        ok = (blocks[:, 0] == 0x7F) & (blocks[:, 1] == 0x7F)
        if not ok.any():
            return
        blocks = blocks[ok]
        hz = blocks[:, 2:HEADER_BYTES].copy().view('<u8')[:, 0].astype(np.int64)
        low_hz = plan.low_mhz * 1_000_000
        # Which tunings go into a sweep: from the first tuning, once the
        # rate allows another sweep, to the first tuning again.
        use = np.zeros(len(hz), dtype=bool)
        ends = []                                   # finish before row i
        for i, f in enumerate(hz):
            if f == low_hz:
                if self._started:
                    ends.append(i)
                    self._started = False
                now = time.monotonic()
                if now >= self._next_start:
                    self._started = True
                    self._next_start = now + 1.0 / self.MAX_SWEEPS_PER_S
            use[i] = self._started
        if not use.any() and not ends:
            return
        rows = np.nonzero(use)[0]
        spec = clipped = None
        if len(rows):
            # The last k*n samples of each block, 8-bit I and Q, to full
            # scale 1; k FFTs of n averaged.
            k = frames_for(n)
            tail = blocks[rows, BYTES_PER_BLOCK - 2 * n * k:].view(np.int8) \
                .astype(np.float32) / 128.0
            x = (tail[:, 0::2] + 1j * tail[:, 1::2]).astype(np.complex64)
            frames = x.reshape(len(rows), k, n) * self._window
            spec = np.fft.fftshift(np.mean(np.abs(np.fft.fft(frames, axis=2)) ** 2, axis=1),
                                   axes=1) / np.sum(self._window) ** 2
            clipped = [full_scale_count(row) / row.size for row in x]
        j = 0
        for i in range(len(hz)):
            if i in ends:
                self._finish()
            if not use[i]:
                continue
            lo = int(hz[i]) + OFFSET_HZ
            below = int(round((lo - low_hz) / plan.bin_hz)) - n // 2
            for part in (self._lower, self._upper):
                a = below + part.start
                b = a + (part.stop - part.start)
                a0, b0 = max(a, 0), min(b, plan.total_bins)
                if b0 > a0:
                    self._panorama[a0:b0] = spec[j, part][a0 - a:b0 - a]
            self._clip_steps[lo] = clipped[j]
            j += 1

    def _finish(self):
        """A whole sweep is in: keep it, and its worst tuning for clipping."""
        plan = self.plan
        done = self._panorama[plan.keep]
        if np.isnan(done).any():                    # gaps: fill from neighbours
            good = ~np.isnan(done)
            if not good.any():
                return
            idx = np.arange(len(done))
            done = np.interp(idx, idx[good], done[good])
        self._completed = to_db(done)
        self._held = (self._completed.copy() if self._held is None
                      or len(self._held) != len(self._completed)
                      else np.maximum(self._held, self._completed))
        if self._clip_steps:
            lo, share = max(self._clip_steps.items(), key=lambda kv: kv[1])
            self._clip_last = (float(share), float(lo))
        self._clip_steps = {}
        self._panorama[:] = np.nan
        self._completed_serial += 1
        self.sweeps += 1
        now = time.monotonic()
        took = now - self._t_sweep
        self.sweep_seconds = (took if self.sweep_seconds is None
                              else 0.8 * self.sweep_seconds + 0.2 * took)
        self._t_sweep = now
        self.error = None
