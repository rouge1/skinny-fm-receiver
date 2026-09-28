"""HD Radio's plumbing - no radio, no sound card, no nrsc5.

nrsc5 itself is not needed: a stand-in program takes its place, reading
the IQ from stdin as nrsc5 would and writing a log and a tone (3 kHz for
HD1, 3.5 kHz for HD2) at 44.1 kHz for as much IQ as it is given. Played
through the real :class:`Engine` from the synthetic station (1 kHz left,
2.5 kHz right), it must:

- read nrsc5's log: sync, station, services, programs, BER, now playing;
- hold back audio until a little is buffered, and drop the excess;
- feed the stand-in the station at nrsc5's 744,187.5 S/s, scaled to a
  steady level;
- play the digital audio in place of the analog once it comes, switch
  program by starting the decoder again, and go back to the analog when
  switched off;
- stop the decoder when the engine closes.

Run:  python tools/tests/test_hdradio.py     (about 20 s)
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import hdradio  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.hdradio import HdRadio, empty_state, parse_line  # noqa: E402
from fm_receiver.radios import IQFile  # noqa: E402
from fm_receiver.recording import WavWriter  # noqa: E402
from tests import signals  # noqa: E402

# The stand-in: what nrsc5 -r - --iq-input-format cf32 -o - -t raw N does,
# as far as the window can tell. It notes what it was fed in STATS.
FAKE = r'''#!{python}
import json, sys, time
import numpy as np
program = int(sys.argv[-1])
tone = 3000.0 + 500.0 * program
log = sys.stderr
def say(text):
    log.write(time.strftime("%H:%M:%S ") + text + "\n"); log.flush()
say("Synchronized")
say("Primary service mode: 3")
say("Station name: FAKE")
say("Slogan: Fake FM")
say("Audio program 0: public, type: News, sound experience 0")
say("Audio program 1: public, type: Jazz, sound experience 0")
say("Audio service 0: public, type: News, codec: 0, blend: 2, gain: 0 dB, delay: 96, latency: 8")
say("Audio service 1: public, type: Jazz, codec: 0, blend: 0, gain: 0 dB, delay: 0, latency: 8")
say("SIG Service: type=audio number=1 name=HD1")
say("SIG Service: type=audio number=2 name=Fake Jazz")
say("Title: Song %d" % (program + 1))
say("Artist: The Fakes")
say("BER: 0.010000, avg: 0.010000, min: 0.010000, max: 0.010000")
out = sys.stdout.buffer
fed, sq, phase, t0, owed = 0, 0.0, 0, time.monotonic(), 0.0
while True:
    data = sys.stdin.buffer.read(65536)
    if not data:
        break
    iq = np.frombuffer(data[:len(data) - len(data) % 8], dtype=np.complex64)
    fed += len(iq)
    sq += float(np.sum(np.abs(iq) ** 2))
    owed += len(iq) * 44100 / 744187.5
    n = int(owed)
    owed -= n
    t = (phase + np.arange(n)) / 44100.0
    phase += n
    pcm = (0.3 * np.sin(2 * np.pi * tone * t) * 32767).astype('<i2')
    out.write(np.repeat(pcm, 2).tobytes())
    out.flush()
    with open("{stats}", "w") as fh:
        json.dump({{"fed": fed, "seconds": time.monotonic() - t0,
                   "rms": (sq / max(fed, 1)) ** 0.5}}, fh)
'''

LOG = """\
19:51:52 Synchronized
19:51:52 Frequency offset: 43 Hz
19:51:52 Primary service mode: 3
19:51:52 Station name: WIAD
19:51:52 Audio program 0: public, type: Adult Hits, sound experience 0
19:51:52 Audio program 1: public, type: None, sound experience 0
19:51:52 Slogan: HD1
19:51:52 MER: -2.7 dB (lower), -1.4 dB (upper)
19:51:52 BER: 0.103680, avg: 0.103680, min: 0.103680, max: 0.103680
19:51:52 Audio service 0: public, type: Adult Hits, codec: 0, blend: 2, gain: 0 dB, delay: 96, latency: 8
19:51:52 Title: Nobody Plays More '80s!
19:51:52 Artist: 94.7 The Drive
19:51:52 SIG Service: type=audio number=1 name=HD1
19:51:52   Audio component: id=0 port=0000 type=7 mime=4DC66C5A
19:51:52 SIG Service: type=audio number=2 name=Pride Radio
19:51:52 Audio bit rate: 91.3 kbps
19:51:52 Audio packet CRC mismatches: 4
19:51:52 Audio decoding error
19:51:53 Lost synchronization
"""


def test_parse_log():
    state = empty_state()
    for line in LOG.splitlines(True)[:-1]:
        parse_line(state, line)
    assert state['synced'] and state['mode'] == 3, state
    assert state['station'] == 'WIAD' and state['slogan'] == 'HD1', state
    assert state['types'] == {0: 'Adult Hits', 1: 'None'}, state['types']
    assert state['audio'] == {0}, state['audio']
    assert state['names'] == {0: 'HD1', 1: 'Pride Radio'}, state['names']
    assert state['title'] == "Nobody Plays More '80s!", state['title']
    assert state['artist'] == '94.7 The Drive'
    assert abs(state['ber'] - 0.10368) < 1e-9 and state['mer'] == (-2.7, -1.4)
    assert state['kbps'] == 91.3
    assert state['crc_errors'] == 4 and state['decode_errors'] == 1, state
    assert state['damaged'][0] == 4 / 32
    parse_line(state, LOG.splitlines()[-1])
    assert not state['synced']
    print("    log: OK")


def test_audio_buffer():
    hd = HdRadio(path='/bin/true')
    rate = hdradio.AUDIO_RATE

    def put(seconds):
        frames = np.zeros((int(seconds * rate), 2), np.float32)
        with hd._lock:
            hd._audio.append(frames)
            hd._audio_frames += len(frames)

    put(hdradio.PREFILL_S / 2)
    assert hd.take_audio(1000) is None, 'played before the prefill'
    put(hdradio.PREFILL_S)
    got = hd.take_audio(1000)
    assert got is not None and got.shape == (1000, 2), got
    assert hd.audio_live()
    # Run dry: silence until it has refilled.
    while hd.take_audio(4096) is not None:
        pass
    put(0.05)
    assert hd.take_audio(100) is None, 'played again before refilling'
    # The reader keeps no more than MAX_BUFFER_S (checked where it appends).
    print("    buffer: OK")


def write_fake(folder):
    stats = os.path.join(folder, 'stats.json')
    path = os.path.join(folder, 'nrsc5')
    with open(path, 'w') as fh:
        fh.write(FAKE.format(python=sys.executable, stats=stats))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path, stats


def tones_heard(rx, seconds, folder, name):
    """(1 kHz, 2.5 kHz, 3 kHz, 3.5 kHz) levels in the audio tap's left
    channel over ``seconds`` (the analog is 1 kHz left, 2.5 kHz right)."""
    settle = time.monotonic() + 0.4           # what the buffers still hold
    while time.monotonic() < settle:
        pump(rx)
        time.sleep(0.1)
    wav = WavWriter(os.path.join(folder, name + '.wav'))
    rx.tap.set_writer(wav)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump(rx)
        time.sleep(0.1)
    rx.tap.set_writer(None)
    wav.close()
    import wave
    with wave.open(wav.path) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), '<i2').reshape(-1, 2) / 32767.0
    left = pcm[:, 0]
    return tuple(signals.tone_level(left, 48000, f) for f in (1000, 2500, 3000, 3500))


def pump(rx):
    """What the window's timer does: run the decoder, pick what is heard."""
    hd = rx.hd
    hd.poll()
    s = hd.status()
    if not s['enabled']:
        heard = 'analog'
    elif s['playing']:
        heard = 'digital'
    else:
        heard = 'analog' if s['program'] == 0 else 'none'
    rx.set_hd_audio(heard)


def wait_playing(rx, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(rx)
        if rx.hd.status()['playing']:
            return True
        time.sleep(0.1)
    return False


def test_through_the_chain():
    folder = tempfile.mkdtemp(prefix='fmrx-hd-')
    tb = None
    try:
        fake, stats = write_fake(folder)
        path = signals.write_station(os.path.join(folder, 'station'), seconds=16.0)
        radio = IQFile(path, repeat=False, throttle=True)
        tb = Engine(want_audio=False)
        tb.hd = HdRadio(path=fake)
        tb.use_radio(radio)
        tb.start_receive(98.7e6, radio.rate, region='RBDS', stereo=True, volume=0.5)
        rx = tb.rx
        assert rx.hd is tb.hd, 'the chain should take HD Radio at 2.5 MS/s'

        analog = tones_heard(rx, 1.5, folder, 'analog')
        print(f"    analog:  1k {analog[0]:.3f}  3k {analog[2]:.4f}")
        assert analog[0] > 0.05 and analog[2] < 0.01, analog

        tb.hd.set_enabled(True)
        assert wait_playing(rx), tb.hd.status()
        s = tb.hd.status()
        assert s['station'] == 'FAKE' and s['names'][1] == 'Fake Jazz', s
        assert s['title'] == 'Song 1', s
        hd1 = tones_heard(rx, 1.5, folder, 'hd1')
        print(f"    HD1:     1k {hd1[0]:.4f}  3k {hd1[2]:.3f}")
        assert hd1[2] > 0.05 and hd1[0] < 0.01, hd1

        with open(stats) as fh:
            fed = json.load(fh)
        rate = fed['fed'] / max(fed['seconds'], 1e-3)
        print(f"    fed {rate / 1e3:.1f} kS/s at rms {fed['rms']:.3f}")
        assert abs(rate / hdradio.IQ_RATE - 1) < 0.2, rate
        assert abs(fed['rms'] / hdradio.IQ_RMS - 1) < 0.3, fed['rms']

        tb.hd.set_program(1)
        assert wait_playing(rx), tb.hd.status()
        assert tb.hd.status()['title'] == 'Song 2'
        hd2 = tones_heard(rx, 1.5, folder, 'hd2')
        print(f"    HD2:     3k {hd2[2]:.4f}  3.5k {hd2[3]:.3f}")
        assert hd2[3] > 0.05 and hd2[2] < 0.01, hd2

        # A retune goes back to HD1, after it settles.
        tb.tune(98.7e6 + 100e3)
        assert tb.hd.program == 0 and not tb.hd.running
        tb.tune(98.7e6)

        tb.hd.set_enabled(False)
        back = tones_heard(rx, 1.0, folder, 'back')
        print(f"    off:     1k {back[0]:.3f}  3.5k {back[3]:.4f}")
        assert back[0] > 0.05 and back[3] < 0.01, back

        tb.hd.set_enabled(True)
        assert wait_playing(rx)
        proc = tb.hd._proc
        tb.close()
        assert proc.poll() is not None, 'the decoder outlived the engine'
        print("    chain: OK")
    finally:
        if tb is not None:
            tb.close()                  # a running flowgraph at exit aborts
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    test_parse_log()
    test_audio_buffer()
    test_through_the_chain()
    print("HD Radio: all checks passed")
