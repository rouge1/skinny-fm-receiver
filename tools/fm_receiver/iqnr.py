"""5G NR synchronisation-block (SSB) finder for a recording. Numpy only.

Given complex IQ, its sample rate and centre frequency, list every NR cell whose
SS/PBCH block is in the capture: where it sits on the sync raster (GSCN), its
subcarrier spacing (15 or 30 kHz), physical cell ID (PCI), SSB index and
half-frame (from the PBCH DM-RS), burst period, carrier offset, and a chance
estimate. It decodes nothing: no PBCH bits, no MIB. NR's sync signals are
m-sequences, not LTE's Zadoff-Chu, so ``iqlte`` can only call an NR carrier
"possible"; this module is the NR counterpart of its PSS/SSS search.

Standard used (TS 38.211, 38.213, 38.104)
-----------------------------------------
- 38.104 table 5.4.3.1-1: the sync raster. Below 3 GHz SS_REF = N x 1200 kHz +
  M x 50 kHz (M = 1, 3, 5; GSCN = 3N + (M - 3)/2); from 3 GHz SS_REF = 3 GHz +
  N x 1.44 MHz (GSCN = 7499 + N). ``gscn_to_hz``, ``hz_to_gscn``, ``raster_in_band``.
- 38.211 7.4.3.1: an SSB is 4 OFDM symbols x 240 subcarriers, SS_REF at
  subcarrier 120. PSS in symbol 0 and SSS in symbol 2, both on k = 56..182;
  PBCH DM-RS in symbols 1, 2, 3 on every fourth subcarrier from k = PCI mod 4.
- 38.211 7.4.2.2: PSS d(n) = 1 - 2 x((n + 43 N_ID2) mod 127), x(i+7) =
  (x(i+4) + x(i)) mod 2. 7.4.2.3: SSS, a product of two m-sequences shifted by
  m0 = 15 floor(N_ID1/112) + 5 N_ID2 and m1 = N_ID1 mod 112. PCI = 3 N_ID1 + N_ID2.
- 38.211 7.4.1.4.1: DM-RS r(m) from the length-31 Gold sequence, c_init =
  2^11 (i_bar + 1)(floor(PCI/4) + 1) + 2^6 (i_bar + 1) + PCI mod 4. With L_max 4,
  i_bar = i_SSB + 4 n_hf (so the half-frame bit is read); with L_max 8,
  i_bar = i_SSB mod 8 (no half-frame bit). L_max is 4 at or below 3 GHz, else 8.
- 38.211 5.3.1: normal cyclic prefix, 144 nfft/2048 samples. The SSBs of cases
  A, B and C never contain a long-prefix symbol, so the four symbols of an SSB
  are evenly spaced (nfft + CP samples).

Method
------
1. Band selection. For one candidate frequency (``ssb_hz``, or each raster
   point) the capture is cut to the SSB's own band in the frequency domain: one
   FFT of the whole capture is kept, the slice of bins around the candidate is
   moved to DC and inverse-transformed at 256 x SCS (3.84 MS/s at 15 kHz,
   7.68 MS/s at 30 kHz, the SSB's native rate with nfft 256), and again at half
   that rate for the PSS scan (the PSS is 127 subcarriers, half the band). A
   brick-wall filter and an exact resampler in one step, and the cost of the
   first FFT is shared by every candidate. The capture length is trimmed to a
   multiple of the rate ratio's denominator so the output rate is exact. The
   sub-bin remainder of the candidate's offset (under half a bin of the
   capture, 5 Hz at 100 ms) is left in the CFO.
2. PSS search. The time-domain PSS symbol (128 samples at the half rate) for each
   N_ID2 is correlated over the whole capture (overlap-save blocks of 4096, in
   single precision: this finds peaks, it does not measure them). The
   carrier-frequency error is a few kHz, up to a third of a subcarrier, which
   turns the replica by a large part of a cycle over one symbol; so the
   replicas are also rotated on a grid of five offsets spanning +/- SCS/3
   (+/- 5 kHz at 15 kHz, +/- 10 kHz at 30 kHz). The score at each lag is the
   best of the 15 normalised magnitudes |c|^2 / (E_replica E_window). The
   strongest well-separated peaks (at least 10 x the median score, at most
   16 + one per 5 ms) are the SSB candidates; the peak's replica gives N_ID2.
3. Timing and equalisation. At each peak the four symbols of the SSB are cut
   starting four samples early (inside the cyclic prefix, so no leakage from
   the previous symbol), de-rotated by the grid offset and FFT'd. A timing
   error is a linear phase ramp over the subcarriers, which wrecks a
   127-subcarrier correlation (one sample is 3 rad over the PSS), and it is
   not small at low SNR. The received PSS divided by the known sequence is
   fitted by a matched search over the ramp slope (801 slopes, then a
   parabolic refinement); the same ramp is removed from the other three symbols.
   The slope is also the fractional timing: it gives the burst's start time.
4. SSS search. The SSS bins are correlated with the 336 N_ID1 sequences of the
   PSS's N_ID2. Each hypothesis scores s = |<d, Y>|^2 / (127 |Y|^2), in [0, 1].
   Y is the received vector, so the score needs no noise estimate. The
   null for a vector with no sequence in it is s ~ Beta(1, 126), whose tail is
   (1 - s)^126; E = -126 ln(1 - s) is then exponential(1) under the null.
   Bursts that agree on (N_ID2, N_ID1) are one cell. Its chance is the tail of
   its best burst's E, multiplied by every trial made: 336 hypotheses x the
   peaks examined x the frequencies and spacings searched. A cell is listed
   only when that chance is at most 1e-3: a PCI is never guessed. The other
   bursts only count (``bursts``) and time the period.
5. DM-RS. With the PCI known, the eight i_bar sequences are correlated per
   symbol (the three symbols are scored separately because the CFO turns the
   phase from symbol to symbol, then their E add). The winning i_bar gives
   ssb_index and, at L_max 4, half_frame; both are None unless the DM-RS
   chance (eight trials) is at most 1e-2.
6. CFO. A cyclic-prefix sample and the sample one useful symbol later are equal,
   so an offset turns their product by 2 pi f nfft / fs. The four prefixes of
   every burst of the cell are summed and the argument read: unambiguous within
   +/- SCS/2 (7.5 kHz at 15 kHz, 15 kHz at 30 kHz), which covers the asked
   range. cfo_hz is that offset from ``ssb_hz``.
7. Period. Bursts of one SSB index (one i_bar at L_max 8, one i_SSB at L_max 4,
   whose half-frame bit may alternate) are timed; the smallest spacing of at
   least 4 ms, snapped to 5, 10, 20, 40, 80 or 160 ms, is the period. It is
   None with fewer than two bursts of one index.
8. Raster mode runs steps 1 to 7 at every point of ``raster_in_band`` for both
   spacings and keeps one entry per (GSCN, PCI), the lower chance first.
   Entries are ordered by SSB power (the PSS and SSS subcarrier power, noise
   included; ``relative_power_db`` is against the strongest).

Wrong turns
-----------
The first four were ruled out in design (from ``iqlte``'s history and a hand
calculation); the rest were hit while building this.

- Correlating the PSS with no carrier-frequency grid. At 5 kHz (1/3 of a 15 kHz
  subcarrier) the replica turns 2 rad over one symbol and the peak is gone.
  The grid of five offsets removes it; LTE's ``iqlte`` met the same.
- Taking the timing from the PSS peak as an integer sample and FFT-ing there.
  A half-sample error is 1.5 rad of ramp across the PSS and an SSS correlation
  of 127 terms loses most of its height; at 0 dB it is the difference between
  finding the cell and not. The ramp is estimated, not rounded.
- Estimating that ramp from the phase of adjacent subcarrier products. At 0 dB
  SNR its error, times 64 subcarriers out from the centre, is many radians. The
  matched search over slopes is the maximum-likelihood estimate and is
  accurate at that SNR.
- Reading the CFO from the phase step between the PSS, SSS and DM-RS symbols.
  Subcarriers sit at fixed frequencies from the carrier centre, so after the
  capture is moved to the SSB the phase also steps by 2 pi f_o (nfft + CP) / fs,
  where f_o is the SSB's offset from the carrier centre, which a receiver does
  not know. The estimate was right at f_o = 0 and 11 kHz off at 5 MHz. The
  cyclic prefix has no such term.
- Summing the E of bursts that agree on the cell ID. A bursty signal (the
  uplink-like negative: 2 ms on, 5 ms off) puts its strongest PSS peaks at the
  same place of every burst, and the same wrong N_ID1 won each time, so the
  "independent" sum gave chances of 1e-14 for four false cells. The chance is
  the best burst's alone; agreement only counts bursts.
- Combining the DM-RS of the three symbols coherently. The CFO has turned the
  phase by 1 or 2 rad between symbols, so they are scored one at a time and
  added in E.
- A trial factor of only 336. The best of the many peaks and frequencies tried
  then lands on the 1e-3 line for noise; the factor is
  336 x PSS peaks submitted to an SSS trial (every one, before any is dropped for
  falling too near the capture edge) x frequencies x spacings, so only a strong match clears it.
- Scanning the PSS at the full working rate with the offset grid: the
  elementwise work of 15 correlations dominated a raster search (63 candidates
  at 20 MS/s). Half the rate and one batched argmax cut it by a third.
"""

import math
from fractions import Fraction
from functools import lru_cache

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

_NFFT = 256                    # working FFT size; rate = 256 x SCS
_CP = 144 * _NFFT // 2048      # normal cyclic prefix, 18 samples
_SYM = _NFFT + _CP             # symbol pitch inside an SSB, 274 samples
_BACK = 4                      # FFT window starts this many samples early
_BLOCK = 4096                  # overlap-save block of the PSS scan
_N_SC = 240
_REF_K = 120
_MIN_FS = 3.84e6
_MIN_SECONDS = 2e-3
_PASS = 1e-3                   # SSS chance limit
_DMRS_PASS = 1e-2
_CFO_STEPS = (-1.0, -0.5, 0.0, 0.5, 1.0)     # x SCS/3
_PSS_FLOOR = 10.0              # peak must reach this many medians
_PERIODS = (5, 10, 20, 40, 80, 160)
_FLOOR = 1e-300
_N_SLOPE = 801
_SLOPE_MAX = 0.4               # rad per subcarrier (+/- 16 samples)
_PSS_X = (0, 1, 1, 0, 1, 1, 1)


# ----- sync raster ------------------------------------------------------------------

def gscn_to_hz(gscn):
    """SS_REF in Hz of a GSCN (38.104 table 5.4.3.1-1); ValueError outside 2..22255."""
    g = int(gscn)
    if g != gscn or g < 2 or g > 22255:
        raise ValueError(f'GSCN {gscn!r} is outside 2..22255')
    if g <= 7498:
        n = (g + 1) // 3
        m = 3 + 2 * (g - 3 * n)
        return float(n * 1200000 + m * 50000)
    return float(3000000000 + (g - 7499) * 1440000)


def hz_to_gscn(hz, tol_hz=1000.0):
    """GSCN of the raster point within ``tol_hz`` of ``hz``, or None."""
    hz = float(hz)
    if not math.isfinite(hz):
        return None
    best, best_d = None, float(tol_hz)
    n0 = int(hz // 1200000)
    for n in range(n0 - 1, n0 + 2):
        for m in (1, 3, 5):
            if 1 <= n <= 2499:
                d = abs(hz - (n * 1200000 + m * 50000))
                if d <= best_d:
                    best, best_d = 3 * n + (m - 3) // 2, d
    k = int(round((hz - 3e9) / 1.44e6))
    if 0 <= k <= 14756:
        d = abs(hz - (3e9 + k * 1.44e6))
        if d <= best_d:
            best, best_d = 7499 + k, d
    return best


@lru_cache(maxsize=1)
def _raster_table():
    g = np.arange(2, 22256)
    n = (g + 1) // 3
    low = n * 1200000 + (3 + 2 * (g - 3 * n)) * 50000
    hz = np.where(g <= 7498, low, 3000000000 + (g - 7499) * 1440000)
    return g, hz.astype(np.int64)


def raster_in_band(center_hz, fs, scs_khz):
    """[(gscn, hz)] ascending: every raster point whose whole SSB (SS_REF +/- 120 x SCS)
    lies inside center_hz +/- 0.45 fs."""
    half = int(round(120 * float(scs_khz) * 1e3))
    lo = int(round(center_hz - 0.45 * fs)) + half
    hi = int(round(center_hz + 0.45 * fs)) - half
    g, hz = _raster_table()
    keep = (hz >= lo) & (hz <= hi)
    return [(int(a), float(b)) for a, b in zip(g[keep], hz[keep])]


# ----- sequences --------------------------------------------------------------------

def _mseq(init, taps, length=127):
    x = np.zeros(length + 7, dtype=np.int64)
    x[:7] = init
    for i in range(length):
        x[i + 7] = sum(x[i + t] for t in taps) % 2
    return x[:length]


@lru_cache(maxsize=1)
def _pss_table():
    x = _mseq(_PSS_X, (4, 0))
    n = np.arange(127)
    return np.array([1 - 2 * x[(n + 43 * r) % 127] for r in range(3)], dtype=np.float64)


@lru_cache(maxsize=1)
def _sss_table():
    """(3, 336, 127): the SSS sequence of every N_ID1 for each N_ID2."""
    x0 = _mseq((1, 0, 0, 0, 0, 0, 0), (4, 0))
    x1 = _mseq((1, 0, 0, 0, 0, 0, 0), (1, 0))
    n = np.arange(127)
    nid1 = np.arange(336)
    out = np.zeros((3, 336, 127))
    for r in range(3):
        m0 = 15 * (nid1 // 112) + 5 * r
        m1 = nid1 % 112
        out[r] = (1 - 2 * x0[(n[None, :] + m0[:, None]) % 127]) * \
                 (1 - 2 * x1[(n[None, :] + m1[:, None]) % 127])
    return out


def _gold(c_init, nbits):
    total = 1600 + nbits
    x1 = np.zeros(total + 31, dtype=np.uint8)
    x1[0] = 1
    x2 = np.zeros(total + 31, dtype=np.uint8)
    x2[:31] = (int(c_init) >> np.arange(31)) & 1
    for n in range(total):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return (x1 ^ x2)[1600:1600 + nbits]


@lru_cache(maxsize=4096)
def _dmrs(pci, i_bar):
    """The 144 DM-RS values split by symbol: (symbol 1: 60, symbol 2: 24, symbol 3: 60)."""
    c_init = (2 ** 11) * (i_bar + 1) * (pci // 4 + 1) + (2 ** 6) * (i_bar + 1) + (pci % 4)
    c = _gold(c_init, 288).astype(np.float64)
    r = ((1 - 2 * c[0::2]) + 1j * (1 - 2 * c[1::2])) / np.sqrt(2.0)
    return r[:60], r[60:84], r[84:]


def _dmrs_bins(pci):
    """FFT indices (centred on SS_REF) of the DM-RS in symbols 1, 2, 3."""
    v = pci % 4
    k1 = v + 4 * np.arange(60)
    k2 = np.concatenate((v + 4 * np.arange(12), v + 192 + 4 * np.arange(12)))
    return ((k1 - _REF_K) % _NFFT, (k2 - _REF_K) % _NFFT, (k1 - _REF_K) % _NFFT)


_Q_SYNC = np.arange(56, 183) - _REF_K               # -64..62
_IDX_SYNC = _Q_SYNC % _NFFT


@lru_cache(maxsize=1)
def _slope_matrix():
    a = np.linspace(-_SLOPE_MAX, _SLOPE_MAX, _N_SLOPE)
    return a, np.exp(-1j * np.outer(a, _Q_SYNC))


@lru_cache(maxsize=8)
def _replicas(scs_khz):
    """Time-domain PSS symbols at half the working rate (128 samples: the PSS spans 127
    subcarriers), 3 roots x 5 frequency offsets, rows root-major; the offsets in Hz and
    the replica energy."""
    d = _pss_table()
    nd = _NFFT // 2
    t = np.arange(nd)
    rate = nd * scs_khz * 1e3
    reps = []
    cfos = [s * scs_khz * 1e3 / 3.0 for s in _CFO_STEPS]
    for r in range(3):
        spec = np.zeros(nd, dtype=np.complex128)
        spec[_Q_SYNC % nd] = d[r]
        td = np.fft.ifft(spec)
        for f in cfos:
            reps.append(td * np.exp(2j * np.pi * f * t / rate))
    reps = np.array(reps)
    return reps, cfos, float(np.sum(np.abs(reps[0]) ** 2))


@lru_cache(maxsize=16)
def _ramp(f_hz, rate):
    return np.exp(-2j * np.pi * f_hz * np.arange(3 * _SYM + _NFFT + 8) / rate)


# ----- statistics -------------------------------------------------------------------

def _tail(total, count):
    """P(Gamma(count, 1) >= total), floored at 1e-300."""
    if total <= 0:
        return 1.0
    count = max(1, int(count))
    lt = math.log(total)
    logs = [i * lt - math.lgamma(i + 1) for i in range(count)]
    m = max(logs)
    lq = -total + m + math.log(sum(math.exp(v - m) for v in logs))
    if lq < -690:
        return _FLOOR
    return max(_FLOOR, min(1.0, math.exp(lq)))


def _evidence(s, n):
    """E = -(n - 1) ln(1 - s): exponential(1) under 'no sequence' for a unit-norm match over n terms."""
    return -(n - 1.0) * np.log(np.clip(1.0 - np.asarray(s), 1e-15, 1.0))


# ----- band extraction --------------------------------------------------------------

class _Capture:
    """The whole capture's spectrum, shared by every candidate."""

    def __init__(self, x, fs):
        self.x = x
        self.fs = float(fs)
        self.spec = {}

    def band(self, offset_hz, scs_khz):
        """The capture moved by -offset_hz and cut to the SSB's band at 256 x SCS, and again
        at half that rate (the PSS fills only half of it). Returns (y, rate, y_half, rate_half),
        or None when the band does not fit in the capture's rate."""
        fs = self.fs
        out_rate = _NFFT * scs_khz * 1e3
        if abs(offset_hz) + 0.5 * _N_SC * scs_khz * 1e3 > 0.5 * fs * 1.0001 or fs < out_rate:
            return None
        n = len(self.x)
        frac = Fraction(int(round(out_rate)), int(round(fs)))
        step = 2 * frac.denominator
        n_use = n - n % step
        if n_use < 0.5 * n or n_use < 7680:
            n_use = n - n % 2
        m = int(round(n_use * out_rate / fs))
        md = m // 2
        if n_use not in self.spec:
            self.spec[n_use] = np.fft.fft(self.x[:n_use])
        spec = self.spec[n_use]
        b = int(round(offset_hz * n_use / fs))
        q = np.arange(-(m // 2), m - m // 2)
        z = np.roll(spec.take(q + b, mode='wrap'), -(m // 2))        # bins b-m/2 .. b+m/2 to FFT order
        zd = np.roll(z.take(np.arange(-(md // 2), md - md // 2), mode='wrap'), -(md // 2))
        y = np.fft.ifft(z) * (m / float(n_use))
        yd = np.fft.ifft(zd) * (md / float(n_use))
        rate = fs * m / n_use
        rate_d = fs * md / n_use
        return y, rate, yd, rate_d


# ----- PSS scan ---------------------------------------------------------------------

def _scan(y, reps, er):
    """Normalised PSS match |c|^2 / (E_rep E_win) at every lag, best over the replicas.
    Returns (score, which replica won)."""
    n = len(y)
    nf = reps.shape[1]
    n_out = n - nf + 1
    hop = _BLOCK - nf + 1
    nblk = -(-n_out // hop)
    yp = np.zeros((nblk - 1) * hop + _BLOCK, dtype=np.complex64)   # single precision: a peak
    yp[:n] = y                                                      # finder, not a measurement
    spec = np.conj(np.fft.fft(reps, _BLOCK, axis=1)).astype(np.complex64)
    nr = len(reps)
    best = np.zeros(nblk * hop, dtype=np.float32)
    arg = np.zeros(nblk * hop, dtype=np.int16)
    win = sliding_window_view(yp, _BLOCK)[::hop][:nblk]
    step = 64
    for a in range(0, nblk, step):
        yb = np.fft.fft(win[a:a + step], axis=1)
        pw = np.empty((nr, yb.shape[0], hop), dtype=np.float32)
        for r in range(nr):
            cc = np.fft.ifft(yb * spec[r], axis=1)[:, :hop]
            np.multiply(cc.real, cc.real, out=pw[r])
            pw[r] += cc.imag * cc.imag
        lo = a * hop
        size = yb.shape[0] * hop
        arg[lo:lo + size] = pw.argmax(axis=0).reshape(-1)
        best[lo:lo + size] = pw.max(axis=0).reshape(-1)
    best = best[:n_out]
    arg = arg[:n_out]
    cs = np.concatenate(([0.0], np.cumsum(y.real ** 2 + y.imag ** 2)))
    energy = cs[nf:nf + n_out] - cs[:n_out]
    score = best / (er * np.maximum(energy, 1e-30))
    return score, arg


def _peaks(score, limit):
    """Indices of the strongest well-separated peaks, at least _PSS_FLOOR x the median."""
    med = float(np.median(score))
    if not np.isfinite(med) or med <= 0:
        return []
    cand = np.flatnonzero(score >= _PSS_FLOOR * med)
    if cand.size == 0:
        return []
    if cand.size > 4000:
        cand = cand[np.argpartition(score[cand], -4000)[-4000:]]
    cand = cand[np.argsort(-score[cand])]
    kept = []
    sep = _SYM
    for i in cand:
        if all(abs(int(i) - j) >= sep for j in kept):
            kept.append(int(i))
            if len(kept) >= limit:
                break
    return kept


# ----- one burst ------------------------------------------------------------------

def _burst(y, rate, scs_khz, n, root, cfo_h):
    """Equalise and test the SSB whose PSS the scan put at sample ``n``; None near the ends."""
    s0 = n - _BACK
    length = 3 * _SYM + _NFFT
    if s0 < 0 or s0 + length > len(y):
        return None
    seg = y[s0:s0 + length] * _ramp(round(cfo_h, 3), round(rate, 3))[:length]
    frames = np.stack([seg[j * _SYM:j * _SYM + _NFFT] for j in range(4)])
    x = np.fft.fft(frames, axis=1)
    d = _pss_table()[root]
    z = x[0][_IDX_SYNC] * d
    a_grid, w = _slope_matrix()
    mag = np.abs(w @ z)
    k = int(np.argmax(mag))
    if 0 < k < len(mag) - 1:
        den = mag[k - 1] - 2 * mag[k] + mag[k + 1]
        shift = 0.5 * (mag[k - 1] - mag[k + 1]) / den if den < 0 else 0.0
    else:
        shift = 0.0
    slope = float(a_grid[k] + shift * (a_grid[1] - a_grid[0]))
    q_all = np.where(np.arange(_NFFT) < _NFFT // 2, np.arange(_NFFT), np.arange(_NFFT) - _NFFT)
    ramp = np.exp(-1j * slope * q_all)
    xc = x * ramp[None, :]
    ys = xc[2][_IDX_SYNC]
    tab = _sss_table()[root]
    corr = tab @ ys
    energy = float(np.sum(np.abs(ys) ** 2))
    if energy <= 0:
        return None
    s = np.abs(corr) ** 2 / (127.0 * energy)
    h = int(np.argmax(s))
    s_sorted = np.partition(s, -2)
    power = float(np.sum(np.abs(xc[0][_IDX_SYNC]) ** 2) + energy) / _NFFT ** 2
    start = s0 - slope * _NFFT / (2 * math.pi)       # useful part of the PSS symbol
    # Cyclic-prefix correlation: a prefix sample and the sample one useful symbol later are
    # equal, so any offset turns them by 2 pi f nfft / fs (unambiguous within +/- SCS/2). It
    # does not depend on where the SSB sits in the carrier, which the symbol-to-symbol phase
    # of the sequences does (a wrong turn, see the docstring).
    cp_corr = 0j
    for j in range(4):
        i0 = int(round(start + j * _SYM)) - _CP + 2
        if i0 >= 0 and i0 + _CP - 4 + _NFFT <= len(y):
            a = y[i0:i0 + _CP - 4]
            cp_corr += np.sum(np.conj(a) * y[i0 + _NFFT:i0 + _NFFT + _CP - 4])
    return dict(cp_corr=cp_corr, start=start, root=root, cfo_h=cfo_h, nid1=h, s=float(s[h]),
                s_next=float(s_sorted[-2]), e=float(_evidence(s[h], 127)), xc=xc, power=power)


def _dmrs_eval(xc, pci):
    """E and normalised score for each of the eight i_bar at this burst."""
    bins = _dmrs_bins(pci)
    ys = [xc[j + 1][bins[j]] for j in range(3)]
    ey = [float(np.sum(np.abs(v) ** 2)) for v in ys]
    e_tot = np.zeros(8)
    sc = np.zeros(8)
    for ib in range(8):
        refs = _dmrs(pci, ib)
        tot, wsum = 0.0, 0.0
        for j in range(3):
            if ey[j] <= 0:
                continue
            c = np.vdot(refs[j], ys[j])
            s = float(abs(c) ** 2 / (len(refs[j]) * ey[j]))   # |ref|^2 = len, since |r| = 1
            tot += float(_evidence(s, len(refs[j])))
            wsum += s * len(refs[j])
        e_tot[ib] = tot
        sc[ib] = wsum / 144.0
    return e_tot, sc


# ----- the search -------------------------------------------------------------------

def _period_ms(times, keys):
    """Burst period from the spacing of bursts that share an SSB index."""
    by = {}
    for t, k in zip(times, keys):
        by.setdefault(k, []).append(t)
    gaps = []
    for ts in by.values():
        ts = sorted(ts)
        gaps += [b - a for a, b in zip(ts, ts[1:]) if b - a >= 4e-3]
    if not gaps:
        return None
    g0 = min(gaps)
    near = [g for g in gaps if g <= 1.15 * g0]
    g = float(np.median(near)) * 1e3
    best = min(_PERIODS, key=lambda p: abs(p - g))
    return best if abs(best - g) <= 0.15 * best else None


def _search_candidate(cap, ssb_hz, center_hz, scs_khz, n_trials):
    """Cells found at one candidate frequency and spacing (unsorted, chance not yet floored by raster)."""
    got = cap.band(ssb_hz - center_hz, scs_khz)
    if got is None:
        return []
    y, rate, yd, rate_d = got
    if len(y) < 8 * _SYM:
        return []
    reps, cfos, er = _replicas(scs_khz)
    score, arg = _scan(yd, reps, er)
    dur_ms = 1e3 * len(y) / rate
    peaks = _peaks(score, 16 + int(dur_ms / 5))
    if not peaks:
        return []
    l_max = 4 if ssb_hz <= 3e9 else 8
    bursts = []
    for n in peaks:
        which = int(arg[n])
        b = _burst(y, rate, scs_khz, int(round(n * rate / rate_d)), which // len(cfos),
                   cfos[which % len(cfos)])
        if b is not None:
            b['pss_score'] = float(score[n])
            bursts.append(b)
    if not bursts:
        return []
    trials = 336.0 * len(peaks) * n_trials
    groups = {}
    for b in bursts:
        groups.setdefault((b['root'], b['nid1']), []).append(b)
    out = []
    for (nid2, nid1), grp in groups.items():
        chance = min(1.0, trials * _tail(max(b['e'] for b in grp), 1))
        if chance > _PASS:
            continue
        pci = 3 * nid1 + nid2
        votes = {}
        for b in grp:
            e_d, s_d = _dmrs_eval(b['xc'], pci)
            ib = int(np.argmax(e_d))
            b['i_bar'], b['e_dmrs'], b['s_dmrs'] = ib, float(e_d[ib]), float(s_d[ib])
            votes.setdefault(ib, []).append(b)
        win = max(votes, key=lambda k: (len(votes[k]), sum(b['e_dmrs'] for b in votes[k])))
        vb = votes[win]
        dm_chance = min(1.0, 8.0 * _tail(sum(b['e_dmrs'] for b in vb), len(vb) * 3))
        dm_ok = dm_chance <= _DMRS_PASS
        cfo = float(np.angle(sum(b['cp_corr'] for b in grp))) * rate / (2 * math.pi * _NFFT)
        times = [(b['start'] - _CP) / rate for b in grp]
        order = np.argsort(times)
        times = [times[i] for i in order]
        grp = [grp[i] for i in order]
        if dm_ok:
            keys = [(b['i_bar'] % 4) if l_max == 4 else b['i_bar'] for b in grp]
        else:
            keys = [0] * len(grp)
        period = _period_ms(times, keys) if len(grp) >= 2 else None
        index = half = None
        if dm_ok:
            index = win % 4 if l_max == 4 else win
            half = win // 4 if l_max == 4 else None
        power = float(np.mean([b['power'] for b in grp]))
        out.append(dict(
            ssb_hz=float(ssb_hz), gscn=hz_to_gscn(ssb_hz), scs_khz=int(scs_khz), pci=int(pci),
            nid1=int(nid1), nid2=int(nid2), ssb_index=index, half_frame=half, l_max=l_max,
            period_ms=period, bursts=len(grp), first_ssb_s=float(times[0]), cfo_hz=cfo,
            pss_score=float(np.mean([b['pss_score'] for b in grp])),
            sss_score=float(np.mean([b['s'] for b in grp])),
            sss_next=float(np.mean([b['s_next'] for b in grp])),
            chance=float(chance), dmrs_score=float(np.mean([b['s_dmrs'] for b in vb])),
            dmrs_chance=float(dm_chance), relative_power_db=0.0,
            detail=(f'PCI {pci} (N_ID1 {nid1}, N_ID2 {nid2}), {len(grp)} bursts, SSS score '
                    f'{np.mean([b["s"] for b in grp]):.3f}, chance {chance:.1e}, CFO {cfo:.0f} Hz'),
            _power=power))
    return out


def ssb_search(iq, fs, center_hz, ssb_hz=None, scs_khz=None, max_cells=4):
    """NR SSBs in a capture, strongest first (see the module docstring).

    ``ssb_hz`` given: only that frequency. None: every raster point inside the capture
    for 15 and 30 kHz (or only ``scs_khz``). Only cells whose SSS chance is at most 1e-3
    are listed, at most one per (gscn, pci). Under 2 ms, under 3.84 MS/s or noise: [].
    """
    try:
        fs = float(fs)
        center_hz = float(center_hz)
    except (TypeError, ValueError):
        return []
    x = np.asarray(iq)
    if x.ndim != 1 or x.size == 0 or not math.isfinite(fs) or fs < _MIN_FS:
        return []
    if x.size < _MIN_SECONDS * fs:
        return []
    x = np.nan_to_num(x.astype(np.complex128))
    spacings = (15, 30) if scs_khz is None else (int(scs_khz),)
    if any(s not in (15, 30) for s in spacings):
        return []
    cands = []
    if ssb_hz is not None:
        cands = [(float(ssb_hz), s) for s in spacings]
    else:
        for s in spacings:
            cands += [(hz, s) for _, hz in raster_in_band(center_hz, fs, s)]
    if not cands:
        return []
    cap = _Capture(x, fs)
    found = {}
    for hz, s in cands:
        for e in _search_candidate(cap, hz, center_hz, s, len(cands)):
            key = (e['gscn'] if e['gscn'] is not None else e['ssb_hz'], e['pci'])
            if key not in found or e['chance'] < found[key]['chance']:
                found[key] = e
    out = sorted(found.values(), key=lambda e: -e['_power'])
    if out:
        top = out[0]['_power']
        for e in out:
            e['relative_power_db'] = float(10.0 * math.log10(max(e['_power'], 1e-300) / top))
    for e in out:
        del e['_power']
    return out[:max(0, int(max_cells))]
