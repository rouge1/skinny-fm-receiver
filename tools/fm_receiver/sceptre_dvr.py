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
  bytes, in this mode; :class:`Sweeps` reads them). Both kinds start with the same header
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

- **Tiles** (``SB``) are 16 sweeps of ``bins`` signed bytes, stored bin by
  bin. Their header gives the start frequency (double at 0x34), the bin width
  (0x3c) and the bin count (0x44). :class:`Sweeps` reads them: a sweep DVR is
  nothing else (1,228,800 bins, 9 kHz to 6 GHz), and an IQ DVR's tiles are
  16384 bins over its band. **Each tile has its own scale**: two float32s at 8
  and 12 (``gain`` and ``offset``) make ``byte = gain * dBm + offset``, so
  ``dBm = (byte - offset) / gain`` (``Sweeps.read(dbm=True)``). Without them
  the bytes of different tiles cannot be compared: the scale runs from the
  tile's one deepest bin (-127, somewhere near -190 dBm, a different value
  every tile) to a fixed ceiling (+127, about -53 dBm), so the whole waterfall
  steps up and down by up to 6 dB from tile to tile.

**While Sceptre is recording.** The file can be read as it is written; what
that changes is handled here, and none of it was needed for a paused file:

- The window on page 1 can be left over from an earlier recording (a sweep
  DVR's was seen still describing an IQ one), so for a live file the chunks
  are trusted, not the window: the newest unbroken stretch of chunks in time
  is the recording, and anything before a break is left over.
- The newest chunk may be half written, so it is left out.
- The oldest chunks are the next ones overwritten. Each is checked, after it
  is copied, for the stamp it had when the file was scanned: a reader counts
  the chunks that changed under it (``overwritten``), and :class:`Sweeps`
  leaves them out. A scan is a snapshot: rescan for what has been recorded
  since.

A file counts as live when it was written in the last ``LIVE_S`` seconds
(``scan(path, live=...)`` says which, if that is wrong). Nothing here writes
the file. No GNU Radio is needed (``radios.sdvr_source`` streams it into a
flowgraph), so the tools that read a DVR run in any Python with numpy.
"""

import bisect
import mmap
import os
import struct
import time
from typing import NamedTuple

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
#: A file written this recently (s) is being recorded to. A sweep DVR writes a
#: tile every 3.7 s, so this is longer than that.
LIVE_S = 8.0
#: Chunks further apart than this (s), or twice the earlier one's length plus
#: half a second, are not from the same recording.
BREAK_S = 1.0


class SdvrError(Exception):
    """The file is not one this can play; the message says why."""


def _time(mm, offset):
    whole, frac = struct.unpack_from('<dd', mm, offset)
    return whole + frac


class Chunk(NamedTuple):
    """One chunk's header. ``start_hz``, ``bin_hz`` and ``bins`` are for a
    tile; ``centre`` and ``scale`` are for a run of IQ."""
    page: int
    tag: bytes
    when: float
    interval: float
    count: int
    data_pages: int
    centre: float
    scale: float
    start_hz: float
    bin_hz: float
    bins: int

    @property
    def data(self):
        """Byte offset of the chunk's data."""
        return (self.page + 1) * PAGE

    @property
    def seconds(self):
        return self.count * self.interval


def _chunk(mm, page):
    """The chunk whose header page is ``page``, or None if there is none."""
    o = page * PAGE
    tag = bytes(mm[o + 0x10:o + 0x12])
    if tag not in (b'CI', b'SB'):
        return None
    when = _time(mm, o + 0x14)
    interval = struct.unpack_from('<d', mm, o + 0x24)[0]
    count = struct.unpack_from('<I', mm, o + 0x2c)[0]
    if not (1e9 < when < 4e9) or not (0 < count < 1 << 28):
        return None
    scale = start_hz = bin_hz = 0.0
    bins = 0
    if tag == b'CI':
        if not (1e-9 < interval < 1e-3):
            return None
        size = count * SAMPLE_BYTES
        centre = struct.unpack_from('<d', mm, o)[0]
        scale = struct.unpack_from('<f', mm, o + 8)[0]
    else:
        bins = struct.unpack_from('<I', mm, o + 0x44)[0]
        if not (0 < bins < 1 << 24):
            return None
        size = count * bins
        centre = 0.0
        start_hz, bin_hz = struct.unpack_from('<dd', mm, o + 0x34)
    return Chunk(page, tag, when, interval, count, -(-size // PAGE), centre,
                 scale, start_hz, bin_hz, bins)


def _walk(mm, npages):
    """Every whole chunk in the file, in file order."""
    found = []
    page = FIRST_PAGE
    while page < npages:
        chunk = _chunk(mm, page)
        if chunk is None:
            page += 1
            continue
        if page + 1 + chunk.data_pages > npages:    # cut short by the end of the file
            break
        found.append(chunk)
        page += 1 + chunk.data_pages
    return found


def _is_live(path):
    return time.time() - os.path.getmtime(path) < LIVE_S


def _open(path, live):
    """``(chunks, window, band, live, name)`` of a DVR file; the chunks are in
    file order, and without the newest one if the file is being written."""
    size = os.path.getsize(path)
    name = os.path.basename(path)
    with open(path, 'rb') as fh:
        head = fh.read(PAGE)
        if head[:4] != MAGIC:
            raise SdvrError(f"{name} is not a Sceptre DVR "
                            "file (it does not start with SDVR).")
        if size < (FIRST_PAGE + 2) * PAGE:
            raise SdvrError(f"{name} is too short to hold a recording.")
        live = _is_live(path) if live is None else live
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            window = band = None
            t0, t1 = _time(mm, PAGE), _time(mm, PAGE + 16)
            if 1e9 < t0 < t1 < 4e9:
                window = (t0, t1)
                band = struct.unpack_from('<dd', mm, PAGE + 32)
            chunks = _walk(mm, size // PAGE)
        finally:
            mm.close()
    if live and chunks:
        chunks.remove(max(chunks, key=lambda c: c.when))    # may be half written
    return chunks, window, band, live, name


def _streak(chunks):
    """The newest unbroken stretch of ``chunks`` (time order, oldest first):
    what one recording wrote, without what an earlier one left."""
    kept = [chunks[-1]]
    for c in reversed(chunks[:-1]):
        if kept[-1].when - c.when > max(BREAK_S, 2 * c.seconds + 0.5):
            break
        kept.append(c)
    return kept[::-1]


def scan(path, live=None):
    """Find the IQ in a Sceptre DVR file.

    Returns a dict: ``runs`` (``(byte offset, samples)`` in time order),
    ``run_times`` (each run's header time, to tell later if it was
    overwritten), ``samples``, ``rate``, ``center_hz``, ``scale`` and
    ``full_scale_dbm`` (the dBm of a full-scale sample), ``band_hz``,
    ``start`` and ``end`` (seconds since 1970), ``gaps`` (runs that do not
    follow on from the one before), ``tiles`` and ``live``. Raises
    :class:`SdvrError` if the file is not a Sceptre DVR, or holds no IQ.
    ``live`` says whether Sceptre is writing the file; None looks at when it
    was last written.
    """
    chunks, window, band, live, name = _open(path, live)
    runs = [c for c in chunks if c.tag == b'CI']
    tiles = len(chunks) - len(runs)
    if not runs:
        raise SdvrError(f"{name} holds no IQ. This DVR was recording sweeps "
                        "or spectra; switch Sceptre to its IQ tab and record "
                        "again (Sweeps reads the sweeps it does hold).")
    # One recording: the newest run's sample interval and centre.
    newest = max(runs, key=lambda c: c.when)
    key = (newest.interval, newest.centre)
    runs = sorted((c for c in runs if (c.interval, c.centre) == key),
                  key=lambda c: c.when)
    if live or not window:
        # A live file's window may be stale: go by the chunks themselves.
        runs = _streak(runs)
    else:
        lo, hi = window[0] - WINDOW_SLACK_S, window[1] + WINDOW_SLACK_S
        kept = [c for c in runs if lo <= c.when and c.when + c.seconds <= hi]
        runs = kept or _streak(runs)        # a window from before holds none
    interval, centre = key
    scales = [c.scale for c in runs if c.scale > 0]
    scale = max(set(scales), key=scales.count) if scales else None
    gaps = sum(1 for a, b in zip(runs, runs[1:])
               if abs((b.when - a.when) - a.count * interval) > GAP_TOLERANCE_S)
    return {
        'runs': [(c.data, c.count) for c in runs],
        'run_times': [c.when for c in runs],
        'samples': sum(c.count for c in runs),
        'rate': round(1.0 / interval, 3),
        'center_hz': centre,
        # dBm of a complex sample of magnitude 1.0 (full scale): add it to a
        # level in dBFS for dBm. None if the file does not say.
        'scale': scale,
        'full_scale_dbm': (20 * np.log10(scale * FULL_SCALE) if scale else None),
        'band_hz': tuple(band) if band else None,
        'start': runs[0].when,
        'end': runs[-1].when + runs[-1].seconds,
        'gaps': gaps,
        'tiles': tiles,
        'live': live,
    }


class _Ring:
    """A DVR file mapped for reading, with the check that a chunk still is
    the one that was scanned."""

    def __init__(self, path):
        self._raw = np.memmap(path, dtype=np.uint8, mode='r')
        #: How many times a chunk was found overwritten after it was read.
        self.overwritten = 0

    def _stamp(self, data_offset):
        """The time in the header of the chunk whose data is at
        ``data_offset``, as it is now."""
        whole, frac = struct.unpack_from('<dd', self._raw,
                                         data_offset - PAGE + 0x14)
        return whole + frac


class Reader(_Ring):
    """The IQ of a scanned DVR file, in time order, as complex float32.

    ``fill`` and ``read`` are by sample number from the start of the first
    run. Plain numpy: nothing here needs GNU Radio. On a file Sceptre is
    still writing, a run can be overwritten after the scan: ``fill`` counts
    it in ``overwritten`` (the samples it gave for that run are then partly
    from a later time).
    """

    def __init__(self, path, layout):
        super().__init__(path)
        self._data = self._raw.view(np.int16)
        self._runs = layout['runs']
        self._times = layout.get('run_times')
        self.total = layout['samples']
        self._starts = [0]
        for _, count in self._runs:
            self._starts.append(self._starts[-1] + count)

    def fill(self, pos, out):
        """Copy the samples from ``pos`` into ``out`` (complex64), across
        runs, until ``out`` is full or the IQ ends. Returns how many."""
        scale = np.float32(1.0 / FULL_SCALE)
        done, want = 0, len(out)
        first = None
        while done < want and pos < self.total:
            i = bisect.bisect_right(self._starts, pos) - 1
            first = i if first is None else first
            offset, count = self._runs[i]
            within = pos - self._starts[i]
            n = min(want - done, count - within)
            at = offset // 2 + within * 2
            src = self._data[at:at + 2 * n].reshape(n, 2)
            dst = out[done:done + n].view(np.float32).reshape(n, 2)
            np.multiply(src, scale, out=dst)
            done += n
            pos += n
        if self._times and first is not None:
            last = bisect.bisect_right(self._starts, pos - 1) - 1
            for i in range(first, last + 1):
                if self._stamp(self._runs[i][0]) != self._times[i]:
                    self.overwritten += 1
        return done

    def read(self, start, stop):
        """Samples ``start`` to ``stop`` (fewer at the end), as a new array."""
        out = np.empty(max(0, min(stop, self.total) - start), dtype=np.complex64)
        return out[:self.fill(start, out)]


def scan_sweeps(path, live=None):
    """Find the spectrum tiles in a Sceptre DVR file (a sweep DVR's, or an IQ
    DVR's beside its runs).

    Returns a dict: ``tiles`` (``(byte offset, time)`` in time order),
    ``start_hz`` (of bin 0), ``bin_hz``, ``bins``, ``sweeps`` (per tile),
    ``interval`` (s between sweeps), ``start``, ``end``, ``gaps`` (tiles that
    do not follow on from the one before) and ``live``. The tiles are those
    of the newest tile's kind, in the newest unbroken stretch: never the
    window on page 1, which a sweep DVR leaves from an earlier recording.
    Raises :class:`SdvrError` if there are none.
    """
    chunks, _, _, live, name = _open(path, live)
    tiles = [c for c in chunks if c.tag == b'SB']
    if not tiles:
        raise SdvrError(f"{name} holds no spectrum tiles. This DVR was "
                        "recording IQ only, which sceptre_dvr.Reader reads.")
    newest = max(tiles, key=lambda c: c.when)
    kind = (newest.start_hz, newest.bin_hz, newest.bins, newest.count,
            newest.interval)
    tiles = _streak(sorted((c for c in tiles
                            if (c.start_hz, c.bin_hz, c.bins, c.count,
                                c.interval) == kind), key=lambda c: c.when))
    gaps = sum(1 for a, b in zip(tiles, tiles[1:])
               if (b.when - a.when) > 1.5 * a.seconds)
    return {
        'tiles': [(c.data, c.when) for c in tiles],
        'start_hz': newest.start_hz,
        'bin_hz': newest.bin_hz,
        'bins': newest.bins,
        'sweeps': newest.count,
        'interval': newest.interval,
        'start': tiles[0].when,
        'end': tiles[-1].when + tiles[-1].seconds,
        'gaps': gaps,
        'live': live,
    }


class Sweeps(_Ring):
    """The spectrum tiles of a DVR file: signed bytes, each tile with its own
    gain and offset (see the module's notes), or ``dbm=True`` for dBm.

    ``read`` gives a band of them as a waterfall, sweeps by bins, in time
    order. A tile that Sceptre overwrote after the scan is left out and
    counted in ``overwritten``.
    """

    def __init__(self, path, layout=None):
        super().__init__(path)
        self.layout = layout or scan_sweeps(path)
        lay = self.layout
        self.bins, self.sweeps = lay['bins'], lay['sweeps']
        self.bin_hz, self.start_hz = lay['bin_hz'], lay['start_hz']
        self.interval = lay['interval']
        self.tiles = lay['tiles']

    def freqs(self, first=0, stop=None):
        """The frequency of each bin from ``first`` to ``stop``, Hz."""
        stop = self.bins if stop is None else stop
        return self.start_hz + np.arange(first, stop) * self.bin_hz

    def bin_range(self, f_lo=None, f_hi=None):
        """The bin numbers ``(first, stop)`` that cover ``f_lo`` to ``f_hi``."""
        lo = 0 if f_lo is None else int(np.floor((f_lo - self.start_hz) / self.bin_hz))
        hi = self.bins if f_hi is None else int(np.ceil((f_hi - self.start_hz) / self.bin_hz)) + 1
        lo, hi = max(0, lo), min(self.bins, hi)
        if lo >= hi:
            raise SdvrError(f"{f_lo} to {f_hi} Hz is outside the DVR's "
                            f"{self.start_hz:.0f} to "
                            f"{self.start_hz + (self.bins - 1) * self.bin_hz:.0f} Hz.")
        return lo, hi

    def calibration(self, offset):
        """``(gain, offset)`` of the tile whose data is at ``offset``:
        ``byte = gain * dBm + offset``."""
        return struct.unpack_from('<ff', self._raw, offset - PAGE + 8)

    def read(self, f_lo=None, f_hi=None, first=0, stop=None, dbm=False):
        """``(times, freqs, levels)``: the sweeps of tiles ``first`` to
        ``stop`` (all, by default), the bins from ``f_lo`` to ``f_hi`` Hz (all,
        by default). ``times`` is each sweep's time (s since 1970), ``freqs``
        each bin's Hz, ``levels`` one row a sweep: the signed bytes as int8,
        or with ``dbm=True`` float32 dBm, each tile by its own calibration
        (bytes at +-127 are the ends of the scale, not measurements)."""
        lo, hi = self.bin_range(f_lo, f_hi)
        times, rows = [], []
        for offset, when in self.tiles[first:stop]:
            size = self.bins * self.sweeps
            tile = self._raw[offset:offset + size].view(np.int8)
            block = np.array(tile.reshape(self.bins, self.sweeps)[lo:hi].T)
            gain, off = self.calibration(offset)
            if self._stamp(offset) != when:                 # overwritten meanwhile
                self.overwritten += 1
                continue
            if dbm:
                block = (block.astype(np.float32) - np.float32(off)) / np.float32(gain)
            rows.append(block)
            times.append(when + np.arange(self.sweeps) * self.interval)
        if not rows:
            return (np.empty(0), self.freqs(lo, hi),
                    np.empty((0, hi - lo), dtype=np.float32 if dbm else np.int8))
        return np.concatenate(times), self.freqs(lo, hi), np.concatenate(rows)
