"""The sweep, with no radio: plan arithmetic, stitching, station finding, and
the stale-sample skip against a simulated radio that is slow to retune.

Run:  python tools/tests/test_sweep.py        (a few seconds)
"""

import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from gnuradio import gr  # noqa: E402

from fm_receiver.dsp import clip_probe  # noqa: E402
from fm_receiver.sweep import FULL_SCALE, SweepPlan, find_stations, sweep_sink  # noqa: E402


def test_plan_tiles_the_span():
    plan = SweepPlan(87.5e6, 108e6, 20e6, 4096, 0.84)
    assert plan.usable_bins % 2 == 0
    assert plan.steps == int(np.ceil(plan.total_bins / plan.usable_bins))
    # Adjacent steps are exactly one usable block apart, and step 0's first
    # usable bin is the span's start.
    assert abs(plan.center(1) - plan.center(0) - plan.step_hz) < 1e-3
    first_bin_freq = plan.center(0) + (plan.first_bin - 4096 // 2) * plan.rbw
    assert abs(first_bin_freq - plan.start_hz) < 1e-3
    assert plan.steps == 2, plan.describe()
    one = SweepPlan(98e6, 99e6, 20e6, 4096, 0.84)
    assert one.steps == 1


def test_place_and_notch():
    plan = SweepPlan(100e6, 110e6, 10e6, 1024, 0.5, dc_notch_hz=30e3)
    pano = plan.new_panorama()
    spectrum = np.ones(1024)
    spectrum[512] = 1000.0                       # a DC spike at the LO bin
    for k in range(plan.steps):
        plan.place(pano, k, spectrum)
    assert np.allclose(pano, 1.0), 'the notch should remove the spike'
    assert plan.dc_notch_bins == 4


def test_find_stations():
    freqs = np.arange(87.5e6, 108e6, 5e3)
    power = np.full(len(freqs), -100.0)
    for f, level in ((88.5e6, -40), (98.7e6, -50), (101.1e6, -70), (104.3e6, -95)):
        m = np.abs(freqs - f) < 90e3
        power[m] = level
    found = find_stations(freqs, power, min_snr_db=15)
    got = [round(f / 1e5) / 10 for f, _, _ in found]
    assert got == [88.5, 98.7, 101.1], got


class slow_radio(gr.sync_block):
    """A radio whose retunes take ``latency`` samples to show, like a HackRF's
    USB buffers: tones at fixed RF frequencies, seen relative to the LO."""

    def __init__(self, rate, tones, latency):
        gr.sync_block.__init__(self, name='slow_radio', in_sig=None,
                               out_sig=[np.complex64])
        self.rate = rate
        self.tones = tones
        self.latency = latency
        self.center = 0.0
        #: True: sends nothing, as an unplugged radio (test_gui part 7).
        self.silent = False
        self._pending = []                        # (applies at sample, hz)
        self._lock = threading.Lock()
        self._rng = np.random.default_rng(3)

    def set_center(self, hz):
        with self._lock:
            self._pending.append((self.nitems_written(0) + self.latency, hz))

    def work(self, input_items, output_items):
        if self.silent:
            time.sleep(0.01)
            return 0
        out = output_items[0]
        n = min(len(out), 16384)
        start = self.nitems_written(0)
        idx = np.arange(n)
        centers = np.full(n, self.center)
        with self._lock:
            keep = []
            for at, hz in self._pending:
                if at < start + n:
                    centers[max(0, at - start):] = hz
                    self.center = hz
                else:
                    keep.append((at, hz))
            self._pending = keep
        t = (start + idx) / self.rate
        x = 1e-3 * (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))
        for f, a in self.tones:
            # Only what the radio's anti-alias filter lets through: without
            # this a tone 8.75 MHz off at 10 MS/s folds back as a ghost.
            inband = np.abs(f - centers) < 0.45 * self.rate
            x += inband * a * np.exp(2j * np.pi * (f - centers) * t)
        out[:n] = x
        return n


def run_sweep(latency, settle_ms, sweeps=3):
    rate = 10e6
    tones = [(90.0e6, 0.5), (95.3e6, 0.05)]
    plan = SweepPlan(87.5e6, 108e6, rate, 1024, 0.75)
    tb = gr.top_block()
    radio = slow_radio(rate, tones, latency)
    radio.center = plan.center(0)
    sink = sweep_sink(plan, radio.set_center, radio, frames=4, settle_ms=settle_ms)
    tb.connect(radio, sink)
    tb.start()
    deadline = time.time() + 30
    while sink.sweeps < sweeps and time.time() < deadline:
        time.sleep(0.02)
    tb.stop()
    tb.wait()
    freqs, _, done, _ = sink.snapshot()
    assert done is not None, 'no sweep completed'
    return plan, freqs, done


def ghosts(freqs, db, tones):
    """Peaks 30 dB over the floor that are not near a real tone."""
    floor = np.median(db)
    hot = freqs[db > floor + 30]
    real = np.array([f for f, _ in tones])
    return [f for f in hot if np.min(np.abs(real - f)) > 50e3]


def test_stale_samples_are_skipped():
    tones = [(90.0e6, 0.5), (95.3e6, 0.05)]
    latency = 200000                              # 20 ms at 10 MS/s
    plan, freqs, db = run_sweep(latency, settle_ms=25.0)
    for f, _ in tones:
        k = int(round((f - plan.start_hz) / plan.rbw))
        assert db[k - 2:k + 3].max() > np.median(db) + 40, f
    bad = ghosts(freqs, db, tones)
    assert not bad, f"ghosts with enough settle: {bad[:5]}"
    # And the check has teeth: too little settle for this radio does show
    # the last step's signals where they are not.
    _, freqs0, db0 = run_sweep(latency, settle_ms=0.0)
    assert ghosts(freqs0, db0, tones), 'expected ghosts with no settle'


def test_replan_drops_the_old_sweep():
    """A new plan's snapshot never pairs its frequencies with a sweep of
    the old one - the window listed stations from exactly that."""
    plan = SweepPlan(87.5e6, 108e6, 10e6, 1024, 0.75)
    sink = sweep_sink(plan, lambda hz: None, frames=1, settle_ms=0)
    sink._completed = plan.new_panorama()
    freqs, _, done, _ = sink.snapshot()
    assert done is not None and len(done) == len(freqs)
    sink.set_plan(SweepPlan(88.5e6, 108e6, 10e6, 1024, 0.75))
    freqs, live, done, _ = sink.snapshot()
    assert done is None and len(live) == len(freqs)


def test_clipping_is_counted():
    """The probe's share of samples at full scale, and the sweep's, which
    counts only the frames each step measures and names the worst step."""
    x = np.full(1000, 0.1 + 0.1j, dtype=np.complex64)
    x[:30] = FULL_SCALE + 0j                     # I at full scale
    x[30:50] = -1j                               # Q past it
    x[50:60] = 0.97 + 0.97j                      # close, but under
    probe = clip_probe()
    probe.work([x], [])
    assert probe.take() == (50, 1000)
    assert probe.take() == (0, 0)

    plan = SweepPlan(87.5e6, 108e6, 10e6, 1024, 0.75)
    assert plan.steps == 3, plan.describe()
    lo = [plan.center(0)]
    sink = sweep_sink(plan, lambda hz: lo.append(hz), frames=4, settle_ms=0)
    assert sink.clip_report() is None            # no sweep yet
    loud = plan.center(1)                        # only the middle step clips
    rng = np.random.default_rng(1)
    while sink.sweeps < 2:
        chunk = (0.01 * (rng.standard_normal(4096) + 1j * rng.standard_normal(4096))
                 ).astype(np.complex64)
        if lo[-1] == loud:
            chunk[::20] = 1.0                        # 5% of it
        sink.work([chunk], [])
    worst, where = sink.clip_report()
    assert where == loud and abs(worst - 0.05) < 0.005, (worst, where)
    sink.set_plan(plan)
    assert sink.clip_report() is None            # a new plan starts again


def test_agc_reference_level():
    """5 dB over the strongest input, rounded up to 5 dB; up at once, down
    only once it has fallen 10 dB; within the device's reference range. The
    input is the most power in any 27 MHz, whatever the RBW, and never less
    than the peak."""
    from fm_receiver.bb60_sweep import REF_RANGE_DB, agc_ref, strongest_input
    assert agc_ref(-40.0, -90.0) == -35.0
    assert agc_ref(-41.0, -90.0) == -35.0
    assert agc_ref(-39.0, -35.0) == -30.0            # up as soon as needed
    assert agc_ref(-44.0, -35.0) is None             # a little lower: held
    assert agc_ref(-49.0, -35.0) is None
    assert agc_ref(-50.0, -35.0) == -45.0            # 10 dB down: follows
    assert agc_ref(30.0, 0.0) == REF_RANGE_DB[1]
    assert agc_ref(-200.0, -20.0) == REF_RANGE_DB[0]

    def station(rbw, bin_hz, dbm=-30.0, width=200e3, span=60e6):
        # An FM station of ``dbm`` in all, spread over ``width``: each point
        # holds the power in one RBW. Far off, a second, 30 MHz away.
        n = int(span / bin_hz)
        db = np.full(n, -130.0)
        per = dbm - 10 * np.log10(width / rbw)
        k = int(round(width / bin_hz))
        db[1000:1000 + k] = per
        far = 1000 + int(30e6 / bin_hz)
        db[far:far + k] = per
        return db

    for rbw, bin_hz in ((1e3, 1e3), (10e3, 10e3), (10e3, 3.125e3)):
        got = strongest_input(station(rbw, bin_hz), bin_hz, rbw)
        assert abs(got - -30.0) < 0.3, (rbw, bin_hz, got)
    # Two stations within 27 MHz add up: 3 dB more.
    db = station(1e3, 1e3)
    k = 1000 + int(10e6 / 1e3)
    db[k:k + 200] = db[1000]
    assert abs(strongest_input(db, 1e3, 1e3) - -27.0) < 0.3
    # A carrier narrower than the RBW: its peak.
    db = np.full(10000, -130.0)
    db[5000:5003] = -20.0
    assert strongest_input(db, 300.0, 1e3) == -20.0


def test_hackrf_blocks_are_placed():
    """The HackRF firmware's blocks, made up: a header, then 8-bit I/Q, one
    block per tuning, interleaved (87, 92, 107, 112 MHz for 87-127). A tone
    at 95.1 MHz lands at 95.1 in the sweep, whole and without gaps, and the
    tuning whose samples clip is the one named."""
    from fm_receiver.hackrf_sweep import (BYTES_PER_BLOCK, OFFSET_HZ, HackRFSweepPlan,
                                          hackrf_sweeper)
    from fm_receiver.radios import rx_gain_plan
    plan = HackRFSweepPlan(87.5e6, 108e6, 100e3)
    assert (plan.low_mhz, plan.high_mhz, plan.fft_size) == (87, 127, 200), plan.describe()
    sweeper = hackrf_sweeper(plan, rx_gain_plan, 40)
    rng = np.random.default_rng(2)

    def block(mhz, clip=False):
        b = np.zeros(BYTES_PER_BLOCK, dtype=np.uint8)
        b[0] = b[1] = 0x7F
        b[2:10] = np.frombuffer(np.uint64(mhz * 1_000_000).tobytes(), dtype=np.uint8)
        n = (BYTES_PER_BLOCK - 10) // 2
        t = np.arange(n) / 20e6
        lo = mhz * 1e6 + OFFSET_HZ
        x = 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
        x += 0.5 * np.exp(2j * np.pi * (95.1e6 - lo) * t)
        iq = np.empty(2 * n)
        iq[0::2], iq[1::2] = x.real, x.imag
        raw = np.clip(np.round(iq * 128), -127, 127)
        if clip:
            raw[::10] = 127
        b[10:10 + 2 * n] = raw.astype(np.int8).view(np.uint8)
        return b

    tunings = [87, 92, 107, 112, 87]
    blocks = np.stack([block(m, clip=(m == 107)) for m in tunings])
    sweeper._take(blocks)
    freqs, db, done, serial = sweeper.snapshot()
    assert done is not None and serial == 1, serial
    assert not np.isnan(done).any() and len(done) == len(freqs) == plan.points
    peak = freqs[np.argmax(done)]
    assert abs(peak - 95.1e6) <= plan.bin_hz, peak
    assert done.max() > np.median(done) + 40, (done.max(), np.median(done))
    share, lo = sweeper.clip_report()
    assert lo == 107e6 + OFFSET_HZ and share > 0.05, (share, lo)


if __name__ == '__main__':
    test_hackrf_blocks_are_placed()
    test_agc_reference_level()
    test_clipping_is_counted()
    test_plan_tiles_the_span()
    test_place_and_notch()
    test_find_stations()
    test_replan_drops_the_old_sweep()
    test_stale_samples_are_skipped()
    print('sweep: all checks passed')
