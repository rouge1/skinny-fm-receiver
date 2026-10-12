"""Sceptre's spectrum recordings, read as dBm: a sweep DVR (``.sdvr``) and a
spectrum ``.fft``, for the Recordings tab and for playing them without sound.

A sweep DVR is read through :mod:`sceptre_dvr` (its scan, and :class:`Sweeps`,
which decodes each tile by the tile's own gain and offset); a ``.fft`` through
:mod:`sceptre_blue`. Both are opened read-only and memory-mapped, and a read
touches only the tiles (a DVR) or rows (a ``.fft``) it needs, so a 1 GB ring
can be browsed without being read whole. Plain numpy: no Qt, no GNU Radio.
"""

import math
import os

import numpy as np

from . import sceptre_blue, sceptre_dvr

#: Slack, in rows or bins, for float error when a time or frequency lands on
#: an exact row or bin.
_EPS = 1e-9


class SpectraError(Exception):
    """The file cannot be read as a spectrum; the message says why."""


class SpectrumFile:
    """A spectrum recording. Made by :func:`open_spectra`; :meth:`read` gives
    any stretch of it, any band, as dBm. Row ``k`` is at ``k * row_seconds``
    from ``start``, bin ``b`` at ``start_hz + b * bin_hz``."""

    def __init__(self, path, kind, start, rows, row_seconds, bins, start_hz,
                 bin_hz, source):
        self.path = path
        self.kind = kind
        self.start = start
        self.rows = rows
        self.row_seconds = row_seconds
        self.seconds = rows * row_seconds
        self.bins = bins
        self.start_hz = start_hz
        self.bin_hz = bin_hz
        self.stop_hz = start_hz + (bins - 1) * bin_hz
        self._source = source

    def read(self, t0=0.0, t1=None, f_lo=None, f_hi=None, max_rows=None, max_cols=None):
        """-> (times, freqs, db): times (n,) seconds from the file's start (row k is k * row_seconds), freqs
        (m,) Hz, db (n, m) float32 dBm.
        Rows: those k with t0 <= k * row_seconds < t1 (t1 None: to the end). Empty, not an error, outside the
        file. max_rows: if there are more, an even thinning (the first row kept).
        Bins: from the bin f_lo falls in to the one f_hi falls in (None: the whole row), then at most max_cols
        columns, each the *loudest* of the bins it covers (a narrow carrier must not vanish); freqs is each
        column's mean frequency.
        A sweep DVR's dBm comes from each tile's own gain and offset (sceptre_dvr.Sweeps.read(dbm=True)), and a
        stretch reads only the tiles it touches."""
        ks = self._row_numbers(t0, t1, max_rows)
        lo, hi = self._bin_span(f_lo, f_hi)
        if self.kind == 'dvr-sweep':
            ks, db = self._read_dvr(ks, lo, hi)
        else:
            ks, db = self._read_fft(ks, lo, hi)
        freqs = self.start_hz + np.arange(lo, hi) * self.bin_hz
        if max_cols and hi - lo > max_cols:
            db, freqs = _pool(db, freqs, max_cols)
        return ks * self.row_seconds, freqs, db

    def _row_numbers(self, t0, t1, max_rows):
        """The row numbers k in [t0, t1), thinned evenly to ``max_rows``."""
        first = max(0, math.ceil(t0 / self.row_seconds - _EPS))
        stop = self.rows if t1 is None else min(self.rows, math.ceil(t1 / self.row_seconds - _EPS))
        ks = np.arange(first, max(first, stop))
        if max_rows is not None and len(ks) > max_rows:
            ks = ks[np.arange(max_rows) * len(ks) // max_rows]
        return ks

    def _bin_span(self, f_lo, f_hi):
        """The bins ``(lo, hi)``, hi exclusive: from the one ``f_lo`` falls in
        to the one ``f_hi`` falls in, clipped to the file."""
        lo = 0 if f_lo is None else math.floor((f_lo - self.start_hz) / self.bin_hz + _EPS)
        hi = self.bins if f_hi is None else math.floor((f_hi - self.start_hz) / self.bin_hz + _EPS) + 1
        lo = min(max(lo, 0), self.bins)
        return lo, min(max(hi, lo), self.bins)

    def _read_fft(self, ks, lo, hi):
        """The rows ``ks`` and bins ``lo`` to ``hi`` of a ``.fft``: only those
        rows are read from the map."""
        return ks, np.asarray(self._source[ks, lo:hi], dtype=np.float32)

    def _read_dvr(self, ks, lo, hi):
        """The rows ``ks`` and bins ``lo`` to ``hi`` of a sweep DVR, one tile at
        a time, so only the tiles the rows are in are read. A tile Sceptre
        overwrote meanwhile gives no rows: those are left out."""
        sweeps = self._source.sweeps
        kept, rows = [], []
        if hi > lo:
            # Sweeps.read rounds Hz to bins itself: aim at the middle of the
            # first bin, and at half a bin below the last one's centre, which
            # it rounds up to that bin.
            f_lo = self.start_hz + (lo + 0.5) * self.bin_hz
            f_hi = self.start_hz + (hi - 1.5) * self.bin_hz
            for tile in np.unique(ks // sweeps):
                _, _, block = self._source.read(f_lo, f_hi, first=tile, stop=tile + 1, dbm=True)
                if not len(block):
                    continue
                mine = ks[ks // sweeps == tile]
                kept.append(mine)
                rows.append(block[mine - tile * sweeps])
        if not rows:
            return np.empty(0, dtype=np.int64), np.empty((0, hi - lo), dtype=np.float32)
        return np.concatenate(kept), np.concatenate(rows)


def _pool(db, freqs, cols):
    """Pool the columns of ``db`` into ``cols``: each the loudest (max) of the
    bins it covers, and its mean frequency."""
    m = len(freqs)
    starts = np.arange(cols) * m // cols
    counts = np.diff(np.append(starts, m))
    return np.maximum.reduceat(db, starts, axis=1), np.add.reduceat(freqs, starts) / counts


def _open_dvr(path):
    layout = sceptre_dvr.scan_sweeps(path)
    return SpectrumFile(path, 'dvr-sweep', layout['start'],
                        layout['sweeps'] * len(layout['tiles']), layout['interval'],
                        layout['bins'], layout['start_hz'], layout['bin_hz'],
                        sceptre_dvr.Sweeps(path, layout))


def _open_fft(path):
    blue = sceptre_blue.read(path)
    if blue.fmt != 'SF' or blue.data.ndim != 2:
        raise SpectraError(f"{os.path.basename(path)} holds {blue.fmt!r}, not a spectrum "
                           "(rows of float32 dBm, format SF).")
    rows, bins = blue.data.shape
    return SpectrumFile(path, 'fft', blue.start, rows, blue.ydelta, bins,
                        blue.xstart, blue.xdelta, blue.data)


_OPENERS = {'.sdvr': _open_dvr, '.fft': _open_fft}


def open_spectra(path):
    """The spectrum recording at ``path``: a ``.sdvr`` sweep DVR or a ``.fft``.
    Raises :class:`SpectraError` for a missing file, another kind of file, a
    DVR with no spectrum tiles (IQ only), or a BLUE file that is not float32
    rows. Nothing is written."""
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in _OPENERS:
        raise SpectraError(f"{name} is not a spectrum file (a .sdvr or .fft).")
    if not os.path.isfile(path):
        raise SpectraError(f"{name} is missing.")
    try:
        return _OPENERS[ext](path)
    except SpectraError:
        raise
    except sceptre_dvr.SdvrError as err:
        raise SpectraError(str(err)) from err
    except Exception as err:    # a damaged file can fail anywhere in the readers
        raise SpectraError(f"{name} cannot be read as a spectrum ({err}).") from err
