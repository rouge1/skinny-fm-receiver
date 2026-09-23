"""The radios, behind one small interface.

Each :class:`Radio` owns one GNU Radio source block (``.block``) that stays
the same object for as long as that radio is chosen: switching between
Sweep and Receive stops the flowgraph, changes the rate and rebuilds what
is downstream, but never makes a second source. (A HackRF opened twice in
one process is refused; a BB60D is closed in ``stop()`` and reopened in
``start()`` by its own block.)

What each radio brings, and why:

- **Rates.** Receive rates are the IQ bandwidths the receive chain can
  decimate to the 250 kHz MPX rate; sweep rates are the wide ones, for
  covering spectrum fast. The BB60D's rates are its hardware ladder.
- **Usable fraction** of each rate that is flat enough to stitch into a
  sweep. Measured on the BB60D (see ``bb60_source.IQ_BANDWIDTH``); the
  HackRF's baseband filter is set to 75% of the rate.
- **Settle time** after a retune, before sweep samples are believed. The
  BB60D's retune blocks for ~24 ms and its first samples afterwards are
  already at the new frequency (measured), so it needs almost none. The
  HackRF's are a guess from its USB buffering (four 6.5 ms transfers at
  20 MS/s in flight) and are adjustable in the window.
- **DC notch**: a HackRF has a spike at its LO, which a sweep blanks and
  fills from its neighbours. The BB60D has none (measured: centre bin level
  with the floor).

Receive gain goes on after ``tb.start()`` (:meth:`Radio.apply_gain`):
SoapyHackRF ignores the preamp stage set before the stream runs.
"""

import json
import math
import os
import sys

from gnuradio import blocks, gr  # type: ignore

from . import bb60_source as _bb60

# SoapySDR loads its driver modules once, on first use, from the paths it
# knows then. The BB60D's module is a system one, found only through
# SOAPY_SDR_PLUGIN_PATH - so that must be set before anything touches
# SoapySDR. Opening a HackRF first (gr-soapy) and then choosing the BB60D
# once found no BB60D at all in that process.
try:
    _bb60.ensure_plugin_path()
except Exception as _exc:                               # pragma: no cover
    print(f"FM receiver: BB60 plugin path: {_exc}", file=sys.stderr)

#: Tuning range offered in the window, as in the RF bench toolkit's receiver.
FREQ_MIN_HZ = 30e6
FREQ_MAX_HZ = 6000e6


def rx_gain_plan(percent):
    """HackRF: spread 0-100% over preamp, LNA (8 dB steps) and VGA (2 dB).

    Copied from the RF bench toolkit's RDS receiver. ~54 dB (preamp + LNA 16
    + VGA 24) suited a good antenna there; a modest one needed ~68 dB.
    """
    percent = min(max(float(percent), 0.0), 100.0)
    amp = 14.0 if percent >= 20 else 0.0
    total = percent / 100.0 * 102.0
    lna = min(40.0, round(total * 0.45 / 8.0) * 8.0)
    vga = min(62.0, max(0.0, round((total - lna) / 2.0) * 2.0))
    return {'AMP': amp, 'LNA': lna, 'VGA': vga}


class RadioError(RuntimeError):
    """A radio could not be opened; the message is meant for the window."""


class Radio:
    kind = ''
    name = ''
    receive_rates = (2e6,)
    #: Receive rates listed but not usable on this computer, each with why:
    #: the window shows them greyed out, with the reason as a tooltip.
    unavailable_rates = {}
    sweep_rates = (20e6,)
    default_receive_rate = 2e6
    default_sweep_rate = 20e6
    settle_ms = 20.0
    dc_notch_hz = 0.0
    usable = 0.75
    default_gain = 50
    can_sweep = True
    #: Sweeps in the device itself, as a plan from :meth:`native_plan`; and
    #: of those, real time and AGC (the BB60D's).
    has_realtime = False
    has_agc = False
    freq_range_hz = (FREQ_MIN_HZ, FREQ_MAX_HZ)
    #: Offset of the station from the LO when the LO is moved to it: keeps the
    #: station off a HackRF's DC spike. The RF bench toolkit uses the same.
    lo_offset_hz = 300e3
    #: How close to the LO the tuner may go: the DC spike is there.
    min_offset_hz = 100e3
    #: How far inside the usable band's edge the tuner stops: half a channel
    #: and a margin, so the whole channel stays in the flat part.
    EDGE_MARGIN_HZ = 150e3
    #: No overload flag of its own: the window watches its samples for
    #: clipping instead (``dsp.clip_probe``).
    clip_warn = False
    #: Sweeps in the device itself (:meth:`native_sweeper`), rather than by
    #: hopping the IQ stream's LO.
    native_sweep = False

    @classmethod
    def usable_receive_rates(cls):
        """The receive rates that can be chosen here."""
        return tuple(r for r in cls.receive_rates if r not in cls.unavailable_rates)

    @property
    def sweep_range_hz(self):
        """How far a sweep can go: the tuner's range, unless the radio
        sweeps further than it streams."""
        return self.freq_range_hz

    def __init__(self):
        self.block = None
        self.rate = None
        self.center_hz = None
        self.gain_percent = float(self.default_gain)

    # -- lifecycle
    def open(self):
        raise NotImplementedError

    def close(self):
        release = getattr(self.block, 'release', None)
        if release is not None:
            release()
        self.block = None

    # -- settings
    def set_rate(self, rate):
        self.rate = float(rate)

    def set_center(self, hz):
        self.center_hz = float(hz)

    def apply_gain(self, percent=None):
        if percent is not None:
            self.gain_percent = float(percent)

    def usable_fraction(self, rate):
        return self.usable

    def max_offset(self, rate):
        """How far from the LO the tuner may go at ``rate``, to the kHz."""
        edge = rate * self.usable_fraction(rate) / 2 - self.EDGE_MARGIN_HZ
        return max(0.0, math.floor(edge / 1e3) * 1e3)

    def tune_plan(self, station_hz, rate, lo_hz=None):
        """Where to put the LO for ``station_hz``, and the station's offset
        from it. If the station already fits in the band around ``lo_hz``
        (clear of the edges and the DC spike), the LO stays put: no hardware
        retune, and the RF view does not jump."""
        if lo_hz is not None:
            offset = station_hz - lo_hz
            if self.min_offset_hz <= abs(offset) <= self.max_offset(rate):
                return lo_hz, offset
        return station_hz - self.lo_offset_hz, self.lo_offset_hz

    def clamp_offset(self, offset_hz, rate, lo_hz, direction=0):
        """The nearest offset to ``offset_hz`` the tuner may have with the LO
        at ``lo_hz``: inside the band, inside the radio's range, and clear of
        the DC spike - passed over in the ``direction`` the tuner is going
        (+1 up, -1 down), else to the nearer side."""
        edge = self.max_offset(rate)
        low, high = self.freq_range_hz
        offset = min(max(offset_hz, -edge, low - lo_hz), edge, high - lo_hz)
        if abs(offset) < self.min_offset_hz:
            side = direction or (1 if offset >= 0 else -1)
            offset = side * self.min_offset_hz
            if abs(offset) > edge:
                offset = -offset
        return offset

    def health(self):
        """Counters worth showing: samples dropped, input overloaded."""
        return {}

    def ensure_open(self):
        """Open the IQ stream's block again if a sweep of the radio's own
        closed it (the HackRF's)."""
        if self.block is None:
            self.open()

    def describe(self):
        return self.name


class HackRF(Radio):
    kind = 'hackrf'
    name = 'HackRF One'
    receive_rates = (2e6, 4e6, 5e6, 8e6, 10e6, 20e6)
    sweep_rates = (8e6, 10e6, 20e6)
    default_receive_rate = 2e6
    default_sweep_rate = 20e6
    #: Measured off air (tests/hw_hackrf_check.py, 2026-09-22): ghosts at 0
    #: and 5 ms, none from 10 ms. Twice that, for margin.
    settle_ms = 20.0
    dc_notch_hz = 30e3
    usable = 0.75
    clip_warn = True
    #: Measured best on the bench antenna: 40% gave 99% RDS blocks and 43 dB
    #: SNR on 89.3; 54% clipped (channel +2 dBFS) and RDS was lost.
    default_gain = 40
    freq_range_hz = (1e6, 6000e6)
    #: Sweeps with its firmware (``hackrf_sweep``): the IQ stream's device is
    #: closed while it does, and opened again for Receive.
    native_sweep = True
    sweep_range_hz = (1e6, 6000e6)
    sweeper = None

    def open(self):
        from gnuradio import soapy  # type: ignore
        try:
            self.block = soapy.source('driver=hackrf', 'fc32', 1, '', '',
                                      [''], [''])
        except Exception as exc:
            raise RadioError(
                "No HackRF One could be opened. Check the USB cable, and "
                "that nothing else - hackrf_transfer, GQRX, another receiver "
                f"window - has it open.\n\n({exc})") from exc
        self.block.set_gain_mode(0, False)

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.set_sample_rate(0, self.rate)
        # The baseband filter at 75% of the rate, which is what the usable
        # fraction assumes; the driver rounds to the filter it has.
        self.block.set_bandwidth(0, 0.75 * self.rate)

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_frequency(0, self.center_hz)

    def apply_gain(self, percent=None):
        super().apply_gain(percent)
        if self.sweeper is not None and self.sweeper.running:
            self.sweeper.set_gain_percent(self.gain_percent)
        if self.block is not None:
            for name, value in rx_gain_plan(self.gain_percent).items():
                self.block.set_gain(0, name, value)

    def native_plan(self, start_hz, stop_hz, rbw_hz=None, **_view):
        from .hackrf_sweep import HackRFSweepPlan
        return HackRFSweepPlan(start_hz, stop_hz, rbw_hz)

    def native_sweeper(self, plan):
        """Close the IQ stream's device and sweep with the firmware; the
        sweep is returned, running. Receive opens the stream again
        (:meth:`ensure_open`), once the engine has stopped this."""
        import gc
        from .hackrf_sweep import HackRFError, hackrf_sweeper
        self.sweeper = None
        self.block = None
        gc.collect()                            # the SoapySDR device, closed
        sweeper = hackrf_sweeper(plan, rx_gain_plan, self.gain_percent)
        try:
            sweeper.start()
        except (HackRFError, OSError) as exc:
            raise RadioError(str(exc)) from exc
        self.sweeper = sweeper
        return sweeper

    def close(self):
        if self.sweeper is not None:
            self.sweeper.stop()
            self.sweeper = None
        super().close()


class USRP(Radio):
    kind = 'usrp'
    name = 'Ettus USRP'
    clip_warn = True
    receive_rates = (2e6, 2.5e6, 5e6, 10e6)
    sweep_rates = (10e6, 20e6, 25e6)
    default_receive_rate = 2.5e6
    default_sweep_rate = 20e6
    settle_ms = 5.0
    dc_notch_hz = 10e3
    usable = 0.8
    default_gain = 50

    def __init__(self, address=''):
        super().__init__()
        self.address = address.strip()

    def describe(self):
        return f"{self.name} ({self.address or 'first found'})"

    def open(self):
        from gnuradio import uhd  # type: ignore
        args = f"addr={self.address}" if self.address else ''
        try:
            self.block = uhd.usrp_source(
                args, uhd.stream_args(cpu_format='fc32', args='',
                                      channels=[0]))
            self.block.set_antenna('RX2', 0)
        except Exception as exc:
            where = f"at {self.address}" if self.address else 'on the network or USB'
            raise RadioError(f"No USRP answered {where}.\n\n({exc})") from exc

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.set_samp_rate(self.rate)

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center_freq(self.center_hz, 0)

    def apply_gain(self, percent=None):
        super().apply_gain(percent)
        try:
            rng = self.block.get_gain_range(0)
            low, high = float(rng.start()), float(rng.stop())
        except Exception:
            low, high = 0.0, 31.5
        self.block.set_gain(low + (high - low) * self.gain_percent / 100.0, 0)


class BB60(Radio):
    kind = 'bb60'
    name = 'Signal Hound BB60D'
    #: 20 and 40 MS/s measured off air (2026-09-22, 89.3 MHz, 12 s each):
    #: no samples lost, RDS 548/548 and 552/552, CPU 64% and 84% of a core
    #: against 57% at 10. 40 shows 24 MHz of the band flat (27 MHz filter).
    receive_rates = (2.5e6, 5e6, 10e6, 20e6, 40e6)
    if sys.platform == 'darwin':
        # Signal Hound's Mac library (5.0.11) streams garbage at a
        # decimation of 16 or more - 2.5 MS/s and below: on the Mac mini
        # (2026-09-22), levels a thousand times too high, runs of NaN, values
        # to 1e37, differently each time. 5 MS/s and up were sound every time.
        unavailable_rates = {2.5e6: "Not on a Mac: Signal Hound's library for it gives\n"
                                    "unreliable IQ at 2.5 MS/s. 5 MS/s and up are fine."}
    sweep_rates = (10e6, 20e6, 40e6)
    #: 10 MS/s: CPU and reception measured the same at all three (see
    #: knowledge/roadmap.md, item 6), and it shows four times the band.
    default_receive_rate = 10e6
    default_sweep_rate = 20e6
    settle_ms = 1.0
    dc_notch_hz = 0.0
    #: No DC spike to keep off: the BB60D samples at IF.
    min_offset_hz = 0.0
    # 60%: attenuator open, no RF gain - the RF bench toolkit's measured best
    # for FM, where more gain overloads the front end with every other station.
    default_gain = 60
    freq_range_hz = (FREQ_MIN_HZ, 6000e6)
    #: Measured flat to 0.5 dB - see bb60_source.IQ_BANDWIDTH.
    USABLE = {40e6: 0.60, 20e6: 0.84, 10e6: 0.75, 5e6: 0.75, 2.5e6: 0.75}
    #: Sweeps in the device (``bb60_sweep``), over the whole of its range
    #: from 9 kHz - the IQ stream is kept to the tuner's range.
    native_sweep = True
    #: Real time and AGC in its own sweep (``bb60_sweep``).
    has_realtime = True
    has_agc = True
    sweep_range_hz = (9e3, 6000e6)
    sweeper = None

    def open(self):
        from .bb60_source import bb60_source, find_devices
        _load_bb60_module()
        if not find_devices():
            raise RadioError(
                "No Signal Hound BB60 was found on USB. Check it is "
                "connected, and that no other program - Spike, another "
                "receiver - has it open.")
        # The device itself is opened by the block's start(), or hold().
        self.block = bb60_source(center_freq=100e6, sample_rate=2.5e6,
                                 gain_percent=self.gain_percent)

    def close(self):
        self.sweeper = None
        super().close()

    def usable_fraction(self, rate):
        return self.USABLE.get(float(rate), 0.75)

    def native_plan(self, start_hz, stop_hz, rbw_hz=None, **view):
        from .bb60_sweep import NativeSweepPlan
        return NativeSweepPlan(start_hz, stop_hz, rbw_hz, **view)

    def native_sweeper(self, plan):
        """Start the device's own sweep of ``plan`` (a
        ``bb60_sweep.NativeSweepPlan``) on the device the IQ stream opens,
        which must be stopped; the sweep is returned, running."""
        from .bb60_sweep import BB60Error, bb60_sweeper, device_handle
        try:
            self.block.hold()
        except RuntimeError as exc:
            raise RadioError(str(exc)) from exc
        handle = device_handle()
        if handle is None:
            raise RadioError("The BB60 is open, but its handle could not be found.")
        sweeper = bb60_sweeper(handle, plan, self.gain_percent)
        try:
            sweeper.start()
        except BB60Error as exc:
            raise RadioError(str(exc)) from exc
        self.sweeper = sweeper
        return sweeper

    def set_rate(self, rate):
        super().set_rate(rate)
        self.block.set_sample_rate(self.rate)

    def set_center(self, hz):
        super().set_center(hz)
        self.block.set_center_freq(self.center_hz)

    def apply_gain(self, percent=None):
        super().apply_gain(percent)
        self.block.set_gain_percent(self.gain_percent)
        if self.sweeper is not None and self.sweeper.running:
            self.sweeper.set_gain_percent(self.gain_percent)

    def health(self):
        if self.block is None:
            return {}
        swept = self.sweeper.overflows if self.sweeper is not None else 0
        health = {'dropped': self.block.overflows,
                  'overload': self.block.adc_overflows() + swept}
        try:
            from .bb60_sweep import diagnostics
            health.update(diagnostics() or {})
        except Exception:                        # a reading, never a failure
            pass
        return health


def _load_bb60_module():
    """Load the BB60 SoapySDR module, in case SoapySDR started without it
    (something used SoapySDR before the plugin path was set).

    ``SoapySDR.listModules()`` cannot tell: it lists the module *files* on the
    search path as it is now, not the modules loaded - measured, it names the
    BB60 module while enumerate still finds no BB60. So this always calls
    ``loadModules``, which loads every module on the search path as it is
    now and passes over those already in.

    Not ``loadModule`` on the BB60's file alone: in a process that had not
    used SoapySDR yet, that was the only module ever loaded - SoapySDR's own
    load on the first enumerate never came - so the HackRF chosen after a
    BB60D that was not plugged in was "no match" (2026-09-22).
    """
    try:
        import SoapySDR  # type: ignore
        _bb60.ensure_plugin_path()
        SoapySDR.loadModules()
    except Exception as exc:
        print(f"FM receiver: could not load the BB60 module: {exc}", file=sys.stderr)


# ---------------------------------------------------------------- IQ files

def read_iq_metadata(path):
    """Find an IQ recording's data file, rate and centre frequency.

    Accepts any of the files a recording is made of: SigMF
    (``.sigmf-meta`` / ``.sigmf-data``) or the RF bench toolkit's capture
    format (``.cfile`` with a ``.json`` sidecar holding ``rate``,
    ``offset_hz`` and ``station_hz``). This app writes both for every IQ
    recording. Only complex float32 is read.
    """
    base, ext = os.path.splitext(path)
    meta_file = None
    if ext in ('.sigmf-meta', '.sigmf-data'):
        meta_file = base + '.sigmf-meta'
    elif os.path.exists(base + '.sigmf-meta'):
        meta_file = base + '.sigmf-meta'
    if meta_file and os.path.exists(meta_file):
        with open(meta_file) as fh:
            meta = json.load(fh)
        glob_ = meta.get('global', {})
        dtype = glob_.get('core:datatype', 'cf32_le')
        if dtype not in ('cf32_le', 'cf32'):
            raise RadioError(f"{os.path.basename(meta_file)} holds {dtype}; "
                             "only complex float32 (cf32_le) can be played.")
        data = glob_.get('core:dataset')
        data = (os.path.join(os.path.dirname(meta_file), data) if data
                else base + '.sigmf-data')
        captures = meta.get('captures') or [{}]
        centre = float(captures[0].get('core:frequency', 0.0))
        station = glob_.get('fmrx:station_frequency')
        return {'data': data, 'rate': float(glob_['core:sample_rate']),
                'center_hz': centre,
                'station_hz': float(station) if station else None}
    sidecar = base + '.json'
    if os.path.exists(sidecar):
        with open(sidecar) as fh:
            meta = json.load(fh)
        data = base + '.cfile' if ext in ('.json', '') else path
        station = float(meta.get('station_hz', 0.0))
        offset = float(meta.get('offset_hz', 0.0))
        return {'data': data, 'rate': float(meta['rate']),
                'center_hz': float(meta.get('center_hz', station - offset)),
                'station_hz': station or None}
    raise RadioError(f"No metadata beside {os.path.basename(path)}: "
                     "expected a .sigmf-meta, or a .json sidecar with "
                     "rate, offset_hz and station_hz.")


class _file_source(gr.hier_block2):
    def __init__(self, data, rate, repeat=True, throttle=True):
        gr.hier_block2.__init__(self, 'iq_file_source',
                                gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.file = blocks.file_source(gr.sizeof_gr_complex, data, repeat)
        if throttle:
            self.throttle = blocks.throttle(gr.sizeof_gr_complex, rate, True)
            self.connect(self.file, self.throttle, self)
        else:
            self.connect(self.file, self)


class IQFile(Radio):
    """A recording played back as if it were a radio: in real time, looped.

    The LO is wherever the recording's was, so tuning moves only the channel
    within the recorded band. It cannot sweep.

    **Where it is**, for the Recordings tab: the file source reads ahead,
    so the place is counted where the samples leave the throttle - the
    real-time pace - from where the file was put (:meth:`place`) or last
    jumped to (:meth:`seek`). A block's counters start from zero each time
    the flowgraph starts, and must not be read before it first has: hence
    :meth:`counting`.
    """
    kind = 'file'
    name = 'IQ recording'
    can_sweep = False
    settle_ms = 0.0
    default_gain = 0
    min_offset_hz = 0.0

    def __init__(self, path='', repeat=True, throttle=True):
        super().__init__()
        self.path = path
        self.repeat = repeat
        self.throttle = throttle
        self.meta = None

    def describe(self):
        return f"IQ file: {os.path.basename(self.path) or '(none chosen)'}"

    def open(self):
        if not self.path:
            raise RadioError("Choose an IQ recording to play (File...).")
        try:
            self.meta = read_iq_metadata(self.path)
        except RadioError:
            raise
        except Exception as exc:
            raise RadioError(f"Could not read {self.path}: {exc}") from exc
        if not os.path.exists(self.meta['data']):
            raise RadioError(f"The data file {self.meta['data']} is missing.")
        rate = self.meta['rate']
        self.receive_rates = (rate,)
        self.sweep_rates = ()
        self.default_receive_rate = rate
        self.rate = rate
        self.center_hz = self.meta['center_hz']
        self.freq_range_hz = (self.center_hz - rate / 2 + 120e3,
                              self.center_hz + rate / 2 - 120e3)
        self.block = _file_source(self.meta['data'], rate, self.repeat,
                                  self.throttle)
        self.total = max(1, os.path.getsize(self.meta['data']) // gr.sizeof_gr_complex)
        self._base = 0
        self._counting = False

    def set_rate(self, rate):
        pass                                   # fixed by the recording

    def set_center(self, hz):
        pass                                   # fixed by the recording

    def tune_plan(self, station_hz, rate, lo_hz=None):
        return self.center_hz, self.clamp_offset(station_hz - self.center_hz, rate,
                                                 self.center_hz)

    def usable_fraction(self, rate):
        return 0.9

    # -- the place in the file
    def _count(self):
        inner = self.block.throttle if self.throttle else self.block.file
        return inner.nitems_written(0)

    def place(self, sample):
        """Put the file at ``sample``, while the flowgraph is stopped."""
        self._base = int(min(max(sample, 0), self.total - 1))
        self._counting = False
        self.block.file.seek(self._base, 0)

    def counting(self, running):
        """The flowgraph has just started (True), or is about to stop."""
        if not running:
            self._base = self.position()
        self._counting = bool(running)

    def seek(self, sample):
        """Jump to ``sample``, running or not."""
        if not self._counting:
            self.place(sample)
            return
        sample = int(min(max(sample, 0), self.total - 1))
        self.block.file.seek(sample, 0)
        self._base = sample - self._count()

    def played(self):
        """Samples played since the file's start, counting every loop."""
        return self._base + (self._count() if self._counting else 0)

    def position(self):
        return self.played() % self.total


RADIO_KINDS = {'hackrf': HackRF, 'usrp': USRP, 'bb60': BB60, 'file': IQFile}
RADIO_NAMES = {'hackrf': 'HackRF One', 'usrp': 'Ettus USRP',
               'bb60': 'Signal Hound BB60D', 'file': 'IQ recording (playback)'}


def make_radio(kind, usrp_address='', file_path=''):
    if kind == 'usrp':
        return USRP(usrp_address)
    if kind == 'file':
        return IQFile(file_path)
    return RADIO_KINDS.get(kind, HackRF)()


def detect_radios():
    """The radio kinds plugged in now, cheapest checks only (USB, SoapySDR).
    A USRP on the network is not looked for: that takes seconds."""
    found = []
    try:
        from .bb60_source import find_devices
        if find_devices():
            found.append('bb60')
    except Exception as exc:
        print(f"FM receiver: BB60 check failed: {exc}", file=sys.stderr)
    try:
        import SoapySDR  # type: ignore
        if SoapySDR.Device.enumerate('driver=hackrf'):
            found.append('hackrf')
    except Exception as exc:
        print(f"FM receiver: HackRF check failed: {exc}", file=sys.stderr)
    return found


def rate_label(rate):
    return f"{rate / 1e6:g} MS/s"

