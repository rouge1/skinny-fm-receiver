"""Sweep: cover more spectrum than the radio sees at once, with FFTs only.

The radio runs at a wide rate (20 MS/s, say) and hops its LO across the
span. At each stop, :class:`sweep_sink` drops the samples that might still
be from the last frequency, averages a few FFT frames, keeps the flat middle
of the band, and writes it into one long panorama. Nothing is filtered,
demodulated or decimated: per stop the work is a handful of FFTs, so a
sweep costs a small fraction of what receiving one station at the same
rate does. When a station is chosen the window switches to Receive, which
runs the radio at a narrow rate and does the IQ work there.

**The steps tile the span exactly, bin for bin.** The panorama is one grid,
``start + j * rbw``; step ``k`` has its LO at the centre of bins
``[k*U, (k+1)*U)``, where ``U`` is the number of flat bins per capture, so
its FFT bins land on that grid with no resampling and no gaps or overlaps
(:class:`SweepPlan`).

**Stale samples.** After a retune the flowgraph still holds samples taken
at the old frequency - in the source's output buffer, and in the radio's
own. The sink therefore skips what the source has written but the sink has
not yet read at the moment of the retune (read off the two blocks'
counters), plus the radio's settle time. On a BB60D the retune itself
blocks for ~24 ms and what follows is clean (measured), so its settle is
~1 ms; a HackRF buffers several milliseconds in USB transfers, so its
default is 40 ms. Too little shows as a ghost of the last step's signals.

**The DC spike** of a HackRF sits at every step's centre; ``dc_notch``
bins either side of it are replaced by a straight line between their
neighbours.
"""

import math
import threading
import time

import numpy as np  # type: ignore
from gnuradio import gr  # type: ignore

#: Spectrum levels are in dB relative to digital full scale: a full-scale
#: tone in one bin reads 0 dB.
FLOOR_DB = -200.0


class SweepPlan:
    """The steps of one sweep, and where each lands in the panorama."""

    def __init__(self, start_hz, stop_hz, rate, fft_size, usable_fraction,
                 dc_notch_hz=0.0):
        if stop_hz <= start_hz:
            raise ValueError("the sweep must stop above where it starts")
        self.start_hz = float(start_hz)
        self.stop_hz = float(stop_hz)
        self.rate = float(rate)
        self.fft_size = int(fft_size)
        self.rbw = self.rate / self.fft_size
        # Flat bins per capture: even, so the LO sits between the two middle
        # bins of the block's centre and the arithmetic below stays exact.
        usable = int(self.fft_size * min(max(usable_fraction, 0.05), 1.0))
        self.usable_bins = max(2, usable - usable % 2)
        self.total_bins = int(math.ceil((self.stop_hz - self.start_hz) / self.rbw))
        self.steps = max(1, int(math.ceil(self.total_bins / self.usable_bins)))
        self.dc_notch_bins = int(math.ceil(dc_notch_hz / self.rbw)) if dc_notch_hz else 0
        n, u = self.fft_size, self.usable_bins
        self.first_bin = n // 2 - u // 2           # of the fftshifted FFT

    @property
    def step_hz(self):
        return self.usable_bins * self.rbw

    def center(self, k):
        """The LO for step ``k``."""
        return self.start_hz + (k * self.usable_bins + self.usable_bins / 2) * self.rbw

    def centers(self):
        return [self.center(k) for k in range(self.steps)]

    def freqs(self):
        """Frequency of every panorama bin, trimmed to the span."""
        return self.start_hz + np.arange(self.total_bins) * self.rbw

    def place(self, panorama, k, spectrum):
        """Write step ``k``'s fftshifted power spectrum into ``panorama``."""
        u = self.usable_bins
        seg = np.array(spectrum[self.first_bin:self.first_bin + u], dtype=np.float64)
        d = self.dc_notch_bins
        if d:
            mid = u // 2                       # the bin at the LO
            lo, hi = mid - d - 1, mid + d + 1
            if lo >= 0 and hi < u:
                seg[lo + 1:hi] = np.linspace(seg[lo], seg[hi], hi - lo + 1)[1:-1]
        j0 = k * u
        j1 = min(j0 + u, len(panorama))
        panorama[j0:j1] = seg[:j1 - j0]

    def new_panorama(self):
        # Bins not measured yet read as the floor, not as zero power in dB.
        return np.full(self.total_bins, 10 ** (FLOOR_DB / 10))

    def describe(self):
        return (f"{self.steps} step{'s' if self.steps != 1 else ''} of "
                f"{self.step_hz / 1e6:.2f} MHz, RBW {self.rbw / 1e3:.2f} kHz")


#: Full scale for the clipping count: an 8-bit sample of 125 of 127 or
#: more, the threshold ble-scanner uses, so the two apps agree.
FULL_SCALE = 125 / 127


def full_scale_count(x):
    """Samples of ``x`` with I or Q at full scale (:data:`FULL_SCALE`)."""
    return int(np.count_nonzero((np.abs(x.real) >= FULL_SCALE)
                                | (np.abs(x.imag) >= FULL_SCALE)))


def power_spectrum(frames, window):
    """Mean power over frames, fftshifted, in full-scale units."""
    spec = np.fft.fft(frames * window, axis=1)
    power = np.mean(spec.real ** 2 + spec.imag ** 2, axis=0)
    return np.fft.fftshift(power) / (np.sum(window) ** 2)


def to_db(power):
    return 10.0 * np.log10(np.maximum(power, 10 ** (FLOOR_DB / 10)))


def find_stations(freqs, power_db, min_snr_db=15.0, grid_hz=100e3,
                  channel_hz=150e3, snap=True):
    """Stations in a sweep: channels standing ``min_snr_db`` above the floor.

    The power in a ``channel_hz`` window is measured on a ``grid_hz`` raster
    (100 kHz covers both the Americas' odd tenths and Europe's 100 kHz
    plan), and a channel counts when it beats both neighbours - an FM
    station's skirts light up the channels beside it, and those are not
    stations. The floor is the median, which a band full of stations still
    leaves on the noise. Returns ``[(freq_hz, snr_db, level_db), ...]``,
    strongest first.
    """
    freqs = np.asarray(freqs, dtype=np.float64)
    power_db = np.asarray(power_db, dtype=np.float64)
    if len(freqs) < 8:
        return []
    rbw = (freqs[-1] - freqs[0]) / max(1, len(freqs) - 1)
    floor = float(np.median(power_db))
    linear = 10 ** (power_db / 10)
    csum = np.concatenate([[0.0], np.cumsum(linear)])
    if snap:
        first = math.ceil(freqs[0] / grid_hz) * grid_hz
        grid = np.arange(first, freqs[-1], grid_hz)
    else:
        grid = np.arange(freqs[0] + channel_hz / 2, freqs[-1], grid_hz)
    half = max(1, int(round(channel_hz / 2 / rbw)))
    idx = np.clip(np.round((grid - freqs[0]) / rbw).astype(int), 0, len(freqs) - 1)
    lo = np.clip(idx - half, 0, len(freqs) - 1)
    hi = np.clip(idx + half + 1, 1, len(freqs))
    level = 10 * np.log10(np.maximum((csum[hi] - csum[lo]) / np.maximum(hi - lo, 1),
                                     1e-30))
    snr = level - floor
    found = []
    for i in range(len(grid)):
        left = snr[i - 1] if i > 0 else -np.inf
        right = snr[i + 1] if i + 1 < len(grid) else -np.inf
        if snr[i] >= min_snr_db and snr[i] >= left and snr[i] > right:
            found.append((float(grid[i]), float(snr[i]), float(level[i])))
    found.sort(key=lambda s: -s[1])
    return found


class sweep_sink(gr.sync_block):
    """Hops the radio across a :class:`SweepPlan` and stitches the panorama.

    ``tune(hz)`` is called from this block's thread; each radio's setter is
    safe there (the BB60D's leaves the value for its own work thread).
    ``source`` is the radio block, read only for its ``nitems_written``
    counter, to know how many stale samples are queued.
    """

    #: Samples also skipped after a retune on top of the counted backlog: the
    #: source may be part way through producing a buffer as the retune lands.
    GUARD = 65536
    #: A span one step covers needs no retuning, and would otherwise be FFT'd
    #: back to back - measured 4000+ sweeps/s and 140% CPU on a BB60D at
    #: 40 MS/s, for a screen drawn 15 times a second. So a one-step sweep
    #: skips samples to run at most this often.
    MAX_SWEEPS_PER_S = 30.0

    def __init__(self, plan, tune, source=None, frames=16, settle_ms=10.0):
        gr.sync_block.__init__(self, name='sweep_sink',
                               in_sig=[np.complex64], out_sig=None)
        self._tune = tune
        self._source = source
        self._lock = threading.Lock()
        self.paused = False
        self._set_plan(plan, frames, settle_ms)
        self.sweeps = 0
        self.sweep_seconds = None
        self._t_sweep = time.monotonic()
        self._completed = None
        self._completed_serial = 0

    # -- control, from the Qt thread
    def set_plan(self, plan, frames=None, settle_ms=None):
        with self._lock:
            self._set_plan(plan, frames or self.frames,
                           self.settle_ms if settle_ms is None else settle_ms)
            self._retune_pending = True

    def _set_plan(self, plan, frames, settle_ms):
        self.plan = plan
        self.frames = max(1, int(frames))
        self.settle_ms = max(0.0, float(settle_ms))
        n = plan.fft_size
        self._window = np.blackman(n).astype(np.float32)
        self._buf = np.zeros((self.frames, n), dtype=np.complex64)
        self._fill = 0
        self._panorama = plan.new_panorama()
        # The last whole sweep was of the old plan: its bins are not the new
        # plan's, and paired with its frequencies they mislabelled the
        # station list (and could not be masked, being another length).
        self._completed = None
        self._clip_steps = np.zeros(plan.steps)
        self._clip_last = None
        self._step = 0
        self._skip = self._settle_samples() + self.GUARD
        self._retune_pending = False
        self._t_sweep = time.monotonic()

    def clip_report(self):
        """The last complete sweep's worst step for clipping: (share of its
        samples at full scale, the step's centre in Hz), or None before a
        sweep is complete. Only the frames each step measures are counted,
        not the settle or the stale backlog. One step is what matters: a
        full-range sweep takes tens of seconds, and one TV transmitter
        clipping its step is lost in an average over all of them."""
        with self._lock:
            return self._clip_last

    def set_settle_ms(self, ms):
        with self._lock:
            self.settle_ms = max(0.0, float(ms))

    def set_frames(self, frames):
        with self._lock:
            self.frames = max(1, int(frames))
            self._buf = np.zeros((self.frames, self.plan.fft_size), dtype=np.complex64)
            self._fill = 0

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)

    def detach(self):
        """Let go of the radio, once this sweep is stopped for good: the
        block outlives it (the engine keeps old chains referenced), and a
        radio block it still held would keep the device open - a HackRF
        stayed busy to other programs after Stop until this."""
        with self._lock:
            self._source = None
            self._tune = lambda hz: None

    def snapshot(self):
        """(freqs, live panorama in dB, last complete sweep in dB or None,
        serial of that sweep) - the serial goes up once per complete sweep."""
        with self._lock:
            plan = self.plan
            live = to_db(self._panorama)
            done = None if self._completed is None else to_db(self._completed)
            serial = self._completed_serial
        return plan.freqs(), live, done, serial

    # -- streaming
    def _settle_samples(self):
        return int(self.settle_ms * 1e-3 * self.plan.rate)

    def _backlog(self, consumed):
        if self._source is None:
            return self.GUARD
        try:
            written = self._source.nitems_written(0)
        except Exception:
            return self.GUARD
        read = self.nitems_read(0) + consumed
        return max(0, int(written - read)) + self.GUARD

    def _retune(self, consumed):
        self._tune(self.plan.center(self._step))
        self._skip = self._settle_samples() + self._backlog(consumed)
        self._fill = 0

    def work(self, input_items, output_items):
        x = input_items[0]
        n = len(x)
        try:
            with self._lock:
                if self.paused:
                    return n
                if self._retune_pending:
                    self._retune_pending = False
                    self._retune(0)
                i = 0
                plan = self.plan
                size = plan.fft_size
                while i < n:
                    if self._skip:
                        take = min(self._skip, n - i)
                        self._skip -= take
                        i += take
                        continue
                    flat = self._buf.reshape(-1)
                    want = self.frames * size - self._fill
                    take = min(want, n - i)
                    flat[self._fill:self._fill + take] = x[i:i + take]
                    self._fill += take
                    i += take
                    if self._fill < self.frames * size:
                        continue
                    self._clip_steps[self._step] = full_scale_count(self._buf) / self._buf.size
                    plan.place(self._panorama, self._step,
                               power_spectrum(self._buf, self._window))
                    self._step += 1
                    if self._step >= plan.steps:
                        self._step = 0
                        self._completed = self._panorama.copy()
                        worst = int(np.argmax(self._clip_steps))
                        self._clip_last = (float(self._clip_steps[worst]), plan.center(worst))
                        self._completed_serial += 1
                        self.sweeps += 1
                        now = time.monotonic()
                        self.sweep_seconds = now - self._t_sweep
                        self._t_sweep = now
                    if plan.steps > 1:
                        self._retune(i)
                    else:
                        self._fill = 0
                        self._skip = max(0, int(plan.rate / self.MAX_SWEEPS_PER_S)
                                         - self.frames * size)
        except Exception as exc:                  # never kill the flowgraph
            print(f"sweep_sink: {exc}")
        return n
