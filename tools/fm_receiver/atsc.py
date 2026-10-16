"""ATSC television: 8VSB from the radio's IQ, the transport stream, and the
picture and sound of one program.

The demodulator is ``gr-dtv``'s own blocks, arranged as the RF bench
toolkit's ``atscReceiver`` arranges them (``/data/python/SDR/apps/
atscReceiver.py``; ``knowledge/atsc.md`` there says why: the resampler is
half the AFC, the counters are what tell a weak signal from a mistuned one).
Changes made for this app are marked "FM receiver".

Three things happen here, none of them in the window's thread:

- ``AtscDemod`` and ``ts_sink`` are in the flowgraph, beside the FM chain
  (``dsp.ReceiveChain`` connects them when the window asks for ATSC).
- ``VideoPipe`` feeds the transport stream to an ffmpeg that decodes one
  program to raw pictures and PCM; reader threads keep the newest picture
  and queue the sound.
- ``AtscRadio`` is what the window talks to: it measures the pilot and the
  level, takes the AFC's two corrections, counts good and bad packets, and
  chooses the program.

Nothing here decodes video itself: ffmpeg does.
"""

import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np  # type: ignore
from gnuradio import analog, blocks, dtv, filter, gr  # type: ignore
from gnuradio.filter import firdes  # type: ignore

from .atsc_core import (Afc, SYMBOL_RATE, STREAM_TYPES, TsAnalyzer,
                        channel_for_center, mer_db)

#: The 6 MHz channel needs at least this much IQ to be received.
MIN_RATE = 8e6
#: Samples per symbol into the demodulator: the one knob for how much work
#: the receiver does (toolkit measurement: 1.2 buys headroom for half a dB).
SPS = 1.2
#: 45 ms of signal at 12 MS/s: the AFC's window.
AFC_WINDOW = 1 << 19
#: Enough equalized symbols for a steady MER.
MER_WINDOW = 1 << 14
TS_PACKET = 188
#: What a stalled player may hold before whole packets are dropped.
PLAYER_BACKLOG = 4 << 20

#: The picture ffmpeg makes: one size whatever the program (a 4:3 one is
#: padded), so the reader never has to know what is coming.
VIDEO_W, VIDEO_H = 854, 480
FRAME_BYTES = VIDEO_W * VIDEO_H * 3
AUDIO_RATE = 48000
AUDIO_PREFILL_S = 0.25
AUDIO_MAX_S = 1.5
#: Bad packets over the last few seconds, as a share, above which the
#: stream is not being decoded.
NOT_DECODING_PCT = 50.0
WINDOW_S = 3.0
#: How long after a retune the signal is only being looked for.
SEARCH_S = 3.0


def find_ffmpeg():
    return shutil.which('ffmpeg')


def page_aligned_items(item_size, page=4096):
    """Items of this size that fill whole pages: GNU Radio rounds a buffer up
    to a page and warns when it was not already one (a 188- or 207-byte
    packet never is)."""
    return page // math.gcd(page, item_size)


def align_output_buffer(block, port, item_size):
    try:
        block.set_min_output_buffer(port, page_aligned_items(item_size))
    except Exception:
        pass


# ------------------------------------------------------------ flowgraph

class sample_tap(gr.sync_block):
    """Keeps the most recent samples so the Qt thread can measure them: a
    ring, so nothing builds up behind it."""

    def __init__(self, depth, dtype=np.complex64):
        gr.sync_block.__init__(self, name='atsc_sample_tap', in_sig=[dtype],
                               out_sig=None)
        self._depth = int(depth)
        self._buf = np.zeros(self._depth, dtype=dtype)
        self._pos = 0
        self._filled = 0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        x = input_items[0]
        n = len(x)
        d = self._depth
        with self._lock:
            if n >= d:
                self._buf[:] = x[n - d:]
                self._pos = 0
            else:
                end = self._pos + n
                if end <= d:
                    self._buf[self._pos:end] = x
                    self._pos = end % d
                else:
                    k = d - self._pos
                    self._buf[self._pos:] = x[:k]
                    self._buf[:n - k] = x[k:]
                    self._pos = n - k
            self._filled = min(d, self._filled + n)
        return n

    def latest(self):
        """The buffer in order, oldest first; empty until it has filled."""
        with self._lock:
            if self._filled < self._depth:
                return self._buf[:0].copy()
            return np.concatenate((self._buf[self._pos:], self._buf[:self._pos]))


class ts_sink(gr.sync_block):
    """The end of the chain: analyse the stream, and hand it to a player.

    The player's pipe is non-blocking and whole packets are dropped when it
    will not keep up: a blocking write into a stalled player would park the
    scheduler thread, and there is no backpressure on the air.
    """

    def __init__(self):
        gr.sync_block.__init__(self, name='atsc_ts_sink', in_sig=[np.uint8],
                               out_sig=None)
        self.analyzer = TsAnalyzer()
        self._lock = threading.Lock()
        self._player = None
        self._pending = bytearray()
        self.dropped = 0

    def work(self, input_items, output_items):
        data = input_items[0].tobytes()
        try:
            with self._lock:
                self.analyzer.feed(data)
                if self._player is not None:
                    self._pending += data
                    self._drain()
        except Exception as exc:               # never stop the flowgraph
            print(f"FM receiver: ATSC stream: {exc}", file=sys.stderr)
        return len(input_items[0])

    def _drain(self):
        if len(self._pending) > PLAYER_BACKLOG:
            excess = len(self._pending) - PLAYER_BACKLOG
            excess += (-excess) % TS_PACKET
            del self._pending[:excess]
            self.dropped += excess
        try:
            written = os.write(self._player.stdin.fileno(), self._pending)
            del self._pending[:written]
        except BlockingIOError:
            pass
        except (OSError, ValueError, AttributeError):
            self._close_player()

    def attach_player(self, proc):
        """Send the stream to ``proc``'s stdin from now on."""
        fd = proc.stdin.fileno()
        os.set_blocking(fd, False)
        try:
            import fcntl
            fcntl.fcntl(fd, 1031, 1 << 20)         # F_SETPIPE_SZ (Linux)
        except (ImportError, OSError, AttributeError):
            pass
        with self._lock:
            self._close_player()
            self._player = proc
            self._pending = bytearray()
            self.dropped = 0

    def detach_player(self):
        with self._lock:
            self._close_player()

    def _close_player(self):
        proc, self._player = self._player, None
        self._pending = bytearray()
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def snapshot(self):
        with self._lock:
            return self.analyzer.snapshot()

    def programs(self):
        with self._lock:
            return {p: dict(v) for p, v in self.analyzer.programs.items()}

    def psip(self):
        with self._lock:
            ps = self.analyzer.psip
            return ({p: dict(c) for p, c in ps.channels.items()},
                    {s: list(e) for s, e in ps.events.items()})


class AtscDemod(gr.hier_block2):
    """Complex baseband on a 6 MHz channel in, transport stream bytes out:
    ``gnuradio.dtv.atsc_rx`` built from the same blocks, so the resampler
    (half the AFC) and the counters stay reachable."""

    NFILTS = 16
    RRC_SYMS = 8
    EXCESS_BW = 0.1152

    def __init__(self, sample_rate, sps=SPS):
        gr.hier_block2.__init__(
            self, "atsc_demod",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_char))
        self.sample_rate = float(sample_rate)
        self.sps = float(sps)
        out_rate = SYMBOL_RATE * self.sps
        self.nominal_interp = out_rate / self.sample_rate

        self.tap = sample_tap(AFC_WINDOW)
        self.rotator = blocks.rotator_cc(0.0)
        # The agc inside the chain creeps at 1e-5 a sample toward 4.0, so an
        # off-air signal at 0.003 rms would take half a minute to decode:
        # the level is set from a measurement, and the agc has the fine work.
        self.level = blocks.multiply_const_cc(1.0)

        filter_rate = self.sample_rate * self.NFILTS
        ntaps = int((2 * self.RRC_SYMS + 1) * self.sps * self.NFILTS)
        rrc = firdes.root_raised_cosine(
            self.NFILTS * (SYMBOL_RATE / 2.0) / filter_rate,
            filter_rate, SYMBOL_RATE / 2.0, self.EXCESS_BW, ntaps)
        self.pfb = filter.pfb_arb_resampler_ccf(self.nominal_interp, rrc,
                                                self.NFILTS)
        self.fpll = dtv.atsc_fpll(out_rate)
        self.dcr = filter.dc_blocker_ff(4096)
        self.agc = analog.agc_ff(1e-5, 4.0)
        self.btl = dtv.atsc_sync(out_rate)
        self.fsc = dtv.atsc_fs_checker()
        self.equ = dtv.atsc_equalizer()
        self.vit = dtv.atsc_viterbi_decoder()
        self.dei = dtv.atsc_deinterleaver()
        self.rsd = dtv.atsc_rs_decoder()
        self.der = dtv.atsc_derandomizer()
        self.dep = dtv.atsc_depad()
        for blk in (self.vit, self.dei):
            align_output_buffer(blk, 0, 207)
        for blk in (self.rsd, self.der):
            align_output_buffer(blk, 0, 188)

        # Python blocks hold the GIL for every call: at 10 MS/s a tap that
        # copies every sample starved the radio's reader (BB60D "Sample
        # Overrun"). The AFC and the level need a window now and then, not
        # every sample: one window in eight, a tenth of a second apart.
        self.gate = blocks.keep_m_in_n(gr.sizeof_gr_complex, AFC_WINDOW,
                                       8 * AFC_WINDOW, 0)
        self.connect(self, self.gate, self.tap)
        # The decoder's PLLs and its AGC settle on whatever arrives first, so
        # the level and both AFC corrections must be right before the first
        # sample reaches it: changed in flight, it locked on about one start
        # in three. A valve (a selector with the second output to nowhere)
        # keeps everything from the rotator on idle until ``open`` - which
        # also means an empty channel costs no decoding.
        self.valve = blocks.selector(gr.sizeof_gr_complex, 0, 1)
        self.drain = blocks.null_sink(gr.sizeof_gr_complex)
        self.opened = False
        self.connect(self, self.valve)
        self.connect((self.valve, 1), self.drain)
        self.connect((self.valve, 0), self.rotator, self.level, self.pfb, self.fpll,
                     self.dcr, self.agc, self.btl, self.fsc)
        self.connect((self.fsc, 0), (self.equ, 0))
        self.connect((self.fsc, 1), (self.equ, 1))
        self.connect((self.equ, 0), (self.vit, 0))
        self.connect((self.equ, 1), (self.vit, 1))
        self.connect((self.vit, 0), (self.dei, 0))
        self.connect((self.vit, 1), (self.dei, 1))
        self.connect((self.dei, 0), (self.rsd, 0))
        self.connect((self.dei, 1), (self.rsd, 1))
        self.connect((self.rsd, 0), (self.der, 0))
        self.connect((self.rsd, 1), (self.der, 1))
        self.connect(self.der, self.dep, self)

        self.symbols = sample_tap(MER_WINDOW, np.float32)
        self.flatten = blocks.vector_to_stream(gr.sizeof_float, 832)
        self.thin = blocks.keep_one_in_n(gr.sizeof_float, 32)   # as above
        self.connect((self.equ, 0), self.flatten, self.thin, self.symbols)

    def input_samples(self):
        return self.tap.latest()

    def mer(self):
        s = self.symbols.latest()
        return mer_db(s) if s.size else 0.0

    def rs_counters(self):
        return {'packets': self.rsd.num_packets(),
                'bad': self.rsd.num_bad_packets(),
                'corrected': self.rsd.num_errors_corrected()}

    def open(self):
        """Let the stream through to the decoder."""
        self.opened = True
        self.valve.set_output_index(0)

    def set_level(self, gain):
        self.level.set_k(float(gain))

    def apply_afc(self, afc):
        self.rotator.set_phase_inc(-2 * np.pi * afc.error_hz / self.sample_rate)
        self.pfb.set_rate(self.nominal_interp * afc.clock_ratio)


class atsc_audio_source(gr.sync_block):
    """The program's sound, left and right at 48 kHz; silence where there is
    none. Paced by what reads it (the audio mix, on the radio's clock)."""

    def __init__(self, radio):
        gr.sync_block.__init__(self, name='atsc_audio_source', in_sig=None,
                               out_sig=[np.float32, np.float32])
        self.radio = radio

    def work(self, input_items, output_items):
        left, right = output_items[0], output_items[1]
        n = min(len(left), len(right))
        try:
            frames = self.radio.take_audio(n)
        except Exception as exc:
            print(f"FM receiver: ATSC audio: {exc}", file=sys.stderr)
            frames = None
        got = 0 if frames is None else len(frames)
        if got:
            left[:got] = frames[:, 0]
            right[:got] = frames[:, 1]
        left[got:n] = 0.0
        right[got:n] = 0.0
        return n


# ------------------------------------------------------------ ffmpeg

_STREAM = re.compile(r'Stream #0:\d+\[0x([0-9a-f]+)\][^:]*: (Video|Audio): (.*)')
_PROGRAM = re.compile(r'^\s+Program (\d+)')


def describe_video(text):
    """'1080i MPEG-2 29.97 fps' out of ffmpeg's stream line."""
    codec = text.split(' ', 1)[0].replace('mpeg2video', 'MPEG-2') \
        .replace('h264', 'H.264').replace('hevc', 'HEVC')
    size = re.search(r'\b(\d{3,5})x(\d{3,5})\b', text)
    fps = re.search(r'([\d.]+) fps', text)
    scan = 'i' if re.search(r'(top|bottom) first', text) else 'p'
    parts = []
    if size:
        parts.append(f"{size.group(2)}{scan}")
    parts.append(codec)
    if fps:
        parts.append(f"{float(fps.group(1)):g} fps")
    return ' '.join(parts)


def describe_audio(text):
    name = re.match(r'\w+', text)
    name = name.group(0) if name else ''
    codec = {'ac3': 'AC-3', 'eac3': 'E-AC-3', 'aac': 'AAC', 'mp2': 'MP2'}.get(name, name)
    chans = re.search(r',\s*(mono|stereo|\d\.\d(?:\([a-z]+\))?|\d+ channels)', text)
    layout = chans.group(1) if chans else ''
    layout = re.sub(r'\(.*\)', '', layout)
    return f"{codec} {layout}".strip()


class VideoPipe:
    """ffmpeg reading the transport stream on its stdin and writing one
    program's pictures (raw RGB, ``VIDEO_W`` x ``VIDEO_H``) on its stdout
    and its sound (48 kHz stereo PCM) on a second pipe.

    A thread reads each; the newest picture is kept, and the sound queued.
    Choosing another program starts a new ffmpeg with another ``-map``.
    """

    def __init__(self, ffmpeg, on_audio):
        self.ffmpeg = ffmpeg
        self.on_audio = on_audio
        self.proc = None
        self.program = None
        self.started = 0.0
        self._frame = None
        self.frame_no = 0
        self.frames_seen = 0
        self.formats = {}                 # program -> {'video':, 'audio':}
        self.log = deque(maxlen=12)
        self._lock = threading.Lock()

    def start(self, program, sink):
        self.stop(sink)
        read_end, write_end = os.pipe()
        nice = shutil.which('nice')
        argv = ([nice, '-n', '10'] if nice else []) + [self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'info',
                '-fflags', '+discardcorrupt', '-probesize', '2000000',
                '-analyzeduration', '2000000', '-f', 'mpegts', '-i', 'pipe:0',
                '-map', f'0:p:{program}:v:0',
                '-vf', ('scale=iw*sar:ih:flags=fast_bilinear,setsar=1,'
                        f'scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease:'
                        f'flags=fast_bilinear,pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2'),
                '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
                '-map', f'0:p:{program}:a:0?', '-ar', str(AUDIO_RATE), '-ac', '2',
                '-f', 's16le', f'pipe:{write_end}']
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    pass_fds=(write_end,))
        except Exception:
            os.close(read_end)
            os.close(write_end)
            raise
        os.close(write_end)
        self.proc, self.program, self.started = proc, program, time.monotonic()
        with self._lock:
            self._frame = None
        for target, args in ((self._read_video, (proc,)),
                             (self._read_audio, (proc, read_end)),
                             (self._read_log, (proc,))):
            threading.Thread(target=target, args=args, daemon=True).start()
        sink.attach_player(proc)

    def stop(self, sink=None):
        if sink is not None:
            sink.detach_player()
        proc, self.proc = self.proc, None
        self.program = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            threading.Thread(target=self._reap, args=(proc,), daemon=True).start()

    @staticmethod
    def _reap(proc):
        try:
            proc.wait(2)
        except Exception:
            proc.kill()
            proc.wait()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def frame(self):
        """(rgb bytes, frame number) of the newest picture, or None."""
        with self._lock:
            return (self._frame, self.frame_no) if self._frame is not None else None

    def _read_video(self, proc):
        out = proc.stdout
        while True:
            try:
                data = out.read(FRAME_BYTES)
            except (OSError, ValueError):
                return
            if not data or len(data) < FRAME_BYTES:
                return
            if self.proc is not proc:
                return
            with self._lock:
                self._frame = data
                self.frame_no += 1
                self.frames_seen += 1

    def _read_audio(self, proc, fd):
        chunk = 4 * 2 * 1024
        with os.fdopen(fd, 'rb', 0) as src:
            pending = b''
            while True:
                try:
                    data = src.read(chunk)
                except (OSError, ValueError):
                    return
                if not data or self.proc is not proc:
                    return
                pending += data
                usable = len(pending) - len(pending) % 4
                if usable:
                    self.on_audio(pending[:usable])
                    pending = pending[usable:]

    def _read_log(self, proc):
        program = None
        for raw in iter(proc.stderr.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            if not line:
                continue
            self.log.append(line)
            m = _PROGRAM.match(line)
            if m:
                program = int(m.group(1))
                continue
            m = _STREAM.search(line)
            if m and program is not None:
                kind = m.group(2).lower()
                text = (describe_video if kind == 'video' else describe_audio)(m.group(3))
                self.formats.setdefault(program, {})[kind] = text


# ------------------------------------------------------------ the radio

class AtscRadio:
    """What the window and ``fmctl`` talk to; kept across rebuilds of the
    flowgraph, like ``HdRadio``. ``attach`` puts the demodulator into a
    chain; ``poll`` is called from the window's timer."""

    def __init__(self):
        self.ffmpeg = find_ffmpeg()
        self.demod = None
        self.sink = None
        self.audio_src = None
        self._kept = []
        self.center_hz = None
        self.rate = None
        self.afc = Afc(0.0)
        self.want_program = None          # None: the first with a picture
        self.playing = True
        self._audio = deque()
        self._audio_frames = 0
        self._audio_on = False
        self._audio_lock = threading.Lock()
        self._level_db = None
        self._afc_at = 0.0
        self._attached = 0.0
        self._history = deque()
        self._restart_at = 0.0
        self.video = VideoPipe(self.ffmpeg, self._put_audio) if self.ffmpeg else None
        self.state = self._empty_state()

    # -- the flowgraph
    def available(self):
        return self.ffmpeg is not None

    def attach(self, tb, source, rate, center_hz):
        """Connect the demodulator to ``source``. Returns the audio source
        block, to be mixed into the sound by the caller."""
        self.detach()
        self.rate, self.center_hz = float(rate), float(center_hz)
        self.afc = Afc(self.center_hz)
        self._level_db = None
        self._afc_at = 0.0
        self._attached = time.monotonic()
        self._history.clear()
        self.demod = AtscDemod(self.rate)
        self.sink = ts_sink()
        self.audio_src = atsc_audio_source(self)
        tb.connect(source, self.demod, self.sink)
        self.state = self._empty_state()
        self.state['channel'] = channel_for_center(self.center_hz / 1e6)
        return self.audio_src

    def detach(self):
        if self.video is not None:
            self.video.stop(self.sink)
        self.clear_audio()
        # The blocks stay referenced: they may still be in a stopped flowgraph.
        if self.demod is not None:
            self._kept = (self._kept + [(self.demod, self.sink, self.audio_src)])[-4:]
        self.demod = self.sink = self.audio_src = None

    def close(self):
        self.detach()

    # -- sound
    def _put_audio(self, data):
        pcm = np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768.0
        frames = pcm.reshape(-1, 2)
        with self._audio_lock:
            self._audio.append(frames)
            self._audio_frames += len(frames)
            while self._audio_frames > AUDIO_MAX_S * AUDIO_RATE and len(self._audio) > 1:
                self._audio_frames -= len(self._audio.popleft())

    def clear_audio(self):
        with self._audio_lock:
            self._audio.clear()
            self._audio_frames = 0
            self._audio_on = False

    def take_audio(self, n):
        with self._audio_lock:
            if not self._audio_on:
                if self._audio_frames < AUDIO_PREFILL_S * AUDIO_RATE:
                    return None
                self._audio_on = True
            parts, got = [], 0
            while self._audio and got < n:
                chunk = self._audio[0]
                if len(chunk) <= n - got:
                    self._audio.popleft()
                else:
                    self._audio[0] = chunk[n - got:]
                    chunk = chunk[:n - got]
                parts.append(chunk)
                got += len(chunk)
            self._audio_frames -= got
            if not self._audio:
                self._audio_on = False
        return np.concatenate(parts) if parts else None

    # -- what the window reads
    @staticmethod
    def _empty_state():
        return {'channel': None, 'pilot_hz': None, 'prominence_db': 0.0,
                'ppm': 0.0, 'mer_db': 0.0, 'packets': 0, 'bad': 0,
                'bad_pct': None, 'rate_pps': 0.0, 'lock': 'searching',
                'programs': [], 'program': None, 'picture': False,
                'level_dbfs': None, 'dropped': 0}

    def choose(self, program):
        """A program number to show, or None for the first with a picture."""
        self.want_program = None if program is None else int(program)
        self.clear_audio()
        self._restart_at = 0.0
        if self.video is not None and self.sink is not None:
            self.video.stop(self.sink)

    def poll(self):
        """Measure, correct, count; start the picture when there is a
        stream. Cheap enough for a 4 Hz timer. Returns the state."""
        demod, sink = self.demod, self.sink
        if demod is None or sink is None:
            return self.state
        now = time.monotonic()
        st = self.state
        samples = demod.input_samples()
        if samples.size:
            mid = samples[samples.size // 2:]
            rms = float(np.sqrt(np.mean(np.abs(mid) ** 2)))
            if rms > 0:
                db = 20 * math.log10(rms)
                st['level_dbfs'] = db
                # Before the valve opens the level is set as measured; once
                # the decoder runs only a real change (a gain step) moves it.
                limit = 1.5 if not demod.opened else 4.0
                if self._level_db is None or abs(db - self._level_db) > limit:
                    self._level_db = db
                    demod.set_level(1.0 / rms)
            if now - self._afc_at > 1.0 or (not demod.opened and now - self._afc_at > 0.25):
                self._afc_at = now
                if self.afc.update(samples, self.rate):
                    demod.apply_afc(self.afc)
            if not demod.opened and self.afc.locked and self._level_db is not None:
                demod.open()
        st['prominence_db'] = self.afc.prominence_db
        st['pilot_hz'] = self.afc.raw_hz if self.afc.locked else None
        st['ppm'] = self.afc.ppm
        rs = demod.rs_counters()
        self._history.append((now, rs['packets'], rs['bad']))
        while self._history and self._history[0][0] < now - WINDOW_S:
            self._history.popleft()
        t0, p0, b0 = self._history[0]
        dp, db_ = rs['packets'] - p0, rs['bad'] - b0
        st['packets'], st['bad'] = rs['packets'], rs['bad']
        st['bad_pct'] = 100.0 * db_ / dp if dp > 50 else None
        st['rate_pps'] = dp / (now - t0) if now > t0 else 0.0
        st['mer_db'] = demod.mer() if dp else 0.0
        if not self.afc.locked:
            # The AFC measures once a second: give it a few before saying
            # there is nothing here.
            st['lock'] = ('searching' if now - self._attached < SEARCH_S
                          else 'no signal')
        elif st['bad_pct'] is None or st['bad_pct'] > NOT_DECODING_PCT:
            st['lock'] = 'pilot only'
        else:
            st['lock'] = 'locked'
        st['programs'] = self._programs(sink)
        st['dropped'] = sink.dropped
        self._pick(sink, now)
        st['program'] = self.video.program if self.video is not None else None
        st['picture'] = bool(self.video and self.video.frame() is not None)
        return st

    def _programs(self, sink):
        pat = sink.programs()
        channels, events = sink.psip()
        out = []
        for number in sorted(pat):
            streams = pat[number]['streams']
            kinds = {STREAM_TYPES.get(t, '') for t in streams.values()}
            has_video = any('video' in k for k in kinds)
            ch = channels.get(number, {})
            now_title = next_title = ''
            if ch:
                now, nxt = _now_next(events.get(ch['source'], []), time.time())
                now_title = _title(now)
                next_title = _title(nxt)
            fmt = (self.video.formats.get(number, {}) if self.video else {})
            out.append({
                'program': number, 'video': has_video,
                'name': ch.get('name', ''),
                'number': (f"{ch['major']}.{ch['minor']}" if ch else ''),
                'now': now_title, 'next': next_title,
                'format': fmt.get('video', ''), 'audio': fmt.get('audio', ''),
                'streams': ', '.join(sorted(k for k in kinds if k)),
            })
        return out

    def _pick(self, sink, now):
        """Start (or restart) the picture of the wanted program."""
        v = self.video
        if v is None or not self.playing or self.state['lock'] != 'locked':
            return
        programs = [p for p in self.state['programs'] if p['video']
                    and pat_has_streams(sink, p['program'])]
        if not programs:
            return
        wanted = self.want_program
        if wanted is None or all(p['program'] != wanted for p in programs):
            wanted = programs[0]['program']
        if v.alive() and v.program == wanted:
            return
        if now < self._restart_at:
            return
        self._restart_at = now + 2.0
        try:
            v.start(wanted, sink)
        except Exception as exc:
            print(f"FM receiver: ffmpeg would not start: {exc}", file=sys.stderr)

    def set_playing(self, on):
        self.playing = bool(on)
        if not on and self.video is not None:
            self.video.stop(self.sink)
            self.clear_audio()


def pat_has_streams(sink, program):
    return bool(sink.programs().get(program, {}).get('streams'))


def _title(event):
    """An event's title; '' when none is known, and a note when the station
    sent it compressed (so that is told apart from a guide with nothing)."""
    if event is None:
        return ''
    return event[2] or ('' if event[3] else '(title sent compressed - not read)')


def _now_next(events, unix_now):
    now = nxt = None
    for e in events:
        if e[0] <= unix_now < e[0] + e[1]:
            now = e
        elif e[0] > unix_now and (nxt is None or e[0] < nxt[0]):
            nxt = e
    return now, nxt
