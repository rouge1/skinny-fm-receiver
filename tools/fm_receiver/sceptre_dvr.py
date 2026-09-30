"""Sceptre DVR files (``.sdvr``), read as IQ recordings.

Signal Hound's Sceptre keeps a DVR of what the BB60D received in one
fixed-size file (``dvr.sdvr``, 1 GB by default) that it writes round and
round as a ring. With its IQ tab active the file holds the raw IQ, and this
module reads that IQ so the FM receiver can play it like any other
recording.

The layout below was worked out from the files alone - what is in them, and
what Sceptre's own displays said about the same data - never from Sceptre's
program or library. ``knowledge/sceptre-dvr.md`` has what is known and what
is not.

- **Pages.** The file is 4 KB pages. Page 0 starts ``SDVR``. Pages 1 and 2
  describe the streams: the window of time the ring holds (two doubles for
  the start, two for the end: whole seconds since 1970, then the fraction)
  and the band recorded. Page 3 can be left over from an earlier use of the
  file. Data starts at page 4.
- **Chunks.** From page 4 the file is a series of chunks, each one header page
  and then its data pages: ``CI`` chunks hold IQ, ``SB`` chunks hold the
  spectrum tiles Sceptre draws its waterfall from (16 sweeps of 16384 signed
  bytes, in this mode; skipped here). Both kinds start with the same header
  fields: at 0x10 the two-letter tag, at 0x14 the chunk's time (whole
  seconds then fraction, two doubles), at 0x24 the interval between its
  samples (or sweeps) in seconds, at 0x2c the number of samples (or sweeps).
  A ``CI`` header also has the centre frequency, a double at 0.
- **IQ** is int16, I then Q, full scale 32768, in runs of 1,048,576 samples
  (1,024 pages). Runs and tiles alternate at a slightly uneven rhythm. A
  ``CI`` header's float32 at 8 is the size of one count in Sceptre's own
  units, which are the square root of milliwatts: the power of a sample is
  ``(I**2 + Q**2) * scale**2`` mW. It follows the reference level in 20 dB
  steps, and a full-scale complex sample is ``ref + 10`` dBm (checked: the
  band's floor and a station's carrier came out within about 1 dB of the
  BB60D's own calibrated sweep).
- **The ring wraps**: the newest chunks are early in the file and the oldest
  are after them, so the chunks are put in time order, and any older than the
  window on page 1 - left from before - are dropped.

Nothing here writes the file, and Sceptre may be writing it while it is
read: pause the DVR first for a reliable read. No GNU Radio is needed here
(``radios.sdvr_source`` streams it into a flowgraph), so the tools that read
a DVR run in any Python with numpy.
"""

import bisect
import mmap
import os
import struct

import numpy as np

PAGE = 4096
FIRST_PAGE = 4
MAGIC = b'SDVR'
#: Bytes of an IQ sample: int16 I and int16 Q.
SAMPLE_BYTES = 4
FULL_SCALE = 32768.0
#: A chunk's start time may differ from the window on page 1 by this much (s).
WINDOW_SLACK_S = 0.005
#: Runs closer to each other than this many sample periods are contiguous.
GAP_TOLERANCE_S = 1e-3


class SdvrError(Exception):
    """The file is not one this can play; the message says why."""


def _time(mm, offset):
    whole, frac = struct.unpack_from('<dd', mm, offset)
    return whole + frac


def _chunk(mm, page):
    """The chunk whose header page is ``page``, or None if there is none.

    ``(tag, time, interval, count, data_pages, centre_hz)``.
    """
    o = page * PAGE
    tag = bytes(mm[o + 0x10:o + 0x12])
    if tag not in (b'CI', b'SB'):
        return None
    when = _time(mm, o + 0x14)
    interval = struct.unpack_from('<d', mm, o + 0x24)[0]
    count = struct.unpack_from('<I', mm, o + 0x2c)[0]
    if not (1e9 < when < 4e9) or not (0 < count < 1 << 28):
        return None
    if tag == b'CI':
        if not (1e-9 < interval < 1e-3):
            return None
        size = count * SAMPLE_BYTES
    else:
        bins = struct.unpack_from('<I', mm, o + 0x44)[0]
        if not (0 < bins < 1 << 24):
            return None
        size = count * bins
    centre = struct.unpack_from('<d', mm, o)[0] if tag == b'CI' else 0.0
    return tag, when, interval, count, -(-size // PAGE), centre


def scan(path):
    """Find the IQ in a Sceptre DVR file.

    Returns a dict: ``runs`` (``(byte offset, samples)`` in time order),
    ``samples``, ``rate``, ``center_hz``, ``band_hz``, ``start`` and ``end``
    (seconds since 1970), ``gaps`` (runs that do not follow on from the one
    before) and ``tiles``. Raises :class:`SdvrError` if the file is not a
    Sceptre DVR, or holds no IQ.
    """
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        head = fh.read(PAGE)
        if head[:4] != MAGIC:
            raise SdvrError(f"{os.path.basename(path)} is not a Sceptre DVR "
                            "file (it does not start with SDVR).")
        if size < (FIRST_PAGE + 2) * PAGE:
            raise SdvrError(f"{os.path.basename(path)} is too short to hold a "
                            "recording.")
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _scan(mm, size, os.path.basename(path))
        finally:
            mm.close()


def _scan(mm, size, name):
    window = None
    band = None
    t0, t1 = _time(mm, PAGE), _time(mm, PAGE + 16)
    if 1e9 < t0 < t1 < 4e9:
        window = (t0, t1)
        band = struct.unpack_from('<dd', mm, PAGE + 32)
    npages = size // PAGE
    runs, tiles = [], 0
    page = FIRST_PAGE
    while page < npages:
        chunk = _chunk(mm, page)
        if chunk is None:
            page += 1
            continue
        tag, when, interval, count, data_pages, centre = chunk
        if page + 1 + data_pages > npages:      # cut short by the end of the file
            break
        if tag == b'CI':
            runs.append((when, interval, count, centre, (page + 1) * PAGE,
                         struct.unpack_from('<f', mm, page * PAGE + 8)[0]))
        else:
            tiles += 1
        page += 1 + data_pages
    if not runs:
        raise SdvrError(f"{name} holds no IQ. This DVR was recording sweeps "
                        "or spectra; switch Sceptre to its IQ tab and record "
                        "again.")
    # One recording: the most common sample interval and centre.
    key = max(set((r[1], r[3]) for r in runs),
              key=lambda k: sum(1 for r in runs if (r[1], r[3]) == k))
    runs = [r for r in runs if (r[1], r[3]) == key]
    if window:
        lo, hi = window[0] - WINDOW_SLACK_S, window[1] + WINDOW_SLACK_S
        kept = [r for r in runs if lo <= r[0] and r[0] + r[2] * r[1] <= hi]
        runs = kept or runs
    runs.sort(key=lambda r: r[0])
    interval, centre = key
    scales = [r[5] for r in runs if r[5] > 0]
    scale = max(set(scales), key=scales.count) if scales else None
    gaps = sum(1 for a, b in zip(runs, runs[1:])
               if abs((b[0] - a[0]) - a[2] * interval) > GAP_TOLERANCE_S)
    return {
        'runs': [(r[4], r[2]) for r in runs],
        'samples': sum(r[2] for r in runs),
        'rate': round(1.0 / interval, 3),
        'center_hz': centre,
        # dBm of a complex sample of magnitude 1.0 (full scale): add it to a
        # level in dBFS for dBm. None if the file does not say.
        'scale': scale,
        'full_scale_dbm': (20 * np.log10(scale * FULL_SCALE) if scale else None),
        'band_hz': tuple(band) if band else None,
        'start': runs[0][0],
        'end': runs[-1][0] + runs[-1][2] * interval,
        'gaps': gaps,
        'tiles': tiles,
    }


class Reader:
    """The IQ of a scanned DVR file, in time order, as complex float32.

    ``fill`` and ``read`` are by sample number from the start of the first
    run. Plain numpy: nothing here needs GNU Radio.
    """

    def __init__(self, path, layout):
        self._data = np.memmap(path, dtype=np.int16, mode='r')
        self._runs = layout['runs']
        self.total = layout['samples']
        self._starts = [0]
        for _, count in self._runs:
            self._starts.append(self._starts[-1] + count)

    def fill(self, pos, out):
        """Copy the samples from ``pos`` into ``out`` (complex64), across
        runs, until ``out`` is full or the IQ ends. Returns how many."""
        scale = np.float32(1.0 / FULL_SCALE)
        done, want = 0, len(out)
        while done < want and pos < self.total:
            i = bisect.bisect_right(self._starts, pos) - 1
            offset, count = self._runs[i]
            within = pos - self._starts[i]
            n = min(want - done, count - within)
            first = offset // 2 + within * 2
            src = self._data[first:first + 2 * n].reshape(n, 2)
            dst = out[done:done + n].view(np.float32).reshape(n, 2)
            np.multiply(src, scale, out=dst)
            done += n
            pos += n
        return done

    def read(self, start, stop):
        """Samples ``start`` to ``stop`` (fewer at the end), as a new array."""
        out = np.empty(max(0, min(stop, self.total) - start), dtype=np.complex64)
        return out[:self.fill(start, out)]
