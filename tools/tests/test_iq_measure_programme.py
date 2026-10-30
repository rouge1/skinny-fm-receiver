"""Things that look like part of the programme (roadmap Round 31, phase 4).

Every signal is synthetic and seeded, built here with numpy. What the checks pin:

- ``audio`` on an ``fm`` or ``am`` card: a tone keyed on and off 12 dB under speech in FM
  audio (a square wave, and Morse at 20 wpm) is listed and called keyed; a steady tone 20 dB
  under speech, and a note that swells and fades as in music, are listed and not keyed;
  speech alone lists no line; a programme that is one tone lists that tone and is not
  looked at for a loop;
- DTMF digits in AM speech, early in the span and late, read in order; none in speech alone;
- a looped clip's repeat is found (3 s), and one with under two repeats in the span warns;
  speech that does not repeat says so;
- two bursts of OOK bits (300 ms in 1 s) in a span warn that it is too short to see the repeat;
- ``beside``: next to a strong signal off the tuned centre, the narrow images of its lines
  (an IQ imbalance) are called images, and a faint beacon elsewhere not an image; with a
  band that leaves its mirror out, the beacon's image is not judged;
- DTMF: a 15 ms two-tone blip is not a digit; in quiet a digit's time and length are right
  to 10 ms.

Run:  PYTHONPATH=tools python tools/tests/test_iq_measure_programme.py   (about half a minute)
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fm_receiver import iqmeasure  # noqa: E402
from tests.test_iq_measure_classes import expect, noisy, report, RESULTS  # noqa: E402

RATE = 250e3
MEMO = {}


def speech(seconds, seed=2, rate=RATE):
    """Speech-like: noise 300 Hz to 3 kHz in syllables (about 3 a second, with pauses),
    peaking near full scale."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / rate)
    X[(f < 300) | (f > 3000)] = 0
    x = np.fft.irfft(X, n)
    t = np.arange(n) / rate
    words = rng.random(int(seconds * 4) + 1)[(t * 4).astype(int)] > 0.3
    x = x * (np.sin(2 * np.pi * 3.3 * t + rng.uniform(0, 6)) > -0.2) * words
    return x / (3 * np.std(x))


def tone(seconds, hz, under_db, ref, keyed_hz=None, rate=RATE):
    """A tone ``under_db`` under the power of ``ref``, keyed on and off at ``keyed_hz``."""
    t = np.arange(int(seconds * rate)) / rate
    x = np.sqrt(2 * np.mean(ref ** 2)) * 10 ** (-under_db / 20) * np.cos(2 * np.pi * hz * t)
    return x * (np.sin(2 * np.pi * keyed_hz * t) > 0) if keyed_hz else x


def morse_key(seconds, wpm, seed=9, rate=RATE):
    """On and off as Morse keys it: dots and dashes (1 and 3 units) in letters of 1 to 4,
    a unit of 1.2 / wpm seconds, a unit between elements and three between letters."""
    unit, rng, key = 1.2 / wpm, np.random.default_rng(seed), []
    while len(key) * unit < seconds:
        for _ in range(rng.integers(1, 5)):
            key += [1] * (1 if rng.random() < 0.5 else 3) + [0]
        key += [0, 0]
    t = np.arange(int(seconds * rate)) / rate
    return np.asarray(key, float)[(t / unit).astype(int)]


def note(seconds, hz, ref, seed=10, rate=RATE):
    """A note played now and then: 0.2 to 0.8 s long, struck and fading (a 0.15 s decay),
    each at its own level within 12 dB, 6 dB under ``ref`` at its loudest."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    env = np.zeros(t.size)
    at = 0.3
    while at < seconds:
        length = rng.uniform(0.2, 0.8)
        k = (t >= at) & (t < at + length)
        env[k] = 10 ** (-rng.uniform(0, 12) / 20) * np.exp(-(t[k] - at) / 0.15)
        at += length + rng.uniform(0.2, 0.8)
    return np.sqrt(2 * np.mean(ref ** 2)) * 10 ** (-6 / 20) * env * np.cos(2 * np.pi * hz * t)


def dtmf(seconds, digits, at_s, rate=RATE, level=0.5):
    """``digits`` from ``at_s``: 100 ms each, 80 ms apart, both tones at ``level``."""
    keys = {k: (lo, hi) for lo, row in zip(iqmeasure.DTMF_LOW, iqmeasure.DTMF_KEYS)
            for hi, k in zip(iqmeasure.DTMF_HIGH, row)}
    x = np.zeros(int(seconds * rate))
    for i, d in enumerate(digits):
        s = int((at_s + 0.18 * i) * rate)
        t = np.arange(int(0.1 * rate)) / rate
        lo, hi = keys[d]
        x[s:s + t.size] += level * (np.cos(2 * np.pi * lo * t) + np.cos(2 * np.pi * hi * t))
    return x


def blip(seconds, at_s, rate=RATE):
    """One 15 ms burst of DTMF 5's two tones."""
    x = np.zeros(int(seconds * rate))
    t = np.arange(int(0.015 * rate)) / rate
    s = int(at_s * rate)
    x[s:s + t.size] = 0.5 * (np.cos(2 * np.pi * 770 * t) + np.cos(2 * np.pi * 1336 * t))
    return x


def fm(audio, dev=5e3, snr_db=40.0, seed=3):
    return noisy(np.exp(2j * np.pi * dev * np.cumsum(audio) / RATE), snr_db, seed)


def am(audio, depth=0.8, snr_db=40.0, seed=3):
    return noisy((1 + depth * audio / max(1e-9, np.max(np.abs(audio)))).astype(np.complex64), snr_db, seed)


def looped(clip_s, seconds, seed=5):
    clip = speech(clip_s, seed)
    return np.tile(clip, int(np.ceil(seconds / clip_s)))[:int(seconds * RATE)]


def bursts_iq():
    """Two 300 ms bursts of OOK bits (1 kBd, seeded) in 1 s. (Mostly-off bursts, a tenth on,
    are not yet found at all: the night's upgrade M2, not this phase.)"""
    t = np.arange(int(1.0 * RATE)) / RATE
    bits = np.random.default_rng(8).random(1001) > 0.5
    on = (((t > 0.1) & (t < 0.4)) | ((t > 0.6) & (t < 0.9))) & bits[(t * 1e3).astype(int)]
    return noisy((on * np.exp(2j * np.pi * 10e3 * t)).astype(np.complex64), 30.0, 4)


def beside_iq():
    """10 MS/s: a strong signal at +1.5 MHz (a 1 MHz hump with two narrow carriers in it), its
    image 35 dB down (an IQ imbalance), and a faint keyed beacon at -2.6 MHz."""
    rate, n = 10e6, 1 << 21
    rng = np.random.default_rng(6)
    t = np.arange(n) / rate
    X = np.fft.fft(rng.standard_normal(n) + 1j * rng.standard_normal(n))
    f = np.fft.fftfreq(n, 1 / rate)
    X[np.abs(f - 1.5e6) > 0.5e6] = 0
    hump = np.fft.ifft(X)
    hump *= 0.3 / np.std(hump)
    sig = hump + np.exp(2j * np.pi * 1.2e6 * t) + 0.5 * np.exp(2j * np.pi * 1.75e6 * t)
    sig = sig + 10 ** (-35 / 20) * np.conj(sig)
    beacon = 3e-3 * (np.sin(2 * np.pi * 20 * t) > 0) * np.exp(2j * np.pi * -2.6e6 * t)
    floor = 1e-4 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (sig + beacon + floor).astype(np.complex64), rate


SECONDS = 8.0
SP = speech(SECONDS)
MAKERS = {
    'fm_speech': lambda: (fm(SP), RATE),
    'fm_keyed': lambda: (fm(SP + tone(SECONDS, 2220, 12, SP, keyed_hz=2.5)), RATE),
    'fm_morse': lambda: (fm(SP + tone(SECONDS, 2220, 12, SP) * morse_key(SECONDS, 20)), RATE),
    'fm_steady': lambda: (fm(SP + tone(SECONDS, 1500, 20, SP)), RATE),
    'fm_note': lambda: (fm(SP + note(SECONDS, 707, SP)), RATE),
    'fm_tone': lambda: (fm(tone(SECONDS, 1000, 0, SP)), RATE),
    'am_speech': lambda: (am(SP), RATE),
    'am_dtmf_early': lambda: (am(SP + dtmf(SECONDS, '159#', 0.5)), RATE),
    'am_dtmf_late': lambda: (am(SP + dtmf(SECONDS, '0*7', 6.8)), RATE),
    'fm_blip': lambda: (fm(SP * 0.3 + blip(SECONDS, 3.0), dev=15e3), RATE),
    'fm_quiet_digit': lambda: (fm(SP * (np.arange(SP.size) / RATE < 3.0) + dtmf(SECONDS, '5', 4.0)), RATE),
    'beside_band': lambda: (beside_iq()[0], beside_iq()[1], (100e6 - 3e6, 100e6 + 0.5e6)),
    'fm_loop': lambda: (fm(looped(3.0, 12.0)), RATE),
    'fm_loop_short': lambda: (fm(looped(6.0, 10.0)), RATE),
    'ook_two': lambda: (bursts_iq(), RATE),
    'beside': beside_iq,
}


def card(name):
    if name not in MEMO:
        x, rate, *band = MAKERS[name]()
        MEMO[name] = iqmeasure.measure(x, rate, center_hz=100e6, band=band[0] if band else None)
    return MEMO[name]


def lines(name):
    return (card(name)['audio'] or {}).get('lines') or []


def near(name, hz, within=10.0):
    got = [x for x in lines(name) if abs(x['freq_hz'] - hz) <= within]
    return got[0] if got else None


def check_lines():
    expect("FM speech with a keyed 2220 Hz tone 12 dB under it: the line is listed, keyed",
           lambda: (near('fm_keyed', 2220) is not None and near('fm_keyed', 2220)['keyed'] is True,
                    f"{lines('fm_keyed')}"))
    expect("the keyed tone is listed once", lambda: (len(lines('fm_keyed')) == 1, f"{lines('fm_keyed')}"))
    expect("Morse at 20 wpm on a 2220 Hz tone 12 dB under speech: listed, keyed",
           lambda: (near('fm_morse', 2220) is not None and near('fm_morse', 2220)['keyed'] is True,
                    f"{lines('fm_morse')}"))
    expect("a note that swells and fades (music) is listed and not keyed",
           lambda: (near('fm_note', 707) is not None and near('fm_note', 707)['keyed'] is False,
                    f"{lines('fm_note')}"))
    expect("FM speech with a steady 1500 Hz tone 20 dB under it: listed, not keyed",
           lambda: (near('fm_steady', 1500) is not None and near('fm_steady', 1500)['keyed'] is False,
                    f"{lines('fm_steady')}"))
    for name in ('fm_speech', 'am_speech'):
        expect(f"{name}: no line", lambda n=name: (lines(n) == [], f"{lines(n)}"))
    expect("a programme that is one tone: that tone, steady, and the audio does not vary",
           lambda: (near('fm_tone', 1000) is not None and near('fm_tone', 1000)['keyed'] is False
                    and card('fm_tone')['audio']['varies'] is False and card('fm_tone')['audio']['loop_s'] is None,
                    f"{card('fm_tone')['audio']}"))
    expect("the evidence names a keyed line",
           lambda: (any('2220 Hz' in e and 'keyed' in e for e in card('fm_keyed')['evidence']),
                    f"{card('fm_keyed')['evidence']}"))


def check_dtmf():
    digits = lambda n: ''.join(d['digit'] for d in card(n)['audio']['dtmf'])  # noqa: E731
    expect("AM speech with DTMF 1 5 9 # at 0.5 s: read in order", lambda: (digits('am_dtmf_early') == '159#',
                                                                          f"{card('am_dtmf_early')['audio']['dtmf']}"))
    expect("DTMF 0 * 7 at 6.8 s, near the span's end: read, the first's time to 15 ms (speech may "
           "hide part of a digit)",
           lambda: (digits('am_dtmf_late') == '0*7'
                    and abs(card('am_dtmf_late')['audio']['dtmf'][0]['t_s'] - 6.8) < 0.015
                    and all(0.04 <= d['duration_s'] <= 0.12 for d in card('am_dtmf_late')['audio']['dtmf']),
                    f"{card('am_dtmf_late')['audio']['dtmf']}"))
    expect("a 15 ms two-tone blip is not a digit",
           lambda: (card('fm_blip')['audio']['dtmf'] == [], f"{card('fm_blip')['audio']['dtmf']}"))
    expect("in quiet, a 100 ms digit's time and length to 10 ms",
           lambda: ([(d['digit'], round(d['t_s'], 2), round(d['duration_s'], 2))
                     for d in card('fm_quiet_digit')['audio']['dtmf']] == [('5', 4.0, 0.1)],
                    f"{card('fm_quiet_digit')['audio']['dtmf']}"))
    for name in ('am_speech', 'fm_speech', 'fm_keyed'):
        expect(f"{name}: no DTMF", lambda n=name: (card(n)['audio']['dtmf'] == [], f"{card(n)['audio']['dtmf']}"))


def check_repeat():
    expect("a 3 s clip looped for 12 s: it repeats every 3 s",
           lambda: (card('fm_loop')['audio']['loop_s'] is not None
                    and abs(card('fm_loop')['audio']['loop_s'] - 3.0) < 0.1 and card('fm_loop')['warnings'] == [],
                    f"{card('fm_loop')['audio']}, {card('fm_loop')['warnings']}"))
    expect("a 6 s clip in 10 s: found, and a warning that the span holds under two repeats",
           lambda: (card('fm_loop_short')['audio']['loop_s'] is not None
                    and abs(card('fm_loop_short')['audio']['loop_s'] - 6.0) < 0.1
                    and any('under two repeats' in w for w in card('fm_loop_short')['warnings']),
                    f"{card('fm_loop_short')['audio']}, {card('fm_loop_short')['warnings']}"))
    expect("speech that does not repeat: no loop, and the evidence says so",
           lambda: (card('fm_speech')['audio']['loop_s'] is None
                    and any('does not repeat' in e for e in card('fm_speech')['evidence']),
                    f"{card('fm_speech')['evidence']}"))
    expect("two OOK bursts: a warning to read a longer span",
           lambda: (card('ook_two')['class'] == 'ook' and any('Only 2 bursts' in w for w in card('ook_two')['warnings']),
                    f"{card('ook_two')['class']}, {card('ook_two')['warnings']}"))
    expect("a card that is not am or fm has no audio",
           lambda: (card('ook_two')['audio'] is None and card('beside')['audio'] is None,
                    f"{card('ook_two')['audio']}"))


def check_beside():
    b = lambda: card('beside')['beside']  # noqa: E731

    def at(hz, within=5e3):
        got = [x for x in b() if abs(x['freq_hz'] - (100e6 + hz)) <= within]
        return got[0] if got else None

    for hz in (-1.2e6, -1.75e6):
        expect(f"the image of the carrier at {-hz / 1e6:g} MHz is listed beside it and called an image",
               lambda h=hz: (at(h) is not None and at(h)['image'] is True, f"{at(h)}; {b()}"))
    expect("the faint beacon at -2.6 MHz is not an image",
           lambda: (at(-2.6e6) is not None and at(-2.6e6)['image'] is False, f"{at(-2.6e6)}; {b()}"))
    expect("the evidence says the beacon is not an image",
           lambda: (any('97.3999' in e and 'not an image' in e for e in card('beside')['evidence']),
                    f"{card('beside')['evidence']}"))
    expect("a band that leaves the beacon's mirror out: its image is not judged (null)",
           lambda: (any(abs(x['freq_hz'] - 97.4e6) <= 5e3 and x['image'] is None for x in card('beside_band')['beside']),
                    f"{card('beside_band')['beside']}"))
    expect("an FM programme on its own has nothing beside it", lambda: (card('fm_speech')['beside'] == [],
                                                                       f"{card('fm_speech')['beside']}"))


GROUPS = [check_lines, check_dtmf, check_repeat, check_beside]


def main():
    for group in GROUPS:
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
