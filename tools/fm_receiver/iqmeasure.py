"""Measuring a signal from an IQ recording: what it is, as numbers and a class.

Behind ``tools/iq-measure`` and ``fmctl measure``. It reads a recording of IQ
(a ``.cfile`` with its ``.sigmf-meta``, a bare ``.cfile`` given its rate, the same
bytes as ``.cf32`` or SigMF's cf32_le ``.sigmf-data``, a
Sceptre DVR's IQ ``.sdvr``, or a BLUE ``.cdif``) a span at a time, never
whole, and returns a card: a JSON-safe dict of the level, the occupied
bandwidth, the tones, the bursts, the envelope and frequency behaviour, the
symbol rate and pulse shape, the LTE cells and 5G NR sync blocks in a wideband
span, ATSC, analog TV, a pulse train, PSK and an FM signal's subcarriers, the lines
beside the signal (each an image of its mirror or not), an AM or FM programme's audio
(its pure lines, keyed or steady, DTMF digits and its repeat), warnings where the span is
too short to see a repeat, and a class
('noise', 'lte', 'nr', 'lte+nr', 'atsc', 'tv', 'pulses', 'carrier', 'ook',
'ask', 'psk', 'fsk', 'fm', 'am' or 'unknown') with the evidence for it. The class rules are fixed
(CONTRACTS-measure.md), so the same recording always gives the same card.
Numpy only, so it runs anywhere; it writes nothing but the card, when asked.

Two spectra are taken. Tones and the noise floor come from a 1,024-bin
Blackman-Harris periodogram (a bin is rate / 1024; its very low side lobes
keep a strong tone's leakage out of the tone list). Bandwidth and the
centroid come from a finer Hann periodogram, so a narrow signal is not
widened to a whole bin. The envelope and the frequency are tracked in blocks
of 16 samples, from the phase advance summed over each block, so the noise
averages down; the 64-sample track gives the frequency's spread without the
block-to-block noise. TV, pulses, PSK, subcarriers and keying of 4 or 8
levels are judged sample by sample on the start of that track (SEG samples
at its own rate).

The cells are looked for only in a span sampled at 1.92 MS/s or more whose
occupied width is 1 MHz or more, and only in its first 50 ms (``iqlte`` and
``iqnr``, both numpy only): every LTE and NR cell found in recordings so far
was found in 50 ms as in 200 ms, at a third of the memory. An LTE cell is
listed only when its reference signals (CRS) confirm its PCI. LTE is looked for
at the station the recording was tuned to (the Receive tab records the band
300 kHz off it), else at the occupied centre on the 100 kHz raster; NR's
search walks the sync raster itself.
"""

import json
import math
import os
from typing import NamedTuple

import numpy as np

from . import sceptre_blue, sceptre_dvr

SCHEMA = 2
MIN_SAMPLES = 1024
MAX_SPAN_S = 30.0
CARD_SUFFIX = '.card.json'
#: Samples read at a time (a span is never held whole).
CHUNK = 1 << 16
#: Samples per envelope and frequency block.
BLOCK = 16
#: Symbols faster than a quarter of the block rate (62.5 kblocks/s at 1 MS/s) blur in 16-sample
#: blocks: a 20 kBd FSK read 12.5 kBd, a 50 kBd one FM. Such a card is taken again in blocks
#: of a quarter symbol, BLOCK_MIN samples or more (see _analyse).
BLOCK_MIN = 2
BLOCKS_PER_SYMBOL = 4
COARSE_NFFT = 1024
FINE_NFFT = 16384
#: Padding either side of a block for the frequency-domain band filter: at least this.
PAD = 4096
#: The filter's ringing lasts about rate / band width samples; this many of them are kept.
RING_LENGTHS = 3.0
#: The most padding either side of a block: a narrower band is filtered with this.
MAX_PAD = 1 << 20
#: The transform length of the band filter's blocks (a power of two) is at least this,
#: and at least four times the padding; it is never more than MAX_BLOCK, the most a
#: read or a transform takes at once.
FILTER_BLOCK = 1 << 15
MAX_BLOCK = 1 << 22
#: Bins of raised-cosine skirt outside the passband. Measured, not assumed:
#: on the hard-cut filter a CW leaves one 16-sample frequency block per 877
#: outputs (1140 Hz) off the others. At +100 kHz and 19 dB that offset is
#: about 83 Hz and, from half a second up, still one level (deviation 27 Hz).
#: At +64750 Hz the same splice widens a 1 s record's deviation to 143 Hz.
#: PAD=16384 instead of 4096 brings that 143 Hz back to 22 Hz, and the same
#: pad on c8ae476 also clears the 50 ms +100 kHz misclassification. The mixer
#: term ``(-fc * (s - pad) / rate) % 1`` at s=2.4e8 is 2e-8 deg off in
#: float64, so it is not the splice. Four skirt bins bring the +64750 Hz
#: deviation to 63 Hz. Six or eight bins key a clean +50 kHz tone
#: (envelope or a second frequency level), so the skirt stays at four.
SKIRT_BINS = 4
TONE_DB = 15.0
#: A tone more than this far under the strongest is a window's sidelobe, not a tone.
TONE_RANGE_DB = 60.0
#: The band the envelope and frequency are taken in: 3 x the occupied width, at least this.
TRACK_WIDTH = 3.0
TRACK_MIN_HZ = 20e3
#: The envelope and frequency are tracked at no less than this rate, so a block of
#: samples lasts no longer than at 1 MS/s and a symbol clock is still resolved.
TRACK_RATE = 1e6
MAX_TONES = 8
TONE_GAP = 8
LISTED = 32
KEYED_SIGMA = 3.0
MIDDLE_MAX = 0.12
#: An envelope is keyed only if its levels are this far apart, as (high - low) /
#: (high + low): a clean constant envelope's ripple splits cleanly too (a bench FM with a
#: 92 kHz subcarrier, read in 2-sample blocks, gave levels 0.99992 and 0.99999, a depth of
#: 4e-5). Ripple deeper than this (a wide FM cut by the decimator) still passes.
KEYED_DEPTH = 1e-3
MIN_CLUSTER = 0.005
STEADY_HZ = 500.0
#: The cells are looked for only at this rate and occupied width or more, in this much of the span.
CELLS_MIN_RATE = 1.92e6
CELLS_MIN_WIDTH_HZ = 1e6
CELLS_SPAN_S = 0.05
#: LTE's channel raster: a carrier centre is a multiple of this.
LTE_RASTER_HZ = 100e3
#: The sample-level checks (tv, pulses, psk, subcarriers) read this many samples of the
#: second pass, at its own rate: a second at 1 MS/s, 26 ms at 40 MS/s.
SEG = 1 << 20
#: Analog TV: the line rates of 525-line (NTSC M) and 625-line television, how far off a
#: line may be, the narrowest TV signal, and how far a line (and its second harmonic) must
#: stand over the median of the spectrum around it. On real NTSC (AM) and FM video the
#: stronger track's line stood 48 to 51 dB over; on every other bench signal 100 kHz wide
#: or more, 15 dB or less (an AM tone's envelope, 2 kHz wide, reached 34 dB). AM vision's
#: 99 % width is mostly its carrier: 284 kHz for a synthetic 525-line picture.
TV_LINE_HZ = {'525-line': 15734.264, '625-line': 15625.0}
TV_TOLERANCE = 0.005
TV_MIN_WIDTH_HZ = 100e3
TV_LINE_DB = 36.0
#: ATSC (8VSB): a block this wide whose strongest tone, the pilot, sits within ATSC_EDGE of
#: its width from one edge (310 kHz above the lower edge of the 6 MHz channel; at the upper
#: edge the spectrum is inverted). Off air, the block was 5.51 MHz wide (99 %) and the pilot
#: 17 kHz from its lower edge, the strongest of its tones by 4.6 dB.
ATSC_WIDTH_HZ = (4.8e6, 6.2e6)
ATSC_EDGE = 0.1
#: FM video's swing is read with the frequency averaged to these bands (see _video_swing).
VIDEO_BAND_HZ = 5e6
VIDEO_LOW_HZ = 0.3e6
VIDEO_PRE_RATIO = 1.4
#: Pulses: a train of short pulses, at this duty or less, at least this many, and this
#: share of the starts no more than a quarter of the median spacing off it. A pulse moves
#: (PPM) when its start strays from the fitted clock by more than this share of the period
#: (rms) and more than two samples: real PPM strayed 4 samples rms in 52 ms at 20 MS/s
#: (period 500), where a fixed clock's starts stray by their rounding, 0.3 of a sample.
PULSE_DUTY = 0.25
PULSE_MIN = 20
PULSE_REGULAR = 0.9
PULSE_MOVES = 0.002
#: A train runs through the whole segment: no gap over PULSE_GAP periods, and its first and
#: last starts within that of the segment's ends. A sensor's burst of PWM or PPM bits, with
#: silence around it, is OOK, not a train. Widths that spread by more than PULSE_WIDTHS of
#: their median (10th to 90th percentile) are width-modulated.
PULSE_GAP = 4
PULSE_WIDTHS = 0.3
#: PSK: the M-th power of PSK of order M is a line (the data's phases removed) while the
#: signal itself has none (no carrier). Coherence is the strongest bin of x^M against the
#: sum of |x|^M: 1 for a pure tone, near 0 for noise. Real QPSK gave 0.012 at M=1 and
#: 0.52 at M=4; FM, FSK, AM, OOK and TV gave 0.1 or more at M=1, or under 0.06 at every M.
PSK_ORDERS = (2, 4, 8)
PSK_LINE = 0.3
PSK_NO_CARRIER = 0.1
#: PSK's envelope varies sample to sample (filtered: CV 0.28 to 0.30; the 16-sample blocks
#: smooth it under 0.1). With unfiltered symbols the envelope is constant, as FM's is, and
#: the frequency is spikes at the symbol changes: its kurtosis is high (a tone's FM is
#: 1.5). Those spikes can look like a second frequency level, but one that holds few
#: samples: FSK's two levels share them about evenly. Under PSK_SPIKES, PSK is looked for.
PSK_ENVELOPE_CV = 0.1
PSK_KURTOSIS = 6.0
PSK_SPIKES = 0.1
#: PSK is judged on the segment's first PSK_SPAN samples: thousands of symbols, and the
#: transforms of a whole segment would cost more memory than the span measured (CONTRACTS m5).
PSK_SPAN = 1 << 17
#: Keying of more than two levels (4ASK, 8ASK, 4FSK), which the 16-sample blocks split
#: into two clusters with a crowded middle: looked for sample by sample, when the blocks
#: find no keying, at the symbol clock (a line this far over the median in the spectrum
#: of the series' steps, at LEVEL_MIN_BAUD or more), over LEVEL_MIN_SYMBOLS symbols or
#: more of the segment's first LEVEL_SPAN samples (PSK's: at 1 MS/s, 0.13 s, so down to
#: about 1.5 kBd; a whole segment broke the memory bound at 28 MS/s). Each level holds
#: LEVEL_SHARE of its fair share or more, and the symbols are white: their spectrum's
#: top bin under LEVEL_WHITE times its mean. The smallest of LEVEL_ORDERS that fits is
#: the order; two levels are left to the blocks. A symbol is averaged over the middle
#: LEVEL_WINDOW of it, where shaped symbols (Gaussian, RRC) have settled: 8ASK's levels
#: stood 12 deviations apart so, 5 over the whole symbol.
#: Measured through ``measure`` on the tests' signals (2026-10-30): the keyed ones' clock
#: lines stood 41 to 59 dB over and their symbols' whiteness 6 to 11. What keeps the rest
#: out is mostly not the clock: AM and FM by noise (speech-like) made lines up to 44 dB,
#: but slow ones (under LEVEL_MIN_SYMBOLS in the span); tones made lines up to 86 dB, but
#: their symbols repeat (whiteness 130 to 3079); two equal tones' beat keys the frequency
#: between two values, white (10), at an 80 kHz clock: hence no order 2 here.
LEVEL_ORDERS = (4, 8)
LEVEL_SPAN = PSK_SPAN
LEVEL_CLOCK_DB = 35.0
LEVEL_MIN_BAUD = 100.0
LEVEL_MIN_SYMBOLS = 200
LEVEL_PHASES = 16
LEVEL_WINDOW = 0.5
LEVEL_ITERATIONS = 30
LEVEL_SHARE = 0.5
LEVEL_WHITE = 50.0
#: Shaped symbols (Gaussian BT 0.3 4FSK) hold a share of each neighbour, which splits every
#: level into sub-levels: the gap stood 2.2 to 3 deviations. When a fit fails, each symbol
#: is decided, the share of its two neighbours' decided levels fitted (least squares) and
#: taken off, LEVEL_ISI_ROUNDS times; kept when the share is LEVEL_ISI_MIN to LEVEL_ISI_MAX
#: and the levels then stand clear. Gaussian 4FSK's share was 0.20 and its gap rose to 10 to
#: 24 deviations; on the tests' FM, AM and tones the share was under 0.01 or the symbols not
#: white, and nothing changed.
LEVEL_ISI_ROUNDS = 3
LEVEL_ISI_MIN = 0.05
LEVEL_ISI_MAX = 0.4
#: The pulse shape: the spectrum in PULSE_NFFT-bin pieces of the segment's first PSK_SPAN
#: samples. Fitted on synthetic QPSK (20 to 100 kBd, roll-off 0.2 to 0.8) and 4ASK, the
#: roll-off came within 0.02; their power 1.1 to 1.5 symbol rates out stood -34 to -57 dB
#: under the top, rectangular symbols' (a sinc's first sidelobe) -12 to -13 dB.
PULSE_NFFT = 4096
#: Carrier-off ASK is read on the real axis, its phase followed in pieces this long.
BIPOLAR_BLOCK = 4096
PULSE_SIDELOBE_DB = -25.0
PULSE_RECT_DB = -18.0
PULSE_FIT_DB = 25.0
#: FM subcarriers: lines in the demodulated frequency above the audio, this far over the
#: median of that band (a 67 kHz subcarrier stood 86 dB over; an FM tone's noise, 38 dB).
#: Lines closer than SUBCARRIER_GROUP_HZ are one subcarrier and its sidebands (RDS is two
#: humps 2.4 kHz either side of 57 kHz), named at their power-weighted centre. FM video's
#: sound subcarriers are looked for between TV_SOUND_HZ, above the picture.
SUBCARRIER_MIN_HZ = 15e3
SUBCARRIER_DB = 50.0
SUBCARRIER_GROUP_HZ = 8e3
#: And a subcarrier carries a share of the demodulated signal: its power within 4 kHz, against
#: all of it above 20 Hz, this many dB or more. On a clean FM tone (blind test, 53 dB SNR) a
#: line 83 dB over an empty band's median held -53 dB; RDS held -20, a pilot -11, FM video's
#: sound -17, a 67 kHz subcarrier 0.
SUBCARRIER_SHARE_DB = -35.0
MAX_SUBCARRIERS = 4
#: FM broadcast: a pilot is a subcarrier within BROADCAST_NEAR_HZ of 19 kHz, RDS one of 57 kHz.
#: A pilot alone does not make stereo: the bench transmitter sends one with a mono programme
#: (left and right the same, L-R empty), and a blind round called that stereo. L-R is the
#: demodulated power 23 to 53 kHz (the 38 kHz carrier's own 1 kHz left out), against the
#: noise there, which is estimated beside the pilot (16 to 22 kHz, where nothing is sent) and
#: scaled by f squared, as a discriminator's noise rises (an SCA at 67 kHz, where the noise
#: might else be read, does not move it). Synthetic mono read 0.0 dB over (35 and 15 dB SNR,
#: with an SCA), stereo 11 to 31 dB: stereo from BROADCAST_STEREO_DB, the receive
#: chain's own STEREO_LR_DB (through it, stereo at 10 dB SNR read 6.4 dB).
BROADCAST_NEAR_HZ = 200.0
#: What a subcarrier carries: the demodulated frequency within SUB_HALF_HZ of it, as a complex
#: baseband. Its carrier is its strongest line (the pilot, RDS and TV sound are named apart);
#: the sidebands are the power past SUB_CARRIER_HZ either side, less the noise. One side SUB_SSB_DB over the
#: other is SSB; else a constant envelope (CV under SUB_FM_CV) with a frequency spread is FM,
#: a carrier holding SUB_CARRIER_SHARE of the power or more is AM, and none DSB.
SUB_HALF_HZ = 8e3
SUB_CARRIER_HZ = 150.0
SUB_SSB_DB = 10.0
SUB_FM_CV = 0.15
SUB_FM_HZ = 200.0
SUB_CARRIER_SHARE = 0.05
SUB_MIRROR = 0.25
SUB_NOISE_HZ = 12e3
BROADCAST_STEREO_DB = 3.0
TV_SOUND_HZ = (4.6e6, 9e6)
#: Lines beside the signal (Round 31, phase 4): peaks of the fine spectrum outside the
#: occupied band (BESIDE_MARGIN x its half width) and outside the signal's skirt (the bins
#: about its peak whose neighbourhood's strongest stays within BESIDE_SKIRT_DB of it: a
#: tone's FM lines and OOK's sidebands are its own) and with no twin at their mirror about the
#: signal's centre (BESIDE_PAIR_DB: its own sidebands), BESIDE_DC_BINS from the tuned centre
#: (where a mirror cannot judge) and inside BESIDE_SPAN of the span (the radio's filter rolls
#: off at its edges: a 40 MS/s recording showed a line 19.8 MHz out), BESIDE_DB over the floor and over the median of
#: BESIDE_LOCAL bins either side, at most MAX_BESIDE, strongest first. A line is the image
#: of its mirror about the tuned centre only when that mirror is IMAGE_DB or more stronger
#: and its spectrum, taken down by that much and flipped, fits the line's within
#: IMAGE_FIT_DB on IMAGE_FIT_SHARE of the bins either shows: an image brings its parent's
#: neighbourhood with it. A blind round's faint beacon beside a TV signal was taken for an
#: image without that check; a line is a signal until its mirror shows otherwise.
BESIDE_MARGIN = 1.2
BESIDE_SKIRT_DB = 40.0
BESIDE_PAIR_DB = 6.0
#: A TV signal's own carriers and picture lines lie across its channel, not its 99 % band: AM
#: TV's channel runs from TV_VISION_LOW_HZ under its vision carrier (its strongest tone) for
#: TV_CHANNEL_HZ, FM video's sound subcarriers reach TV_SOUND_HZ[1] either side, ATSC's channel
#: is 6 MHz. Lines are beside a TV signal only outside those.
TV_CHANNEL_HZ = {'525-line': 6e6, '625-line': 8e6}
TV_VISION_LOW_HZ = 1.25e6
ATSC_CHANNEL_HZ = 6e6
BESIDE_DC_BINS = 3
BESIDE_SPAN = 0.9
BESIDE_DB = 10.0
BESIDE_LOCAL = 32
MAX_BESIDE = 8
BESIDE_MAX_BINS = 64
IMAGE_DB = 20.0
IMAGE_HALF = 16
IMAGE_FIT_DB = 4.0
IMAGE_FIT_SHARE = 0.8
#: Programme audio (phase 4), for `am` and `fm` cards no wider than AUDIO_MAX_WIDTH_HZ (one
#: programme, an FM station with a 92 kHz subcarrier included; a band of several stations
#: read 3.3 MHz): the occupied band (AUDIO_REACH x its
#: width, AUDIO_MIN_HZ at least) taken again, demodulated (the envelope or the frequency) and
#: brought to AUDIO_RATE or a little over, cut at AUDIO_BAND_HZ or at AUDIO_EDGE of the band
#: taken: near that band's edge the band filter's block joins (every rate / step samples)
#: leave lines at multiples of that rate (a recording's AM tone showed a comb at 203 Hz
#: spacing, 7.7 to 8.5 kHz, cut at 10 kHz), and a discriminator's rising noise falls off.
AUDIO_REACH = 1.2
AUDIO_MAX_WIDTH_HZ = 500e3
AUDIO_MIN_HZ = 20e3
AUDIO_RATE = 32e3
AUDIO_EDGE = 0.5
AUDIO_BAND_HZ = 15e3
AUDIO_PAD = 2048
AUDIO_TAPER_HZ = 1000.0
AUDIO_BLOCK = 1 << 16
#: Lines in the audio: peaks of a long average (AUDIO_LINE_BIN_HZ bins) AUDIO_LINE_DB over the
#: median of AUDIO_LOCAL_HZ either side, AUDIO_LINE_MAX_HZ wide or less at -6 dB, from
#: AUDIO_LINE_LO_HZ up; a tone under speech stands there, speech's harmonics move and do not.
#: A keyed line is not in the long average: in short frames (AUDIO_FRAME_HZ bins) it is a
#: peak AUDIO_KEYED_DB over the frame's median beside it in AUDIO_KEYED_SHARE of them or more.
#: A line is keyed when it is absent from AUDIO_KEYED_SHARE of the frames where its usual
#: power stands AUDIO_KEYED_DB over the median beside it (where it would have shown), so a
#: steady line hidden by loud speech is not keyed, nor a weak one that flickers, and its power
#: when on spreads AUDIO_KEYED_SPREAD_DB or less (10th to 90th percentile): a key holds one
#: level (Morse at 12 to 25 wpm spread 0.4 to 1.3 dB), a note in music swells and fades (7.3).
AUDIO_LINE_BIN_HZ = 4.0
AUDIO_LINE_DB = 10.0
AUDIO_LOCAL_HZ = 100.0
AUDIO_LINE_MAX_HZ = 30.0
AUDIO_LINE_LO_HZ = 50.0
MAX_AUDIO_LINES = 8
AUDIO_FRAME_HZ = 16.0
AUDIO_KEYED_SHARE = 0.1
AUDIO_KEYED_DB = 16.0
AUDIO_KEYED_SPREAD_DB = 4.0
#: Keyed or steady is said only of a line on in AUDIO_KEYED_MIN frames or more (else None).
#: A line with AUDIO_CLUSTER others within AUDIO_CLUSTER_HZ is a band of content (music's
#: highs held 8 within 500 Hz), not a tone, and is not listed.
AUDIO_KEYED_MIN = 5
AUDIO_CLUSTER = 2
AUDIO_CLUSTER_HZ = 250.0
#: DTMF: 40 ms frames every 10 ms (25 Hz resolution, the grid 73 Hz apart at the least), the
#: power at the eight frequencies: the strongest of each group DTMF_SEP_DB over the rest of
#: it, the two within DTMF_TWIST_DB, holding DTMF_SHARE of the frame's power together; a
#: digit is DTMF_MIN_FRAMES such frames in a row. A tone of D ms in quiet passes about D / 10
#: frames (its edges in a Hann frame's tails count too: 15 ms passed 2, 45 ms 5), so its
#: length is the run times the hop and it is centred on the run's frames; four frames is a
#: tone of about 35 ms (the standard takes 40 ms and up, and refuses under 23).
DTMF_LOW = (697.0, 770.0, 852.0, 941.0)
DTMF_HIGH = (1209.0, 1336.0, 1477.0, 1633.0)
DTMF_KEYS = ('123A', '456B', '789C', '*0#D')
DTMF_FRAME_S = 0.04
DTMF_HOP_S = 0.01
DTMF_SEP_DB = 10.0
DTMF_TWIST_DB = 8.0
DTMF_SHARE = 0.3
DTMF_MIN_FRAMES = 4
#: A programme that repeats (a looped clip): the short frames' energy in AUDIO_LOOP_BANDS bands
#: (log, each band's mean and spread taken off), correlated with itself at every lag from
#: AUDIO_LOOP_MIN_S, with AUDIO_LOOP_OVERLAP_S of overlap at least: a loop is the first lag to
#: reach AUDIO_LOOP_CORR. Only audio that varies is looked at (its bands' median spread
#: AUDIO_VARIES or more, log10 units): a steady tone correlates at every lag.
AUDIO_LOOP_BANDS = 16
#: Under three bursts in a span, the card cannot measure their repeat (and warns), unless one
#: burst fills BURSTS_FILL of the span or more: that is a continuous signal.
BURSTS_FILL = 0.9
AUDIO_LOOP_MIN_S = 1.0
AUDIO_LOOP_OVERLAP_S = 3.0
AUDIO_LOOP_CORR = 0.8
AUDIO_VARIES = 0.2


class IQSourceError(Exception):
    """The file cannot be measured (not IQ, too short, missing, or not a
    file this reads); the message says why."""


class IQSource:
    """A recording of IQ, read a span at a time. ``read`` never writes."""

    def __init__(self, path, kind, rate, center_hz, samples, full_scale_dbm, fetch,
                 station_hz=None):
        self.path = path
        self.kind = kind
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.samples = int(samples)
        self.full_scale_dbm = None if full_scale_dbm is None else float(full_scale_dbm)
        self._fetch = fetch
        #: The station a recording of this app was tuned to (its ``.sigmf-meta``), or None.
        self.station_hz = None if not station_hz else float(station_hz)

    @property
    def seconds(self):
        return self.samples / self.rate

    def read(self, start, count):
        """Samples ``start`` to ``start + count`` as complex64, clipped to the file."""
        first = min(max(0, int(start)), self.samples)
        stop = min(first + max(0, int(count)), self.samples)
        if stop <= first:
            return np.empty(0, dtype=np.complex64)
        return np.array(self._fetch(first, stop), dtype=np.complex64)

    def view(self, i0, i1, mix_hz=0.0):
        """Samples ``i0`` to ``i1`` as an :class:`IQView`: read when sliced, not held."""
        return IQView(self, i0, i1, mix_hz)


class IQView:
    """Samples ``i0`` to ``i1`` of an :class:`IQSource`, read from the file only when sliced
    (``x[a:b]``; ``len(x)``), so a decoder can walk seconds of a recording without holding them
    (a cfile is a memmap). Each slice is mixed by -``mix_hz`` with its phase from the view's
    first sample, as ``iqlte.mix_to_dc`` mixes a whole span: the same samples to the bit."""

    ndim = 1
    dtype = np.dtype(np.complex64)

    def __init__(self, src, i0, i1, mix_hz=0.0):
        self.src = src
        self.i0 = min(max(0, int(i0)), src.samples)
        self.n = max(0, min(int(i1), src.samples) - self.i0)
        self.mix_hz = float(mix_hz)

    def __len__(self):
        return self.n

    @property
    def shape(self):
        return (self.n,)

    def __getitem__(self, key):
        if not isinstance(key, slice) or key.step not in (None, 1):
            raise TypeError('an IQView is read by plain slices, x[a:b]')
        a, b, _step = key.indices(self.n)
        if b <= a:
            return np.empty(0, dtype=np.complex64)
        y = self.src.read(self.i0 + a, b - a)
        if abs(self.mix_hz) >= 1e-3:
            t = np.arange(a, a + y.size) / self.src.rate
            y = (y * np.exp(-2j * np.pi * self.mix_hz * t)).astype(np.complex64)
        return y


def _sidecar_meta(path):
    meta = os.path.splitext(path)[0] + '.sigmf-meta'
    if not os.path.isfile(meta):
        return {}
    try:
        with open(meta) as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise IQSourceError(f"{os.path.basename(meta)} is not readable: {exc}") from exc


#: Complex float32 IQ, interleaved little-endian, under any of these names: GNU Radio's
#: .cfile, rtl_433's and the bench toolkit's .cf32, and SigMF's .sigmf-data when its meta says
#: cf32_le. One reader serves them all; the kind in the card is the extension's.
CF32_KINDS = {'.cfile': 'cfile', '.cf32': 'cf32', '.sigmf-data': 'sigmf'}


def _open_cfile(path, rate, center_hz, kind='cfile'):
    name = os.path.basename(path)
    samples = os.path.getsize(path) // 8
    if samples < MIN_SAMPLES:
        raise IQSourceError(f"{name} holds {samples} samples; at least {MIN_SAMPLES} are needed.")
    meta = _sidecar_meta(path)
    datatype = meta.get('global', {}).get('core:datatype')
    if kind == 'sigmf' and datatype not in ('cf32_le', 'cf32'):
        raise IQSourceError(f"{name} holds {datatype or 'no datatype'} by its .sigmf-meta; "
                            "only cf32_le is read.")
    meta_rate = meta.get('global', {}).get('core:sample_rate')
    captures = meta.get('captures') or [{}]
    meta_centre = captures[0].get('core:frequency')
    if rate is None:
        rate = meta_rate
    if rate is None:
        raise IQSourceError(f"{name} has no .sigmf-meta giving its rate: pass rate= "
                            f"(and center_hz=) to measure a bare {os.path.splitext(name)[1]}.")
    if rate <= 0:
        raise IQSourceError("the sample rate must be positive.")
    centre = center_hz if center_hz is not None else (meta_centre or 0.0)
    data = np.memmap(path, dtype=np.complex64, mode='r', shape=(samples,))
    return IQSource(path, kind, rate, centre, samples, None,
                    lambda a, b: data[a:b],
                    station_hz=meta.get('global', {}).get('fmrx:station_frequency'))


def _open_sdvr(path, rate, center_hz):
    name = os.path.basename(path)
    try:
        layout = sceptre_dvr.scan(path)
        reader = sceptre_dvr.Reader(path, layout)
    except (sceptre_dvr.SdvrError, OSError, ValueError) as exc:
        raise IQSourceError(str(exc)) from exc
    if layout['samples'] < MIN_SAMPLES:
        raise IQSourceError(f"{name} holds {layout['samples']} samples; at least {MIN_SAMPLES} are needed.")
    centre = center_hz if center_hz is not None else layout['center_hz']
    return IQSource(path, 'sdvr', rate if rate is not None else layout['rate'], centre,
                    layout['samples'], layout['full_scale_dbm'], reader.read)


def _data_gain(blue):
    """The file's DATA_GAIN in dB, or None when it has none (or it is not a number)."""
    try:
        gain = float(blue.keywords.get('DATA_GAIN'))
    except (TypeError, ValueError):
        return None
    return gain if math.isfinite(gain) else None


def _open_cdif(path, rate, center_hz):
    """A BLUE file's IQ. With DATA_GAIN the samples are counts, read as BlueReader
    reads them: each is scaled to volts by 10 ** (-DATA_GAIN / 20), so |x|**2 is
    mW and the level is in dBm (full scale 0 dBm). Without it they are raw."""
    name = os.path.basename(path)
    try:
        blue = sceptre_blue.read(path)
    except (sceptre_blue.BlueError, OSError, ValueError) as exc:
        raise IQSourceError(str(exc)) from exc
    if blue.fmt[:1] != 'C':
        raise IQSourceError(f"{name} holds {blue.fmt!r}, not complex IQ.")
    if blue.samples < MIN_SAMPLES:
        raise IQSourceError(f"{name} holds {blue.samples} samples; at least {MIN_SAMPLES} are needed.")
    if rate is None and not blue.rate:
        raise IQSourceError(f"{name} does not say its sample rate: pass rate=.")
    centre = center_hz if center_hz is not None else (blue.center_hz or 0.0)
    gain = _data_gain(blue)
    if gain is None:
        fetch, full_scale_dbm = blue.iq, None
    else:
        scale = np.float32(10 ** (-gain / 20.0))
        fetch, full_scale_dbm = (lambda a, b: blue.iq(a, b) * scale), 0.0
    return IQSource(path, 'cdif', rate if rate is not None else blue.rate, centre,
                    blue.samples, full_scale_dbm, fetch)


def open_iq(path, rate=None, center_hz=None):
    """Open a recording of IQ for reading. ``rate`` and ``center_hz`` (absolute)
    override the file's own, and a bare ``.cfile`` needs ``rate``.

    Raises :class:`IQSourceError` for a missing file, a type this does not
    read (a ``.fft`` spectrum, a sweep ``.sdvr``), or one too short to measure.
    """
    path = str(path)
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise IQSourceError(f"{name} does not exist.")
    ext = os.path.splitext(path)[1].lower()
    if ext in CF32_KINDS:
        return _open_cfile(path, rate, center_hz, CF32_KINDS[ext])
    if ext == '.sdvr':
        return _open_sdvr(path, rate, center_hz)
    if ext == '.cdif':
        return _open_cdif(path, rate, center_hz)
    if ext == '.fft':
        raise IQSourceError(f"{name} is a spectrum, not IQ: nothing to measure.")
    raise IQSourceError(f"{name} is not a type this reads (.cfile, .cf32, .sigmf-data, .sdvr, .cdif).")


# ---- spectra -----------------------------------------------------------------

def _pow2_floor(n):
    return 1 << (int(n).bit_length() - 1)


def _blackman_harris(n):
    k = 2 * np.pi * np.arange(n) / (n - 1)
    return 0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)


class _Welch:
    """Averaged periodogram of consecutive whole segments of ``nfft`` samples.
    Each bin is in units of per-sample power: white noise of power p gives p."""

    def __init__(self, nfft, window):
        self.nfft = nfft
        self.window = window
        self.norm = float(np.sum(window ** 2))
        self.acc = np.zeros(nfft)
        self.segments = 0
        self.tail = np.empty(0, dtype=np.complex64)

    def feed(self, x):
        buf = np.concatenate([self.tail, x]) if self.tail.size else x
        whole = len(buf) // self.nfft
        if whole:
            seg = buf[:whole * self.nfft].reshape(whole, self.nfft) * self.window
            spec = np.fft.fft(seg, axis=1)
            self.acc += np.sum(spec.real ** 2 + spec.imag ** 2, axis=0)
            self.segments += whole
        self.tail = buf[whole * self.nfft:]

    def spectrum(self):
        """Mean power per bin, shifted so 0 Hz is in the middle."""
        return np.fft.fftshift(self.acc / (self.segments * self.norm))


class _Scan:
    """One pass over the samples: the power and the two spectra."""

    def __init__(self, nfft_coarse, nfft_fine):
        self.coarse = _Welch(nfft_coarse, _blackman_harris(nfft_coarse))
        self.fine = _Welch(nfft_fine, np.hanning(nfft_fine))
        self.power = 0.0
        self.count = 0

    def feed(self, x):
        self.power += float(np.sum(x.real * x.real + x.imag * x.imag, dtype=np.float64))
        self.count += len(x)
        self.coarse.feed(x)
        self.fine.feed(x)


class _Track:
    """One pass over the samples: the envelope and the phase advance, in blocks."""

    def __init__(self, block=BLOCK):
        self.block = block
        self.env = []
        self.prod = []
        self.tail = np.empty(0, dtype=np.complex64)
        self.seg = []
        self.seg_n = 0

    def feed(self, x):
        if self.seg_n < SEG:
            self.seg.append(x[:SEG - self.seg_n])
            self.seg_n += len(self.seg[-1])
        buf = np.concatenate([self.tail, x]) if self.tail.size else x
        # The last sample is kept: it is the lag partner of the next block's first.
        b = self.block
        cut = (len(buf) - 1) // b * b
        if cut:
            self.env.append(np.abs(buf[:cut].reshape(-1, b)).mean(axis=1))
            prod = buf[1:cut + 1] * np.conj(buf[:cut])
            self.prod.append(prod.reshape(-1, b).sum(axis=1))
        self.tail = buf[cut:]


def _floor(P, segments, inband=None):
    """The noise floor per bin of a spectrum averaged over ``segments``: the
    25th percentile, which the signal's own spectrum lifts less than the median,
    divided by that percentile of the averaged chi-square (Wilson-Hilferty).
    ``inband`` (a mask) limits it to the bins the band covers: outside them a
    decimated spectrum is empty, not noise."""
    vals = P if inband is None or not inband.any() else P[inband]
    k = 1.0 / (9 * segments)
    z = -0.6745
    return float(np.percentile(vals, 25)) / (1 - k + z * math.sqrt(k)) ** 3


def _tones(P, floor, bin_hz, origin):
    """The strongest tones of the coarse spectrum ``P`` (shifted): each more than
    TONE_DB above the floor, at least 3 bins apart, strongest first. A tone's
    level is its 9-bin power against the floor in the same bins."""
    n = len(P)
    nine = np.convolve(P, np.ones(9), mode='same')
    level = 10 * np.log10(np.maximum(nine, 1e-300) / (9 * floor))
    k = np.flatnonzero((P[1:-1] > P[:-2]) & (P[1:-1] >= P[2:])) + 1
    k = k[(k >= 4) & (k < n - 4)]
    k = k[level[k] > TONE_DB]
    order = k[np.argsort(-level[k], kind='stable')]
    if order.size:
        order = order[level[order] >= level[order[0]] - TONE_RANGE_DB]
    # Tones are 8 bins apart, not 3: a tone's 9-bin window reaches into the
    # main lobe of a stronger one, and that skirt is not a tone of its own.
    chosen = []
    for c in order:
        if all(abs(int(c) - o) >= TONE_GAP for o in chosen):
            chosen.append(int(c))
            if len(chosen) == MAX_TONES:
                break
    lp = np.log(np.maximum(P, 1e-300))
    out = []
    for c in chosen:
        den = lp[c - 1] - 2 * lp[c] + lp[c + 1]
        d = 0.5 * (lp[c - 1] - lp[c + 1]) / den if den < 0 else 0.0
        d = min(max(d, -0.5), 0.5)
        out.append({'freq_hz': origin + (c + d - n // 2) * bin_hz,
                    'level_db': float(level[c])})
    return out


def _occupied(P, bin_hz, origin):
    """(centre, 99 % width, width 20 dB down) of the fine spectrum ``P``."""
    n = len(P)
    total = float(P.sum())
    if not total > 0:
        return origin, 0.0, 0.0
    centre = origin + bin_hz * float(np.sum((np.arange(n) - n // 2) * P) / total)
    cum = np.concatenate([[0.0], np.cumsum(P)])
    j = np.searchsorted(cum, cum[:-1] + 0.99 * total, side='left')
    ok = j <= n
    counts = j[ok] - np.flatnonzero(ok)
    bw99 = float(counts.min()) * bin_hz
    k = int(np.argmax(P))
    above = P >= P[k] / 100
    below = np.flatnonzero(~above[:k])
    left = int(below[-1]) + 1 if below.size else 0
    beyond = np.flatnonzero(~above[k + 1:])
    right = k + 1 + int(beyond[0]) if beyond.size else n
    return centre, bw99, (right - left) * bin_hz


# ---- levels, bursts and symbol rate -------------------------------------------

def _levels(v):
    """Whether the values ``v`` form two levels a threshold separates cleanly.

    Returns ``(keyed, low, high, threshold)``: the threshold is the best
    two-class split (Otsu); the levels are the medians either side; keyed
    means the levels are more than 3 within-level deviations apart (robust
    deviation), and few values lie in the middle quarter of the gap (a
    sine's values do, a keyed signal's do not)."""
    none = (False, None, None, None)
    if v.size < 16:
        return none
    lo_v, hi_v = float(v.min()), float(v.max())
    if not hi_v > lo_v:
        return none
    hist, edges = np.histogram(v, bins=256, range=(lo_v, hi_v))
    centres = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = v.size - w0
    s0 = np.cumsum(hist * centres)
    valid = (w0 > 0) & (w1 > 0)
    m0 = np.divide(s0, w0, out=np.zeros_like(s0), where=valid)
    m1 = np.divide(s0[-1] - s0, w1, out=np.zeros_like(s0), where=valid)
    between = np.where(valid, w0 * w1 * (m0 - m1) ** 2, 0.0)
    # An empty gap ties every split in it: the middle of the tie, not its low edge.
    ties = np.flatnonzero(between >= between.max())
    k = int(ties[(len(ties) - 1) // 2]) if ties.size and ties[-1] - ties[0] == len(ties) - 1 \
        else int(np.argmax(between))
    thr = float(edges[k + 1])
    low = v < thr
    n_low = int(np.count_nonzero(low))
    if min(n_low, v.size - n_low) < MIN_CLUSTER * v.size:
        return none
    a, b = v[low], v[~low]
    lo, hi = float(np.median(a)), float(np.median(b))
    s = 1.4826 * max(float(np.median(np.abs(a - lo))), float(np.median(np.abs(b - hi))))
    sep = hi - lo
    middle = float(np.mean(np.abs(v - thr) < sep / 4))
    keyed = sep > 0 and sep > KEYED_SIGMA * s and middle < MIDDLE_MAX
    return keyed, lo, hi, thr


def _bursts(on, fs_block, span_s):
    """The bursts of an envelope: runs of ``on`` blocks, with the gaps between
    them merged when they are short (within a burst) and kept when long
    (between bursts): the gap lengths split at their largest jump.

    Returns ``(card section, mask)``: the mask covers each burst from its start
    to its end, its internal gaps included."""
    empty = {'count': 0, 'durations_s': [], 'median_duration_s': None, 'gaps_s': [],
             'repeat_interval_s': None, 'duty': None}
    if not on.any():
        return empty, np.zeros(on.size, dtype=bool)
    flips = np.flatnonzero(on[1:] != on[:-1]) + 1
    edges = np.concatenate([[0], flips, [on.size]])
    starts, ends = edges[:-1], edges[1:]
    is_on = on[starts]
    s_on, e_on = starts[is_on], ends[is_on]
    gaps = s_on[1:] - e_on[:-1]
    separate = np.zeros(gaps.size, dtype=bool)
    if gaps.size:
        u = np.unique(gaps)
        if u.size == 1:
            cut = float(u[0])
        else:
            ratio = u[1:] / u[:-1]
            j = int(np.argmax(ratio))
            cut = math.sqrt(float(u[j]) * float(u[j + 1])) if ratio[j] >= 4 else math.inf
        separate = gaps >= cut
    first = np.flatnonzero(np.concatenate([[True], separate]))
    last = np.concatenate([first[1:] - 1, [len(s_on) - 1]])
    b_start, b_end = s_on[first], e_on[last]
    durations = (b_end - b_start) / fs_block
    starts_s = b_start / fs_block
    gap_s = gaps[separate] / fs_block
    count = int(first.size)
    step = np.zeros(on.size + 1, dtype=np.int64)
    np.add.at(step, b_start, 1)
    np.add.at(step, b_end, -1)
    card = {
        'count': count,
        'durations_s': durations[:LISTED].tolist(),
        'median_duration_s': float(np.median(durations)),
        'gaps_s': gap_s[:LISTED].tolist(),
        'repeat_interval_s': float(np.median(np.diff(starts_s))) if count >= 3 else None,
        'duty': float(np.sum(durations) / span_s),
    }
    return card, np.cumsum(step)[:-1] > 0


def _symbol_rate(labels, fs_block):
    """The symbol rate of a keyed signal: the lowest strong line in the
    spectrum of its label changes (a line at the symbol clock), or None."""
    edges = (labels[1:] != labels[:-1]).astype(np.float64)
    if edges.sum() < 4:
        return None
    edges -= edges.mean()
    n = max(1 << 16, 1 << int(len(edges) - 1).bit_length())
    power = np.abs(np.fft.rfft(edges, n)) ** 2
    freqs = np.arange(power.size) * fs_block / n
    band = np.flatnonzero((freqs >= 200.0) & (freqs <= fs_block / 4))
    if band.size < 3:
        return None
    p = power[band]
    top = float(p.max())
    peaks = np.flatnonzero((p[1:-1] >= p[:-2]) & (p[1:-1] > p[2:]) & (p[1:-1] >= 0.5 * top)) + 1
    k = int(band[peaks[0]]) if peaks.size else int(band[int(np.argmax(p))])
    if not 0 < k < power.size - 1:
        return None
    y0, y1, y2 = power[k - 1], power[k], power[k + 1]
    den = y0 - 2 * y1 + y2
    d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    return float((k + min(max(d, -0.5), 0.5)) * fs_block / n)


# ---- the sample-level checks: tv, pulses, psk, subcarriers -------------------

def _spectrum(v, rate):
    """(freqs, power) of a real series, its mean taken off, Hann-windowed."""
    v = v - v.mean()
    return np.fft.rfftfreq(len(v), 1 / rate), np.abs(np.fft.rfft(v * np.hanning(len(v)))) ** 2


def _line(freqs, power, lo, hi):
    """The strongest bin in lo..hi: (Hz, dB over the median of lo/2..1.5 hi), or None."""
    w = np.flatnonzero((freqs >= lo) & (freqs <= hi))
    ref = power[(freqs >= lo / 2) & (freqs <= 1.5 * hi)]
    if not w.size or not ref.size:
        return None
    i = w[int(np.argmax(power[w]))]
    return float(freqs[i]), 10 * math.log10(max(power[i], 1e-300) / max(float(np.median(ref)), 1e-300))


def _tv(seg, rate, bw99, cv):
    """Analog TV: the line rate as a line, with its second harmonic, in the envelope
    (AM vision) or the frequency (FM video) of a signal 1 MHz wide or more. Returns
    the card's ``tv`` section, or None."""
    if bw99 < TV_MIN_WIDTH_HZ or len(seg) < 64 or rate < 4 * max(TV_LINE_HZ.values()):
        return None
    tracks = {'envelope': np.abs(seg).astype(np.float64),
              'frequency': np.angle(seg[1:] * np.conj(seg[:-1])).astype(np.float64)}
    best = None
    for where, v in tracks.items():
        freqs, power = _spectrum(v, rate)
        for system, f_h in TV_LINE_HZ.items():
            one = _line(freqs, power, f_h * (1 - TV_TOLERANCE), f_h * (1 + TV_TOLERANCE))
            two = _line(freqs, power, 2 * f_h * (1 - TV_TOLERANCE), 2 * f_h * (1 + TV_TOLERANCE))
            if one and two and min(one[1], two[1]) >= TV_LINE_DB and (best is None or one[1] > best[0]):
                best = (one[1], system, one[0], where)
    if best is None:
        return None
    return {'system': best[1], 'line_rate_hz': best[2], 'line_db': best[0], 'seen_in': best[3],
            'modulation': 'fm' if cv < 0.1 else 'am'}


def _video_swing(seg, rate):
    """FM video's swing: {deviation_hz, peak_to_peak_hz, low_peak_to_peak_hz, pre_emphasis}.
    The peak to peak (0.1 to 99.9 %) of the frequency averaged to the video band
    (VIDEO_BAND_HZ) and to its low part (VIDEO_LOW_HZ). Pre-emphasis lifts the high video
    frequencies, so the edges overshoot: the video band's swing over the low one's was 2.2
    on the bench's F.405 PAL, 1.02 on video without it. Over VIDEO_PRE_RATIO is pre-emphasis,
    and then a nominal deviation (F.405's "8 MHz for 1 V", at the network's crossover) is
    not what either swing shows."""
    d = np.angle(seg[1:] * np.conj(seg[:-1])).astype(np.float64) * (rate / (2 * np.pi))

    def swing(band):
        n = max(1, int(round(rate / band)))
        v = np.convolve(d, np.ones(n) / n, 'valid') if n > 1 else d
        lo, hi = np.percentile(v, [0.1, 99.9])
        return float(hi - lo)

    video, low = swing(VIDEO_BAND_HZ), swing(VIDEO_LOW_HZ)
    return {'deviation_hz': video / 2, 'peak_to_peak_hz': video, 'low_peak_to_peak_hz': low,
            'pre_emphasis': bool(low > 0 and video / low > VIDEO_PRE_RATIO)}


def _atsc(tones, centre, bw99):
    """ATSC 8VSB: the card's ``atsc`` section, or None. The pilot is the strongest tone,
    at one edge of a block about 5.4 MHz wide."""
    if not tones or not ATSC_WIDTH_HZ[0] <= bw99 <= ATSC_WIDTH_HZ[1]:
        return None
    pilot = tones[0]['freq_hz']
    low, high = centre - bw99 / 2, centre + bw99 / 2
    near = ATSC_EDGE * bw99
    if abs(pilot - low) <= near:
        inverted = False
    elif abs(pilot - high) <= near:
        inverted = True
    else:
        return None
    # The channel's own centre: the pilot is 309.441 kHz inside its 6 MHz edge.
    channel = pilot + (-1 if inverted else 1) * (3e6 - 309.441e3)
    return {'pilot_hz': pilot, 'channel_centre_hz': channel, 'inverted': inverted}


def _pulses(seg, rate):
    """A train of short regular pulses: the card's ``pulses`` section, or None. Each
    pulse's start is fitted to a clock; starts that stray from it carry the data (PPM)."""
    a = np.abs(seg)
    if len(a) < 64:
        return None
    on = a > 0.5 * float(np.percentile(a, 99.9))
    duty = float(np.mean(on))
    rises = np.flatnonzero(~on[:-1] & on[1:]) + 1
    falls = np.flatnonzero(on[:-1] & ~on[1:]) + 1
    if duty > PULSE_DUTY or rises.size < PULSE_MIN:
        return None
    gaps = np.diff(rises)
    med = float(np.median(gaps))
    if not med > 0 or np.mean(np.abs(gaps - med) < 0.25 * med) < PULSE_REGULAR:
        return None
    reach = PULSE_GAP * med
    if gaps.max() > reach or rises[0] > reach or len(a) - rises[-1] > reach:
        return None
    falls = falls[falls > rises[0]]
    k = min(rises.size, falls.size)
    widths = (falls[:k] - rises[:k]).astype(np.float64)
    width = float(np.median(widths)) / rate if k else None
    spread = float(np.percentile(widths, 90) - np.percentile(widths, 10)) / max(float(np.median(widths)), 1.0) \
        if k else 0.0
    # The clock: starts counted in periods (a missed pulse is a skipped count), a line fitted.
    count = np.round((rises - rises[0]) / med)
    slope, start = np.polyfit(count, rises.astype(np.float64), 1)
    stray = rises - (start + slope * count)
    jitter = float(np.std(stray))
    moves = jitter > max(PULSE_MOVES * slope, 2.0)
    return {'rate_hz': rate / slope, 'width_s': width, 'duty': duty, 'count': int(rises.size),
            'position_rms_s': jitter / rate, 'position_swing_s': float(np.ptp(stray)) / rate,
            'position_modulated': bool(moves), 'width_modulated': bool(spread > PULSE_WIDTHS)}


def _psk(seg, rate, base):
    """PSK of order 2, 4 or 8: the card's ``psk`` section, or None. The order is the
    smallest M whose M-th power is a line while the signal has no carrier line; the
    line's frequency over M is the carrier's offset."""
    if len(seg) < 64:
        return None
    # complex64 throughout, and only the start: see PSK_SPAN.
    x = seg[:PSK_SPAN].astype(np.complex64)

    def coherence(m):
        z = x ** m
        spec = np.abs(np.fft.fft(z))
        i = int(np.argmax(spec))
        return (float(spec[i]) / max(float(np.abs(z).sum(dtype=np.float64)), 1e-300),
                float(i if i < len(x) // 2 else i - len(x)) * rate / len(x))

    # A carrier line ends it first: one transform, and a CW or AM never gets further.
    if coherence(1)[0] >= PSK_NO_CARRIER:
        return None
    a = np.abs(x)
    cv = float(a.std() / a.mean()) if a.mean() > 0 else 0.0
    d = np.angle(x[1:] * np.conj(x[:-1]))
    d = d - d.mean()
    var = float(np.var(d))
    kurtosis = float(np.mean(d.astype(np.float64) ** 4)) / var ** 2 if var > 0 else 0.0
    del d
    if not (cv >= PSK_ENVELOPE_CV or kurtosis > PSK_KURTOSIS):
        return None
    for m in PSK_ORDERS:
        c, f = coherence(m)
        if c >= PSK_LINE:
            env2 = np.abs(x) ** 2
            sym = _line(*_spectrum(env2, rate), 1e3, rate / 2) if cv >= PSK_ENVELOPE_CV else None
            return {'order': m, 'line_coherence': c, 'carrier_offset_hz': f / m + base,
                    'symbol_rate_baud': sym[0] if sym else None}
    return None


def _clock(v, rate, smooth):
    """The symbol clock of a keyed series: the lowest strong line in the spectrum of its
    steps (``|v'|`` after a ``smooth``-sample average). Returns (Hz, dB over the median)."""
    if smooth > 1:
        c = np.concatenate([[0.0], np.cumsum(v)])
        v = (c[smooth:] - c[:-smooth]) / smooth
    d = np.abs(np.diff(v))
    if d.size < 64:
        return None
    freqs, power = _spectrum(d, rate)
    band = np.flatnonzero((freqs >= LEVEL_MIN_BAUD) & (freqs <= rate / 8))
    if band.size < 3:
        return None
    p = power[band]
    peaks = np.flatnonzero((p[1:-1] >= p[:-2]) & (p[1:-1] > p[2:]) & (p[1:-1] >= 0.5 * p.max())) + 1
    i = int(band[peaks[0]]) if peaks.size else int(band[int(np.argmax(p))])
    return float(freqs[i]), 10 * math.log10(max(power[i], 1e-300) / max(float(np.median(p)), 1e-300))


def _fit_levels(s, m):
    """``m`` levels in the symbol values ``s`` (1-D, medians from quantile seeds). Returns
    (levels low first, smallest share times m, smallest gap over the within-level robust
    deviation, the largest share of values in the middle quarter of a gap)."""
    c = np.quantile(s, (np.arange(m) + 0.5) / m)
    for _ in range(LEVEL_ITERATIONS):
        lab = np.argmin(np.abs(s[:, None] - c[None, :]), axis=1)
        c = np.array([np.median(s[lab == j]) if np.any(lab == j) else c[j] for j in range(m)])
    c = np.sort(c)
    lab = np.argmin(np.abs(s[:, None] - c[None, :]), axis=1)
    share = float(np.bincount(lab, minlength=m).min()) / s.size * m
    dev = max(1.4826 * float(np.median(np.abs(s[lab == j] - c[j]))) for j in range(m) if np.any(lab == j))
    gaps = np.diff(c)
    sep = float(gaps.min()) / dev if dev > 0 else math.inf
    middle = max(float(np.mean(np.abs(s - (a + b) / 2) < (b - a) / 4)) for a, b in zip(c[:-1], c[1:]))
    return c.tolist(), share, sep, middle


def _symbols(v, rate, baud):
    """The symbols of a sample-rate series at ``baud``: each averaged over its middle
    LEVEL_WINDOW, at the phase where they spread most. None when too few or too short."""
    sps = rate / baud
    n = int(round(LEVEL_WINDOW * sps))
    if n < 2 or len(v) < LEVEL_MIN_SYMBOLS * sps:
        return None
    c = np.concatenate([[0.0], np.cumsum(v)])
    avg = (c[n:] - c[:-n]) / n
    best = None
    for phase in np.linspace(0, sps, LEVEL_PHASES, endpoint=False):
        idx = np.round(phase + np.arange(int((len(avg) - 1 - phase) / sps)) * sps).astype(np.int64)
        s = avg[idx]
        if best is None or s.var() > best.var():
            best = s
    return best


def _neighbours_off(s, m):
    """The symbols ``s`` with their neighbours' share taken off, for ``m`` levels: each
    decided, ``s = g0 a[k] + g1 (a[k-1] + a[k+1]) + d`` fitted, and ``g1`` times the
    neighbours subtracted. Returns (symbols, g1 / g0)."""
    x, ratio = s, 0.0
    for _ in range(LEVEL_ISI_ROUNDS):
        c = np.asarray(_fit_levels(x, m)[0])
        a = c[np.argmin(np.abs(x[:, None] - c[None, :]), axis=1)]
        nb = np.zeros_like(a)
        nb[1:] += a[:-1]
        nb[:-1] += a[1:]
        g0, g1, _ = np.linalg.lstsq(np.column_stack([a, nb, np.ones_like(a)]), s, rcond=None)[0]
        ratio = float(g1 / g0) if g0 else 0.0
        x = s - g1 * nb
    return x, ratio


def _level_order(s, orders):
    """The smallest of ``orders`` whose levels stand clear in the symbols ``s``, as they are
    or with their neighbours' share taken off: (order, levels, share) or None. The symbols
    must be white: a sum of tones sampled at a clock it made (their difference) repeats a
    few values, its spectrum lines."""
    z = np.abs(np.fft.rfft(s - s.mean()))[1:] ** 2
    if not z.size or z.max() > LEVEL_WHITE * z.mean():
        return None

    def clear(fit):
        _, share, sep, middle = fit
        return share >= LEVEL_SHARE and sep > KEYED_SIGMA and middle < MIDDLE_MAX

    for m in orders:
        fit = _fit_levels(s, m)
        if clear(fit):
            return m, fit[0], 0.0
        x, ratio = _neighbours_off(s, m)
        if LEVEL_ISI_MIN <= ratio <= LEVEL_ISI_MAX:
            fit = _fit_levels(x, m)
            if clear(fit):
                # The levels a run of one symbol settles to: its own and both neighbours'.
                c = np.asarray(fit[0])
                return m, (c.mean() + (c - c.mean()) * (1 + 2 * ratio)).tolist(), ratio
    return None


def _shaped(share):
    """The evidence's words for a neighbour share taken off, or nothing."""
    return (f" Read with each neighbour's {share:.2f} share taken off (shaped symbols, "
            "Gaussian or the like)." if share else "")


def _keyed_levels(v, rate, smooth, clock=None):
    """Keying of 4 or 8 levels in a sample-rate series (an envelope or a frequency),
    found at its symbol clock. Returns {order, levels, symbol_rate_baud, clock_db} or None."""
    v = v.astype(np.float64)
    clock = _clock(v, rate, smooth) if clock is None else clock
    if clock is None or clock[1] < LEVEL_CLOCK_DB:
        return None
    s = _symbols(v, rate, clock[0])
    fit = _level_order(s, LEVEL_ORDERS) if s is not None else None
    if fit is None:
        return None
    return {'order': fit[0], 'levels': fit[1], 'symbol_rate_baud': clock[0], 'clock_db': clock[1],
            'neighbour_share': fit[2]}


def _bipolar(x, rate, offset):
    """The real axis of a carrierless BPSK-like signal: ``x`` brought from ``offset`` Hz to
    0 and turned so its symbols lie on the real line, then the real part. The phase is
    half the angle of the square's mean, followed through the span in BIPOLAR_BLOCK pieces
    (the M-th power line gives the offset to a few hertz, a turn or so over the span)."""
    n = len(x)
    z = x.astype(np.complex128) * np.exp(-2j * np.pi * offset * np.arange(n) / rate)
    k = n // BIPOLAR_BLOCK
    if k < 2:
        return np.real(z * np.exp(-0.5j * np.angle(np.mean(z ** 2))))
    sq = (z[:k * BIPOLAR_BLOCK] ** 2).reshape(k, BIPOLAR_BLOCK).mean(axis=1)
    ang = np.unwrap(np.angle(sq))
    centres = (np.arange(k) + 0.5) * BIPOLAR_BLOCK
    slope, start = np.polyfit(centres, ang, 1)
    return np.real(z * np.exp(-0.5j * (start + slope * np.arange(n))))


def _rc_power(f, baud, beta):
    """A raised cosine's power at ``f`` from the centre: what an RRC-shaped signal shows."""
    a = np.abs(f) / baud
    lo, hi = (1 - beta) / 2, (1 + beta) / 2
    p = np.where(a <= lo, 1.0, 0.0)
    mid = (a > lo) & (a < hi)
    p[mid] = 0.5 * (1 + np.cos(math.pi / beta * (a[mid] - lo)))
    return p


def _pulse(seg, rate, offset, baud):
    """The pulse shape of a linear modulation (PSK, ASK) at ``baud``, centred ``offset``
    from the segment's centre: the card's ``pulse`` section. Its spectrum (the carrier's
    own bins left out) is fitted with a raised cosine; ``sidelobe_db`` is the most power
    from 1.1 to 1.5 symbol rates out, against the top. Rectangular symbols leave a sinc's
    sidelobe there and a raised cosine none, so a roll-off is given only under PULSE_SIDELOBE_DB."""
    nfft = PULSE_NFFT
    x = seg[:PSK_SPAN]
    k = len(x) // nfft
    if k < 4 or not baud or baud * 1.5 > rate / 2:
        return None
    x = x[:k * nfft] * np.exp(-2j * np.pi * offset * np.arange(k * nfft) / rate).astype(np.complex64)
    P = np.fft.fftshift(np.mean(np.abs(np.fft.fft(x.reshape(k, nfft) * np.hanning(nfft), axis=1)) ** 2, axis=0))
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate))
    a = np.abs(f) / baud
    off_carrier = np.abs(f) > 4 * rate / nfft      # a carrier and its Hann leakage out
    centre = (a <= 0.2) & off_carrier
    ring = (a >= 1.0) & (a <= 1.5)
    if not centre.any() or not ring.any():
        return None
    top = float(np.median(P[centre]))
    floor = float(np.percentile(P[ring], 10))
    if not top > floor:
        return None
    side = (a >= 1.1) & (a <= 1.5)
    sidelobe = 10 * math.log10(max(float(np.max(P[side] - floor)) / (top - floor), 1e-12))
    keep = (a <= 1.2) & off_carrier
    y = (P[keep] - floor) / (top - floor)
    # Two shapes: impulses through a root raised cosine (the spectrum a raised cosine), and
    # held symbols through one (a sinc times it; the bench generators filter that way, and
    # fitted as the first their roll-off read 0.62 to 0.68 for 0.35 and 0.5).
    # In dB, where the spectrum is within PULSE_FIT_DB of the top: the edges carry the roll-off,
    # and in power the passband's ripple (the data's own, about 1 dB over the span) outweighed
    # them (a bench QPSK at 0.35 fitted 0.25).
    held = np.sinc(f[keep] / baud) ** 2
    fit = y > 10 ** (-PULSE_FIT_DB / 10)
    y_db = 10 * np.log10(y[fit])
    best = min((float(np.mean((y_db - 10 * np.log10(np.maximum(_rc_power(f[keep], baud, b) * w, 1e-12))[fit]) ** 2)),
                float(b), name)
               for name, w in (('raised cosine', np.ones_like(held)), ('held raised cosine', held))
               for b in np.arange(0.05, 1.0 + 1e-9, 0.01))
    shaped = sidelobe < PULSE_SIDELOBE_DB
    return {'rolloff': best[1] if shaped else None, 'sidelobe_db': sidelobe,
            'shape': best[2] if shaped else 'rectangular' if sidelobe > PULSE_RECT_DB else None}


def _run_width(P, k, share):
    """Bins either side of ``k`` (and ``k``) where ``P`` stays at ``share`` of ``P[k]`` or more."""
    lo, hi = k, k
    while lo > 0 and P[lo - 1] >= share * P[k]:
        lo -= 1
    while hi < len(P) - 1 and P[hi + 1] >= share * P[k]:
        hi += 1
    return lo, hi


def _image_check(P, floor, k, j, half):
    """(mirror over the line in dB, image or not) for the line at bin ``k`` of the shifted
    spectrum ``P`` and its mirror at bin ``j``: see IMAGE_DB."""
    n = len(P)
    a = P[max(0, j - half):min(n, j + half + 1)]
    jm = max(0, j - half) + int(np.argmax(a))
    over = 10 * math.log10(P[jm] / P[k]) if P[k] > 0 and P[jm] > 0 else 0.0
    if over < IMAGE_DB:
        return over, False
    # The mirror's neighbourhood, flipped and taken down to the line's level, against the line's.
    d = np.arange(-IMAGE_HALF, IMAGE_HALF + 1)
    ok = (k + d >= 0) & (k + d < n) & (jm - d >= 0) & (jm - d < n)
    line = P[k + d[ok]]
    pred = P[jm - d[ok]] * (P[k] / P[jm])
    shown = (line >= 2 * floor) | (pred >= 2 * floor)
    if not shown.any():
        return over, False
    fit = np.abs(10 * np.log10((line[shown] + floor) / (pred[shown] + floor))) <= IMAGE_FIT_DB
    return over, bool(np.mean(fit) >= IMAGE_FIT_SHARE)


def _beside(P, floor, bin_hz, origin, centre, bw99, center_hz, inband):
    """Lines of the fine spectrum ``P`` (shifted) outside the occupied band, each with its
    mirror about the tuned centre: see BESIDE_MARGIN."""
    n = len(P)
    f = origin + (np.arange(n) - n // 2) * bin_hz
    out_band = np.abs(f - centre) > BESIDE_MARGIN * bw99 / 2 + 3 * bin_hz
    out_band &= np.abs(f - center_hz) > BESIDE_DC_BINS * bin_hz
    out_band &= np.abs(f - origin) <= BESIDE_SPAN * n * bin_hz / 2
    if inband is not None:
        out_band &= inband
    w = 2 * BESIDE_LOCAL + 1
    if n < 4 * w or not out_band.any():
        return []
    padded = np.concatenate([P[BESIDE_LOCAL:0:-1], P, P[-2:-BESIDE_LOCAL - 2:-1]])
    windows = np.lib.stride_tricks.sliding_window_view(padded, w)
    local = np.median(windows, axis=1)
    # The signal's skirt: about its peak, while the neighbourhood's strongest stays within
    # BESIDE_SKIRT_DB of it.
    top = windows.max(axis=1) >= float(P.max()) * 10 ** (-BESIDE_SKIRT_DB / 10)
    pk = int(np.argmax(P))
    lo_s = pk - (int(np.argmin(top[pk::-1])) if not top[pk::-1].all() else pk + 1) + 1
    hi_s = pk + (int(np.argmin(top[pk:])) if not top[pk:].all() else n - pk) - 1
    out_band[max(0, lo_s):hi_s + 1] = False
    ref = np.maximum(local, floor)
    level = 10 * np.log10(np.maximum(P, 1e-300) / ref)
    k = np.flatnonzero((P[1:-1] > P[:-2]) & (P[1:-1] >= P[2:])) + 1
    k = k[out_band[k] & (level[k] >= BESIDE_DB)]
    # A line with its twin at its mirror about the signal's centre (within a few bins and
    # BESIDE_PAIR_DB) is the signal's own sideband: a tone's FM lines, OOK's keying lines.
    pivot = (centre - origin) / bin_hz + n // 2
    lines, taken = [], []
    for c in k[np.argsort(-P[k], kind='stable')]:
        if any(lo - TONE_GAP <= c <= hi + TONE_GAP for lo, hi in taken):
            continue
        lo, hi = _run_width(P, int(c), 0.25)
        taken.append((lo, hi))
        if hi - lo + 1 > BESIDE_MAX_BINS:
            continue
        t = int(round(2 * pivot - c))
        if 0 <= t < n:
            twin = float(P[max(0, t - BESIDE_DC_BINS):t + BESIDE_DC_BINS + 1].max())
            if abs(10 * math.log10(max(twin, 1e-300) / P[c])) <= BESIDE_PAIR_DB:
                continue
        fhz = float(f[c])
        j = int(round((2 * center_hz - fhz - origin) / bin_hz)) + n // 2
        line = {'freq_hz': fhz, 'level_db': float(10 * math.log10(P[c] / floor)),
                'width_hz': float((hi - lo + 1) * bin_hz), 'mirror_hz': 2 * center_hz - fhz,
                'mirror_db': None, 'image': None}
        if 0 <= j < n and (inband is None or inband[j]):
            line['mirror_db'], line['image'] = _image_check(P, floor, int(c), j, max(2, (hi - lo) // 2 + 1))
        lines.append(line)
        if len(lines) == MAX_BESIDE:
            break
    return lines


def _beside_evidence(lines):
    """One sentence on the lines beside the signal, or None."""
    if not lines:
        return None
    parts = []
    for b in lines[:4]:
        where = f"{b['freq_hz'] / 1e6:.6f} MHz, {b['level_db']:.0f} dB over the floor, {b['width_hz'] / 1e3:.3g} kHz wide"
        if b['image'] is None:
            parts.append(f"{where} (its mirror is outside the band read: a signal until shown otherwise)")
        elif b['image']:
            parts.append(f"{where}: the image of its mirror at {b['mirror_hz'] / 1e6:.6f} MHz "
                         f"({b['mirror_db']:.0f} dB stronger, the same shape)")
        else:
            parts.append(f"{where}: not an image (its mirror at {b['mirror_hz'] / 1e6:.6f} MHz does not "
                         "match): a signal until shown otherwise")
    more = f" and {len(lines) - 4} more" if len(lines) > 4 else ""
    return "Beside the signal: " + '; '.join(parts) + more + "."


# ---- programme audio (am and fm cards) ---------------------------------------

def _audio_stream(chunks, rate_in, kind, half_band):
    """The demodulated audio of ``chunks`` (complex, at ``rate_in``, ``half_band`` either side
    of 0 Hz): ``(audio, rate, cutoff)``, the envelope (``kind`` 'am') or the frequency in Hz
    ('fm'), cut by overlap-save at AUDIO_BAND_HZ or AUDIO_EDGE of the band taken, and
    decimated to AUDIO_RATE or a little over. Read once, in order."""
    dec = max(1, int(rate_in // AUDIO_RATE))
    rate_a = rate_in / dec
    cutoff = min(AUDIO_BAND_HZ, 0.45 * rate_a, AUDIO_EDGE * half_band)
    L, P = AUDIO_BLOCK, AUDIO_PAD
    # A cosine taper of AUDIO_TAPER_HZ past the cut, under the new rate's half: a hard mask
    # rings past the overlap and leaves a small comb at the block rate.
    f = np.fft.rfftfreq(L, 1 / rate_in)
    taper = min(AUDIO_TAPER_HZ, rate_a / 2 - cutoff)
    keep = np.where(f <= cutoff, 1.0, np.where(f < cutoff + taper,
                                               0.5 * (1 + np.cos(np.pi * (f - cutoff) / max(taper, 1e-9))), 0.0))
    out = []
    buf = np.zeros(P, np.float64)          # P zeros ahead of the stream's first sample
    start = -P                             # the stream index of buf[0]
    last = np.empty(0, np.complex64)
    total = 0

    def blocks(final):
        nonlocal buf, start
        while len(buf) >= L or (final and len(buf) > 2 * P):
            blk = buf[:L] if len(buf) >= L else np.concatenate([buf, np.zeros(L - len(buf))])
            y = np.fft.irfft(np.fft.rfft(blk) * keep, L)
            g0 = start + P
            g1 = min(start + L - P, total) if final else start + L - P
            first = max(0, -(-g0 // dec) * dec)
            idx = np.arange(first, g1, dec)
            out.append(y[idx - start])
            buf, start = buf[L - 2 * P:], start + L - 2 * P
            if final and g1 >= total:
                break

    for x in chunks:
        if kind == 'fm':
            s = np.concatenate([last, x]) if last.size else x
            v = np.angle(s[1:] * np.conj(s[:-1])).astype(np.float64) * (rate_in / (2 * np.pi))
            if not last.size and len(x):
                v = np.concatenate([[v[0] if v.size else 0.0], v])
            last = x[-1:] if len(x) else last
        else:
            v = np.abs(x).astype(np.float64)
        total += len(v)
        buf = np.concatenate([buf, v])
        blocks(False)
    buf = np.concatenate([buf, np.zeros(P)])
    blocks(True)
    a = np.concatenate(out) if out else np.empty(0)
    return a - (float(np.mean(a)) if a.size else 0.0), rate_a, cutoff


def _frames(a, nfft):
    """Hann-windowed power spectra of ``a`` in whole frames of ``nfft`` (frames x bins)."""
    m = len(a) // nfft
    if m < 1:
        return np.empty((0, nfft // 2 + 1))
    seg = a[:m * nfft].reshape(m, nfft) * np.hanning(nfft)
    return np.abs(np.fft.rfft(seg, axis=1)) ** 2


def _running_median(P, half):
    """The median of ``2 half + 1`` bins about each, along the last axis (ends reflected)."""
    w = 2 * half + 1
    padded = np.concatenate([P[..., half:0:-1], P, P[..., -2:-half - 2:-1]], axis=-1)
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, w, axis=-1), axis=-1)


def _audio_lines(a, rate_a, cutoff):
    """Steady or keyed lines in the audio: see AUDIO_LINE_DB."""
    nfft = 1 << int(round(math.log2(rate_a / AUDIO_LINE_BIN_HZ)))
    S = _frames(a, nfft)
    nfft_s = 1 << int(round(math.log2(rate_a / AUDIO_FRAME_HZ)))
    Ss = _frames(a, nfft_s)
    if len(S) < 2 or len(Ss) < 8:
        return []
    # Steady lines from the long average; keyed ones from the short frames (AUDIO_KEYED_DB).
    P = S.mean(axis=0)
    bin_hz, bin_s = rate_a / nfft, rate_a / nfft_s
    ref = np.maximum(_running_median(P, max(2, int(round(AUDIO_LOCAL_HZ / bin_hz)))), 1e-300)
    level = 10 * np.log10(np.maximum(P, 1e-300) / ref)
    f = np.arange(len(P)) * bin_hz
    k = np.flatnonzero((P[1:-1] > P[:-2]) & (P[1:-1] >= P[2:])) + 1
    k = k[(f[k] >= AUDIO_LINE_LO_HZ) & (f[k] <= 0.95 * cutoff) & (level[k] >= AUDIO_LINE_DB)]
    half_s = max(2, int(round(AUDIO_LOCAL_HZ / bin_s)))
    local = np.maximum(_running_median(Ss, half_s), 1e-300)
    peaks = np.zeros(Ss.shape, bool)
    peaks[:, 1:-1] = (Ss[:, 1:-1] > Ss[:, :-2]) & (Ss[:, 1:-1] >= Ss[:, 2:])
    hit = peaks & (Ss >= 10 ** (AUDIO_KEYED_DB / 10) * local)
    near = hit.copy()
    near[:, 1:] |= hit[:, :-1]
    near[:, :-1] |= hit[:, 1:]
    share = near.mean(axis=0)
    fs_ = np.arange(Ss.shape[1]) * bin_s
    ks = np.flatnonzero((share >= max(AUDIO_KEYED_SHARE, 5 / len(Ss))) & (fs_ >= AUDIO_LINE_LO_HZ)
                        & (fs_ <= 0.95 * cutoff))
    cands = [(float(level[c]), float(f[c]), True) for c in k]
    raw = hit.mean(axis=0)
    # Neighbouring bins that pass are one line, at the bin its peaks fall in most often.
    for run in np.split(ks, np.flatnonzero(np.diff(ks) > 1) + 1) if ks.size else []:
        c = int(run[np.argmax(raw[run])])
        if not any(abs(fc - fs_[c]) <= 1.5 * bin_s for _, fc, _ in cands):
            on = near[:, c]
            cands.append((10 * math.log10(float(np.median(Ss[on, c] / local[on, c]))), float(fs_[c]), False))
    lines, chosen = [], []
    for lev, fc, steady in sorted(cands, key=lambda x: -x[0]):
        if any(abs(fc - o) <= 1.5 * bin_s for o in chosen):
            continue
        if sum(abs(fc - o) <= AUDIO_CLUSTER_HZ for _, o, _ in cands) - 1 >= AUDIO_CLUSTER:
            continue
        c = int(round(fc / bin_hz))
        if not steady:
            # Its frequency from the long average's finer bins, within the frame's bin.
            w = max(1, int(round(bin_s / bin_hz)))
            c = max(1, c - w) + int(np.argmax(P[max(1, c - w):c + w + 1]))
        if c + 1 < len(P):
            if steady:
                lo, hi = _run_width(P, c, 0.25)
                if (hi - lo + 1) * bin_hz > AUDIO_LINE_MAX_HZ:
                    continue
            lp = np.log(np.maximum(P[c - 1:c + 2], 1e-300))
            den = lp[0] - 2 * lp[1] + lp[2]
            d = 0.5 * (lp[0] - lp[2]) / den if den < 0 else 0.0
            fc = (c + min(max(d, -0.5), 0.5)) * bin_hz
        chosen.append(fc)
        # On in a frame: the line's peak there. Off where it would have shown (its usual level
        # over the frame's median beside it) and does not: keyed, not hidden by the programme.
        j = int(round(fc / bin_s))
        cols = slice(max(0, j - 1), j + 2)
        on = near[:, j]
        held = Ss[on, cols].max(axis=1)
        power = float(np.median(held)) if on.any() else 0.0
        off_visible = (~on) & (power >= 10 ** (AUDIO_KEYED_DB / 10) * local[:, j])
        spread = float(np.diff(10 * np.log10(np.percentile(held, [10, 90])))[0]) if held.size >= 2 else math.inf
        keyed = None
        if held.size >= AUDIO_KEYED_MIN:
            keyed = bool(np.mean(off_visible) >= AUDIO_KEYED_SHARE and spread <= AUDIO_KEYED_SPREAD_DB)
        lines.append({'freq_hz': float(fc), 'level_db': float(lev), 'keyed': keyed, 'duty': float(np.mean(on))})
        if len(lines) == MAX_AUDIO_LINES:
            break
    return lines


def _dtmf(a, rate_a):
    """DTMF digits in the audio: [{digit, t_s, duration_s}], see DTMF_SEP_DB."""
    N = int(round(DTMF_FRAME_S * rate_a))
    hop = int(round(DTMF_HOP_S * rate_a))
    if len(a) < N + hop:
        return []
    starts = np.arange(0, len(a) - N + 1, hop)
    w = np.hanning(N)
    freqs = np.array(DTMF_LOW + DTMF_HIGH)
    basis = np.exp(-2j * np.pi * np.outer(freqs, np.arange(N)) / rate_a) * w
    frames = np.lib.stride_tricks.sliding_window_view(a, N)[starts]
    X = frames @ basis.T
    # A tone of amplitude A gives |X| = A sum(w) / 2; its power A^2 / 2 is 2 |X|^2 / sum(w)^2.
    p = 2 * np.abs(X) ** 2 / np.sum(w) ** 2
    power = (frames ** 2 * w ** 2).sum(axis=1) / np.sum(w ** 2)
    sep = 10 ** (DTMF_SEP_DB / 10)
    digits = []
    for g in range(len(starts)):
        lo, hi = p[g, :4], p[g, 4:]
        il, ih = int(np.argmax(lo)), int(np.argmax(hi))
        ok = (lo[il] > 0 and hi[ih] > 0
              and lo[il] >= sep * np.max(np.delete(lo, il)) and hi[ih] >= sep * np.max(np.delete(hi, ih))
              and abs(10 * math.log10(lo[il] / hi[ih])) <= DTMF_TWIST_DB
              and lo[il] + hi[ih] >= DTMF_SHARE * power[g])
        digits.append(DTMF_KEYS[il][ih] if ok else None)
    out, g = [], 0
    while g < len(digits):
        if digits[g] is None:
            g += 1
            continue
        e = g
        while e + 1 < len(digits) and digits[e + 1] == digits[g]:
            e += 1
        if e - g + 1 >= DTMF_MIN_FRAMES:
            length = (e - g + 1) * hop
            middle = (starts[g] + starts[e] + N) / 2
            out.append({'digit': digits[g], 't_s': float((middle - length / 2) / rate_a),
                        'duration_s': float(length / rate_a)})
        g = e + 1
    return out


def _loop(a, rate_a, cutoff):
    """(varies, the seconds after which the programme repeats or None): see AUDIO_LOOP_CORR."""
    nfft = 1 << int(round(math.log2(rate_a / AUDIO_FRAME_HZ)))
    S = _frames(a, nfft)
    frame_s = nfft / rate_a
    if len(S) * frame_s < AUDIO_LOOP_MIN_S + AUDIO_LOOP_OVERLAP_S:
        return False, None
    f = np.arange(S.shape[1]) * rate_a / nfft
    edges = np.geomspace(100.0, cutoff, AUDIO_LOOP_BANDS + 1)
    E = np.stack([S[:, (f >= lo) & (f < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
    E = np.log10(np.maximum(E, 1e-30))
    spread = E.std(axis=0)
    varies = bool(np.median(spread) >= AUDIO_VARIES)
    if not varies:
        return False, None
    z = (E - E.mean(axis=0)) / np.where(spread > 0, spread, 1.0)
    T = len(z)
    m = 1 << (2 * T - 1).bit_length()
    Z = np.fft.rfft(z, m, axis=0)
    corr = np.fft.irfft((Z * np.conj(Z)).sum(axis=1), m)[:T] / (z.shape[1] * (T - np.arange(T)))
    first = int(math.ceil(AUDIO_LOOP_MIN_S / frame_s))
    last = T - int(math.ceil(AUDIO_LOOP_OVERLAP_S / frame_s))
    for L in range(first, last + 1):
        if corr[L] >= AUDIO_LOOP_CORR and corr[L] >= corr[L - 1] and (L + 1 >= T or corr[L] >= corr[L + 1]):
            return True, float(L * frame_s)
    return True, None


def _programme(read, n, rate, center_hz, card, band=None):
    """``(audio section, evidence, warnings)`` of an ``am`` or ``fm`` card: its occupied band
    taken again and demodulated, within ``band`` when one was given. See AUDIO_REACH."""
    kind = card['class']
    centre, bw99 = card['occupied']['centre_hz'], card['occupied']['bandwidth_hz']
    reach = max(AUDIO_REACH * bw99, AUDIO_MIN_HZ) / 2
    c = centre - center_hz
    edge_lo, edge_hi = (-rate / 2, rate / 2) if band is None else _band_offsets(band, center_hz, rate)
    lo, hi = max(c - reach, edge_lo), min(c + reach, edge_hi)
    # A skirt half the band's width either side (the joins of a narrow one left a comb at
    # rate / step), kept inside the span: past its edge it would not wrap.
    skirt = max(0.0, min((hi - lo) / 2, lo + rate / 2, rate / 2 - hi))
    dec, chunks = _decimated(read, n, rate, lo, hi, 2 * AUDIO_RATE, skirt)
    a, rate_a, cutoff = _audio_stream(chunks, rate / dec, kind, (hi - lo) / 2)
    span_s = len(a) / rate_a
    lines = _audio_lines(a, rate_a, cutoff)
    dtmf = _dtmf(a, rate_a)
    varies, loop_s = _loop(a, rate_a, cutoff)
    audio = {'source': 'envelope' if kind == 'am' else 'frequency', 'rate_hz': rate_a,
             'band_hz': cutoff, 'lines': lines, 'dtmf': dtmf, 'varies': varies, 'loop_s': loop_s}
    ev, warn = [], []
    if lines:
        ev.append(f"Lines in the {kind.upper()} audio: " + ', '.join(
            f"{x['freq_hz']:.0f} Hz ({x['level_db']:.0f} dB over the audio beside it"
            + (f", keyed on and off, on {x['duty']:.0%} of the time)" if x['keyed']
               else ", steady)" if x['keyed'] is False else ")")
            for x in lines) + ".")
    if dtmf:
        ev.append(f"DTMF in the audio: {' '.join(d['digit'] for d in dtmf)} "
                  f"({len(dtmf)} digit{'s' if len(dtmf) > 1 else ''}, from {dtmf[0]['t_s']:.2f} s).")
    if loop_s:
        ev.append(f"The programme repeats every {loop_s:.1f} s.")
        if span_s < 2 * loop_s:
            warn.append(f"The span holds under two repeats of the programme ({span_s:.1f} s of a "
                        f"{loop_s:.1f} s loop): what is sent once a loop may be missing; read a longer span.")
    elif varies:
        ev.append(f"The programme does not repeat within the {span_s:.1f} s read: a loop, if it is "
                  "one, is longer, and what is sent once a loop may be outside the span.")
    return audio, ev, warn


def _warnings(card, span_s):
    """What the card cannot see in a span this short."""
    out = []
    count, duty = card['bursts']['count'], card['bursts']['duty']
    # One burst that fills the span is a continuous signal, not a burst.
    if card['envelope']['keyed'] and count in (1, 2) and duty is not None and duty < BURSTS_FILL:
        out.append(f"Only {count} burst{'s' if count > 1 else ''} in the {span_s:.3g} s read: "
                   "read a longer span to see whether, and how often, it repeats.")
    return out


def _finish(card, read, n, rate, center_hz, recording_s=None, band=None):
    """The programme audio, the lines' evidence and the warnings, on a card ``_analyse`` gave."""
    span_s = n / rate
    card['audio'] = None
    card['warnings'] = _warnings(card, span_s)
    beside = _beside_evidence(card['beside'])
    if card['class'] in ('am', 'fm') and card['occupied']['bandwidth_hz'] <= AUDIO_MAX_WIDTH_HZ:
        card['audio'], ev, warn = _programme(read, n, rate, center_hz, card, band)
        card['evidence'].extend(ev)
        card['warnings'].extend(warn)
    if beside:
        card['evidence'].append(beside)
    if recording_s is not None and recording_s > span_s + 1.0:
        card['warnings'].append(f"The recording runs {recording_s:.1f} s and {span_s:.1f} s of it was read "
                                "(t0 and t1 choose the span).")
    return card


def _broadcast(seg, rate, subcarriers):
    """An FM broadcast's multiplex, when a pilot is among ``subcarriers``: the card's
    ``broadcast`` section {pilot_hz, pilot_db, rds, l_minus_r_db, stereo}, or None."""
    pilot = next((s for s in subcarriers if abs(s['freq_hz'] - 19e3) <= BROADCAST_NEAR_HZ), None)
    if pilot is None or rate < 2 * 53e3 or len(seg) < 64:
        return None
    d = np.angle(seg[1:] * np.conj(seg[:-1])).astype(np.float64)
    f, p = _spectrum(d, rate)
    ref = ((f >= 16e3) & (f <= 18.5e3)) | ((f >= 19.5e3) & (f <= 22e3))
    band = ((f >= 23e3) & (f <= 37.5e3)) | ((f >= 38.5e3) & (f <= 53e3))
    noise = float(p[ref].sum()) / max(float((f[ref] ** 2).sum()), 1e-300) * float((f[band] ** 2).sum())
    lr = 10 * math.log10(max(float(p[band].sum()), 1e-300) / max(noise, 1e-300))
    return {'pilot_hz': pilot['freq_hz'], 'pilot_db': pilot['level_db'],
            'rds': any(abs(s['freq_hz'] - 57e3) <= BROADCAST_NEAR_HZ * 5 for s in subcarriers),
            'l_minus_r_db': lr, 'stereo': lr >= BROADCAST_STEREO_DB}


def _subcarriers(seg, rate, low=SUBCARRIER_MIN_HZ, high=None, carried=True):
    """Lines in the demodulated frequency between ``low`` and ``high`` (default 0.45 of
    the rate): [{freq_hz, level_db, share_db, peak_hz, carries, sidebands_db}], strongest
    first, a line's sidebands grouped with it. An FM broadcast's pilot and RDS, a
    subcarrier's centre, TV sound in FM video (whose wide FM ``carried`` leaves unread)."""
    high = 0.45 * rate if high is None else min(high, 0.45 * rate)
    if len(seg) < 64 or high <= low:
        return []
    d = np.angle(seg[1:] * np.conj(seg[:-1])).astype(np.float64)
    freqs, power = _spectrum(d, rate)
    band = (freqs >= low) & (freqs <= high)
    if not band.any():
        return []
    floor = max(float(np.median(power[band])), 1e-300)
    level = 10 * np.log10(np.maximum(power, 1e-300) / floor)
    k = np.flatnonzero(band[1:-1] & (power[1:-1] > power[:-2]) & (power[1:-1] >= power[2:])) + 1
    k = k[level[k] >= SUBCARRIER_DB]
    groups = np.split(k, np.flatnonzero(np.diff(freqs[k]) > SUBCARRIER_GROUP_HZ) + 1) if k.size else []
    total = max(float(power[freqs >= 20].sum()), 1e-300)
    out = []
    for g in groups:
        f = float(np.sum(freqs[g] * power[g]) / np.sum(power[g]))
        share = 10 * math.log10(max(float(power[np.abs(freqs - f) <= SUBCARRIER_GROUP_HZ / 2].sum()), 1e-300) / total)
        if share >= SUBCARRIER_SHARE_DB:
            out.append({'freq_hz': f, 'level_db': float(level[g].max()), 'share_db': share,
                        'peak_hz': float(freqs[g[int(np.argmax(power[g]))]])})
    out.sort(key=lambda s: -s['level_db'])
    out = out[:MAX_SUBCARRIERS]
    spectrum = np.fft.rfft(d) if out and carried else None
    # Sideband powers from a windowed spectrum: a subcarrier's carrier can hold nearly all of
    # its power (98.6 % on a bench USB subcarrier), and unwindowed its leakage filled both
    # sides alike. The baseband for FM's test is taken unwindowed, as a window shapes it.
    windowed = np.fft.rfft(d * np.hanning(len(d))) if out and carried else None
    for s in out:
        s.update(_carried(spectrum, windowed, rate, len(d), s['peak_hz'], s['freq_hz']) if carried
                 else {'carries': None, 'sidebands_db': None})
    return out


def _about(spectrum, rate, n, f0):
    """The bins within SUB_HALF_HZ of ``f0``: (bins, offsets in Hz, power upper, lower, carrier),
    or None at the spectrum's edge. The sidebands' powers have the noise taken off: its
    density, as a discriminator's rises with f squared, from the bins SUB_HALF_HZ to
    SUB_NOISE_HZ out (a weak sideband over noise read 9 dB lopsided with it, 15 or more
    without)."""
    df = rate / n
    i0 = int(round(f0 / df))
    half = int(SUB_HALF_HZ / df)
    if i0 - half < 1 or i0 + half >= spectrum.size:
        return None
    band = spectrum[i0 - half:i0 + half + 1]
    off = (np.arange(band.size) - half) * df
    p = np.abs(band) ** 2
    up, lo = off > SUB_CARRIER_HZ, off < -SUB_CARRIER_HZ
    upper, lower = float(p[up].sum()), float(p[lo].sum())
    reach = int(SUB_NOISE_HZ / df)
    if i0 - reach >= 1 and i0 + reach < spectrum.size:
        idx = np.concatenate([np.arange(i0 - reach, i0 - half), np.arange(i0 + half + 1, i0 + reach + 1)])
        f_out = idx * df
        k = float(np.median(np.abs(spectrum[idx]) ** 2 / f_out ** 2))
        f_in = f0 + off
        upper = max(upper - k * float((f_in[up] ** 2).sum()), 1e-300)
        lower = max(lower - k * float((f_in[lo] ** 2).sum()), 1e-300)
    return band, off, upper, lower, float(p[np.abs(off) <= SUB_CARRIER_HZ].sum())


def _carried(spectrum, windowed, rate, n, peak, centre):
    """What a subcarrier carries: {carries, sidebands_db}. ``carries`` is 'usb' or 'lsb' (its
    strongest line a carrier, SUB_CARRIER_SHARE of the power or more, with the sidebands on
    one side of it), else, about its power-weighted ``centre``, 'fm', 'am' or 'dsb'; None for
    a pilot, RDS's 57 kHz and a subcarrier at the edge. ``sidebands_db`` is the upper's power
    over the lower's about the reference taken."""
    none = {'carries': None, 'sidebands_db': None}
    # RDS's strongest line is one of its humps, 2.4 kHz off 57 kHz (a real station's read 'dsb').
    if any(abs(f - 19e3) <= BROADCAST_NEAR_HZ or abs(f - 57e3) <= 5 * BROADCAST_NEAR_HZ for f in (peak, centre)):
        return none
    at = _about(windowed, rate, n, peak)
    if at is None:
        return none
    _, _, upper, lower, car = at
    sidebands = 10 * math.log10(max(upper, 1e-300) / max(lower, 1e-300))
    # DSB by a tone has two equal lines, either side of its centre: about one of them all the
    # rest is on one side (stereo's L-R read USB). A line at the strongest's mirror is DSB.
    df = rate / n
    mirror = 2 * centre - peak
    pw = np.abs(windowed) ** 2
    near = lambda f: float(pw[max(0, int(round(f / df)) - 2):int(round(f / df)) + 3].max())
    paired = abs(mirror - peak) > SUB_CARRIER_HZ and near(mirror) >= SUB_MIRROR * near(peak)
    if abs(sidebands) >= SUB_SSB_DB and car >= SUB_CARRIER_SHARE * (upper + lower + car) and not paired:
        return {'carries': 'usb' if sidebands > 0 else 'lsb', 'sidebands_db': sidebands}
    at = _about(spectrum, rate, n, centre)
    if at is None:
        return none
    band, off, _, _, _ = at
    _, _, upper, lower, car = _about(windowed, rate, n, centre)
    sidebands = 10 * math.log10(max(upper, 1e-300) / max(lower, 1e-300))
    # The baseband: the bins about the centre brought to 0 Hz, a few times oversampled.
    size = 1 << int(4 * band.size - 1).bit_length()
    z = np.zeros(size, complex)
    z[np.round(off / df).astype(np.int64) % size] = band
    z = np.fft.ifft(z)
    a = np.abs(z)
    cv = float(a.std() / a.mean()) if a.mean() > 0 else 0.0
    spread = float(np.std(np.angle(z[1:] * np.conj(z[:-1])))) * size * df / (2 * np.pi)
    if cv < SUB_FM_CV and spread >= SUB_FM_HZ:
        kind = 'fm'
    elif car >= SUB_CARRIER_SHARE * (upper + lower + car):
        kind = 'am'
    else:
        kind = 'dsb'
    return {'carries': kind, 'sidebands_db': sidebands}


# ---- the measurement ---------------------------------------------------------

def _db(x):
    return 10.0 * math.log10(x) if x > 0 else None


def _clean(v):
    """A JSON-safe copy: plain Python types, floats to 6 significant digits."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        x = float(v)
        return float(f'{x:.6g}') if math.isfinite(x) else None
    return v


def _band_offsets(band, centre_hz, rate):
    """``band`` (absolute Hz) as offsets from the centre, clipped to the recording."""
    f_lo, f_hi = float(band[0]) - centre_hz, float(band[1]) - centre_hz
    if not f_lo < f_hi:
        raise IQSourceError("band=(f_lo, f_hi) needs f_lo < f_hi.")
    lo, hi = max(f_lo, -rate / 2), min(f_hi, rate / 2)
    if not hi > lo:
        raise IQSourceError(f"the band {band[0]:.6g} to {band[1]:.6g} Hz is outside the recording.")
    return lo, hi


def _banded(read, n, rate, lo, hi, dec, skirt_hz=0.0):
    """The band lo..hi (offsets from the centre) of the samples, brick-wall
    filtered by overlap-save in blocks (see the constants above), moved to 0 Hz
    and decimated by ``dec``. Outputs lie on multiples of ``dec``.

    A narrow band (the usual case) takes each output as a sum of the band's bins'
    phasors, so no inverse transform is taken at all; otherwise a block's inverse
    transform keeps the band's bins. The padding is widened for a narrow band (its
    ringing is longer) and the block with it, up to MAX_BLOCK. Outputs sit at the
    multiples of ``dec`` by sample index, whether or not a block holds a whole
    output step. The file is read once, in order: the overlap is carried over.

    ``skirt_hz`` widens the skirt (outside the band) to that, and the overlap with it: the
    filter's ringing then fits the overlap closely, so the block joins leave no comb (the
    programme audio's own pass; the other passes keep the narrow skirt and their results)."""
    fc = (lo + hi) / 2
    pad = int(min(max(PAD, math.ceil(RING_LENGTHS * rate / min(hi - lo, skirt_hz or hi - lo))), MAX_PAD))
    length = min(MAX_BLOCK, 1 << (max(FILTER_BLOCK, 4 * pad) - 1).bit_length())
    room = length - 2 * pad
    step = dec * (room // dec) if room >= dec else room
    f = np.fft.fftfreq(length, 1 / rate)
    # A cosine skirt of a few bins, outside the band: the passband stays flat,
    # and the shorter ringing fits the overlap, so the blocks agree at the splice.
    dist = np.maximum(lo - f, f - hi)
    skirt = max(SKIRT_BINS * rate / length, skirt_hz)
    weight = np.zeros(length, np.float64)
    weight[dist <= 0] = 1.0
    edge = (dist > 0) & (dist < skirt)
    weight[edge] = 0.5 * (1.0 + np.cos(np.pi * dist[edge] / skirt))
    keep = np.flatnonzero(weight > 0)
    aligned = step % dec == 0
    grid = pad + dec * np.arange(step // dec) if aligned else None
    phasors = None
    if aligned and keep.size * grid.size <= length * np.log2(length):
        # Output j is at local sample grid[j], mixed to fc by the phasor in the loop.
        phasors = np.exp(2j * np.pi * np.outer(grid, f[keep] - fc) / rate) / length
    buf, first, fed = np.empty(0, np.complex64), 0, 0     # buf holds samples first..fed
    for s in range(0, n, step):
        m = min(step, n - s)
        a, b = max(0, s - pad), min(n, s + m + pad)
        if fed < b:
            buf = np.concatenate([buf, read(fed, b - fed)])
            fed = b
        seg = np.zeros(length, np.complex64)
        pre = a - (s - pad)
        seg[pre:pre + (b - a)] = buf[a - first:b - first]
        spec = np.fft.fft(seg)
        if phasors is not None:
            count = -(-m // dec)
            bins = spec[keep] * weight[keep] * np.exp(2j * np.pi * ((-fc * (s - pad) / rate) % 1.0))
            # An elementwise sum, not a matrix product: a threaded BLAS costs ms on a call this small.
            y = (phasors[:count] * bins).sum(axis=1)
        else:
            ts = np.arange(-(-s // dec) * dec, s + m, dec)
            y = np.fft.ifft(spec * weight)[ts - (s - pad)]
            y = y * np.exp(-2j * np.pi * fc * (ts / rate))
        yield y.astype(np.complex64)
        nxt = max(0, s + step - pad)
        if nxt > first:
            buf, first = buf[nxt - first:], nxt


def _decimated(read, n, rate, lo, hi, min_rate=0.0, skirt_hz=0.0):
    """The band lo..hi (offsets from the centre) of ``n`` samples, decimated to at
    least twice its width (and its skirt) and to at least ``min_rate``:
    ``(decimation, chunks)``, as :func:`_banded` gives them."""
    dec = max(1, int(rate // max(2 * (hi - lo + 2 * skirt_hz) if skirt_hz else 2 * (hi - lo), min_rate)))
    return dec, _banded(read, n, rate, lo, hi, dec, skirt_hz)


def _cells(read, n, rate, center_hz, centre, bw99, station_hz):
    """The card's ``lte`` and ``nr`` sections: the cells in the span's first 50 ms.

    ``bw99`` None skips the width gate (``cells_file``: no occupied width is measured)."""
    def skipped(why):
        return {'searched': False, 'reason': why}

    if rate < CELLS_MIN_RATE:
        why = f"sampled at {rate / 1e6:.3g} MS/s, under {CELLS_MIN_RATE / 1e6:.3g} MS/s"
        return skipped(why), skipped(why)
    if bw99 is not None and bw99 < CELLS_MIN_WIDTH_HZ:
        why = "the occupied width is under 1 MHz"
        return skipped(why), skipped(why)
    from . import iqlte, iqnr
    iq = read(0, min(n, int(round(CELLS_SPAN_S * rate))))
    span_s = len(iq) / rate
    if station_hz is not None and abs(station_hz - center_hz) < rate / 2:
        block = float(station_hz)
    else:
        block = round(centre / LTE_RASTER_HZ) * LTE_RASTER_HZ
    fp = iqlte.fingerprint(iqlte.mix_to_dc(iq, rate, block - center_hz), rate)
    lte = {'searched': True, 'reason': None, 'span_s': span_s,
           'block_center_hz': block, 'verdict': fp.get('verdict'), 'pci': fp.get('pci'),
           'bandwidth_mhz': iqlte.BANDWIDTH_MHZ.get(fp.get('nfft')), 'duplex': fp.get('duplex'),
           'cells': [{'pci': c.get('pci'), 'relative_power_db': c.get('relative_power_db'),
                      'chance': c.get('chance'), 'duplex': c.get('duplex'),
                      'frame_offset_s': None if c.get('frame_offset_samples') is None
                      else c['frame_offset_samples'] / rate}
                     for c in fp.get('cells') or [] if iqlte.crs_confirms(c)]}
    ssbs = iqnr.ssb_search(iq, rate, center_hz)
    nr = {'searched': True, 'reason': None, 'span_s': span_s,
          'ssbs': [{k: e.get(k) for k in ('ssb_hz', 'gscn', 'scs_khz', 'pci', 'ssb_index',
                                          'period_ms', 'first_ssb_s', 'relative_power_db',
                                          'chance')} for e in ssbs]}
    return lte, nr


def _analyse(read, n, rate, center_hz, full_scale_dbm, band, station_hz=None, block=BLOCK, cells=None):
    """The card's sections for ``n`` samples from ``read(start, count)``.

    Two passes. The first, over the band (or the whole span), gives the level,
    the spectra, the tones and the occupied centre and width. The second shifts
    the signal to that centre, band-limits it to max(3 x width, 20 kHz) and
    decimates it, and only then takes the envelope, the frequency, the bursts and
    the symbol rate: a tone in a wide band is judged on its own band, not on the
    noise of the whole span.
    """
    if band is None:
        dec, base, half_band = 1, 0.0, None
        chunks = (read(s, min(CHUNK, n - s)) for s in range(0, n, CHUNK))
    else:
        lo, hi = _band_offsets(band, center_hz, rate)
        dec, chunks = _decimated(read, n, rate, lo, hi)
        base, half_band = (lo + hi) / 2, (hi - lo) / 2
    rate_1 = rate / dec
    n_1 = -(-n // dec)
    if n_1 < 64:
        raise IQSourceError(f"the band leaves {n_1} samples after decimation: too few to measure.")
    scan = _Scan(min(COARSE_NFFT, _pow2_floor(n_1)),
                 min(FINE_NFFT, _pow2_floor(max(n_1 // 4, 64))))
    for x in chunks:
        scan.feed(x)

    origin = center_hz + base
    total = scan.power / scan.count
    Pc = scan.coarse.spectrum()
    segs = scan.coarse.segments
    inband = None
    if half_band is not None:
        inband = np.abs((np.arange(len(Pc)) - len(Pc) // 2) * rate_1 / len(Pc)) <= half_band
    floor = max(_floor(Pc, segs, inband), 1e-300)
    snr = 10 * math.log10(max((total - floor) / floor, 1e-6))
    power_dbfs = _db(total)
    tones = _tones(Pc, floor, rate_1 / len(Pc), origin)

    # The occupied width is the signal's: the noise floor is taken off first,
    # or the flat noise would use up the 1 % outside the 99 % band.
    Pf = scan.fine.spectrum()
    signal = np.maximum(Pf - floor, 0.0)
    centre, bw99, bw20 = _occupied(signal if signal.sum() > 0 else Pf,
                                   rate_1 / len(Pf), origin)
    inband_f = None
    if half_band is not None:
        inband_f = np.abs((np.arange(len(Pf)) - len(Pf) // 2) * rate_1 / len(Pf)) <= half_band
    beside = _beside(Pf, floor, rate_1 / len(Pf), origin, centre, bw99, center_hz, inband_f)

    # The second pass: the signal at its occupied centre, within the first pass's band.
    half_span = rate / 2 if half_band is None else half_band
    reach = max(TRACK_WIDTH * bw99, TRACK_MIN_HZ) / 2
    c = centre - center_hz
    lo_s, hi_s = max(c - reach, base - half_span), min(c + reach, base + half_span)
    base_s = (lo_s + hi_s) / 2
    dec_s, chunks = _decimated(read, n, rate, lo_s, hi_s, TRACK_RATE)
    rate_s = rate / dec_s
    n_s = -(-n // dec_s)
    if n_s < 64:
        raise IQSourceError(f"the band leaves {n_s} samples after decimation: too few to measure.")
    track = _Track(block)
    for x in chunks:
        track.feed(x)
    env = np.concatenate(track.env).astype(np.float64)
    prod = np.concatenate(track.prod)
    fs_b = rate_s / block
    span_s = len(env) / fs_b
    # The angle of a block's summed products is the phase step per sample.
    track16 = np.angle(prod).astype(np.float64) * (rate_s / (2 * np.pi))

    # Bursts: the blocks above a tenth of the peak level, with the gaps inside
    # a burst kept in it. The levels are those inside the bursts.
    active = env > 0.1 * float(np.percentile(env, 99.5))
    burst_card, in_burst = _bursts(active, fs_b, span_s)
    keyed_env, env_lo, env_hi, env_thr = _levels(env[in_burst]) if in_burst.any() \
        else (False, None, None, None)
    if keyed_env and env_hi - env_lo < KEYED_DEPTH * (env_hi + env_lo):
        keyed_env = False
    on = in_burst if keyed_env else np.ones(env.size, dtype=bool)
    env_on = env[on]
    mean_on = float(np.mean(env_on))
    cv = float(np.std(env_on) / mean_on) if mean_on > 0 else 0.0
    strong = on & (env >= 0.5 * float(np.median(env_on)))

    k4 = len(env) // 4
    track64 = np.angle(prod[:4 * k4].reshape(-1, 4).sum(axis=1)).astype(np.float64) \
        * (rate_s / (2 * np.pi))
    f64 = track64[strong[:4 * k4].reshape(-1, 4).all(axis=1)]
    fstd = float(np.std(f64)) if f64.size else 0.0

    f16 = track16[strong]
    keyed_f, f_lo, f_hi, f_thr = _levels(f16)

    if keyed_env:
        labels = env >= env_thr
    elif keyed_f:
        labels = track16 >= f_thr
    else:
        labels = None
    sym = _symbol_rate(labels, fs_b) if labels is not None else None

    if keyed_env:
        bursts = burst_card
        env_levels = [env_lo, env_hi]
        depth = (env_hi - env_lo) / (env_hi + env_lo) if env_hi + env_lo > 0 else 0.0
    else:
        bursts = {'count': 0, 'durations_s': [], 'median_duration_s': None, 'gaps_s': [],
                  'repeat_interval_s': None, 'duty': None}
        p_lo, p_hi = np.percentile(env, [0.5, 99.5])
        env_levels = [float(np.median(env))]
        depth = (p_hi - p_lo) / (p_hi + p_lo) if p_hi + p_lo > 0 else 0.0

    if keyed_f:
        freq_levels = [f_lo + base_s, f_hi + base_s]
        deviation = (f_hi - f_lo) / 2
    else:
        freq_levels = [float(np.median(f16)) + base_s] if f16.size else []
        deviation = float((np.percentile(f64, 99.5) - np.percentile(f64, 0.5)) / 2) if f64.size else 0.0

    # The sample-level checks, on the start of the second pass at its own rate.
    seg = np.concatenate(track.seg) if track.seg else np.empty(0, np.complex64)
    atsc = _atsc(tones, centre, bw99)
    tv = None if atsc else _tv(seg, rate_s, bw99, cv)
    pulses = None if tv or atsc else _pulses(seg, rate_s)
    spikes = keyed_f and min(np.mean(f16 >= f_thr), np.mean(f16 < f_thr)) < PSK_SPIKES
    psk = None if atsc or tv or pulses or (keyed_f and not spikes) else _psk(seg, rate_s, base_s)
    if psk and keyed_env:
        # No carrier and a line in the M-th power is PSK, whatever its blocks' envelope: a
        # bench BPSK at 125 kBd (8 samples a symbol) left blocks of three envelope values,
        # two of which looked like ASK at 248 Bd. OOK and carrier-on ASK keep their carrier
        # line, so _psk passes them by.
        keyed_env, env_order = False, None
        bursts = {'count': 0, 'durations_s': [], 'median_duration_s': None, 'gaps_s': [],
                  'repeat_interval_s': None, 'duty': None}
        p_lo, p_hi = np.percentile(env, [0.5, 99.5])
        env_levels = [float(np.median(env))]
        depth = (p_hi - p_lo) / (p_hi + p_lo) if p_hi + p_lo > 0 else 0.0
        sym = None
    env_order = 2 if keyed_env else None
    freq_order = 2 if keyed_f else None
    fast = None
    isi = 0.0
    # The steps are taken after an average over half the occupied width's inverse: a symbol's
    # share or less (a symbol lasts about that or longer), and the clock line stands higher
    # (Gaussian 4FSK at 15 kHz deviation: 34 dB unsmoothed, 45 so).
    smooth = max(1, int(rate_s // (2 * max(bw99, 1.0))))
    if (keyed_env or keyed_f) and not (atsc or tv or pulses or psk) and len(seg) >= 64:
        # Keyed in blocks, but a symbol clock past their reach is the rate: the blocks'
        # own reading of it is an alias (a 20 kBd FSK read 12.5 kBd).
        head = seg[:LEVEL_SPAN + 1]
        v = np.abs(head).astype(np.float64) if keyed_env else \
            np.angle(head[1:] * np.conj(head[:-1])).astype(np.float64) * (rate_s / (2 * np.pi))
        c = _clock(v, rate_s, smooth)
        if c and c[1] >= LEVEL_CLOCK_DB and c[0] > fs_b / 4:
            sym = c[0]
    elif not (atsc or tv or pulses or psk or keyed_env or keyed_f) and len(seg) >= 64:
        head = seg[:LEVEL_SPAN + 1]
        a = np.abs(head).astype(np.float64)
        fr = np.angle(head[1:] * np.conj(head[:-1])).astype(np.float64) * (rate_s / (2 * np.pi))
        clocks = [_clock(a, rate_s, smooth), _clock(fr, rate_s, smooth)]
        # A clock the blocks cannot follow: read again in shorter blocks (below).
        strong = [c for c in clocks if c and c[1] >= LEVEL_CLOCK_DB and c[0] > fs_b / 4]
        fast = max(strong, key=lambda c: c[1])[0] if strong else None
        many = _keyed_levels(a, rate_s, smooth, clocks[0])
        if many and many['levels'][-1] - many['levels'][0] < KEYED_DEPTH * (
                many['levels'][-1] + many['levels'][0]):
            many = None
        if many:
            keyed_env, env_order, sym = True, many['order'], many['symbol_rate_baud']
            isi = many['neighbour_share']
            env_lo, env_hi = many['levels'][0], many['levels'][-1]
            bursts, env_levels = burst_card, many['levels']
            depth = (env_hi - env_lo) / (env_hi + env_lo) if env_hi + env_lo > 0 else 0.0
        elif cv < 0.1:
            many = _keyed_levels(fr, rate_s, smooth, clocks[1])
            if many:
                keyed_f, freq_order, sym = True, many['order'], many['symbol_rate_baud']
                isi = many['neighbour_share']
                f_lo, f_hi = many['levels'][0], many['levels'][-1]
                freq_levels = [f + base_s for f in many['levels']]
                deviation = (f_hi - f_lo) / 2
    if tv and tv['modulation'] == 'fm':
        # FM video: its sound subcarriers sit above the 4.2 MHz of the picture.
        subcarriers = _subcarriers(seg, rate_s, *TV_SOUND_HZ, carried=False)
    elif cv < 0.1 and fstd >= STEADY_HZ and not (keyed_f or pulses or psk):
        subcarriers = _subcarriers(seg, rate_s)
    else:
        subcarriers = []
    broadcast = _broadcast(seg, rate_s, subcarriers) if not tv else None
    if psk and psk['symbol_rate_baud']:
        sym = psk['symbol_rate_baud']
        # BPSK whose symbols take two amplitudes is 4-level ASK with the carrier suppressed
        # (bipolar levels): its envelope at the symbol clock has those two.
        psk['amplitude_levels'] = None
        if psk['order'] == 2:
            s2 = _symbols(_bipolar(seg[:LEVEL_SPAN], rate_s, psk['carrier_offset_hz'] - base_s), rate_s, sym)
            fit = _level_order(s2, (2, 4)) if s2 is not None else None
            if fit and fit[0] == 4:
                mags = np.sort(np.abs(np.asarray(fit[1])))
                psk['amplitude_levels'] = [float(mags[:2].mean()), float(mags[2:].mean())]
    elif psk:
        psk['amplitude_levels'] = None
    if tv and tv['modulation'] == 'am' and tones:
        vision = tones[0]['freq_hz']
        beside = [b for b in beside if not (vision - TV_VISION_LOW_HZ <= b['freq_hz']
                                            <= vision - TV_VISION_LOW_HZ + TV_CHANNEL_HZ[tv['system']])]
    elif tv:
        beside = [b for b in beside if abs(b['freq_hz'] - centre) > TV_SOUND_HZ[1]]
    elif atsc:
        beside = [b for b in beside if abs(b['freq_hz'] - atsc['channel_centre_hz']) > ATSC_CHANNEL_HZ / 2]
    if tv:
        tv.update(_video_swing(seg, rate_s) if tv['modulation'] == 'fm' else
                  {'deviation_hz': None, 'peak_to_peak_hz': None, 'low_peak_to_peak_hz': None,
                   'pre_emphasis': None})

    lte, nr = _cells(read, n, rate, center_hz, centre, bw99, station_hz) if cells is None else cells
    lte_pcis = []
    if lte.get('verdict') == 'lte':
        # Confirmed: CP, PSS and SSS agree, and the SSS gives a PCI.
        lte_pcis = [c['pci'] for c in lte['cells']] or ([lte['pci']] if lte['pci'] is not None else [])
    nr_pcis = [e['pci'] for e in nr.get('ssbs') or []]
    tone_n = len(tones)
    # A cell found is a detection at a chance of 1e-3 or less. It comes before the noise
    # rule, because a band full of carriers leaves no floor: the floor is the signal.
    if lte_pcis or nr_pcis:
        cls = 'lte+nr' if lte_pcis and nr_pcis else 'lte' if lte_pcis else 'nr'
        parts = []
        if lte_pcis:
            parts.append(f"an LTE downlink (cyclic prefix, PSS and SSS) at "
                         f"{lte['block_center_hz'] / 1e6:.6g} MHz, PCI "
                         + ', '.join(str(p) for p in lte_pcis))
        if nr_pcis:
            parts.append(f"{len(nr_pcis)} 5G NR sync block{'s' if len(nr_pcis) > 1 else ''} "
                         "(PSS, SSS and PBCH DM-RS), PCI " + ', '.join(str(p) for p in nr_pcis))
        ev = "In the first 50 ms: " + '; '.join(parts) + "."
    elif snr < 6:
        cls = 'noise'
        ev = f"The signal is {snr:.1f} dB over the noise floor, under 6 dB."
    elif atsc:
        cls = 'atsc'
        ev = (f"ATSC 8VSB: {bw99 / 1e6:.3g} MHz wide, its strongest tone (the pilot) at "
              f"{atsc['pilot_hz'] / 1e6:.6g} MHz at its {'upper' if atsc['inverted'] else 'lower'} edge"
              f"; channel centre {atsc['channel_centre_hz'] / 1e6:.6g} MHz.")
    elif tv:
        cls = 'tv'
        ev = (f"Analog TV ({tv['system']}, {tv['modulation'].upper()}): a line at "
              f"{tv['line_rate_hz']:.0f} Hz and its harmonic in the {tv['seen_in']}, "
              f"{tv['line_db']:.0f} dB over, {bw99 / 1e6:.3g} MHz wide"
              + ("; sound subcarriers at " + ', '.join(f"{s['freq_hz'] / 1e6:.2f}" for s in subcarriers)
                 + " MHz." if subcarriers else ".")
              + (f" It swings {tv['peak_to_peak_hz'] / 1e6:.3g} MHz peak to peak in the video band, "
                 f"{tv['low_peak_to_peak_hz'] / 1e6:.3g} MHz below 0.3 MHz"
                 + (": pre-emphasis (its edges overshoot)." if tv['pre_emphasis'] else ".")
                 if tv['peak_to_peak_hz'] else ""))
    elif pulses:
        cls = 'pulses'
        how = []
        if pulses['position_modulated']:
            how.append(f"each start moves by {pulses['position_rms_s'] * 1e6:.3g} us rms "
                       "(pulse-position modulation)")
        if pulses['width_modulated']:
            how.append("the widths vary (pulse-width modulation)")
        ev = (f"A train of {pulses['width_s'] * 1e6:.3g} us pulses at {pulses['rate_hz']:.0f} per "
              f"second (duty {pulses['duty']:.2f}); " + ('; '.join(how) or 'regular') + ".")
    elif keyed_env:
        ratio = env_lo / env_hi if env_hi > 0 else 0.0
        if env_order > 2:
            cls = 'ask'
            ev = (f"The envelope is keyed between {env_order} levels, {env_lo:.3g} to {env_hi:.3g}, "
                  f"at {sym:.0f} Bd." + _shaped(isi))
        elif ratio < 0.1:
            cls = 'ook'
            ev = (f"The envelope is keyed between {env_lo:.3g} and {env_hi:.3g}; "
                  f"the low level is {-20 * math.log10(max(ratio, 1e-12)):.0f} dB under the high one.")
        else:
            cls = 'ask'
            ev = (f"The envelope is keyed between {env_lo:.3g} and {env_hi:.3g}; "
                  "the low level is at least 10 % of the high one.")
    elif psk and psk['amplitude_levels']:
        lo, hi = psk['amplitude_levels']
        cls = 'ask'
        ev = (f"4-level ASK with the carrier suppressed: no carrier line, the square is a line "
              f"(BPSK's two phases), and the symbols take two amplitudes, {lo:.3g} and {hi:.3g}"
              + (f"; symbols at {psk['symbol_rate_baud']:.0f} Bd." if psk['symbol_rate_baud'] else "."))
    elif psk:
        name = {2: 'BPSK', 4: 'QPSK', 8: '8PSK'}[psk['order']]
        ev = (f"{name}: no carrier line, and the {psk['order']}th power is a line "
              f"(coherence {psk['line_coherence']:.2f})"
              + (f"; symbols at {psk['symbol_rate_baud']:.0f} Bd." if psk['symbol_rate_baud'] else "."))
        cls = 'psk'
    elif keyed_f and cv < 0.1:
        cls = 'fsk'
        if freq_order > 2:
            ev = (f"The frequency is keyed between {freq_order} levels, {f_lo + base_s:.0f} to "
                  f"{f_hi + base_s:.0f} Hz from the centre, at {sym:.0f} Bd, and the envelope "
                  f"is constant (CV {cv:.3f})." + _shaped(isi))
        else:
            ev = (f"The frequency is keyed at {f_lo + base_s:.0f} and {f_hi + base_s:.0f} Hz from the "
                  f"centre, and the envelope is constant (CV {cv:.3f}).")
    elif cv < 0.1 and fstd >= STEADY_HZ:
        cls = 'fm'
        ev = (f"The envelope is constant (CV {cv:.3f}) and the frequency spreads by "
              f"{fstd:.0f} Hz (standard deviation)"
              + ("; subcarriers at " + ', '.join(f"{s['freq_hz'] / 1e3:.1f}"
                                                  + (f" ({s['carries'].upper()})" if s.get('carries') else "")
                                                  for s in subcarriers)
                 + " kHz." if subcarriers else "."))
        if broadcast:
            ev += (" A broadcast multiplex: a 19 kHz pilot"
                   + (", RDS at 57 kHz" if broadcast['rds'] else "")
                   + (f"; stereo, L-R {broadcast['l_minus_r_db']:.0f} dB over its noise." if broadcast['stereo']
                      else f"; L-R empty ({broadcast['l_minus_r_db']:.0f} dB over its noise): a mono "
                           "programme sent with a pilot."))
    elif cv >= 0.1 and fstd < STEADY_HZ:
        cls = 'am'
        ev = f"The envelope varies (CV {cv:.2f}) and the frequency is steady ({fstd:.0f} Hz)."
    elif tone_n == 1 and cv < 0.1:
        cls = 'carrier'
        ev = (f"One tone at {tones[0]['freq_hz']:.0f} Hz, the envelope constant (CV {cv:.3f}) "
              "and no modulation measured.")
    else:
        cls = 'unknown'
        ev = (f"No rule fits: {tone_n} tones, envelope CV {cv:.2f}, "
              f"frequency spread {fstd:.0f} Hz.")

    pulse = None
    if cls in ('psk', 'ask') and sym and len(seg) >= 64:
        off = (psk['carrier_offset_hz'] if psk else centre - center_hz) - base_s
        pulse = _pulse(seg, rate_s, off, sym)
        if pulse and pulse['rolloff'] is not None:
            ev += f" Raised-cosine symbols, roll-off {pulse['rolloff']:.2f}."
        elif pulse and pulse['shape'] == 'rectangular':
            ev += " Rectangular symbols (a sinc's sidelobes)."
    card = {
        'level': {
            'power_dbfs': power_dbfs,
            'power_dbm': (power_dbfs + full_scale_dbm)
            if (full_scale_dbm is not None and power_dbfs is not None) else None,
            'noise_floor_dbfs': _db(floor),
            'snr_db': snr,
        },
        'occupied': {'bandwidth_hz': bw99, 'bandwidth_20db_hz': bw20, 'centre_hz': centre},
        'tones': tones,
        'beside': beside,
        'bursts': bursts,
        'envelope': {'levels': env_levels, 'depth': depth, 'keyed': bool(keyed_env), 'order': env_order},
        'frequency': {'levels_hz': freq_levels, 'deviation_hz': deviation,
                      'keyed': bool(keyed_f), 'order': freq_order, 'subcarriers': subcarriers},
        'symbol_rate_baud': sym,
        'pulse': pulse,
        'broadcast': broadcast,
        'atsc': atsc,
        'tv': tv,
        'pulses': pulses,
        'psk': psk,
        'lte': lte,
        'nr': nr,
        'class': cls,
        'evidence': [ev],
    }
    if fast and block == BLOCK and cls in ('am', 'fm', 'unknown'):
        # Symbols faster than a quarter of the block rate blur into a crowded middle. Taken
        # again in blocks a quarter of a symbol long, and kept only if that finds keying.
        short = int(max(BLOCK_MIN, min(BLOCK - 1, rate_s // (BLOCKS_PER_SYMBOL * fast))))
        again = _analyse(read, n, rate, center_hz, full_scale_dbm, band, station_hz, short, (lte, nr))
        if again['class'] in ('ook', 'ask', 'fsk'):
            again['evidence'].append(f"Read in {short}-sample blocks: a {fast:.0f} Hz symbol clock "
                                     f"is beyond {BLOCK}-sample blocks.")
            return again
    return card


def measure(iq, rate, center_hz=0.0, full_scale_dbm=None, t0=0.0, band=None):
    """The card of a complex array ``iq`` sampled at ``rate``, centred on
    ``center_hz`` (absolute). ``t0`` labels its start in the card; ``band``
    (absolute f_lo, f_hi) restricts the analysis to that band."""
    iq = np.asarray(iq)
    n = len(iq)
    if rate <= 0:
        raise ValueError("the sample rate must be positive.")

    def read(start, count):
        return np.asarray(iq[start:start + count], dtype=np.complex64)

    card = {'schema': SCHEMA, 'source': {
        'path': None, 'kind': 'array', 'rate_sps': float(rate), 'center_hz': float(center_hz),
        't0_s': float(t0), 't1_s': float(t0) + n / rate, 'samples': n}}
    card.update(_analyse(read, n, float(rate), float(center_hz), full_scale_dbm, band))
    _finish(card, read, n, float(rate), float(center_hz), band=band)
    return _clean(_ordered(card))


def cells_file(path, t0=None, rate=None, center_hz=None):
    """``(lte, nr)``: the card's cell sections for the 50 ms of ``path`` from ``t0`` seconds,
    without measuring the rest of a card (``iq-picture --cells``). There is no width gate,
    and the LTE carrier is looked for at the station, else at the power centroid of those
    50 ms on the 100 kHz raster. Times in the sections count from ``t0``."""
    src = open_iq(path, rate=rate, center_hz=center_hz)
    i0 = max(0, int(round((0.0 if t0 is None else float(t0)) * src.rate)))
    n = max(0, min(src.samples - i0, int(round(CELLS_SPAN_S * src.rate))))
    if n < MIN_SAMPLES:
        raise IQSourceError(f"{os.path.basename(str(path))}: fewer than {MIN_SAMPLES} samples from t0.")

    def read(start, count):
        return src.read(i0 + start, count)

    x = read(0, n)
    seg = min(COARSE_NFFT, n)
    blocks = x[:(n // seg) * seg].reshape(-1, seg)
    P = np.fft.fftshift((np.abs(np.fft.fft(blocks, axis=1)) ** 2).mean(axis=0))
    f = (np.arange(seg) - seg // 2) * src.rate / seg
    centre = src.center_hz + (float(np.sum(f * P) / np.sum(P)) if np.sum(P) > 0 else 0.0)
    return _cells(read, n, src.rate, src.center_hz, centre, None, src.station_hz)


def measure_file(path, t0=None, t1=None, band=None, rate=None, center_hz=None):
    """The card of a recording (see :func:`open_iq`). ``t0`` and ``t1`` are
    seconds into the file; by default the first 30 s."""
    src = open_iq(path, rate=rate, center_hz=center_hz)
    start_s = 0.0 if t0 is None else float(t0)
    stop_s = min(src.seconds, start_s + MAX_SPAN_S) if t1 is None else float(t1)
    i0 = max(0, int(round(start_s * src.rate)))
    i1 = min(src.samples, int(round(stop_s * src.rate)))
    if i1 - i0 < MIN_SAMPLES:
        raise IQSourceError(f"{os.path.basename(str(path))}: the span {start_s:g} to {stop_s:g} s "
                            f"holds fewer than {MIN_SAMPLES} samples.")
    n = i1 - i0
    card = {'schema': SCHEMA, 'source': {
        'path': str(path), 'kind': src.kind, 'rate_sps': src.rate, 'center_hz': src.center_hz,
        't0_s': i0 / src.rate, 't1_s': i1 / src.rate, 'samples': n}}
    read = lambda a, c: src.read(i0 + a, c)        # noqa: E731
    card.update(_analyse(read, n, src.rate, src.center_hz, src.full_scale_dbm, band, src.station_hz))
    _finish(card, read, n, src.rate, src.center_hz, src.seconds - i0 / src.rate if t1 is None else None, band)
    return _clean(_ordered(card))


class IQSlice(NamedTuple):
    """A span of IQ for a decoder: ``rate`` is its sample rate, ``center_hz`` the
    absolute centre of its band (now at 0 Hz), ``t0`` and ``t1`` the span in seconds."""
    iq: np.ndarray
    rate: float
    center_hz: float
    t0: float
    t1: float


#: With no band given, a file above max_rate is taken this far either side of its centre.
EXTRACT_HALF_HZ = 250e3
#: An extract with more output samples than this is refused before anything is read.
MAX_OUTPUT = 50_000_000


def _decimation(rate, lo, hi, max_rate):
    """``(dec, lo, hi)``: the decimation of a band lo..hi (offsets, Hz) of a file at
    ``rate``, and the band it keeps.

    ``dec`` is the largest integer that leaves the rate at least twice the band's
    width (so a narrow band is decimated far, and its span stays under the output
    cap). When that rate is above ``max_rate``, ``dec`` is the least integer that
    brings it to ``max_rate``. When no integer fits both, the band is taken as 0.9 of
    the output rate about its centre (a complex stream at rate R holds nearly R; the
    0.9 leaves room for the skirt), clipped to the file: it can be wider than the band
    asked for, when that is narrower than 0.9 of the output rate."""
    width = hi - lo
    wide = math.floor(rate / (2 * width) + 1e-9)
    fast = max(1, math.ceil(rate / max_rate - 1e-9))
    if fast <= wide:
        return wide, lo, hi
    kept, mid = 0.9 * rate / fast, (lo + hi) / 2
    return fast, max(mid - kept / 2, -rate / 2), min(mid + kept / 2, rate / 2)


def extract(path, t0=None, t1=None, band=None, max_rate=1e6, rate=None, center_hz=None):
    """The IQ of a span of a recording, for a decoder (see :func:`open_iq`).

    ``t0`` and ``t1`` are seconds into the file (by default the whole of it). ``band``
    is absolute (f_lo, f_hi) in Hz; with none, the whole file when its rate is at most
    ``max_rate`` (then passed through unchanged), else +/- 250 kHz about its centre. A
    band partly outside the file is clipped to it, and ``center_hz`` is then the middle
    of the clipped band.

    The band is mixed to 0 Hz and band-limited, then decimated (see ``_decimation``):
    the output rate is at least twice the band's width and at most ``max_rate``, when
    an integer decimation allows it. If none does, the kept band is 0.9 of the output
    rate, centred on the band's centre; that can widen a band asked for when it is
    narrower than 0.9 of the output rate. The kept band is clipped to the file, so
    ``center_hz`` moves with it when the band sits at the file's edge. ``max_rate``
    must be a finite number above 0. The span is read in chunks: only the output is
    held whole."""
    if not (math.isfinite(max_rate) and max_rate > 0):
        raise IQSourceError(f"max_rate must be a finite number above 0, not {max_rate!r}.")
    src = open_iq(path, rate=rate, center_hz=center_hz)
    start_s = 0.0 if t0 is None else float(t0)
    stop_s = src.seconds if t1 is None else float(t1)
    i0 = max(0, int(round(start_s * src.rate)))
    i1 = min(src.samples, int(round(stop_s * src.rate)))
    n = i1 - i0
    if n < MIN_SAMPLES:
        raise IQSourceError(f"{os.path.basename(str(path))}: the span {start_s:g} to {stop_s:g} s "
                            f"holds fewer than {MIN_SAMPLES} samples.")
    if band is None and src.rate <= max_rate:
        dec, lo, hi = 1, None, None
    else:
        if band is None:
            band = (src.center_hz - EXTRACT_HALF_HZ, src.center_hz + EXTRACT_HALF_HZ)
        lo, hi = _band_offsets(band, src.center_hz, src.rate)
        dec, lo, hi = _decimation(src.rate, lo, hi, max_rate)
    n_out = -(-n // dec)
    if n_out > MAX_OUTPUT:
        raise IQSourceError(f"the span would give {n_out} output samples, more than {MAX_OUTPUT}: "
                            "shorten the span, or narrow the band (a narrower band is decimated "
                            "further, so fewer samples).")
    out = np.empty(n_out, dtype=np.complex64)
    if lo is None:
        for s in range(0, n, CHUNK):
            m = min(CHUNK, n - s)
            out[s:s + m] = src.read(i0 + s, m)
        base = 0.0
    else:
        pos = 0
        for y in _banded(lambda a, c: src.read(i0 + a, c), n, src.rate, lo, hi, dec):
            out[pos:pos + len(y)] = y
            pos += len(y)
        base = (lo + hi) / 2
    return IQSlice(out, src.rate / dec, src.center_hz + base, i0 / src.rate, i1 / src.rate)


def _ordered(card):
    keys = ['schema', 'source', 'level', 'occupied', 'tones', 'beside', 'bursts', 'envelope',
            'frequency', 'symbol_rate_baud', 'pulse', 'broadcast', 'audio', 'atsc', 'tv', 'pulses', 'psk', 'lte',
            'nr', 'class', 'evidence', 'warnings']
    return {k: card[k] for k in keys}


def card_path(path):
    """``<base>.card.json`` beside a ``.cfile``; None for any other file (the
    app does not write beside those)."""
    base, ext = os.path.splitext(str(path))
    if ext.lower() != '.cfile':
        return None
    return base + CARD_SUFFIX


def _is_card(path):
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and 'schema' in doc


def write_card(path, card):
    """Write ``card`` as ``card_path(path)``, beside a ``.cfile``. An existing
    file there is replaced only if it is a card (it has a ``schema`` key)."""
    target = card_path(path)
    if target is None:
        raise IQSourceError(f"{os.path.basename(str(path))} is not a .cfile: a card is written "
                            "only beside a .cfile.")
    if os.path.exists(target) and not _is_card(target):
        raise IQSourceError(f"{os.path.basename(target)} exists and is not a card: not overwritten.")
    with open(target, 'w') as fh:
        json.dump(_clean(card), fh, indent=2, allow_nan=False)
        fh.write('\n')
    return target
