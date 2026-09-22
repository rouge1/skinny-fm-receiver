"""The flowgraph: one radio, and either the sweep or the receive chain.

Sweep and Receive want the radio at different rates and share nothing
downstream of it, so switching mode stops the flowgraph, disconnects
everything, sets the rate and builds the other chain onto the same source
block. It takes 0.2-0.3 s on a BB60D (measured), which keeps its device
open across the switch (``halt(hold=True)``) - reopening it was 1.6 s. What
must *not* rebuild - retuning, channel bandwidth, volume, mute, stereo,
region, recording - is done on the running blocks.

Rules carried over from the RF bench toolkit:

- **Receive gain goes on after** ``start()`` (:meth:`Engine._started`).
- **Every Python block stays referenced** while it might be running; old
  chains are kept in ``_retired`` rather than dropped the moment they are
  disconnected.
"""

import sys

from gnuradio import audio, gr  # type: ignore

from .dsp import AUDIO_RATE, ReceiveChain
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
        self.rate = None
        self.lo_hz = None
        self.offset_hz = None
        self.station_hz = None
        self.audio_error = None
        self._audio_sink = None
        self._retired = []

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
        between a stop and a start (the BB60D) open, for a mode switch."""
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
        for chain in (self.rx, self.sweeper):
            if chain is not None:
                self._retired.append(chain)
        del self._retired[:-self.RETIRED_KEPT]
        self.rx = None
        self.sweeper = None
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
        # SoapyHackRF ignores the preamp if it is set before the stream runs.
        self.radio.apply_gain()

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
    def start_receive(self, station_hz, rate, center_hz=None, **settings):
        """Build the receive chain for ``station_hz`` at IQ rate ``rate``
        and start. The LO stays at ``center_hz`` if the station fits in the
        band around it, else it is placed for the station. ``settings`` go
        to :class:`ReceiveChain`."""
        self.halt(hold=True)
        self._clear()
        radio = self.radio
        radio.set_rate(rate)
        self.rate = float(radio.rate or rate)
        self.lo_hz, self.offset_hz = radio.tune_plan(station_hz, self.rate, center_hz)
        radio.set_center(self.lo_hz)
        self.station_hz = self.lo_hz + self.offset_hz
        self.rx = ReceiveChain(self, radio.block, self.rate, self.offset_hz,
                               audio_sink=self._audio(), **settings)
        self.mode = 'receive'
        self._started()

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
        self.rx.discard_stale(self.radio.block, moved, self.radio.settle_ms / 1e3)
        return moved

    # ---------------------------------------------------------- sweep
    def start_sweep(self, plan, frames=16, settle_ms=None):
        self.halt(hold=True)
        self._clear()
        radio = self.radio
        radio.set_rate(plan.rate)
        self.rate = plan.rate
        radio.set_center(plan.center(0))
        self.lo_hz = plan.center(0)
        settle = radio.settle_ms if settle_ms is None else settle_ms
        self.sweeper = sweep_sink(plan, radio.set_center, radio.block,
                                  frames=frames, settle_ms=settle)
        self.connect(radio.block, self.sweeper)
        self.mode = 'sweep'
        self._started()
