"""Hardware check with an RTL-SDR and an FM antenna - off air.

Not part of the no-radio tests; run it when an RTL-SDR is plugged in and
nothing else has it (no rtl_tcp running, this app closed):

    python tools/tests/hw_rtl_check.py [--address HOST[:PORT]] [--keep]
                                                         (about three minutes)

``--address`` checks a dongle on another computer, as ``--rtl-address``
does in the app: an ssh host, with rtl_tcp installed there.

1. **Found**: ``detect_radios`` lists it (on this computer only).
2. **Opened**: rtl_tcp is started by us (nothing was listening), and the
   tuner is named.
3. **Sweep settle time** of the LO-hopping sweep: the FM band against
   single-LO references at several settle times, as the HackRF check does.
   The default (100 ms) must show no ghosts, and no buffer is dropped.
4. **Reception**: the strongest stations until one carries RDS; stereo,
   a recording, and nothing dropped.
5. **Gain**: RDS, SNR and the clipped share at several gains; the default
   must decode and not warn of clipping.
6. **The DC spike**: its height at the LO, against the tuner's 100 kHz
   keep-out.
7. **Move the Center, play back** - as the BB60D check.
8. **Let go**: once the engine is closed, the rtl_tcp we started is gone
   and ``rtl_test`` - another program - can open the dongle.
9. **The window**: opens the RTL-SDR, sweeps the FM band, switches to
   Receive, shows the clipped share, and after **Stop** leaves the dongle
   free, with our rtl_tcp gone.
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

from fm_receiver import rtl_tcp  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import RTLSDR, detect_radios  # noqa: E402
from fm_receiver.sweep import SweepPlan, find_stations, to_db  # noqa: E402
from tests.hw_bb60_check import (center_check, playback_check,  # noqa: E402
                                 receive_check)
from tests.hw_hackrf_check import average_sweeps, count_ghosts, dc_spike  # noqa: E402

RATE = 2.4e6
ADDRESS = sys.argv[sys.argv.index('--address') + 1] if '--address' in sys.argv else ''


def run_there(script, timeout=20):
    """``script`` in a shell on the dongle's computer; (exit code, output)."""
    server = rtl_tcp.RtlTcpServer(rtl_tcp.parse_address(ADDRESS)[0], '', 0)
    out = subprocess.run(server._shell(script), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=timeout)
    return out.returncode, out.stdout + out.stderr


def nothing_listening():
    host, port = rtl_tcp.parse_address(ADDRESS)
    try:
        sock, _, _ = rtl_tcp.connect(rtl_tcp.resolve_ssh_host(host), port, timeout=2)
    except OSError:
        return True
    sock.close()
    return False


def gone(pid):
    code, _ = run_there(f'kill -0 {pid} 2>/dev/null')
    return code != 0


def free_check():
    """Another program can open the dongle now. ``rtl_test -t`` opens it,
    names it and exits (its E4000 benchmark gives up on other tuners)."""
    _, text = run_there('rtl_test -t 2>&1')
    ok = 'Found 1 device' in text and 'Failed to open' not in text
    print(f"rtl_test while this process runs: {'opened it' if ok else 'FAILED'}")
    return ok, text


def settle_check(tb, radio, settles=(0.0, 50.0, 100.0)):
    """Ghosts in the stitched FM-band sweep at each settle time, against
    single-LO references taken once. Returns (freqs, last sweep in dB,
    {settle: ghosts}, dropped buffers)."""
    u_frac = radio.usable_fraction(RATE)
    plan = SweepPlan(87.5e6, 108e6, RATE, 4096, u_frac, radio.dc_notch_hz)
    u = plan.usable_bins
    dropped0 = radio.health()['dropped']
    reference = np.full(plan.steps * u, np.nan)
    for k in range(plan.steps):
        lo = plan.start_hz + k * u * plan.rbw
        one = SweepPlan(lo, lo + u * plan.rbw, RATE, 4096, u_frac, radio.dc_notch_hz)
        assert one.steps == 1 and one.usable_bins == u, one.describe()
        tb.start_sweep(one, frames=16, settle_ms=radio.settle_ms)
        part, _ = average_sweeps(tb, 1.5)
        reference[k * u:(k + 1) * u] = part[:u]
    r_db = to_db(reference)
    quiet = r_db[:plan.total_bins] < np.median(r_db[:plan.total_bins]) + 6
    results, s_db = {}, None
    for settle in settles:
        tb.start_sweep(plan, frames=16, settle_ms=settle)
        stitched, n = average_sweeps(tb, 10.0)
        s_db = to_db(stitched)
        ghosts, unexplained = count_ghosts(s_db, r_db, u)
        ms = tb.sweeper.sweep_seconds * 1e3 if tb.sweeper.sweep_seconds else float('nan')
        results[settle] = ghosts
        print(f"settle {settle:4.0f} ms: {plan.steps} steps, {n} sweeps, {ms:4.0f} ms per "
              f"sweep, ghosts {ghosts} of {int(quiet.sum())} quiet bins"
              f" (+{unexplained} over a quiet bin with no source a step away)")
    share = tb.sweeper.clip_report()
    if share:
        print(f"  worst step clipped {share[0] * 100:.2f}% (centred on {share[1] / 1e6:.1f} MHz)")
    dropped = radio.health()['dropped'] - dropped0
    print(f"  dropped buffers while sweeping: {dropped}")
    assert tb.sweeper.sweep_seconds < 6.0, tb.sweeper.sweep_seconds
    return plan.freqs(), s_db, results, dropped


def gain_check(tb, radio, gains=(40, 60, 80, 100), seconds=8.0):
    """RDS, SNR and the clipped share on the tuned station at each gain,
    set live. Returns {gain: (RDS % good, clipped share)}."""
    rx = tb.rx
    rows = {}
    for g in gains:
        radio.apply_gain(g)
        time.sleep(1.0)
        rx.reset_decoders()
        rx.clip.take()
        t0 = time.time()
        while time.time() - t0 < seconds:
            rx.update_stereo()
            time.sleep(0.2)
        hit, n = rx.clip.take()
        clipped = hit / n if n else 0.0
        snap = rx.rds.snapshot()
        db = to_db(rx.rf_probe.snapshot())
        k = len(db) // 2 + int(round(tb.offset_hz / (tb.rate / len(db))))
        half = int(round(90e3 / (tb.rate / len(db))))
        snr = 10 * np.log10(np.mean(10 ** (db[k - half:k + half] / 10))) - np.median(db)
        good = 100 * (1 - snap['block_error_rate']) if snap['blocks_seen'] else 0
        rows[g] = (good, clipped)
        print(f"gain {g:3d}%: RDS {good:5.1f}% blocks good ({snap['groups']} groups), "
              f"SNR {snr:4.1f} dB, channel {rx.channel_power_db():6.1f} dBFS, "
              f"clipped {clipped * 100:.2f}%")
    radio.apply_gain(radio.default_gain)
    return rows


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

    argv = ['--radio', 'rtlsdr', '--mode', 'sweep', '--no-audio']
    if ADDRESS:
        argv += ['--rtl-address', ADDRESS]
    w = fmapp.MainWindow(fmapp.parse_args(argv), {})
    try:
        w.show()
        pump(0.2)
        w.start_initial()
        assert w._mode == 'sweep' and w.engine.running, w.status.text()
        pid = w.radio.server.pid if w.radio.server else None
        assert pid, 'the window should have started rtl_tcp'
        w.preset_combo.setCurrentIndex(1)                 # FM broadcast
        w._preset_chosen(1)
        pump(12, lambda: 'clipped' in w.status.text())
        swept = w.status.text()
        print(f"window: sweeping the FM band the status reads {swept!r}")
        assert 'clipped' in swept and 'overloaded' not in swept, swept
        w.tabs.setCurrentIndex(1)
        assert w._mode == 'receive' and w.engine.running, w.status.text()
        pump(4)
        calm = w.status.text()
        print(f"window: receiving it reads {calm!r}")
        assert 'clipped' in calm and 'overloaded' not in calm, calm
        w.run_btn.click()                                 # Stop
        assert w.radio is None and w.run_btn.text() == 'Start'
        assert gone(pid), f'rtl_tcp (pid {pid}) is still running after Stop'
        free, _ = free_check()
        assert free, 'Stop should leave the dongle free for other programs'
    finally:
        w.close()


def main():
    where = ADDRESS or 'this computer'
    assert nothing_listening(), (
        f"an rtl_tcp is already listening on {where}: close the app (or that "
        "rtl_tcp) first, so the check starts and stops its own")
    if not ADDRESS:
        found = detect_radios()
        print("detected:", found)
        assert 'rtlsdr' in found, found
    folder = tempfile.mkdtemp(prefix='fmrx-rtl-')
    radio = RTLSDR(ADDRESS)
    tb = Engine(want_audio=False)
    t0 = time.time()
    tb.use_radio(radio)
    pid = radio.server.pid if radio.server else None
    print(f"opened {radio.describe()} in {time.time() - t0:.1f} s, rtl_tcp pid {pid}")
    assert pid, 'rtl_tcp should have been started by us'
    try:
        freqs, db, ghosts, sweep_dropped = settle_check(tb, radio)
        stations = find_stations(freqs, db, min_snr_db=20)
        print("strongest:", ", ".join(f"{f / 1e6:.1f} ({s:.0f} dB)" for f, s, _ in stations[:6]))
        dropped0 = radio.health()['dropped']
        pi, iq_path = receive_check(tb, radio, [f for f, _, _ in stations[:5]], folder,
                                    rate=RATE)
        rx_dropped = radio.health()['dropped'] - dropped0
        print(f"dropped buffers while receiving: {rx_dropped}")
        dc_spike(tb)
        gains = gain_check(tb, radio)
        center_check(tb, pi)
    finally:
        tb.close()
    stopped = gone(pid)
    print(f"rtl_tcp (pid {pid}) after close: {'gone' if stopped else 'STILL RUNNING'}")
    free, text = free_check()
    playback_check(iq_path, pi)
    window_check()
    assert ghosts[radio.settle_ms] == 0, f"ghosts at the default settle: {ghosts}"
    assert sweep_dropped == 0 and rx_dropped == 0, (sweep_dropped, rx_dropped)
    good, clipped = gains[radio.default_gain]
    assert good > 80 and clipped < 0.01, gains[radio.default_gain]
    assert stopped, f'rtl_tcp (pid {pid}) was left running'
    assert free, text
    print("RTL-SDR hardware check passed")
    if '--keep' in sys.argv:
        print(f"files kept in {folder}")
    else:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    main()
