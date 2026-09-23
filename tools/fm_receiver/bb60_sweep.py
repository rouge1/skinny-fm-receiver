"""The BB60D's own sweep: 9 kHz to 6 GHz at about 26 GHz/s.

FM receiver's own, not from the RF bench toolkit. Signal Hound's API
sweeps in the device itself, with its own spectral processing; hopping the
IQ stream's LO, as :mod:`sweep` does for every radio, covers 200-600 MHz/s
on the same BB60D. Measured on 2026-09-22: 9 kHz-6 GHz in 231 ms at any
RBW from 30 kHz to 1 MHz (19k to 614k points), the FM band in 11 ms.

**It borrows the SoapySDR module's device.** The module and this share one
``libbb_api`` in the process (one copy mapped, measured), so the device the
module opened - its handle is 0 - is swept here while the module's stream
is stopped. Switching costs about 10 ms to stop the stream and 150 ms to
start it again (measured), where closing one and opening the other would
cost 1.4 s and 1.6 s. The module sets the gain, attenuation, IQ centre and
rate afresh each time its stream starts, so a sweep's settings do not leak
into Receive: a station read -52.0 dBFS before, and -52.2 to -52.3 after
sweeps at 30 dB of attenuation, at auto gain and at full gain.

Levels are dBm at the input, calibrated by the device - not dBFS.

**Real time**, for spans up to 27 MHz (the FM band is 20.5): the API FFTs
every sample, at 50% overlap, instead of visiting each frequency in turn,
so nothing lasting longer than its probability-of-intercept time is missed
- 307 us at 10 kHz RBW, measured. Each of 30 frames a second brings the
trace (the most each point reached in that 33 ms) and a density map, 525 x
256: how often each level was hit, column by column, row 0 the bottom of
the scale and the last row the reference level. The reference level places
the map even with the gain set by hand, without changing a level. It cost
36-56% of a core on the FM band.

**On a Mac** (Apple Silicon) Signal Hound's library, 5.0.11 on, sweeps and
streams IQ but has no real time (``README_macos.txt`` in its SDK), and its
sweep takes RBWs down to 1 kHz - the least ``RBW_LADDER`` offers anyway.
:data:`REALTIME_OK` says which; the window greys Real time out without it.

The constants and calls below were written from the device's behaviour, and
then checked against Signal Hound's API reference (``bb_api.h`` and the
measurement modes, linked from ``knowledge/google_bb60d.md``): every value
matches. Two things it says that shape this module: ``bbFetchTrace_32f``
blocks, and the sweep does not start until it is called - so fetching less
often costs less (``MAX_SWEEPS_PER_S``); and 200 kHz is the suggested
minimum span, though 20 Hz is the absolute one.
"""

import ctypes
import math
import os
import sys
import threading
import time
from ctypes import POINTER, byref, c_double, c_float, c_int, c_uint32

import numpy as np  # type: ignore

from .bb60_source import gain_plan

#: The API's constants (``bb_api.h``, 5.0).
BB_SWEEPING = 0
BB_REAL_TIME = 1
BB_MIN_AND_MAX = 0
BB_AVERAGE = 1
BB_LOG_SCALE = 0
BB_POWER = 2
BB_RBW_SHAPE_NUTTALL = 0
BB_NO_SPUR_REJECT = 0
BB_ADC_OVERFLOW = 2
BB_MAX_DEVICES = 8
#: The BB60D's specified range (the API takes up to 6.4 GHz), and the least
#: span it is asked to sweep: 20 Hz is the API's floor, 200 kHz its advice.
MIN_HZ, MAX_HZ = 9e3, 6000e6
MIN_SPAN_HZ = 200e3

#: The RBWs offered, and what "auto" picks from: the smallest of these that
#: keeps a sweep near ``AUTO_POINTS`` points or fewer.
RBW_LADDER = (1e3, 3e3, 10e3, 30e3, 100e3, 300e3, 1e6)
AUTO_POINTS = 80000
#: More points than this are not asked for: a smaller RBW is raised. The
#: device takes about three points per RBW; 614k (6 GHz at 30 kHz) draws
#: and averages at the sweep rate, and below 10 kHz a wide sweep also
#: leaves the device's fast mode.
MAX_POINTS = 1_500_000
POINTS_PER_RBW = 3.2
#: Real time: the spans and RBWs the API takes (``BB_MIN_RT_SPAN``,
#: ``BB60C_MAX_RT_SPAN``, ``BB_MIN_RT_RBW``, ``BB_MAX_RT_RBW``), its frames
#: a second, and the RBW "auto" picks - 307 us to catch anything, 4,200
#: points over the FM band.
RT_MAX_SPAN_HZ = 27e6
RT_MIN_RBW, RT_MAX_RBW = 2465.820313, 631250.0
RT_AUTO_RBW = 10e3
RT_FRAME_RATE = 30
#: Whether this platform's library does real time: not the Mac's.
REALTIME_OK = sys.platform != 'darwin'
#: The reference level the API takes, and the density map's height.
REF_RANGE_DB = (-130.0, 20.0)
SCALE_RANGE_DB = (10.0, 200.0)

#: The API's library by the name the SoapySDR module links it under - its
#: soname on Linux, its install name on a Mac - and, on a Mac, where it is
#: installed: in the conda environment, beside the module that loads it
#: (see ``knowledge/usage.md``). dyld does not look in ``/usr/local/lib``
#: for a plain name (macOS 26), and a second copy anywhere would be a
#: second API, blind to the device the module opened.
if sys.platform == 'darwin':
    API_LIBRARY = 'libbb_api.5.dylib'
    API_PATHS = (os.path.join(sys.prefix, 'lib', API_LIBRARY),)
else:
    API_LIBRARY = 'libbb_api.so.5'
    API_PATHS = ()

_LIB = None


def load_api():
    """The API's library, by name, then by path."""
    for name in (API_LIBRARY,) + API_PATHS:
        try:
            return ctypes.CDLL(name)
        except OSError as exc:
            error = exc
    raise error


def _lib():
    """The API, loaded by its soname: the SoapySDR module has it in the
    process already, and the same copy is what is needed - a second one
    would see the device as not open."""
    global _LIB
    if _LIB is None:
        lib = load_api()
        lib.bbGetErrorString.restype = ctypes.c_char_p
        lib.bbGetErrorString.argtypes = [c_int]
        for name, args in (
                ('bbGetSerialNumber', [c_int, POINTER(c_uint32)]),
                ('bbConfigureRefLevel', [c_int, c_double]),
                ('bbConfigureGainAtten', [c_int, c_int, c_int]),
                ('bbConfigureCenterSpan', [c_int, c_double, c_double]),
                ('bbConfigureSweepCoupling', [c_int, c_double, c_double, c_double,
                                              c_uint32, c_uint32]),
                ('bbConfigureAcquisition', [c_int, c_uint32, c_uint32]),
                ('bbConfigureProcUnits', [c_int, c_uint32]),
                ('bbInitiate', [c_int, c_uint32, c_uint32]),
                ('bbAbort', [c_int]),
                ('bbQueryTraceInfo', [c_int, POINTER(c_uint32), POINTER(c_double),
                                      POINTER(c_double)]),
                ('bbFetchTrace_32f', [c_int, c_int, POINTER(c_float), POINTER(c_float)]),
                ('bbConfigureRealTime', [c_int, c_double, c_int]),
                ('bbQueryRealTimeInfo', [c_int, POINTER(c_int), POINTER(c_int)]),
                ('bbQueryRealTimePoi', [c_int, POINTER(c_double)]),
                ('bbFetchRealTimeFrame', [c_int, POINTER(c_float), POINTER(c_float),
                                          POINTER(c_float), POINTER(c_float)])):
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = c_int
        _LIB = lib
    return _LIB


class BB60Error(RuntimeError):
    pass


def _check(status, what):
    if status < 0:
        raise BB60Error(f"BB60 {what}: {_lib().bbGetErrorString(status).decode()}")
    return status


def device_handle():
    """The handle of the BB60 open in this process (the SoapySDR module's),
    or None."""
    lib = _lib()
    serial = c_uint32()
    for handle in range(BB_MAX_DEVICES):
        if lib.bbGetSerialNumber(handle, byref(serial)) == 0:
            return handle
    return None


#: Signal Hound's automatic gain and attenuation (``BB_AUTO_GAIN``,
#: ``BB_AUTO_ATTEN``), which follow the reference level.
BB_AUTO_GAIN = BB_AUTO_ATTEN = -1
#: AGC keeps the reference level this far over the strongest input, as
#: Signal Hound recommends, in steps of this size.
AGC_HEADROOM_DB = 5.0
AGC_STEP_DB = 5.0
#: It rises as soon as a signal needs it, and falls only once the strongest
#: signal has dropped this far, so a station fading doesn't retune it.
AGC_FALL_DB = 10.0
#: The strongest input is the most any sweep reached over this long, so a
#: burst that comes and goes doesn't drop the reference level between.
AGC_WINDOW_S = 3.0
#: The input level is the power the front end takes in at once, the IF's
#: width: 27 MHz, the widest real-time span. Measured on 2026-09-22, a
#: sweep's peak is no measure of it: at 1 kHz RBW an FM station's peak
#: swung from -54 to -39 dBm sweep to sweep (the carrier bunches up in
#: quiet audio), and AGC following it climbed from -20 to -5 dBm. Power
#: summed over a stretch held to +-0.2 dB at 1, 10 and 100 kHz RBW - an FM
#: station's envelope is constant. One station's power (-36.8 dBm, so a
#: reference of -30) wasn't enough either: at -30 and -35 dBm the device
#: overloaded now and then (3 sweeps in 800), with the whole FM band
#: reaching it; at -25 and -20, 5 dB over the band's power, it did not.
AGC_INPUT_BW_HZ = RT_MAX_SPAN_HZ


def strongest_input(db, bin_hz, rbw_hz):
    """The strongest input in a sweep, in dBm: the most power found in any
    :data:`AGC_INPUT_BW_HZ` of it (all of it, if narrower). Each point is
    the power in one RBW, and the points are ``bin_hz`` apart, so a
    stretch's power is its points' sum scaled by ``bin_hz / rbw_hz``;
    never less than the sweep's peak."""
    lin = 10.0 ** (np.asarray(db, dtype=np.float64) / 10.0)
    n = max(1, int(round(AGC_INPUT_BW_HZ / bin_hz)))
    if n >= len(lin):
        total = lin.sum()
    else:
        c = np.concatenate(([0.0], np.cumsum(lin)))
        total = (c[n:] - c[:-n]).max()
    return max(10.0 * math.log10(total * bin_hz / rbw_hz), float(np.max(db)))


def agc_ref(level_db, ref_db):
    """The reference level AGC wants for a strongest input of ``level_db``
    (:func:`strongest_input`), when it is ``ref_db`` now; None to leave
    it."""
    want = math.ceil((level_db + AGC_HEADROOM_DB) / AGC_STEP_DB) * AGC_STEP_DB
    want = min(max(want, REF_RANGE_DB[0]), REF_RANGE_DB[1])
    if want > ref_db or want <= ref_db - AGC_FALL_DB:
        return want
    return None


#: Signal Hound's ``BB_MIN_USB_VOLTAGE``: below it, measurements may be out
#: of specification (their API reference, bbGetDeviceDiagnostics).
MIN_USB_VOLTAGE = 4.4


def diagnostics():
    """The open BB60's temperature (deg C), USB voltage (V) and current
    (A), as a dict, or None when there is no device or no such call.

    Measured on 2026-09-22 (by behaviour): it answers in about 10 us while
    the IQ stream runs, in the device's own sweep and in real time, with
    nothing dropped. The current comes back in mA - 1187.5 for a BB60D
    drawing about 1.2 A - despite the argument's name, so it is scaled
    here. The temperature is in steps of 1/8 degree."""
    lib = _lib()
    fn = getattr(lib, 'bbGetDeviceDiagnostics', None)
    handle = device_handle()
    if fn is None or handle is None:
        return None
    if fn.argtypes is None:
        fn.argtypes = [c_int, POINTER(c_float), POINTER(c_float), POINTER(c_float)]
        fn.restype = c_int
    temp, volts, milliamps = c_float(), c_float(), c_float()
    if fn(handle, byref(temp), byref(volts), byref(milliamps)) < 0:
        return None
    return {'temp_c': temp.value, 'usb_v': volts.value, 'usb_a': milliamps.value / 1e3}


def gain_atten(percent):
    """The slider's 0-100% as the sweep's (gain, attenuation) indices, 0-3
    each, in the order :func:`bb60_source.gain_plan` opens them up: the
    attenuator (30 dB in 10 dB steps) comes off first, then gain goes on."""
    plan = gain_plan(percent)
    atten = int(round(-plan['ATT'] / 10.0))
    gain = int(round(plan['RF'] / 20.0 * 3))
    return min(max(gain, 0), 3), min(max(atten, 0), 3)


def auto_rbw(span_hz):
    for rbw in RBW_LADDER:
        if span_hz / (rbw / POINTS_PER_RBW) <= AUTO_POINTS:
            return rbw
    return RBW_LADDER[-1]


def least_rbw(span_hz):
    """The smallest RBW that keeps the sweep within ``MAX_POINTS``."""
    need = span_hz * POINTS_PER_RBW / MAX_POINTS
    return next((r for r in RBW_LADDER if r >= need), RBW_LADDER[-1])


class NativeSweepPlan:
    """A span for the device to sweep, and the RBW (None: automatic) - or,
    with ``realtime`` and a span of 27 MHz or less, to watch in real time,
    with a density map from ``ref_db`` down ``scale_db``. What the device
    makes of it - how many points, how far apart, the intercept time - is
    known once it is set up, and filled in by the sweeper."""

    native = True
    #: Calibrated by the device.
    unit = 'dBm'

    def __init__(self, start_hz, stop_hz, rbw_hz=None, realtime=False,
                 ref_db=-20.0, scale_db=100.0, auto_gain=False):
        start_hz = max(float(start_hz), MIN_HZ)
        stop_hz = min(float(stop_hz), MAX_HZ)
        if stop_hz - start_hz < MIN_SPAN_HZ:
            raise ValueError(f"the sweep must span at least {MIN_SPAN_HZ / 1e3:g} kHz")
        self.start_hz, self.stop_hz = start_hz, stop_hz
        span = stop_hz - start_hz
        self.asked_rbw = rbw_hz
        asked = bool(realtime) and REALTIME_OK
        self.realtime = asked and span <= RT_MAX_SPAN_HZ
        #: Real time was asked for, but the span is too wide for it.
        self.too_wide = asked and not self.realtime
        if self.realtime:
            rbw = RT_AUTO_RBW if not rbw_hz else float(rbw_hz)
            self.rbw = min(max(rbw, RT_MIN_RBW), RT_MAX_RBW)
        else:
            rbw = auto_rbw(span) if not rbw_hz else float(rbw_hz)
            self.rbw = max(rbw, least_rbw(span))
        self.raised = bool(rbw_hz) and abs(self.rbw - float(rbw_hz)) > 1
        self.ref_db = min(max(float(ref_db), REF_RANGE_DB[0]), REF_RANGE_DB[1])
        self.scale_db = min(max(float(scale_db), SCALE_RANGE_DB[0]), SCALE_RANGE_DB[1])
        #: Gain and attenuation left to the device, set by ``ref_db``.
        self.auto_gain = bool(auto_gain)
        self.points = None
        self.bin_hz = None
        self.poi_s = None
        self.trace_span = None
        self._freqs = None

    def set_trace(self, first_hz, bin_hz, count):
        """What the device will return: ``count`` points ``bin_hz`` apart
        from ``first_hz``. Keeps the ones inside the span."""
        f = first_hz + np.arange(count) * bin_hz
        keep = (f >= self.start_hz - bin_hz / 2) & (f <= self.stop_hz + bin_hz / 2)
        idx = np.nonzero(keep)[0]
        self.keep = slice(int(idx[0]), int(idx[-1]) + 1) if len(idx) else slice(0, count)
        self._freqs = f[self.keep]
        self.points = len(self._freqs)
        self.bin_hz = float(bin_hz)
        # The whole trace, which a real-time density map spans, cropped or not.
        self.trace_span = (float(first_hz) - bin_hz / 2, float(first_hz) + (count - 0.5) * bin_hz)

    def freqs(self):
        if self._freqs is None:
            return np.linspace(self.start_hz, self.stop_hz, 2)
        return self._freqs

    def describe(self):
        rbw = f"RBW {self.rbw / 1e3:.4g} kHz" + (" (auto)" if not self.asked_rbw else "")
        if self.raised:
            rbw += " - the nearest it takes" if self.realtime else " - raised, for this span"
        points = f", {self.points:,} points" if self.points else ""
        if self.realtime:
            poi = (f": nothing longer than {self.poi_s * 1e6:.0f} us is missed"
                   if self.poi_s else "")
            agc = f", AGC to {self.ref_db:.0f} dBm" if self.auto_gain else ""
            return f"The BB60D in real time, {rbw}{poi}{points}{agc}"
        wide = (" (real time takes 27 MHz at most)" if self.too_wide else "")
        agc = f", AGC to {self.ref_db:.0f} dBm" if self.auto_gain else ""
        return f"The BB60D's own sweep{wide}, {rbw}{points}{agc}"


class bb60_sweeper:
    """Sweeps the device on a thread of its own, and keeps the last sweep.

    Stands in for :class:`sweep.sweep_sink` in the window: ``snapshot()``,
    ``plan``, ``sweeps``, ``sweep_seconds``, ``set_plan``, ``set_paused``,
    ``detach``. There is no flowgraph while it runs.
    """

    native = True
    #: A sweep that fails is retried after this.
    RETRY_S = 0.5
    #: The device's processing is on this computer, and costs most of a core
    #: at full speed - measured, 87% sweeping the FM band 88 times a second,
    #: for a screen drawn 15 times. A narrow span waits out the rest of each
    #: 1/30 s; 9 kHz-6 GHz, at 4.3 a second, never does.
    MAX_SWEEPS_PER_S = 30.0
    #: :meth:`lost`: the API's words for an unplugged device, and how many
    #: failures in a row, with nothing fetched between, must carry them.
    CONNECTION_LOST = 'connection issues'
    LOST_AFTER_FAILURES = 2

    def __init__(self, handle, plan, gain_percent):
        self.handle = handle
        self.plan = plan
        self.gain_percent = float(gain_percent)
        self.paused = False
        self.sweeps = 0
        self.sweep_seconds = None
        self.overflows = 0
        self.error = None
        #: When a sweep or frame last came from the device (``Engine.data_age``).
        self.last_data = None
        #: Failures in a row since the last sweep or frame (:meth:`lost`).
        self.failures = 0
        self._lock = threading.Lock()
        self._configure = True
        self._stop = threading.Event()
        self._thread = None
        self._completed = None
        self._completed_serial = 0
        self._density = None
        self._held = None

    # -- control, from the Qt thread
    def start(self):
        self._configure_now()                  # errors show here, not later
        self._thread = threading.Thread(target=self._run, name='bb60 sweep', daemon=True)
        self._thread.start()

    def stop(self):
        """Finish the sweep in progress and leave the device idle - at most
        one sweep's time (231 ms for 9 kHz-6 GHz)."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        if self.handle is not None:
            _lib().bbAbort(self.handle)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def set_plan(self, plan, frames=None, settle_ms=None):
        with self._lock:
            self.plan = plan
            self._completed = None
            self._density = None
            self._held = None
            self._configure = True

    def set_gain_percent(self, percent):
        with self._lock:
            self.gain_percent = float(percent)
            self._configure = True

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)

    def set_frames(self, _frames):
        pass                                   # the device averages itself

    def set_settle_ms(self, _ms):
        pass                                   # nor is there a retune to wait out

    def detach(self):
        self.stop()
        self.handle = None

    def snapshot(self):
        """As :meth:`sweep.sweep_sink.snapshot`: (freqs, live, last, serial)
        - here every fetch is a whole sweep, so live and last are one."""
        with self._lock:
            plan = self.plan
            done = self._completed
            serial = self._completed_serial
        freqs = plan.freqs()
        if done is None or len(done) != len(freqs):
            return freqs, np.full(len(freqs), -200.0), None, serial
        return freqs, done, done, serial

    def take_held(self):
        """The most each point reached in every sweep or frame since the
        last call, or None if none came. The window draws 15 times a second
        and real time makes 30 frames: without this, every other frame -
        and a burst in it - never reached the trace, peak hold or
        waterfall."""
        with self._lock:
            held, self._held = self._held, None
        return held

    def density(self):
        """In real time, the last frame's density map - rows bottom (the
        reference level less the scale) to top (the reference level) - where
        it goes, and its persistence: (map, (low Hz, high Hz, bottom dB, top
        dB), alpha). None otherwise, or before the first frame.

        The API keeps a pixel in the map after its hits stop, and ``alpha``
        says how recent they were: 1 when hit, falling by 0.92 a frame
        (measured, 2026-09-22) - 0.4 s at 30 frames a second - to 0.042, then
        gone, 1.3 s after. Drawn as the pixel's opacity, a burst fades out
        where it was."""
        with self._lock:
            return self._density

    def lost(self):
        """Why the device has gone, or None: cheap, from any thread, never
        raises.

        Not just :attr:`error`, which is also set by failures the sweep
        retries past. Unplugged (2026-09-23), within 0.1 s every fetch
        failed with "BB60 sweep: Device connection issues detected" (the
        API's error string), re-set at each retry, for good - 130 s,
        plugged back in or not; running, :attr:`error` stayed None. So it
        takes those words, on :data:`LOST_AFTER_FAILURES` failures in a row
        (a retry's :data:`RETRY_S` apart, half a second) with no sweep
        between: a single one that the next fetch gets past is not a loss."""
        try:
            error = self.error
            if (error and self.failures >= self.LOST_AFTER_FAILURES
                    and self.CONNECTION_LOST in error.lower()):
                return ("the BB60D's USB connection was lost "
                        "(its sweep reports connection issues)")
        except Exception:
            pass
        return None

    # -- the device
    def _configure_now(self):
        lib, h = _lib(), self.handle
        with self._lock:
            plan, percent = self.plan, self.gain_percent
            self._configure = False
        lib.bbAbort(h)
        # With the gain set by hand the reference level changes no level; in
        # real time it is the top of the density map. With AGC the device
        # picks the gain and attenuation for it, band by band.
        if plan.auto_gain:
            gain, atten = BB_AUTO_GAIN, BB_AUTO_ATTEN
        else:
            gain, atten = gain_atten(percent)
        _check(lib.bbConfigureRefLevel(h, plan.ref_db if plan.realtime or plan.auto_gain
                                       else -20.0), 'reference level')
        _check(lib.bbConfigureGainAtten(h, gain, atten), 'gain')
        _check(lib.bbConfigureCenterSpan(h, (plan.start_hz + plan.stop_hz) / 2,
                                         plan.stop_hz - plan.start_hz), 'span')
        _check(lib.bbConfigureSweepCoupling(h, plan.rbw, plan.rbw, 0.001,
                                            BB_RBW_SHAPE_NUTTALL, BB_NO_SPUR_REJECT), 'RBW')
        # In real time the frame's 33 ms of FFTs are max-held, so a burst
        # shows at its full height; a sweep averages at each point.
        _check(lib.bbConfigureAcquisition(h, BB_MIN_AND_MAX if plan.realtime else BB_AVERAGE,
                                          BB_LOG_SCALE), 'detector')
        _check(lib.bbConfigureProcUnits(h, BB_POWER), 'units')
        if plan.realtime:
            _check(lib.bbConfigureRealTime(h, plan.scale_db, RT_FRAME_RATE), 'real time')
            _check(lib.bbInitiate(h, BB_REAL_TIME, 0), 'real time')
        else:
            _check(lib.bbInitiate(h, BB_SWEEPING, 0), 'sweep')
        count, bin_hz, first = c_uint32(), c_double(), c_double()
        _check(lib.bbQueryTraceInfo(h, byref(count), byref(bin_hz), byref(first)), 'trace')
        plan.set_trace(first.value, bin_hz.value, count.value)
        self._low = np.zeros(count.value, dtype=np.float32)
        self._high = np.zeros(count.value, dtype=np.float32)
        self._frame = self._alpha = None
        if plan.realtime:
            width, height, poi = c_int(), c_int(), c_double()
            _check(lib.bbQueryRealTimeInfo(h, byref(width), byref(height)), 'frame size')
            _check(lib.bbQueryRealTimePoi(h, byref(poi)), 'intercept time')
            plan.poi_s = poi.value
            self._frame_shape = (height.value, width.value)
            self._frame = np.zeros(height.value * width.value, dtype=np.float32)
            self._alpha = np.zeros_like(self._frame)
        self._t_sweep = time.monotonic()
        self._t_fetch = 0.0
        self.sweep_seconds = None

    def _run(self):
        lib = _lib()
        while not self._stop.is_set():
            try:
                if self._configure:
                    self._configure_now()
                if self.paused:
                    time.sleep(0.05)
                    continue
                low, high, frame = self._low, self._high, self._frame
                if frame is not None:
                    # Real time: a frame every 1/30 s, the API's own pace.
                    status = _check(lib.bbFetchRealTimeFrame(
                        self.handle, low.ctypes.data_as(POINTER(c_float)),
                        high.ctypes.data_as(POINTER(c_float)),
                        frame.ctypes.data_as(POINTER(c_float)),
                        self._alpha.ctypes.data_as(POINTER(c_float))), 'real time')
                else:
                    rest = self._t_fetch + 1.0 / self.MAX_SWEEPS_PER_S - time.monotonic()
                    if rest > 0 and self._stop.wait(rest):
                        break
                    self._t_fetch = time.monotonic()
                    status = _check(lib.bbFetchTrace_32f(
                        self.handle, len(high), low.ctypes.data_as(POINTER(c_float)),
                        high.ctypes.data_as(POINTER(c_float))), 'sweep')
                if status == BB_ADC_OVERFLOW:
                    self.overflows += 1
                now = time.monotonic()
                self.last_data = now
                with self._lock:
                    if self._configure:            # re-planned meanwhile
                        continue
                    plan = self.plan
                    self._completed = high[plan.keep].astype(np.float64)
                    self._held = (self._completed.copy() if self._held is None
                                  or len(self._held) != len(self._completed)
                                  else np.maximum(self._held, self._completed))
                    if frame is not None:
                        low_hz, high_hz = plan.trace_span
                        self._density = (frame.reshape(self._frame_shape).copy(),
                                         (low_hz, high_hz, plan.ref_db - plan.scale_db,
                                          plan.ref_db),
                                         self._alpha.reshape(self._frame_shape).copy())
                    self._completed_serial += 1
                    self.sweeps += 1
                    # Smoothed: one frame's time jitters by a few ms either way.
                    took = now - self._t_sweep
                    self.sweep_seconds = (took if self.sweep_seconds is None
                                          else 0.8 * self.sweep_seconds + 0.2 * took)
                    self._t_sweep = now
                    self.error = None
                    self.failures = 0
            except Exception as exc:                  # keep trying; say why
                self.error = str(exc)
                self.failures += 1
                self._configure = True
                self._stop.wait(self.RETRY_S)
