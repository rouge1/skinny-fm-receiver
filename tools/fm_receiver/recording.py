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
- **channel** - just the tuned station, filtered and decimated to 250 kS/s
  complex with the station at 0 Hz. An eighth to an eightieth of the size.

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
"""

import datetime as _dt
import json
import os
import queue
import threading
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


def write_iq_metadata(base, rate, center_hz, station_hz, kind, radio_name,
                      samples=None):
    """The ``.sigmf-meta`` and ``.json`` descriptions of ``base.cfile``."""
    now = _dt.datetime.now(_dt.timezone.utc).isoformat().replace('+00:00', 'Z')
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
        tmp = path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, path)


class IqRecording:
    """One IQ recording on an always-connected, normally closed file_sink."""

    ITEM = 8                                    # complex float32

    def __init__(self, sink, kind, directory, rate, center_hz, station_hz,
                 radio_name):
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
        self._first = None

    def start(self):
        os.makedirs(self.directory, exist_ok=True)
        if self._first is None:
            self._first = recording_base(self.directory, self.station_hz,
                                         'iq-' + self.kind)
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
