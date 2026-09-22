"""Hardware check with a Signal Hound BB60D and an FM antenna - off air.

Not part of the no-radio tests; run it when a BB60D is plugged in:

    python tools/tests/hw_bb60_check.py [--keep]     (about a minute)

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
"""

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


def tune_for_rds(tb, radio, station, wait_s=15, rate=2.5e6):
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


def receive_check(tb, radio, stations, folder, rate=2.5e6):
    for station in stations:
        snap = tune_for_rds(tb, radio, station, rate=rate)
        if snap['pi_hex']:
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
        center_check(tb, pi)
    finally:
        tb.close()
    playback_check(iq_path, pi)
    print("BB60D hardware check passed")
    if '--keep' in sys.argv:
        print(f"files kept in {folder}")
    else:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    main()
