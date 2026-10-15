"""What the frames of one network have in common, and which scrambler and CRC fit them.

Numpy only; the input is bit strings from ``iqburst.demod_fsk`` (or hex frames). Four steps
(workflow steps 6 and 7):

1. ``align``      where a frame starts: the sync, as the bits every frame shares just
                  after its preamble (or one the caller names).
2. ``structure``  per byte position: constant or not, and which byte plus a constant
                  equals the frame's length.
3. ``search``     the catalog (``iqcatalog``): every scrambler (and none) and every CRC,
                  over start offsets and end rules, with the polarity and the bit order of
                  the bytes tried too. A hit counts only when it holds on several frames.
4. ``lfsr_hints`` windows whose bits obey a short linear recurrence (Berlekamp-Massey):
                  a scrambler not in the catalog shows up as low linear complexity.

The false-positive rate is the risk. A per-frame 255-seed search against 71 CRCs will
match an 8-bit CRC by chance on every frame, so a hypothesis is rejected beforehand when
its chance of a hit per frame is above ``MAX_CHANCE``, and every accepted hit carries
``p_chance`` (the binomial tail of its hit count) and the number of hypotheses tried.

CRC linearity is what makes the search fast. The descrambled data is ``raw xor keystream``
and a CRC register is linear in the data, so the register over the keystream is computed
once per (scrambler, seed, start) and shared by every frame.
"""

import itertools
import math

import numpy as np

from . import iqcatalog as cat

MAX_CHANCE = 0.05      # per-frame chance of a false hit above this: hypothesis not tried
ACCEPT_P = 1e-3        # accepted when (binomial tail of the hit count) x (hypotheses) is below this
VARIANTS = tuple(itertools.product((False, True), (False, True)))   # (invert, lsb_first)


# ---- bit strings to frames -----------------------------------------------------------

def _alternation(bits):
    """Length of the preamble: the leading run in which each bit differs from the last."""
    n = 1
    while n < len(bits) and bits[n] != bits[n - 1]:
        n += 1
    return n


def _pack(bits, invert, lsb_first):
    arr = np.frombuffer(bits.encode(), dtype=np.uint8) - ord('0')
    if invert:
        arr = 1 - arr
    n = len(arr) // 8 * 8
    return np.packbits(arr[:n].astype(np.uint8), bitorder='little' if lsb_first else 'big').tobytes()


def align(bit_frames, shifts=range(-2, 3), min_common=8, majority=0.7, min_keep=3):
    """(start bit of each frame, sync bits, indices of the frames that carry the sync).

    The frame starts where the preamble's alternation ends, give or take a bit (the sync's
    first bit often continues the alternation by chance). For each shift the sync is the
    longest consensus prefix: at each bit the frames still in the running must agree to
    ``majority``, and those that disagree drop out. The shift with the longest sync wins.
    Frames outside the consensus are left out of the search (noise, another network); with
    under ``min_keep`` frames in common, or a sync under ``min_common`` bits, all frames are
    kept, the alternation's end is the start and the sync is empty.
    """
    pre = [_alternation(b) for b in bit_frames]
    everyone = list(range(len(bit_frames)))
    best = (0, '', everyone)
    if len(bit_frames) >= min_keep:
        for s in shifts:
            starts = [p + s for p in pre]
            tails = [b[st:st + 64] if st >= 0 else '' for b, st in zip(bit_frames, starts)]
            alive = [i for i in everyone if len(tails[i]) >= min_common]
            sync = ''
            for col in range(64):
                alive_c = [i for i in alive if len(tails[i]) > col]
                if len(alive_c) < min_keep:
                    break
                ones = sum(tails[i][col] == '1' for i in alive_c)
                bit = '1' if ones * 2 >= len(alive_c) else '0'
                share = max(ones, len(alive_c) - ones) / len(alive_c)
                if share < majority:
                    break
                alive = [i for i in alive_c if tails[i][col] == bit]
                if len(alive) < min_keep:
                    break
                sync += bit
            if len(sync) > len(best[1]) or (len(sync) == len(best[1]) and len(alive) > len(best[2]) and sync):
                best = (s, sync, alive)
    shift, sync, keep = best
    if len(sync) < min_common:
        return list(pre), '', everyone
    # a leading bit that still alternates is the preamble's last, not the sync's first
    while len(sync) > min_common and sync[0] != sync[1] and shift < max(shifts):
        sync, shift = sync[1:], shift + 1
    # every frame is placed by the template, at the shift where it differs from it least, so
    # a frame with one bit error in its sync is kept (the strict consensus above dropped it)
    max_err = 0 if len(sync) < 12 else 1
    starts, kept = list(pre), []
    for i, (b, p) in enumerate(zip(bit_frames, pre)):
        best_s, best_d = None, None
        for sh in range(shift - 2, shift + 3):
            st = p + sh
            if st < 0 or st + len(sync) > len(b):
                continue
            d = sum(x != y for x, y in zip(b[st:st + len(sync)], sync))
            if best_d is None or d < best_d:
                best_s, best_d = sh, d
        if best_d is not None and best_d <= max_err:
            starts[i] = p + best_s
            kept.append(i)
    if len(kept) < min_keep:
        return [p + shift for p in pre], sync, keep
    return starts, sync, kept


def frames_as_bytes(bit_frames, starts, invert, lsb_first):
    return [_pack(b[st:], invert, lsb_first) for b, st in zip(bit_frames, starts)]


# ---- structure --------------------------------------------------------------------------

def structure(frames, width=16, min_share=0.4):
    """Per byte position the distinct values and whether it is constant, and the length fits.

    ``length_fields``: positions whose value tracks the frame's length. A frame read from air
    runs a few bytes past its end (the margin), so the offset ``len(frame) - frame[j]`` is
    not constant: a position counts when its top three offsets together hold at least
    ``min_share`` of the frames, and at least two distinct lengths exist. Each entry: pos,
    offset (the commonest), share.
    """
    rows = []
    for j in range(width):
        vals = [f[j] for f in frames if len(f) > j]
        if not vals:
            break
        distinct = sorted(set(vals))
        rows.append(dict(pos=j, distinct=len(distinct), constant=(len(distinct) == 1),
                         value=distinct[0] if len(distinct) == 1 else None))
    fits = []
    lengths = {len(f) for f in frames}
    if len(lengths) >= 2:
        for j in range(width):
            offs = [len(f) - f[j] for f in frames if len(f) > j]
            if len(offs) < 3:
                continue
            counts = {}
            for o in offs:
                counts[o] = counts.get(o, 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
            share = sum(c for _, c in top) / len(offs)
            # constant bytes (the sync) fit nothing: they carry no length
            if share >= min_share and len({f[j] for f in frames if len(f) > j}) > 3:
                fits.append(dict(pos=j, offset=top[0][0], share=round(share, 2)))
    return dict(positions=rows, length_fields=fits, lengths=sorted(lengths))


# ---- the search -------------------------------------------------------------------------

def _binom_tail(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    p = min(max(p, 1e-300), 1.0)
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * p ** i * (1 - p) ** (n - i)
    return min(1.0, total)


_REF8 = np.array(cat._REFLECT8, dtype=np.uint64)


def _reflect_vec(v, width):
    """Bit-reflect an array of ``width``-bit integers."""
    out = np.zeros_like(v)
    for i in range(width):
        out |= ((v >> i) & 1) << (width - 1 - i)
    return out


def _step_vec(reg, byte_col, spec, table):
    """One CRC byte step for an array of registers and a byte (scalar or array, uint64)."""
    w = spec.width
    if spec.refin:
        byte_col = _REF8[np.asarray(byte_col, dtype=np.int64)]
    idx = ((reg >> np.uint64(w - 8)) ^ byte_col) & np.uint64(0xFF)
    return ((reg << np.uint64(8)) & np.uint64((1 << w) - 1)) ^ table[idx]


def _keystreams(spec, nbytes):
    """(seeds array, keystream matrix [n_seeds, nbytes]) for a scrambler, or one zero row."""
    if spec is None:
        return np.array([0]), np.zeros((1, nbytes), dtype=np.uint8)
    seeds = np.array(spec.seeds)
    ks = np.zeros((len(seeds), nbytes), dtype=np.uint8)
    for i, s in enumerate(spec.seeds):
        ks[i] = np.frombuffer(cat.keystream(spec, int(s), nbytes), dtype=np.uint8)
    return seeds, ks


def search(frames, scramblers=None, crcs=None, starts=None, tail_slack=4, length_fields=(),
           min_hits=3, probe=6, byte_orders=('big', 'little'), report_top=10):
    """Scrambler and CRC hypotheses that fit at least ``min_hits`` of ``frames`` (bytes).

    A hypothesis is (scrambler or None, scrambler start, CRC, CRC start, byte order, end rule).
    The end rule is ``tail`` (the CRC field ends ``slack`` bytes before the frame's end,
    slack 0..``tail_slack``) or ``len`` (the CRC field starts at ``frame[j] + k``). The seed
    is free per frame. Returns a dict with ``hits`` (best first) and ``tried``.
    """
    scramblers = [None] + list(cat.SCRAMBLERS if scramblers is None else scramblers)
    crcs = list(cat.CRCS if crcs is None else crcs)
    # a frame heard twice is one frame: identical copies are not independent evidence
    frames = list(dict.fromkeys(bytes(f) for f in frames))
    n_frames = len(frames)
    if n_frames < min_hits:
        return dict(hits=[], tried=0, unique_frames=n_frames, note='fewer frames than min_hits')
    lmax = max(len(f) for f in frames)
    starts = sorted(set(range(0, 9)) if starts is None else set(starts))
    order = sorted(range(n_frames), key=lambda i: len(frames[i]))
    # probe frames: spread over the lengths
    probe_idx = [order[int(i * (n_frames - 1) / max(probe - 1, 1))] for i in range(min(probe, n_frames))]
    raw = np.zeros((n_frames, lmax), dtype=np.uint8)
    lens = np.array([len(f) for f in frames])
    for i, f in enumerate(frames):
        raw[i, :len(f)] = np.frombuffer(f, dtype=np.uint8)
    # 'len': the CRC field starts at frame[pos] + k, for k = 0..12 (the header before the
    # payload is a few bytes); 'tail': it ends slack bytes before the frame's end
    rules = [('tail', s) for s in range(tail_slack + 1)] + \
            [('len', fl['pos'], k) for fl in length_fields for k in range(13)]
    hits, tried = [], 0
    for scr in scramblers:
        seeds, ks_all = _keystreams(scr, lmax)
        ns = len(seeds)
        # a scrambler whose seed may be any non-zero state: a keystream that starts later is
        # another seed's, so its start offset cannot be told apart from the CRC's start.
        full_state = scr is not None and ns == (1 << scr.width) - 1
        for scr_start in (starts if scr is not None else [0]):
            ks = np.zeros_like(ks_all)
            if scr is not None:
                ks[:, scr_start:] = ks_all[:, :lmax - scr_start]
            for spec in crcs:
                n = spec.width // 8
                table = np.array(cat.crc_table(spec), dtype=np.uint64)
                p_frame = ns * 2.0 ** -spec.width
                if p_frame > MAX_CHANCE:
                    continue
                for crc_start in starts:
                    if full_state and crc_start != scr_start:
                        continue
                    # register over the keystream (init 0), prefix states for every end
                    regK = np.zeros((ns, lmax + 1), dtype=np.uint64)
                    reg = np.zeros(ns, dtype=np.uint64)
                    for j in range(crc_start, lmax):
                        reg = _step_vec(reg, ks[:, j].astype(np.uint64), spec, table)
                        regK[:, j + 1] = reg
                    # register over the raw frames (init), all frames at once, prefix states
                    regR = np.zeros((n_frames, lmax + 1), dtype=np.uint64)
                    reg = np.full(n_frames, spec.init, dtype=np.uint64)
                    regR[:, crc_start] = reg
                    for j in range(crc_start, lmax):
                        reg = _step_vec(reg, raw[:, j].astype(np.uint64), spec, table)
                        regR[:, j + 1] = reg
                    # field values: raw field and keystream field per end e
                    for order_name in byte_orders:
                        for rule in rules:
                            def match(i):
                                """The seeds (indices) that make frame i's CRC fit under this rule, or None."""
                                L = int(lens[i])
                                if rule[0] == 'tail':
                                    e = L - n - rule[1]
                                else:
                                    e = int(frames[i][rule[1]]) + rule[2] if len(frames[i]) > rule[1] else -1
                                if e < crc_start + 1 or e + n > L:
                                    return None, None
                                key = raw[i, crc_start:e + n].tobytes()
                                tot = regR[i, e] ^ regK[:, e]
                                if spec.refout:
                                    tot = _reflect_vec(tot, spec.width)
                                val = tot ^ np.uint64(spec.xorout)
                                fld_raw = int.from_bytes(bytes(raw[i, e:e + n]), order_name)
                                fld_ks = np.zeros(ns, dtype=np.uint64)
                                for b in range(n):
                                    shift = 8 * (n - 1 - b) if order_name == 'big' else 8 * b
                                    fld_ks |= ks[:, e + b].astype(np.uint64) << np.uint64(shift)
                                ok = np.flatnonzero(val == (np.uint64(fld_raw) ^ fld_ks))
                                return (ok if ok.size else None), key
                            tried += 1
                            matched = [None] * n_frames
                            keys = [None] * n_frames
                            seen_probe = False
                            for i in probe_idx:
                                matched[i], keys[i] = match(i)
                                if matched[i] is not None:
                                    seen_probe = True
                            if not seen_probe:
                                continue
                            for i in range(n_frames):
                                if i not in probe_idx:
                                    matched[i], keys[i] = match(i)
                            # copies of one body are one observation, not several
                            m = len({keys[i] for i in range(n_frames) if matched[i] is not None})
                            n_eff = len({k for k in keys if k is not None})
                            if m < min_hits:
                                continue
                            p = ns * 2.0 ** -spec.width
                            hits.append(dict(
                                scrambler=scr.name if scr else None, scr_start=scr_start if scr else None,
                                crc=spec.name, crc_start=crc_start, byte_order=order_name, rule=list(rule),
                                frames_matched=m, frames=n_eff,
                                seeds=[int(seeds[x[0]]) if x is not None else None for x in matched] if scr else None,
                                p_frame=p, p_tail=_binom_tail(m, n_eff, p)))
    for h in hits:
        h['p_adjusted'] = min(1.0, h['p_tail'] * max(tried, 1))
    hits.sort(key=lambda h: (h['p_adjusted'], -h['frames_matched']))
    # one hypothesis may fit under two end rules (a length byte and the frame's tail): keep the best
    seen, unique = set(), []
    for h in hits:
        key = (h['scrambler'], h['scr_start'], h['crc'], h['crc_start'], h['byte_order'])
        if key not in seen:
            seen.add(key)
            unique.append(h)
    hits = unique
    accepted = [h for h in hits if h['p_adjusted'] < ACCEPT_P]
    return dict(hits=accepted[:report_top], tried=tried, unaccepted=len(hits) - len(accepted),
                unique_frames=n_frames)


# ---- linear complexity ------------------------------------------------------------------

def linear_complexity(bits):
    """Berlekamp-Massey: the length of the shortest LFSR that makes ``bits`` (a 0/1 sequence)."""
    s = [int(b) for b in bits]
    n = len(s)
    c, b = [1] + [0] * n, [1] + [0] * n
    length, m = 0, -1
    for i in range(n):
        d = s[i]
        for j in range(1, length + 1):
            d ^= c[j] & s[i - j]
        if d:
            t = c[:]
            shift = i - m
            for j in range(n - shift + 1):
                if b[j]:
                    c[j + shift] ^= 1
            if length <= i // 2:
                length, m, b = i + 1 - length, i, t
    return length


def lfsr_hints(bit_frames, starts, window=96, step=32, max_share=0.25, max_frames=12):
    """Windows (frame, bit offset, complexity) where a short LFSR explains the bits.

    Random bits of a window of ``window`` bits have a linear complexity near window / 2;
    a window under ``max_share`` x window obeys a short recurrence.
    """
    out = []
    for i, (b, st) in enumerate(list(zip(bit_frames, starts))[:max_frames]):
        body = b[st:]
        for off in range(0, max(0, len(body) - window + 1), step):
            lc = linear_complexity(body[off:off + window])
            if lc < max_share * window:
                out.append(dict(frame=i, bit_offset=off, complexity=lc, window=window))
    return out


# ---- everything -------------------------------------------------------------------------

def analyse(bit_frames, variants=VARIANTS, min_hits=3, **kw):
    """Align, describe and search ``bit_frames`` (a list of '0'/'1' strings)."""
    starts, sync, keep = align(bit_frames)
    kept = [bit_frames[i] for i in keep]
    kept_starts = [starts[i] for i in keep]
    result = dict(n_frames=len(bit_frames), n_aligned=len(kept), sync_bits=sync, variants=[])
    for invert, lsb in variants:
        frames = frames_as_bytes(kept, kept_starts, invert, lsb)
        st = structure(frames)
        found = search(frames, min_hits=min_hits, length_fields=st['length_fields'], **kw)
        result['variants'].append(dict(invert=invert, lsb_first=lsb, structure=st, search=found,
                                       sync_hex=_pack(sync, invert, lsb).hex() if len(sync) >= 8 else ''))
    result['lfsr_hints'] = lfsr_hints(kept, kept_starts)
    return result
