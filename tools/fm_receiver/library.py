"""The recordings in the folder, for the Recordings tab.

**One press of Record is one recording** (:class:`Recording`), however many
files it made. Each file that can be played is a :class:`Track`: the WAV,
the channel IQ and the band IQ, and each ``-partN`` a retune started. They
are grouped by name - ``fm-98.70MHz-20260922-181500`` up to the kind -
and ``<name>-recording.json`` (``recording.RecordingInfo``) adds the RDS the
station sent while it was recorded. Recordings made before that file
existed have none, and their kinds could be named a second apart: those
within :data:`MERGE_S` of each other, on the same station, are one.

**:func:`render`** draws any stretch of a track as a waterfall, in the
spectrum views' own dB: the whole track, small, for the Recordings tab's
mini map, and the stretch the big waterfall shows, from the file. It
samples the file - a few FFT frames for each row - rather than reading it
all, so a gigabyte of band IQ takes as long as a minute of WAV.
"""

import datetime as _dt
import json
import os
import re
import struct

import numpy as np  # type: ignore

from .radios import read_iq_metadata
from .recording import RecordingInfo

#: fm-98.70MHz-20260922-181500[-2]-iq-band[-2][-part3].cfile - the ``-2``
#: before the kind is a second recording in the same second, the one after
#: it the older way of naming that.
NAME = re.compile(r'^fm-(?P<mhz>\d+\.\d+)MHz-(?P<stamp>\d{8}-\d{6})(?:-(?P<dup>\d+))?'
                  r'-(?P<kind>audio|iq-channel|iq-band)(?:-(?P<dup2>\d+))?'
                  r'(?:-part(?P<part>\d+))?\.(?P<ext>wav|cfile)$')
INFO = re.compile(r'^(?P<base>fm-(?P<mhz>\d+\.\d+)MHz-(?P<stamp>\d{8}-\d{6})'
                  r'(?:-(?P<dup>\d+))?)' + re.escape(RecordingInfo.SUFFIX) + '$')
#: Kinds named this close together, on one station, are one recording.
MERGE_S = 2.0
KIND_ORDER = ('iq-band', 'iq-channel', 'audio')
KIND_TEXT = {'audio': 'WAV', 'iq-channel': 'IQ channel', 'iq-band': 'IQ band'}


def clock(seconds):
    """1:05, or 1:02:05."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def size_text(n):
    for scale, unit in ((1e9, 'GB'), (1e6, 'MB'), (1e3, 'kB')):
        if n >= scale:
            return f"{n / scale:.3g} {unit}"
    return f"{n} bytes"


# ------------------------------------------------------------------ WAV

def wav_layout(path):
    """Where a WAV file's samples are: (data offset, frames, channels, numpy
    dtype, rate). PCM 16-bit and float 32-bit. A recording cut short (the
    app killed) has sizes of zero in its header; the file's own length
    stands in for them."""
    with open(path, 'rb') as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            raise ValueError(f"{os.path.basename(path)} is not a WAV file")
        fmt = None
        while True:
            chunk = fh.read(8)
            if len(chunk) < 8:
                raise ValueError(f"{os.path.basename(path)} has no audio in it")
            cid, size = chunk[:4], struct.unpack('<I', chunk[4:])[0]
            if cid == b'fmt ':
                fmt = struct.unpack('<HHIIHH', fh.read(16))
                fh.seek(size - 16 + (size & 1), 1)
            elif cid == b'data':
                offset = fh.tell()
                break
            else:
                fh.seek(size + (size & 1), 1)
    if fmt is None:
        raise ValueError(f"{os.path.basename(path)} has no format chunk")
    code, channels, rate, _, align, bits = fmt
    if code == 1 and bits == 16:
        dtype = np.dtype('<i2')
    elif code == 3 and bits == 32:
        dtype = np.dtype('<f4')
    else:
        raise ValueError(f"{os.path.basename(path)}: only 16-bit PCM or 32-bit "
                         "float WAV can be played")
    available = os.path.getsize(path) - offset
    if size == 0 or size == 0xFFFFFFFF or size > available:
        size = available
    return offset, size // align, channels, dtype, rate


def wav_frames(path):
    """The samples, memory-mapped: (frames, channels)."""
    offset, frames, channels, dtype, _ = wav_layout(path)
    if frames <= 0:
        return np.zeros((0, channels), dtype=dtype)
    return np.memmap(path, dtype=dtype, mode='r', offset=offset,
                     shape=(frames, channels))


# ------------------------------------------------------------------ model

class Track:
    """One playable file of a recording."""

    def __init__(self, path, kind, part):
        self.path = path
        self.kind = kind
        self.part = part
        self.bytes = os.path.getsize(path)
        self.seconds = 0.0
        self.rate = None
        self.center_hz = None
        self.station_hz = None
        self.error = None
        try:
            if kind == 'audio':
                _, frames, _, _, rate = wav_layout(path)
                self.rate = float(rate)
                self.seconds = frames / self.rate
            else:
                meta = read_iq_metadata(path)
                self.rate = meta['rate']
                self.center_hz = meta['center_hz']
                self.station_hz = meta['station_hz']
                self.seconds = self.bytes / 8 / self.rate
        except Exception as exc:
            self.error = str(exc)

    @property
    def is_iq(self):
        return self.kind != 'audio'

    def sidecars(self):
        """The files that describe this one."""
        base = os.path.splitext(self.path)[0]
        return [base + ext for ext in ('.json', '.sigmf-meta')
                if self.is_iq and os.path.exists(base + ext)]

    def label(self):
        what = {'audio': "Audio (WAV)", 'iq-channel': "IQ - channel",
                'iq-band': "IQ - whole band"}[self.kind]
        if self.kind == 'iq-band' and self.rate:
            what += f", {self.rate / 1e6:g} MS/s"
        if self.part > 1:
            what += f", part {self.part}"
            if self.station_hz:
                what += f" at {self.station_hz / 1e6:.2f} MHz"
        text = f"{what} - {clock(self.seconds)}"
        return text + (" (unreadable)" if self.error else "")


class Recording:
    """One press of Record: its tracks, and what it was of."""

    def __init__(self, key, station_hz, started):
        self.key = key                    # the path every file starts with
        self.station_hz = station_hz
        self.started = started            # local time, from the name
        self.tracks = []
        self.info_path = None
        self.info = {}

    @property
    def name(self):
        return self.info.get('station_name') or ''

    @property
    def callsign(self):
        return self.info.get('callsign') or ''

    @property
    def seconds(self):
        """The longest kind: the WAV, or a kind's parts end to end."""
        per_kind = {}
        for track in self.tracks:
            per_kind[track.kind] = per_kind.get(track.kind, 0.0) + track.seconds
        return max(per_kind.values(), default=0.0)

    @property
    def bytes(self):
        return sum(track.bytes for track in self.tracks)

    def kinds(self):
        return [k for k in KIND_ORDER if any(t.kind == k for t in self.tracks)]

    def kinds_text(self):
        parts = []
        for kind in reversed(KIND_ORDER):
            count = sum(1 for t in self.tracks if t.kind == kind)
            if count:
                parts.append(KIND_TEXT[kind] + (f" ({count} parts)" if count > 1 else ""))
        return " + ".join(parts)

    def best_track(self):
        """What plays unless another is chosen: the IQ - the whole band
        first, it holds the most - else the WAV."""
        for kind in KIND_ORDER:
            for track in self.tracks:
                if track.kind == kind and not track.error:
                    return track
        return self.tracks[0] if self.tracks else None

    def files(self):
        """Every file the recording is made of."""
        out = []
        for track in self.tracks:
            out.append(track.path)
            out.extend(track.sidecars())
        if self.info_path:
            out.append(self.info_path)
        return out

    def rds_at(self, seconds):
        """The RadioText and Now Playing in force ``seconds`` in, from the
        recording's log: ('', '') where there is none."""
        text = playing = ''
        for entry in self.info.get('timeline') or []:
            if entry.get('t', 0) > seconds:
                break
            text, playing = entry.get('radiotext', ''), entry.get('nowplaying', '')
        return text, playing


def _stamp_time(stamp):
    return _dt.datetime.strptime(stamp, '%Y%m%d-%H%M%S')


def scan(folder):
    """The recordings in ``folder``, newest first."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    groups, infos = {}, {}
    for name in names:
        m = NAME.match(name)
        if m:
            key = (m['mhz'], m['stamp'], m['dup'] or m['dup2'] or '')
            groups.setdefault(key, []).append((name, m['kind'], int(m['part'] or 1)))
            continue
        m = INFO.match(name)
        if m:
            infos[(m['mhz'], m['stamp'], m['dup'] or '')] = name
    # Kinds of one older recording named a second or two apart.
    keys = sorted(groups, key=lambda k: (k[0], k[1], k[2]))
    merged = {}
    for key in keys:
        into = None
        for other in merged:
            # Two with descriptions of their own were two presses of Record.
            if other[0] == key[0] and other[2] == key[2] == '' and \
                    abs((_stamp_time(other[1]) - _stamp_time(key[1])).total_seconds()) \
                    <= MERGE_S and not (other in infos and key in infos) and \
                    not ({k for _, k, _ in merged[other]} & {k for _, k, _ in groups[key]}):
                into = other
                break
        if into is None:
            merged[key] = list(groups[key])
        else:
            merged[into].extend(groups[key])
    out = []
    for key, files in merged.items():
        mhz, stamp, dup = key
        base = f"fm-{mhz}MHz-{stamp}" + (f"-{dup}" if dup else "")
        rec = Recording(os.path.join(folder, base), float(mhz) * 1e6, _stamp_time(stamp))
        for name, kind, part in sorted(files, key=lambda f: (KIND_ORDER.index(f[1]), f[2])):
            try:
                rec.tracks.append(Track(os.path.join(folder, name), kind, part))
            except OSError:
                pass                      # gone while we looked
        if key in infos:
            rec.info_path = os.path.join(folder, infos[key])
            try:
                with open(rec.info_path) as fh:
                    rec.info = json.load(fh)
                if rec.info.get('station_hz'):
                    rec.station_hz = float(rec.info['station_hz'])
            except Exception:
                rec.info = {}
        if rec.tracks:
            out.append(rec)
    out.sort(key=lambda r: (r.started, r.key), reverse=True)
    return out


def learn(rec, **fields):
    """Keep what playing ``rec`` back decoded (``station_name``, ``pi``,
    ``callsign``) in its ``-recording.json`` - made now for a recording
    that has none."""
    path = rec.info_path or rec.key + RecordingInfo.SUFFIX
    doc = dict(rec.info) if rec.info else {
        'format': 'fm-receiver recording', 'version': 1,
        'station_hz': float(rec.station_hz),
        'files': [os.path.basename(t.path) for t in rec.tracks],
        'tuned': [{'t': 0.0, 'station_hz': float(rec.station_hz)}], 'timeline': []}
    doc.update(fields)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)
    rec.info, rec.info_path = doc, path


def delete(rec):
    """Remove every file of ``rec``. Returns the ones that would not go."""
    failed = []
    for path in rec.files():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed.append(f"{os.path.basename(path)}: {exc.strerror}")
    return failed


# ----------------------------------------------------------------- render

#: The spectrum views' FFT sizes (``dsp.ReceiveChain.RF_FFT``,
#: ``dsp.WavChain.FFT``): what is drawn here has their dB, bin for bin.
RF_FFT = 4096
AUDIO_FFT = 2048
#: The most FFTs worked at once: 256 of 4096 complex64 is 8 MB.
CHUNK = 256


def blackman_harris(n):
    """The spectrum taps' window (``dsp.spectrum_tap``), in numpy."""
    k = 2 * np.pi * np.arange(n) / (n - 1)
    return (0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k)
            - 0.01168 * np.cos(3 * k))


def extent(track):
    """The (low, high) frequency, in Hz, a track covers: the band an IQ
    file was recorded over, or 0 to half the rate of a WAV."""
    if track.kind == 'audio':
        return 0.0, track.rate / 2
    centre = track.center_hz or 0.0
    return centre - track.rate / 2, centre + track.rate / 2


def bin_freqs(track):
    """The frequency of each of :func:`render`'s native columns - the same
    sum the live views make from their taps, so a row drawn from the file
    and a row from the radio are on one scale."""
    if track.kind == 'audio':
        n = AUDIO_FFT
        return np.arange(n // 2 + 1) * track.rate / n
    n = RF_FFT
    return (track.center_hz or 0.0) + (np.arange(n) - n / 2) * track.rate / n


def _pool(power, cols):
    """``power`` (rows, bins) as ``cols`` columns, each the most of the bins
    it covers: a narrow carrier must not vanish when the band is squeezed."""
    bins = power.shape[1]
    if cols is None or cols >= bins:
        return power
    edges = (np.arange(cols) * bins / cols).astype(np.intp)
    return np.maximum.reduceat(power, edges, axis=1)


def render(track, t0, t1, rows, cols=None, frames=2):
    """``track`` from ``t0`` to ``t1`` seconds as a waterfall: a (rows,
    columns) float32 array in dB and the frequency of each column in Hz.
    **Row 0 is the earliest** slot, and a slot outside the file is NaN.

    **In the spectrum views' own dB** (dBFS for IQ): the same window, FFT
    size and scaling as their taps, so what is drawn here can be coloured
    by a view's Ref level and Range and mean what the view does, and a row
    drawn from the file sits beside rows the radio makes. Each row is the
    mean power of ``frames`` FFTs spread over its slot. ``cols`` None gives
    the views' own bins (:func:`bin_freqs`); a number squeezes them into
    that many columns, each the most of its bins. The columns cover the
    whole band (:func:`extent`); a view's zoom is its own business.

    A WAV's columns are linear in frequency, 0 to half its rate, as the
    audio spectrum's are; its channels are mixed to one. It reads a few
    frames of the file for each row rather than all of it, so a gigabyte of
    band IQ takes as long as a minute of WAV."""
    rows = max(1, int(rows))
    if track.kind == 'audio':
        data = wav_frames(track.path)
        nfft = AUDIO_FFT
        scale = 1.0 / 32768.0 if data.dtype == np.int16 else 1.0
        win = blackman_harris(nfft)
        rate = track.rate
    else:
        # An empty file cannot be mapped: it is a recording of nothing.
        data = (np.memmap(track.path, dtype=np.complex64, mode='r')
                if os.path.getsize(track.path) >= 8 else np.zeros(0, np.complex64))
        nfft = RF_FFT
        scale = 1.0
        win = blackman_harris(nfft).astype(np.float32)
        rate = track.rate
    total = len(data)
    freqs = bin_freqs(track)
    norm = float(np.sum(win)) ** 2
    nbins = len(freqs)
    width = nbins if cols is None or cols >= nbins else int(cols)
    out = np.full((rows, width), np.nan, dtype=np.float32)
    edges = np.linspace(t0, t1, rows + 1) * rate
    lo = np.floor(edges[:-1]).astype(np.int64)
    hi = np.floor(edges[1:]).astype(np.int64)
    inside = np.flatnonzero((hi > 0) & (lo < total))
    frames = max(1, int(frames))
    for first in range(0, len(inside), max(1, CHUNK // frames)):
        chunk = inside[first:first + max(1, CHUNK // frames)]
        segs = []
        for r in chunk:
            a = max(int(lo[r]), 0)
            b = max(a, min(int(hi[r]), total) - nfft)
            for start in np.linspace(a, b, frames).astype(np.int64):
                start = min(start, max(total - nfft, 0))     # a whole window, if the file has one
                seg = np.asarray(data[start:start + nfft])
                if len(seg) < nfft:                    # the end of the file
                    pad = np.zeros((nfft - len(seg),) + seg.shape[1:], dtype=seg.dtype)
                    seg = np.concatenate([seg, pad])
                segs.append(seg.mean(axis=1) * scale if seg.ndim > 1 else seg)
        segs = np.stack(segs) * win
        if track.kind == 'audio':
            power = np.abs(np.fft.rfft(segs, axis=1)) ** 2 / norm
        else:
            power = np.fft.fftshift(np.abs(np.fft.fft(segs, axis=1)) ** 2, axes=1) / norm
        power = power.reshape(len(chunk), frames, nbins).mean(axis=1)
        out[chunk] = 10 * np.log10(_pool(power, width) + 1e-20)
    if width != nbins:
        groups = (np.arange(width) * nbins / width).astype(np.intp)
        ends = np.append(groups[1:], nbins)
        freqs = np.array([freqs[a:b].mean() for a, b in zip(groups, ends)])
    return out, freqs
