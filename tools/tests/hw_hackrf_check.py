"""Hardware check with a HackRF One and an FM antenna - off air.

Not part of the no-radio tests; run it when a HackRF is plugged in, and
nothing else has it open:

    python tools/tests/hw_hackrf_check.py [--keep]     (about three minutes)

1. **Found**: ``detect_radios`` lists it.
2. **Sweep settle time** of the LO-hopping sweep - the window's for a
   USRP; the HackRF sweeps with its firmware there (stage 6). The HackRF's queue of samples in USB transfers is
   invisible to the sweep's backlog count, so its settle time has to cover
   it; 40 ms was an estimate. Sweeps the FM band in two steps at several
   settle times against single-LO references, and reports the ghosts at
   each. The default must show none. A ghost is a stale frame from the
   other step, so it lands exactly one step away from a signal: a bin
   counts only if it stands 15 dB over a quiet reference *and* the other
   step's reference has a signal at the same place in its step. A weak
   station fading between the sweeps, which the bare 15 dB rule counted,
   has no such source and is reported apart.
3. **Reception and gain.** Tunes the strongest stations until one carries
   RDS, then tries several gains on it: RDS blocks good, SNR, pilot.
4. **The DC spike**: its height at the LO, against the tuner's 100 kHz
   keep-out.
5. **Record, move the Center, play back** - as the BB60D check.
6. **Its own sweep** (``hackrf_sweep``, the firmware's sweep mode, which
   the window uses): the FM band with the station in it, 1 MHz-6 GHz in
   under 2 s, the clipping per tuning, and Receive after it on the IQ
   stream's device, decoding the same PI; both switches timed.
7. **Let go**: once the engine is closed, ``hackrf_info`` - another
   program - must be able to open the HackRF while this one still runs.
8. **The window**: opens the HackRF, sweeps, switches to Receive, shows
   the clipped share at all times, warns of clipping at too much gain and
   not at the default - in Receive, and in a sweep naming the step - and
   after **Stop** leaves the HackRF free for another program.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import HackRF, detect_radios  # noqa: E402
from fm_receiver.sweep import SweepPlan, find_stations, to_db  # noqa: E402
from tests.hw_bb60_check import (center_check, playback_check,  # noqa: E402
                                 receive_check)

RATE_RX = 2e6


def average_sweeps(tb, seconds):
    s = tb.sweeper
    time.sleep(0.5)
    total, count, serial = None, 0, -1
    end = time.time() + seconds
    while time.time() < end:
        _, _, done, ser = s.snapshot()
        if done is not None and ser != serial:
            serial = ser
            lin = 10 ** (done / 10)
            total = lin if total is None else total + lin
            count += 1
        time.sleep(0.01)
    return total / max(count, 1), count


def count_ghosts(s_db, ref_db, u):
    """Ghosts in a stitched sweep ``s_db`` against ``ref_db``, each step's
    single-LO reference in full (``u`` bins a step, the last one past the
    plan's stop). Returns (ghosts, unexplained): both stand 15 dB over a
    quiet reference bin; a ghost also has a signal 15 dB over the floor at
    the same place in another step's reference, and the rest don't."""
    steps = len(ref_db) // u
    floor = np.median(ref_db[:len(s_db)])
    ref = ref_db.reshape(steps, u)
    ghosts = unexplained = 0
    for i in range(len(s_db)):
        k, j = divmod(i, u)
        if ref[k, j] >= floor + 6 or s_db[i] - ref[k, j] <= 15:
            continue
        others = np.delete(ref[:, j], k)
        if np.any(others > floor + 15):
            ghosts += 1
        else:
            unexplained += 1
    return ghosts, unexplained


def settle_check(tb, radio, settles=(0.0, 5.0, 10.0, 20.0, 40.0)):
    """Ghosts in a stitched FM-band sweep at each settle time, against
    single-LO references taken once."""
    rate = 20e6
    u_frac = radio.usable_fraction(rate)
    plan = SweepPlan(87.5e6, 108e6, rate, 4096, u_frac, radio.dc_notch_hz)
    assert plan.steps == 2, plan.describe()
    u = plan.usable_bins
    # Each step's reference whole, the last one past 108 MHz too: a stale
    # frame from it carries whatever is there.
    reference = np.full(plan.steps * u, np.nan)
    for k in range(plan.steps):
        lo = plan.start_hz + k * u * plan.rbw
        one = SweepPlan(lo, lo + u * plan.rbw, rate, 4096, u_frac, radio.dc_notch_hz)
        assert one.steps == 1 and one.usable_bins == u, one.describe()
        tb.start_sweep(one, frames=16, settle_ms=radio.settle_ms)
        part, m = average_sweeps(tb, 2.0)
        reference[k * u:(k + 1) * u] = part[:u]
    r_db = to_db(reference)
    quiet = r_db[:plan.total_bins] < np.median(r_db[:plan.total_bins]) + 6
    results = {}
    s_db = None
    for settle in settles:
        tb.start_sweep(plan, frames=16, settle_ms=settle)
        stitched, n = average_sweeps(tb, 3.0)
        s_db = to_db(stitched)
        ghosts, unexplained = count_ghosts(s_db, r_db, u)
        diff = float(np.median(np.abs(s_db - r_db[:plan.total_bins])))
        ms = tb.sweeper.sweep_seconds * 1e3 if tb.sweeper.sweep_seconds else float('nan')
        results[settle] = ghosts
        print(f"settle {settle:4.0f} ms: {n:3d} sweeps, {ms:4.0f} ms per sweep, "
              f"median |diff| {diff:.2f} dB, ghosts {ghosts} of {int(quiet.sum())} quiet bins"
              f" (+{unexplained} over a quiet bin with no source a step away)")
    return plan.freqs(), s_db, results


def _sweeps(tb, n, limit=30.0):
    """The sweep after ``n`` more whole ones."""
    s = tb.sweeper
    serial, got, t0 = s.snapshot()[3], 0, time.time()
    while got < n:
        assert time.time() - t0 < limit, f"{got} of {n} sweeps in {limit} s: {s.error}"
        freqs, _, done, ser = s.snapshot()
        if done is not None and ser > serial:
            serial, got = ser, got + 1
        time.sleep(0.005)
    return freqs, done


def native_check(tb, radio, station, pi):
    """The firmware's sweep, as the window runs it: the FM band with the
    station in it, the whole range in under 2 s, clipping counted, then
    Receive on the IQ stream's device again, decoding the same PI - the
    mode switch both ways, timed."""
    t0 = time.time()
    tb.start_sweep(radio.native_plan(87.5e6, 108e6))
    to_sweep = time.time() - t0
    s = tb.sweeper
    freqs, db = _sweeps(tb, 10)
    found = find_stations(freqs, db, min_snr_db=20)
    print(f"own sweep: {s.plan.describe()}, {s.sweep_seconds * 1e3:.0f} ms a sweep; stations "
          + ", ".join(f"{f / 1e6:.1f}" for f, _, _ in found[:8]))
    assert not np.isnan(db).any()
    assert any(abs(f - station) < 150e3 for f, _, _ in found), (station, found)
    assert s.sweep_seconds < 0.1, s.sweep_seconds
    share, lo = s.clip_report()
    print(f"  FM band: worst tuning clipped {share * 100:.2f}% (LO {lo / 1e6:.1f} MHz)")
    s.set_plan(radio.native_plan(*radio.sweep_range_hz))
    freqs, db = _sweeps(tb, 3)
    share, lo = s.clip_report()
    print(f"  {freqs[0] / 1e6:.0f} MHz to {freqs[-1] / 1e6:.0f} MHz: {s.plan.describe()}, "
          f"{s.sweep_seconds * 1e3:.0f} ms a sweep, strongest {db.max():.1f} dBFS at "
          f"{freqs[np.argmax(db)] / 1e6:.1f} MHz; worst tuning clipped {share * 100:.1f}% "
          f"(LO {lo / 1e6:.1f} MHz)")
    assert s.sweep_seconds < 2.0 and not np.isnan(db).any(), s.sweep_seconds
    t0 = time.time()
    tb.start_receive(station, RATE_RX)
    to_receive = time.time() - t0
    rx = tb.rx
    snap = rx.rds.snapshot()
    while time.time() - t0 < 20 and not (snap['pi_hex'] and snap['blocks_seen'] > 100):
        rx.update_stereo()
        time.sleep(0.2)
        snap = rx.rds.snapshot()
    print(f"  mode switch: to its own sweep {to_sweep * 1e3:.0f} ms, back to Receive "
          f"{to_receive * 1e3:.0f} ms, then PI {snap['pi_hex']}, "
          f"blocks {snap['blocks_ok']}/{snap['blocks_seen']}")
    assert snap['pi_hex'] == pi, (snap['pi_hex'], pi)


def gain_check(tb, radio, gains=(30, 40, 54, 67), seconds=8.0):
    """RDS and SNR on the tuned station at each gain, set live."""
    rx = tb.rx
    rows = []
    for g in gains:
        radio.apply_gain(g)
        time.sleep(1.0)
        rx.reset_decoders()
        t0 = time.time()
        while time.time() - t0 < seconds:
            rx.update_stereo()
            time.sleep(0.2)
        snap = rx.rds.snapshot()
        spec = rx.rf_probe.snapshot()
        db = to_db(spec)
        n = len(db)
        k = n // 2 + int(round(tb.offset_hz / (tb.rate / n)))
        half = int(round(90e3 / (tb.rate / n)))
        snr = 10 * np.log10(np.mean(10 ** (db[k - half:k + half] / 10))) - np.median(db)
        good = 100 * (1 - snap['block_error_rate']) if snap['blocks_seen'] else 0
        rows.append((g, good, snap['groups'], snr, rx.channel_power_db(), rx.pilot_level()))
        print(f"gain {g:3d}%: RDS {good:5.1f}% blocks good ({snap['groups']} groups), "
              f"SNR {snr:4.1f} dB, channel {rx.channel_power_db():6.1f} dBFS, "
              f"pilot {rx.pilot_level():.1e}")
    radio.apply_gain(radio.default_gain)
    return rows


def dc_spike(tb):
    """The spike at the LO, dB over the median floor, and its width."""
    spec = tb.rx.rf_probe.snapshot()
    db = to_db(spec)
    n = len(db)
    floor = np.median(db)
    centre = db[n // 2 - 2:n // 2 + 3].max() - floor
    bin_hz = tb.rate / n
    wide = [i for i in range(n // 2 - 200, n // 2 + 200) if db[i] > floor + 10]
    width = (max(wide) - min(wide) + 1) * bin_hz if wide else 0.0
    print(f"DC spike: {centre:.1f} dB over the floor at the LO, "
          f"{width / 1e3:.1f} kHz wide above +10 dB")
    return centre, width


def free_check():
    """Another program can open the HackRF now."""
    out = subprocess.run(['hackrf_info'], capture_output=True, text=True, timeout=20)
    text = out.stdout + out.stderr
    ok = 'Serial number' in text and 'Resource busy' not in text
    print(f"hackrf_info while this process runs: {'opened it' if ok else 'FAILED'}")
    return ok, text


def window_check():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['FMRX_CONFIG'] = os.path.join(tempfile.mkdtemp(prefix='fmrx-hw-'), 'c.json')
    from PyQt5 import Qt
    from fm_receiver import app as fmapp
    qapp = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])

    def pump(seconds, until=None):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return until() if until else True

    w = fmapp.MainWindow(fmapp.parse_args(['--radio', 'hackrf', '--mode', 'sweep',
                                           '--freq', '89.3', '--no-audio']), {})
    try:
        w.show()
        pump(0.2)
        w.start_initial()
        assert w._mode == 'sweep' and w.engine.running, w.status.text()
        # The FM band: the full range takes tens of seconds a sweep, and at
        # the default gain clipped 2.5% in a UHF TV step (533.5 MHz) here.
        w.preset_combo.setCurrentIndex(1)
        w._preset_chosen(1)
        pump(5, lambda: 'clipped' in w.status.text())
        swept = w.status.text()
        print(f"window: sweeping at {HackRF.default_gain}% gain the status reads {swept!r}")
        assert 'clipped' in swept and 'overloaded' not in swept, swept
        w.tabs.setCurrentIndex(1)
        assert w._mode == 'receive' and w.engine.running, w.status.text()
        assert w.gain_slider.value() == HackRF.default_gain
        pump(4)
        calm = w.status.text()
        # Enough gain clips: 60% did on the Linux bench. Where the signal is
        # weaker it takes more - on the Mac mini (2026-09-22) 54% put the
        # strongest station at -12 dBFS.
        for loud_gain in (60, 75, 90):
            w.gain_slider.setValue(loud_gain)
            if pump(3, lambda: 'overloaded' in w.status.text()):
                break
        loud = w.status.text()
        print(f"window: at {HackRF.default_gain}% gain the status reads {calm!r}")
        print(f"window: at {loud_gain}% gain it reads {loud!r}")
        assert 'clipped' in calm and 'overloaded' not in calm, calm
        assert 'overloaded' in loud and 'of samples clipped' in loud, loud
        # The sweep counts its clipping too, and names the step.
        w.tabs.setCurrentIndex(0)
        assert w._mode == 'sweep' and w.engine.running, w.status.text()
        pump(5, lambda: 'step centred on' in w.status.text())
        loud_sweep = w.status.text()
        print(f"window: sweeping at {loud_gain}% gain it reads {loud_sweep!r}")
        assert 'overloaded' in loud_sweep and 'step centred on' in loud_sweep, loud_sweep
        w.gain_slider.setValue(HackRF.default_gain)
        w.run_btn.click()                              # Stop
        assert w.radio is None and w.run_btn.text() == 'Start'
        free, _ = free_check()
        assert free, 'Stop should leave the HackRF free for other programs'
    finally:
        w.close()


def main():
    folder = tempfile.mkdtemp(prefix='fmrx-hackrf-')
    found = detect_radios()
    print("detected:", found)
    assert 'hackrf' in found, found
    radio = HackRF()
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        freqs, db, ghosts = settle_check(tb, radio)
        stations = find_stations(freqs, db, min_snr_db=20)
        print("strongest:", ", ".join(f"{f / 1e6:.1f} ({s:.0f} dB)" for f, s, _ in stations[:6]))
        t0 = time.time()
        pi, iq_path = receive_check(tb, radio, [f for f, _, _ in stations[:5]], folder,
                                    rate=RATE_RX)
        station = tb.station_hz                  # center_check moves the tuner off it
        dc_spike(tb)
        gain_check(tb, radio)
        center_check(tb, pi)
        native_check(tb, radio, station, pi)
    finally:
        tb.close()
    free, text = free_check()
    playback_check(iq_path, pi)
    window_check()
    assert ghosts[radio.settle_ms] == 0, f"ghosts at the default settle: {ghosts}"
    assert free, text
    print("HackRF hardware check passed")
    if '--keep' in sys.argv:
        print(f"files kept in {folder}")
    else:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    main()
