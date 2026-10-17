"""LTE structural fingerprint of a centred capture (normal cyclic prefix only).

Given complex IQ and its sample rate, say whether the block is an LTE FDD
downlink, from the signal alone. Three checks run. The carrier is assumed
centred, normal cyclic prefix, FDD.

The cyclic-prefix autocorrelation looks for the 15 kHz OFDM grid. Every
bandwidth shares that spacing: the useful symbol is 1/15 ms, the first prefix
of a slot is 160/2048 of that and the other six are 144/2048. A moving
correlation at the useful-symbol lag is folded onto one slot. The score is
the mean correlation at the seven prefix positions; the chance is how often
noise, or a signal without that slot pattern, would reach it. That null comes
from the other alignments of this same capture (median and MAD of the
alignment scores), turned into a tail probability and counted over the
independent alignments that were tried.

The PSS search takes those symbol boundaries, removes a small carrier-frequency
grid (a few kHz, the tolerance asked for), FFTs the last symbol of each slot
and correlates the central 62 bins with the three Zadoff-Chu roots 25, 29 and
34 (N_ID(2) = 0, 1, 2). The chance is a Beta tail fitted to the correlations
of the other symbols in the capture, again counted over every root, frequency
and symbol that was tried.

The SSS check needs that PSS root and the symbol timing. In FDD the SSS sits
in the symbol immediately before the PSS, on the same central 62 subcarriers,
once per half-frame. The received PSS divided by the known root is the channel
on those subcarriers, and that channel equalises the SSS. There are 168 x 2
hypotheses: N_ID(1) from 0 to 167, and which half-frame is subframe 0 rather
than subframe 5 (the swap of the two interleaved m-sequences, 36.211 6.11.2.1).
Equalised half-frames of one kind are averaged, then correlated. The strongest
hypothesis gives N_ID(1) and the half-frame. The chance is a Gaussian tail of
the other hypotheses' scores in this same capture (the null), multiplied by
the number of hypotheses and floored at 1e-300. The physical cell ID is
3 * N_ID(1) + N_ID(2) when that chance is at most 1e-3. A capture shorter
than 20 ms has no SSS result and raises nothing. CRS is named and not run.

The carrier is assumed centred, so the 62 synchronisation bins sit on DC and
the occupied width can be read from the same symbol FFTs. The reported FFT
size is the bandwidth class of that occupied width (1024 for a 10 MHz carrier
however it was sampled), not the transform used internally. A capture shorter
than 10 ms, or slower than 1.92 MS/s, is ``unknown`` and raises nothing.
Chances are floored at 1e-300.

Wrong turns, kept out of the result:

- A coherent sum of the prefix correlation across symbols. A CFO of a few kHz
  turns the correlation by a large fraction of a cycle from symbol to symbol,
  and the sum cancels. Each symbol is taken by its magnitude, then averaged.
- Trying every smaller bandwidth class as its own prefix lag, and keeping the
  class with the best chance. Low-passing a wide LTE carrier leaves a narrower
  15 kHz grid with a legal prefix, so the narrow class also "passes" and the
  FFT size comes out too small. One lag, fixed by the 15 kHz spacing at the
  analysis rate, and the class from how many subcarriers are actually in use.
- Correlating the PSS on integer FFT bins with no frequency trial. At 2 kHz
  the tone sits a seventh of a bin off; at 5 kHz a third. The 62 bins no
  longer match the root. A coarse derotation before the FFT is enough; the
  prefix check never needed it, because the prefix is only a few microseconds.
- Treating a large raw correlation as a rare event. A tone correlates at every
  lag, and the best of a thousand noise alignments always looks special if the
  other alignments are ignored. The chance is the extreme-value probability
  under the capture's own bulk, so a flat metric scores near 1 and pure noise
  stays above the 1e-3 line.
- Adding every half-frame against one SSS sequence. Subframe 0 and subframe 5
  swap the two m-sequences, so a coherent sum across a frame cancels the
  cell ID. The two residues are scored as a pair, swapped in the two ways.
- Zero-forcing the SSS with no floor. A weak PSS bin (a deep fade, or a slot
  that is not a PSS) blows up and the average follows the noise. The channel
  divide is floored at a fraction of the median bin power.
- Reading the winning hypothesis off as a tiny chance because it beat the
  second place. The other hypotheses are the null, and that one-draw tail is
  multiplied by all 336 candidates. A capture with no SSS then stays above
  1e-3, and a real one falls to the floor.

``cells`` lists every cell whose SSS clears that same 1e-3 line, strongest
first. The PSS of the three roots is nearly orthogonal, so each root is
correlated on its own: a normalised time-domain match against that root's
useful symbol, folded onto one 10 ms frame by the mean of the magnitudes.
Peaks of one root that sit more than a short guard apart are different
timings (two towers that may share a root). Peaks of different roots that
sit within a few samples are one timing (sectors of one tower). At that
timing the three known PSS sequences share the symbol, so a per-bin divide
by one root is the other sectors' PSS. A least-squares fit of the three
roots gives a flat gain per root; the SSS hypotheses are then scored
strongest root first, and a PCI that clears 1e-3 is subtracted (its SSS
only) before the next one is scored. The same root is scored again after
that, because two cells can share a root and a timing and then only the
SSS tells them apart. A PCI the SSS does not support is not reported. Power is that root's fitted gain, and ``relative_power_db``
is the gain against the strongest kept cell.

Wrong turns for several cells, kept out of the result:

- Folding the correlation onto 5 ms. The two PSS occasions then stack, which
  is fine for one cell, but a neighbour with the same root sitting half a
  frame away lands on the same bin and disappears. The fold is one frame,
  and the half-frame mate is the same cell found twice.
- Blanking that half-frame mate while picking peaks. A sidelobe blanked
  5 ms away as well, and that is where a second tower sometimes sits.
- Searching only the cyclic-prefix peaks of the strongest grid. A second
  tower is not on that grid, so its PSS is never a candidate. The prefix
  peaks stay the single-cell path; the multi-cell search is this other fold.
- Keeping the single best root at a shared timing. The other sectors are
  the other roots, and a global argmax drops them.
- Zero-forcing each SSS bin with the raw PSS bin. On a shared timing that
  bin is the sum of the sectors, and the weak sector's SSS chance stays
  just above 1e-3. The joint gain, then subtracting the stronger SSS, is
  what brings it under the line.
- A coherent sum across frames before the peak pick. A few kilohertz of
  offset turns the PSS from frame to frame and the sum cancels; the fold
  averages magnitudes.
- Subtracting a regenerated data subframe of the strong cell. The data is
  not known, and a wrong regeneration leaves a residual that can look like
  another cell. Only the SSS that already cleared the chance line is removed,
  and only by its own amplitude. Taking the whole joint PSS gain out with
  the first of two same-root cells leaves a residual that correlates with
  an identity that was never there.
- Keeping a same-root peak a few hundred samples off a stronger cell. That
  is a sidelobe, not a second tower: a second tower is not that close, and
  a sector on the same sample has a different root.
"""

import math

import numpy as np

# (FFT size of the class, native sample rate, used subcarriers excluding DC)
_CLASSES = (
    (128, 1.92e6, 72),
    (256, 3.84e6, 180),
    (512, 7.68e6, 300),
    (1024, 15.36e6, 600),
    (1536, 23.04e6, 900),
    (2048, 30.72e6, 1200),
)
_MIN_FS = 1.92e6
_MIN_DUR = 0.010
_PASS = 1e-3
_CHANCE_FLOOR = 1e-300
_PSS_ROOTS = (25, 29, 34)
# 2 kHz steps from -6 to +6 kHz. A 5 kHz offset then sits 1 kHz off the
# nearest trial. Finer than that costs another FFT per prefix peak; the
# prefix check itself is not derotated.
_CFO_HZ = np.arange(-6000.0, 6000.1, 2000.0)

_PSS_SEQ = None  # (3, 62), unit-magnitude Zadoff-Chu, filled on first use
_SSS_TEMPL = None  # (3, 168, 2, 62) real ±1, nid2 then N_ID(1) then subframe
_SSS_MIN_DUR = 0.020
_N_SSS_HYP = 168 * 2


def _m_sequence(taps):
    """Length-31 m-sequence 1 - 2x(i), x(0..4) = 0,0,0,0,1 (36.211 6.11.2.1)."""
    x = np.zeros(31, dtype=np.int8)
    x[4] = 1
    taps = np.asarray(taps, dtype=np.int64)
    for i in range(26):
        x[i + 5] = np.bitwise_xor.reduce(x[i + taps])
    return 1.0 - 2.0 * x.astype(np.float64)


def _sss_templates():
    """SSS for every N_ID(2), N_ID(1) and both half-frames, shape (3, 168, 2, 62).

    Index 0 of the pair is subframe 0, index 1 is subframe 5. Built from the
    m-sequences, the cyclic shifts m0/m1 of N_ID(1), and the N_ID(2) scrambling
    in 36.211 6.11.2.1. Real ±1.
    """
    global _SSS_TEMPL
    if _SSS_TEMPL is not None:
        return _SSS_TEMPL
    s = _m_sequence((2, 0))
    c = _m_sequence((3, 0))
    z = _m_sequence((4, 2, 1, 0))
    nid1 = np.arange(168)
    q_prime = nid1 // 30
    q = (nid1 + q_prime * (q_prime + 1) // 2) // 30
    m_prime = nid1 + q * (q + 1) // 2
    m0 = m_prime % 31
    m1 = (m0 + m_prime // 31 + 1) % 31
    n = np.arange(31)
    s0 = s[(n + m0[:, None]) % 31]
    s1 = s[(n + m1[:, None]) % 31]
    z0 = z[(n + (m0 % 8)[:, None]) % 31]
    z1 = z[(n + (m1 % 8)[:, None]) % 31]
    out = np.empty((3, 168, 2, 62), dtype=np.float64)
    for nid2 in range(3):
        c0 = c[(n + nid2) % 31]
        c1 = c[(n + nid2 + 3) % 31]
        even0 = s0 * c0
        odd0 = s1 * c1 * z0
        even5 = s1 * c0
        odd5 = s0 * c1 * z1
        out[nid2, :, 0, 0::2] = even0
        out[nid2, :, 0, 1::2] = odd0
        out[nid2, :, 1, 0::2] = even5
        out[nid2, :, 1, 1::2] = odd5
    _SSS_TEMPL = out
    return out


def _pss_seqs():
    """Length-62 PSS, DC element of the length-63 Zadoff-Chu removed (36.211 6.11.1)."""
    global _PSS_SEQ
    if _PSS_SEQ is None:
        n = np.arange(62)
        m = np.where(n < 31, n * (n + 1.0), (n + 1.0) * (n + 2.0))
        seqs = [np.exp(-1j * np.pi * u * m / 63.0) for u in _PSS_ROOTS]
        _PSS_SEQ = np.stack(seqs).astype(np.complex128)
    return _PSS_SEQ


def _floor_chance(p):
    if not np.isfinite(p):
        return 1.0
    return float(min(1.0, max(_CHANCE_FLOOR, p)))


def _extreme(p_one, n_trials):
    """Probability that the largest of ``n_trials`` draws beats a one-draw tail ``p_one``."""
    p = float(p_one)
    k = int(max(1, n_trials))
    if p <= 0.0:
        return _CHANCE_FLOOR
    if p >= 1.0:
        return 1.0
    if p * k < 1e-8:
        return _floor_chance(p * k)
    return _floor_chance(1.0 - (1.0 - p) ** k)


def _check(name, ran, score, chance, detail):
    chance = _floor_chance(chance if ran else (1.0 if chance is None else chance))
    if not ran:
        chance = _floor_chance(chance)
    score_f = float(score) if score is not None and np.isfinite(score) else 0.0
    return {
        'name': name,
        'ran': bool(ran),
        'passed': bool(ran) and chance <= _PASS,
        'score': score_f,
        'chance': chance,
        'detail': detail,
    }


def _later(name, detail):
    return {
        'name': name,
        'ran': False,
        'passed': False,
        'score': 0.0,
        'chance': 1.0,
        'detail': detail,
    }


def _unknown(reason, iq=None, fs=None):
    return {
        'checks': [
            _check('cp-autocorr', False, 0.0, 1.0, reason),
            _check('pss', False, 0.0, 1.0, reason),
            _later('sss', 'SSS is a later stage'),
            _later('crs', 'CRS is a later stage'),
        ],
        'verdict': 'unknown',
        'pci': None,
        'nid2': None,
        'nfft': None,
        'chance': 1.0,
        'cells': [] if iq is None else cells(iq, fs),
    }


def _vector(iq):
    """1-D complex samples, or None when the input is not a 1-D sequence.

    Anything else (a missing buffer, an ``(N, 2)`` IQ pair, a matrix) is not
    evaluated: the callers return unknown or a chance of 1, and do not raise.
    Non-finite samples are zeros, so a NaN capture scores 0 with chance 1
    instead of poisoning the correlations.
    """
    if iq is None:
        return None
    try:
        a = np.asarray(iq)
    except (TypeError, ValueError):
        return None
    if a.ndim != 1:
        return None
    out = np.array(a, dtype=np.complex128, copy=True)
    bad = ~np.isfinite(out.real) | ~np.isfinite(out.imag)
    if bad.any():
        out[bad] = 0
    return out


def _too_small(iq, fs):
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        return True
    if not np.isfinite(fs) or fs < _MIN_FS:
        return True
    vec = iq if isinstance(iq, np.ndarray) and iq.ndim == 1 else _vector(iq)
    n = 0 if vec is None else vec.size
    return n < _MIN_DUR * fs


def _analysis_plan(fs):
    """Largest LTE class rate that fits in ``fs``. The 15 kHz lag is then an integer."""
    chosen = None
    for n_fft, rate, n_sc in _CLASSES:
        if rate <= fs * (1.0 + 5e-6):
            chosen = (n_fft, rate, n_sc)
    return chosen


def _resample(iq, fs_in, fs_out):
    """Band-limited resample, complex, numpy only.

    A full FFT cuts everything above the output Nyquist, then a cubic picks
    the output instants. Used when the capture is not already on a class rate
    (a 10 MHz carrier at 20 MS/s). Exact class rates are returned as they are.
    """
    if abs(fs_in - fs_out) <= 1e-3:
        return np.ascontiguousarray(iq, dtype=np.complex128)
    x = np.ascontiguousarray(iq, dtype=np.complex128)
    n = x.size
    n_out = int(round(n * fs_out / fs_in))
    if n_out < 8 or n < 8:
        return x[:0]
    spec = np.fft.fft(x)
    freqs = np.fft.fftfreq(n, d=1.0 / fs_in)
    spec[np.abs(freqs) > (0.96 * fs_out * 0.5)] = 0.0
    y = np.fft.ifft(spec)
    # One sample of pad so the cubic can see both neighbours at the ends.
    y = np.concatenate((y[:1], y, y[-1:]))
    idx = np.arange(n_out) * (fs_in / fs_out) + 1.0  # shift by the pad
    i0 = np.floor(idx).astype(np.int64)
    i0 = np.clip(i0, 1, n - 1)
    f = idx - i0
    ym1 = y[i0 - 1]
    y0 = y[i0]
    y1 = y[i0 + 1]
    y2 = y[np.minimum(i0 + 2, n)]
    f2 = f * f
    f3 = f2 * f
    out = 0.5 * (
        (2.0 * y0)
        + (-ym1 + y1) * f
        + (2.0 * ym1 - 5.0 * y0 + 4.0 * y1 - y2) * f2
        + (-ym1 + 3.0 * y0 - 3.0 * y1 + y2) * f3
    )
    return out


def _cp_lengths(n_fft):
    return 160 * n_fft // 2048, 144 * n_fft // 2048


def _slot_layout(n_fft):
    """Prefix length of each of the seven symbols, and the sample where its prefix starts."""
    cp0, cp = _cp_lengths(n_fft)
    starts = []
    lens = []
    pos = 0
    for i in range(7):
        this = cp0 if i == 0 else cp
        starts.append(pos)
        lens.append(this)
        pos += this + n_fft
    return starts, lens, pos, cp  # pos is one slot


def _moving_sum(z, length):
    c = np.cumsum(z)
    out = c[length - 1:].copy()
    out[1:] -= c[:-length]
    return out


def _cp_alignment(iq, n_fft):
    """Folded prefix correlation and the score of every slot alignment.

    Returns ``(align, slot, cp, n_slots_used)`` or ``None`` when the block
    holds less than two slots after the lag.
    """
    starts, _lens, slot, cp = _slot_layout(n_fft)
    length = cp
    n = iq.size
    if n < n_fft + length + 2 * slot:
        return None
    x = iq
    prod = x[:-n_fft] * np.conj(x[n_fft:])
    gamma = _moving_sum(prod, length)
    power = _moving_sum(np.abs(x) ** 2, length)
    m = min(gamma.size, power.size - n_fft)
    if m < 2 * slot:
        return None
    den = np.sqrt(np.maximum(power[:m], 0.0) * np.maximum(power[n_fft:n_fft + m], 0.0))
    rho = np.abs(gamma[:m]) / (den + 1e-20)
    n_slots = m // slot
    folded = rho[:n_slots * slot].reshape(n_slots, slot).mean(axis=0)
    # Circular correlation of the folded profile with the seven prefix instants.
    kernel = np.zeros(slot, dtype=np.float64)
    for s in starts:
        kernel[(-s) % slot] += 1.0 / 7.0
    align = np.fft.ifft(np.fft.fft(folded) * np.fft.fft(kernel)).real
    return align, slot, cp, n_slots, folded


def _independent_looks(align):
    """How many of the alignment scores are independent, from this capture.

    Neighbouring scores share a moving window, so they move together. The lag
    where their correlation falls through one half is one independent look;
    the rest of the slot is that many looks. A fixed fraction of the prefix
    length over-counts the looks on noise (the chance comes out too small)
    and under-counts them when the scores stay correlated for longer.
    """
    a = np.asarray(align, dtype=np.float64)
    a = a - np.median(a)
    if float(np.dot(a, a)) <= 0.0:
        return 1
    n = a.size
    ac = np.fft.irfft(np.abs(np.fft.rfft(a)) ** 2, n)
    ac0 = float(ac[0])
    if ac0 <= 0.0:
        return 1
    rho = ac[:max(1, n // 2)] / ac0
    below = np.flatnonzero(rho < 0.5)
    length = int(below[0]) if below.size else max(1, n // 2)
    return max(1, n // max(1, length))


def _cp_chance(align):
    """Extreme-value chance of the best alignment under the bulk of ``align``."""
    peak = float(np.max(align))
    med = float(np.median(align))
    mad = float(np.median(np.abs(align - med)))
    scale = 1.4826 * mad
    n_indep = _independent_looks(align)
    if scale <= 1e-6 * (1.0 + abs(med)):
        # Flat across alignments: a tone, or silence. The peak is typical.
        if peak <= med + 1e-4 * (1.0 + abs(med)):
            return peak, 1.0
        return peak, _CHANCE_FLOOR
    z = (peak - med) / scale
    if z <= 0.0:
        return peak, 1.0
    p_one = 0.5 * math.erfc(z / math.sqrt(2.0))
    return peak, _extreme(p_one, n_indep)


def _class_from_width(n_used, fs):
    """Snap a measured subcarrier count to the class it fits.

    A class is eligible when its occupied width fits in the capture, not when
    the capture was taken at that class's native rate: a 15 MHz carrier
    (13.5 MHz occupied, FFT 1536) sampled at 20 MS/s is still that class.
    Among eligible classes, the nearest subcarrier count wins and a tie goes
    to the wider one. There is no error gate — once the prefix check has
    passed, a measured width still names a class.
    """
    if n_used < 36:
        return None
    best_n = None
    best_key = None
    for n_fft, _rate, n_sc in _CLASSES:
        if n_sc * 15e3 > float(fs) * 1.05:
            continue
        err = abs(int(n_sc) - int(n_used))
        key = (err, -n_fft)
        if best_key is None or key < best_key:
            best_key = key
            best_n = n_fft
    return best_n


def _occupied_subcarriers(power):
    """How many subcarriers around DC are in use. DC itself is empty and not counted.

    The floor is a low percentile of the smoothed bin powers, not the outer
    twelfth of the transform. A 15 MHz carrier analysed at 1024 bins leaves
    only a slim guard, so those outer bins are already signal, and a fixed
    twice-the-edge rule then sees no band at all. The threshold sits a third
    of the way (in log power) from that floor up to the high end: at -5 dB
    the used bins are only about 1.5 times the empty ones, still above the
    cut, and a flat noise spectrum (high end barely above the floor) counts
    as empty. A hole of a few bins (the DC bin) does not break the walk.
    """
    n = power.size
    ps = np.fft.fftshift(np.maximum(power, 0.0))
    smooth = np.convolve(ps, np.ones(5) / 5.0, mode='same')
    lo = float(np.percentile(smooth, 5))
    hi = float(np.percentile(smooth, 90))
    if not np.isfinite(lo) or not np.isfinite(hi) or lo <= 0.0 or hi < lo * 1.15:
        return 0
    thr = lo * (hi / lo) ** 0.35
    above = smooth > thr
    center = n // 2
    gap = 6

    def walk(step):
        i = center
        last = center
        holes = 0
        while 0 <= i < n:
            if above[i]:
                last = i
                holes = 0
            else:
                holes += 1
                if holes >= gap:
                    break
            i += step
        return last

    width = walk(1) - walk(-1) + 1
    return max(0, width - 1)


def _symbol_bodies(iq, n_fft, phase0, which):
    """Useful samples of selected symbols on the slot grid.

    ``phase0`` is the sample, inside a slot, where symbol 0's prefix starts.
    ``which`` is a tuple of symbol indices (0..6). Bodies that would run off
    the end of ``iq`` are dropped.
    """
    starts, lens, slot, _cp = _slot_layout(n_fft)
    which = tuple(which)
    phases = np.array([(phase0 + starts[i]) % slot + lens[i] for i in which], dtype=np.int64)
    n_slots = iq.size // slot
    if n_slots < 1:
        return np.empty((0, n_fft), dtype=np.complex128), 0
    # The largest phase reaches into the following slot; keep only slots that fit.
    need = int(phases.max()) + n_fft
    n_use = n_slots
    while n_use > 0 and (n_use - 1) * slot + need > iq.size:
        n_use -= 1
    if n_use < 1:
        return np.empty((0, n_fft), dtype=np.complex128), 0
    slots = np.arange(n_use, dtype=np.int64)[:, None, None]
    sym = np.arange(len(which), dtype=np.int64)[None, :, None]
    k = np.arange(n_fft, dtype=np.int64)[None, None, :]
    idx = slots * slot + phases[None, :, None] + k
    bodies = iq[idx].reshape(n_use * len(which), n_fft)
    return bodies, n_use


def _bandwidth(iq, n_fft, phase0, fs_capture):
    """Class FFT size from the occupied width, or None when it matches no class."""
    bodies, _n = _symbol_bodies(iq, n_fft, phase0, range(7))
    if bodies.shape[0] < 4:
        return None, 0
    # Average in chunks so a 20 MHz block is not one huge stack of spectra.
    acc = np.zeros(n_fft, dtype=np.float64)
    step = 64
    for a in range(0, bodies.shape[0], step):
        spec = np.fft.fft(bodies[a:a + step], axis=1)
        acc += np.sum(np.abs(spec) ** 2, axis=0)
    acc /= bodies.shape[0]
    n_used = _occupied_subcarriers(acc)
    return _class_from_width(n_used, fs_capture), n_used


def _pss_chance(peak, obs):
    """Chance the best correlation is a Zadoff-Chu root, from this capture.

    The null is the same centre-bin vectors correlated against the three roots
    with their bins scrambled: circular shifts, plus a fixed set of
    permutations. Each scramble contributes its maximum over every symbol,
    frequency trial and prefix peak that the real search used. A Beta tail
    fitted to the unscrambled bulk is too thin for an OFDM signal that is not
    LTE (an uplink-like waveform then scores as if it had a PSS).

    When the real peak does not clear the scrambled maxima, the chance is the
    rank of that peak, which cannot be finer than one over the number of
    scrambles. When it does clear them, a Gaussian tail of those maxima is
    used, with the scale held at least a few hundredths so a lucky gap of one
    scramble cannot pretend to be 1e-30. A true PSS sits near 1 and the
    scrambles near 0.4, so the tail is still far below 1e-6.
    """
    if obs is None or obs.ndim != 2 or obs.shape[0] < 4 or not np.isfinite(peak):
        return 1.0
    peak = float(np.clip(peak, 0.0, 1.0))
    refs = _pss_seqs()
    refs = refs / np.linalg.norm(refs, axis=1, keepdims=True)
    null_max = []
    # Shifts of 0 are the real roots; they are the hypothesis, not the null.
    for shift in range(1, 62):
        templ = np.roll(refs, shift, axis=1)
        corr = np.abs(obs @ templ.conj().T)
        null_max.append(float(corr.max()))
    # Bin permutations. The seed is fixed so the same capture always reports
    # the same chance. Skip a draw that landed back on a real root.
    draw = np.random.default_rng(0)
    for _ in range(24):
        perm = draw.permutation(62)
        templ = refs[:, perm]
        # A permutation of one root can match another; drop that draw.
        if float(np.max(np.abs(templ.conj() @ refs.T))) > 0.9:
            continue
        corr = np.abs(obs @ templ.conj().T)
        null_max.append(float(corr.max()))
    null = np.asarray(null_max, dtype=np.float64)
    null = null[np.isfinite(null)]
    if null.size < 8:
        return 1.0
    n_ge = int(np.sum(null >= peak))
    rank = (n_ge + 1.0) / (null.size + 1.0)
    # A few hundredths above the best scramble is what an OFDM signal that is
    # not a PSS looks like (the uplink-like negative clears the scrambles by
    # about 0.02). A real root clears them by two tenths even at -5 dB.
    # Below that gap the chance stays the rank, no finer than one scramble.
    if peak < float(np.max(null)) + 0.08:
        return _floor_chance(max(rank, 1.0 / (null.size + 1.0)))
    med = float(np.median(null))
    mad = float(np.median(np.abs(null - med)))
    scale = max(1.4826 * mad, 0.03)
    z = (peak - med) / scale
    if z <= 0.0:
        return _floor_chance(rank)
    p = 0.5 * math.erfc(z / math.sqrt(2.0))
    return _floor_chance(p)


def _prefix_peaks(folded, n_peaks, guard):
    """The strongest samples of the folded prefix profile, separated by ``guard``."""
    work = folded.copy()
    n = work.size
    dist = np.arange(n)
    peaks = []
    for _ in range(n_peaks):
        i = int(np.argmax(work))
        if work[i] < 0.0:
            break
        peaks.append(i)
        d = np.minimum(np.abs(dist - i), n - np.abs(dist - i))
        work[d <= guard] = -1.0
    return peaks


def _bodies_at(iq, n_fft, slot, useful_phase):
    """One OFDM body per slot, starting at ``useful_phase`` inside the slot."""
    phase = int(useful_phase) % int(slot)
    n_slots = iq.size // slot
    need = phase + n_fft
    n_use = n_slots
    while n_use > 0 and (n_use - 1) * slot + need > iq.size:
        n_use -= 1
    if n_use < 2:
        return np.empty((0, n_fft), dtype=iq.dtype)
    idx = (np.arange(n_use, dtype=np.int64)[:, None] * slot + phase) + np.arange(n_fft, dtype=np.int64)
    return iq[idx]


def _pss_on_grid(iq, fs, n_fft, folded, slot, cp):
    """Best PSS correlation at the prefix peaks.

    The slot comb can lock one symbol off, because six of the seven prefixes
    are the same length; the folded profile still has a peak on every prefix,
    and the PSS is the last symbol of only two slots, so every peak is tried.
    A couple of samples of timing error (the peak jitters at low SNR, and a
    CFO walks the bin phase) is a ramp across the FFT bins, not a new FFT.
    """
    peaks = _prefix_peaks(folded, 7, max(cp // 2, 2))
    if not peaks:
        return 0.0, None, 0.0, np.zeros((0, 62), dtype=np.complex128), 0
    refs = _pss_seqs()
    ref_m = (refs / np.linalg.norm(refs, axis=1, keepdims=True)).conj().T
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    k = np.arange(n_fft)
    # Offsets that stay inside a normal prefix, so the shift is circular.
    offsets = range(-1, 2)
    best = 0.0
    best_root = 0
    best_cfo = 0.0
    kept = []
    n_sym = 0
    t = k / float(fs)
    for phase in peaks:
        base = _bodies_at(iq, n_fft, slot, phase + cp)
        if base.shape[0] < 2:
            continue
        n_sym = base.shape[0]
        for f in _CFO_HZ:
            ramp_t = np.exp(-2j * np.pi * float(f) * t)
            spec = np.fft.fft(base * ramp_t, axis=1)
            for d in offsets:
                # Window starting d samples earlier, still inside the prefix.
                spec_d = spec * np.exp(2j * np.pi * d * k / n_fft)
                obs = spec_d[:, bins]
                norms = np.linalg.norm(obs, axis=1)
                good = norms > 1e-12
                obs_n = np.zeros_like(obs)
                obs_n[good] = obs[good] / norms[good, None]
                corr = np.abs(obs_n @ ref_m)
                # One row in five is enough for the scrambled null and keeps
                # the store small; the peak itself still sees every symbol.
                kept.append(obs_n[::5])
                flat = int(np.argmax(corr))
                m = float(corr.ravel()[flat])
                if m > best:
                    best = m
                    best_root = int(flat % 3)
                    best_cfo = float(f)
    if not kept:
        return 0.0, None, 0.0, np.zeros((0, 62), dtype=np.complex128), 0
    return best, best_root, best_cfo, np.concatenate(kept, axis=0), n_sym


def _prepare(iq, fs):
    """Return analysis IQ at a class rate, or None when the block cannot be one."""
    if _too_small(iq, fs):
        return None
    fs = float(fs)
    plan = _analysis_plan(fs)
    if plan is None:
        return None
    n_fft, rate, _n_sc = plan
    vec = _vector(iq)
    if vec is None:
        return None
    block = _resample(vec, fs, rate)
    if block.size < n_fft * 4:
        return None
    return block, rate, n_fft, fs


def cp_autocorr(iq, fs):
    """Cyclic-prefix check. Dict with ``score`` and ``chance``; no exception on a short or slow capture.

    ``score`` is the mean prefix correlation (0 to 1) at the best slot alignment.
    ``chance`` is the probability a block without the LTE prefix pattern reaches
    that score, from this capture's other alignments.
    """
    prepared = _prepare(iq, fs)
    if prepared is None:
        return {'score': 0.0, 'chance': 1.0, 'detail': 'too short or too slow to check',
                'passed': False, 'nfft': None}
    block, rate, n_fft, fs_in = prepared
    found = _cp_alignment(block, n_fft)
    if found is None:
        return {'score': 0.0, 'chance': 1.0, 'detail': 'shorter than two slots',
                'passed': False, 'nfft': None}
    align, _slot, cp, _n_slots, _folded = found
    score, chance = _cp_chance(align)
    if not np.isfinite(score):
        score, chance = 0.0, 1.0
    chance = _floor_chance(chance)
    phase0 = int(np.argmax(align))
    n_class, n_used = _bandwidth(block, n_fft, phase0, fs_in)
    return {
        'score': float(score),
        'chance': chance,
        'passed': chance <= _PASS,
        'nfft': n_class,
        'detail': f'prefix correlation {score:.3f} over {cp} samples, '
                  f'about {n_used} subcarriers in use',
    }


def pss_search(iq, fs):
    """PSS-root check. Dict with ``score`` and ``chance``; no exception on a short or slow capture.

    ``score`` is the best correlation of the central 62 bins with one of the
    three roots. ``nid2`` is that root when the chance passes, else None.
    """
    prepared = _prepare(iq, fs)
    if prepared is None:
        return {'score': 0.0, 'chance': 1.0, 'detail': 'too short or too slow to check',
                'passed': False, 'nid2': None}
    block, rate, n_fft, _fs_in = prepared
    found = _cp_alignment(block, n_fft)
    if found is None:
        return {'score': 0.0, 'chance': 1.0, 'detail': 'no symbol grid to search',
                'passed': False, 'nid2': None}
    align, slot, cp, _n_slots, folded = found
    peak, root, cfo, obs, _n = _pss_on_grid(block, rate, n_fft, folded, slot, cp)
    chance = _floor_chance(_pss_chance(peak, obs))
    passed = chance <= _PASS
    return {
        'score': float(peak),
        'chance': chance,
        'passed': passed,
        'nid2': int(root) if passed and root is not None else None,
        'detail': f'best root {root} correlation {peak:.3f} at {cfo:.0f} Hz',
    }


def _sss_result(ran, nid1, nid2, pci, half_frame, frame_offset, score, chance,
                n_used, detail):
    return {
        'ran': bool(ran),
        'nid1': None if nid1 is None else int(nid1),
        'nid2': None if nid2 is None else int(nid2),
        'pci': None if pci is None else int(pci),
        'half_frame': None if half_frame is None else int(half_frame),
        'frame_offset_samples': None if frame_offset is None else int(frame_offset),
        'score': float(score) if score is not None and np.isfinite(score) else 0.0,
        'chance': _floor_chance(1.0 if chance is None else chance),
        'halfframes_used': int(n_used),
        'detail': detail,
    }


def _sss_none(detail, ran=False):
    return _sss_result(ran, None, None, None, None, None, 0.0, 1.0, 0, detail)


def _pss_body_offset(n_fft):
    """Samples from the start of a slot (prefix included) to the PSS body.

    The PSS is symbol 6 of the slot. Symbol 0 has the long prefix; the other
    six, including the PSS, have the short one.
    """
    cp0, cp = _cp_lengths(n_fft)
    return cp0 + 6 * cp + 6 * n_fft, cp


def _locate_pss(block, rate, n_fft, folded, slot, cp):
    """Best PSS root, CFO and body start, plus the per-slot correlation.

    Same grid as ``_pss_on_grid``: every prefix peak, the coarse CFO trials,
    and a few samples of timing (a ramp across the bins, not a new FFT). The
    body start is the sample, inside a slot, of the window that won.
    """
    peaks = _prefix_peaks(folded, 7, max(cp // 2, 2))
    if not peaks:
        return None
    refs = _pss_seqs()
    ref_m = (refs / np.linalg.norm(refs, axis=1, keepdims=True)).conj().T
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    k = np.arange(n_fft)
    offsets = range(-3, 4)
    best = -1.0
    best_hit = None
    t = k / float(rate)
    ramp_d = [np.exp(2j * np.pi * d * k / n_fft) for d in offsets]
    for phase in peaks:
        base = _bodies_at(block, n_fft, slot, phase + cp)
        if base.shape[0] < 2:
            continue
        for f in _CFO_HZ:
            spec = np.fft.fft(base * np.exp(-2j * np.pi * float(f) * t), axis=1)
            for d, ramp in zip(offsets, ramp_d):
                obs = (spec * ramp)[:, bins]
                norms = np.linalg.norm(obs, axis=1)
                good = norms > 1e-12
                obs_n = np.zeros_like(obs)
                obs_n[good] = obs[good] / norms[good, None]
                corr = np.abs(obs_n @ ref_m)
                m = float(corr.max())
                if m > best:
                    best = m
                    flat = int(np.argmax(corr))
                    best_hit = {
                        'score': m,
                        'root': int(flat % 3),
                        'cfo': float(f),
                        'phase': int(phase),
                        'd': int(d),
                        'n_slots': int(base.shape[0]),
                        'corr': corr[:, int(flat % 3)].copy(),
                    }
    if best_hit is None or best < 0.25:
        return None
    # The winning ramp starts the window ``d`` samples earlier than phase+cp.
    best_hit['body'] = int(best_hit['phase'] + cp - best_hit['d'])
    return best_hit


def _equalise_sss(block, rate, n_fft, slot, hit):
    """Channel-equalised SSS for every PSS occasion of the winning residue.

    Returns ``(eq0, eq1, used0, used1)`` where each ``eq`` is (n, 62) complex
    and the ``used`` arrays are the PSS slot indices that contributed. Group 0
    is the earlier residue class (slot mod 20 == r), group 1 the other.
    """
    cp = _cp_lengths(n_fft)[1]
    root = hit['root']
    pss_ref = _pss_seqs()[root]
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    corr = hit['corr']
    n_slots = min(corr.size, hit['n_slots'])
    if n_slots < 2:
        return None
    residues = []
    for r in range(10):
        take = corr[r:n_slots:10]
        residues.append(float(take.mean()) if take.size else -1.0)
    r_best = int(np.argmax(residues))
    if residues[r_best] < 0.2:
        return None
    idxs = np.arange(r_best, n_slots, 10, dtype=np.int64)
    body = hit['body']
    pss_at = idxs * slot + body
    # Symbol 5 (SSS) to symbol 6 (PSS): one short prefix plus the useful symbol.
    sss_at = pss_at - (n_fft + cp)
    keep = (sss_at >= 0) & (pss_at + n_fft <= block.size)
    idxs = idxs[keep]
    pss_at = pss_at[keep]
    sss_at = sss_at[keep]
    if idxs.size < 1:
        return None
    k = np.arange(n_fft, dtype=np.int64)
    cfo = hit['cfo']

    def _specs(starts):
        samp = starts[:, None] + k[None, :]
        t = samp / float(rate)
        window = block[samp] * np.exp(-2j * np.pi * cfo * t)
        return np.fft.fft(window, axis=1)[:, bins]

    yp = _specs(pss_at)
    ys = _specs(sss_at)
    power = np.abs(yp) ** 2
    floor = 0.05 * np.median(power, axis=1, keepdims=True) + 1e-18
    # H = Yp * conj(root); equalise Ys / H, with a floor on |H|^2.
    eq = ys * np.conj(yp) * pss_ref / (power + floor)
    group0 = (idxs % 20) == r_best
    # r_best is in 0..9, so the other class is r_best+10, whose mod 20 is not r_best.
    return eq[group0], eq[~group0], idxs[group0], idxs[~group0]


def _hypothesis_scores(eq0, eq1, templ):
    """Mean correlation of each (N_ID(1), half-frame assignment). Shape (168, 2).

    Column 0 assigns subframe 0 to group 0. Column 1 swaps the two groups.
    A missing group is left out of the average rather than filled with zeros.
    """
    seq_norm = math.sqrt(62.0)
    dots = []
    for eq, sign in ((eq0, 1), (eq1, -1)):
        if eq.size == 0:
            continue
        mean = eq.mean(axis=0)
        scale = float(np.linalg.norm(mean)) * seq_norm
        if scale <= 1e-18:
            continue
        # templ is (168, 2, 62). Dot on the last axis -> (168, 2).
        corr = np.real(templ @ mean) / scale
        # sign < 0 swaps the subframe axis: group 1 against subframe 0 is column 1
        # of the unswapped templates when we later add column-wise.
        dots.append(corr if sign > 0 else corr[:, ::-1])
    if not dots:
        return None
    acc = np.zeros_like(dots[0])
    for d in dots:
        acc += d
    return acc / float(len(dots))


def _sss_chance(scores):
    """One-draw tail of the winner under the other hypotheses, times how many there are.

    The null is the other hypotheses' scores in this capture. The width is
    1.4826 times their MAD, the usual normal scale. That tail, times the
    number of hypotheses, is the chance. It is floored at 1e-300.
    """
    flat = scores.ravel()
    k = int(np.argmax(flat))
    peak = float(flat[k])
    null = np.concatenate((flat[:k], flat[k + 1:]))
    null = null[np.isfinite(null)]
    if null.size < 8 or not np.isfinite(peak):
        return peak, 1.0, k
    med = float(np.median(null))
    mad = float(np.median(np.abs(null - med)))
    scale = 1.4826 * mad
    # A null whose scores are one value has no Gaussian width: MAD is roundoff
    # next to the null's own magnitude, and any positive gap would become an
    # infinite z. Treat that as "not separated" when the peak does not clear
    # the null's own median by more than that roundoff, and as a floor tail
    # when it does. The allowance is a fraction of the null's magnitude, not
    # a fixed correlation gap.
    level = float(np.median(np.abs(null)))
    if scale <= 1e-9 * (1.0 + level):
        if peak <= med + 1e-9 * (1.0 + level):
            return peak, 1.0, k
        return peak, _CHANCE_FLOOR, k
    z = (peak - med) / scale
    if z <= 0.0:
        return peak, 1.0, k
    p_one = 0.5 * math.erfc(z / math.sqrt(2.0))
    return peak, _floor_chance(p_one * _N_SSS_HYP), k


def sss_search(iq, fs):
    """SSS check and the physical cell ID. No exception on a short or slow capture.

    See the module docstring for the method. ``pci`` is None when no PSS was
    found, the capture is shorter than 20 ms, or the chance is above 1e-3.
    ``half_frame`` is 0 when ``frame_offset_samples`` is the first sample of
    subframe 0 (prefix included), and 1 when the reported offset is 5 ms later
    and subframe 5's SSS starts that half. The offset is in samples of the
    capture's own rate, modulo 10 ms.
    """
    try:
        fs_in = float(fs)
    except (TypeError, ValueError):
        return _sss_none('not a sample rate')
    vec = _vector(iq)
    if vec is None or not np.isfinite(fs_in) or fs_in < _MIN_FS:
        return _sss_none('too short or too slow to check')
    if vec.size < _SSS_MIN_DUR * fs_in:
        return _sss_none('shorter than 20 ms', ran=True)
    prepared = _prepare(vec, fs_in)
    if prepared is None:
        return _sss_none('no LTE bandwidth class fits this sample rate', ran=True)
    block, rate, n_fft, fs_cap = prepared
    found = _cp_alignment(block, n_fft)
    if found is None:
        return _sss_none('no symbol grid to search', ran=True)
    _align, slot, cp, _n_slots, folded = found
    hit = _locate_pss(block, rate, n_fft, folded, slot, cp)
    if hit is None:
        return _sss_none('no PSS to equalise the SSS from', ran=True)
    parts = _equalise_sss(block, rate, n_fft, slot, hit)
    if parts is None:
        return _sss_none('PSS timing left no SSS symbol', ran=True)
    eq0, eq1, used0, used1 = parts
    n_used = int(used0.size + used1.size)
    templ = _sss_templates()[hit['root']]
    scores = _hypothesis_scores(eq0, eq1, templ)
    if scores is None:
        return _sss_none('SSS symbols were empty', ran=True)
    peak, chance, k = _sss_chance(scores)
    nid1, col = (int(v) for v in np.unravel_index(k, scores.shape))
    # Column 1 means group 0 carries subframe 5, so the frame starts 5 ms earlier.
    group0_is_sub5 = col == 1
    nid2 = int(hit['root'])
    pci = 3 * nid1 + nid2
    # A representative PSS of group 0 (or group 1 if group 0 contributed nothing).
    if used0.size:
        slot_i = int(used0[0])
        this_is_sub5 = group0_is_sub5
    else:
        slot_i = int(used1[0])
        this_is_sub5 = not group0_is_sub5
    body_off, _cp_short = _pss_body_offset(n_fft)
    pss_sample = slot_i * slot + hit['body']
    half_slots = 10 * slot
    frame = 20 * slot
    frame_start = pss_sample - body_off - (half_slots if this_is_sub5 else 0)
    frame_start_mod = frame_start % frame
    # Both labels name the same instant. Report the subframe-0 boundary.
    half_frame = 0
    frame_cap = int(round(0.010 * fs_cap))
    if frame_cap <= 0:
        return _sss_none('sample rate is not a capture', ran=True)
    offset = int(round(frame_start_mod * fs_cap / rate)) % frame_cap
    reported = pci if chance <= _PASS else None
    detail = (f'N_ID(1) {nid1} N_ID(2) {nid2} pci {pci} correlation {peak:.3f} '
              f'over {n_used} half-frames at {hit["cfo"]:.0f} Hz')
    return _sss_result(True, nid1 if reported is not None else None,
                       nid2 if reported is not None else None,
                       reported, half_frame if reported is not None else None,
                       offset if reported is not None else None,
                       peak, chance, n_used, detail)


def _pss_template(n_fft, root):
    """Unit-norm time-domain PSS body for ``root`` (the useful symbol, no prefix)."""
    spec = np.zeros(n_fft, dtype=np.complex128)
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    spec[bins] = _pss_seqs()[root]
    td = np.fft.ifft(spec)
    norm = np.linalg.norm(td)
    if norm <= 0.0:
        return td
    return td / norm


def _pss_match(block, td):
    """Normalised correlation of ``block`` against a unit-norm PSS body.

    ``out[t]`` is |<x[t:t+N], td>| / sqrt(sum |x[t:t+N]|^2). The peak sits on
    the first sample of the useful symbol. The FFT length is zero-padded so
    the match is linear, not circular, across the join of the block.
    """
    n = block.size
    n_fft = td.size
    if n <= n_fft:
        return np.zeros(n, dtype=np.float64)
    n_corr = 1 << int(n + n_fft - 1).bit_length()
    spec = np.fft.fft(block, n_corr) * np.conj(np.fft.fft(td, n_corr))
    corr = np.fft.ifft(spec)
    m = n - n_fft + 1
    mag = np.abs(corr[:m])
    power = np.abs(block) ** 2
    cum = np.empty(n + 1, dtype=np.float64)
    cum[0] = 0.0
    np.cumsum(power, out=cum[1:])
    energy = cum[n_fft:n_fft + m] - cum[:m]
    out = np.zeros(n, dtype=np.float64)
    out[:m] = mag / np.sqrt(np.maximum(energy, 1e-20))
    return out


def _blank_around(work, center, guard):
    """Write -1 over ``center`` ± ``guard``, wrapping on the frame."""
    n = work.size
    if n == 0 or guard < 0:
        return
    center = int(center) % n
    guard = int(guard)
    if 2 * guard + 1 >= n:
        work[:] = -1.0
        return
    a = center - guard
    b = center + guard
    if a >= 0 and b < n:
        work[a:b + 1] = -1.0
        return
    work[np.arange(a, b + 1) % n] = -1.0


def _frame_peaks(folded, n_keep, guard, floor):
    """The strongest samples of one frame of folded PSS correlation.

    Each peak blanks only its own neighbourhood, so a second tower half a
    frame away stays visible. The half-frame mate of a real cell is then a
    second peak of the same cell and is removed when PCIs are merged.
    Returns ``(index, height)`` strongest first, and nothing below ``floor``.
    """
    frame = folded.size
    if frame < 4 or n_keep < 1:
        return []
    work = folded.copy()
    found = []
    for _ in range(n_keep):
        i = int(np.argmax(work))
        height = float(folded[i])
        if height < floor or work[i] < 0.0:
            break
        found.append((i, height))
        _blank_around(work, i, guard)
    return found


def _sync_groups(block, n_fft, frame, peak):
    """PSS and SSS centre bins at ``peak`` and half a frame later.

    Each entry is ``(yp, ys)`` with shape ``(occasions, 62)``, or None when
    that half falls off the capture.
    """
    cp = _cp_lengths(n_fft)[1]
    half = frame // 2
    n = block.size
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    k = np.arange(n_fft, dtype=np.int64)

    def _grab(base):
        starts = np.arange(int(base) % int(frame), n - n_fft, frame, dtype=np.int64)
        sss_at = starts - (n_fft + cp)
        keep = (sss_at >= 0) & (starts + n_fft <= n)
        starts = starts[keep]
        sss_at = sss_at[keep]
        if starts.size < 1:
            return None
        yp = np.fft.fft(block[starts[:, None] + k], axis=1)[:, bins]
        ys = np.fft.fft(block[sss_at[:, None] + k], axis=1)[:, bins]
        return yp, ys

    return _grab(peak), _grab(int(peak) + half)


def _offset_from_pss(pss_mod, n_fft, frame, fs_cap, rate, sub5):
    """Capture-rate frame offset of the subframe-0 boundary, or None."""
    slot = frame // 20
    body_off, _cp_short = _pss_body_offset(n_fft)
    frame_start = int(pss_mod) - body_off - (10 * slot if sub5 else 0)
    frame_cap = int(round(0.010 * fs_cap))
    if frame_cap <= 0:
        return None
    return int(round((frame_start % frame) * fs_cap / rate)) % frame_cap


def _cells_at_timing(block, n_fft, fs_cap, rate, frame, peak):
    """Cells at one PSS timing: joint root gains, then SSS with cancellation.

    Roots are scored strongest gain first. A root whose SSS chance is above
    1e-3 is left in the residual and not reported. One that passes is
    subtracted before the next root, so a sector 6 dB down is not scored
    against the stronger sector's SSS.
    """
    pair = _sync_groups(block, n_fft, frame, peak)
    design = _pss_seqs().T  # (62, 3), the three roots side by side
    groups = []
    mags = np.zeros(3, dtype=np.float64)
    n_groups = 0
    for part in pair:
        if part is None:
            groups.append(None)
            continue
        yp, ys = part
        gain = np.linalg.lstsq(design, yp.T, rcond=None)[0].T
        groups.append({'ys': ys, 'g': gain})
        mags += np.abs(gain).mean(axis=0)
        n_groups += 1
    if n_groups < 1 or float(mags.max()) <= 0.0:
        return []
    templ = _sss_templates()
    order = np.argsort(-mags)
    found = []
    seen = set()
    # A root this far under the loudest gain at this timing is the fit's
    # leftover, not a sector. -10 dB is still above this cut. Two cells on
    # one root share that gain, so a root that already produced a PCI is
    # scored again on the residual.
    gain_cut = 0.18 * float(mags[order[0]])
    for root in order:
        root = int(root)
        if mags[root] < gain_cut:
            continue
        for _again in range(4):
            eqs = []
            n_used = 0
            for info in groups:
                if info is None:
                    eqs.append(np.empty((0, 62), dtype=np.complex128))
                    continue
                alpha = info['g'][:, root]
                n_used += int(alpha.size)
                denom = (np.abs(alpha) ** 2) + 1e-18
                eqs.append(info['ys'] * np.conj(alpha)[:, None] / denom[:, None])
            scores = _hypothesis_scores(eqs[0], eqs[1], templ[root])
            if scores is None:
                break
            peak_score, chance, k = _sss_chance(scores)
            if chance > _PASS:
                break
            nid1, col = (int(v) for v in np.unravel_index(k, scores.shape))
            pci = 3 * nid1 + root
            if pci in seen:
                break
            offset = _offset_from_pss(
                int(peak) % int(frame), n_fft, frame, fs_cap, rate, col == 1)
            if offset is None:
                break
            seen.add(pci)
            # Subtract this cell's own SSS amplitude, not the joint PSS gain.
            # Two cells on one root share that gain; taking all of it out with
            # the first SSS leaves a residual that correlates with a third,
            # invented, identity.
            seq0 = templ[root, nid1, 0]
            seq5 = templ[root, nid1, 1]
            power = 0.0
            n_pow = 0
            for gi, info in enumerate(groups):
                if info is None:
                    continue
                sub5 = (col == 1) if gi == 0 else (col == 0)
                seq = seq5 if sub5 else seq0
                amp = info['ys'] @ seq / 62.0
                power += float(np.mean(np.abs(amp)))
                n_pow += 1
                info['ys'] = info['ys'] - amp[:, None] * seq
            if n_pow:
                power /= n_pow
            else:
                power = float(mags[root])
            detail = (f'N_ID(1) {nid1} N_ID(2) {root} pci {pci} correlation {peak_score:.3f} '
                      f'over {n_used} half-frames')
            found.append({
                'pci': pci,
                'nid1': nid1,
                'nid2': root,
                'frame_offset_samples': int(offset),
                'half_frame': 0,
                'score': float(peak_score),
                'chance': _floor_chance(chance),
                'detail': detail,
                '_pss': power,
            })
    return found


def _cluster_timings(peaks, frame, guard):
    """Merge peaks that land within ``guard`` samples. Strongest position wins.

    ``peaks`` is a list of ``(pos, height)``. The half-frame mate is not
    merged: it is the same cell's other PSS and is deduplicated by PCI.
    """
    clusters = []
    for pos, height in sorted(peaks, key=lambda item: item[1], reverse=True):
        placed = False
        for cluster in clusters:
            delta = abs(int(pos) - int(cluster[0]))
            dist = min(delta, frame - delta)
            if dist <= guard:
                placed = True
                break
        if not placed:
            clusters.append((int(pos), float(height)))
    return clusters


def cells(iq, fs, max_cells=4):
    """Every LTE cell on one carrier whose SSS chance is at most 1e-3.

    See the module docstring for the method. The list is strongest first.
    ``relative_power_db`` is 0.0 on the first entry and negative after it.
    An empty list (no exception) means the capture is under 20 ms, under
    1.92 MS/s, or nothing in it cleared the SSS line. ``max_cells`` caps
    the list after that cut. A PCI is never filled in from the PSS alone.
    """
    try:
        fs_in = float(fs)
        limit = int(max_cells)
    except (TypeError, ValueError):
        return []
    if limit < 1:
        return []
    vec = _vector(iq)
    if vec is None or not np.isfinite(fs_in) or fs_in < _MIN_FS:
        return []
    if vec.size < _SSS_MIN_DUR * fs_in:
        return []
    prepared = _prepare(vec, fs_in)
    if prepared is None:
        return []
    block, rate, n_fft, fs_cap = prepared
    _starts, _lens, slot, _cp = _slot_layout(n_fft)
    frame = 20 * slot
    if block.size < frame:
        return []
    # Shoulders of one PSS are a few dozen samples wide (62 bins in a 1024
    # transform). A second cell on the same root is much further away.
    guard = max(24, n_fft // 32)
    # Below this the folded match is what noise and the non-LTE negatives
    # reach. A cell at 0 dB, and one 10 dB under another root, sit above it.
    floor = 0.10
    raw = []
    for root in range(3):
        match = _pss_match(block, _pss_template(n_fft, root))
        n_frames = match.size // frame
        if n_frames < 1:
            continue
        folded = match[:n_frames * frame].reshape(n_frames, frame).mean(axis=0)
        # Enough peaks that a same-root neighbour under the strong cell's
        # sidelobes is still in the list. The SSS chance drops the sidelobes.
        raw.extend(_frame_peaks(folded, 8, guard, floor))
    found = []
    for pos, _height in _cluster_timings(raw, frame, 6):
        found.extend(_cells_at_timing(block, n_fft, fs_cap, rate, frame, pos))
    frame_cap = int(round(0.010 * fs_cap))
    return _finish_cells(found, limit, frame_cap)


def _drop_same_root_ghosts(kept, frame_cap):
    """Drop a same-root hit that sits just off a stronger cell's timing.

    Sectors that share a timing have different roots. A second cell on the
    same root is another tower, so its timing is nowhere near the first.
    A peak a few hundred samples away is a sidelobe of the strong PSS: the
    SSS can still clear 1e-3 there when two equal cells were cancelled, and
    the PCI is not one of the two. Same-sample hits (one root, two SSS) stay.
    """
    if frame_cap <= 0:
        return kept
    slot = frame_cap // 20
    drop = set()
    for weak in kept:
        for strong in kept:
            if weak['pci'] == strong['pci'] or weak['nid2'] != strong['nid2']:
                continue
            if weak['_pss'] > strong['_pss']:
                continue
            delta = abs(weak['frame_offset_samples'] - strong['frame_offset_samples'])
            dist = min(delta, frame_cap - delta)
            if 4 < dist < slot:
                drop.add(weak['pci'])
                break
    if not drop:
        return kept
    return [hit for hit in kept if hit['pci'] not in drop]


def _finish_cells(found, limit, frame_cap):
    """One entry per PCI, strongest PSS first, relative power against that one."""
    best = {}
    for hit in found:
        pci = hit['pci']
        prev = best.get(pci)
        if prev is None or hit['_pss'] > prev['_pss']:
            best[pci] = hit
    kept = sorted(best.values(), key=lambda h: h['_pss'], reverse=True)
    kept = _drop_same_root_ghosts(kept, frame_cap)
    kept = kept[:limit]
    if not kept:
        return []
    top = kept[0]['_pss']
    out = []
    for i, hit in enumerate(kept):
        if i == 0 or top <= 0.0:
            rel = 0.0
        else:
            rel = float(20.0 * math.log10(max(hit['_pss'], 1e-30) / top))
        out.append({
            'pci': int(hit['pci']),
            'nid1': int(hit['nid1']),
            'nid2': int(hit['nid2']),
            'frame_offset_samples': int(hit['frame_offset_samples']),
            'half_frame': int(hit['half_frame']),
            'relative_power_db': rel,
            'score': float(hit['score']),
            'chance': float(hit['chance']),
            'detail': hit['detail'],
        })
    return out


def _apply_sss(iq, fs, checks, verdict, nid2):
    """Run ``sss_search`` and fold its PCI into the fingerprint.

    The call goes through the module-level name so a test can replace it.
    A PCI whose N_ID(2) is not the PSS root drops the verdict to ``possible``.
    """
    found = sss_search(iq, fs)
    detail = str(found.get('detail', ''))
    pci = found.get('pci')
    if pci is not None and nid2 is not None and int(pci) % 3 != int(nid2):
        verdict = 'possible'
        detail = detail + '; N_ID(2) mismatch with the PSS root'
    checks.append(_check('sss', bool(found.get('ran')), found.get('score'),
                         found.get('chance'), detail))
    checks.append(_later('crs', 'CRS is a later stage'))
    return checks, verdict, pci


def fingerprint(iq, fs):
    """Structural fingerprint. See the module docstring for the method.

    ``verdict`` is ``lte`` when the CP and PSS checks both pass at 1e-3,
    ``possible`` when one does, ``not-lte`` when neither does, and ``unknown``
    when the capture is shorter than 10 ms or slower than 1.92 MS/s. It also
    drops to ``possible`` when the SSS cell ID and the PSS root disagree.
    ``pci`` is the SSS physical cell ID, or None when that check has none.
    ``chance`` is the product of the CP and PSS chances, floored at 1e-300.
    ``cells`` is ``cells()`` of this capture (the module-level name, so a
    caller can replace it): every cell that cleared the SSS line.
    """
    if _vector(iq) is None or _too_small(iq, fs):
        try:
            slow = float(fs) < _MIN_FS
        except (TypeError, ValueError):
            slow = True
        if _vector(iq) is None and not slow:
            why = 'not a 1-D capture'
        else:
            why = 'sample rate under 1.92 MS/s' if slow else 'shorter than 10 ms'
        return _unknown(why, iq, fs)

    prepared = _prepare(iq, fs)
    if prepared is None:
        return _unknown('no LTE bandwidth class fits this sample rate', iq, fs)

    block, rate, n_fft, fs_in = prepared
    found = _cp_alignment(block, n_fft)
    if found is None:
        cp = _check('cp-autocorr', True, 0.0, 1.0, 'shorter than two slots')
        pss = _check('pss', True, 0.0, 1.0, 'no symbol grid to search')
    else:
        align, slot, cp_len, _n_slots, folded = found
        score, chance = _cp_chance(align)
        phase0 = int(np.argmax(align))
        n_class, n_used = _bandwidth(block, n_fft, phase0, fs_in)
        cp = _check(
            'cp-autocorr', True, score, chance,
            f'prefix correlation {score:.3f} over {cp_len} samples, '
            f'about {n_used} subcarriers in use',
        )
        peak, root, cfo, obs, _n = _pss_on_grid(block, rate, n_fft, folded, slot, cp_len)
        pss_chance = _pss_chance(peak, obs)
        pss = _check(
            'pss', True, peak, pss_chance,
            f'best root {root} correlation {peak:.3f} at {cfo:.0f} Hz',
        )
        # N_ID(2) only when the root itself cleared the line. The class FFT
        # size only when the prefix did: a noise spectrum still has a widest
        # bump, and it is not a bandwidth.
        nid2 = int(root) if pss['passed'] and root is not None else None
        nfft = n_class if cp['passed'] else None
        n_pass = int(cp['passed']) + int(pss['passed'])
        if n_pass == 2:
            verdict = 'lte'
        elif n_pass == 1:
            verdict = 'possible'
        else:
            verdict = 'not-lte'
        checks, verdict, pci = _apply_sss(iq, fs, [cp, pss], verdict, nid2)
        joint = _floor_chance(cp['chance'] * pss['chance'])
        return {
            'checks': checks,
            'verdict': verdict,
            'pci': pci,
            'nid2': nid2,
            'nfft': nfft,
            'chance': joint,
            'cells': cells(iq, fs),
        }

    checks, verdict, pci = _apply_sss(iq, fs, [cp, pss], 'not-lte', None)
    joint = _floor_chance(cp['chance'] * pss['chance'])
    return {
        'checks': checks,
        'verdict': verdict,
        'pci': pci,
        'nid2': None,
        'nfft': None,
        'chance': joint,
        'cells': cells(iq, fs),
    }
