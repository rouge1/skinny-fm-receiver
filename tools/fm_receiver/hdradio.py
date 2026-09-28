"""HD Radio (NRSC-5): the tuned station's digital audio, through nrsc5.

The station's IQ is taken after the channelizer's first stage (at 0 Hz, the
digital sidebands' +-198 kHz intact), resampled to nrsc5's native
744,187.5 S/s and piped as cf32 into the ``nrsc5`` program
(github.com/theori-io/nrsc5, installed separately), which writes the
program's audio as raw 16-bit stereo at 44.1 kHz to its stdout and its
log - station, services, now playing, BER - to its stderr.

::

    stage 1 (station at 0 Hz, ~1 MS/s) ─ arb resampler ─ hd_iq_sink ─┐
                                                                      │ pipe
    nrsc5 -r - --iq-input-format cf32 -o - -t raw PROGRAM  <──────────┘
      stdout: audio ─ hd_audio_source (44.1 kHz) ─ 48 kHz ─ the audio mix
      stderr: log ─ :class:`HdRadio` state (the window's HD Radio box)

**A separate program, not the library.** A crash or hang in the decoder
stays in its process; the window only sees a pipe close. nrsc5 can't change
program while reading a pipe (its keys need a terminal), so a new program -
and a new station - starts it again: about 2-4 s to sync.

**The analog unless a program is chosen.** The decoder runs all the
time (the window's lamp and buttons say what the station carries), but
the digital is heard only once a program is chosen (:meth:`HdRadio.choose`,
an HD button); choosing none, or a retune, goes back to the analog.

**Lost audio is silence, and HD1 goes back to the analog for it.** For
a packet that is damaged or missing, or while it has lost sync, nrsc5
still writes its frame of 2048 samples, all exact zeros. Those frames are
marked lost here. HD1 plays the analog once lost frames are more than
``LOST_SHARE`` of the last ``LOST_WINDOW_S``, and the digital again only
after ``CLEAN_S`` with none lost. The analog is not delayed to match the
digital, so a switch jumps a few seconds; the hysteresis keeps that rare.
HD2-4 have no analog: they play with the gaps.

**The audio shares the radio's clock.** nrsc5's audio comes out of the
radio's samples, so it arrives at exactly the rate the analog audio does
and the mix (``ReceiveChain``) consumes both together. The source plays
silence until a little is buffered, and drops the excess past a few
seconds, so a stall or a burst does not build up delay.

Everything here runs off the window's thread except the three pipe
threads; the window calls :meth:`HdRadio.poll` from its timer.
"""

import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np  # type: ignore
from gnuradio import gr  # type: ignore

#: nrsc5's input rate for cs16/cf32, FM.
IQ_RATE = 744187.5
#: Its audio: 16-bit stereo.
AUDIO_RATE = 44100
#: RMS the IQ is scaled to (full scale 1.0): a cs16 file at 3000/32768
#: decoded cleanly, and it leaves the peaks far from clipping.
IQ_RMS = 0.09
#: Audio buffered before it starts to play, and the most kept.
PREFILL_S = 0.35
MAX_BUFFER_S = 3.0
#: How long a retune must stand before nrsc5 starts again (a drag across
#: the band would otherwise start it at every step).
RESTART_DELAY_S = 0.6
#: nrsc5's audio frame: 2048 stereo samples of 16 bits.
FRAME_BYTES = 2048 * 4
#: HD1 goes to the analog when more than this share of the frames played
#: in the last LOST_WINDOW_S were lost, and back after CLEAN_S with none.
LOST_SHARE = 0.10
LOST_WINDOW_S = 1.5
CLEAN_S = 3.0
#: The window the lost share is reported over.
REPORT_WINDOW_S = 3.0
#: HD1-HD8, as nrsc5 numbers them 0-7: NRSC-5 allows a main program and
#: seven more. Most stations carry up to four; 107.7 here lists five.
PROGRAMS = 8
#: The programs the window always shows a button for; HD5-HD8 appear only
#: when a station lists them.
PROGRAMS_SHOWN = 4

_EXTRA_PATHS = ('/usr/local/bin', '/opt/homebrew/bin', os.path.expanduser('~/.local/bin'))


def find_nrsc5():
    """The nrsc5 program's path, or None."""
    found = shutil.which('nrsc5')
    if found:
        return found
    for folder in _EXTRA_PATHS:
        path = os.path.join(folder, 'nrsc5')
        if os.access(path, os.X_OK):
            return path
    return None


# ------------------------------------------------------------ the log

_LINE = re.compile(r'^\d\d:\d\d:\d\d\s+(.*)$')
_SIG = re.compile(r'^SIG Service: type=(\w+) number=(\d+) name=(.*)$')
_PROGRAM = re.compile(r'^Audio program (\d+): (\w+), type: ([^,]*),')
_SERVICE = re.compile(r'^Audio service (\d+): (\w+), type: ([^,]*),')
_BER = re.compile(r'^BER: [\d.]+, avg: ([\d.]+)')
_MER = re.compile(r'^MER: (-?[\d.]+) dB \(lower\), (-?[\d.]+) dB \(upper\)')
_RATE = re.compile(r'^Audio bit rate: ([\d.]+) kbps')
_CRC = re.compile(r'^Audio packet CRC mismatches: (\d+)')
_OFFSET = re.compile(r'^Frequency offset: (-?[\d.]+) Hz')
_DATA = re.compile(r'^Data component: id=\d+ port=([0-9A-Fa-f]+) .*mime=([0-9A-Fa-f]{8})')
_LOT = re.compile(r'^LOT file: port=([0-9A-Fa-f]+) lot=(\d+) name=(.*) size=\d+ '
                  r'mime=[0-9A-Fa-f]{8} expiry=')
_XHDR = re.compile(r'^XHDR: (-?\d+) ([0-9A-Fa-f]{8}) (-?\d+)')
_ALERT = re.compile(r'^Alert: (Category=\[[^\]]*\] (?:\w+=)?\[[^\]]*\]) (.*)$')

#: The MIME numbers of a program's pictures (nrsc5.h).
MIME_ALBUM_ART = 0xBE4B7536
MIME_STATION_LOGO = 0xD9C72536


def empty_state():
    return {
        'synced': False,
        'mode': None,            # primary service mode: 1 MP1, 2 MP2, ...
        'station': '',
        'slogan': '',
        'message': '',
        'names': {},             # program -> SIG name ("HD2", "Pride Radio")
        'types': {},             # program -> programme type ("Adult Hits")
        'audio': set(),          # programs the station says carry audio
        'title': '',
        'artist': '',
        'album': '',
        'genre': '',
        'alert': None,           # (message, its category and places) or None
        'ber': None,
        'mer': None,
        'kbps': None,
        'offset_hz': None,       # the station's frequency as nrsc5 found it
        'decode_errors': 0,      # audio frames nrsc5 could not decode
        'crc_errors': 0,         # audio packets that arrived damaged
        'damaged': None,         # (share of the last 32 packets, when)
        # Pictures: each program's data ports for album art and logo (from
        # its SIG service), the files nrsc5 saved ((port, lot) -> name),
        # the latest file per port, and the album art the song points at.
        'ports': {},             # program -> {'art': port, 'logo': port}
        'lots': {},
        'latest': {},
        'art_lot': None,
        '_sig': None,            # the SIG service its components belong to
        'lines': 0,
    }


def parse_line(state, line):
    """Update ``state`` from one line of nrsc5's log; returns ``state``."""
    m = _LINE.match(line.rstrip('\n'))
    text = m.group(1) if m else line.strip()
    state['lines'] += 1
    if text == 'Synchronized':
        state['synced'] = True
    elif text == 'Lost synchronization':
        state['synced'] = False
    elif text.startswith('Primary service mode: '):
        try:
            state['mode'] = int(text.rsplit(' ', 1)[1])
        except ValueError:
            pass
    elif text.startswith('Station name: '):
        state['station'] = text[len('Station name: '):].strip()
    elif text.startswith('Slogan: '):
        state['slogan'] = text[len('Slogan: '):].strip()
    elif text.startswith('Message: '):
        state['message'] = text[len('Message: '):].strip()
    elif text == 'Audio decoding error':
        state['decode_errors'] += 1
    elif text.startswith('Title: '):
        state['title'] = text[len('Title: '):].strip()
    elif text.startswith('Artist: '):
        state['artist'] = text[len('Artist: '):].strip()
    elif text.startswith('Album: '):
        state['album'] = text[len('Album: '):].strip()
    elif text.startswith('Genre: '):
        state['genre'] = text[len('Genre: '):].strip()
    elif text == 'Alert ended':
        state['alert'] = None
    else:
        for pattern, key in ((_SIG, 'sig'), (_PROGRAM, 'program'), (_SERVICE, 'service'),
                             (_BER, 'ber'), (_MER, 'mer'), (_RATE, 'kbps'),
                             (_CRC, 'crc'), (_OFFSET, 'offset'), (_DATA, 'data'),
                             (_LOT, 'lot'), (_XHDR, 'xhdr'), (_ALERT, 'alert')):
            m = pattern.match(text)
            if not m:
                continue
            if key == 'sig':
                state['_sig'] = None
                if m.group(1) == 'audio':
                    program = int(m.group(2)) - 1
                    state['names'][program] = m.group(3).strip()
                    state['_sig'] = program
            elif key == 'data':
                program, mime = state['_sig'], int(m.group(2), 16)
                kind = {MIME_ALBUM_ART: 'art', MIME_STATION_LOGO: 'logo'}.get(mime)
                if program is not None and kind:
                    state['ports'].setdefault(program, {})[kind] = int(m.group(1), 16)
            elif key == 'lot':
                port, lot = int(m.group(1), 16), int(m.group(2))
                name = f"{lot}_{m.group(3).strip()}"      # as nrsc5 saves it
                state['lots'][(port, lot)] = name
                state['latest'][port] = name
            elif key == 'xhdr':
                if int(m.group(2), 16) == MIME_ALBUM_ART:
                    lot = int(m.group(3))
                    state['art_lot'] = lot if lot >= 0 else None
            elif key == 'alert':
                state['alert'] = (m.group(2).strip(), m.group(1))
            elif key == 'offset':
                state['offset_hz'] = float(m.group(1))
            elif key == 'program':
                state['types'][int(m.group(1))] = m.group(3).strip()
            elif key == 'service':
                state['audio'].add(int(m.group(1)))
            elif key == 'ber':
                state['ber'] = float(m.group(1))
            elif key == 'mer':
                state['mer'] = (float(m.group(1)), float(m.group(2)))
            elif key == 'crc':
                state['crc_errors'] += int(m.group(1))
                state['damaged'] = (int(m.group(1)) / 32.0, time.monotonic())
            else:
                state['kbps'] = float(m.group(1))
            break
    return state


# ------------------------------------------------------------ the decoder

class HdRadio:
    """The nrsc5 process, its pipes, and what it has said.

    ``set_enabled`` and ``set_program`` say what is wanted; :meth:`poll`
    (the window's timer) starts, stops and restarts the process to match.
    The flowgraph's blocks call :meth:`feed` and :meth:`take_audio`.
    """

    def __init__(self, path=None):
        self.path = path if path is not None else find_nrsc5()
        self.enabled = False
        self.program = 0
        # Whether the digital is wanted (a lit HD button) or the analog is
        # (none lit). The decoder runs either way, so the window can say
        # what the station has.
        self.chosen = False
        self.error = None
        self.state = empty_state()
        self._lock = threading.Lock()
        self._proc = None
        self._iq = collections.deque()
        self._iq_bytes = 0
        self._iq_ready = threading.Event()
        self._audio = collections.deque()
        self._audio_frames = 0
        self._playing = False
        self._last_audio = None
        self._restart_at = None
        self._gain = None
        self._started_at = None
        # Where a gap would come from: the buffer running dry while playing
        # (this end), IQ dropped because the writer fell behind (this end),
        # or nrsc5's own decode and CRC errors (the signal).
        self.underruns = 0
        self.iq_dropped_s = 0.0
        self._reset_judgement()
        # Where nrsc5 saves the station's pictures (album art, logos): made
        # on first start, removed on close. A program change on the same
        # station keeps what it knows of them (a logo can take minutes to
        # come round again); a retune does not.
        self._files = None
        self._new_station = True

    def pictures(self, s=None):
        """(album art, station logo) for the program playing, as paths to
        the files nrsc5 saved, or None where there is none (yet)."""
        s = s if s is not None else self.status()
        folder = self._files
        if folder is None:
            return None, None
        ports = s['ports'].get(self.program) or s['ports'].get(0) or {}

        def path(name):
            full = os.path.join(folder, name) if name else None
            return full if full and os.path.exists(full) else None

        art = None
        if s['art_lot'] is not None and 'art' in ports:
            art = path(s['lots'].get((ports['art'], s['art_lot'])))
        logo = path(s['latest'].get(ports['logo'])) if 'logo' in ports else None
        return art, logo

    def _reset_judgement(self):
        # (when played, frames, lost) for the last REPORT_WINDOW_S, and
        # whether HD1's digital is good enough to hear (with hysteresis).
        self._played = collections.deque()
        self._clean_since = None
        self._good = False

    @property
    def available(self):
        return self.path is not None

    @property
    def running(self):
        return self._proc is not None

    # -- wanted
    def set_enabled(self, on):
        on = bool(on) and self.available
        if on != self.enabled:
            self.enabled = on
            self._restart_at = time.monotonic() if on else None
            if not on:
                self._stop()

    def set_program(self, program):
        program = min(max(int(program), 0), PROGRAMS - 1)
        if program != self.program:
            self.program = program
            if self.enabled:
                self._restart_at = time.monotonic()

    def choose(self, program):
        """Play ``program`` (0-3) digitally, or with None the analog. The
        decoder keeps the last program it was given, so choosing it again
        needs no restart."""
        if program is None:
            self.chosen = False
            return
        self.set_program(program)
        self.chosen = True

    def retuned(self):
        """A new station (or a new chain): the analog, and the decoder
        again once it settles, at HD1 - the old station's programs mean
        nothing here."""
        self.program = 0
        self.chosen = False
        self._new_station = True
        if self.enabled:
            self._stop()
            self._restart_at = time.monotonic() + RESTART_DELAY_S

    def close(self):
        self.enabled = False
        self._restart_at = None
        self._stop()
        folder, self._files = self._files, None
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

    # -- the window's timer
    def poll(self):
        """Start or restart the process when due; notice one that died."""
        proc = self._proc
        if proc is not None and proc.poll() is not None:
            self.error = f"nrsc5 stopped (exit {proc.returncode})"
            self._stop()
            if self.enabled:
                self._restart_at = time.monotonic() + 2.0
        if self._restart_at is not None and time.monotonic() >= self._restart_at:
            self._restart_at = None
            self._stop()
            if self.enabled:
                self._start()

    def audio_live(self, within_s=1.0):
        """Digital audio has been playing within the last ``within_s``."""
        last = self._last_audio
        return last is not None and time.monotonic() - last < within_s

    def lost_share(self, window_s=REPORT_WINDOW_S):
        """The share of the frames played in the last ``window_s`` that
        nrsc5 had no audio for (0 when nothing played)."""
        since = time.monotonic() - window_s
        with self._lock:
            total = lost = 0
            for when, frames, gone in self._played:
                if when >= since:
                    total += frames
                    lost += frames if gone else 0
        return lost / total if total else 0.0

    def digital_good(self):
        """HD1's digital is good enough to hear rather than the analog:
        see the module notes."""
        now = time.monotonic()
        if not self.audio_live():
            self._good = False
        elif self._good:
            if self.lost_share(LOST_WINDOW_S) > LOST_SHARE:
                self._good = False
        else:
            clean = self._clean_since
            self._good = clean is not None and now - clean >= CLEAN_S
        return self._good

    def heard(self):
        """What the mix should play: 'analog', 'digital' or 'none'."""
        if not self.enabled or not self.chosen:
            return 'analog'
        if self.program == 0:
            return 'digital' if self.digital_good() else 'analog'
        return 'digital' if self.audio_live() else 'none'

    def status(self):
        """A copy of what nrsc5 has said, plus how this end stands."""
        with self._lock:
            s = dict(self.state)
            s['names'] = dict(s['names'])
            s['types'] = dict(s['types'])
            s['audio'] = sorted(s['audio'])
            s['ports'] = {k: dict(v) for k, v in s['ports'].items()}
            s['lots'] = dict(s['lots'])
            s['latest'] = dict(s['latest'])
        s.pop('_sig', None)
        damaged = s.pop('damaged')
        # nrsc5 says so only for a group of 32 packets (~1.5 s) with damage.
        s['damaged_share'] = (damaged[0] if damaged is not None
                              and time.monotonic() - damaged[1] < 3.0 else 0.0)
        s['lost_share'] = self.lost_share()
        s.update(enabled=self.enabled, available=self.available, running=self.running,
                 program=self.program, playing=self.audio_live(), error=self.error,
                 heard=self.heard(), digital_good=self._good, chosen=self.chosen,
                 starting=self._restart_at is not None,
                 underruns=self.underruns, iq_dropped_s=round(self.iq_dropped_s, 3),
                 buffer_s=round(self._audio_frames / AUDIO_RATE, 3),
                 seconds=(time.monotonic() - self._started_at
                          if self._started_at is not None else None))
        s['art_path'], s['logo_path'] = self.pictures(s)
        return s

    # -- process
    def _start(self):
        if self._files is None:
            try:
                self._files = tempfile.mkdtemp(prefix='fmrx-hd-')
            except OSError as exc:
                print(f"FM receiver: HD Radio pictures: {exc}", file=sys.stderr)
        cmd = [self.path, '-r', '-', '--iq-input-format', 'cf32',
               '-o', '-', '-t', 'raw']
        if self._files is not None:
            cmd += ['--dump-aas-files', self._files]
        cmd.append(str(self.program))
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            self.error = f"nrsc5 would not start: {exc}"
            print(f"FM receiver: {self.error}", file=sys.stderr)
            return
        with self._lock:
            old, self.state = self.state, empty_state()
            if not self._new_station:
                for key in ('ports', 'lots', 'latest'):
                    self.state[key] = old[key]
            self._new_station = False
            self._iq.clear()
            self._iq_bytes = 0
            self._audio.clear()
            self._audio_frames = 0
        self._playing = False
        self._last_audio = None
        self._reset_judgement()
        self._gain = None
        self.error = None
        self.underruns = 0
        self.iq_dropped_s = 0.0
        self._started_at = time.monotonic()
        self._proc = proc
        for target in (self._write_iq, self._read_audio, self._read_log):
            threading.Thread(target=target, args=(proc,), daemon=True,
                             name=f"nrsc5 {target.__name__}").start()

    def _stop(self):
        proc, self._proc = self._proc, None
        self._started_at = None
        self._iq_ready.set()                       # the writer sees it is over
        if proc is None:
            return
        for pipe in (proc.stdin,):
            try:
                pipe.close()
            except OSError:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        with self._lock:
            self._audio.clear()
            self._audio_frames = 0
        self._playing = False
        self._last_audio = None
        self._reset_judgement()

    # -- IQ in (the flowgraph's thread)
    def feed(self, samples):
        """The station at 0 Hz, complex64 at ``IQ_RATE``. Scaled to a steady
        level and queued for the writer; dropped when nothing runs or the
        writer is more than a second behind."""
        if self._proc is None or not len(samples):
            return
        power = float(np.mean(samples.real ** 2 + samples.imag ** 2))
        if power <= 0:
            return
        want = IQ_RMS / np.sqrt(power)
        # Slow, so the level does not breathe with the modulation.
        self._gain = want if self._gain is None else self._gain + 0.05 * (want - self._gain)
        data = (samples * np.float32(self._gain)).astype(np.complex64).tobytes()
        with self._lock:
            if self._iq_bytes > IQ_RATE * 8:
                self.iq_dropped_s += len(samples) / IQ_RATE
                return
            self._iq.append(data)
            self._iq_bytes += len(data)
        self._iq_ready.set()

    def _write_iq(self, proc):
        while self._proc is proc:
            self._iq_ready.wait(0.5)
            self._iq_ready.clear()
            while self._proc is proc:
                with self._lock:
                    if not self._iq:
                        break
                    data = self._iq.popleft()
                    self._iq_bytes -= len(data)
                try:
                    proc.stdin.write(data)
                except (OSError, ValueError):
                    return

    # -- audio out
    def _read_audio(self, proc):
        out = proc.stdout
        pending = b''
        while True:
            try:
                data = out.read(FRAME_BYTES)
            except (OSError, ValueError):
                return
            if not data:
                return
            if self._proc is not proc:
                return
            pending += data
            usable = len(pending) - len(pending) % FRAME_BYTES
            if usable:
                self.put_audio(pending[:usable])
                pending = pending[usable:]

    def put_audio(self, data):
        """Whole frames of nrsc5's raw audio, each marked lost when it is
        all zeros (see the module notes)."""
        with self._lock:
            for start in range(0, len(data), FRAME_BYTES):
                pcm = np.frombuffer(data[start:start + FRAME_BYTES], dtype='<i2')
                lost = not pcm.any()
                frames = (pcm.astype(np.float32) / 32768.0).reshape(-1, 2)
                self._audio.append((frames, lost))
                self._audio_frames += len(frames)
            while self._audio_frames > MAX_BUFFER_S * AUDIO_RATE and len(self._audio) > 1:
                self._audio_frames -= len(self._audio.popleft()[0])

    def take_audio(self, n):
        """Up to ``n`` stereo frames for the flowgraph, as an (m, 2) array;
        m is 0 while it fills (and after it ran dry, until it has refilled)."""
        with self._lock:
            if not self._playing:
                if self._audio_frames < PREFILL_S * AUDIO_RATE:
                    return None
                self._playing = True
            parts, got, now = [], 0, time.monotonic()
            while self._audio and got < n:
                chunk, lost = self._audio[0]
                need = n - got
                if len(chunk) <= need:
                    self._audio.popleft()
                else:
                    self._audio[0] = (chunk[need:], lost)
                    chunk = chunk[:need]
                parts.append(chunk)
                got += len(chunk)
                self._played.append((now, len(chunk), lost))
                if lost:
                    self._clean_since = None
                elif self._clean_since is None:
                    self._clean_since = now
            while self._played and self._played[0][0] < now - REPORT_WINDOW_S:
                self._played.popleft()
            self._audio_frames -= got
            if not self._audio:
                self._playing = False
                self.underruns += 1
        if got:
            self._last_audio = now
        return np.concatenate(parts) if parts else None

    # -- log
    def _read_log(self, proc):
        for raw in iter(proc.stderr.readline, b''):
            if self._proc is not proc:
                break
            line = raw.decode('utf-8', 'replace')
            with self._lock:
                parse_line(self.state, line)


# ------------------------------------------------------------ blocks

class hd_iq_sink(gr.sync_block):
    """Hands the station's IQ to :meth:`HdRadio.feed`. Does nothing while
    no nrsc5 runs, which is most of the time."""

    def __init__(self, hd):
        gr.sync_block.__init__(self, name='hd_iq_sink', in_sig=[np.complex64],
                               out_sig=None)
        self.hd = hd

    def work(self, input_items, output_items):
        samples = input_items[0]
        try:
            if self.hd.running:
                self.hd.feed(samples)
        except Exception as exc:                   # never stop the flowgraph
            print(f"FM receiver: HD Radio feed: {exc}", file=sys.stderr)
        return len(samples)


class hd_audio_source(gr.sync_block):
    """nrsc5's audio, left and right at 44.1 kHz: silence where there is
    none. Paced by what reads it (the audio mix, with the analog audio)."""

    def __init__(self, hd):
        gr.sync_block.__init__(self, name='hd_audio_source', in_sig=None,
                               out_sig=[np.float32, np.float32])
        self.hd = hd

    def work(self, input_items, output_items):
        left, right = output_items[0], output_items[1]
        n = min(len(left), len(right))
        try:
            frames = self.hd.take_audio(n)
        except Exception as exc:
            print(f"FM receiver: HD Radio audio: {exc}", file=sys.stderr)
            frames = None
        got = 0 if frames is None else len(frames)
        if got:
            left[:got] = frames[:, 0]
            right[:got] = frames[:, 1]
        left[got:n] = 0.0
        right[got:n] = 0.0
        return n
