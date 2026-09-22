"""The receive chain end to end, on a synthetic broadcast - no radio, no
sound card, no window.

A station 300 kHz off centre in a 2.5 MS/s recording, left channel a 1 kHz
tone and right a 2.5 kHz one, with RDS. Played through the real
:class:`Engine` from an IQ file in real time, it must:

- decode the PI, the PS name and the RadioText;
- lock the pilot and find the right 38 kHz phase for both conventions - the
  broadcast standard's sines and the RF bench toolkit transmitter's cosines;
- separate the channels by at least 30 dB, the right way round;
- write a WAV of the right length, and IQ recordings with their metadata;
- go silent when muted, and keep recording while muted.

Run:  python tools/tests/test_receive_chain.py [--keep]     (about 30 s)
"""

import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver.dsp import CHANNEL_RATE, PHASE_STANDARD, PHASE_COSINE  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import IQFile, read_iq_metadata  # noqa: E402
from fm_receiver.recording import IqRecording, WavWriter  # noqa: E402
from tests import signals  # noqa: E402

SECONDS = 9.0


def run_station(folder, convention):
    base = os.path.join(folder, f"station-{convention}")
    path = signals.write_station(base, seconds=SECONDS, convention=convention)
    radio = IQFile(path, repeat=False, throttle=True)
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    tb.start_receive(98.7e6, radio.rate, region='RBDS', stereo=True,
                     volume=0.5)
    rx = tb.rx
    assert abs(tb.offset_hz - 300e3) < 1, tb.offset_hz

    wav = WavWriter(os.path.join(folder, f"audio-{convention}.wav"))
    rx.tap.set_writer(wav)
    band = IqRecording(rx.band_sink, 'band', folder, tb.rate, tb.lo_hz,
                       tb.station_hz, 'test')
    channel = IqRecording(rx.channel_sink, 'channel', folder, rx.channel_rate,
                          tb.station_hz, tb.station_hz, 'test')
    band.start()
    channel.start()

    stereo_seen = []
    done = threading.Event()

    def poll():
        while not done.is_set():
            stereo_seen.append(rx.update_stereo())
            time.sleep(0.1)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    time.sleep(1.0)
    band.stop()                     # one second of band IQ is plenty
    time.sleep(1.0)
    # Muted for a second: the output gain is zero, and the tap - which is
    # before the volume stage - still hears the station, so the WAV runs on.
    rx.set_muted(True)
    time.sleep(0.3)
    rx.tap.levels()
    time.sleep(0.5)
    muted_peak = max(rx.tap.levels()[0])
    muted_gain = rx.vol_l.k()
    rx.set_muted(False)
    tb.wait()                       # the file ends
    done.set()
    poller.join()
    rx.tap.set_writer(None)
    wav.close()
    channel.stop()
    snap = rx.rds.snapshot()
    result = {'snap': snap, 'stereo': stereo_seen, 'phase': rx.stereo_phase,
              'wav': wav.path, 'band': band.paths, 'channel': channel.paths,
              'pilot': rx.pilot_level(), 'power_db': rx.channel_power_db(),
              'rf': rx.rf_probe.snapshot(), 'mpx': rx.mpx_probe.snapshot(),
              'muted_peak': muted_peak, 'muted_gain': muted_gain}
    tb.close()
    return result


def separation(wav_path):
    with wave.open(wav_path) as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    pcm = np.frombuffer(frames, dtype='<i2').reshape(-1, 2) / 32767.0
    tail = pcm[-int(2.5 * rate):]
    left, right = tail[:, 0], tail[:, 1]
    l1k, r1k = (signals.tone_level(left, rate, 1000),
                signals.tone_level(right, rate, 1000))
    l25, r25 = (signals.tone_level(left, rate, 2500),
                signals.tone_level(right, rate, 2500))
    return {'frames': len(pcm), 'rate': rate,
            'left_1k': l1k, 'right_1k': r1k, 'left_2k5': l25, 'right_2k5': r25,
            'sep_left_db': 20 * math.log10(l1k / max(r1k, 1e-9)),
            'sep_right_db': 20 * math.log10(r25 / max(l25, 1e-9))}


def check(convention, expect_phase):
    folder = tempfile.mkdtemp(prefix=f"fmrx-{convention}-")
    r = run_station(folder, convention)
    snap = r['snap']
    print(f"--- {convention}: PI {snap['pi_hex']}  PS {snap['ps']!r}  "
          f"name {snap['station_name']!r}  RT {snap['radiotext']!r}  "
          f"blocks {snap['blocks_ok']}/{snap['blocks_seen']}")
    sep = separation(r['wav'])
    print(f"    pilot {r['pilot']:.2e}, channel {r['power_db']:.1f} dBFS, "
          f"phase used {math.degrees(r['phase']):.0f} deg "
          f"(expected {math.degrees(expect_phase):.0f}), last stereo "
          f"{r['stereo'][-1]}")
    print(f"    WAV {sep['frames'] / sep['rate']:.2f} s; L: 1k {sep['left_1k']:.3f} "
          f"2.5k {sep['left_2k5']:.4f}; R: 1k {sep['right_1k']:.4f} "
          f"2.5k {sep['right_2k5']:.3f}; separation L {sep['sep_left_db']:.1f} dB, "
          f"R {sep['sep_right_db']:.1f} dB")

    assert snap['pi_hex'] == '0x1234', snap['pi_hex']
    assert snap['station_name'].strip() == 'TEST FM', snap['station_name']
    assert snap['radiotext'].startswith('Hello from the synthetic station'), \
        snap['radiotext']
    assert snap['block_error_rate'] is not None and snap['block_error_rate'] < 0.02
    assert r['pilot'] > 1e-4, r['pilot']
    assert r['muted_gain'] == 0.0 and r['muted_peak'] > 0.1, r
    diff = (r['phase'] - expect_phase + math.pi) % (2 * math.pi) - math.pi
    assert abs(diff) < math.radians(15), math.degrees(r['phase'])
    assert r['stereo'][-1][0], 'stereo should be on'
    assert sep['sep_left_db'] > 30 and sep['sep_right_db'] > 30, sep
    assert sep['left_1k'] > 0.05 and sep['right_2k5'] > 0.05, sep
    # The WAV runs from the start to the end of the file, near enough.
    assert abs(sep['frames'] / sep['rate'] - SECONDS) < 1.0, sep['frames']

    # IQ recordings: data, SigMF and toolkit metadata, and readable back.
    band = r['band'][0]
    meta = read_iq_metadata(band)
    assert meta['rate'] == 2.5e6 and abs(meta['center_hz'] - 98.4e6) < 1, meta
    n = os.path.getsize(band) // 8
    assert 0.5 * 2.5e6 < n < 1.6 * 2.5e6, n
    sig = json.load(open(band[:-len('.cfile')] + '.sigmf-meta'))
    assert sig['global']['core:datatype'] == 'cf32_le'
    chan = r['channel'][0]
    cmeta = read_iq_metadata(chan[:-len('.cfile')] + '.json')
    assert cmeta['rate'] == CHANNEL_RATE and abs(cmeta['center_hz'] - 98.7e6) < 1
    m = os.path.getsize(chan) // 8
    assert abs(m / CHANNEL_RATE - SECONDS) < 1.0, m
    x = np.fromfile(band, dtype=np.complex64, count=4096)
    assert np.isfinite(x).all() and np.abs(x).mean() > 0.1

    rf, mpx = r['rf'], r['mpx']
    assert rf is not None and len(rf) == 4096
    assert mpx is not None and len(mpx) == 2048
    # The pilot stands out in the MPX spectrum: bin of 19 kHz at 250 kHz/2048.
    k = int(round(19e3 / 250e3 * 2048))
    assert 10 * np.log10(mpx[k - 1:k + 2].max() / np.median(mpx[:1024])) > 20
    print(f"    recordings OK in {folder}")
    return folder


def test_volume_law():
    """Volume is square law up to 1.5x; mute is zero whatever the volume."""
    from fm_receiver.dsp import ReceiveChain

    class Chain:
        muted, volume = True, 0.7
    assert ReceiveChain._gain(Chain()) == 0.0
    Chain.muted = False
    assert abs(ReceiveChain._gain(Chain()) - 1.5 * 0.49) < 1e-9


if __name__ == '__main__':
    test_volume_law()
    folders = [check('standard', PHASE_STANDARD), check('cosine', PHASE_COSINE)]
    print("receive chain: all checks passed")
    if '--keep' not in sys.argv:
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
