"""Hardware check with a Signal Hound BB60D and an FM antenna - off air.

Not part of the no-radio tests; run it when a BB60D is plugged in:

    python tools/tests/hw_bb60_check.py [--keep]     (about two minutes)

1. **Sweep stitching.** Sweeps the FM band in two steps, and compares it
   bin by bin with two single-step sweeps - each one LO, never retuned, so
   nothing stale can reach them. A stitched bin standing well above its
   reference would be a ghost of the other step.
2. **Reception.** Tunes the strongest stations the sweep found, in turn,
   until one carries RDS - not all do: here 102.1, the strongest, sends a
   pilot and stereo but nothing at 57 kHz, and the RF bench toolkit's own
   offline decoder finds no RDS in it either. Requires the stereo pilot and
   RDS (a PI code, blocks mostly good).
3. **Record and play back.** Records audio and channel IQ from it, then plays
   the IQ recording back through the file source and requires the same PI.
4. **Move the Center.** With the station still inside the band, moves the
   radio's centre past it: the station must stay put and decode the same PI
   again. Moved far, the tuner must be pulled in to the band's edge.
5. **The BB60D's own sweep** (``bb60_sweep``), on the device the IQ stream
   opened: 9 kHz-6 GHz under half a second a sweep, finding the station;
   the FM band at 10 kHz RBW; the FM band in real time (30 frames a
   second, the density map the right way up - not on a Mac, whose library
   has no real time); the gain slider; then back to Receive, which must
   decode the same PI - the sweep's settings must not leak into the
   stream - with both switches timed.
6. **Let go**: once the engine is closed, another program can open the
   BB60D. Not on a Mac, where the app keeps the device until it quits
   (``bb60_source.KEEP_OPEN``: Signal Hound's Mac library traps in
   ``bbCloseDevice``). There a new radio in the same process must get the
   device back and decode the station again - the window's Stop, then
   Start.
"""

import subprocess

import os
import shutil
import sys
import tempfile
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import BB60, IQFile  # noqa: E402
from fm_receiver.recording import IqRecording, WavWriter  # noqa: E402
from fm_receiver.bb60_sweep import REALTIME_OK, NativeSweepPlan  # noqa: E402
from fm_receiver.bb60_source import KEEP_OPEN  # noqa: E402

#: Receive at the lowest rate the radio offers: 2.5 MS/s, or 5 on a Mac,
#: whose library streams garbage below that (see radios.BB60).
RX_RATE = min(BB60.receive_rates)
from fm_receiver.sweep import SweepPlan, find_stations, to_db  # noqa: E402


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


def sweep_check(tb, radio, rate=20e6):
    plan = SweepPlan(87.5e6, 108e6, rate, 4096, radio.usable_fraction(rate),
                     radio.dc_notch_hz)
    assert plan.steps == 2, plan.describe()
    tb.start_sweep(plan, frames=16, settle_ms=radio.settle_ms)
    stitched, n = average_sweeps(tb, 4.0)
    print(f"stitched: {plan.describe()}, {n} sweeps averaged, "
          f"{tb.sweeper.sweep_seconds * 1e3:.0f} ms per sweep")
    reference = np.zeros_like(stitched)
    u = plan.usable_bins
    for k in range(plan.steps):
        lo = plan.start_hz + k * u * plan.rbw
        one = SweepPlan(lo, lo + u * plan.rbw, rate, 4096, radio.usable_fraction(rate),
                        radio.dc_notch_hz)
        assert one.steps == 1
        tb.start_sweep(one, frames=16, settle_ms=radio.settle_ms)
        part, m = average_sweeps(tb, 2.0)
        j1 = min((k + 1) * u, len(reference))
        reference[k * u:j1] = part[:j1 - k * u]
        print(f"reference step {k}: LO {one.center(0) / 1e6:.2f} MHz, {m} captures")
    s_db, r_db = to_db(stitched), to_db(reference)
    floor = np.median(r_db)
    quiet = r_db < floor + 6                       # bins with nothing in them
    excess = s_db[quiet] - r_db[quiet]
    ghosts = int(np.sum(excess > 15))
    diff = np.abs(s_db - r_db)
    print(f"stitched vs reference: median |diff| {np.median(diff):.2f} dB, "
          f"quiet bins {int(quiet.sum())}, ghosts (>15 dB over a quiet bin) {ghosts}")
    assert np.median(diff) < 2.0
    assert ghosts == 0
    return plan.freqs(), s_db


def tune_for_rds(tb, radio, station, wait_s=15, rate=RX_RATE):
    tb.start_receive(station, rate, region='RBDS', stereo=True, volume=0.0)
    rx = tb.rx
    t0 = time.time()
    snap = rx.rds.snapshot()
    while time.time() - t0 < wait_s:
        rx.update_stereo()
        snap = rx.rds.snapshot()
        if snap['pi_hex'] and snap['blocks_seen'] > 300 and snap['station_name']:
            break
        time.sleep(0.2)
    print(f"{station / 1e6:.1f} MHz: PI {snap['pi_hex']} ({snap['callsign_confirmed'] or snap['callsign']}), "
          f"name {snap['station_name']!r}, PS {snap['ps']!r}, PTY {snap['pty']}, "
          f"blocks {snap['blocks_ok']}/{snap['blocks_seen']}, pilot {rx.pilot_level():.1e}, "
          f"channel {rx.channel_power_db():.1f} dBFS, health {radio.health()}")
    return snap


def receive_check(tb, radio, stations, folder, rate=RX_RATE):
    # The first station whose RDS decodes cleanly - which that is depends
    # on where the antenna is (the Mac mini's first pick, 90.1, lost 23%).
    for station in stations:
        snap = tune_for_rds(tb, radio, station, rate=rate)
        if snap['pi_hex'] and snap['block_error_rate'] < 0.2:
            break
    rx = tb.rx
    assert rx.pilot_locked(), 'no stereo pilot'
    assert snap['pi_hex'], 'no RDS on any of the strongest stations'
    assert snap['block_error_rate'] < 0.2, snap['block_error_rate']

    wav = WavWriter(os.path.join(folder, 'live.wav'))
    rx.tap.set_writer(wav)
    rec = IqRecording(rx.channel_sink, 'channel', folder, rx.channel_rate, tb.station_hz,
                      tb.station_hz, radio.describe())
    rec.start()
    for _ in range(40):                              # 8 s
        rx.update_stereo()
        time.sleep(0.2)
    stereo, phase, coherence = rx.update_stereo()
    rx.tap.set_writer(None)
    wav.close()
    paths = rec.stop()
    with wave.open(wav.path) as w:
        frames = w.getnframes()
        pcm = np.frombuffer(w.readframes(frames), dtype='<i2').reshape(-1, 2) / 32767
    rms = np.sqrt(np.mean(pcm ** 2, axis=0))
    corr = np.corrcoef(pcm[:, 0], pcm[:, 1])[0, 1]
    print(f"recorded {frames / 48000:.1f} s of audio, rms L {rms[0]:.3f} R {rms[1]:.3f}, "
          f"L/R correlation {corr:.2f}; stereo {stereo} at "
          f"{np.degrees(phase):.0f} deg, coherence {coherence:.2f}; "
          f"IQ {os.path.getsize(paths[0]) / 8 / rx.channel_rate:.1f} s")
    assert abs(frames / 48000 - 8) < 1.5
    assert rms.min() > 0.01, 'no audio'
    return snap['pi_hex'], paths[0]


def center_check(tb, pi):
    station = tb.station_hz
    lo = tb.lo_hz
    assert tb.set_center(station + 400e3), 'the LO should move'
    assert abs(tb.lo_hz - (station + 400e3)) < 1 and abs(tb.station_hz - station) < 1
    rx = tb.rx
    t0 = time.time()
    snap = rx.rds.snapshot()
    while time.time() - t0 < 20 and not (snap['pi_hex'] and snap['blocks_seen'] > 200):
        rx.update_stereo()
        time.sleep(0.2)
        snap = rx.rds.snapshot()
    print(f"centre {lo / 1e6:.3f} -> {tb.lo_hz / 1e6:.3f} MHz, station {station / 1e6:.3f} held: "
          f"PI {snap['pi_hex']}, blocks {snap['blocks_ok']}/{snap['blocks_seen']}, "
          f"pilot {'locked' if rx.pilot_locked() else 'lost'}")
    assert snap['pi_hex'] == pi and rx.pilot_locked()
    edge = tb.radio.max_offset(tb.rate)
    tb.set_center(station + 3e6)                      # the station is out of reach now
    assert abs(tb.station_hz - (station + 3e6 - edge)) < 1, tb.station_hz
    print(f"centre moved 3 MHz: tuner pulled in to {tb.station_hz / 1e6:.3f} MHz (the edge)")


def _next_sweep(tb, after, seconds=5.0):
    """The first whole sweep with a serial past ``after``."""
    end = time.time() + seconds
    while time.time() < end:
        freqs, _, done, serial = tb.sweeper.snapshot()
        if done is not None and serial > after:
            return freqs, done, serial
        time.sleep(0.01)
    raise AssertionError(f"no sweep in {seconds} s: {tb.sweeper.error}")


def native_check(tb, radio, station, pi):
    t0 = time.time()
    tb.start_sweep(NativeSweepPlan(9e3, 6000e6))
    to_sweep = time.time() - t0
    s = tb.sweeper
    freqs, db, serial = _next_sweep(tb, 0)
    for _ in range(5):
        freqs, db, serial = _next_sweep(tb, serial)
    print(f"own sweep: {s.plan.describe()}, {freqs[0] / 1e3:.1f} kHz to {freqs[-1] / 1e6:.1f} MHz, "
          f"{s.sweep_seconds * 1e3:.0f} ms a sweep "
          f"({(s.plan.stop_hz - s.plan.start_hz) / s.sweep_seconds / 1e9:.1f} GHz/s)")
    assert s.sweep_seconds < 0.5, s.sweep_seconds
    assert freqs[0] >= 9e3 - s.plan.bin_hz and freqs[-1] <= 6000e6 + s.plan.bin_hz
    fm = (freqs >= 87.5e6) & (freqs <= 108e6)
    found = find_stations(freqs[fm], db[fm], min_snr_db=15)
    print("  FM stations in it:", ", ".join(f"{f / 1e6:.1f} ({lv:.0f} dBm)"
                                           for f, _, lv in sorted(found, key=lambda x: -x[1])[:6]))
    assert any(abs(f - station) < 150e3 for f, _, _ in found), (station, found)
    # The FM band, finely.
    s.set_plan(NativeSweepPlan(87.5e6, 108e6, 10e3))
    freqs, db, serial = _next_sweep(tb, serial)
    freqs, db, serial = _next_sweep(tb, serial)
    k = np.abs(freqs - station) < 90e3
    print(f"  FM band at 10 kHz: {s.plan.points} points, {s.sweep_seconds * 1e3:.0f} ms a sweep, "
          f"{station / 1e6:.1f} at {db[k].max():.1f} dBm, floor {np.median(db):.1f} dBm")
    assert s.sweep_seconds < 0.1
    if REALTIME_OK:
        realtime_check(tb, station)
    else:
        print("real time: skipped - not in Signal Hound's Mac library")
    # The slider reaches the sweep: at 0% the attenuator is in and the floor rises.
    floors = {}
    for g in (0, radio.default_gain):
        radio.apply_gain(g)
        freqs, db, serial = _next_sweep(tb, serial)
        freqs, db, serial = _next_sweep(tb, serial)
        floors[g] = float(np.median(db))
    print(f"  floor at 0% gain {floors[0]:.1f} dBm, at {radio.default_gain}% {floors[radio.default_gain]:.1f}")
    assert floors[0] > floors[radio.default_gain] + 10, floors
    # Back to Receive, on the same open device.
    t0 = time.time()
    tb.start_receive(station, RX_RATE, region='RBDS', stereo=True, volume=0.0)
    to_receive = time.time() - t0
    rx = tb.rx
    snap = rx.rds.snapshot()
    while time.time() - t0 < 20 and not (snap['pi_hex'] and snap['blocks_seen'] > 200):
        rx.update_stereo()
        time.sleep(0.2)
        snap = rx.rds.snapshot()
    print(f"  back in Receive: PI {snap['pi_hex']}, blocks {snap['blocks_ok']}/{snap['blocks_seen']}, "
          f"channel {rx.channel_power_db():.1f} dBFS, pilot {'locked' if rx.pilot_locked() else 'lost'}")
    print(f"  switches: Receive -> own sweep {to_sweep * 1e3:.0f} ms; "
          f"own sweep -> Receive {to_receive * 1e3:.0f} ms")
    assert snap['pi_hex'] == pi and rx.pilot_locked(), (snap['pi_hex'], pi)


def realtime_check(tb, station):
    """Real time over the FM band: 30 frames a second, an intercept time,
    and a density map the right way up - at the station, the highest level
    it hit matches the trace's peak there."""
    s = tb.sweeper
    serial = s.snapshot()[3]
    s.set_plan(NativeSweepPlan(87.5e6, 108e6, None, realtime=True, ref_db=-20, scale_db=100))
    freqs, db, serial = _next_sweep(tb, serial)
    t0, n0 = time.time(), s.sweeps
    for _ in range(30):
        freqs, db, serial = _next_sweep(tb, serial)
    fps = (s.sweeps - n0) / (time.time() - t0)
    frame, (lo, hi, bottom, top) = s.density()
    rows, cols = frame.shape
    col = int(round((station - lo) / (hi - lo) * (cols - 1)))
    near = frame[:, col - 2:col + 3].sum(axis=1)
    top_hit = bottom + (np.nonzero(near > 0)[0].max() + 0.5) * (top - bottom) / rows
    f_lo = lo + (col - 2) / cols * (hi - lo)
    f_hi = lo + (col + 3) / cols * (hi - lo)
    peak = db[(freqs >= f_lo) & (freqs <= f_hi)].max()
    print(f"  real time: {s.plan.describe()}, {fps:.1f} frames a second, map {cols} x {rows}; "
          f"at {station / 1e6:.1f} the map's highest hit {top_hit:.1f} dBm, the trace's peak "
          f"{peak:.1f} dBm")
    assert 25 <= fps <= 35, fps
    assert s.plan.poi_s and s.plan.poi_s < 1e-3, s.plan.poi_s
    assert abs(top_hit - peak) < 3, (top_hit, peak)


def free_check():
    """Another program can open the BB60D now."""
    code = ("import ctypes; from fm_receiver.bb60_sweep import load_api; lib = load_api(); "
            "d = ctypes.c_int(-1); r = lib.bbOpenDevice(ctypes.byref(d)); print(r); "
            "lib.bbCloseDevice(d)")
    if KEEP_OPEN:
        # A Mac: bbCloseDevice would trap, and the output with it; the
        # process ending lets the device go.
        code = code.replace("print(r); lib.bbCloseDevice(d)", "print(r, flush=True)")
    env = dict(os.environ, LD_LIBRARY_PATH='/usr/local/lib', PYTHONPATH=os.path.dirname(HERE))
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                         timeout=60, env=env)
    ok = out.stdout.strip().splitlines()[-1:] == ['0']
    print(f"another program opening the BB60D after close: {'opened it' if ok else out.stdout + out.stderr}")
    return ok


def reopen_check(station, pi):
    """A Mac: the engine closed, a new radio gets the kept device back."""
    radio = BB60()
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        snap = tune_for_rds(tb, radio, station)
    finally:
        tb.close()
    print(f"reopened in this process (the device kept): PI {snap['pi_hex']}")
    assert snap['pi_hex'] == pi, (snap['pi_hex'], pi)


def playback_check(path, pi):
    radio = IQFile(path, repeat=True, throttle=True)
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    tb.start_receive(radio.meta['station_hz'], radio.rate)
    t0 = time.time()
    snap = tb.rx.rds.snapshot()
    while time.time() - t0 < 20 and not snap['pi_hex']:
        time.sleep(0.2)
        snap = tb.rx.rds.snapshot()
    print(f"played back: PI {snap['pi_hex']}, name {snap['station_name']!r}, "
          f"blocks {snap['blocks_ok']}/{snap['blocks_seen']}")
    tb.close()
    assert snap['pi_hex'] == pi


def main():
    folder = tempfile.mkdtemp(prefix='fmrx-bb60-')
    radio = BB60()
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        freqs, db = sweep_check(tb, radio)
        found = find_stations(freqs, db, min_snr_db=20)
        print("strongest:", ", ".join(f"{f / 1e6:.1f} ({s:.0f} dB)" for f, s, _ in found[:6]))
        pi, iq_path = receive_check(tb, radio, [f for f, _, _ in found[:5]], folder)
        station = tb.station_hz
        center_check(tb, pi)
        native_check(tb, radio, station, pi)
    finally:
        tb.close()
    if KEEP_OPEN:
        print("let go: not on a Mac - the app keeps the BB60D until it quits")
        reopen_check(station, pi)
        free = True
    else:
        free = free_check()
    playback_check(iq_path, pi)
    assert free, 'the BB60D should be free once closed'
    print("BB60D hardware check passed")
    if '--keep' in sys.argv:
        print(f"files kept in {folder}")
    else:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    main()
