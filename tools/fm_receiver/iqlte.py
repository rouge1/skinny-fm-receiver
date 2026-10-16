"""LTE structural fingerprint of a centred capture (normal cyclic prefix only).

Given complex IQ and its sample rate, say whether the block is an LTE FDD
downlink, from the signal alone. Two checks run.

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
and symbol that was tried. SSS and CRS are named and not run.

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
# Coarse enough that a 5 kHz offset leaves only a few hundred Hz, fine enough
# that a 0.1 s capture stays cheap. The prefix itself is not derotated.
_CFO_HZ = np.arange(-6000.0, 6000.1, 2000.0)

_PSS_SEQ = None  # (3, 62), unit-magnitude Zadoff-Chu, filled on first use


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


def _unknown(reason):
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
    }


def _too_small(iq, fs):
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        return True
    if not np.isfinite(fs) or fs < _MIN_FS:
        return True
    n = 0 if iq is None else len(iq)
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
    """Snap a measured subcarrier count to a class whose native rate fits in ``fs``."""
    best_n = None
    best_err = None
    for n_fft, rate, n_sc in _CLASSES:
        if rate > fs * (1.0 + 5e-6):
            continue
        err = abs(int(n_sc) - int(n_used))
        if best_err is None or err < best_err:
            best_err = err
            best_n = n_fft
    if best_n is None or best_err > max(24, int(0.15 * max(n_used, 1))):
        return None
    return best_n


def _occupied_subcarriers(power):
    """How many subcarriers around DC are in use. DC itself is empty and not counted.

    The noise reference is the outer guard, which every class leaves empty when
    the transform is the widest class that fits. A bin is occupied when it holds
    more than twice that floor: at 0 dB the used bins still sit near 2.7 times
    the empty ones, and a tenth of a second of symbols makes the two levels
    tight. A hole of a few bins (the DC bin) does not break the walk.
    """
    n = power.size
    ps = np.fft.fftshift(power)
    edge = max(4, n // 12)
    noise = float(min(np.median(ps[:edge]), np.median(ps[-edge:])))
    if not np.isfinite(noise) or noise <= 0.0:
        return 0
    above = ps > (noise * 2.0)
    center = n // 2
    gap = 4

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


def _pss_chance(peak, r2):
    """Beta(1, β) tail of the best correlation, β fitted to this capture's bulk.

    Random 62-bins give R² ~ Beta(1, 61). A signal whose centre bins are not a
    Zadoff-Chu root has a heavier or lighter bulk; the 90th percentile of this
    capture, with the very top excluded so a real PSS does not set the fit,
    fixes β. The chance is then the probability the best of the trials beats
    ``peak``.
    """
    if r2.size < 8 or not np.isfinite(peak):
        return 1.0
    peak = float(np.clip(peak, 0.0, 1.0))
    cut = float(np.quantile(r2, 0.995))
    bulk = r2[r2 <= cut]
    if bulk.size < 8:
        bulk = r2
    q = float(np.quantile(bulk, 0.9))
    q = min(max(q, 1e-12), 1.0 - 1e-9)
    beta = math.log(0.1) / math.log(1.0 - q)
    if beta <= 0.0:
        return 1.0
    u = min(peak * peak, 1.0 - 1e-15)
    p_one = (1.0 - u) ** beta
    return _extreme(p_one, r2.size)


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


def _correlate_pss(spec, bins, ref_m):
    """Correlation of each symbol's central bins with the three unit-norm roots."""
    obs = spec[:, bins]
    norms = np.linalg.norm(obs, axis=1)
    obs_n = np.zeros_like(obs)
    good = norms > 1e-12
    obs_n[good] = obs[good] / norms[good, None]
    return np.abs(obs_n @ ref_m)


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
        return 0.0, None, 0.0, np.zeros(1), 0
    refs = _pss_seqs()
    ref_m = (refs / np.linalg.norm(refs, axis=1, keepdims=True)).conj().T
    bins = np.concatenate((np.arange(-31, 0), np.arange(1, 32))) % n_fft
    k = np.arange(n_fft)
    # Offsets that stay inside a normal prefix, so the shift is circular.
    offsets = range(-1, 2)
    best = 0.0
    best_root = 0
    best_cfo = 0.0
    chunks = []
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
                corr = _correlate_pss(spec_d, bins, ref_m)
                chunks.append((corr * corr).ravel())
                flat = int(np.argmax(corr))
                m = float(corr.ravel()[flat])
                if m > best:
                    best = m
                    best_root = int(flat % 3)
                    best_cfo = float(f)
    if not chunks:
        return 0.0, None, 0.0, np.zeros(1), 0
    return best, best_root, best_cfo, np.concatenate(chunks), n_sym


def _prepare(iq, fs):
    """Return analysis IQ at a class rate, or None when the block cannot be one."""
    if _too_small(iq, fs):
        return None
    fs = float(fs)
    plan = _analysis_plan(fs)
    if plan is None:
        return None
    n_fft, rate, _n_sc = plan
    block = _resample(np.asarray(iq), fs, rate)
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
    peak, root, cfo, r2, _n = _pss_on_grid(block, rate, n_fft, folded, slot, cp)
    chance = _floor_chance(_pss_chance(peak, r2))
    passed = chance <= _PASS
    return {
        'score': float(peak),
        'chance': chance,
        'passed': passed,
        'nid2': int(root) if passed and root is not None else None,
        'detail': f'best root {root} correlation {peak:.3f} at {cfo:.0f} Hz',
    }


def fingerprint(iq, fs):
    """Structural fingerprint. See the module docstring for the method.

    ``verdict`` is ``lte`` when both checks pass at 1e-3, ``possible`` when one
    does, ``not-lte`` when neither does, and ``unknown`` when the capture is
    shorter than 10 ms or slower than 1.92 MS/s. ``pci`` is always None here.
    ``chance`` is the product of the two check chances, floored at 1e-300.
    """
    if _too_small(iq, fs):
        try:
            slow = float(fs) < _MIN_FS
        except (TypeError, ValueError):
            slow = True
        why = 'sample rate under 1.92 MS/s' if slow else 'shorter than 10 ms'
        return _unknown(why)

    prepared = _prepare(iq, fs)
    if prepared is None:
        return _unknown('no LTE bandwidth class fits this sample rate')

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
        peak, root, cfo, r2, _n = _pss_on_grid(block, rate, n_fft, folded, slot, cp_len)
        pss_chance = _pss_chance(peak, r2)
        pss = _check(
            'pss', True, peak, pss_chance,
            f'best root {root} correlation {peak:.3f} at {cfo:.0f} Hz',
        )
        # N_ID(2) only when the root itself cleared the line. The class FFT
        # size only when the prefix did: a noise spectrum still has a widest
        # bump, and it is not a bandwidth.
        nid2 = int(root) if pss['passed'] and root is not None else None
        nfft = n_class if cp['passed'] else None
        checks = [cp, pss, _later('sss', 'SSS is a later stage'), _later('crs', 'CRS is a later stage')]
        n_pass = int(cp['passed']) + int(pss['passed'])
        if n_pass == 2:
            verdict = 'lte'
        elif n_pass == 1:
            verdict = 'possible'
        else:
            verdict = 'not-lte'
        joint = _floor_chance(cp['chance'] * pss['chance'])
        return {
            'checks': checks,
            'verdict': verdict,
            'pci': None,
            'nid2': nid2,
            'nfft': nfft,
            'chance': joint,
        }

    checks = [cp, pss, _later('sss', 'SSS is a later stage'), _later('crs', 'CRS is a later stage')]
    joint = _floor_chance(cp['chance'] * pss['chance'])
    return {
        'checks': checks,
        'verdict': 'not-lte',
        'pci': None,
        'nid2': None,
        'nfft': None,
        'chance': joint,
    }
