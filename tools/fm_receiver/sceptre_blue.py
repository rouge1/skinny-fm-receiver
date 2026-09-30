"""BLUE files (X-Midas), as Sceptre writes them: ``.cdif`` IQ and ``.fft``.

Sceptre's *Recordings* folder holds what it exports, in BLUE files, with
``sceptre.db`` listing them. Layout, as worked out from the files (and the
public BLUE description), little-endian (``EEEI``):

- A 512-byte header: ``BLUE`` at 0; the header and data byte order at 4 and
  8; at 24 the extended header's start (in 512-byte blocks) and at 28 its
  length; at 32 and 40 the data's start and size in bytes (doubles); at 48 the
  type (1000 + the dimensions: 1001 a series, 2001 rows of ``subsize``
  values) and at 52 the format, two letters - the first ``C`` for complex or
  ``S`` for scalar, the second ``F`` float32, ``D`` float64, ``I`` int16,
  ``L`` int32, ``B`` int8; at 56 the time code (seconds since 1950); and the
  first axis at 256: start, step (so the sample rate is 1 / step), units,
  then at 276 ``subsize``.
- The data from its start, then, from the extended header, keyword records:
  a length (int32, the whole record), ``lext`` (int16), the tag's length
  (int8), a type letter, the value (``lkey - lext`` bytes from byte 8), then
  the tag.

Sceptre's keywords include ``SAMPLE_RATE``, ``RF_FREQ``, ``DATA_BANDWIDTH``,
``DATA_GAIN`` and ``TIME_EPOCH``. Plain numpy; nothing is written.
"""

import datetime
import os
import struct

import numpy as np

BLOCK = 512
#: Second letter of the format: numpy type and bytes.
_KINDS = {'F': ('<f4', 4), 'D': ('<f8', 8), 'I': ('<i2', 2), 'L': ('<i4', 4),
          'B': ('i1', 1)}
_KEYWORD_TYPES = {'D': '<d', 'F': '<f', 'L': '<i', 'I': '<h', 'X': '<q', 'B': 'b'}
#: BLUE time codes count seconds from 1950-01-01.
_EPOCH_1950 = datetime.datetime(1950, 1, 1, tzinfo=datetime.timezone.utc)


class BlueError(Exception):
    """The file is not a BLUE file this can read; the message says why."""


class Blue:
    """One BLUE file: ``.fmt``, ``.rate`` (samples, or rows, a second),
    ``.start`` (seconds since 1970 of the first sample or row), ``.keywords``
    (a dict), and ``.data`` - a numpy memmap, complex64 for a ``C`` format and
    2-D when the type says so. For rows, ``.xstart`` and ``.xdelta`` are the
    first bin and the bin step along a row (Hz, in a spectrum)."""

    def __init__(self, path):
        self.path = path
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            head = fh.read(BLOCK)
        if len(head) < BLOCK or head[:4] != b'BLUE':
            raise BlueError(f"{os.path.basename(path)} is not a BLUE file.")
        if head[4:8] != b'EEEI' or head[8:12] != b'EEEI':
            raise BlueError(f"{os.path.basename(path)} is big-endian BLUE; only "
                            "little-endian (EEEI) is read.")
        ext_start, ext_size = struct.unpack_from('<ii', head, 24)
        data_start, data_size = struct.unpack_from('<dd', head, 32)
        self.type = struct.unpack_from('<i', head, 48)[0]
        self.fmt = head[52:54].decode('ascii', 'replace')
        self.timecode = struct.unpack_from('<d', head, 56)[0]
        xstart, xdelta = struct.unpack_from('<dd', head, 256)
        self.xstart, self.xdelta = xstart, xdelta
        self.subsize = struct.unpack_from('<i', head, 276)[0] if self.type >= 2000 else 1
        # A series' axis is time; in rows (2001) the first axis runs along a row
        # (a spectrum's frequency, Hz) and the second, at 280, is time.
        self.ystart, self.ydelta = ((struct.unpack_from('<dd', head, 280))
                                    if self.type >= 2000 else (0.0, 0.0))
        step = self.ydelta if self.type >= 2000 else xdelta
        self.rate = 1.0 / step if step else None
        if self.fmt[0] not in 'CS' or self.fmt[1] not in _KINDS:
            raise BlueError(f"{os.path.basename(path)} holds format {self.fmt!r}; "
                            "read: CF CD CI CL CB and the scalar SF SD SI SL SB.")
        kind, width = _KINDS[self.fmt[1]]
        complex_ = self.fmt[0] == 'C'
        per_row = max(self.subsize, 1)
        row_bytes = width * (2 if complex_ else 1) * per_row
        rows = int(min(data_size, size - data_start) // row_bytes)
        raw = np.memmap(path, dtype=kind, mode='r', offset=int(data_start),
                        shape=(rows * per_row * (2 if complex_ else 1),))
        if complex_ and kind == '<f4':
            self.data = raw.view('<c8')                 # complex64, straight from the file
        elif complex_:
            self.data = raw.reshape(-1, 2)              # other widths: (I, Q) pairs
        else:
            self.data = raw.reshape(rows, per_row) if per_row > 1 else raw
        self.keywords = self._keywords(path, ext_start * BLOCK, ext_size, size)

    @staticmethod
    def _keywords(path, start, length, size):
        out = {}
        if start <= 0 or start >= size:
            return out
        with open(path, 'rb') as fh:
            fh.seek(start)
            block = fh.read(min(length, size - start) if length > 0 else size - start)
        o = 0
        while o + 8 <= len(block):
            lkey, lext, ltag, kind = struct.unpack_from('<ihbc', block, o)
            if lkey < 8 or o + lkey > len(block):
                break
            vlen = lkey - lext
            value = block[o + 8:o + 8 + vlen]
            tag = block[o + 8 + vlen:o + 8 + vlen + ltag].decode('ascii', 'replace')
            k = kind.decode('ascii', 'replace')
            if k in _KEYWORD_TYPES and len(value) >= struct.calcsize(_KEYWORD_TYPES[k]):
                out[tag] = struct.unpack_from(_KEYWORD_TYPES[k], value)[0]
            elif k == 'A':
                out[tag] = value.split(b'\0')[0].decode('ascii', 'replace')
            o += lkey
        return out

    @property
    def start(self):
        """Seconds since 1970 of the first sample (``TIME_EPOCH`` plus the
        time axis start when Sceptre wrote it, else the header's time code)."""
        epoch = self.keywords.get('TIME_EPOCH')
        if epoch:
            try:
                base = datetime.datetime.fromisoformat(
                    epoch.rstrip('Z')[:26] + '+00:00').timestamp()
                return base + self._t0
            except ValueError:
                pass
        return (_EPOCH_1950 + datetime.timedelta(seconds=self.timecode)).timestamp() \
            + self._t0

    @property
    def _t0(self):
        return self.ystart if self.type >= 2000 else self.xstart

    @property
    def center_hz(self):
        return self.keywords.get('RF_FREQ') or self.keywords.get('COL_RF')

    @property
    def samples(self):
        return len(self.data)

    def iq(self, start=0, stop=None):
        """Samples ``start`` to ``stop`` as complex64 (a ``C`` format only)."""
        if self.fmt[0] != 'C':
            raise BlueError(f"{os.path.basename(self.path)} is not complex ({self.fmt}).")
        block = np.asarray(self.data[start:stop])
        if block.dtype == np.complex64:
            return block
        return (block[:, 0].astype(np.float32) + 1j * block[:, 1].astype(np.float32)
                ).astype(np.complex64)


def read(path):
    """Open a BLUE file."""
    return Blue(path)
