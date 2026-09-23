#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Signal Hound BB60D as a live GNU Radio source.

Copied from the RF bench toolkit (``/data/python/SDR/apps/bb60_source.py``,
commit 20c76e4). Seven changes for this app, all marked "FM receiver": the
analog bandwidth is set with every rate (see :data:`IQ_BANDWIDTH`),
``start()`` drops settings left pending from before it,
:attr:`bb60_source.hold_open` keeps the device open across a stop and start,
:meth:`bb60_source.hold` opens it with no stream, for ``bb60_sweep``, it
finds the module and the SoapySDR libraries on a Mac as well, and on a Mac
it never closes the device (:data:`KEEP_OPEN`), and :meth:`bb60_source.lost`
says when the device has gone.

The BB60D is a SoapySDR device, but it cannot be driven through
``gr-soapy``: ``soapy.source(...)`` constructs, and then *every* setter -
``set_frequency``, ``set_gain``, ``set_sample_rate`` - fails with
``setupStream: Invalid format ''``. So this wraps the raw SoapySDR Python
binding as a ``gr.sync_block`` instead, in the same spirit as
``apps/vsg_sink.py`` wrapping the VSG's vendor C API.

Four things about this device that are not like the others:

- **``setupStream`` comes before configuration, not after.** Set the rate or
  the frequency on a device whose stream has not been set up and the module
  reports the format as empty and nothing works afterwards.
- **It is opened by driver name alone.** ``driver=SignalHoundBB60`` opens
  it; the same arguments ``enumerate()`` hands back - serial, label,
  device_id - are refused, one with "no match" and one with "device_id is
  not a number".
- **Its module is a system one.** It lives in ``/usr/local/lib/SoapySDR``
  rather than inside the conda environment, so ``SOAPY_SDR_PLUGIN_PATH``
  has to point at it. The ABI matches (both 0.8), so the conda binding
  loads the system module quite happily once it can find it. (FM
  receiver: on a Mac it is built into the conda environment instead, where
  SoapySDR looks anyway - see ``knowledge/usage.md``.)
- **Its rates are a ladder**: 40, 20, 10, 5, 2.5 MS/s and on down in
  halves. Nothing in between, so a flowgraph picks one off the ladder and
  resamples. At 10 MS/s the analog filter is 8 MHz, which is what makes it
  usable for a 6 MHz television channel.

Gain is two elements, an attenuator and an RF stage, presented as one
0-100% control like every other radio here.
"""

import ctypes
import glob
import os
import re
import sys
import threading
import time

import numpy as np
from gnuradio import gr  # type: ignore

DRIVER = 'SignalHoundBB60'

#: FM receiver: on a Mac the device is never closed. Signal Hound's Mac
#: library (5.0.11) crashes in ``bbCloseDevice`` - EXC_BREAKPOINT (SIGTRAP)
#: in macOS's crash report - on every device it has opened, even straight
#: after ``bbOpenDevice``, so the module's destructor, which calls it, must
#: never run. The device is opened once, marked closed for
#: SoapySDR's ``close()`` (which then leaves it alone), kept here for the
#: life of the process, and handed to every later open; macOS lets it go
#: when the app quits. Meanwhile ``enumerate()`` no longer lists it, so
#: :func:`find_devices` adds it back.
KEEP_OPEN = sys.platform == 'darwin'
_kept = None            # (the SoapySDR device, what find_devices said of it)
#: The rates the hardware actually has. Anything else is refused.
SAMPLE_RATES = [40e6, 20e6, 10e6, 5e6, 2.5e6, 1.25e6, 625e3, 312.5e3]
#: Gain elements, in the order the slider should open them up: take the
#: attenuator off first, because attenuation costs noise figure outright,
#: and only then start adding RF gain.
GAIN_STAGES = [('ATT', -30.0, 0.0), ('RF', 0.0, 20.0)]
GAIN_SPAN = sum(high - low for _, low, high in GAIN_STAGES)   # 50 dB

#: FM receiver: the widest analog filter each rate allows, set with the rate.
#: **The module keeps the last bandwidth across a rate change** - measured:
#: 17.8 MHz set at 20 MS/s stayed 17.8 at 40 MS/s, where 27 is possible, so a
#: sweep at 40 saw a third of its span as nothing. Measured flat (to 0.5 dB)
#: to 75% of the span at 2.5 and 10 MS/s, 84% at 20 and 60% at 40 with 27 MHz.
IQ_BANDWIDTH = {40e6: 27e6, 20e6: 17.8e6, 10e6: 8e6, 5e6: 3.75e6,
                2.5e6: 2e6, 1.25e6: 1e6, 625e3: 500e3, 312.5e3: 250e3}

# Messages the vendor module prints on every ordinary retune. They are
# logged at ERROR, which they are not - the frequency reads back correctly
# afterwards - and they scroll a terminal several lines per second.
_EXPECTED_CHATTER = ('ConfigureIQCenter', 'ConfigureIO', 'Using format',
                     'set decimation', 'deprecrated')
_overflows = [0]
_last_message = ['']
#: FM receiver: the driver's words when the USB connection has gone. Seen
#: on 2026-09-23 with the BB60D unplugged under a stream: "[ERROR] GetIQ:
#: Device connection issues detected" on fd 2 every ~0.25 s (each read's
#: timeout) from the unplug on, and never in 80 s of normal running. It
#: went on after the device was plugged back in: the stream never recovers.
CONNECTION_LOST = 'connection issues'
#: How many such lines since the last ``start()``, and when the last came
#: (``time.monotonic``). The first is passed on to stderr, the rest counted.
_connection_issues = [0, None]


def _log_handler(level, text):
    """Count real problems, drop the noise, pass anything else on.

    The ADC overflowing is the one message that matters and the only sign
    of it: the samples that come back are filtered and decimated, so a
    front end being driven into the converter does *not* show up as
    clipping in what the flowgraph sees. Without this it is an ERROR line
    in a terminal nobody is looking at.
    """
    message = str(text).strip()
    if 'overflow' in message.lower():
        _overflows[0] += 1
        _last_message[0] = message
        return
    if any(k in message for k in _EXPECTED_CHATTER):
        return
    print(f"BB60: {message}", file=sys.stderr)


#: The C callback type, and the callbacks themselves - which must outlive
#: every library they are given to, or the driver calls freed memory.
_C_LOG_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)
_c_handlers = []
_registered = set()


def _c_log_handler(level, message):
    _log_handler(level, message.decode('utf-8', 'replace') if message else '')


def _soapy_libraries():
    """Every libSoapySDR mapped into this process, plus the usual system one.

    There is normally more than one. The BB60 module is a *system* module
    and links against the system ``libSoapySDR``; the Python binding here
    is conda's and carries its own. Both end up in the process.

    FM receiver: a Mac has no ``/proc``; there dyld lists what it loaded.
    """
    paths = []
    try:
        for path in _mapped_files():
            if _SOAPY_LIB.match(os.path.basename(path)) and path not in paths:
                paths.append(path)
    except Exception:
        pass
    for path in ('/lib/x86_64-linux-gnu/libSoapySDR.so.0.8',
                 '/usr/lib/x86_64-linux-gnu/libSoapySDR.so.0.8',
                 '/usr/local/lib/libSoapySDR.so.0.8',
                 '/opt/homebrew/lib/libSoapySDR.0.8.dylib',
                 '/usr/local/lib/libSoapySDR.0.8.dylib'):
        if os.path.exists(path) and path not in paths:
            paths.append(path)
    return paths


#: FM receiver: libSoapySDR.so.0.8 on Linux, libSoapySDR.0.8.dylib on a Mac.
_SOAPY_LIB = re.compile(r'libSoapySDR\.(so|[\d.]*dylib$)')


def _mapped_files():
    """FM receiver: the files this process has loaded, on Linux or a Mac."""
    if sys.platform == 'darwin':
        dyld = ctypes.CDLL(None)
        dyld._dyld_image_count.restype = ctypes.c_uint32
        dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
        dyld._dyld_get_image_name.restype = ctypes.c_char_p
        names = (dyld._dyld_get_image_name(i) for i in range(dyld._dyld_image_count()))
        return [n.decode('utf-8', 'replace') for n in names if n]
    with open('/proc/self/maps') as fh:
        return [line.rstrip().rpartition(' ')[2] for line in fh]


def install_log_handler():
    """Catch the driver's logging, whichever libSoapySDR it goes into.

    **Registering through the Python binding is not enough, and that was
    not obvious.** ``SoapySDR.registerLogHandler`` does work - a message
    logged from Python goes straight to `_log_handler` - but the BB60
    module's own messages came out anyway, in SoapySDR's default format,
    several lines per retune. The reason is that there are two copies of
    the library in the process: the module links against the system
    ``libSoapySDR.so.0.8`` (318 kB, /lib/x86_64-linux-gnu) while the conda
    binding carries its own (629 kB). Each keeps its own handler registry,
    so registering through the binding leaves the one the driver actually
    logs into still holding the default handler.

    That cost more than a tidy terminal. ``adc_overflows`` counts *only*
    what this handler sees, and the converter being overdriven is logged
    and nothing else - the samples come back filtered and decimated, so it
    never shows as clipping. With the messages going elsewhere the count
    stayed at zero however hard the front end was driven, and the ATSC
    receiver's "Input overloaded - turn the RF gain down" could not fire.
    (The separate ``overflows`` counter, for samples actually lost, comes
    from ``readStream``'s return code and was never affected.)

    So the handler goes into every one of them, through the C API. Calling
    this more than once is cheap and is meant to happen: the module - and
    with it the system library - is not loaded until the first enumerate.
    """
    installed = False
    try:
        import SoapySDR  # type: ignore
        SoapySDR.registerLogHandler(_log_handler)
        installed = True
    except Exception:
        pass
    for path in _soapy_libraries():
        if path in _registered:
            continue
        try:
            lib = ctypes.CDLL(path)
            register = lib.SoapySDR_registerLogHandler
            register.argtypes = [_C_LOG_HANDLER]
            register.restype = None
            callback = _C_LOG_HANDLER(_c_log_handler)
            _c_handlers.append(callback)
            register(callback)
            _registered.add(path)
            installed = True
        except Exception:
            _registered.add(path)          # do not keep retrying a bad one
    return installed


_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_LEVEL = re.compile(r'^\[(?:TRACE|DEBUG|INFO|NOTICE|WARNING|ERROR|CRITICAL|'
                    r'FATAL|SSI)\]\s*')


class _DriverOutput:
    """Filter what the BB60 module prints, at the one place it appears.

    **A log handler does not catch it, and finding that out took a while.**
    The module logs through libSoapySDR - the ``[INFO] %s`` formatting on
    its lines comes out of that library's own default handler, not out of
    the module - and `install_log_handler` registers on every copy of the
    library in the process. A message logged through either copy's C API
    does reach `_log_handler`, both of them, checked. And yet during a real
    open and two retunes our handler was called **zero times** while the
    module printed fifteen lines. ``SoapySDR_setLogLevel`` on the other
    hand does silence them, so the level is consulted somewhere the handler
    is not. Whatever the reason, the only place the module's words reliably
    turn up is file descriptor 2.

    That matters beyond a tidy terminal: ``adc_overflows`` counts what the
    handler sees, and an overdriven converter is *only* ever reported in a
    message - the samples come back filtered and decimated, so it never
    shows as clipping. With nothing reaching the handler that count stayed
    at zero however hard the front end was driven, and the ATSC receiver's
    "Input overloaded - turn the RF gain down" could never fire.

    So this takes fd 2 for as long as a BB60 is streaming, drops the
    chatter, counts the overflows and passes everything else - GNU Radio's
    warnings, Python's tracebacks - straight through to the real stderr.
    """

    def __init__(self):
        self._saved = os.dup(2)
        read_fd, write_fd = os.pipe()
        try:
            os.dup2(write_fd, 2)
        finally:
            os.close(write_fd)
        self._read = read_fd
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name='bb60-stderr')
        self._thread.start()

    def _pump(self):
        pending = b''
        while True:
            try:
                chunk = os.read(self._read, 4096)
            except OSError:
                break
            if not chunk:
                break                      # fd 2 restored: nothing can write
            pending += chunk
            while b'\n' in pending:
                line, _, pending = pending.partition(b'\n')
                self._line(line)
        if pending:
            self._line(pending)

    def _line(self, raw):
        try:
            text = raw.decode('utf-8', 'replace')
            plain = _LEVEL.sub('', _ANSI.sub('', text)).strip()
            if 'overflow' in plain.lower():
                _overflows[0] += 1
                _last_message[0] = plain
                return
            if any(k in plain for k in _EXPECTED_CHATTER):
                return
            if CONNECTION_LOST in plain.lower():      # FM receiver: see lost()
                _connection_issues[0] += 1
                _connection_issues[1] = time.monotonic()
                if _connection_issues[0] > 1:
                    return                     # four a second, for ever
            os.write(self._saved, raw + b'\n')
        except Exception:
            # Never let filtering stderr be the thing that breaks a run.
            pass

    def close(self):
        # Putting the real stderr back removes the pipe's last writer, so
        # the pump sees end of file and finishes on its own.
        try:
            os.dup2(self._saved, 2)
        except OSError:
            pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        for fd in (self._read, self._saved):
            try:
                os.close(fd)
            except OSError:
                pass


#: The filter, and how many sources are using it.
_driver_output = [None, 0]


def capture_driver_output():
    """Start filtering the driver's output; safe to call more than once."""
    if _driver_output[0] is None:
        try:
            _driver_output[0] = _DriverOutput()
        except Exception as exc:
            print(f"BB60: could not filter the driver's output: {exc}",
                  file=sys.stderr)
            return False
    _driver_output[1] += 1
    return True


def release_driver_output():
    """Give the real stderr back once the last source has stopped."""
    if _driver_output[0] is None:
        return
    _driver_output[1] -= 1
    if _driver_output[1] <= 0:
        stream, _driver_output[0] = _driver_output[0], None
        _driver_output[1] = 0
        stream.close()


def overflow_count():
    """How many times the converter has been overdriven since reset."""
    return _overflows[0]


def reset_overflows():
    _overflows[0] = 0
    _last_message[0] = ''


def reset_connection_issues():
    """FM receiver: forget the driver's connection-issue lines (a new open)."""
    _connection_issues[0] = 0
    _connection_issues[1] = None

_MODULE_DIRS = [
    '/usr/local/lib/SoapySDR/modules0.8',
    '/usr/lib/x86_64-linux-gnu/SoapySDR/modules0.8',
    '/usr/local/lib/SoapySDR/modules*',
    '/usr/lib/*/SoapySDR/modules*',
    # FM receiver: Homebrew's, on a Mac.
    '/opt/homebrew/lib/SoapySDR/modules0.8',
]


def ensure_plugin_path():
    """Put the system SoapySDR module directory on the plugin path.

    SoapySDR reads ``SOAPY_SDR_PLUGIN_PATH`` when it first loads modules,
    which is on the first enumerate, so setting it here is in time even
    though ``SoapySDR`` may already be imported. Whatever is already in the
    variable is kept ahead of what we add.
    """
    paths = [p for p in os.environ.get('SOAPY_SDR_PLUGIN_PATH', '').split(':')
             if p]
    for pattern in _MODULE_DIRS:
        for path in sorted(glob.glob(pattern), reverse=True):
            # The patterns deliberately overlap - an exact directory and a
            # glob that also matches it - so duplicates have to be dropped
            # here, or the variable grows every time this is called.
            if (path not in paths and os.path.isdir(path)
                    and glob.glob(os.path.join(path, '*BB60*'))):
                paths.append(path)
    if paths:
        os.environ['SOAPY_SDR_PLUGIN_PATH'] = ':'.join(paths)
    return paths


def gain_plan(percent):
    """Map 0-100% onto the attenuator and the RF stage, in that order.

    **The level falls as this rises, and that is correct.** Measured on a
    real broadcaster against an empty channel, which is the only way to see
    it - raw level says the opposite:

    ======  ====  ==========  ==========
    ATT     RF    level       SNR
    ======  ====  ==========  ==========
    -30     0     -56.5 dBFS   -0.1 dB
    -20     0     -61.4 dBFS    0.0 dB
    -10     0     -69.9 dBFS    1.2 dB
      0     0     -74.0 dBFS    4.7 dB
      0    20     -74.5 dBFS    6.6 dB
    ======  ====  ==========  ==========

    Winding the attenuator stage negative adds 18 dB of level and *all* of
    it is noise. So the slider opens the attenuator toward 0 first and only
    then adds RF, which makes it monotone in signal-to-noise even though
    the input level meter goes the other way.

    The last 20 dB of RF is worth under 2 dB of SNR and is front-end
    amplification, which is what overdrives the converter on a strong local
    signal - so a high setting is the first thing to wind back if
    ``overflow_count()`` starts climbing, and it costs almost nothing.
    """
    percent = min(max(float(percent), 0.0), 100.0)
    budget = percent / 100.0 * GAIN_SPAN
    plan = {}
    for name, low, high in GAIN_STAGES:
        take = min(high - low, budget)
        plan[name] = low + take
        budget -= take
    return plan


def nearest_rate(rate):
    """The rate on the hardware's ladder closest to what was asked for."""
    return min(SAMPLE_RATES, key=lambda r: abs(r - float(rate)))


def find_devices():
    """Every BB60 the machine can see, as dicts. Empty if none or no module.

    ``enumerate`` hands back ``SoapySDRKwargs``, a SWIG map proxy with no
    ``get`` - it has to be turned into a dict before it can be read like
    one. Getting that wrong once cost an afternoon, because the exception
    it raises looks exactly like no device being plugged in.
    """
    ensure_plugin_path()
    try:
        import SoapySDR  # type: ignore
        devices = [dict(d) for d in SoapySDR.Device.enumerate()]
    except Exception as exc:
        print(f"BB60: could not enumerate SoapySDR devices: {exc}",
              file=sys.stderr)
        return []
    found = [d for d in devices
             if DRIVER.lower() in str(d.get('driver', '')).lower()]
    if _kept is not None:                         # FM receiver: see KEEP_OPEN
        found.append(_kept[1])
    return found


def is_available():
    """True if the SoapySDR module for this device can be found at all."""
    ensure_plugin_path()
    try:
        import SoapySDR  # type: ignore
        return any('BB60' in m for m in SoapySDR.listModules())
    except Exception:
        return False


class bb60_source(gr.sync_block):
    """Complex baseband from a BB60D.

    The device is opened in ``start()`` and closed in ``stop()``, so the
    block can be built before anything is plugged in and so the device is
    released the moment the flowgraph ends.

    **Every call into the vendor module is made from the work thread.**
    ``set_center_freq`` and ``set_gain_percent`` are called from the Qt
    thread while ``work`` is parked inside ``readStream``; rather than
    serialise on a lock and make the GUI wait up to a read timeout, they
    leave the new value behind and ``work`` applies it before its next
    read. Nothing else touches the device, so there is nothing to race.
    """

    #: How long readStream may wait. Long enough that an idle flowgraph is
    #: not a spin loop, short enough that a pending retune lands promptly.
    TIMEOUT_US = 200000

    #: FM receiver: :meth:`lost` without the driver's words - reads that came
    #: back with nothing, this many in a row and over this long. Unplugged
    #: (2026-09-23), every read returned 0 at its timeout, ~0.25 s apart, for
    #: good; running, 331776 samples a read and never 0 in 80 s. A read at
    #: the slowest rate (312.5 kS/s) takes about 1 s and may time out once
    #: or twice, and a retune may cost one; 8 reads and 2 s is neither.
    EMPTY_READS_LOST = 8
    EMPTY_SECONDS_LOST = 2.0

    #: FM receiver: while True, stop() closes only the stream and keeps the
    #: device, and the next start() sets up a stream on it again. Opening the
    #: device is most of the 1.6 s a Sweep/Receive switch took; the window
    #: sets this for the switch and :meth:`release` lets the device go.
    hold_open = False

    def __init__(self, center_freq, sample_rate, gain_percent=60.0):
        gr.sync_block.__init__(self, name='bb60_source', in_sig=None,
                               out_sig=[np.complex64])
        self.center_freq = float(center_freq)
        self.sample_rate = nearest_rate(sample_rate)
        self.gain_percent = float(gain_percent)
        self.overflows = 0
        self._sdr = None
        self._stream = None
        self._lock = threading.Lock()
        self._pending = {}
        self._held = None
        self._reset_lost()

    # -- lifecycle -------------------------------------------------------

    def start(self):
        ensure_plugin_path()
        install_log_handler()
        capture_driver_output()
        reset_overflows()
        reset_connection_issues()                 # FM receiver: see lost()
        self._reset_lost()
        import SoapySDR  # type: ignore
        from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32  # type: ignore

        if self._held is not None:
            # FM receiver: the device kept open by hold_open.
            self._sdr, self._held = self._held, None
        else:
            self._sdr = self._open()
        # Before configuration, not after - see the module docstring.
        self._stream = self._sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        # FM receiver: everything pending is about to be applied from the
        # attributes anyway; applying it again in work() cost a second
        # 24 ms retune on every start.
        with self._lock:
            self._pending = {}
        self._configure(center_freq=self.center_freq,
                        sample_rate=self.sample_rate,
                        gain_percent=self.gain_percent)
        self._sdr.activateStream(self._stream)
        return True

    def _open(self):
        global _kept
        import SoapySDR  # type: ignore
        if _kept is not None:                     # FM receiver: see KEEP_OPEN
            return _kept[0]
        found = find_devices()
        if not found:
            raise RuntimeError(
                "No Signal Hound BB60 was found on USB. Check it is "
                "connected, and that no other application - Sceptre, or "
                "another flowgraph - already has it open.")
        # Enumerating is what loads the module, and with it the system
        # libSoapySDR that the module logs into. Before this call that
        # library may not have been in the process at all.
        install_log_handler()
        # Opened by driver name alone: the arguments enumerate() returns
        # are refused, serial with "no match" and the whole dict with
        # "device_id is not a number".
        sdr = SoapySDR.Device(f"driver={DRIVER}")
        if KEEP_OPEN:
            setattr(sdr, '__closed__', True)      # FM receiver: see KEEP_OPEN
            _kept = (sdr, found[0])
        return sdr

    def hold(self):
        """FM receiver: open the device with no stream, and keep it for
        :meth:`start` - the BB60D's own sweep (``bb60_sweep``) borrows it
        meanwhile. Does nothing if it is open already."""
        if self._sdr is None and self._held is None:
            ensure_plugin_path()
            install_log_handler()
            self._held = self._open()

    def stop(self):
        sdr, stream, self._sdr, self._stream = self._sdr, self._stream, None, None
        if sdr is not None and stream is not None:
            try:
                sdr.deactivateStream(stream)
                sdr.closeStream(stream)
            except Exception as exc:
                print(f"BB60 source: error closing the stream: {exc}",
                      file=sys.stderr)
        if self.hold_open and sdr is not None:
            self._held = sdr                      # FM receiver: see hold_open
        release_driver_output()
        return True

    def release(self):
        """FM receiver: close a device kept open by :attr:`hold_open`."""
        self._held = None

    # -- FM receiver: a device that has gone -----------------------------

    def _reset_lost(self):
        self._t_start = time.monotonic()
        self._t_samples = None             # when a read last brought samples
        self._empty_reads = 0              # reads in a row that brought none
        self._t_empty = None               # when the first of them returned

    def lost(self):
        """FM receiver: why the stream has stopped for good, or None.

        Two signs, both seen with the device unplugged (2026-09-23), neither
        in normal running. The driver's "Device connection issues detected"
        line (:data:`CONNECTION_LOST`), newer than this start and than the
        last samples, says so at once. Failing that - the line worded
        otherwise, or fd 2 not filtered - reads coming back empty for
        :data:`EMPTY_READS_LOST` reads and :data:`EMPTY_SECONDS_LOST`. None
        while stopped: the stream is closed then, or lent to ``bb60_sweep``.
        Cheap and safe from any thread; never raises."""
        try:
            if self._stream is None:
                return None
            count, when = _connection_issues
            samples = self._t_samples
            if (count and when is not None and when > self._t_start
                    and (samples is None or when > samples)):
                return ("the BB60D's USB connection was lost "
                        "(the driver reports connection issues)")
            first = self._t_empty
            if (self._empty_reads >= self.EMPTY_READS_LOST and first is not None
                    and time.monotonic() - first >= self.EMPTY_SECONDS_LOST):
                return ("the BB60D stopped streaming "
                        f"({self._empty_reads} reads in a row brought nothing)")
        except Exception:
            pass
        return None

    # -- settings --------------------------------------------------------

    def set_center_freq(self, hz):
        self.center_freq = float(hz)
        with self._lock:
            self._pending['center_freq'] = self.center_freq

    def set_gain_percent(self, percent):
        self.gain_percent = float(percent)
        with self._lock:
            self._pending['gain_percent'] = self.gain_percent

    def set_sample_rate(self, rate):
        self.sample_rate = nearest_rate(rate)
        with self._lock:
            self._pending['sample_rate'] = self.sample_rate

    # The UHD and Soapy setter idioms, so this drops into an app that
    # branches on radio type without a fourth branch everywhere.
    def set_frequency(self, _chan, hz):
        self.set_center_freq(hz)

    def set_samp_rate(self, rate):
        self.set_sample_rate(rate)

    def _configure(self, **values):
        from SoapySDR import SOAPY_SDR_RX  # type: ignore
        if 'sample_rate' in values:
            self._sdr.setSampleRate(SOAPY_SDR_RX, 0, values['sample_rate'])
            # FM receiver: see IQ_BANDWIDTH.
            bandwidth = IQ_BANDWIDTH.get(values['sample_rate'])
            if bandwidth:
                self._sdr.setBandwidth(SOAPY_SDR_RX, 0, bandwidth)
        if 'center_freq' in values:
            # The module logs "ERROR ConfigureIQCenter: <hz>" for every
            # successful retune; the frequency reads back correctly after
            # it. It is a log level, not a failure.
            self._sdr.setFrequency(SOAPY_SDR_RX, 0, values['center_freq'])
        if 'gain_percent' in values:
            for name, value in gain_plan(values['gain_percent']).items():
                self._sdr.setGain(SOAPY_SDR_RX, 0, name, value)

    # -- streaming -------------------------------------------------------

    def work(self, input_items, output_items):
        if self._sdr is None or self._stream is None:
            return -1                      # nothing to read from; end cleanly

        with self._lock:
            pending, self._pending = self._pending, {}
        if pending:
            # FM receiver: a retune may cost a read; count empties afresh.
            self._empty_reads, self._t_empty = 0, None
            try:
                self._configure(**pending)
            except Exception as exc:
                print(f"BB60 source: could not apply {sorted(pending)}: {exc}",
                      file=sys.stderr)

        out = output_items[0]
        status = self._sdr.readStream(self._stream, [out], len(out),
                                      timeoutUs=self.TIMEOUT_US)
        if status.ret > 0:
            self._t_samples = time.monotonic()     # FM receiver: see lost()
            self._empty_reads, self._t_empty = 0, None
            return status.ret
        if status.ret == -4:               # SOAPY_SDR_OVERFLOW - samples lost
            self.overflows += 1
        elif status.ret == 0:              # FM receiver: see lost()
            if self._t_empty is None:
                self._t_empty = time.monotonic()
            self._empty_reads += 1
        return 0

    def adc_overflows(self):
        """Times the converter has been overdriven - a gain problem, not a
        dropped-sample one, and invisible in the samples themselves."""
        return overflow_count()
