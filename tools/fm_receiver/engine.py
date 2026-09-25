"""The flowgraph: one radio, and the sweep or the receive chain (with rtl_433).

Sweep and Receive want the radio at different rates and share nothing
downstream of it, so switching mode stops the flowgraph, disconnects
everything, sets the rate and builds the other chain onto the same source
block. It takes 0.2-0.3 s on a BB60D (measured), which keeps its device
open across the switch (``halt(hold=True)``) - reopening it was 1.6 s. The
BB60D sweeps in the device instead (``bb60_sweep``), with no flowgraph at
all, on the same open device; Receive starts its stream again. What
must *not* rebuild - retuning, channel bandwidth, volume, mute, stereo,
region, recording - is done on the running blocks. Receive can carry
rtl_433's chain (``rtl433.DecodeChain``) too, on the same samples as the
station (the Receive tab's rtl_433 card): its rtl_433 processes start with
the chain and end with it, and retuning moves its slices without a
rebuild.

Recordings play through here too, with no radio open. An IQ recording is
a radio (``radios.IQFile``) and goes through Receive; a WAV has a chain
of its own (``dsp.WavChain``). **Pausing an IQ recording stops the
flowgraph and starts it again as it stood** (:meth:`pause`, :meth:`resume`),
so RDS and the averages carry on; the file keeps its place.

Rules carried over from the RF bench toolkit:

- **Receive gain goes on after** ``start()`` (:meth:`Engine._started`).
- **Every Python block stays referenced** while it might be running; old
  chains are kept in ``_retired`` rather than dropped the moment they are
  disconnected.
"""

import sys
import time

from gnuradio import audio, gr  # type: ignore

from . import rtl433
from .dsp import AUDIO_RATE, ReceiveChain, WavChain, data_clock
from .sweep import sweep_sink


class Engine(gr.top_block):

    #: Old chains kept referenced after a rebuild; see the module notes.
    RETIRED_KEPT = 8

    def __init__(self, want_audio=True):
        gr.top_block.__init__(self, "FM Receiver", catch_exceptions=True)
        self.want_audio = bool(want_audio)
        self.radio = None
        self.mode = None
        self.running = False
        self.rx = None
        self.sweeper = None
        self.player = None
        self.decoder = None
        self.decode_error = None                 # why rtl_433 did not start in Receive
        self.rate = None
        self.lo_hz = None
        self.offset_hz = None
        self.station_hz = None
        self.audio_error = None
        self._audio_sink = None
        self._retired = []
        # When the radio's samples last arrived, in either mode; one block,
        # reconnected on every rebuild. ``_since`` is when this run began,
        # so a stall is counted from there, not from before a mode switch.
        self._clock = data_clock()
        self._since = None

    # ---------------------------------------------------------- radio
    def use_radio(self, radio):
        """Close whatever radio there was and open ``radio``. Raises
        ``RadioError`` (with the old one already closed) if it cannot."""
        self.halt()
        self._clear()
        if self.radio is not None:
            self.radio.close()
            self.radio = None
        radio.open()
        self.radio = radio

    def close(self):
        self.halt()
        self._clear()
        if self.radio is not None:
            self.radio.close()
            self.radio = None

    # ---------------------------------------------------------- lifecycle
    def halt(self, hold=False):
        """Stop the flowgraph. ``hold`` keeps a radio that can stay open
        between a stop and a start (the BB60D) open, for a mode switch. A
        radio's own sweep has no flowgraph: it is stopped, and its device
        stays open for the IQ stream."""
        if self.running and getattr(self.sweeper, 'native', False):
            self.sweeper.stop()
            self.running = False
            return
        if self.running:
            block = self.radio.block if self.radio is not None else None
            can_hold = hold and hasattr(block, 'hold_open')
            if can_hold:
                block.hold_open = True
            try:
                self.stop()
                self.wait()
            finally:
                if can_hold:
                    block.hold_open = False
            self.running = False

    def _clear(self):
        """Take the stopped chain out. It stays referenced (see the module
        notes), but lets go of the radio, so closing the radio frees it."""
        self.disconnect_all()
        if self.sweeper is not None:
            self.sweeper.detach()
        if self.decoder is not None:
            self.decoder.close()                 # rtl_433 ends with its chain
        for chain in (self.rx, self.sweeper, self.player, self.decoder):
            if chain is not None:
                self._retired.append(chain)
        del self._retired[:-self.RETIRED_KEPT]
        self.rx = None
        self.sweeper = None
        self.player = None
        self.decoder = None
        self.mode = None

    def _started(self):
        try:
            self.start()
        except Exception:
            # A BB60D opens its device in start(), so a missing or busy one
            # fails here; leave the flowgraph stopped, not half started.
            try:
                self.stop()
                self.wait()
            except Exception:
                pass
            raise
        self.running = True
        self._since = time.monotonic()
        # SoapyHackRF ignores the preamp if it is set before the stream runs.
        if self.radio is not None:
            self.radio.apply_gain()

    # ---------------------------------------------------------- lost radio
    def data_age(self):
        """Seconds since the radio last sent anything, counted from the
        start of this run; None when nothing is expected (stopped, paused,
        no radio). A native sweep reports its own data; a paused one sends
        none, so the count starts again when it resumes."""
        if not self.running or self.radio is None \
                or self.mode not in ('receive', 'sweep'):
            return None
        now = time.monotonic()
        s = self.sweeper
        if self.mode == 'sweep' and getattr(s, 'native', False):
            if s.paused:
                self._since = now
                return None
            last = s.last_data
        else:
            last = self._clock.last
        since = self._since if self._since is not None else now
        return now - max(since, last if last is not None else since)

    def lost(self):
        """Why the radio is gone, when it knows for sure, else None: the
        IQ stream's radio (``Radio.lost``: an RTL-SDR's closed connection,
        the BB60D's driver reporting connection issues) or a radio's own
        sweep (its ``lost()``: the HackRF's stream stopped). The window
        says so at once."""
        if self.radio is None:
            return None
        reason = self.radio.lost()
        s = self.sweeper
        if reason is None and self.mode == 'sweep' and getattr(s, 'native', False):
            lost = getattr(s, 'lost', None)
            reason = lost() if lost is not None else None
        return reason

    def lost_reason(self):
        """Why the radio stopped sending, as far as anything says: what
        :meth:`lost` knows, else a native sweep's last error (which may be
        one it recovers from, so it is only shown once the samples stop)."""
        reason = self.lost()
        if reason is None and self.mode == 'sweep' and getattr(self.sweeper, 'native', False):
            reason = self.sweeper.error
        return reason

    # ---------------------------------------------------------- playback
    def start_wav(self, source, volume=0.5, muted=False):
        """Play a ``dsp.wav_source``. There is no radio: close it first."""
        self.halt()
        self._clear()
        self.player = WavChain(self, source, volume=volume, muted=muted,
                               audio_sink=self._audio())
        self.rate = AUDIO_RATE
        self.mode = 'wav'
        self._started()

    def pause(self):
        """Stop the flowgraph and keep it as it is, to :meth:`resume`."""
        self.halt()

    def resume(self):
        if self.running or self.mode is None:
            return
        if self.rx is not None:
            self.rx.restarted()
        self._started()

    def _audio(self):
        """The sound card's block, made once and reused across rebuilds - a
        second one open on the same device can be refused."""
        if not self.want_audio or self.audio_error:
            return None
        if self._audio_sink is None:
            try:
                self._audio_sink = audio.sink(AUDIO_RATE, '', True)
            except Exception as exc:
                self.audio_error = str(exc)
                print(f"FM receiver: no audio output ({exc})", file=sys.stderr)
                return None
        return self._audio_sink

    # ---------------------------------------------------------- receive
    def start_receive(self, station_hz, rate, center_hz=None, decode=None, **settings):
        """Build the receive chain for ``station_hz`` at IQ rate ``rate``
        and start. The LO stays at ``center_hz`` if the station fits in the
        band around it, else it is placed for the station. ``settings`` go
        to :class:`ReceiveChain`. ``decode``, (rtl_433's path, its extra
        arguments, a width), passes rtl_433 the band too: with
        ``rtl433.WHOLE_BAND``, in slices (:func:`rtl433.band_slices`), else
        one slice that wide at the tuner. If rtl_433 cannot start,
        ``decode_error`` says why and receiving goes on without it."""
        self.halt(hold=True)
        self._clear()
        radio = self.radio
        radio.ensure_open()                 # after a HackRF's own sweep
        radio.set_rate(rate)
        self.rate = float(radio.rate or rate)
        self.lo_hz, self.offset_hz = radio.tune_plan(station_hz, self.rate, center_hz)
        radio.set_center(self.lo_hz)
        self.station_hz = self.lo_hz + self.offset_hz
        self.rx = ReceiveChain(self, radio.block, self.rate, self.offset_hz,
                               audio_sink=self._audio(), **settings)
        self.decode_error = None
        if decode is not None:
            program, extra_args, width = decode
            whole = width == rtl433.WHOLE_BAND
            self.decoder = rtl433.DecodeChain(
                self, radio.block, self.rate, self.lo_hz, None,
                offset_hz=self.offset_hz, width_hz=width,
                band=rtl433.band_slices(radio, self.rate) if whole else None,
                dc_notch_hz=getattr(radio, 'dc_notch_hz', 0.0))
            try:
                self.decoder.launch(program, extra_args)
            except Exception as exc:
                self.decode_error = str(exc) or type(exc).__name__
        self.connect(radio.block, self._clock)
        self.mode = 'receive'
        try:
            self._started()
        except Exception:
            if self.decoder is not None:
                self.decoder.close()
            raise

    def tune(self, station_hz, follow=False):
        """Retune in Receive. The tuner stays inside the band around the LO,
        stopping at its edge - only :meth:`set_center` moves the band -
        unless ``follow``: then a station outside the band moves the LO to
        it. Returns True if the LO moved."""
        station_hz = float(station_hz)
        if self.mode != 'receive':
            self.station_hz = station_hz
            return False
        if follow:
            lo, offset = self.radio.tune_plan(station_hz, self.rate, self.lo_hz)
        else:
            direction = (station_hz > self.station_hz) - (station_hz < self.station_hz)
            lo = self.lo_hz
            offset = self.radio.clamp_offset(station_hz - lo, self.rate, lo, direction)
        return self._retune(lo, offset)

    def set_center(self, center_hz):
        """Move the LO. The tuner stays where it is if the band still holds
        it, else it is pulled in to the nearer edge. Returns True if the LO
        moved."""
        low, high = self.radio.freq_range_hz
        lo = min(max(float(center_hz), low), high)
        offset = self.radio.clamp_offset(self.station_hz - lo, self.rate, lo)
        return self._retune(lo, offset)

    def tuner_range(self):
        """(lowest, highest) the tuner can go without the LO moving."""
        edge = self.radio.max_offset(self.rate)
        low, high = self.radio.freq_range_hz
        return max(self.lo_hz - edge, low), min(self.lo_hz + edge, high)

    def _retune(self, lo, offset):
        moved = abs(lo - self.lo_hz) > 0.5
        if moved:
            self.radio.set_center(lo)
            self.lo_hz = lo

        self.offset_hz = offset
        self.station_hz = lo + offset
        self.rx.set_offset(offset)
        if self.decoder is not None:
            self.decoder.retune(lo, offset)      # rtl_433's slices follow
        self.rx.discard_stale(self.radio.block, moved, self.radio.settle_ms / 1e3)
        return moved

    # ---------------------------------------------------------- sweep
    def start_sweep(self, plan, frames=16, settle_ms=None):
        """Sweep ``plan``: a :class:`sweep.SweepPlan`, which hops the IQ
        stream's LO with a flowgraph, or a plan the radio sweeps itself
        (``plan.native``, the BB60D), with none."""
        self.halt(hold=True)
        self._clear()
        radio = self.radio
        if getattr(plan, 'native', False):
            self.sweeper = radio.native_sweeper(plan)
            self.rate = None
            self.lo_hz = None
            self.mode = 'sweep'
            self.running = True
            self._since = time.monotonic()
            return
        radio.ensure_open()
        radio.set_rate(plan.rate)
        self.rate = plan.rate
        radio.set_center(plan.center(0))
        self.lo_hz = plan.center(0)
        settle = radio.settle_ms if settle_ms is None else settle_ms
        self.sweeper = sweep_sink(plan, radio.set_center, radio.block,
                                  frames=frames, settle_ms=settle)
        self.connect(radio.block, self.sweeper)
        self.connect(radio.block, self._clock)
        self.mode = 'sweep'
        self._started()

