"""Hardware check with a HackRF One and an FM antenna - off air.

Not part of the no-radio tests; run it when a HackRF is plugged in, and
nothing else has it open:

    python tools/tests/hw_hackrf_check.py [--keep]     (about three minutes)

1. **Found**: ``detect_radios`` lists it.
2. **Sweep settle time.** The HackRF's queue of samples in USB transfers is
   invisible to the sweep's backlog count, so its settle time has to cover
   it; 40 ms was an estimate. Sweeps the FM band in two steps at several
   settle times against single-LO references (as the BB60D check does),
   and reports the ghosts at each. The default must show none.
3. **Reception and gain.** Tunes the strongest stations until one carries
   RDS, then tries several gains on it: RDS blocks good, SNR, pilot.
4. **The DC spike**: its height at the LO, against the tuner's 100 kHz
   keep-out.
5. **Record, move the Center, play back** - as the BB60D check.
6. **Mode switch time**, Sweep to Receive.
7. **Let go**: once the engine is closed, ``hackrf_info`` - another
   program - must be able to open the HackRF while this one still runs.
8. **The window**: opens the HackRF, sweeps, switches to Receive, warns
   of clipping at too much gain and not at the default, and after **Stop**
   leaves the HackRF free for another program.
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
from tests.hw_bb60_check import (average_sweeps, center_check,  # noqa: E402
                                 playback_check, receive_check)

RATE_RX = 2e6


def settle_check(tb, radio, settles=(0.0, 5.0, 10.0, 20.0, 40.0)):
    """Ghosts in a stitched FM-band sweep at each settle time, against
    single-LO references taken once."""
    rate = 20e6
    u_frac = radio.usable_fraction(rate)
    plan = SweepPlan(87.5e6, 108e6, rate, 4096, u_frac, radio.dc_notch_hz)
    assert plan.steps == 2, plan.describe()
    reference = None
    u = plan.usable_bins
    for k in range(plan.steps):
        lo = plan.start_hz + k * u * plan.rbw
        one = SweepPlan(lo, lo + u * plan.rbw, rate, 4096, u_frac, radio.dc_notch_hz)
        tb.start_sweep(one, frames=16, settle_ms=radio.settle_ms)
        part, m = average_sweeps(tb, 2.0)
        if reference is None:
            reference = np.zeros(plan.total_bins)
        j1 = min((k + 1) * u, len(reference))
        reference[k * u:j1] = part[:j1 - k * u]
    r_db = to_db(reference)
    floor = np.median(r_db)
    quiet = r_db < floor + 6
    results = {}
    s_db = None
    for settle in settles:
        tb.start_sweep(plan, frames=16, settle_ms=settle)
        stitched, n = average_sweeps(tb, 3.0)
        s_db = to_db(stitched)
        excess = s_db[quiet] - r_db[quiet]
        ghosts = int(np.sum(excess > 15))
        diff = float(np.median(np.abs(s_db - r_db)))
        ms = tb.sweeper.sweep_seconds * 1e3 if tb.sweeper.sweep_seconds else float('nan')
        results[settle] = ghosts
        print(f"settle {settle:4.0f} ms: {n:3d} sweeps, {ms:4.0f} ms per sweep, "
              f"median |diff| {diff:.2f} dB, ghosts {ghosts} of {int(quiet.sum())} quiet bins")
    return plan.freqs(), s_db, results


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
        good = 100 * (1 - (snap['block_error_rate'] or 1)) if snap['blocks_seen'] else 0
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
        pump(3)
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
            if pump(3, lambda: 'clipped' in w.status.text()):
                break
        loud = w.status.text()
        print(f"window: at {HackRF.default_gain}% gain the status reads {calm!r}")
        print(f"window: at {loud_gain}% gain it reads {loud!r}")
        assert 'overloaded' not in calm and 'clipped' in loud
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
        dc_spike(tb)
        gain_check(tb, radio)
        center_check(tb, pi)
        t0 = time.time()
        plan = SweepPlan(87.5e6, 108e6, 20e6, 4096, radio.usable_fraction(20e6),
                         radio.dc_notch_hz)
        tb.start_sweep(plan, frames=16)
        t1 = time.time()
        tb.start_receive(tb.station_hz, RATE_RX)
        t2 = time.time()
        print(f"mode switch: to Sweep {(t1 - t0) * 1e3:.0f} ms, to Receive {(t2 - t1) * 1e3:.0f} ms")
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
