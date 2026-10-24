"""5G NR downlink test signal (phase n1): synchronisation blocks on a loaded carrier.

Method, from ``tools/swarm-records/specs/n1.md`` ("The standard" and "Test signal"):

* ``synth_nr`` builds one cell: a carrier ``bw_mhz`` wide, centred at 0 Hz, whose
  used resource elements (floor(bw x 0.9 / scs) subcarriers) carry random QPSK
  with probability ``load``, and the SS/PBCH blocks (SSB) on top of it.
  - PSS: TS 38.211 7.4.2.2 (m-sequence, cyclic shift 43 N_ID2), symbol 0, k 56..182.
  - SSS: TS 38.211 7.4.2.3 (two m-sequences, m0 and m1 from N_ID1, N_ID2), symbol 2,
    k 56..182.
  - PBCH DM-RS: TS 38.211 7.4.1.4.1 and table 7.4.3.1-1: QPSK from the length-31 Gold
    sequence (Nc = 1600) with c_init = 2^11 (i_bar+1)(PCI//4+1) + 2^6 (i_bar+1) + PCI%4,
    on k = v + 4i (v = PCI mod 4). L_max 4: i_bar = i_SSB + 4 n_hf; L_max 8: i_SSB mod 8.
  - PBCH data fills the other PBCH resource elements (symbols 1, 3 all 240, symbol 2
    k 0..47 and 192..239) with random QPSK; the gaps of symbols 0 and 2 stay zero.
  - SSB symbol placement: TS 38.213 4.1 cases A (15 kHz), B and C (30 kHz), with the
    L_max 4 or 8 candidate lists, in a 5 ms half frame.
  - OFDM: TS 38.211 5.3.1, normal CP, nfft samples plus 144 nfft/2048 of CP, the first
    symbol of every 0.5 ms with 16 x 2^mu x nfft/2048 samples more.
* ``synth_nr_multi`` sums several such cells, each moved to its own carrier offset and
  power, then adds noise once.

Reading choices where the spec is open:

* ``ssb_offset_hz`` is the frequency of SSB subcarrier k = 120 (SS_REF) relative to the
  carrier centre (0 Hz of the cell's own capture). When it is not a multiple of the
  subcarrier spacing the whole carrier is rotated by the remainder (under scs/2), so SS_REF
  is exact and the carrier's centre moves by that remainder.
* Frame timing: the first radio-frame boundary is at ``truth['frame_start_s']`` (seconds
  from the capture's first sample, seeded, 1 sample to 10 ms, so sample 0 is never a boundary); earlier signal is the previous frame.
  A period of 5 ms sends in every half frame, n_hf alternating 0/1 (the given ``n_hf``
  is ignored); longer periods send in half frame ``n_hf`` of each frame with frame number
  (counted from the boundary above) a multiple of period/10 (period 10: every frame).
* ``truth['ssb_times_s']`` is the start of the first symbol's cyclic prefix of every SSB
  sent that lies wholly inside the capture, in order, with parallel lists
  ``ssb_indices`` and ``ssb_half_frames``.
* Power: a data RE has mean power ``load`` (QPSK present with probability ``load``), so every
  SSB RE (PSS, SSS, DM-RS, PBCH data) is scaled by sqrt(load) to have that same mean power;
  load = 1 is unchanged. The cell is normalised to unit mean power before the noise, as ``synth_lte``;
  non-finite ``snr_db`` adds none. ``cfo_hz`` rotates the output.
* ``synth_nr_multi`` returns ``(iq, fs, truths)``: ``truths`` one ``synth_nr`` truth per
  cell, plus ``power_db``, ``carrier_offset_hz`` and ``ssb_hz_from_center`` (the SSB's
  SS_REF from the capture centre). A cell's seed is ``seed + i`` unless it gives ``seed``.
  Its ``power_db`` 0 is unit power before the final normalisation of the sum.

Numpy builds the waveform; scipy only resamples when ``fs`` is not the native rate.

Run:  /home/user/miniconda3/envs/gnu/bin/python tools/tests/nr_synth.py
"""

import sys

import numpy as np
from scipy.signal import resample_poly

_GOLD_NC = 1600
_SSB_SC = 240
_SSB_REF_K = 120
_FRAME_MS = 10
_PSS_X_INIT = (0, 1, 1, 0, 1, 1, 1)   # x(0)..x(6)


# ----- sequences ----------------------------------------------------------------------

def _gold(c_init, nbits):
    """Gold sequence c(n) of 38.211 5.2.1, the first ``nbits`` after the Nc = 1600 skip."""
    total = _GOLD_NC + nbits
    x1 = np.zeros(total + 31, dtype=np.uint8)
    x1[0] = 1
    x2 = np.zeros(total + 31, dtype=np.uint8)
    x2[:31] = (int(c_init) >> np.arange(31)) & 1
    for n in range(total):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return (x1 ^ x2)[_GOLD_NC:_GOLD_NC + nbits]


def _mseq(init, taps, length=127):
    """x(i+7) = sum of x(i+t) for t in taps, mod 2."""
    x = np.zeros(length + 7, dtype=np.int64)
    x[:7] = init
    for i in range(length):
        x[i + 7] = sum(x[i + t] for t in taps) % 2
    return x[:length]


def _pss(nid2):
    """PSS d(n), n = 0..126 (38.211 7.4.2.2)."""
    x = _mseq(_PSS_X_INIT, (4, 0))
    n = np.arange(127)
    return (1 - 2 * x[(n + 43 * nid2) % 127]).astype(np.float64)


def _sss(nid1, nid2):
    """SSS d(n), n = 0..126 (38.211 7.4.2.3)."""
    x0 = _mseq((1, 0, 0, 0, 0, 0, 0), (4, 0))
    x1 = _mseq((1, 0, 0, 0, 0, 0, 0), (1, 0))
    m0 = 15 * (nid1 // 112) + 5 * nid2
    m1 = nid1 % 112
    n = np.arange(127)
    return ((1 - 2 * x0[(n + m0) % 127]) * (1 - 2 * x1[(n + m1) % 127])).astype(np.float64)


def _dmrs(pci, i_bar):
    """144 PBCH DM-RS values (38.211 7.4.1.4.1)."""
    c_init = (2 ** 11) * (i_bar + 1) * (pci // 4 + 1) + (2 ** 6) * (i_bar + 1) + (pci % 4)
    c = _gold(c_init, 288).astype(np.float64)
    return ((1 - 2 * c[0::2]) + 1j * (1 - 2 * c[1::2])) / np.sqrt(2.0)


def _qpsk(rng, shape):
    return ((1 - 2 * rng.integers(0, 2, shape)) + 1j * (1 - 2 * rng.integers(0, 2, shape))) / np.sqrt(2.0)


# ----- frame structure ----------------------------------------------------------------

def _candidates(case, l_max):
    """First-symbol index of each candidate SSB in a half frame (38.213 4.1)."""
    case = str(case).upper()
    if l_max not in (4, 8):
        raise ValueError('l_max must be 4 or 8')
    if case in ('A', 'C'):
        base, step, count = (2, 8), 14, (2 if l_max == 4 else 4)
    elif case == 'B':
        base, step, count = (4, 8, 16, 20), 28, (1 if l_max == 4 else 2)
    else:
        raise ValueError(f'case {case!r} is not A, B or C')
    return [b + step * n for n in range(count) for b in base]


def _numerology(scs_khz):
    mu = {15: 0, 30: 1}.get(int(scs_khz))
    if mu is None:
        raise ValueError('scs_khz must be 15 or 30')
    return mu


def _nfft(bw_mhz, scs_khz):
    need = 1.2 * bw_mhz * 1e6 / (scs_khz * 1e3)
    n = 1
    while n < need:
        n *= 2
    return n


def _frame_layout(nfft, mu):
    """(starts, cps, frame_len): CP start sample, CP length and the frame's length."""
    n_sym = 140 * (2 ** mu)
    cp = 144 * nfft // 2048
    extra = 16 * (2 ** mu) * nfft // 2048
    idx = np.arange(n_sym)
    cps = np.full(n_sym, cp, dtype=np.int64)
    cps[idx % (7 * 2 ** mu) == 0] += extra
    lens = cps + nfft
    starts = np.concatenate(([0], np.cumsum(lens)[:-1]))
    return starts, cps, int(lens.sum())


def _ssb_re(pci, i_bar, load, rng, pbch_syms=None):
    """The 4 x 240 grid of one SSB (zeros in the gaps); data REs present with prob ``load``.
    ``pbch_syms`` (432 values) fills the PBCH data REs, k first then l, instead of random QPSK."""
    nid1, nid2 = pci // 3, pci % 3
    g = np.zeros((4, _SSB_SC), dtype=np.complex128)
    pbch = np.zeros((4, _SSB_SC), dtype=bool)
    pbch[1] = True
    pbch[3] = True
    pbch[2, :48] = True
    pbch[2, 192:] = True
    g[0, 56:183] = _pss(nid2)
    g[2, 56:183] = _sss(nid1, nid2)
    v = pci % 4
    k1 = np.arange(v, v + 237, 4)
    k2 = np.concatenate((np.arange(v, v + 45, 4), np.arange(v + 192, v + 237, 4)))
    r = _dmrs(pci, i_bar)
    dm = np.zeros((4, _SSB_SC), dtype=bool)
    g[1, k1] = r[:60]
    g[2, k2] = r[60:84]
    g[3, k1] = r[84:]
    dm[1, k1] = dm[2, k2] = dm[3, k1] = True
    data = pbch & ~dm
    vals = _qpsk(rng, (4, _SSB_SC)) * (rng.random((4, _SSB_SC)) < load)
    g[data] = vals[data] if pbch_syms is None else np.asarray(pbch_syms)
    return g * np.sqrt(load)


def _resample(iq, native, fs):
    up, down = int(round(fs)), int(round(native))
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


# ----- the cell -----------------------------------------------------------------------

def _cell_native(pci, scs_khz, case, l_max, ssb_sent, period_ms, n_hf, bw_mhz,
                 ssb_offset_hz, dur_ms, seed, load, pbch=None):
    """One cell at the native rate. Returns (iq, native, truth, rng); unit mean power."""
    if not 0 <= int(pci) <= 1007:
        raise ValueError('pci must be 0..1007')
    if int(period_ms) not in (5, 10, 20, 40, 80, 160):
        raise ValueError('period_ms must be 5, 10, 20, 40, 80 or 160')
    mu = _numerology(scs_khz)
    scs = float(scs_khz) * 1e3
    nfft = _nfft(bw_mhz, scs_khz)
    native = nfft * scs
    n_sc = int(np.floor(bw_mhz * 1e6 * 0.9 / scs))
    cands = _candidates(case, l_max)
    ssb_sent = tuple(int(s) for s in ssb_sent)
    if any(s < 0 or s >= len(cands) for s in ssb_sent):
        raise ValueError(f'ssb_sent must index the {len(cands)} candidates of case {case} L_max {l_max}')

    rng = np.random.default_rng(seed)
    starts, cps, frame_len = _frame_layout(nfft, mu)
    syms_per_frame = len(starts)
    syms_per_hf = syms_per_frame // 2
    offset = int(rng.integers(1, frame_len))           # first boundary, in samples (never sample 0)
    n_out = int(round(dur_ms * 1e-3 * native))
    crop = frame_len - offset                          # stream starts one frame early
    n_frames = int(np.ceil((crop + n_out) / frame_len))

    lo = -(n_sc // 2)
    jc = int(round(ssb_offset_hz / scs))
    delta = float(ssb_offset_hz) - jc * scs
    first_bin = jc - _SSB_REF_K
    if first_bin < lo or first_bin + _SSB_SC > lo + n_sc:
        raise ValueError('the SSB does not fit inside the carrier')

    # Grid: frames x symbols x nfft bins.
    n_sym = n_frames * syms_per_frame
    used = (np.arange(lo, lo + n_sc)) % nfft
    grid = np.zeros((n_sym, nfft), dtype=np.complex128)
    grid[:, used] = _qpsk(rng, (n_sym, n_sc)) * (rng.random((n_sym, n_sc)) < load)

    cols = (np.arange(first_bin, first_bin + _SSB_SC)) % nfft
    sent = []
    for f in range(n_frames):
        frame_no = f - 1
        for hf in (0, 1):
            if period_ms == 5:
                use_hf = hf
            else:
                if frame_no % (period_ms // _FRAME_MS) != 0 or hf != n_hf:
                    continue
                use_hf = hf
            for idx in ssb_sent:
                s0 = f * syms_per_frame + hf * syms_per_hf + cands[idx]
                if l_max == 4:
                    i_bar = idx + 4 * use_hf
                else:
                    i_bar = idx % 8
                g = _ssb_re(int(pci), i_bar, load, rng,
                            None if pbch is None else pbch(frame_no, use_hf, idx))
                for sy in range(4):
                    row = grid[s0 + sy]
                    row[cols] = g[sy]
                t0 = f * frame_len + int(starts[s0 % syms_per_frame])
                sent.append((t0, idx, use_hf, s0, frame_no))

    td = np.fft.ifft(grid, axis=1) * (nfft / np.sqrt(n_sc))
    out = np.empty(n_frames * frame_len, dtype=np.complex128)
    for f in range(n_frames):
        base = f * frame_len
        for s in range(syms_per_frame):
            row = td[f * syms_per_frame + s]
            a = base + int(starts[s])
            c = int(cps[s])
            out[a:a + c] = row[nfft - c:]
            out[a + c:a + c + nfft] = row
    iq = out[crop:crop + n_out]
    if delta:
        iq = iq * np.exp(2j * np.pi * delta * np.arange(crop, crop + n_out) / native)
    iq = iq / np.sqrt(np.mean(np.abs(iq) ** 2))

    times, idxs, hfs, frames = [], [], [], []
    sym_len = nfft + cps
    for t0, idx, use_hf, s0, frame_no in sent:
        local = s0 % syms_per_frame
        end = t0 + int(np.sum(sym_len[local:local + 4]))
        if t0 >= crop and end <= crop + n_out:
            times.append((t0 - crop) / native)
            idxs.append(idx)
            hfs.append(use_hf)
            frames.append(frame_no)
    truth = dict(
        pci=int(pci), nid1=int(pci) // 3, nid2=int(pci) % 3, scs_khz=int(scs_khz), case=str(case).upper(),
        l_max=int(l_max), ssb_sent=ssb_sent, n_hf=int(n_hf), period_ms=int(period_ms),
        ssb_offset_hz=float(ssb_offset_hz), fs=float(native), frame_start_s=offset / native,
        bw_mhz=float(bw_mhz), native_fs=float(native), ssb_times_s=times, ssb_indices=idxs, ssb_half_frames=hfs,
        ssb_frames=frames)
    return iq, native, truth, rng


def _finish(iq, rate, snr_db, cfo_hz, rng):
    if np.isfinite(snr_db):
        noise_power = 10.0 ** (-float(snr_db) / 10.0)
        noise = np.sqrt(noise_power / 2.0) * (
            rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
        iq = iq + noise
    if cfo_hz:
        iq = iq * np.exp(2j * np.pi * float(cfo_hz) * np.arange(len(iq)) / rate)
    return iq.astype(np.complex64)


def synth_nr(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0, bw_mhz=10,
             ssb_offset_hz=0.0, dur_ms=100, snr_db=20.0, cfo_hz=0.0, fs=None, seed=0, load=1.0, pbch=None):
    """One NR cell with SSBs. Returns ``(iq complex64, fs, truth)``; see the module docstring.

    ``pbch`` (frame_no, half_frame, ssb_idx) -> 432 symbols gives each SSB's PBCH data, for a
    real PBCH (frame_no counts from 0 at the first frame of the output, -1 before it);
    ``truth['ssb_frames']`` holds each listed SSB's frame_no.

    ``fs`` None keeps the native rate nfft x scs. Signal power over noise power is
    ``snr_db`` at the output rate (non-finite adds none).
    """
    iq, native, truth, rng = _cell_native(
        pci, scs_khz, case, l_max, ssb_sent, period_ms, n_hf, bw_mhz, ssb_offset_hz,
        dur_ms, seed, load, pbch)
    rate = native if fs is None else float(fs)
    if int(round(rate)) != int(round(native)):
        iq = _resample(iq, native, rate)
        iq = iq / np.sqrt(np.mean(np.abs(iq) ** 2))
    truth['fs'] = float(rate)
    return _finish(iq, rate, snr_db, cfo_hz, rng), rate, truth


def synth_nr_multi(cells, bw_mhz=10, dur_ms=100, snr_db=20.0, fs=40e6, seed=0):
    """Several NR carriers in one capture, summed, then one noise draw.

    ``cells``: dicts of ``synth_nr`` keywords plus ``power_db`` and ``carrier_offset_hz``.
    ``bw_mhz`` is the default carrier width. A resampled cell can differ in length by one
    sample from the capture; the accumulator is truncated to the shortest. Returns ``(iq complex64, fs, truths)``.
    """
    if not cells:
        raise ValueError('cells is empty')
    rate = float(fs)
    acc = np.zeros(int(round(dur_ms * 1e-3 * rate)), dtype=np.complex128)
    truths = []
    for i, cell in enumerate(cells):
        kw = dict(cell)
        power_db = float(kw.pop('power_db', 0.0))
        off = float(kw.pop('carrier_offset_hz', 0.0))
        kw.setdefault('bw_mhz', bw_mhz)
        kw.setdefault('seed', int(seed) + i)
        kw['dur_ms'] = dur_ms
        kw['snr_db'] = float('inf')
        kw['fs'] = rate
        kw['cfo_hz'] = kw.get('cfo_hz', 0.0)
        iq, _, truth = synth_nr(**kw)
        n = min(len(iq), len(acc))
        t = np.arange(n) / rate
        acc[:n] += iq[:n] * (10.0 ** (power_db / 20.0)) * np.exp(2j * np.pi * off * t)
        truth.update(power_db=power_db, carrier_offset_hz=off,
                     ssb_hz_from_center=off + truth['ssb_offset_hz'])
        truths.append(truth)
    acc = acc / np.sqrt(np.mean(np.abs(acc) ** 2))
    noise_rng = np.random.default_rng(np.random.SeedSequence([int(seed), 1]))
    return _finish(acc, rate, snr_db, 0.0, noise_rng), rate, truths


# ----- self-check ---------------------------------------------------------------------

def _pss_scores(iq, fs, truth):
    """Mean normalised PSS correlation for N_ID2 = 0, 1, 2 at the truth's SSB times."""
    mu = _numerology(truth['scs_khz'])
    scs = truth['scs_khz'] * 1e3
    nfft = _nfft(truth['bw_mhz'], truth['scs_khz'])
    native = nfft * scs
    off = truth.get('carrier_offset_hz', 0.0)
    if off:
        iq = iq * np.exp(-2j * np.pi * off * np.arange(len(iq)) / fs)
    if int(round(fs)) != int(round(native)):
        iq = _resample(iq, fs, native)
    fs = native
    starts, cps, frame_len = _frame_layout(nfft, mu)
    frame0 = int(round(truth['frame_start_s'] * fs))
    jc = int(round(truth['ssb_offset_hz'] / scs))
    delta = truth['ssb_offset_hz'] - jc * scs
    bins = (np.arange(jc - _SSB_REF_K + 56, jc - _SSB_REF_K + 183)) % nfft
    ref = [_pss(n2) for n2 in range(3)]
    acc = np.zeros(3)
    for t in truth['ssb_times_s']:
        a = int(round(t * fs))
        s = int(np.searchsorted(starts, (a - frame0) % frame_len))
        a += int(cps[s])
        seg = iq[a:a + nfft].astype(np.complex128)
        seg = seg * np.exp(-2j * np.pi * delta * np.arange(a, a + nfft) / fs)
        x = np.fft.fft(seg)[bins]
        norm = np.sqrt(127.0) * np.linalg.norm(x)
        for n2 in range(3):
            acc[n2] += abs(np.vdot(ref[n2], x)) / norm
    return acc / max(1, len(truth['ssb_times_s']))


def _main():
    cases = [
        dict(pci=123, scs_khz=15, case='A', ssb_offset_hz=0.0, seed=0),
        dict(pci=1007, scs_khz=15, case='A', ssb_offset_hz=1.5e6, seed=1, ssb_sent=(1,), n_hf=1),
        dict(pci=217, scs_khz=30, case='C', bw_mhz=20, ssb_offset_hz=-2.55e6, seed=2),
        dict(pci=321, scs_khz=30, case='B', l_max=8, bw_mhz=20, ssb_offset_hz=0.0, seed=3,
             ssb_sent=(0, 2), period_ms=10),
    ]
    bad = 0
    for kw in cases:
        iq, fs, truth = synth_nr(**kw)
        sc = _pss_scores(iq, fs, truth)
        ok = int(np.argmax(sc)) == truth['nid2'] and len(truth['ssb_times_s']) > 0
        bad += not ok
        print(f"{'ok' if ok else 'FAIL'}  pci {truth['pci']} scs {truth['scs_khz']} case {truth['case']} "
              f"fs {fs / 1e6:.2f}M ssbs {len(truth['ssb_times_s'])} nid2 {truth['nid2']} "
              f"pss corr {np.round(sc, 3).tolist()}")
    iq, fs, truths = synth_nr_multi(
        [dict(pci=200, ssb_offset_hz=0.0, power_db=0.0, carrier_offset_hz=-10e6),
         dict(pci=217, ssb_offset_hz=0.0, power_db=-6.0, carrier_offset_hz=8e6)], seed=0)
    ok = len(iq) == 4_000_000 and all(len(t['ssb_times_s']) > 0 for t in truths)
    scs_ = [_pss_scores(iq, fs, t) for t in truths]
    ok = ok and all(int(np.argmax(sc)) == t['nid2'] for sc, t in zip(scs_, truths))
    bad += not ok
    print(f"{'ok' if ok else 'FAIL'}  multi: {len(iq)} samples at {fs / 1e6:.0f}M, "
          f"ssbs {[len(t['ssb_times_s']) for t in truths]} nid2 {[t['nid2'] for t in truths]} "
          f"pss corr {[np.round(sc, 3).tolist() for sc in scs_]}")
    print('self-check', 'passed' if not bad else f'FAILED ({bad})')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(_main())
