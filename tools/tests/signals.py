"""A synthetic FM broadcast, for testing the receiver with no radio.

Builds the multiplex a station sends - mono (L+R)/2, the 19 kHz pilot, L-R
on a 38 kHz DSB-SC subcarrier, RDS at 57 kHz - frequency-modulates it at
75 kHz peak deviation and writes it as an IQ recording this app can play
(``.cfile`` + ``.json``), with the station offset from the recording's
centre and a little noise.

The RDS is a minimal encoder written for the tests: group 0A (the PS name)
and 2A (RadioText), with the checkwords from ``rds_core``'s own code, the
differential coding and biphase symbols of IEC 62106, bit clock and
subcarrier locked to the pilot (x3 and /16).

``convention`` picks the 38 kHz phase: ``'standard'``, pilot and subcarrier
both sines (what broadcasters send), or ``'cosine'``, both cosines (what the
RF bench toolkit's FM + RDS transmitter sends).
"""

import json
import math
import os
import struct

import numpy as np
from scipy import signal as sps

from fm_receiver.rds_core import OFFSET, syndrome

PILOT = 19000.0
MPX_RATE = 250000.0


def rds_block(info, name):
    return (info << 10) | (syndrome(info << 10) ^ OFFSET[name])


def rds_groups(pi, ps, radiotext, pty=10, tp=True):
    """One cycle of groups: the PS in four 0A groups, the RadioText in 2A."""
    ps = (ps + ' ' * 8)[:8]
    groups = []
    for seg in range(4):
        b = (0 << 12) | (0 << 11) | (int(tp) << 10) | (pty << 5) | (1 << 3) | seg
        c = 0xE0CD                                  # no alternative frequencies
        d = (ord(ps[2 * seg]) << 8) | ord(ps[2 * seg + 1])
        groups.append((pi, b, c, d))
    text = radiotext[:64]
    if len(text) < 64:
        text += '\r'
    text += ' ' * (-len(text) % 4)
    for seg in range(len(text) // 4):
        b = (2 << 12) | (0 << 11) | (int(tp) << 10) | (pty << 5) | (0 << 4) | seg
        chars = [ord(ch) for ch in text[4 * seg:4 * seg + 4]]
        groups.append((pi, b, (chars[0] << 8) | chars[1], (chars[2] << 8) | chars[3]))
    return groups


def rds_bits(groups, nbits):
    """Enough differentially encoded bits for ``nbits`` bit periods."""
    raw = []
    while len(raw) < nbits + 104:
        for pi, b, c, d in groups:
            for info, name in ((pi, 'A'), (b, 'B'), (c, 'C'), (d, 'D')):
                block = rds_block(info, name)
                raw.extend((block >> i) & 1 for i in range(25, -1, -1))
    raw = np.array(raw[:nbits + 1], dtype=np.uint8)
    return np.bitwise_xor.accumulate(raw)          # e[n] = d[n] ^ e[n-1]


def multiplex(seconds, left, right, convention='standard', pi=0x1234,
              ps='TEST FM ', radiotext='Hello from the synthetic station',
              rds_level=0.04, pilot_level=0.09, audio_level=0.45):
    """The MPX at 250 kHz, in shares of full deviation."""
    n = int(seconds * MPX_RATE)
    t = np.arange(n) / MPX_RATE
    wp = 2 * np.pi * PILOT * t
    if convention == 'standard':
        pilot, sub38, sub57 = np.sin(wp), np.sin(2 * wp), np.sin(3 * wp)
    else:
        pilot, sub38, sub57 = np.cos(wp), np.cos(2 * wp), np.cos(3 * wp)
    mid, side = (left + right) / 2, (left - right) / 2
    # Biphase symbols on the pilot-locked bit clock, then band-limited.
    bitpos = t * (PILOT / 16)
    nbits = int(bitpos[-1]) + 2
    bits = rds_bits(rds_groups(pi, ps, radiotext), nbits)
    idx = bitpos.astype(np.int64)
    first_half = (bitpos - idx) < 0.5
    level = np.where(bits[idx] == 1, 1.0, -1.0)
    symbols = np.where(first_half, level, -level)
    shaping = sps.firwin(401, 2400.0, fs=MPX_RATE)
    rds = sps.lfilter(shaping, 1.0, symbols)
    return (audio_level * mid + audio_level * side * sub38
            + pilot_level * pilot + rds_level * rds * sub57)


def tones(seconds, left_hz=1000.0, right_hz=2500.0, amplitude=0.8):
    t = np.arange(int(seconds * MPX_RATE)) / MPX_RATE
    left = amplitude * np.sin(2 * np.pi * left_hz * t) if left_hz else 0 * t
    right = amplitude * np.sin(2 * np.pi * right_hz * t) if right_hz else 0 * t
    return left, right


def fm_iq(mpx, rate, offset_hz, snr_db=35.0, seed=1):
    """Frequency-modulate the MPX at ``rate``, ``offset_hz`` off centre."""
    up = int(round(rate / MPX_RATE))
    mpx_up = sps.resample_poly(mpx, up, 1) if up > 1 else mpx
    phase = 2 * np.pi * 75e3 * np.cumsum(mpx_up) / rate
    t = np.arange(len(mpx_up)) / rate
    iq = 0.5 * np.exp(1j * (phase + 2 * np.pi * offset_hz * t))
    rng = np.random.default_rng(seed)
    noise_power = 0.25 / 10 ** (snr_db / 10) * (rate / 200e3)
    iq += np.sqrt(noise_power / 2) * (rng.standard_normal(len(iq))
                                      + 1j * rng.standard_normal(len(iq)))
    return iq.astype(np.complex64)


def write_station(path_base, seconds=8.0, rate=2.5e6, center_hz=98.4e6,
                  station_hz=98.7e6, convention='standard', **kw):
    """Write ``path_base.cfile`` + ``.json``; returns the .cfile path."""
    left, right = tones(seconds)
    mpx = multiplex(seconds, left, right, convention=convention, **kw)
    iq = fm_iq(mpx, rate, station_hz - center_hz)
    iq.tofile(path_base + '.cfile')
    with open(path_base + '.json', 'w') as fh:
        json.dump({'rate': rate, 'offset_hz': station_hz - center_hz,
                   'station_hz': station_hz, 'center_hz': center_hz}, fh)
    return path_base + '.cfile'


def _sdvr_header(tag, when, interval, count, centre=0.0, bins=0, scale=0.0):
    """One chunk's header page of a Sceptre DVR file (see sceptre_dvr.py)."""
    page = bytearray(4096)
    struct.pack_into('<d', page, 0, centre)
    struct.pack_into('<f', page, 8, scale)
    page[0x10:0x12] = tag
    page[0x12:0x14] = b'\x00\x01' if tag == b'CI' else b'\x00\x02'
    struct.pack_into('<dd', page, 0x14, math.floor(when), when - math.floor(when))
    struct.pack_into('<d', page, 0x24, interval)
    struct.pack_into('<I', page, 0x2c, count)
    if tag == b'SB':
        struct.pack_into('<I', page, 0x44, bins)
    return bytes(page)


def _pad(data):
    return data + bytes(-len(data) % 4096)


def write_sdvr(path, iq, rate, center_hz, run=1 << 15, rotate=True, stale=True,
               tiles=True, start=1.7907e9 + 0.25, ref_dbm=-20.0):
    """Write ``iq`` (complex, |x| < 1) as a Sceptre DVR file, ``path``.

    Made the way the real ones are: runs of ``run`` samples as int16 I, Q,
    each a header page and its data; spectrum tiles between them at an
    uneven rhythm (none, one or two after a run); the ring wrapped, so the
    newest chunks come first; and, if ``stale``, an old run left at the end
    from before the window the file describes. Returns the samples it holds
    as they will be read: quantised, scaled to 1.0 = full scale. The scale
    the runs carry makes full scale ``ref_dbm + 10`` dBm, as Sceptre's do.
    """
    n = len(iq) // run * run
    q = np.empty((n, 2), dtype='<i2')
    q[:, 0] = np.clip(np.round(iq[:n].real * 32768.0), -32768, 32767)
    q[:, 1] = np.clip(np.round(iq[:n].imag * 32768.0), -32768, 32767)
    interval = 1.0 / rate
    scale = 10 ** ((ref_dbm + 10) / 20) / 32768.0        # a count, in sqrt(mW)
    chunks = []
    for k in range(n // run):
        when = start + k * run * interval
        chunks.append(_sdvr_header(b'CI', when, interval, run, center_hz, scale=scale)
                      + _pad(q[k * run:(k + 1) * run].tobytes()))
        if tiles:
            for j in range((0, 1, 2)[k % 3]):
                chunks.append(_sdvr_header(b'SB', when, 2.34e-3, 16, bins=64)
                              + _pad(bytes((k * 7 + j) % 251 for _ in range(16 * 64))))
    if rotate:
        cut = len(chunks) * 2 // 3
        chunks = chunks[cut:] + chunks[:cut]
    if stale:
        chunks.append(_sdvr_header(b'CI', 1.6e9, interval, run, center_hz)
                      + _pad(bytes(run * 4)))
    end = start + n * interval
    head = bytearray(4096)
    head[:4] = b'SDVR'
    stream = bytearray(4096)
    struct.pack_into('<dd', stream, 0, math.floor(start), start - math.floor(start))
    struct.pack_into('<dd', stream, 16, math.floor(end), end - math.floor(end))
    struct.pack_into('<dd', stream, 32, center_hz - rate / 2, center_hz + rate / 2)
    with open(path, 'wb') as fh:
        fh.write(bytes(head) + bytes(stream) + bytes(stream) + bytes(4096))
        for c in chunks:
            fh.write(c)
    return (q[:, 0] + 1j * q[:, 1]).astype(np.complex64) / 32768.0


def tone_level(x, fs, hz):
    """Amplitude of the ``hz`` component of ``x`` (windowed single bin)."""
    x = np.asarray(x, dtype=np.float64)
    w = np.hanning(len(x))
    t = np.arange(len(x)) / fs
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * hz * t))) / np.sum(w)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
