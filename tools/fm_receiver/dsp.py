"""The receive chain: one FM station out of the radio's IQ, as stereo audio,
RDS, spectra and levels.

::

    IQ (rate R, LO) ─┬─ xlate + decimate to ~1 MS/s ─ channel filter, resample
                     │    to 500 kS/s (channel BW is live) ─┬─ FM discriminator ─ /2 ─ MPX
                     │                                      └─ channel IQ recorder
                     ├─ RF spectrum tap ─ band IQ recorder
    MPX ─┬─ 19 kHz band-pass ─ PLL ─┬─ RDS decoder (rds_core)
         │                          └─ PLL² = 38 kHz ─ phase ─ x MPX ─ side (L-R)
         ├─ 16-18 + 20-22 kHz band-pass: the noise beside the pilot
         ├─ mid (L+R) ─┐
         └─ MPX spectrum tap         mid ± side ─ de-emphasis ─ L, R ─ audio tap
                                     (levels, WAV) ─ volume/mute ─ sound card

**The channel runs at 500 kS/s, the MPX at 250.** An FM station needs
no more than 250 kS/s, but a channel filter opened past that - up to
400 kHz, wide enough to take in an HD Radio station's digital sidebands -
needs room: the channel (and its IQ recording) is 500 kS/s, the
discriminator runs there, and its output is low-passed and halved to the
250 kS/s multiplex everything after it expects. Past about 250 kHz the
audio takes in any neighbour that close.

**The channel is filtered in two stages.** A freq-xlating filter shifts the
station to 0 Hz and decimates to about 1 MS/s with a short, wide filter;
the channel filter proper then runs at that low rate. One stage at 20 MS/s
with a 20 kHz transition would be ~3300 taps; this way the two cost a few
hundred MACs per output sample at any rate.

**The pilot band-pass has no phase shift at 19 kHz**, by construction: its
taps are a low-pass times ``exp(j w0 n)`` with ``n`` counted from the first
tap, not the middle, so a tone at exactly 19 kHz comes out in phase with
itself. The PLL then locks to the pilot's own phase, and the 38 kHz carrier
made by squaring it lines up with the MPX with no delay to match. (The RF
bench toolkit's band-pass is centred, which is why its separation test had
to fit a 144 degree offset.)

**A pilot is a tone that stands out from the noise beside it**, not a level.
With no station, the discriminator turns noise into a multiplex full of
noise, and its 19 kHz band reads about 6e-3 - three times a real 9% pilot
(2e-3). A fixed level (the toolkit's 1e-4) called every empty channel
stereo. Nothing is broadcast from 15 to 23 kHz but the pilot, so the chain
measures that guard band either side of it and :meth:`ReceiveChain.pilot_locked`
asks for the pilot to stand ``PILOT_LOCK_DB`` over it, in the same
bandwidth. Measured on a BB60D band recording (2026-09-22): 25 empty
channels within 2 dB of 0, the five stations with a pilot 17 to 37 dB.

**Which 38 kHz phase, found from the signal.** The broadcast standard puts
the pilot and the subcarrier both as sines, crossing zero together, which is
90 degrees at 38 kHz from the cosine pair the RF bench toolkit's own FM +
RDS transmitter sends - and demodulating with the wrong one recovers no
stereo at all. :class:`stereo_phase_probe` mixes the MPX down by the squared
pilot and measures the axis of what the L-R signal leaves there; the window
uses the standard phase until that axis says otherwise.
"""

import cmath
import math
import os
import threading
import time

import numpy as np  # type: ignore
from gnuradio import analog, blocks, fft, filter, gr  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore
from scipy import signal as sps  # type: ignore

from .rds_core import RdsDemod, RdsProtocol
from .sweep import full_scale_count

MPX_RATE = 250e3
#: The channel's rate: twice the MPX, so the filter can open to 400 kHz.
CHANNEL_RATE = 500e3
#: The widest channel filter: HD Radio's sidebands reach +-199 kHz.
CHANNEL_MAX_BW = 400e3
AUDIO_RATE = 48000
MAX_DEVIATION = 75e3
PILOT_HZ = 19e3
#: How far the pilot must stand over the noise beside it, in the same
#: bandwidth, for stereo; it is let go only below the second. Noise reads
#: within 2 dB of 0; off air, pilots 13 dB and up, and a weak station
#: (RDS 72% good) 8-12 dB, which the second holds. A synthetic pilot still
#: reads 9 dB at an SNR of 5 dB, where mono is better anyway.
PILOT_LOCK_DB = 10.0
PILOT_UNLOCK_DB = 6.0
#: Taps of the audio low-pass-and-resample, 250 kHz -> 48 kHz (24/125).
AUDIO_INTERP, AUDIO_DECIM = 24, 125
#: The 38 kHz phase each convention needs, relative to twice the PLL's.
PHASE_STANDARD = math.pi / 2          # pilot and subcarrier both sines
PHASE_COSINE = 0.0                    # both cosines (the toolkit's transmitter)
DEEMPHASIS = {'RBDS': 75e-6, 'RDS': 50e-6}


# ------------------------------------------------------------ filter design

def plan_channelizer(rate):
    """(stage-1 decimation, intermediate rate, resample up, resample down)."""
    d1 = max(1, int(rate // 1e6))
    mid = rate / d1
    ratio = CHANNEL_RATE / mid
    from fractions import Fraction
    frac = Fraction(ratio).limit_denominator(128)
    return d1, mid, frac.numerator, frac.denominator


def stage1_taps(rate, mid_rate):
    """Wide and short: pass the channel, stop what would alias onto it."""
    if rate == mid_rate:
        return [1.0]
    passband = CHANNEL_MAX_BW / 2
    stop = mid_rate - passband
    return firdes.low_pass(1.0, rate, (passband + stop) / 2, stop - passband,
                           window.WIN_HAMMING)


def channel_taps(mid_rate, up, bandwidth_hz):
    """The channel filter, designed at the resampler's upsampled rate."""
    cutoff = min(max(bandwidth_hz / 2.0, 20e3), CHANNEL_MAX_BW / 2)
    transition = min(15e3, CHANNEL_RATE / 2 - cutoff + 5e3)
    return firdes.low_pass(float(up), mid_rate * up, cutoff, transition,
                           window.WIN_HAMMING)


def mpx_taps():
    """Low-pass for the discriminator's output, 500 -> 250 kS/s: passes the
    whole multiplex (to 100 kHz, SCA included), stops by 125 kHz."""
    return firdes.low_pass(1.0, CHANNEL_RATE, 110e3, 30e3, window.WIN_HAMMING)


def pilot_taps(ntaps=1601, half_width=800.0):
    """A complex band-pass around 19 kHz with zero phase at 19 kHz - see the
    module notes. Odd length, so its delay is a whole number of samples."""
    lp = sps.firwin(ntaps, half_width, fs=MPX_RATE)
    n = np.arange(ntaps)
    return (lp * np.exp(2j * np.pi * PILOT_HZ / MPX_RATE * n)).astype(np.complex64).tolist()


def guard_taps(ntaps=1601, inner=1000.0, outer=3000.0):
    """A complex band-pass either side of the pilot, 16-18 and 20-22 kHz:
    the guard band, where nothing is broadcast, so what comes through is the
    noise the pilot has to stand out from. Symmetric about 19 kHz, so the
    discriminator's noise rising with frequency evens out."""
    band = sps.firwin(ntaps, outer, fs=MPX_RATE) - sps.firwin(ntaps, inner, fs=MPX_RATE)
    n = np.arange(ntaps)
    return (band * np.exp(2j * np.pi * PILOT_HZ / MPX_RATE * n)).astype(np.complex64).tolist()


def noise_bandwidth(taps):
    """What a filter passes of flat noise, as a share of the rate."""
    return float(np.sum(np.abs(np.asarray(taps)) ** 2))


def audio_taps():
    """Low-pass to 15 kHz at the resampler's 6 MHz internal rate: takes out
    the pilot, the subcarrier and RDS, and does the anti-alias for 48 kHz."""
    return firdes.low_pass(float(AUDIO_INTERP), MPX_RATE * AUDIO_INTERP,
                           15.5e3, 3e3, window.WIN_HAMMING)


def deemphasis_taps(tau, fs=AUDIO_RATE):
    """GNU Radio's own ``fm_deemph`` design (bilinear, pre-warped), as taps
    so the region can change on a running filter."""
    w_c = 1.0 / tau
    w_ca = 2.0 * fs * math.tan(w_c / (2.0 * fs))
    k = -w_ca / (2.0 * fs)
    p1 = (1.0 + k) / (1.0 - k)
    b0 = -k / (1.0 - k)
    return [b0, b0], [1.0, -p1]


# ----------------------------------------------------------- python blocks

class rds_sink(gr.sync_block):
    """MPX and the pilot reference in, decoded RDS out through snapshot().
    From the RF bench toolkit's receiver, unchanged in substance."""

    def __init__(self, mpx_rate, region):
        gr.sync_block.__init__(self, name='rds_sink',
                               in_sig=[np.float32, np.complex64], out_sig=None)
        self.demod = RdsDemod(mpx_rate)
        self.proto = RdsProtocol(region=region)
        self._lock = threading.Lock()
        self._discard_before = 0

    def discard_until(self, item):
        """Ignore MPX before sample ``item`` (counted from the start): what
        was still queued from the station before a retune."""
        with self._lock:
            self._discard_before = int(item)

    def work(self, input_items, output_items):
        n = min(len(input_items[0]), len(input_items[1]))
        with self._lock:
            try:
                skip = min(n, max(0, self._discard_before - self.nitems_read(0)))
                if skip < n:
                    bits = self.demod.feed(input_items[0][skip:n], input_items[1][skip:n])
                    if len(bits):
                        self.proto.feed(bits)
            except Exception as exc:
                print(f"rds_sink: {exc}")
        return n

    def reset(self, mpx_rate, region):
        with self._lock:
            self.demod = RdsDemod(mpx_rate)
            self.proto = RdsProtocol(region=region)

    def snapshot(self):
        with self._lock:
            return self.proto.snapshot()


class vector_probe(gr.sync_block):
    """The latest power spectrum, averaged frame to frame, for the window.

    ``alpha`` 1.0 shows each frame as it comes; smaller averages over about
    1/alpha frames. ``scale`` turns FFT power into full-scale units.

    ``period`` is how many upstream samples each frame stands for: frame
    ``j`` is the FFT of samples ``j * period`` onwards, since the spectrum
    tap keeps one frame in every ``period`` samples. That is what lets
    :meth:`discard_until` drop exactly the frames made before a retune.
    """

    def __init__(self, size, scale=1.0, alpha=0.5, period=None):
        gr.sync_block.__init__(self, name='vector_probe',
                               in_sig=[(np.float32, size)], out_sig=None)
        self.size = size
        self.period = int(period or size)
        self.scale = float(scale)
        self.alpha = float(alpha)
        self._avg = None
        self._discard_before = 0
        self.frames = 0
        self._lock = threading.Lock()

    def set_alpha(self, alpha):
        self.alpha = min(max(float(alpha), 0.01), 1.0)

    def reset(self):
        with self._lock:
            self._avg = None

    def discard_until(self, item):
        """Start the average again, and drop every frame made from upstream
        samples before ``item`` (counted from the flowgraph's start)."""
        with self._lock:
            self._avg = None
            self._discard_before = int(math.ceil(item / self.period))

    def discard_frames(self, count):
        """Start again and drop the next ``count`` frames - for a source
        that cannot say how many samples it has made."""
        with self._lock:
            self._avg = None
            self._discard_before = self.nitems_read(0) + int(count)

    def work(self, input_items, output_items):
        frames = input_items[0]
        if len(frames):
            with self._lock:
                a = self.alpha
                skip = min(len(frames), max(0, self._discard_before - self.nitems_read(0)))
                for frame in frames[skip:]:
                    if self._avg is None:
                        self._avg = frame.astype(np.float64) * self.scale
                    else:
                        self._avg += a * (frame * self.scale - self._avg)
                self.frames += len(frames)
        return len(frames)

    def snapshot(self):
        with self._lock:
            return None if self._avg is None else self._avg.copy()


class clip_probe(gr.sync_block):
    """How much of the radio's IQ sits at full scale (``sweep.FULL_SCALE``,
    I or Q) since it was last asked. A HackRF's 8-bit samples clip there,
    and it has no overload flag of its own, so this is how the window knows
    the gain is too high. It is fed a few thousand samples at a time, so it
    costs little at any rate."""

    def __init__(self):
        gr.sync_block.__init__(self, name='clip_probe',
                               in_sig=[np.complex64], out_sig=None)
        self._lock = threading.Lock()
        self._n = 0
        self._hit = 0

    def work(self, input_items, output_items):
        x = input_items[0]
        if len(x):
            hit = full_scale_count(x)
            with self._lock:
                self._n += len(x)
                self._hit += hit
        return len(x)

    def take(self):
        """(samples at full scale, samples seen) since the last call."""
        with self._lock:
            out = self._hit, self._n
            self._n = self._hit = 0
        return out


class data_clock(gr.sync_block):
    """When samples last came out of the radio. A radio that stops sending
    (unplugged, its network gone) raises no error in the flowgraph: the
    stream just stops, and the window would sit on the last picture, so
    the engine watches this instead (:meth:`Engine.data_age`)."""

    def __init__(self):
        gr.sync_block.__init__(self, name='data_clock',
                               in_sig=[np.complex64], out_sig=None)
        self.last = None

    def work(self, input_items, output_items):
        n = len(input_items[0])
        if n:
            self.last = time.monotonic()
        return n


class stereo_phase_probe(gr.sync_block):
    """Where the L-R signal sits relative to twice the pilot's phase.

    Input is the MPX mixed down by the squared pilot and low-passed, so the
    DSB-SC side signal arrives as ``side * exp(j phase)``: a line through the
    origin. The axis of that line - half the angle of the mean of z squared -
    is the phase (to within 180 degrees), and how line-like it is says
    whether there is any L-R to measure. Averaged over about ``tau`` seconds.
    """

    def __init__(self, rate, tau=1.5):
        gr.sync_block.__init__(self, name='stereo_phase_probe',
                               in_sig=[np.complex64], out_sig=None)
        self.rate = float(rate)
        self.tau = float(tau)
        self._z2 = 0j
        self._p = 0.0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        z = input_items[0].astype(np.complex128)
        n = len(z)
        if n:
            decay = math.exp(-n / (self.tau * self.rate))
            with self._lock:
                self._z2 = self._z2 * decay + np.sum(z * z)
                self._p = self._p * decay + float(np.sum(z.real ** 2 + z.imag ** 2))
        return n

    def reset(self):
        with self._lock:
            self._z2, self._p = 0j, 0.0

    def estimate(self):
        """(axis in radians, coherence 0-1, mean side power)."""
        with self._lock:
            z2, p = self._z2, self._p
        if p <= 0:
            return 0.0, 0.0, 0.0
        mean_power = p * (1 - math.exp(-1 / (self.tau * self.rate)))
        return cmath.phase(z2) / 2, abs(z2) / p, mean_power


def choose_stereo_phase(axis, coherence, current, min_coherence=0.6):
    """The 38 kHz phase to demodulate with, from the measured axis.

    The axis is known only to within 180 degrees - which way round is left
    and right. That is settled by convention: an axis nearer vertical is the
    standard (sine) pilot, taken at +90; nearer horizontal is the cosine
    pair, taken at 0. Within either, the measured axis itself is used, so a
    transmitter slightly off either convention still separates well. With
    no L-R to measure (mono programme, noise) the current choice stands.
    """
    if coherence < min_coherence:
        return current
    if abs(axis) > math.pi / 4:                     # nearer +-90: standard
        return axis if axis > 0 else axis + math.pi
    return axis                                     # nearer 0: cosine pair


class audio_tap(gr.sync_block):
    """Left and right at 48 kHz: meters, and the WAV recorder.

    Peak and mean-square since the window last looked, per channel; and when
    a :class:`recording.WavWriter` is attached, a copy of every sample.
    """

    def __init__(self):
        gr.sync_block.__init__(self, name='audio_tap',
                               in_sig=[np.float32, np.float32], out_sig=None)
        self._lock = threading.Lock()
        self._peak = [0.0, 0.0]
        self._sq = [0.0, 0.0]
        self._count = 0
        self.writer = None

    def work(self, input_items, output_items):
        left, right = input_items[0], input_items[1]
        n = min(len(left), len(right))
        if n:
            left, right = left[:n], right[:n]
            with self._lock:
                self._peak[0] = max(self._peak[0], float(np.max(np.abs(left))))
                self._peak[1] = max(self._peak[1], float(np.max(np.abs(right))))
                self._sq[0] += float(np.dot(left, left))
                self._sq[1] += float(np.dot(right, right))
                self._count += n
                writer = self.writer
            if writer is not None:
                writer.put(left, right)
        return n

    def set_writer(self, writer):
        with self._lock:
            self.writer = writer

    def levels(self):
        """((peak L, peak R), (rms L, rms R)) since the last call."""
        with self._lock:
            peak = tuple(self._peak)
            count = max(self._count, 1)
            rms = tuple(math.sqrt(s / count) for s in self._sq)
            self._peak = [0.0, 0.0]
            self._sq = [0.0, 0.0]
            self._count = 0
        return peak, rms


# ------------------------------------------------------------ the chain

def spectrum_frames_per_s(rate, size, target=30.0):
    return max(1, int(round(rate / (size * target))))


def spectrum_tap(tb, keep, upstream, rate, size, complex_input):
    """A :class:`vector_probe` of ``upstream``'s power spectrum, about 30
    frames a second. Its blocks go into ``keep``, to stay referenced."""
    item = gr.sizeof_gr_complex if complex_input else gr.sizeof_float
    period = size * spectrum_frames_per_s(rate, size)
    thin = blocks.keep_m_in_n(item, size, period, 0)
    s2v = blocks.stream_to_vector(item, size)
    win = window.blackmanharris(size)
    if complex_input:
        ft = fft.fft_vcc(size, True, win, True, 1)
    else:
        ft = fft.fft_vfc(size, True, win, False, 1)
    mag = blocks.complex_to_mag_squared(size)
    probe = vector_probe(size, scale=1.0 / (float(np.sum(win)) ** 2),
                         period=period)
    tb.connect(upstream, thin, s2v, ft, mag, probe)
    keep.extend((thin, s2v, ft, mag))
    return probe


class ReceiveChain:
    """Builds and connects the receive chain into top block ``tb``.

    Not a hier block: its blocks go straight into the top block, and every
    one is kept here, Python blocks included - one collected while the
    flowgraph runs is a segfault with no Python frame (an RF bench toolkit
    rule). ``audio_sink`` is the sound card's block, or None for none.
    """

    RF_FFT = 4096
    MPX_FFT = 2048
    #: Samples dropped after a retune on top of the counted backlog, as the
    #: sweep's GUARD: RF at the radio's rate, and MPX - the buffers from the
    #: channelizer through the discriminator and its decimator hold about
    #: 14k samples' worth at 250 kS/s.
    RF_GUARD = 65536
    MPX_GUARD = 24576

    def __init__(self, tb, source, rate, offset_hz, *, channel_bw=200e3,
                 region='RBDS', stereo=True, volume=0.5, muted=False,
                 audio_sink=None, rf_fft=None):
        self.tb = tb
        self.rate = float(rate)
        self.region = region
        self.stereo_enabled = bool(stereo)
        self.stereo_phase = PHASE_STANDARD
        self._blend = 0.0
        self.volume = float(volume)
        self.muted = bool(muted)
        self.channel_bw = float(channel_bw)
        self.rf_fft = int(rf_fft or self.RF_FFT)
        self._keep = []                  # blocks with no other attribute

        d1, mid, up, down = plan_channelizer(self.rate)
        self.mid_rate, self.up = mid, up
        self.stage1 = filter.freq_xlating_fir_filter_ccf(
            d1, stage1_taps(self.rate, mid), offset_hz, self.rate)
        self.stage2 = filter.rational_resampler_ccf(
            up, down, channel_taps(mid, up, self.channel_bw))
        self.channel_rate = CHANNEL_RATE
        self.demod = analog.quadrature_demod_cf(
            CHANNEL_RATE / (2 * math.pi * MAX_DEVIATION))
        # ``mpx`` is the multiplex at 250 kS/s: everything below takes it.
        decim = int(round(CHANNEL_RATE / MPX_RATE))
        self.mpx = filter.fir_filter_fff(decim, mpx_taps())
        tb.connect(source, self.stage1, self.stage2, self.demod, self.mpx)

        # Channel power, for the signal readout.
        self.ch_mag = blocks.complex_to_mag_squared(1)
        self.ch_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.ch_probe = blocks.probe_signal_f()
        tb.connect(self.stage2, self.ch_mag, self.ch_avg, self.ch_probe)

        # Pilot, PLL, RDS.
        self.mpx_c = blocks.float_to_complex(1)
        self.pilot_bpf = filter.fft_filter_ccc(1, pilot_taps())
        self.pll = analog.pll_refout_cc(0.001, 2 * math.pi * 19.2e3 / MPX_RATE,
                                        2 * math.pi * 18.8e3 / MPX_RATE)
        self.rds = rds_sink(MPX_RATE, region)
        tb.connect(self.mpx, self.mpx_c, self.pilot_bpf, self.pll)
        tb.connect(self.mpx, (self.rds, 0))
        tb.connect(self.pll, (self.rds, 1))
        self.pilot_mag = blocks.complex_to_mag_squared(1)
        self.pilot_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.pilot_probe = blocks.probe_signal_f()
        tb.connect(self.pilot_bpf, self.pilot_mag, self.pilot_avg,
                   self.pilot_probe)
        # The guard band beside the pilot: the noise it must stand out from.
        taps = guard_taps()
        self.guard_bpf = filter.fft_filter_ccc(1, taps)
        self.guard_mag = blocks.complex_to_mag_squared(1)
        self.guard_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.guard_probe = blocks.probe_signal_f()
        tb.connect(self.mpx_c, self.guard_bpf, self.guard_mag, self.guard_avg,
                   self.guard_probe)
        # Scales the guard's noise to the pilot band's width.
        self._guard_to_pilot = noise_bandwidth(pilot_taps()) / noise_bandwidth(taps)
        self._pilot_on = False

        # 38 kHz from the pilot, at the phase in use; and the probe that
        # finds that phase.
        self.sq38 = blocks.multiply_cc(1)
        tb.connect(self.pll, (self.sq38, 0))
        tb.connect(self.pll, (self.sq38, 1))
        self.rot38 = blocks.multiply_const_cc(cmath.exp(1j * self.stereo_phase))
        self.car38 = blocks.complex_to_real(1)
        self.side_mix = blocks.multiply_ff(1)
        tb.connect(self.sq38, self.rot38, self.car38, (self.side_mix, 1))
        tb.connect(self.mpx, (self.side_mix, 0))

        self.est_conj = blocks.conjugate_cc()
        self.est_mix = blocks.multiply_cc(1)
        self.est_lpf = filter.fir_filter_ccf(
            10, firdes.low_pass(1.0, MPX_RATE, 12e3, 4e3, window.WIN_HAMMING))
        self.est_probe = stereo_phase_probe(MPX_RATE / 10)
        tb.connect(self.sq38, self.est_conj, (self.est_mix, 1))
        tb.connect(self.mpx_c, (self.est_mix, 0))
        tb.connect(self.est_mix, self.est_lpf, self.est_probe)

        # Mid and side to 48 kHz, de-emphasis, matrix.
        taps = audio_taps()
        self.mid_rs = filter.rational_resampler_fff(AUDIO_INTERP, AUDIO_DECIM, taps)
        self.side_rs = filter.rational_resampler_fff(AUDIO_INTERP, AUDIO_DECIM, taps)
        self.side_gain = blocks.multiply_const_ff(0.0)
        b, a = deemphasis_taps(DEEMPHASIS.get(region, 75e-6))
        self.mid_de = filter.iir_filter_ffd(b, a, False)
        self.side_de = filter.iir_filter_ffd(b, a, False)
        self.left = blocks.add_ff(1)
        self.right = blocks.sub_ff(1)
        tb.connect(self.mpx, self.mid_rs, self.mid_de)
        tb.connect(self.side_mix, self.side_rs, self.side_gain, self.side_de)
        tb.connect(self.mid_de, (self.left, 0))
        tb.connect(self.side_de, (self.left, 1))
        tb.connect(self.mid_de, (self.right, 0))
        tb.connect(self.side_de, (self.right, 1))

        self.tap = audio_tap()
        tb.connect(self.left, (self.tap, 0))
        tb.connect(self.right, (self.tap, 1))

        self.vol_l = blocks.multiply_const_ff(self._gain())
        self.vol_r = blocks.multiply_const_ff(self._gain())
        tb.connect(self.left, self.vol_l)
        tb.connect(self.right, self.vol_r)
        if audio_sink is not None:
            self.audio_sink = audio_sink
            tb.connect(self.vol_l, (audio_sink, 0))
            tb.connect(self.vol_r, (audio_sink, 1))
        else:
            self.audio_sink = None
            self.null_l = blocks.null_sink(gr.sizeof_float)
            self.null_r = blocks.null_sink(gr.sizeof_float)
            tb.connect(self.vol_l, self.null_l)
            tb.connect(self.vol_r, self.null_r)

        # Spectra.
        self.rf_probe = self._spectrum_tap(source, self.rate, self.rf_fft,
                                           complex_input=True)
        # Clipping, sampled as the RF spectrum is.
        size = self.rf_fft
        self.clip_keep = blocks.keep_m_in_n(gr.sizeof_gr_complex, size,
                                            size * spectrum_frames_per_s(self.rate, size), 0)
        self.clip = clip_probe()
        tb.connect(source, self.clip_keep, self.clip)
        self.mpx_probe = self._spectrum_tap(self.mpx, MPX_RATE, self.MPX_FFT,
                                            complex_input=False)

        # Recorders, always connected, normally closed.
        self.band_sink = self._closed_file_sink()
        self.channel_sink = self._closed_file_sink()
        tb.connect(source, self.band_sink)
        tb.connect(self.stage2, self.channel_sink)

    # -- building helpers
    def _spectrum_tap(self, upstream, rate, size, complex_input):
        return spectrum_tap(self.tb, self._keep, upstream, rate, size, complex_input)

    @staticmethod
    def _closed_file_sink():
        sink = blocks.file_sink(gr.sizeof_gr_complex, os.devnull, False)
        sink.close()
        sink.set_unbuffered(False)
        return sink

    # -- live settings
    def _gain(self):
        # Square law: the slider's middle sounds like the middle.
        return 0.0 if self.muted else 1.5 * self.volume ** 2

    def set_volume(self, volume):
        self.volume = min(max(float(volume), 0.0), 1.0)
        self.vol_l.set_k(self._gain())
        self.vol_r.set_k(self._gain())

    def set_muted(self, muted):
        self.muted = bool(muted)
        self.vol_l.set_k(self._gain())
        self.vol_r.set_k(self._gain())

    def set_offset(self, offset_hz):
        self.stage1.set_center_freq(float(offset_hz))
        self.reset_decoders()

    def set_channel_bw(self, bandwidth_hz):
        self.channel_bw = float(bandwidth_hz)
        self.stage2.set_taps(channel_taps(self.mid_rate, self.up, self.channel_bw))

    def set_region(self, region):
        self.region = region
        b, a = deemphasis_taps(DEEMPHASIS.get(region, 75e-6))
        self.mid_de.set_taps(b, a)
        self.side_de.set_taps(b, a)
        self.rds.reset(MPX_RATE, region)

    def set_stereo_enabled(self, enabled):
        self.stereo_enabled = bool(enabled)

    def reset_decoders(self):
        self.rds.reset(MPX_RATE, self.region)
        self._pilot_on = False                 # a new station earns its stereo
        self.est_probe.reset()
        self.rf_probe.reset()
        self.mpx_probe.reset()

    def discard_stale(self, source, lo_moved, settle_s):
        """After a retune, drop what is still queued from before it, so the
        spectra (and so peak hold) and RDS show nothing of the last station.

        The multiplex's output counter says how much MPX came from the
        old channel; ``MPX_GUARD`` covers what the buffers ahead of it still
        hold. When the LO moved, the radio's own queue (its counter, less
        what the channelizer has read) and its settling time are stale too,
        and the RF spectrum with them. ``source`` is the radio block."""
        ratio = MPX_RATE / self.rate
        try:
            stale_rf = 0
            if lo_moved:
                written = source.nitems_written(0)
                self.rf_probe.discard_until(written + settle_s * self.rate + self.RF_GUARD)
                stale_rf = max(0, written - self.stage1.nitems_read(0)) + settle_s * self.rate
            mpx = self.mpx.nitems_written(0) + self.MPX_GUARD + stale_rf * ratio
        except Exception:
            # A source with no counter (a hier block: IQ file playback, whose
            # LO never moves): fall back to a few frames' worth.
            if lo_moved:
                self.rf_probe.discard_frames(3)
            mpx = self.mpx.nitems_written(0) + 4 * self.MPX_GUARD
        self.mpx_probe.discard_until(mpx)
        self.rds.discard_until(mpx)

    def restarted(self):
        """The flowgraph is about to start again as it stood (a paused
        playback resuming). Every block's counters start again from zero,
        so a discard still pending, counted on the old ones, would drop
        the next minutes: clear them."""
        for probe in (self.rf_probe, self.mpx_probe):
            probe.discard_until(0)
        self.rds.discard_until(0)

    # -- polled by the window
    def pilot_level(self):
        return self.pilot_probe.level()

    def pilot_snr_db(self):
        """How far the 19 kHz band stands over the noise beside it, in the
        same bandwidth: 0 dB is noise alone."""
        pilot = self.pilot_probe.level()
        noise = self.guard_probe.level() * self._guard_to_pilot
        if pilot <= 0:
            return -200.0
        if noise <= 0:
            return 200.0                       # a clean signal, no noise at all
        return 10 * math.log10(pilot / noise)

    def pilot_locked(self):
        """A pilot at 2% or more (a 9% one reads ~2e-3 here, 1e-4 is about
        2%) standing out from the noise beside it - see the module notes."""
        need = PILOT_UNLOCK_DB if self._pilot_on else PILOT_LOCK_DB
        self._pilot_on = self.pilot_level() > 1e-4 and self.pilot_snr_db() > need
        return self._pilot_on

    def channel_power_db(self):
        level = self.ch_probe.level()
        return 10 * math.log10(level) if level > 0 else -200.0

    def update_stereo(self):
        """Run from the window's timer: follow the 38 kHz phase, and blend to
        mono when there is no pilot or stereo is switched off. Returns
        (stereo on, phase used, coherence)."""
        axis, coherence, _ = self.est_probe.estimate()
        phase = choose_stereo_phase(axis, coherence, self.stereo_phase)
        if abs(phase - self.stereo_phase) > math.radians(2):
            self.stereo_phase = phase
            self.rot38.set_k(cmath.exp(1j * phase))
        blend = 1.0 if (self.stereo_enabled and self.pilot_locked()) else 0.0
        if blend != self._blend:
            self._blend = blend
            self.side_gain.set_k(2.0 * blend)
        return blend > 0, self.stereo_phase, coherence


# ------------------------------------------------------- WAV playback

class wav_source(gr.sync_block):
    """A WAV recording as left and right, read from its memory-mapped
    frames (``(n, channels)``, int16 or float32): it can jump to any frame,
    loop, pause, and says when it has reached the end.

    Paused or finished it sends silence rather than nothing, so the sound
    card keeps running and never underruns; the window stops drawing.
    ``position`` is the next frame to be read - ahead of the speaker by
    what the buffers hold, which :class:`WavChain` keeps small.
    """

    def __init__(self, frames, loop=False):
        gr.sync_block.__init__(self, name='wav_source', in_sig=None,
                               out_sig=[np.float32, np.float32])
        self.frames = frames
        self.total = len(frames)
        self.scale = 1.0 / 32768.0 if frames.dtype == np.int16 else 1.0
        self.loop = bool(loop)
        self.paused = False
        self.finished = False
        self.position = 0
        self._lock = threading.Lock()

    def seek(self, frame):
        with self._lock:
            self.position = int(min(max(frame, 0), max(0, self.total - 1)))
            self.finished = False

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)

    def work(self, input_items, output_items):
        left, right = output_items[0], output_items[1]
        n = len(left)
        with self._lock:
            if self.paused or self.finished or not self.total:
                left[:] = 0.0
                right[:] = 0.0
                return n
            take = min(n, self.total - self.position)
            chunk = self.frames[self.position:self.position + take]
            left[:take] = chunk[:, 0] * self.scale
            right[:take] = chunk[:, -1] * self.scale      # mono: both sides
            self.position += take
            if self.position >= self.total:
                if self.loop:
                    self.position = 0
                else:
                    self.finished = True
        # A short read at the loop point: the next call goes on from the
        # start, with no gap. At the end, the rest is silence.
        if take < n and self.finished:
            left[take:] = 0.0
            right[take:] = 0.0
            return n
        return take


class WavChain:
    """Plays a :class:`wav_source` to the sound card, with the same audio
    tap (meters) and volume law as the receive chain, and the spectrum of
    left plus right for the window.

    The sound card sets the pace, and the source runs ahead of it by what
    the buffers between them hold: GNU Radio's default, 8192 samples a
    buffer, would put the spectrum a third of a second ahead of the sound,
    so the audio path's buffers are held to about 20 ms each. With no sound
    card, a throttle on each side sets the pace instead.
    """

    FFT = 2048
    BUFFER = 1024

    def __init__(self, tb, source, *, volume=0.5, muted=False, audio_sink=None):
        self.tb = tb
        self.source = source
        self.rate = AUDIO_RATE
        self.volume = float(volume)
        self.muted = bool(muted)
        self._keep = []

        self.tap = audio_tap()
        tb.connect((source, 0), (self.tap, 0))
        tb.connect((source, 1), (self.tap, 1))
        self.mid = blocks.add_ff(1)
        self.half = blocks.multiply_const_ff(0.5)
        tb.connect((source, 0), (self.mid, 0))
        tb.connect((source, 1), (self.mid, 1))
        tb.connect(self.mid, self.half)
        self.probe = spectrum_tap(tb, self._keep, self.half, AUDIO_RATE, self.FFT,
                                  complex_input=False)

        self.vol_l = blocks.multiply_const_ff(self._gain())
        self.vol_r = blocks.multiply_const_ff(self._gain())
        tb.connect((source, 0), self.vol_l)
        tb.connect((source, 1), self.vol_r)
        for block in (source, self.vol_l, self.vol_r):
            block.set_max_output_buffer(self.BUFFER)
        if audio_sink is not None:
            self.audio_sink = audio_sink
            tb.connect(self.vol_l, (audio_sink, 0))
            tb.connect(self.vol_r, (audio_sink, 1))
        else:
            self.audio_sink = None
            self.pace_l = blocks.throttle(gr.sizeof_float, AUDIO_RATE, True)
            self.pace_r = blocks.throttle(gr.sizeof_float, AUDIO_RATE, True)
            self.null_l = blocks.null_sink(gr.sizeof_float)
            self.null_r = blocks.null_sink(gr.sizeof_float)
            tb.connect(self.vol_l, self.pace_l, self.null_l)
            tb.connect(self.vol_r, self.pace_r, self.null_r)

    def _gain(self):
        return 0.0 if self.muted else 1.5 * self.volume ** 2

    def set_volume(self, volume):
        self.volume = min(max(float(volume), 0.0), 1.0)
        self.vol_l.set_k(self._gain())
        self.vol_r.set_k(self._gain())

    def set_muted(self, muted):
        self.muted = bool(muted)
        self.vol_l.set_k(self._gain())
        self.vol_r.set_k(self._gain())

    def seeked(self):
        """The source jumped: start the spectrum's average again."""
        self.probe.reset()
