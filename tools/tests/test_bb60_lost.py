"""The BB60D saying it has gone (``lost``), with no radio: the IQ stream's
block reads from a fake SoapySDR device, the driver's words go through the
stderr filter by hand, and the device's own sweep runs on a fake API.

What the real one did unplugged (2026-09-23) is what the fakes do: reads
returning 0 at every timeout, "[ERROR] GetIQ: Device connection issues
detected" on stderr four times a second, and every sweep fetch failing
with "Device connection issues detected".

- IQ stream: the driver's line says so at once, but not while stopped, not
  once samples come after it, and not after a new start. Empty reads say
  so only many in a row, over seconds: one, or a few around a retune, do
  not. ``radios.BB60.lost`` is the block's.
- Its own sweep: the connection words on two failures in a row say so;
  one, or another error however often, does not; a sweep that comes
  through clears it.

Run:  python tools/tests/test_bb60_lost.py        (a few seconds)
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

from fm_receiver import bb60_source, bb60_sweep, radios  # noqa: E402

LINE = b'\x1b[1m\x1b[31m[ERROR] GetIQ: Device connection issues detected\x1b[0m'


class _status:
    def __init__(self, ret):
        self.ret = ret


class fake_sdr:
    """readStream hands back ``rets`` in turn, then the last for ever."""

    def __init__(self, rets):
        self.rets = list(rets)

    def readStream(self, _stream, _bufs, n, timeoutUs=0):
        ret = self.rets.pop(0) if len(self.rets) > 1 else self.rets[0]
        return _status(min(ret, n))

    def __getattr__(self, name):                   # the setters
        return lambda *a, **k: None


def streaming(block, rets):
    """As ``start()`` leaves it, on a fake device."""
    bb60_source.reset_connection_issues()
    block._reset_lost()
    block._sdr, block._stream = fake_sdr(rets), object()


def read(block, times=1):
    out = np.zeros(4096, dtype=np.complex64)
    for _ in range(times):
        block.work(None, [out])


def driver_line(raw=LINE):
    """The line as the stderr filter gets it; what it passes on goes to
    /dev/null."""
    filt = object.__new__(bb60_source._DriverOutput)
    filt._saved = os.open(os.devnull, os.O_WRONLY)
    try:
        filt._line(raw)
    finally:
        os.close(filt._saved)


def test_driver_line():
    block = bb60_source.bb60_source(100e6, 10e6)
    assert block.lost() is None                    # never started
    streaming(block, [331776])
    read(block, 3)
    assert block.lost() is None
    driver_line(b'[INFO] something else entirely')
    driver_line(b'[ERROR] ConfigureIQCenter: 100000000')   # retune chatter
    assert block.lost() is None
    time.sleep(0.002)
    driver_line()
    reason = block.lost()
    assert reason and 'USB connection was lost' in reason, reason
    # Only the first is passed on; the rest are counted.
    driver_line()
    assert bb60_source._connection_issues[0] == 2
    # Samples after it: it was not the end.
    time.sleep(0.002)
    read(block)
    assert block.lost() is None, block.lost()
    # Stopped (the stream closed, or lent to the device's own sweep): None.
    time.sleep(0.002)
    driver_line()
    assert block.lost()
    block._stream = None
    assert block.lost() is None
    # A new start forgets it.
    streaming(block, [331776])
    assert block.lost() is None
    read(block)
    assert block.lost() is None
    # Through the radio.
    radio = radios.BB60()
    assert radio.lost() is None                    # no block yet
    radio.block = block
    time.sleep(0.002)
    driver_line()
    assert radio.lost() == block.lost() and radio.lost()
    block._stream = None
    assert radio.lost() is None


def test_empty_reads():
    block = bb60_source.bb60_source(100e6, 10e6)
    block.EMPTY_SECONDS_LOST = 0.2                 # for the test's sake
    # One empty read, then samples: a timeout, nothing more.
    streaming(block, [0, 331776])
    read(block)
    assert block.lost() is None
    read(block)
    assert block._empty_reads == 0 and block.lost() is None
    # Many in a row, but in less than the time: not yet.
    streaming(block, [0])
    read(block, block.EMPTY_READS_LOST + 2)
    assert block.lost() is None
    time.sleep(0.25)
    reason = block.lost()
    assert reason and 'stopped streaming' in reason, reason
    # A retune starts the count again.
    block.set_center_freq(101e6)
    read(block)
    assert block._empty_reads == 1 and block.lost() is None
    # Long enough, but too few reads: not lost.
    streaming(block, [0])
    read(block, block.EMPTY_READS_LOST - 1)
    time.sleep(0.25)
    assert block.lost() is None
    # An overflow (-4) is not an empty read.
    streaming(block, [-4])
    read(block, block.EMPTY_READS_LOST + 2)
    time.sleep(0.25)
    assert block.lost() is None and block.overflows == block.EMPTY_READS_LOST + 2
    # Reopened: forgotten.
    streaming(block, [0])
    read(block, block.EMPTY_READS_LOST + 2)
    time.sleep(0.25)
    assert block.lost()
    streaming(block, [331776])
    assert block.lost() is None


class fake_api:
    """The calls the sweeper makes, answering 0, but for a fetch: that
    returns ``fetch`` (a code, or a list of them in turn)."""

    ERRORS = {-8: b'Device connection issues detected', -3: b'Some other trouble'}

    def __init__(self, fetch):
        self.fetch = list(fetch)
        self.fetches = 0
        self.lock = threading.Lock()

    def bbGetErrorString(self, status):
        return self.ERRORS.get(status, b'Unknown')

    def bbQueryTraceInfo(self, _h, count, bin_hz, first):
        count._obj.value, bin_hz._obj.value, first._obj.value = 100, 10e3, 99.5e6
        return 0

    def bbFetchTrace_32f(self, *_args):
        with self.lock:
            self.fetches += 1
            return self.fetch.pop(0) if len(self.fetch) > 1 else self.fetch[0]

    def __getattr__(self, name):
        return lambda *a: 0


def sweeping(fetch, until, timeout=4.0):
    """Run a sweeper on the fake API until ``until(sweeper)``."""
    saved = bb60_sweep._LIB
    bb60_sweep._LIB = api = fake_api(fetch)
    plan = bb60_sweep.NativeSweepPlan(99.5e6, 100.5e6, 10e3)
    sweeper = bb60_sweep.bb60_sweeper(0, plan, 60)
    sweeper.RETRY_S = 0.05
    sweeper.MAX_SWEEPS_PER_S = 1000.0
    try:
        sweeper.start()
        t0 = time.time()
        while not until(sweeper) and time.time() - t0 < timeout:
            time.sleep(0.01)
        return sweeper, api
    finally:
        sweeper.stop()
        bb60_sweep._LIB = saved


def test_native_sweep():
    # Running: nothing.
    s, _ = sweeping([0], lambda s: s.sweeps >= 20)
    assert s.sweeps >= 20 and s.error is None and s.lost() is None
    # Unplugged: the connection words, again and again.
    s, _ = sweeping([-8], lambda s: s.failures >= 3)
    assert 'connection issues' in s.error and s.failures >= 3
    reason = s.lost()
    assert reason and 'USB connection was lost' in reason, reason
    # Just one, and the next sweep comes through: never lost, and cleared.
    seen = []
    s, _ = sweeping([0, 0, -8, 0], lambda s: (seen.append(s.lost()) or s.sweeps >= 10))
    assert s.error is None and s.failures == 0 and s.lost() is None
    assert not any(seen), seen
    # The words on one failure only: an error, not a loss.
    s, _ = sweeping([-8], lambda s: s.failures >= 1)
    s.failures = 1
    assert s.error and s.lost() is None
    # Another error, however often: the sweep keeps trying; not a loss.
    s, _ = sweeping([-3], lambda s: s.failures >= 5)
    assert s.error and 'other trouble' in s.error and s.lost() is None
    # Never raises, whatever state it is in.
    s.error = 12345
    assert s.lost() is None


if __name__ == '__main__':
    test_driver_line()
    test_empty_reads()
    test_native_sweep()
    print('bb60 lost: all checks passed')
