"""LTE FDD downlink test signal (phase l1), and a TDD one (``duplex='tdd'``).

One antenna port, normal cyclic prefix, from the bandwidth-class table in
``tools/swarm-records/specs/l1.md``. PSS, SSS and the port-0 cell-specific
reference signal follow 3GPP TS 36.211 clauses 6.11.1, 6.11.2 and 6.10.1.
Every other used resource element is random QPSK (the PBCH included). The
frame begins after a seeded offset, so a detector cannot assume sample 0 is
the boundary. Numpy builds the waveform; scipy only resamples it.

Run:  /home/user/miniconda3/envs/gnu/bin/python tools/tests/lte_synth.py
"""

import sys

import numpy as np
from scipy.signal import resample_poly

# bw MHz -> (FFT size, native sample rate, used subcarriers excluding DC)
_CLASS = {
    1.4: (128, 1.92e6, 72),
    3: (256, 3.84e6, 180),
    5: (512, 7.68e6, 300),
    10: (1024, 15.36e6, 600),
    15: (1536, 23.04e6, 900),
    20: (2048, 30.72e6, 1200),
}
_PSS_ROOT = (25, 29, 34)          # N_ID(2) = 0, 1, 2  (36.211 Table 6.11.1.1-1)
_N_RB_MAX = 110                   # the CRS sequence is centred in the maximum bandwidth
_GOLD_NC = 1600
_SYMS_PER_SLOT = 7
_SLOTS_PER_FRAME = 20


def _class(bw_mhz):
    key = round(float(bw_mhz), 1)
    if key not in _CLASS:
        raise ValueError(f'bw_mhz {bw_mhz} is not an LTE bandwidth class')
    return _CLASS[key]


def _cp_lengths(n_fft):
    """Normal-CP lengths: first symbol of a slot, then the other six (36.211 6.12)."""
    return 160 * n_fft // 2048, 144 * n_fft // 2048


def _frame_samples(n_fft):
    cp0, cp = _cp_lengths(n_fft)
    return _SLOTS_PER_FRAME * (cp0 + n_fft + 6 * (cp + n_fft))


def _used_bins(n_fft, n_sc):
    """FFT bins of the used subcarriers, lowest frequency first. DC is left empty."""
    half = n_sc // 2
    k = np.concatenate((np.arange(-half, 0), np.arange(1, half + 1)))
    return k % n_fft


def _center62(n_fft):
    """The central 62 used subcarriers: 31 below DC and 31 above (36.211 6.11)."""
    k = np.concatenate((np.arange(-31, 0), np.arange(1, 32)))
    return k % n_fft


def _pss(nid2):
    """Zadoff-Chu PSS, length 62, DC element of the length-63 sequence removed (6.11.1)."""
    u = _PSS_ROOT[nid2]
    n = np.arange(62)
    m = np.where(n < 31, n * (n + 1), (n + 1) * (n + 2))
    return np.exp(-1j * np.pi * u * m / 63.0)


def _m_sequence(taps, length=31):
    """Length-31 m-sequence 1-2x(i). ``taps`` are the feedback indices of x (6.11.2.1)."""
    x = np.zeros(length, dtype=np.int8)
    x[4] = 1
    for i in range(length - 5):
        x[i + 5] = np.bitwise_xor.reduce(x[i + np.array(taps)])
    return 1 - 2 * x


def _sss(nid1, nid2):
    """SSS for subframes 0 and 5, each length 62 (36.211 6.11.2)."""
    q_prime = nid1 // 30
    q = (nid1 + q_prime * (q_prime + 1) // 2) // 30
    m_prime = nid1 + q * (q + 1) // 2
    m0 = m_prime % 31
    m1 = (m0 + m_prime // 31 + 1) % 31

    s = _m_sequence((2, 0))
    c = _m_sequence((3, 0))
    z = _m_sequence((4, 2, 1, 0))
    s0 = s[(np.arange(31) + m0) % 31]
    s1 = s[(np.arange(31) + m1) % 31]
    c0 = c[(np.arange(31) + nid2) % 31]
    c1 = c[(np.arange(31) + nid2 + 3) % 31]
    z0 = z[(np.arange(31) + (m0 % 8)) % 31]
    z1 = z[(np.arange(31) + (m1 % 8)) % 31]

    def interleave(even, odd):
        d = np.empty(62, dtype=np.float64)
        d[0::2] = even
        d[1::2] = odd
        return d

    sub0 = interleave(s0 * c0, s1 * c1 * z0)
    sub5 = interleave(s1 * c0, s0 * c1 * z1)
    return sub0, sub5


_X1 = None


def _gold(c_init, nbits):
    """Gold sequence c(n) of 36.211 7.2, the first ``nbits`` after the Nc = 1600 skip."""
    global _X1
    total = _GOLD_NC + nbits
    if _X1 is None or len(_X1) < total + 31:
        x1 = np.zeros(total + 31, dtype=np.uint8)
        x1[0] = 1
        for n in range(total):
            x1[n + 31] = x1[n + 3] ^ x1[n]
        _X1 = x1
    x2 = np.zeros(total + 31, dtype=np.uint8)
    init = int(c_init)
    x2[:31] = (init >> np.arange(31)) & 1
    for n in range(total):
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return _X1[_GOLD_NC:_GOLD_NC + nbits] ^ x2[_GOLD_NC:_GOLD_NC + nbits]


def _crs(pci, slot, symbol, n_rb):
    """Port-0 CRS for one symbol: resource-element index k and the QPSK value (6.10.1)."""
    v = 0 if symbol == 0 else 3
    shift = (v + pci % 6) % 6
    m = np.arange(2 * n_rb)
    k = 6 * m + shift
    # normal CP: N_CP = 1. n_s is the slot number inside the frame.
    c_init = ((1 << 10) * (7 * (slot + 1) + symbol + 1) * (2 * pci + 1)
              + 2 * pci + 1)
    c = _gold(c_init, 2 * (2 * _N_RB_MAX))
    mp = m + (_N_RB_MAX - n_rb)
    r = ((1 - 2 * c[2 * mp].astype(np.float64))
         + 1j * (1 - 2 * c[2 * mp + 1].astype(np.float64))) / np.sqrt(2.0)
    return k, r


def _assemble(grid, n_fft, n_sc):
    """IFFT, normal CP, scale so a fully loaded grid has unit mean power."""
    cp0, cp = _cp_lengths(n_fft)
    td = np.fft.ifft(grid, axis=1) * (n_fft / np.sqrt(n_sc))
    slots = td.reshape(-1, _SYMS_PER_SLOT, n_fft)
    sym0 = np.concatenate((slots[:, 0, -cp0:], slots[:, 0, :]), axis=1)
    rest = slots[:, 1:, :]
    shorts = np.concatenate((rest[:, :, -cp:], rest), axis=2)
    n_slots = slots.shape[0]
    slot_len = (cp0 + n_fft) + 6 * (cp + n_fft)
    out = np.empty((n_slots, slot_len), dtype=np.complex128)
    out[:, :cp0 + n_fft] = sym0
    step = cp + n_fft
    for i in range(6):
        a = cp0 + n_fft + i * step
        out[:, a:a + step] = shorts[:, i, :]
    return out.reshape(-1)


def _resample(iq, native, fs):
    """Rational resample so the output rate is exactly ``fs``."""
    up = int(round(fs))
    down = int(round(native))
    g = np.gcd(up, down)
    up //= g
    down //= g
    if up == 1 and down == 1:
        return iq
    out = resample_poly(iq, up, down)
    want = int(round(len(iq) * up / down))
    if len(out) > want:
        out = out[:want]
    elif len(out) < want:
        out = np.pad(out, (0, want - len(out)))
    return out


# TDD (36.211 4.2): uplink-downlink configuration 1, D S U U D D S U U D, and
# special subframe configuration 7 (normal CP): DwPTS 10 symbols, then the guard
# period and UpPTS, 4 symbols, silent from the downlink's side.
_TDD_PATTERN = 'DSUUDDSUUD'
_TDD_DWPTS = 10


def _tdd_mask(n_frames):
    """True for every downlink OFDM symbol of ``n_frames`` TDD frames."""
    per_sf = 2 * _SYMS_PER_SLOT
    one = np.zeros(10 * per_sf, dtype=bool)
    for sf, kind in enumerate(_TDD_PATTERN):
        if kind == 'D':
            one[sf * per_sf:(sf + 1) * per_sf] = True
        elif kind == 'S':
            one[sf * per_sf:sf * per_sf + _TDD_DWPTS] = True
    return np.tile(one, int(n_frames))


def _cell_native(bw_mhz, n_frames, pci, seed, pss=True, sss=True, duplex='fdd', idle=False):
    """One downlink at the native rate, after its seeded frame roll.

    ``duplex`` 'tdd' moves the PSS to symbol 2 of slots 2 and 12 (DwPTS) and the
    SSS to symbol 6 of slots 1 and 11, and silences the uplink subframes, the
    guard period and UpPTS (configuration 1, special subframe 7).

    ``idle`` True keeps the QPSK only in the control region (the first three
    symbols of each subframe), as on a quiet real carrier: the other symbols
    carry the reference and synchronisation signals alone.

    Not normalised and with no noise. Returns ``(iq, native_fs, frame_offset, rng)``.
    ``rng`` has already drawn the offset and the QPSK bits, which is the point
    where ``synth_lte`` draws its noise, so that stream is unchanged.
    """
    pci = int(pci)
    if not 0 <= pci <= 503:
        raise ValueError(f'pci {pci} is outside 0..503')
    n_fft, native, n_sc = _class(bw_mhz)
    n_rb = n_sc // 12
    nid2 = pci % 3
    nid1 = pci // 3
    n_sym = int(n_frames) * _SLOTS_PER_FRAME * _SYMS_PER_SLOT
    frame_len = _frame_samples(n_fft)

    rng = np.random.default_rng(seed)
    offset = int(rng.integers(0, frame_len))

    used = _used_bins(n_fft, n_sc)
    bits = rng.integers(0, 2, size=(n_sym, n_sc, 2))
    qpsk = ((1 - 2 * bits[..., 0]) + 1j * (1 - 2 * bits[..., 1])) / np.sqrt(2.0)
    grid = np.zeros((n_sym, n_fft), dtype=np.complex128)
    grid[:, used] = qpsk
    if idle:
        grid[np.arange(n_sym) % (2 * _SYMS_PER_SLOT) >= 3] = 0

    rows = np.arange(int(n_frames)) * (_SLOTS_PER_FRAME * _SYMS_PER_SLOT)
    if duplex not in ('fdd', 'tdd'):
        raise ValueError(f'duplex {duplex!r} is not fdd or tdd')
    pss_seq = _pss(nid2)
    center = _center62(n_fft)
    if duplex == 'fdd':
        # PSS: last symbol of slots 0 and 10. SSS: the symbol before it.
        pss_at = (0 * _SYMS_PER_SLOT + 6, 10 * _SYMS_PER_SLOT + 6)
        sss_at = (0 * _SYMS_PER_SLOT + 5, 10 * _SYMS_PER_SLOT + 5)
    else:
        # PSS: symbol 2 of slots 2 and 12. SSS: last symbol of slots 1 and 11.
        pss_at = (2 * _SYMS_PER_SLOT + 2, 12 * _SYMS_PER_SLOT + 2)
        sss_at = (1 * _SYMS_PER_SLOT + 6, 11 * _SYMS_PER_SLOT + 6)
    # (6.11.1, 6.11.2). np.ix_ so the write hits the grid; a chained slice of
    # integer rows is a copy.
    for at in pss_at:
        grid[np.ix_(rows + at, center)] = pss_seq if pss else 0
    sss0, sss5 = _sss(nid1, nid2)
    grid[np.ix_(rows + sss_at[0], center)] = sss0 if sss else 0
    grid[np.ix_(rows + sss_at[1], center)] = sss5 if sss else 0

    for slot in range(_SLOTS_PER_FRAME):
        for symbol in (0, 4):
            k, r = _crs(pci, slot, symbol, n_rb)
            idx = rows + slot * _SYMS_PER_SLOT + symbol
            grid[np.ix_(idx, used[k])] = r

    if duplex == 'tdd':
        grid[~_tdd_mask(n_frames)] = 0
    iq = _assemble(grid, n_fft, n_sc)
    iq = np.roll(iq, offset)
    return iq, native, offset, rng


def synth_lte(bw_mhz=10, n_frames=10, pci=123, snr_db=20.0, seed=0, cfo_hz=0.0,
              fs=None, pss=True, sss=True, duplex='fdd', idle=False):
    """FDD downlink, one port, normal CP.

    Returns ``(iq complex64, fs)``. ``fs`` None keeps the native rate of the
    bandwidth class. The mean power is 1 before the noise; complex white noise
    is then added so that signal power over noise power is ``snr_db`` at the
    output rate (a non-finite ``snr_db`` adds none). ``cfo_hz`` rotates the
    output. ``pss`` False zeroes the primary synchronisation signal. ``sss``
    False zeroes the secondary synchronisation signal. ``duplex`` 'tdd' makes a
    TDD downlink (see ``_cell_native``); its silent symbols get noise only.
    ``idle`` True leaves the data symbols empty (see ``_cell_native``).
    """
    iq, native, _offset, rng = _cell_native(
        bw_mhz, n_frames, pci, seed, pss=pss, sss=sss, duplex=duplex, idle=idle)

    rate = native if fs is None else float(fs)
    if fs is not None and int(round(fs)) != int(round(native)):
        iq = _resample(iq, native, rate)

    power = np.mean(np.abs(iq) ** 2)
    iq = iq / np.sqrt(power)
    if np.isfinite(snr_db):
        noise_power = 10.0 ** (-float(snr_db) / 10.0)
        noise = np.sqrt(noise_power / 2.0) * (
            rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
        iq = iq + noise
    if cfo_hz:
        iq = iq * np.exp(2j * np.pi * float(cfo_hz) * np.arange(len(iq)) / rate)
    return iq.astype(np.complex64), rate


def synth_lte_multi(cells, bw_mhz=10, n_frames=10, snr_db=20.0, seed=0, cfo_hz=0.0,
                    fs=None, duplex='fdd', idle=False):
    """Several FDD downlinks on one carrier, summed, then one noise draw.

    ``cells`` is a list of ``dict(pci, power_db, delay_samples)``. Cell ``i`` is
    the same native-rate waveform as ``synth_lte`` with ``seed + i`` (so cell 0
    and ``synth_lte(..., seed=seed)`` share data and the random frame offset),
    with no per-cell noise. ``power_db`` 0 is that waveform's amplitude (about
    unit power, as ``_assemble`` leaves it); the cell is multiplied by
    ``10 ** (power_db / 20)``.

    ``delay_samples`` is an integer circular ``np.roll`` at the native rate,
    relative to the first cell: cell ``i``'s frame offset becomes the first
    cell's offset plus ``delay_samples``. A delay of 0 lines the PSS up with
    cell 0; it does not keep each cell's own random offset. The roll is
    ``np.roll(iq, first_offset + delay - own_offset)``.

    The weighted cells are summed, resampled with the same rule as ``synth_lte``
    (``fs`` None stays at the native rate), then scaled so the sum has unit
    mean power. Complex white noise is added at that output rate so total
    signal power over noise power is ``snr_db`` (non-finite adds none). The
    noise generator is ``SeedSequence([seed, 1])``, not a cell seed. ``cfo_hz``
    then rotates the output exactly as in ``synth_lte``. ``idle`` is passed to
    every cell.

    Returns ``(iq complex64, fs)`` and nothing else.
    """
    if not cells:
        raise ValueError('cells is empty')
    waves = []
    offsets = []
    native = None
    for i, cell in enumerate(cells):
        iq_i, native_i, offset, _rng = _cell_native(
            bw_mhz, n_frames, cell['pci'], int(seed) + i, duplex=duplex, idle=idle)
        if native is None:
            native = native_i
        waves.append(iq_i)
        offsets.append(offset)

    base = offsets[0]
    acc = np.zeros(waves[0].shape, dtype=np.complex128)
    for i, cell in enumerate(cells):
        extra = base + int(cell['delay_samples']) - offsets[i]
        weight = 10.0 ** (float(cell['power_db']) / 20.0)
        acc += np.roll(waves[i], extra) * weight

    rate = native if fs is None else float(fs)
    if fs is not None and int(round(fs)) != int(round(native)):
        acc = _resample(acc, native, rate)

    power = np.mean(np.abs(acc) ** 2)
    acc = acc / np.sqrt(power)
    if np.isfinite(snr_db):
        noise_rng = np.random.default_rng(np.random.SeedSequence([int(seed), 1]))
        noise_power = 10.0 ** (-float(snr_db) / 10.0)
        noise = np.sqrt(noise_power / 2.0) * (
            noise_rng.standard_normal(len(acc)) + 1j * noise_rng.standard_normal(len(acc)))
        acc = acc + noise
    if cfo_hz:
        acc = acc * np.exp(2j * np.pi * float(cfo_hz) * np.arange(len(acc)) / rate)
    return acc.astype(np.complex64), rate


def _symbol_spans(n_fft, n_symbols):
    """(start, cp_len) of each OFDM symbol, from the start of a frame."""
    cp0, cp = _cp_lengths(n_fft)
    spans = []
    pos = 0
    for s in range(n_symbols):
        this = cp0 if s % _SYMS_PER_SLOT == 0 else cp
        spans.append((pos, this))
        pos += this + n_fft
    return spans


def _check_cp(iq, n_fft, offset):
    aligned = np.roll(np.asarray(iq), -offset)
    spans = _symbol_spans(n_fft, _SLOTS_PER_FRAME * _SYMS_PER_SLOT * (len(aligned) // _frame_samples(n_fft)))
    for pos, cp in spans:
        if pos + cp + n_fft > len(aligned):
            return False, 'buffer shorter than one frame of symbols'
        prefix = aligned[pos:pos + cp]
        tail = aligned[pos + n_fft:pos + n_fft + cp]
        if not np.array_equal(prefix, tail):
            return False, f'CP mismatch at sample {pos}'
    return True, ''


def _check_pss(iq, n_fft, n_sc, offset, nid2):
    """Frequency-domain correlation of slot 0's PSS symbol against the three roots."""
    cp0, cp = _cp_lengths(n_fft)
    # symbol 6 of slot 0: one long symbol, then five short ones, then its own CP
    start = cp0 + n_fft + 5 * (cp + n_fft) + cp
    aligned = np.roll(np.asarray(iq), -offset)
    body = aligned[start:start + n_fft]
    spec = np.fft.fft(body)
    bins = _center62(n_fft)
    got = spec[bins]
    scores = []
    for root in range(3):
        ref = _pss(root)
        scores.append(abs(np.vdot(ref, got)) / (np.linalg.norm(ref) * np.linalg.norm(got)))
    best = int(np.argmax(scores))
    ok = best == nid2 and scores[nid2] > 0.9 and all(
        scores[i] < 0.5 for i in range(3) if i != nid2)
    detail = (f'roots {list(_PSS_ROOT)} correlate '
              + ', '.join(f'{s:.3f}' for s in scores)
              + f'; want root {_PSS_ROOT[nid2]}')
    return ok, detail


def _main():
    n_frames = 10
    pci = 123
    iq, rate = synth_lte(bw_mhz=10, n_frames=n_frames, pci=pci, snr_db=np.inf, seed=0)
    print(f'samples {len(iq)}  rate {rate:.0f}')
    n_fft, native, n_sc = _class(10)
    offset = int(np.random.default_rng(0).integers(0, _frame_samples(n_fft)))
    failed = 0

    ok, detail = _check_pss(iq, n_fft, n_sc, offset, pci % 3)
    print(f"  {'ok  ' if ok else 'FAIL'} PSS correlates with root {_PSS_ROOT[pci % 3]}"
          f" and not with the others  ({detail})")
    failed += not ok

    ok, detail = _check_cp(iq, n_fft, offset)
    line = f"  {'ok  ' if ok else 'FAIL'} CP of every symbol equals the symbol's tail"
    if not ok:
        line += f'  ({detail})'
    print(line)
    failed += not ok
    return failed


if __name__ == '__main__':
    sys.exit(_main())
