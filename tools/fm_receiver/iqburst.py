"""Bursts in a recording, and the bits of an FSK burst (workflow steps 3 to 5).

Numpy only, like ``iqmeasure``. Two parts:

``find_bursts``  the transmissions of a wide recording, each as a time span and a
                 frequency span, found from a short-time spectrum (a hopping network
                 is a few ms of 100-500 kHz here and there in a 27 MHz capture).
``demod_fsk``    the bits of one burst cut: the centre, a discriminator, the symbol
                 rate chosen from the candidates by the longest alternating preamble,
                 and symbol timing that follows a clock a few hundred ppm off.

Neither decodes a protocol. A burst becomes a bit string; ``iqframes`` works on the bits.
"""

import numpy as np

DEFAULT_RATES = (9600.0, 19200.0, 38400.0, 50000.0, 100000.0, 150000.0, 200000.0, 300000.0)
MIN_PREAMBLE = 16          # alternating bits before a rate counts as having a preamble
MARGIN_BITS = 16           # bits kept past the burst's end: the length field decides the frame's end


# ---- finding the bursts -----------------------------------------------------------------

def _runs(mask, close=2):
    """(first, last) bin of each run of True in a 1-D mask, runs closer than ``close`` merged."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(idx) > close + 1)
    starts = np.concatenate(([0], cuts + 1))
    ends = np.concatenate((cuts, [idx.size - 1]))
    return [(int(idx[a]), int(idx[b])) for a, b in zip(starts, ends)]


def _frame_len(rate, frame_s=50e-6):
    """A power of two, 128 to 2048 samples, closest to ``frame_s`` seconds at ``rate``."""
    n = 128
    while n < 2048 and n * 2 * 0.75 <= rate * frame_s:
        n *= 2
    return n


def find_bursts(source, t0=None, t1=None, nfft=None, over_db=12.0, min_s=150e-6, min_frames=2,
                max_bw_hz=5e6, max_run_share=0.5, edge_share=0.04, chunk_frames=4096,
                merge_gap_hz=300e3):
    """The transmissions in ``source`` (an ``iqmeasure.IQSource``), as a list of dicts.

    A frame is ``nfft`` samples (default: about 50 us, so a burst of a few ms is dozens of
    frames at any file rate), a cell is active when it is ``over_db`` above that bin's
    median in its chunk, active cells that touch in time and frequency are one burst. A burst
    lasts at least ``min_s`` and ``min_frames`` frames. A run wider than ``max_run_share`` of
    the band is not a narrowband burst (a switching click splatters over all of it) and is
    ignored; the outer ``edge_share`` of the band is ignored (a converter's filter skirt
    makes false bursts there). A burst's frequency span is the bins active in at least half
    its frames, so one splattered frame does not widen it. Each dict: t0, t1 (seconds into
    the file), f_lo, f_hi, centre_hz, bw_hz (absolute Hz), snr_db (mean of the four
    strongest frames over the floor). Tracks that overlap in time and sit within one burst
    width of each other are merged (``_merge_overlapping``): sidelobes and fragments.
    """
    rate, centre = source.rate, source.center_hz
    nfft = _frame_len(rate) if nfft is None else int(nfft)
    first = 0 if t0 is None else max(0, int(t0 * rate))
    last = source.samples if t1 is None else min(source.samples, int(t1 * rate))
    n_frames = (last - first) // nfft
    win = np.hanning(nfft).astype(np.float32)
    edge = int(edge_share * nfft)
    frame_s = nfft / rate
    done, open_tracks = [], []

    def close_track(tr):
        frames = tr['f1'] - tr['f0'] + 1
        if frames < min_frames or frames * frame_s < min_s:
            return
        core = np.flatnonzero(tr['hits'] >= 0.5 * frames)
        if core.size == 0:
            return
        lo, hi = int(core[0]), int(core[-1])
        f_lo = centre + (lo - nfft / 2) * rate / nfft
        f_hi = centre + (hi + 1 - nfft / 2) * rate / nfft
        bw = f_hi - f_lo
        if bw <= max_bw_hz:
            done.append(dict(t0=(first / rate) + tr['f0'] * frame_s, t1=(first / rate) + (tr['f1'] + 1) * frame_s,
                             f_lo=f_lo, f_hi=f_hi, centre_hz=0.5 * (f_lo + f_hi), bw_hz=bw,
                             snr_db=float(np.mean(sorted(tr['snr'])[-4:]))))

    max_run = max_run_share * nfft
    for c0 in range(0, n_frames, chunk_frames):
        nf = min(chunk_frames, n_frames - c0)
        x = source.read(first + c0 * nfft, nf * nfft)
        if len(x) < nf * nfft:
            nf = len(x) // nfft
            x = x[:nf * nfft]
        if nf == 0:
            break
        spec = np.fft.fftshift(np.fft.fft(x.reshape(nf, nfft) * win, axis=1), axes=1)
        power = 10 * np.log10(spec.real ** 2 + spec.imag ** 2 + 1e-20)
        floor = np.median(power, axis=0)
        active = power > floor + over_db
        active[:, :edge] = False
        active[:, nfft - edge:] = False
        for k in range(nf):
            gframe = c0 + k
            runs = [r for r in _runs(active[k]) if r[1] - r[0] + 1 <= max_run]
            used = set()
            for a, b in runs:
                hit = None
                for i, tr in enumerate(open_tracks):
                    if i in used or gframe - tr['f1'] > 3:
                        continue
                    if a <= tr['e1'] + 1 and b >= tr['e0'] - 1:
                        hit = i
                        break
                snr = float(np.max(power[k, a:b + 1] - floor[a:b + 1]))
                if hit is None:
                    hits = np.zeros(nfft, dtype=np.int32)
                    hits[a:b + 1] += 1
                    open_tracks.append(dict(f0=gframe, f1=gframe, e0=a, e1=b, snr=[snr], hits=hits))
                    used.add(len(open_tracks) - 1)
                else:
                    tr = open_tracks[hit]
                    tr.update(f1=gframe, e0=a, e1=b)
                    tr['hits'][a:b + 1] += 1
                    tr['snr'].append(snr)
                    used.add(hit)
            keep = []
            for tr in open_tracks:
                if gframe - tr['f1'] > 3:
                    close_track(tr)
                else:
                    keep.append(tr)
            open_tracks = keep
    for tr in open_tracks:
        close_track(tr)
    done.sort(key=lambda d: d['t0'])
    return _merge_overlapping(done, frame_s, merge_gap_hz)


def _merge_overlapping(bursts, frame_s, min_gap_hz):
    """Merge tracks that overlap in time and sit within a burst-width of each other.

    Unshaped FSK has sidelobes that a 12 dB threshold reports as narrow bursts of their own,
    and a burst whose bins fade for a few frames splits in two. Tracks that overlap in time
    (by half the shorter, or touching to within four frames) and whose frequency spans are no further apart
    than the wider one's width (at least ``min_gap_hz``) are one transmission: the union of
    their spans, the strongest SNR. Two unrelated transmissions on neighbouring channels at
    the same instant would merge too; on a hopping network a node is on one channel at a time.
    """
    items = [dict(b) for b in bursts]
    changed = True
    while changed:
        changed = False
        out = []
        while items:
            cur = items.pop()
            merged = True
            while merged:
                merged = False
                rest = []
                for o in items:
                    t_over = min(cur['t1'], o['t1']) - max(cur['t0'], o['t0'])
                    shorter = min(cur['t1'] - cur['t0'], o['t1'] - o['t0'])
                    f_gap = max(cur['f_lo'], o['f_lo']) - min(cur['f_hi'], o['f_hi'])
                    reach = max(cur['bw_hz'], o['bw_hz'], min_gap_hz)
                    t_touch = -t_over <= 4 * frame_s       # overlap, or at most four frames apart
                    if (t_over >= 0.5 * shorter - frame_s or t_touch) and f_gap <= reach:
                        cur = dict(t0=min(cur['t0'], o['t0']), t1=max(cur['t1'], o['t1']),
                                   f_lo=min(cur['f_lo'], o['f_lo']), f_hi=max(cur['f_hi'], o['f_hi']),
                                   snr_db=max(cur['snr_db'], o['snr_db']))
                        cur['bw_hz'] = cur['f_hi'] - cur['f_lo']
                        cur['centre_hz'] = 0.5 * (cur['f_lo'] + cur['f_hi'])
                        merged = changed = True
                    else:
                        rest.append(o)
                items = rest
            out.append(cur)
        items = out
        if not changed:
            break
    items.sort(key=lambda d: d['t0'])
    return items


# ---- the bits of one burst --------------------------------------------------------------

def _lowpass(x, cutoff_hz, fs):
    """Windowed-sinc low-pass by convolution ('same' length)."""
    cutoff = min(cutoff_hz, 0.45 * fs)
    n = int(max(31, min(1201, round(6.0 * fs / cutoff)))) | 1
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * cutoff / fs * t) * np.hamming(n)
    h /= h.sum()
    return np.convolve(x, h, mode='same')


def _burst_span(iq, fs):
    env = np.abs(iq)
    w = max(3, int(fs * 10e-6))
    sm = np.convolve(env, np.ones(w) / w, mode='same')
    top = np.percentile(sm, 99.5)
    on = np.flatnonzero(sm > 0.4 * top)
    if on.size < 8:
        return None
    return int(on[0]), int(on[-1]) + 1


def _centre_offset(seg, fs, rate):
    """Offset of the two tones' midpoint from 0 Hz: the power centroid within 0.9 rate of the peak."""
    n = 1 << 15 if len(seg) >= 1 << 15 else max(1024, 1 << int(np.ceil(np.log2(len(seg)))))
    x = seg[:n]
    p = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(len(x)).astype(np.float32), n))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs))
    k = max(3, int(n * 0.1 * rate / fs))
    sm = np.convolve(p, np.ones(k) / k, mode='same')
    peak = f[int(np.argmax(sm))]
    sel = np.abs(f - peak) < 0.9 * rate
    return float(np.sum(f[sel] * p[sel]) / max(np.sum(p[sel]), 1e-30))


def _track_bits(f, sps, phase, start, stop, gain=0.2, period_gain=0.02, max_pull=0.04):
    """Slice ``f`` (a discriminator output) every ``sps`` samples, following its zero crossings.

    The first sample point is ``start + phase``; sampling runs to ``stop``. A transition
    between two symbols falls halfway between their sample points when the clock is right;
    its distance from there nudges the next sample point and, slower, the symbol period.
    Returns (bits, sample positions).
    """
    n = len(f)
    pos = float(start + phase)
    period = float(sps)
    nominal = float(sps)
    bits, where = [], []
    prev_val = None
    prev_pos = None
    while pos < stop and pos < n - 1:
        i = int(pos)
        frac = pos - i
        val = f[i] * (1 - frac) + f[i + 1] * frac
        bits.append(1 if val > 0 else 0)
        where.append(pos)
        if prev_val is not None and (val > 0) != (prev_val > 0):
            # the transition lies between prev_pos and pos: find where f crosses zero
            lo, hi = int(prev_pos), min(int(pos) + 1, n - 1)
            seg = f[lo:hi + 1]
            s = np.sign(seg)
            cross = np.flatnonzero(s[:-1] * s[1:] < 0)
            if cross.size:
                # the crossing nearest the middle of the two sample points
                mid = 0.5 * (prev_pos + pos)
                best, best_d = None, None
                for c in cross:
                    a, b = seg[c], seg[c + 1]
                    z = lo + c + a / (a - b)
                    d = abs(z - mid)
                    if best_d is None or d < best_d:
                        best, best_d = z, d
                err = best - mid
                err = max(-0.5 * period, min(0.5 * period, err))
                pos += gain * err
                period += period_gain * err
                period = max(nominal * (1 - max_pull), min(nominal * (1 + max_pull), period))
        prev_val, prev_pos = val, where[-1]
        pos += period
    return np.array(bits, dtype=np.uint8), np.array(where)


def _longest_alternation(bits):
    """(start, length) of the longest run where each bit differs from the one before."""
    if len(bits) < 2:
        return 0, len(bits)
    same = np.flatnonzero(bits[1:] == bits[:-1])
    edges = np.concatenate(([-1], same, [len(bits) - 1]))
    lengths = np.diff(edges)
    k = int(np.argmax(lengths))
    return int(edges[k] + 1), int(lengths[k])


def _eye(f, where):
    v = np.abs(f[np.clip(where.astype(int), 0, len(f) - 1)])
    if v.size < 8 or v.std() == 0:
        return 0.0
    return float(v.mean() / v.std())


def demod_fsk(iq, fs, rates=DEFAULT_RATES, min_preamble=MIN_PREAMBLE, margin_bits=MARGIN_BITS):
    """The bits of the FSK burst in ``iq`` (complex, sample rate ``fs``), or None.

    The symbol rate is the candidate that gives the longest alternating run (a preamble);
    ties and runs shorter than ``min_preamble`` fall back to the eye (mean over spread of
    the discriminator at the sample points), which is a worse judge: it prefers a rate
    below the true one on weak bursts. Returns a dict:

    ``rate_baud``, ``preamble_bits``, ``eye``, ``centre_offset_hz``, ``deviation_hz``,
    ``bits`` (a '0'/'1' string from the start of the preamble to ``margin_bits`` past the
    burst's end; positive frequency is 1, so polarity is not resolved), ``candidates``
    (rate, preamble bits, eye of each rate tried).

    Limit: symbols sent while the power is more than about 20 dB down (a slow power ramp
    eating into the last data symbols) are not reliable; the discriminator of the noise
    there is as large as the signal's.
    """
    iq = np.asarray(iq, dtype=np.complex64)
    span = _burst_span(iq, fs)
    if span is None:
        return None
    s, e = span
    seg = iq[s:e]
    best, cands = None, []
    for rate in rates:
        sps = fs / rate
        if sps < 3.0 or len(seg) < 8 * sps:
            continue
        off = _centre_offset(seg, fs, rate)
        n = np.arange(len(iq))
        z = iq * np.exp(-2j * np.pi * off * n / fs).astype(np.complex64)
        z = _lowpass(z, 0.75 * rate + 0.5 * rate * 0.0, fs)
        d = np.angle(z[1:] * np.conj(z[:-1])) * fs / (2 * np.pi)
        # The discriminator of noise is as large as that of the signal: outside the burst it
        # would smear into the last symbols, which are the frame's check bytes. Hold it at 0.
        env = np.abs(z[1:])
        gate = (env > 0.1 * np.median(env[s:e])).astype(np.float32)
        d = _lowpass(d * gate, 0.6 * rate, fs)
        d = d - np.median(d[s:e])
        # sample phase: the phase at which the discriminator is furthest from zero
        stop = e + margin_bits * sps
        best_ph, best_sc = 0.0, -1.0
        for ph in np.arange(0.0, sps, max(0.5, sps / 16)):
            idx = (s + ph + sps / 2 + np.arange(int((e - s) / sps)) * sps).astype(int)
            idx = idx[idx < len(d)]
            if idx.size < 8:
                continue
            v = np.abs(d[idx])
            sc = v.mean() / (v.std() + 1e-9)
            if sc > best_sc:
                best_ph, best_sc = ph, sc
        bits, where = _track_bits(d, sps, best_ph + sps / 2, s, stop)
        a, ln = _longest_alternation(bits)
        eye = _eye(d, where[a:a + ln]) if ln >= 8 else _eye(d, where)
        cands.append((rate, ln, round(eye, 2)))
        key = (ln if ln >= min_preamble else 0, eye)
        if best is None or key > best[0]:
            best = (key, rate, bits, where, a, ln, eye, off, d)
    if best is None:
        return None
    _, rate, bits, where, a, ln, eye, off, d = best
    if ln < min_preamble and eye < 3.0:
        return dict(rate_baud=None, preamble_bits=ln, eye=round(eye, 2), centre_offset_hz=off, deviation_hz=None,
                    bits='', candidates=cands)
    use = bits[a:] if ln >= min_preamble else bits
    dev = float(np.mean(np.abs(d[np.clip(where.astype(int), 0, len(d) - 1)])))
    return dict(rate_baud=rate, preamble_bits=ln if ln >= min_preamble else 0, eye=round(eye, 2),
                centre_offset_hz=off, deviation_hz=dev, bits=''.join('1' if b else '0' for b in use),
                candidates=cands)


def bits_to_bytes(bits, invert=False, lsb_first=False, start=0):
    """Pack a '0'/'1' string into bytes from bit ``start``; drops a trailing partial byte."""
    arr = np.frombuffer(bits.encode(), dtype=np.uint8)[start:] - ord('0')
    if invert:
        arr = 1 - arr
    n = len(arr) // 8 * 8
    return np.packbits(arr[:n].astype(np.uint8), bitorder='little' if lsb_first else 'big').tobytes()
