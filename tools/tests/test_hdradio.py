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
- play the digital audio in place of the analog once it has run clean
  for a while, switch program by starting the decoder again, and go back
  to the analog when switched off;
- take nrsc5's all-zero frames as lost audio: HD1 plays the analog while
  half of them are lost, and the share lost is reported;
- stop the decoder when the engine closes.

Run:  python tools/tests/test_hdradio.py     (about 30 s)
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
import os
loss = float(os.environ.get("FAKE_LOSS", "0"))   # share of frames sent as zeros
fed, sq, phase, t0, owed, count = 0, 0.0, 0, time.monotonic(), 0.0, 0
while True:
    data = sys.stdin.buffer.read(65536)
    if not data:
        break
    iq = np.frombuffer(data[:len(data) - len(data) % 8], dtype=np.complex64)
    fed += len(iq)
    sq += float(np.sum(np.abs(iq) ** 2))
    owed += len(iq) * 44100 / 744187.5
    while owed >= 2048:                   # nrsc5 writes whole frames
        owed -= 2048
        t = (phase + np.arange(2048)) / 44100.0
        phase += 2048
        pcm = (0.3 * np.sin(2 * np.pi * tone * t) * 32767).astype('<i2')
        count += 1
        if loss and (count % 10) < loss * 10:
            pcm[:] = 0                    # a lost packet: silence
        out.write(np.repeat(pcm, 2).tobytes())
    out.flush()
    with open("{stats}.new", "w") as fh:          # whole, or not at all
        json.dump({{"fed": fed, "seconds": time.monotonic() - t0,
                   "rms": (sq / max(fed, 1)) ** 0.5}}, fh)
    os.replace("{stats}.new", "{stats}")
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
19:51:52   Data component: id=1 port=1000 service_data_type=265 type=3 mime=BE4B7536
19:51:52   Data component: id=2 port=1001 service_data_type=265 type=3 mime=D9C72536
19:51:52 SIG Service: type=audio number=2 name=Pride Radio
19:51:52   Data component: id=1 port=1002 service_data_type=265 type=3 mime=BE4B7536
19:51:52 SIG Service: type=data number=3 name=Traffic
19:51:52   Data component: id=1 port=2000 service_data_type=265 type=3 mime=D9C72536
19:51:52 Audio bit rate: 91.3 kbps
19:51:52 Audio packet CRC mismatches: 4
19:51:52 Audio decoding error
19:51:52 Album: Hysteria
19:51:52 Genre: Rock
19:51:52 Message: Nobody plays more 80s
19:51:52 LOT file: port=1001 lot=7 name=logo.png size=3441 mime=4F328CA0 expiry=2036-09-28T00:10:00Z
19:51:52 LOT file: port=1000 lot=21055 name=SD0019336054_1535586.jpg size=3441 mime=1E653E9C expiry=2036-09-28T00:10:00Z
19:51:52 XHDR: 1 BE4B7536 21055
19:51:52 Alert: Category=[Weather] SAME=[24031] Tornado warning until 5 PM
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
    assert state['offset_hz'] == 43.0
    assert (state['album'], state['genre']) == ('Hysteria', 'Rock'), state
    assert state['message'] == 'Nobody plays more 80s'
    # Each audio program's picture ports; a data service's are not a program's.
    assert state['ports'] == {0: {'art': 0x1000, 'logo': 0x1001}, 1: {'art': 0x1002}}, \
        state['ports']
    assert state['lots'][(0x1000, 21055)] == '21055_SD0019336054_1535586.jpg'
    assert state['latest'][0x1001] == '7_logo.png' and state['art_lot'] == 21055
    assert state['alert'] == ('Tornado warning until 5 PM',
                              'Category=[Weather] SAME=[24031]'), state['alert']
    parse_line(state, "19:51:53 Alert ended")
    assert state['alert'] is None
    parse_line(state, "19:51:53 XHDR: 1 BE4B7536 -1")      # no art for this song
    assert state['art_lot'] is None
    parse_line(state, LOG.splitlines()[-1])
    assert not state['synced']
    print("    log: OK")


def test_pictures():
    """The album art the song points at and the station's latest logo,
    for the program playing, from the files nrsc5 saved."""
    folder = tempfile.mkdtemp(prefix='fmrx-hd-pics-')
    try:
        hd = HdRadio(path='/bin/true')
        hd._files = folder
        for name in ('7_logo.png', '21055_SD0019336054_1535586.jpg'):
            open(os.path.join(folder, name), 'wb').close()
        for line in LOG.splitlines():
            parse_line(hd.state, line)
        s = hd.status()
        assert s['art_path'] == os.path.join(folder, '21055_SD0019336054_1535586.jpg'), s
        assert s['logo_path'] == os.path.join(folder, '7_logo.png'), s
        # HD2 has an art port but no art yet, and falls back to HD1's logo
        # port only when it has no ports of its own: none here.
        hd.program = 1
        art, logo = hd.pictures()
        assert art is None and logo is None, (art, logo)
        # A file not saved (yet) is no picture.
        hd.program = 0
        os.remove(os.path.join(folder, '7_logo.png'))
        assert hd.pictures()[1] is None
        hd.close()
        assert hd._files is None and not os.path.exists(folder), 'the folder outlived close'
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("    pictures: OK")


def test_audio_buffer():
    hd = HdRadio(path='/bin/true')
    rate = hdradio.AUDIO_RATE

    def put(seconds, lost=False):
        frames = int(seconds * rate) // 2048 + 1
        pcm = np.zeros(frames * 2048 * 2, '<i2')
        if not lost:
            pcm[::7] = 100
        hd.put_audio(pcm.tobytes())

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
    # All-zero frames are lost ones: counted in what was played.
    put(hdradio.PREFILL_S)
    put(0.2, lost=True)
    while hd.take_audio(4096) is not None:
        pass
    share = hd.lost_share()
    assert 0.1 < share < 0.4, share          # 5 of the 27 frames played
    print(f"    buffer: OK (lost share {share:.2f})")


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
    rx.set_hd_audio(hd.status()['heard'])


def wait_playing(rx, timeout=10.0):
    """Until the digital is heard (HD1: once it has run clean a while)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(rx)
        if rx.hd_heard == 'digital':
            return True
        time.sleep(0.1)
    return False


def test_through_the_chain():
    folder = tempfile.mkdtemp(prefix='fmrx-hd-')
    tb = None
    try:
        fake, stats = write_fake(folder)
        path = signals.write_station(os.path.join(folder, 'station'), seconds=32.0)
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

        # The decoder runs, but nothing is chosen: still the analog.
        tb.hd.set_enabled(True)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not tb.hd.status()['playing']:
            pump(rx)
            time.sleep(0.1)
        s = tb.hd.status()
        assert s['playing'] and s['heard'] == 'analog' and not s['chosen'], s
        tb.hd.choose(0)                            # an HD1 click
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

        tb.hd.choose(1)                            # an HD2 click
        assert wait_playing(rx), tb.hd.status()
        assert tb.hd.status()['title'] == 'Song 2'
        hd2 = tones_heard(rx, 1.5, folder, 'hd2')
        print(f"    HD2:     3k {hd2[2]:.4f}  3.5k {hd2[3]:.3f}")
        assert hd2[3] > 0.05 and hd2[2] < 0.01, hd2

        # HD2 clicked again: the analog, with the decoder left running.
        tb.hd.choose(None)
        off = tones_heard(rx, 1.0, folder, 'hd2-off')
        print(f"    HD2 off: 1k {off[0]:.3f}  3.5k {off[3]:.4f}")
        assert off[0] > 0.05 and off[3] < 0.01 and tb.hd.running, off
        tb.hd.choose(1)
        assert wait_playing(rx)

        # A retune goes back to the analog, and HD1 once it settles.
        tb.tune(98.7e6 + 100e3)
        assert tb.hd.program == 0 and not tb.hd.chosen and not tb.hd.running
        tb.tune(98.7e6)

        tb.hd.set_enabled(False)
        back = tones_heard(rx, 1.0, folder, 'back')
        print(f"    off:     1k {back[0]:.3f}  3.5k {back[3]:.4f}")
        assert back[0] > 0.05 and back[3] < 0.01, back

        # Half the frames lost: HD1 stays on the analog, and says why.
        os.environ['FAKE_LOSS'] = '0.5'
        try:
            tb.hd.set_enabled(True)
            tb.hd.choose(0)
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                pump(rx)
                time.sleep(0.1)
            s = tb.hd.status()
            weak = tones_heard(rx, 1.0, folder, 'weak')
            print(f"    lost:    {100 * s['lost_share']:.0f}% lost, heard {s['heard']}, "
                  f"1k {weak[0]:.3f}  3k {weak[2]:.4f}")
            assert s['playing'] and s['heard'] == 'analog', s
            assert 0.3 < s['lost_share'] < 0.7, s['lost_share']
            assert weak[0] > 0.05 and weak[2] < 0.01, weak
        finally:
            del os.environ['FAKE_LOSS']
        proc = tb.hd._proc
        tb.close()
        assert proc.poll() is not None, 'the decoder outlived the engine'
        assert tb.hd.enabled and not tb.hd.chosen, 'closing the engine switched HD off'
        print("    chain: OK")
    finally:
        if tb is not None:
            tb.close()                  # a running flowgraph at exit aborts
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    test_parse_log()
    test_pictures()
    test_audio_buffer()
    test_through_the_chain()
    print("HD Radio: all checks passed")
