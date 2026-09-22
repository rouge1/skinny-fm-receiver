"""Recording: IQ to disk, and the station's audio to a WAV file.

**IQ** is written by GNU Radio's own ``file_sink``, in C++, because the
wideband kind runs at the radio's full rate (2.5 MS/s is 20 MB/s). The sink
is always in the flowgraph, made closed; Record opens a file on it and Stop
closes it again, so recording never rebuilds the flowgraph - a rebuild
restarts the radio and glitches the audio.

Two kinds:

- **band** - everything the radio receives, at its rate, centred on its LO.
  Big, and it holds every station in the band: play it back and tune
  around in it.
- **channel** - just the tuned station, filtered and decimated to 500 kS/s
  complex with the station at 0 Hz. A quarter to a fortieth of the size.

Each recording is ``<name>.cfile`` (complex float32, little-endian) with two
descriptions beside it: ``<name>.sigmf-meta`` (SigMF 1.0, naming the
``.cfile`` as its dataset) for other tools, and ``<name>.json`` in the RF
bench toolkit's capture format (``rate``, ``offset_hz``, ``station_hz``),
which its ``scripts/test_rds_core.py`` reads. This app plays either back.

**A recording has one centre frequency.** Retuning while recording IQ
closes the file and carries on in a new one (``-part2`` and on), each with
its own description, rather than writing a file whose metadata is wrong for
part of it. The switch lands within one buffer of the retune.

**Audio** is the demodulated stereo pair after de-emphasis and before the
volume control, so mute and volume do not touch what is recorded: 16-bit
PCM WAV at 48 kHz. It is written by a thread of its own - the flowgraph
thread only copies the samples onto a queue - so a slow disk never stalls
the audio.

**One press of Record is one recording.** Its files share one name up to
the kind (:func:`session_base`), and ``<name>-recording.json``
(:class:`RecordingInfo`) describes it: the files, when, and what the
station sent over RDS while it was recorded. The Recordings tab lists
recordings by it (``library.py``).
"""

import datetime as _dt
import json
import os
import queue
import threading
import time
import wave

import numpy as np  # type: ignore

from . import __version__


def timestamp():
    return _dt.datetime.now().strftime('%Y%m%d-%H%M%S')


def recording_base(directory, station_hz, kind):
    """``<dir>/fm-98.70MHz-20260921-181500-<kind>``, unique on disk."""
    base = os.path.join(directory,
                        f"fm-{station_hz / 1e6:.2f}MHz-{timestamp()}-{kind}")
    candidate, n = base, 2
    while any(os.path.exists(candidate + ext)
              for ext in ('.cfile', '.wav', '.sigmf-meta')):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def session_base(directory, station_hz, stamp=None):
    """``<dir>/fm-98.70MHz-20260921-181500``: what every file of one press
    of Record is named from (``-audio.wav``, ``-iq-band.cfile``...), unique
    on disk - a second recording in the same second is ``...-181500-2``."""
    base = os.path.join(directory,
                        f"fm-{station_hz / 1e6:.2f}MHz-{stamp or timestamp()}")
    try:
        names = os.listdir(directory)
    except OSError:
        names = []

    def taken(candidate):
        # Its own files go on with a kind; ``<base>-2-...`` is the next one's.
        start = os.path.basename(candidate) + '-'
        return any(name.startswith(start) and not name[len(start):][:1].isdigit()
                   for name in names)

    candidate, n = base, 2
    while taken(candidate):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


#: How long a station name must hold before it is believed (see
#: :class:`RecordingInfo`).
NAME_STEADY_S = 8.0


def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def _write_json(path, doc):
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


class RecordingInfo:
    """``<name>-recording.json``: what one press of Record made.

    The files, when it started and how long it ran, where it was tuned
    (with the time of every retune), and what the station sent over RDS:
    its name and PI, and each RadioText and Now Playing with its time from
    the start - so a WAV, which carries no RDS of its own, shows them as it
    plays. Written at the start, when the RDS says something new, and at
    the end, each time by an atomic replace.

    RadioText arrives a segment at a time and can be half-filled; a text
    goes into the log once it has held still for :attr:`STEADY_S`. The
    name is the decoder's most common PS, and a station that scrolls words
    through its PS has none for a while - each fragment in turn is the most
    common: a name is kept once it has held for :data:`NAME_STEADY_S`.
    """

    SUFFIX = '-recording.json'
    STEADY_S = 2.0

    def __init__(self, session, station_hz, radio_name, kinds):
        self.path = session + self.SUFFIX
        self._t0 = time.monotonic()
        self._pending = None
        self._name = ('', 0.0)
        self.doc = {
            'format': 'fm-receiver recording', 'version': 1,
            'recorder': f'fm-receiver {__version__}',
            'station_hz': float(station_hz), 'radio': radio_name,
            'started': _utc_now(), 'stopped': None, 'seconds': 0.0,
            'kinds': list(kinds), 'files': [],
            'station_name': '', 'pi': None, 'callsign': None, 'pty': None,
            'tuned': [{'t': 0.0, 'station_hz': float(station_hz)}],
            'timeline': [],
        }
        self.save()

    def elapsed(self):
        return round(time.monotonic() - self._t0, 2)

    def save(self):
        _write_json(self.path, self.doc)

    def note_tune(self, station_hz):
        tuned = self.doc['tuned']
        if abs(tuned[-1]['station_hz'] - station_hz) >= 1:
            tuned.append({'t': self.elapsed(), 'station_hz': float(station_hz)})
            self._pending = None
            self.save()

    def note_rds(self, snap):
        """The window's latest RDS snapshot: keep what is new."""
        doc, changed = self.doc, False
        if len(doc['tuned']) == 1:             # still on the recorded station
            name = snap.get('station_short') or snap.get('station_name') or ''
            if name != self._name[0]:
                self._name = (name, self.elapsed())
            if self.elapsed() - self._name[1] < NAME_STEADY_S:
                name = ''
            for key, value in (('station_name', name), ('pi', snap.get('pi_hex')),
                               ('callsign', snap.get('callsign_confirmed')
                                or snap.get('callsign')),
                               ('pty', snap.get('pty'))):
                if value and doc.get(key) != value:
                    doc[key] = value
                    changed = True
        title, artist = snap.get('title'), snap.get('artist')
        now = {'radiotext': (snap.get('radiotext') or '').strip(),
               'nowplaying': ' - '.join(x for x in (artist, title) if x)}
        last = doc['timeline'][-1] if doc['timeline'] else {}
        if any(now.values()) and any(now[k] != last.get(k, '') for k in now):
            if self._pending is None or self._pending[1] != now:
                self._pending = (self.elapsed(), now)
            elif self.elapsed() - self._pending[0] >= self.STEADY_S:
                doc['timeline'].append({'t': self._pending[0], **now})
                self._pending = None
                changed = True
        if changed:
            self.save()

    def finish(self, paths):
        self.doc['files'] = [os.path.basename(p) for p in paths]
        self.doc['seconds'] = self.elapsed()
        self.doc['stopped'] = _utc_now()
        self.save()


def write_iq_metadata(base, rate, center_hz, station_hz, kind, radio_name,
                      samples=None):
    """The ``.sigmf-meta`` and ``.json`` descriptions of ``base.cfile``."""
    now = _utc_now()
    what = ('the tuned FM channel, station at 0 Hz' if kind == 'channel'
            else 'the whole band the radio received')
    description = f"FM receiver IQ recording: {what}."
    if samples is not None:
        description += f" {samples} samples, {samples / rate:.1f} s."
    sigmf = {
        'global': {
            'core:datatype': 'cf32_le',
            'core:sample_rate': float(rate),
            'core:version': '1.0.0',
            'core:dataset': os.path.basename(base) + '.cfile',
            'core:recorder': f'fm-receiver {__version__}',
            'core:hw': radio_name,
            'core:description': description,
            # Not a registered SigMF extension - read back by this app only.
            'fmrx:station_frequency': float(station_hz),
            'fmrx:kind': kind,
        },
        'captures': [{'core:sample_start': 0,
                      'core:frequency': float(center_hz),
                      'core:datetime': now}],
        'annotations': [],
    }
    toolkit = {'rate': float(rate), 'offset_hz': float(station_hz - center_hz),
               'station_hz': float(station_hz), 'center_hz': float(center_hz),
               'kind': kind, 'radio': radio_name, 'recorded': now}
    if samples is not None:
        toolkit['samples'] = int(samples)
    for path, doc in ((base + '.sigmf-meta', sigmf), (base + '.json', toolkit)):
        _write_json(path, doc)


class IqRecording:
    """One IQ recording on an always-connected, normally closed file_sink."""

    ITEM = 8                                    # complex float32

    def __init__(self, sink, kind, directory, rate, center_hz, station_hz,
                 radio_name, base=None):
        """``base`` names the file (less ``.cfile``); by default it is made
        from the station, the time and the kind."""
        self.sink = sink
        self.kind = kind
        self.directory = directory
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.station_hz = float(station_hz)
        self.radio_name = radio_name
        self.paths = []
        self.base = None
        self._part = 1
        self._first = base

    def start(self):
        os.makedirs(self.directory, exist_ok=True)
        if self.base is None:
            self._first = self._first or recording_base(
                self.directory, self.station_hz, 'iq-' + self.kind)
            self.base = self._first
        else:
            self.base = f"{self._first}-part{self._part}"
        write_iq_metadata(self.base, self.rate, self.center_hz,
                          self.station_hz, self.kind, self.radio_name)
        if not self.sink.open(self.base + '.cfile'):
            raise OSError(f"could not open {self.base}.cfile for writing")
        self.paths.append(self.base + '.cfile')
        return self.base + '.cfile'

    def _close_part(self):
        self.sink.close()
        try:
            samples = os.path.getsize(self.base + '.cfile') // self.ITEM
        except OSError:
            samples = None
        write_iq_metadata(self.base, self.rate, self.center_hz,
                          self.station_hz, self.kind, self.radio_name, samples)

    def retuned(self, center_hz, station_hz):
        """The radio or the channel moved: finish this file, start the next.
        A band recording goes on in the same file while only the channel
        moves inside it - its samples are the same band either way."""
        if abs(center_hz - self.center_hz) < 1 and (
                self.kind == 'band' or abs(station_hz - self.station_hz) < 1):
            return None
        self._close_part()
        self.center_hz = float(center_hz)
        self.station_hz = float(station_hz)
        self._part += 1
        return self.start()

    def stop(self):
        self._close_part()
        return self.paths

    def bytes_written(self):
        total = 0
        for path in self.paths:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total


class WavWriter:
    """Stereo 16-bit WAV, written by its own thread from a queue."""

    def __init__(self, path, rate=48000):
        self.path = path
        self.rate = int(rate)
        self.frames = 0
        self.error = None
        self._queue = queue.Queue(maxsize=512)
        self._dropped = 0
        self._wave = wave.open(path, 'wb')
        self._wave.setnchannels(2)
        self._wave.setsampwidth(2)
        self._wave.setframerate(self.rate)
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='wav-writer')
        self._thread.start()

    def put(self, left, right):
        """Called from the flowgraph thread: copy and hand over, never wait."""
        if self.error is not None:
            return
        try:
            self._queue.put_nowait(np.stack([left, right], axis=1).copy())
        except queue.Full:
            self._dropped += len(left)

    def _run(self):
        while True:
            chunk = self._queue.get()
            if chunk is None:
                break
            if self.error is not None:
                continue
            try:
                pcm = np.clip(chunk, -1.0, 1.0)
                pcm = (pcm * 32767.0).astype('<i2')
                self._wave.writeframes(pcm.tobytes())
                self.frames += len(chunk)
            except Exception as exc:          # disk full, unplugged...
                self.error = exc

    def close(self):
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        try:
            self._wave.close()
        except Exception as exc:
            self.error = self.error or exc
        return self.path

    @property
    def seconds(self):
        return self.frames / self.rate

    @property
    def dropped(self):
        return self._dropped
