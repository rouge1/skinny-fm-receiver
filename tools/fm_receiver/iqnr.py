"""5G NR synchronisation-block (SSB) finder for a recording. Numpy only.

Given complex IQ, its sample rate and centre frequency, list every NR cell whose
SS/PBCH block is in the capture: where it sits on the sync raster (GSCN), its
subcarrier spacing (15 or 30 kHz), physical cell ID (PCI), SSB index and
half-frame (from the PBCH DM-RS), burst period, carrier offset, and a chance
estimate. The search decodes nothing; ``ssb_search(mib=True)`` and
``type0_pdcch`` (SIB1's DCIs) hand the grids they cut to ``iqnr_phy``. NR's sync signals are
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

SIB1's PDCCH (``type0_pdcch``, roadmap Round 28.2)
---------------------------------------------------
The MIB of a cell's first decoded burst times its frames: SSB index i starts
symbol {2, 8, 16, 22}[i] of its half-frame (case A), so frame SFN starts that
far, and 5 ms per half-frame, before ``ssb_s``. CORESET 0's lowest subcarrier
is k0 = -k_SSB - 12 x offset subcarriers from the SSB's subcarrier 0. Each
occasion (slots n0 and n0 + 1 of frames of the right parity) is cut from the
capture alone: about 0.2 ms around its CORESET symbols, moved so subcarrier
6 N_RB is at 0 Hz (the cell's ``cfo_hz`` included) and resampled by cutting
its spectrum to nfft x 15 kHz, nfft the multiple of 128 that holds the
CORESET (640 for 48 RBs). Each symbol's window opens half its cyclic prefix
early, and the ramp that puts on the subcarriers is taken off. So memory
does not grow with the capture, which may be a memmap of a whole recording.

The recorder's clock drifts against the cell's. Over a 5.7 s recording the
DM-RS delay fit walked 2 us (0.36 ppm), the same ratio as the carrier offset
to the carrier frequency: the BB60D's sample clock and tuner share a reference.
So time from the anchoring burst is scaled by 1 - cfo / f; the fitted delays
then stayed within 0.2 us across that file (0.8 us on another). A recorder without a shared
reference leaves a drift the per-candidate delay fit (+/- 3 us) absorbs over
a few seconds.

SIB1 and the other SI (``type0_pdcch(pdsch=True)``, ``other_si``, Round 28.3)
------------------------------------------------------------------------------
Each SIB1 DCI's PDSCH is in the same slot (K0 0), cut the same way over its
symbols S..S+L-1 and decoded on its own (``iqnr_phy.pdsch_llrs`` with the DCI's
delay, then ``dlsch_decode``). ``other_si`` walks SIB1's SI windows (38.331
5.2.2.3.2: message n's window starts x = (n - 1) w slots into a frame whose SFN
mod T is floor(x / 10)), searches each slot the other-SI search space monitors
with that space's candidates (AL2 among them on air), and decodes each DCI
with the SI indicator 1 with SIB1's time list and DM-RS from common RB 0
(CORESET 0 starts offsetToPointA - offset RBs above it).

Wrong turn: the per-symbol phase. 38.211 5.4 starts every symbol's phase
afresh at its useful start, so after the FFT symbol l carries exp(-j 2 pi f
tau_l), f the radio frequency at 0 Hz of the cut (the cell's carrier less a
whole number of subcarriers) and tau_l the useful start within the subframe.
The PBCH and PDCCH never showed it, since every symbol of theirs has DM-RS and
its own gain; PDSCH data symbols are interpolated between DM-RS symbols 2, 7
and 11, and without the term (or with its sign flipped) about 40 % of the
bits agreed with the block on air, none decoded. ``_Timing.grid`` multiplies
each symbol by exp(+j 2 pi f tau_l).
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


def _mibs(grp, times, pci, l_max):
    """``iqnr_phy.decode_pbch`` on each burst of one cell, each with its own DM-RS i_bar:
    (the first MIB decoded, with ``ssb_s`` its burst's time; [decoded, tried])."""
    if l_max != 4:
        return None, [0, 0]
    from . import iqnr_phy
    first, done = None, 0
    for b, t in zip(grp, times):
        if b.get('i_bar') is None:
            continue
        mib = iqnr_phy.decode_pbch(b['xc'], pci, b['i_bar'])
        if mib is not None:
            done += 1
            if first is None:
                first = dict(mib, ssb_s=float(t))
    return first, [done, len(grp)]


def _search_candidate(cap, ssb_hz, center_hz, scs_khz, n_trials, mib=False):
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
        extra = {}
        if mib:
            extra['mib'], extra['mib_bursts'] = _mibs(grp, times, pci, l_max)
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
            _power=power, **extra))
    return out


def ssb_search(iq, fs, center_hz, ssb_hz=None, scs_khz=None, max_cells=4, mib=False):
    """NR SSBs in a capture, strongest first (see the module docstring).

    ``mib`` True also decodes each cell's PBCH, burst by burst (``iqnr_phy``, L_max 4 only):
    each entry gains ``mib`` (the first MIB whose CRC checked, with ``ssb_s``, its burst's
    time, or None) and ``mib_bursts`` ([bursts decoded, bursts of the cell]).

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
        for e in _search_candidate(cap, hz, center_hz, s, len(cands), mib=mib):
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


# ----- SIB1's PDCCH (roadmap Round 28.2) --------------------------------------------

#: Start of symbol l in a 15 kHz slot (cyclic prefix included), and its prefix, in 1/30.72 MHz
#: samples (38.211 5.3.1: the prefix of symbols 0 and 7 is 16 longer).
_TS_RATE = 30.72e6


def _symbol_start(l):
    return l * 2192 + 16 * (l >= 1) + 16 * (l >= 8)


def _symbol_cp(l):
    return 160 if l in (0, 7) else 144


#: First symbol of SSB index i in its half-frame, case A (38.213 4.1: {2, 8} + 14 n).
_CASE_A = (2, 8, 16, 22)


def _cut(x, fs, i0, n_in, shift_hz, out_rate):
    """``x[i0:i0 + n_in]`` moved by -shift_hz (phase from sample 0) and resampled to ``out_rate``
    by cutting its spectrum: sample j is at time i0 / fs + j / out_rate."""
    seg = np.asarray(x[i0:i0 + n_in], dtype=np.complex128)
    seg = seg * np.exp(-2j * np.pi * ((shift_hz / fs * np.arange(i0, i0 + n_in)) % 1.0))
    m = int(round(n_in * out_rate / fs))
    spec = np.fft.fft(seg)
    q = np.arange(-(m // 2), m - m // 2)
    return np.fft.ifft(np.roll(spec.take(q, mode='wrap'), -(m // 2))) * (m / float(n_in))


#: Sign of the per-symbol phase 38.211 5.4 leaves after the FFT: symbol l is turned by
#: exp(-j 2 pi f tau_l), f the cell's own radio frequency at 0 Hz of the cut (the recorder's
#: offset left out: it is the receiver's, not the cell's) and tau_l the symbol's useful start
#: within the subframe; the grid is multiplied by exp(_PHASE_SIGN j 2 pi f tau_l).
_PHASE_SIGN = 1.0


class _Timing:
    """A cell's frames and CORESET 0 band in a capture, from its MIB (15 kHz only)."""

    def __init__(self, fs, center_hz, cell, cs):
        mib = cell['mib']
        self.fs = float(fs)
        self.n_rb = cs['n_rb']
        scs = 15e3
        self.f0 = float(cell['ssb_hz']) + (cs['k0'] - _REF_K) * scs
        self.nfft = 128 * -(-12 * self.n_rb // 128)
        self.rate = self.nfft * scs
        f_mid = self.f0 + 6 * self.n_rb * scs            # subcarrier 6 N_RB sits on bin 0
        self.fits = abs(f_mid - float(center_hz)) + 6 * self.n_rb * scs <= 0.5 * self.fs
        self.shift = f_mid - float(center_hz) + float(cell.get('cfo_hz') or 0.0)
        self.f_rf = f_mid                                # the cell's carrier, not as received
        s = _CASE_A[int(mib['ssb_index'])]
        self.ssb_s = float(mib['ssb_s'])
        self.t_frame = (self.ssb_s - (s // 14) * 1e-3 - _symbol_start(s % 14) / _TS_RATE
                        - 5e-3 * int(mib['half_frame']))
        self.sfn0 = int(mib['sfn'])
        # The recorder's sample clock is taken to share its reference with its tuner: a carrier
        # cfo_hz high means the clock runs slow by cfo / f, and the cell's frames come early.
        self.scale = 1.0 - float(cell.get('cfo_hz') or 0.0) / float(cell['ssb_hz'])
        self.ratio = Fraction(int(round(self.rate)), int(round(self.fs)))
        self.k_bins = np.arange(12 * self.n_rb) - 6 * self.n_rb

    def slot_start(self, frame, slot):
        """Capture time of slot ``slot`` (0..9, or beyond into the next frames) of frame
        ``frame`` counted from the anchoring burst's frame."""
        return self.ssb_s + (self.t_frame - self.ssb_s + frame * 10e-3 + slot * 1e-3) * self.scale

    def frames(self, n_total):
        first = int(math.floor(-self.t_frame / 10e-3))
        last = int(math.ceil((n_total / self.fs - self.t_frame) / 10e-3))
        return range(first, last + 1)

    def grid(self, x, t_slot, symbols):
        """The slot's 14 x 12 N_RB grid from CORESET 0's subcarrier 0, with ``symbols`` filled
        (None when the capture does not hold them)."""
        symbols = sorted(symbols)
        margin = 64 / self.rate
        t_a = t_slot + _symbol_start(symbols[0]) / _TS_RATE - margin
        t_b = t_slot + _symbol_start(symbols[-1] + 1) / _TS_RATE + margin
        i0 = int(math.floor(t_a * self.fs))
        n_in = int(math.ceil((t_b - t_a) * self.fs)) + 1
        n_in += (-n_in) % self.ratio.denominator
        if i0 < 0 or i0 + n_in > len(x):
            return None
        y = _cut(x, self.fs, i0, n_in, self.shift, self.rate)
        nfft = self.nfft
        out = np.zeros((14, 12 * self.n_rb), dtype=np.complex128)
        for l in symbols:
            cp = _symbol_cp(l) * nfft / 2048.0
            start = (t_slot + _symbol_start(l) / _TS_RATE - i0 / self.fs) * self.rate + cp / 2
            p = int(math.floor(start))
            if p < 0 or p + nfft > len(y):
                return None
            spec = np.fft.fft(y[p:p + nfft])
            d = (start + cp / 2) - p                     # the window opens d samples early
            tau = (_symbol_start(l) + _symbol_cp(l)) / _TS_RATE
            out[l] = (spec[self.k_bins % nfft] * np.exp(2j * np.pi * self.k_bins * d / nfft)
                      * np.exp(_PHASE_SIGN * 2j * np.pi * ((self.f_rf * tau) % 1.0)))
        return out


def type0_pdcch(iq, fs, center_hz, cell, rnti=None, pdsch=False, combine=True, known=None):
    """SIB1's DCIs (DCI format 1_0, CRC scrambled by SI-RNTI) of one cell, decoded with
    ``iqnr_phy`` in every Type0-PDCCH occasion of the capture, and with ``pdsch`` the SIB1
    each one schedules.

    ``cell`` is an ``ssb_search(..., mib=True)`` entry with a MIB (its ``ssb_s``, SFN,
    half-frame and SSB index time the frames; ``cfo_hz`` is removed). 15 kHz SSB and common
    spacing only. Returns None without a usable MIB, else a dict: ``coreset0`` (``n_rb``,
    ``n_symb``, ``offset_rb``, ``f0_hz``: its lowest subcarrier), ``occasion`` (from table
    13-11), ``slots`` (slots tried), ``cancelled`` (slots a known transmission was taken out
    of), ``summed`` (see below, or None), and ``dcis``, one per DCI found: ``sfn``, ``slot``,
    ``t_s`` (the slot's start), ``level``, ``cce``, the payload's fields, ``snr_db``. With
    ``pdsch`` each DCI also has ``pdsch``: ``tbs``, ``snr_db``, ``hex`` (the transport block,
    or None when its CRC failed), ``copies`` (1 decoded alone, more when ``combine`` summed it
    with earlier copies of its 160 ms period or since the last pass: ``iqnr_phy.SoftCombiner``;
    0 none), or None when the allocation is not one this reads.

    ``known`` holds what stronger cells on the same carrier were decoded sending
    (``known_transmissions``, by (SFN, slot)): each is rebuilt and cancelled from its slot
    before this cell is read (``iqnr_phy.cancel``). With ``combine``, when no SIB1 DCI decodes
    alone in any occasion, every candidate's soft bits are summed over the occasions of its
    slot. A SIB1 DCI decoded alone twice or more, or failing any one from a sum, is then looked for in every
    occasion where none decoded (its codeword's score over ``_DETECT``, or the place in the
    160 ms period whose occasions score over ``_PLACE`` together) and given to those, each
    marked ``summed`` (how many occasions held it) and ``score``; ``summed`` in the result says
    how the last was found. That finds only copies of the same DCI: a cell cycling redundancy
    versions sends another in each occasion."""
    from . import iqnr_phy as P
    mib = (cell or {}).get('mib')
    if not mib or int(cell.get('scs_khz') or 0) != 15:
        return None
    cs = P.coreset0(mib)
    if cs is None:
        return None
    rnti = P.SI_RNTI if rnti is None else int(rnti)
    x = iq
    pci = int(cell['pci'])
    n_rb, n_symb = cs['n_rb'], cs['n_symb']
    occ = P.type0_occasion(mib['search_space0_index'], int(mib['ssb_index']), n_symb)
    tm = _Timing(fs, center_hz, cell, cs)
    if not tm.fits:
        return None
    l0 = occ['first_symbol']
    comb = P.SoftCombiner() if combine else None
    dcis, slots, cancelled, empty = [], 0, 0, []
    for fr in tm.frames(len(x)):
        sfn = (tm.sfn0 + fr) % 1024
        if sfn % 2 != occ['parity']:
            continue
        for slot_abs in (occ['n0'], occ['n0'] + 1):
            slot = slot_abs % 10
            sfn_slot = (sfn + slot_abs // 10) % 1024
            t_slot, grid, cut = _slot_grid(P, tm, x, fr, slot_abs, (sfn_slot, slot), known, cs, l0, mib, pci)
            if grid is None:
                continue
            slots += 1
            cancelled += cut
            found = P.decode_pdcch(grid[l0:l0 + n_symb], pci, slot, l0, n_rb, n_symb, rnti=rnti)
            if not any(d.get('sii') == 0 for d in found):
                empty.append((fr, slot_abs, sfn_slot, slot, t_slot, grid[l0:l0 + n_symb].copy()))
            for dci in found:
                dci.pop('metric', None)
                entry = dict(sfn=int(sfn_slot), slot=int(slot), t_s=float(t_slot), **dci)
                if pdsch:
                    entry['pdsch'] = _sib1_block(P, tm, x, t_slot, pci, slot, dci, mib, n_rb, rnti,
                                                 comb, sfn_slot // 16, grid if cut else None)
                dcis.append(entry)
    summed = None
    if combine and empty:
        summed = _more_sib1(P, tm, x, cell, cs, occ, empty, known, rnti, pdsch, comb, dcis)
    return {'coreset0': dict(n_rb=n_rb, n_symb=n_symb, offset_rb=cs['offset_rb'], f0_hz=tm.f0),
            'occasion': occ, 'slots': slots, 'cancelled': cancelled, 'summed': summed, 'dcis': dcis}


#: A codeword score (``iqnr_phy.codeword_score``, about 0 +/- 1 where the DCI is not sent) over
#: which an occasion is taken to hold a DCI found from summed occasions; and the mean score, in
#: units of its spread where nothing is sent (1 / sqrt(n) for n occasions), over which a place in
#: SIB1's 160 ms period is taken to hold it in every period (a schedule repeats each period).
_DETECT = 4.0
_PLACE = 5.0


def _slot_grid(P, tm, x, fr, slot_abs, key, known, cs, l0, mib, pci):
    """``(t_slot, grid, cancelled)``: the slot's CORESET symbols, or with known transmissions in
    it the whole slot with them cancelled."""
    t_slot = tm.slot_start(fr, slot_abs)
    ks = _knowns(P, known, key, tm, cs, mib, int(pci))
    if not ks:
        return t_slot, tm.grid(x, t_slot, range(l0, l0 + cs['n_symb'])), False
    grid = tm.grid(x, t_slot, range(14))
    return t_slot, (None if grid is None else P.cancel(grid, ks)), grid is not None


def _knowns(P, known, key, tm, cs, mib, pci):
    """The known transmissions of ``key`` (SFN, slot) rebuilt in this cell's grid: only those
    whose CORESET 0 lies where this cell's does, so the rebuild falls on the right REs."""
    out = []
    for e in (known or {}).get(key, ()):
        if int(e['pci']) == pci:
            continue                                   # a cell's own blocks are not taken from it
        if abs(float(e['f0_hz']) - tm.f0) > 1.0 or int(e['n_rb']) != cs['n_rb'] or int(e['n_symb']) != cs['n_symb']:
            continue
        r = P.known_grid(int(e['pci']), key[1], cs['n_rb'], cs['n_symb'], int(e['first_symbol']), e['dci'],
                         bytes.fromhex(e['hex']), int(e['dmrs_pos']), k_ref=int(e.get('k_ref') or 0),
                         tdra=e.get('tdra'))
        if r is not None:
            out.append(r)
    return out


def known_transmissions(result, cell, known=None):
    """``known`` (a dict, made when None) with every block ``type0_pdcch`` or ``other_si``
    decoded in ``result`` added, by (SFN, slot): what ``known=`` takes for the weaker cells."""
    known = {} if known is None else known
    if not result:
        return known
    k_ref, tdra = result.get('k_ref') or 0, result.get('tdra')
    cs = result.get('coreset0') or {}
    if 'f0_hz' not in cs:
        from . import iqnr_phy as P
        c0 = P.coreset0(cell['mib'])
        cs = dict(c0, f0_hz=float(cell['ssb_hz']) + (c0['k0'] - _REF_K) * 15e3)
    rows = result.get('dcis') if 'dcis' in result else result.get('blocks')
    for d in rows or ():
        block = d.get('pdsch') or {}
        if not block.get('hex'):
            continue
        l0 = d.get('first_symbol', (result.get('occasion') or {}).get('first_symbol', 0))
        dci = {k: d[k] for k in ('bits', 'level', 'cce', 'fda', 'tdra', 'vrb_to_prb', 'mcs', 'rv', 'sii',
                                 'rb_start', 'n_rbs') if k in d}
        known.setdefault((int(d['sfn']), int(d['slot'])), []).append(dict(
            pci=int(cell['pci']), dci=dci, hex=block['hex'], f0_hz=float(cs['f0_hz']), n_rb=int(cs['n_rb']),
            n_symb=int(cs['n_symb']), first_symbol=int(l0), dmrs_pos=int(cell['mib']['dmrs_typeA_position']),
            k_ref=int(k_ref) if d.get('sii') == 1 else 0, tdra=tdra if d.get('sii') == 1 else None))
    return known


def _more_sib1(P, tm, x, cell, cs, occ, empty, known, rnti, pdsch, comb, dcis):
    """SIB1's DCI in the occasions where none decoded (``type0_pdcch``'s ``combine``): each SIB1
    DCI decoded alone, or failing any, one decoded from every candidate's soft bits summed over
    the occasions of its slot, is looked for in each of them, and those that hold it are decoded
    with it. Adds to ``dcis``; returns how the last was found (``slot``, ``level``, ``cce``,
    ``from``: 'alone' or 'summed', ``occasions`` looked in, ``held``, ``scores``), or None."""
    mib = cell['mib']
    pci, n_rb, n_symb, l0 = int(cell['pci']), cs['n_rb'], cs['n_symb'], occ['first_symbol']
    a_len = P.dci_1_0_si_bits(n_rb)
    k = a_len + P.L_CRC

    def check(c):
        return np.array_equal(P.dci_crc(c[:a_len], rnti), c[a_len:])
    times = {}
    for d in dcis:
        if d.get('sii') == 0:
            key = (d['slot'], d['level'], d['cce'], d['bits'])
            times[key] = times.get(key, 0) + 1
    # Only a DCI decoded alone twice or more: a cell that changes its DCI every time (redundancy
    # versions, moving RBs) would otherwise have each looked for in every occasion, inviting a
    # chance match.
    looks = [(sl, lv, cce, np.array([int(b) for b in bits], dtype=np.uint8), 'alone')
             for (sl, lv, cce, bits), n in times.items() if n >= 2]
    llrs = {}

    def soft(i, level, cce0):
        if (i, level, cce0) not in llrs:
            _fr, _sa, _sfn, slot, _t, rows = empty[i]
            llrs[(i, level, cce0)] = P.pdcch_llrs(rows, pci, slot, l0, n_rb, n_symb, level, cce0)
        return llrs[(i, level, cce0)]
    if not looks:
        sums = {}
        for i, e in enumerate(empty):
            for level, cce0 in P.type0_candidates(n_rb * n_symb // 6):
                key = (e[3], level, cce0)
                sums[key] = sums.get(key, 0.0) + soft(i, level, cce0)[0]
        for (slot, level, cce0), llr in sorted(sums.items(), key=lambda kv: -kv[0][1]):
            c, _m = P.polar_decode(llr, k, P.polar_n(k, len(llr), P.N_MAX_DCI), P.LIST, check)
            if c is not None and P.parse_dci_1_0_si(c[:a_len], n_rb)['sii'] == 0:
                looks.append((slot, level, cce0, c[:a_len], 'summed'))
                break
    found = None
    for slot, level, cce0, a, how in looks:
        signs = P.pdcch_codeword(a, rnti, level)
        scored = [(i, P.codeword_score(soft(i, level, cce0)[0], signs)) for i, e in enumerate(empty) if e[3] == slot]
        place = {}
        for i, score in scored:                        # its place in SIB1's 160 ms period
            place.setdefault(empty[i][2] % 16, []).append(score)
        sent = {p for p, v in place.items() if np.mean(v) > _PLACE / np.sqrt(len(v))}
        held = [(i, score) for i, score in scored if empty[i][2] % 16 in sent or score > _DETECT]
        fields = P.parse_dci_1_0_si(a, n_rb)
        bits = ''.join(str(int(b)) for b in a)
        for i, score in held:
            fr, slot_abs, sfn_slot, _sl, t_slot, _rows = empty[i]
            _llr, snr, slope = soft(i, level, cce0)
            dci = dict(level=level, cce=cce0, bits=bits, **fields)
            dci.update(snr_db=float(snr), slope=slope, summed=len(held), score=round(score, 1))
            entry = dict(sfn=int(sfn_slot), slot=int(slot), t_s=float(t_slot), **dci)
            if pdsch:
                _t, grid, cut = _slot_grid(P, tm, x, fr, slot_abs, (sfn_slot, slot), known, cs, l0, mib, pci)
                entry['pdsch'] = _sib1_block(P, tm, x, t_slot, pci, slot, dci, mib, n_rb, rnti, comb,
                                             sfn_slot // 16, grid if cut else None)
            dcis.append(entry)
        if held:
            found = {'slot': int(slot), 'level': int(level), 'cce': int(cce0), 'from': how, 'occasions': len(scored),
                     'held': len(held), 'scores': sorted(round(sc, 1) for _i, sc in scored)}
    dcis.sort(key=lambda d: d['t_s'])
    return found


def _sib1_block(P, tm, x, t_slot, pci, slot, dci, mib, n_rb, rnti, comb=None, group=None, grid=None):
    """The PDSCH a SIB1 DCI schedules (K0 0: the same slot), decoded on its own, or through
    ``comb`` with the copies of ``group``; ``grid`` the whole slot when one was built (known
    transmissions cancelled)."""
    if dci.get('sii') != 0:
        return None
    alloc = P.pdsch_alloc(dci, mib['dmrs_typeA_position'], n_rb)
    if alloc is None or max(alloc['prbs']) >= n_rb:
        return None
    if grid is None:
        grid = tm.grid(x, t_slot, range(alloc['S'], alloc['S'] + alloc['L']))
    if grid is None:
        return None
    llr, snr, _slope = P.pdsch_llrs(grid, pci, slot, alloc, rnti, slope=dci.get('slope'))
    return _block(P, llr, snr, alloc, dci['rv'], comb, 'sib1', group)


def _block(P, llr, snr, alloc, rv, comb, key, group):
    """One PDSCH's transport block from its soft bits, alone or through the combiner."""
    setup = P.dlsch_setup(alloc['tbs'], alloc['rate'])
    soft = P.dlsch_soft(llr, setup, rv, alloc['qm'])
    if comb is None:
        data = P.dlsch_decode(soft, setup)
        copies = 1 if data is not None else 0
    else:
        data, copies = comb.add(key, group, soft, setup)
    return {'tbs': alloc['tbs'], 'snr_db': float(snr), 'hex': data.hex() if data is not None else None,
            'copies': copies}


def _ss_slots(search, frame, slot):
    """Whether a search space ``search`` monitors slot ``slot`` of frame ``frame`` (15 kHz)."""
    period, offset = search.get('period_slots', 1), search.get('offset_slots', 0)
    return (frame * 10 + slot - offset) % period == 0


def other_si(iq, fs, center_hz, cell, config, rnti=None, combine=True, known=None):
    """The other SI messages of one cell (DCI format 1_0 with the SI indicator 1, and the PDSCH
    each schedules), decoded with ``iqnr_phy`` in the SI windows SIB1 names.

    ``config`` comes from SIB1 (``iqnr_decode.si_config``): ``offset_to_point_a``, ``tdra``
    ((K0, mapping, S, L) rows), ``window_slots``, ``periods_rf`` (each SI message's
    periodicity in frames, in SIB1's order) and ``search`` (``counts``, ``first_symbols``,
    ``period_slots``, ``offset_slots``; for search space 0, the Type0 candidates and
    symbols). Returns None when the cell or config cannot be read, else a dict: ``slots`` tried
    and ``blocks``, one per DCI: ``sfn``, ``slot``, ``si`` (1-based message, from the window),
    ``level``, ``cce``, the DCI fields, and ``pdsch`` (``tbs``, ``snr_db``, ``hex`` or None,
    ``copies`` as ``type0_pdcch``'s: with ``combine`` a message's copies are summed within its
    window, then across windows until one passes), and ``first_symbol`` (its PDCCH's).
    ``known`` as ``type0_pdcch``'s: stronger cells' transmissions cancelled from each slot first;
    ``cancelled`` in the result counts the slots; ``k_ref`` and ``tdra`` are the DM-RS reference
    and time list used."""
    from . import iqnr_phy as P
    mib = (cell or {}).get('mib')
    if not mib or int(cell.get('scs_khz') or 0) != 15 or not config:
        return None
    cs = P.coreset0(mib)
    if cs is None or config.get('offset_to_point_a') is None or not config.get('periods_rf'):
        return None
    rnti = P.SI_RNTI if rnti is None else int(rnti)
    tm = _Timing(fs, center_hz, cell, cs)
    if not tm.fits:
        return None
    x = iq
    pci, n_rb, n_symb = int(cell['pci']), cs['n_rb'], cs['n_symb']
    k_ref = 12 * (int(config['offset_to_point_a']) - cs['offset_rb'])
    search = config['search']
    w = int(config['window_slots'])
    comb = P.SoftCombiner() if combine else None
    blocks, slots, cancelled = [], 0, 0
    for fr in tm.frames(len(x)):
        sfn = (tm.sfn0 + fr) % 1024
        for slot in range(10):
            here = sfn * 10 + slot
            which = window = None
            for n, period in enumerate(config['periods_rf'], start=1):
                start = (n - 1) * w                     # 38.331 5.2.2.3.2: x = (n - 1) w
                rel = (here - (start // 10) * 10 - start % 10) % (int(period) * 10)
                if rel < w:
                    which, window = n, (here - rel) % 10240
                    break
            if which is None or not _ss_slots(search, sfn, slot):
                continue
            for l0 in search['first_symbols']:
                t_slot, grid, cut = _slot_grid(P, tm, x, fr, slot, (sfn, slot), known, cs, l0, mib, pci)
                if grid is None:
                    continue
                slots += 1
                cancelled += cut
                for dci in P.decode_pdcch(grid[l0:l0 + n_symb], pci, slot, l0, n_rb, n_symb, rnti=rnti,
                                          counts=search['counts']):
                    dci.pop('metric', None)
                    if dci.get('sii') != 1:
                        continue
                    entry = dict(sfn=int(sfn), slot=int(slot), si=which, t_s=float(t_slot), first_symbol=int(l0), **dci)
                    entry['pdsch'] = None
                    alloc = P.pdsch_alloc(dci, mib['dmrs_typeA_position'], n_rb, tdra=config['tdra'] or None)
                    if alloc is not None and max(alloc['prbs']) < n_rb:
                        g = grid if cut else tm.grid(x, t_slot, range(alloc['S'], alloc['S'] + alloc['L']))
                        if g is not None:
                            llr, snr, _slope = P.pdsch_llrs(g, pci, slot, alloc, rnti, k_ref=k_ref,
                                                            slope=dci.get('slope'))
                            entry['pdsch'] = _block(P, llr, snr, alloc, dci['rv'], comb, ('si', which), window)
                    blocks.append(entry)
    return {'slots': slots, 'cancelled': cancelled, 'k_ref': k_ref, 'tdra': config['tdra'] or None, 'blocks': blocks}
