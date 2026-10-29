"""The card's atsc, psk, pulses and tv classes and FM subcarriers (CONTRACTS-measure.md, 2026-10-29).

Every signal is synthetic and seeded, built here with numpy. What the checks pin:

- 'psk' with its order (BPSK, QPSK, 8PSK, filtered with a root-raised cosine), the symbol
  rate within 1 % and the carrier offset within 50 Hz; an unfiltered QPSK, whose envelope is
  constant as FM's is, is still 'psk';
- 'pulses': a regular 1 us train at 40,000 a second (rate within 0.1 %, width within 10 %,
  not position-modulated), and the same train with each pulse moved by a 1 kHz tone
  (position-modulated); a continuous PWM train is 'pulses', width-modulated;
- a sensor's PWM burst in silence (EV1527 and LaCrosse TX141 timings, as the bench's ISM
  transmitter sends them) stays 'ook', not 'pulses';
- 'atsc': an 8VSB-like signal (lte_negatives) with its pilot at the lower edge, and the
  same signal with its spectrum inverted (pilot at the upper edge), the channel centre
  within 50 kHz;
- 'tv': 525-line AM vision (negative modulation), 625-line AM, and 525-line FM video with a
  6.0 MHz sound subcarrier, which is listed;
- FM subcarriers: a 67 kHz FM subcarrier and a stereo pilot at 19 kHz are listed, at their
  frequency within 500 Hz; a plain FM tone lists none;
- what must not move: FM by a tone at the first zero of J0 (no carrier line) stays 'fm', AM
  at full depth stays 'am', and the new sections are None on a class that is not theirs;
- the card stays JSON-safe with floats to six significant digits.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure_classes.py
(about a minute)
"""

import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqmeasure  # noqa: E402
from tests import lte_negatives  # noqa: E402

CENTRE = 400e6
RESULTS = []
MEMO = {}


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok:
        line += f"  ({detail})"
    print(line)


def expect(name, test):
    """Run ``test()``, which returns ``(ok, detail)``. An exception is a failure."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


def noisy(sig, snr_db, seed):
    rng = np.random.default_rng(seed)
    p = np.mean(np.abs(sig) ** 2) / 10 ** (snr_db / 10)
    n = np.sqrt(p / 2) * (rng.standard_normal(len(sig)) + 1j * rng.standard_normal(len(sig)))
    return (sig + n).astype(np.complex64)


# ---- PSK ----------------------------------------------------------------------

def rrc(beta, sps, span=8):
    """Root-raised-cosine taps, ``span`` symbols each side, unit energy."""
    t = np.arange(-span * sps, span * sps + 1) / sps
    h = np.empty(len(t))
    for i, x in enumerate(t):
        if abs(x) < 1e-9:
            h[i] = 1 - beta + 4 * beta / math.pi
        elif abs(abs(x) - 1 / (4 * beta)) < 1e-9:
            h[i] = beta / math.sqrt(2) * ((1 + 2 / math.pi) * math.sin(math.pi / (4 * beta))
                                         + (1 - 2 / math.pi) * math.cos(math.pi / (4 * beta)))
        else:
            h[i] = (math.sin(math.pi * x * (1 - beta)) + 4 * beta * x * math.cos(math.pi * x * (1 + beta))) \
                / (math.pi * x * (1 - (4 * beta * x) ** 2))
    return h / np.sqrt(np.sum(h ** 2))


def psk_iq(order, baud=100e3, rate=1e6, seconds=1.0, offset=30e3, filtered=True, seed=1):
    rng = np.random.default_rng(seed)
    sps = int(rate // baud)
    count = int(seconds * baud)
    sym = np.exp(2j * np.pi * rng.integers(0, order, count) / order + 1j * math.pi / order)
    if filtered:
        up = np.zeros(count * sps, complex)
        up[::sps] = sym
        x = np.convolve(up, rrc(0.35, sps), 'same')
    else:
        x = np.repeat(sym, sps)
    x = x * np.exp(2j * np.pi * offset * np.arange(len(x)) / rate)
    return noisy(x, 25.0, seed + 100), rate


# ---- pulses -------------------------------------------------------------------

def pulse_iq(swing_s=0.0, rate=20e6, seconds=0.1, prf=40e3, width_s=1e-6, seed=2):
    """A 1 us pulse every 25 us; with ``swing_s`` each start is moved by a 1 kHz tone
    of that peak (pulse-position modulation)."""
    n = int(seconds * rate)
    env = np.zeros(n)
    w = int(round(width_s * rate))
    period = rate / prf
    for k in range(int(seconds * prf) - 1):
        move = swing_s * rate * math.sin(2 * math.pi * 1e3 * k / prf)
        s = int(round(k * period + period / 2 + move))
        env[s:s + w] = 1.0
    x = env * np.exp(2j * np.pi * 1e6 * np.arange(n) / rate)
    return noisy(x, 25.0, seed), rate


def pwm_iq(period_us, short_us, long_us, bits=25, rows=4, row_gap_us=10000, lead_s=0.3,
           rate=1e6, seconds=1.0, seed=8):
    """OOK pulse-width bits: a pulse every ``period_us``, ``short_us`` or ``long_us`` long at
    random, ``rows`` rows of ``bits`` with a gap between, after ``lead_s`` of silence. With
    ``rows`` None the bits run through the whole span (a continuous PWM train)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    env = np.zeros(n)
    us = rate / 1e6
    if rows is None:
        starts = np.arange(0, n - period_us * us, period_us * us)
    else:
        row = np.arange(bits) * period_us * us
        starts = np.concatenate([lead_s * rate + r * (bits * period_us + row_gap_us) * us + row
                                 for r in range(rows)])
    for s in starts.astype(int):
        w = int((short_us if rng.integers(0, 2) else long_us) * us)
        env[s:s + w] = 1.0
    x = env * np.exp(2j * np.pi * 50e3 * np.arange(n) / rate)
    return noisy(x, 25.0, seed), rate


# ---- TV -----------------------------------------------------------------------

def composite(rate, seconds, line_hz, lines_per_frame, sound_hz=None):
    """A composite video level, 0 at the sync tip and 1 at peak white: per line 4.7 us of
    sync, blanking (0.3) to 10.9 us, then eight grey bars falling from white; every
    field's first 9 lines are broad pulses. With ``sound_hz``, an FM sound subcarrier."""
    t = np.arange(int(seconds * rate)) / rate
    line = np.floor(t * line_hz)
    pos = t * line_hz - line                         # 0..1 through the line
    us = pos / line_hz * 1e6
    bars = 1.0 - np.floor((us - 10.9) / ((1e6 / line_hz - 10.9) / 8)) * 0.09
    v = np.where(us < 4.7, 0.0, np.where(us < 10.9, 0.3, bars))
    field_line = line % (lines_per_frame / 2)
    v = np.where((field_line < 9) & (pos % 0.5 < 0.43), 0.0, v)
    if sound_hz:
        v = v + 0.05 * np.cos(2 * np.pi * sound_hz * t + 0.5 * np.sin(2 * np.pi * 1e3 * t))
    return v


def tv_am_iq(system='525', rate=8e6, seconds=0.1, seed=3):
    line_hz, lines = (15734.264, 525) if system == '525' else (15625.0, 625)
    v = composite(rate, seconds, line_hz, lines)
    amp = 1.0 - 0.875 * v                            # negative: sync tip strongest, white at 12.5 %
    x = amp * np.exp(2j * np.pi * -1e6 * np.arange(len(v)) / rate)
    return noisy(x, 35.0, seed), rate


def tv_fm_iq(rate=20e6, seconds=0.05, seed=4):
    v = composite(rate, seconds, 15734.264, 525, sound_hz=6.0e6)
    freq = (v - 0.5) * 8e6                           # 8 MHz peak to peak over the picture
    x = np.exp(2j * np.pi * np.cumsum(freq) / rate)
    return noisy(x, 35.0, seed), rate


# ---- FM -----------------------------------------------------------------------

def fm_subcarrier_iq(rate=1e6, seconds=1.0, seed=5):
    """FM: a 19 kHz pilot (7 kHz deviation) and a 67 kHz subcarrier, itself FM by a
    1 kHz tone (5 kHz deviation), at 40 kHz deviation."""
    t = np.arange(int(seconds * rate)) / rate
    sub = np.cos(2 * np.pi * 67e3 * t + 5.0 * np.sin(2 * np.pi * 1e3 * t))
    freq = 7e3 * np.cos(2 * np.pi * 19e3 * t) + 40e3 * sub
    return noisy(np.exp(2j * np.pi * np.cumsum(freq) / rate), 30.0, seed), rate


def fm_tone_iq(beta=75.0, rate=1e6, seconds=1.0, seed=6):
    t = np.arange(int(seconds * rate)) / rate
    return noisy(np.exp(1j * beta * np.sin(2 * np.pi * 1e3 * t)), 30.0, seed), rate


def am_full_iq(rate=1e6, seconds=1.0, seed=7):
    t = np.arange(int(seconds * rate)) / rate
    x = (1 + np.cos(2 * np.pi * 1e3 * t)) * np.exp(2j * np.pi * 20e3 * t)
    return noisy(x, 30.0, seed), rate


MAKERS = {
    'bpsk': lambda: psk_iq(2, baud=50e3),
    'qpsk': lambda: psk_iq(4),
    '8psk': lambda: psk_iq(8),
    'qpsk_rect': lambda: psk_iq(4, baud=10e3, filtered=False),
    'pulses': lambda: pulse_iq(),
    'ppm': lambda: pulse_iq(swing_s=2e-6),
    'pwm_ev1527': lambda: pwm_iq(1868, 464, 1404),
    'pwm_lacrosse': lambda: pwm_iq(625, 208, 417, bits=40, rows=12, row_gap_us=1000),
    'pwm_train': lambda: pwm_iq(1000, 100, 200, rows=None),
    'atsc': lambda: lte_negatives.synth_atsc_like(),
    'atsc_inverted': lambda: (np.conj(lte_negatives.synth_atsc_like()[0]), 20e6),
    'tv_525': lambda: tv_am_iq('525'),
    'tv_625': lambda: tv_am_iq('625'),
    'tv_fm': lambda: tv_fm_iq(),
    'fm_sub': lambda: fm_subcarrier_iq(),
    'fm_tone': lambda: fm_tone_iq(),
    'fm_j0': lambda: fm_tone_iq(beta=2.404826),
    'am_full': lambda: am_full_iq(),
}


def card(name):
    if name not in MEMO:
        x, rate = MAKERS[name]()
        MEMO[name] = iqmeasure.measure(x, rate, center_hz=CENTRE)
    return MEMO[name]


def check_psk():
    for name, order, baud in (('bpsk', 2, 50e3), ('qpsk', 4, 100e3), ('8psk', 8, 100e3)):
        expect(f"{name}: class psk, order {order}",
               lambda n=name, o=order: (card(n)['class'] == 'psk' and card(n)['psk']['order'] == o,
                                        f"class {card(n)['class']}, psk {card(n)['psk']}"))
        expect(f"{name}: symbol rate within 1 %",
               lambda n=name, b=baud: (card(n)['symbol_rate_baud'] is not None
                                       and abs(card(n)['symbol_rate_baud'] / b - 1) < 0.01,
                                       f"{card(n)['symbol_rate_baud']}"))
        expect(f"{name}: carrier offset within 50 Hz",
               lambda n=name: (abs(card(n)['psk']['carrier_offset_hz'] - 30e3) < 50,
                               f"{card(n)['psk']['carrier_offset_hz']}"))
    expect("unfiltered QPSK (constant envelope): psk, order 4, not fm",
           lambda: (card('qpsk_rect')['class'] == 'psk' and card('qpsk_rect')['psk']['order'] == 4,
                    f"class {card('qpsk_rect')['class']}, psk {card('qpsk_rect')['psk']}"))


def check_pulses():
    p = lambda n: card(n)['pulses']
    expect("pulse train: class pulses",
           lambda: (card('pulses')['class'] == 'pulses', f"class {card('pulses')['class']}"))
    expect("pulse train: 40,000 a second within 0.1 %",
           lambda: (abs(p('pulses')['rate_hz'] / 40e3 - 1) < 1e-3, f"{p('pulses')['rate_hz']}"))
    expect("pulse train: 1 us wide within 10 %",
           lambda: (abs(p('pulses')['width_s'] / 1e-6 - 1) < 0.1, f"{p('pulses')['width_s']}"))
    expect("pulse train: not position-modulated",
           lambda: (p('pulses')['position_modulated'] is False, f"{p('pulses')}"))
    expect("PPM: class pulses, position-modulated, swing near 4 us peak to peak",
           lambda: (card('ppm')['class'] == 'pulses' and p('ppm')['position_modulated']
                    and 3e-6 < p('ppm')['position_swing_s'] < 5e-6, f"{card('ppm')['class']}, {p('ppm')}"))
    expect("pulse train and PPM: not width-modulated",
           lambda: (not p('pulses')['width_modulated'] and not p('ppm')['width_modulated'],
                    f"{p('pulses')}, {p('ppm')}"))
    for name in ('pwm_ev1527', 'pwm_lacrosse'):
        expect(f"{name}: a sensor's PWM burst in silence stays ook, not pulses",
               lambda n=name: (card(n)['class'] == 'ook' and card(n)['pulses'] is None,
                               f"class {card(n)['class']}, pulses {card(n)['pulses']}"))
    expect("continuous PWM: class pulses, width-modulated, not position-modulated",
           lambda: (card('pwm_train')['class'] == 'pulses' and p('pwm_train')['width_modulated']
                    and not p('pwm_train')['position_modulated'],
                    f"class {card('pwm_train')['class']}, pulses {p('pwm_train')}"))


def check_atsc():
    # synth_atsc_like puts the pilot 0.31 MHz above 0 Hz: the channel centre is 2.69 MHz
    # above the pilot, at CENTRE + 3 MHz (inverted: the mirror image, CENTRE - 3 MHz).
    for name, inverted, chan in (('atsc', False, CENTRE + 3e6), ('atsc_inverted', True, CENTRE - 3e6)):
        expect(f"{name}: class atsc, inverted {inverted}, channel centre within 50 kHz",
               lambda n=name, i=inverted, c=chan: (
                   card(n)['class'] == 'atsc' and card(n)['atsc']['inverted'] is i
                   and abs(card(n)['atsc']['channel_centre_hz'] - c) < 50e3,
                   f"class {card(n)['class']}, atsc {card(n)['atsc']}, occupied {card(n)['occupied']}"))


def check_tv():
    t = lambda n: card(n)['tv']
    for name, system, mod in (('tv_525', '525-line', 'am'), ('tv_625', '625-line', 'am'),
                              ('tv_fm', '525-line', 'fm')):
        expect(f"{name}: class tv, {system}, {mod.upper()}",
               lambda n=name, s=system, m=mod: (card(n)['class'] == 'tv' and t(n)['system'] == s
                                                and t(n)['modulation'] == m,
                                                f"class {card(n)['class']}, tv {t(n)}, "
                                                f"evidence {card(n)['evidence']}"))
    expect("tv_525: line rate within 0.5 %",
           lambda: (abs(t('tv_525')['line_rate_hz'] / 15734.264 - 1) < 0.005, f"{t('tv_525')}"))
    expect("tv_fm: the 6.0 MHz sound subcarrier listed",
           lambda: (any(abs(s['freq_hz'] - 6.0e6) < 20e3 for s in card('tv_fm')['frequency']['subcarriers']),
                    f"{card('tv_fm')['frequency']['subcarriers']}"))


def check_fm():
    subs = lambda n: [s['freq_hz'] for s in card(n)['frequency']['subcarriers']]
    expect("FM with a subcarrier: class fm",
           lambda: (card('fm_sub')['class'] == 'fm', f"class {card('fm_sub')['class']}"))
    for hz in (19e3, 67e3):
        expect(f"FM with a subcarrier: {hz / 1e3:.0f} kHz listed within 500 Hz",
               lambda h=hz: (any(abs(f - h) < 500 for f in subs('fm_sub')), f"{subs('fm_sub')}"))
    expect("FM tone: no subcarriers", lambda: (subs('fm_tone') == [], f"{subs('fm_tone')}"))


def check_unmoved():
    expect("FM at the first zero of J0 (no carrier line): still fm",
           lambda: (card('fm_j0')['class'] == 'fm', f"class {card('fm_j0')['class']}, psk {card('fm_j0')['psk']}"))
    expect("AM at full depth: still am, not pulses",
           lambda: (card('am_full')['class'] == 'am', f"class {card('am_full')['class']}"))
    expect("the new sections are None on another class",
           lambda: (all(card(n)[k] is None for n in ('fm_tone', 'am_full')
                        for k in ('atsc', 'tv', 'pulses', 'psk')),
                    f"{[(n, card(n)['tv'], card(n)['pulses'], card(n)['psk']) for n in ('fm_tone', 'am_full')]}"))


def check_shape():
    def floats(v):
        if isinstance(v, bool):
            return []
        if isinstance(v, float):
            return [v]
        if isinstance(v, dict):
            return [f for x in v.values() for f in floats(x)]
        if isinstance(v, list):
            return [f for x in v for f in floats(x)]
        return []

    for name in MAKERS:
        expect(f"{name}: JSON-safe, six significant digits",
               lambda n=name: (json.dumps(card(n), allow_nan=False) is not None
                               and all(x == 0 or float(f'{x:.6g}') == x for x in floats(card(n))),
                               "not JSON-safe or not rounded"))


GROUPS = [check_psk, check_pulses, check_atsc, check_tv, check_fm, check_unmoved, check_shape]


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
