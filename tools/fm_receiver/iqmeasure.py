"""Measuring a signal from an IQ recording: what it is, as numbers and a class.

Behind ``tools/iq-measure`` and ``fmctl measure``. It reads a recording of IQ
(a ``.cfile`` with its ``.sigmf-meta``, a bare ``.cfile`` given its rate, a
Sceptre DVR's IQ ``.sdvr``, or a BLUE ``.cdif``) a span at a time, never
whole, and returns a card: a JSON-safe dict of the level, the occupied
bandwidth, the tones, the bursts, the envelope and frequency behaviour, the
symbol rate, and a class ('noise', 'carrier', 'ook', 'ask', 'fsk', 'fm',
'am' or 'unknown') with the evidence for it. The class rules are fixed
(CONTRACTS-measure.md), so the same recording always gives the same card.
Numpy only, so it runs anywhere; it writes nothing but the card, when asked.

Two spectra are taken. Tones and the noise floor come from a 1,024-bin
Blackman-Harris periodogram (a bin is rate / 1024; its very low side lobes
keep a strong tone's leakage out of the tone list). Bandwidth and the
centroid come from a finer Hann periodogram, so a narrow signal is not
widened to a whole bin. The envelope and the frequency are tracked in blocks
of 16 samples, from the phase advance summed over each block, so the noise
averages down; the 64-sample track gives the frequency's spread without the
block-to-block noise.
"""

import json
import math
import os

import numpy as np

from . import sceptre_blue, sceptre_dvr

SCHEMA = 1
MIN_SAMPLES = 1024
MAX_SPAN_S = 30.0
CARD_SUFFIX = '.card.json'
#: Samples read at a time (a span is never held whole).
CHUNK = 1 << 16
#: Samples per envelope and frequency block.
BLOCK = 16
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
MIN_CLUSTER = 0.005
STEADY_HZ = 500.0


class IQSourceError(Exception):
    """The file cannot be measured (not IQ, too short, missing, or not a
    file this reads); the message says why."""


class IQSource:
    """A recording of IQ, read a span at a time. ``read`` never writes."""

    def __init__(self, path, kind, rate, center_hz, samples, full_scale_dbm, fetch):
        self.path = path
        self.kind = kind
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.samples = int(samples)
        self.full_scale_dbm = None if full_scale_dbm is None else float(full_scale_dbm)
        self._fetch = fetch

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


def _sidecar_meta(path):
    meta = os.path.splitext(path)[0] + '.sigmf-meta'
    if not os.path.isfile(meta):
        return {}
    try:
        with open(meta) as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise IQSourceError(f"{os.path.basename(meta)} is not readable: {exc}") from exc


def _open_cfile(path, rate, center_hz):
    name = os.path.basename(path)
    samples = os.path.getsize(path) // 8
    if samples < MIN_SAMPLES:
        raise IQSourceError(f"{name} holds {samples} samples; at least {MIN_SAMPLES} are needed.")
    meta = _sidecar_meta(path)
    meta_rate = meta.get('global', {}).get('core:sample_rate')
    captures = meta.get('captures') or [{}]
    meta_centre = captures[0].get('core:frequency')
    if rate is None:
        rate = meta_rate
    if rate is None:
        raise IQSourceError(f"{name} has no .sigmf-meta giving its rate: pass rate= "
                            "(and center_hz=) to measure a bare .cfile.")
    if rate <= 0:
        raise IQSourceError("the sample rate must be positive.")
    centre = center_hz if center_hz is not None else (meta_centre or 0.0)
    data = np.memmap(path, dtype=np.complex64, mode='r', shape=(samples,))
    return IQSource(path, 'cfile', rate, centre, samples, None,
                    lambda a, b: data[a:b])


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
    if ext == '.cfile':
        return _open_cfile(path, rate, center_hz)
    if ext == '.sdvr':
        return _open_sdvr(path, rate, center_hz)
    if ext == '.cdif':
        return _open_cdif(path, rate, center_hz)
    if ext == '.fft':
        raise IQSourceError(f"{name} is a spectrum, not IQ: nothing to measure.")
    raise IQSourceError(f"{name} is not a type this reads (.cfile, .sdvr, .cdif).")


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

    def __init__(self):
        self.env = []
        self.prod = []
        self.tail = np.empty(0, dtype=np.complex64)

    def feed(self, x):
        buf = np.concatenate([self.tail, x]) if self.tail.size else x
        # The last sample is kept: it is the lag partner of the next block's first.
        cut = (len(buf) - 1) // BLOCK * BLOCK
        if cut:
            self.env.append(np.abs(buf[:cut].reshape(-1, BLOCK)).mean(axis=1))
            prod = buf[1:cut + 1] * np.conj(buf[:cut])
            self.prod.append(prod.reshape(-1, BLOCK).sum(axis=1))
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
    k = int(np.argmax(between))
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


def _banded(read, n, rate, lo, hi, dec):
    """The band lo..hi (offsets from the centre) of the samples, brick-wall
    filtered by overlap-save in blocks (see the constants above), moved to 0 Hz
    and decimated by ``dec``. Outputs lie on multiples of ``dec``.

    A narrow band (the usual case) takes each output as a sum of the band's bins'
    phasors, so no inverse transform is taken at all; otherwise a block's inverse
    transform keeps the band's bins. The padding is widened for a narrow band (its
    ringing is longer) and the block with it, up to MAX_BLOCK. Outputs sit at the
    multiples of ``dec`` by sample index, whether or not a block holds a whole
    output step. The file is read once, in order: the overlap is carried over."""
    fc = (lo + hi) / 2
    pad = int(min(max(PAD, math.ceil(RING_LENGTHS * rate / (hi - lo))), MAX_PAD))
    length = min(MAX_BLOCK, 1 << (max(FILTER_BLOCK, 4 * pad) - 1).bit_length())
    room = length - 2 * pad
    step = dec * (room // dec) if room >= dec else room
    f = np.fft.fftfreq(length, 1 / rate)
    # A cosine skirt of a few bins, outside the band: the passband stays flat,
    # and the shorter ringing fits the overlap, so the blocks agree at the splice.
    dist = np.maximum(lo - f, f - hi)
    skirt = SKIRT_BINS * rate / length
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


def _decimated(read, n, rate, lo, hi, min_rate=0.0):
    """The band lo..hi (offsets from the centre) of ``n`` samples, decimated to at
    least twice its width and to at least ``min_rate``: ``(decimation, chunks)``,
    as :func:`_banded` gives them."""
    dec = max(1, int(rate // max(2 * (hi - lo), min_rate)))
    return dec, _banded(read, n, rate, lo, hi, dec)


def _analyse(read, n, rate, center_hz, full_scale_dbm, band):
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
    track = _Track()
    for x in chunks:
        track.feed(x)
    env = np.concatenate(track.env).astype(np.float64)
    prod = np.concatenate(track.prod)
    fs_b = rate_s / BLOCK
    span_s = len(env) / fs_b
    # The angle of a block's summed products is the phase step per sample.
    track16 = np.angle(prod).astype(np.float64) * (rate_s / (2 * np.pi))

    # Bursts: the blocks above a tenth of the peak level, with the gaps inside
    # a burst kept in it. The levels are those inside the bursts.
    active = env > 0.1 * float(np.percentile(env, 99.5))
    burst_card, in_burst = _bursts(active, fs_b, span_s)
    keyed_env, env_lo, env_hi, env_thr = _levels(env[in_burst]) if in_burst.any() \
        else (False, None, None, None)
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

    tone_n = len(tones)
    if snr < 6:
        cls = 'noise'
        ev = f"The signal is {snr:.1f} dB over the noise floor, under 6 dB."
    elif keyed_env:
        ratio = env_lo / env_hi if env_hi > 0 else 0.0
        if ratio < 0.1:
            cls = 'ook'
            ev = (f"The envelope is keyed between {env_lo:.3g} and {env_hi:.3g}; "
                  f"the low level is {-20 * math.log10(max(ratio, 1e-12)):.0f} dB under the high one.")
        else:
            cls = 'ask'
            ev = (f"The envelope is keyed between {env_lo:.3g} and {env_hi:.3g}; "
                  "the low level is at least 10 % of the high one.")
    elif keyed_f and cv < 0.1:
        cls = 'fsk'
        ev = (f"The frequency is keyed at {f_lo + base_s:.0f} and {f_hi + base_s:.0f} Hz from the "
              f"centre, and the envelope is constant (CV {cv:.3f}).")
    elif cv < 0.1 and fstd >= STEADY_HZ:
        cls = 'fm'
        ev = (f"The envelope is constant (CV {cv:.3f}) and the frequency spreads by "
              f"{fstd:.0f} Hz (standard deviation).")
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

    return {
        'level': {
            'power_dbfs': power_dbfs,
            'power_dbm': (power_dbfs + full_scale_dbm)
            if (full_scale_dbm is not None and power_dbfs is not None) else None,
            'noise_floor_dbfs': _db(floor),
            'snr_db': snr,
        },
        'occupied': {'bandwidth_hz': bw99, 'bandwidth_20db_hz': bw20, 'centre_hz': centre},
        'tones': tones,
        'bursts': bursts,
        'envelope': {'levels': env_levels, 'depth': depth, 'keyed': bool(keyed_env)},
        'frequency': {'levels_hz': freq_levels, 'deviation_hz': deviation,
                      'keyed': bool(keyed_f)},
        'symbol_rate_baud': sym,
        'class': cls,
        'evidence': [ev],
    }


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
    return _clean(_ordered(card))


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
    card.update(_analyse(lambda a, c: src.read(i0 + a, c), n, src.rate, src.center_hz,
                         src.full_scale_dbm, band))
    return _clean(_ordered(card))


def _ordered(card):
    keys = ['schema', 'source', 'level', 'occupied', 'tones', 'bursts', 'envelope',
            'frequency', 'symbol_rate_baud', 'class', 'evidence']
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
