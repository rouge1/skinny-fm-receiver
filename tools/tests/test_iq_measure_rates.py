"""Fast symbols, the pulse shape and FM video's deviation in the card (roadmap Round 31, phase 2).

Every signal is synthetic and seeded, built here with numpy. What the checks pin:

- 2FSK and 2ASK at 20, 50 and 200 kBd (past the 16-sample blocks' quarter rate) are 'fsk'
  and 'ask' at their symbol rate within 1 % (at 20 kBd the blocks read 12.5 kBd; at 50 kBd
  they called them 'fm' and 'am');
- ``pulse``: RRC QPSK at roll-off 0.2 (100 kBd) and 0.5 (20 kBd), RRC 4ASK with the carrier
  on (0.35) and 8PSK (0.35) give the roll-off within 0.03, RRC BPSK within 0.05; unfiltered
  QPSK and 8ASK are 'rectangular', with no roll-off;
- 4ASK with the carrier suppressed (bipolar levels, RRC 0.5) is 'ask', not BPSK, with two
  amplitudes a third of each other within 10 %;
- FM video's deviation in the ``tv`` section, 4 MHz (8 MHz peak to peak) within 3 %; AM
  vision has none. Video with pre-emphasis (a first-order lift of the high frequencies,
  as F.405's network does) is flagged, video without it not;
- signals built as the bench generators build them (each symbol held, then a root raised
  cosine of 11 symbols' taps; ASK levels k/(M-1) - 0.5, plus the carrier when on): QPSK at
  0.35 and 4ASK, carrier off, at 0.5 fit as a held raised cosine within 0.03; BPSK at
  125 kBd, 8 samples a symbol, is 'psk' at its rate (it was 'ask' at 248 Bd); 2ASK with the
  carrier off is BPSK; 4ASK with the carrier off is 'ask', amplitudes 1 : 3 within 10 %.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure_rates.py
(about half a minute)
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqmeasure  # noqa: E402
from tests.test_iq_measure_classes import (expect, noisy, psk_iq, report, rrc, tv_am_iq,  # noqa: E402
                                           tv_fm_iq, RESULTS)
from tests.test_iq_measure_levels import ask_iq  # noqa: E402

CENTRE = 400e6
MEMO = {}


def fsk2_iq(baud, rate=2e6, seconds=0.5, seed=3):
    """2FSK at +-0.75 of the symbol rate, 15 kHz off the centre."""
    rng = np.random.default_rng(seed)
    sps = int(rate // baud)
    f = np.repeat(np.array([-1.0, 1.0])[rng.integers(0, 2, int(seconds * baud))], sps) * 0.75 * baud
    return noisy(np.exp(2j * np.pi * np.cumsum(f + 15e3) / rate), 25.0, seed), rate


def qpsk_rrc_iq(beta, baud=100e3, rate=1e6, seconds=0.5, offset=30e3, seed=1):
    rng = np.random.default_rng(seed)
    sps = int(rate // baud)
    count = int(seconds * baud)
    up = np.zeros(count * sps, complex)
    up[::sps] = np.exp(2j * np.pi * rng.integers(0, 4, count) / 4 + 1j * math.pi / 4)
    x = np.convolve(up, rrc(beta, sps), 'same') * np.exp(2j * np.pi * offset * np.arange(count * sps) / rate)
    return noisy(x, 25.0, seed + 100), rate


def held_iq(levels, baud, beta, rate=1e6, seconds=0.5, offset=30e3, seed=11, carrier=0.0, complex_=None):
    """Symbols held for a symbol each, then a root raised cosine of 11 symbols' taps, as the
    bench generators make them. ``levels`` are real (ASK: k/(M-1) - 0.5, plus ``carrier``) or,
    with ``complex_``, that many PSK phases."""
    rng = np.random.default_rng(seed)
    sps = int(round(rate / baud))
    count = int(seconds * baud)
    if complex_:
        sym = np.exp(2j * np.pi * rng.integers(0, complex_, count) / complex_ + 1j * math.pi / complex_)
    else:
        sym = np.asarray(levels, float)[rng.integers(0, len(levels), count)]
    taps = rrc(beta, sps, span=5)
    x = np.convolve(np.repeat(sym, sps), taps / taps.sum(), 'same') + carrier
    x = 0.475 * x * np.exp(2j * np.pi * offset * np.arange(len(x)) / rate)
    return noisy(x, 30.0, seed + 100), rate


def ask_levels(m):
    return [k / (m - 1) - 0.5 for k in range(m)]


def pre_emphasised_tv_iq(rate=20e6, seconds=0.05, seed=4):
    """FM video whose picture is lifted above 1 MHz (first order, 10 dB at the top), at the
    same peak to peak as tv_fm_iq's at low frequency."""
    from tests.test_iq_measure_classes import composite
    v = composite(rate, seconds, 15734.264, 525) - 0.5
    a = math.exp(-2 * math.pi * 1e6 / rate)
    lifted = v + 2.2 * (v - np.concatenate([[v[0]], sps_lfilter(v, a)[:-1]]))
    x = np.exp(2j * np.pi * np.cumsum(lifted * 8e6) / rate)
    return noisy(x, 35.0, seed), rate


def sps_lfilter(v, a):
    """A one-pole low-pass: y[n] = (1 - a) v[n] + a y[n-1]."""
    from scipy import signal as sps
    return sps.lfilter([1 - a], [1, -a], v)


MAKERS = {
    **{f'2fsk_{int(b / 1e3)}k': (lambda b=b: fsk2_iq(b)) for b in (20e3, 50e3, 200e3)},
    **{f'2ask_{int(b / 1e3)}k': (lambda b=b: ask_iq([0.3, 1.0], b)) for b in (20e3, 50e3, 200e3)},
    'qpsk_rrc20': lambda: qpsk_rrc_iq(0.2),
    'qpsk_rrc50': lambda: qpsk_rrc_iq(0.5, baud=20e3),
    '4ask_rrc35': lambda: ask_iq(np.linspace(0.25, 1.0, 4), 10e3, beta=0.35),
    '8psk': lambda: psk_iq(8),
    'bpsk': lambda: psk_iq(2, baud=50e3),
    'qpsk_rect': lambda: psk_iq(4, baud=10e3, filtered=False),
    '8ask_rect': lambda: ask_iq(np.linspace(0.3, 1.0, 8), 40e3),
    '4ask_off': lambda: ask_iq([-3, -1, 1, 3], 10e3, beta=0.5),
    'tv_fm': lambda: tv_fm_iq(),
    'tv_am': lambda: tv_am_iq('525'),
    'tv_pre': lambda: pre_emphasised_tv_iq(),
    'held_qpsk35': lambda: held_iq(None, 50e3, 0.35, complex_=4),
    'held_bpsk_fast': lambda: held_iq(None, 125e3, 0.35, complex_=2),
    'held_2ask_off': lambda: held_iq(ask_levels(2), 25e3, 0.35),
    'held_4ask_off': lambda: held_iq(ask_levels(4), 25e3, 0.5),
}


def card(name):
    if name not in MEMO:
        x, rate = MAKERS[name]()
        MEMO[name] = iqmeasure.measure(x, rate, center_hz=CENTRE)
    return MEMO[name]


def check_rates():
    for kind in ('fsk', 'ask'):
        for b in (20e3, 50e3, 200e3):
            name = f'2{kind}_{int(b / 1e3)}k'
            true = 2e6 / int(2e6 // b)
            expect(f"{name}: class {kind}, symbol rate within 1 %",
                   lambda n=name, k=kind, t=true: (card(n)['class'] == k and card(n)['symbol_rate_baud'] is not None
                                                   and abs(card(n)['symbol_rate_baud'] / t - 1) < 0.01,
                                                   f"class {card(n)['class']}, {card(n)['symbol_rate_baud']}"))


def check_pulse():
    for name, beta, tol in (('qpsk_rrc20', 0.2, 0.03), ('qpsk_rrc50', 0.5, 0.03), ('4ask_rrc35', 0.35, 0.03),
                            ('8psk', 0.35, 0.03), ('bpsk', 0.35, 0.05)):
        expect(f"{name}: raised cosine, roll-off {beta} within {tol}",
               lambda n=name, b=beta, t=tol: (card(n)['pulse'] is not None and card(n)['pulse']['rolloff'] is not None
                                              and abs(card(n)['pulse']['rolloff'] - b) <= t,
                                              f"{card(n)['pulse']}"))
    for name in ('qpsk_rect', '8ask_rect'):
        expect(f"{name}: rectangular, no roll-off",
               lambda n=name: (card(n)['pulse'] is not None and card(n)['pulse']['shape'] == 'rectangular'
                               and card(n)['pulse']['rolloff'] is None, f"{card(n)['pulse']}"))


def check_carrier_off():
    expect("4ASK, carrier suppressed: class ask, not psk",
           lambda: (card('4ask_off')['class'] == 'ask', f"class {card('4ask_off')['class']}"))
    expect("4ASK, carrier suppressed: two amplitudes, one a third of the other within 10 %",
           lambda: (card('4ask_off')['psk']['amplitude_levels'] is not None
                    and abs(np.divide(*card('4ask_off')['psk']['amplitude_levels']) * 3 - 1) < 0.1,
                    f"{card('4ask_off')['psk']}"))
    expect("RRC BPSK keeps class psk (one amplitude)",
           lambda: (card('bpsk')['class'] == 'psk' and card('bpsk')['psk']['amplitude_levels'] is None,
                    f"class {card('bpsk')['class']}, {card('bpsk')['psk']}"))


def check_tv():
    t = lambda n: card(n)['tv']
    expect("FM video: deviation 4 MHz, 8 MHz peak to peak, within 3 %",
           lambda: (t('tv_fm')['deviation_hz'] is not None and abs(t('tv_fm')['deviation_hz'] / 4e6 - 1) < 0.03
                    and abs(t('tv_fm')['peak_to_peak_hz'] / 8e6 - 1) < 0.03, f"{t('tv_fm')}"))
    expect("AM vision: no deviation",
           lambda: (t('tv_am')['deviation_hz'] is None, f"{t('tv_am')}"))


def check_held():
    p = lambda n: card(n)['pulse']
    for name, beta in (('held_qpsk35', 0.35), ('held_4ask_off', 0.5)):
        expect(f"{name}: a held raised cosine, roll-off {beta} within 0.03",
               lambda n=name, b=beta: (p(n) is not None and p(n)['shape'] == 'held raised cosine'
                                       and abs(p(n)['rolloff'] - b) <= 0.03, f"{p(n)}"))
    expect("held BPSK at 125 kBd, 8 samples a symbol: psk at its rate within 1 %",
           lambda: (card('held_bpsk_fast')['class'] == 'psk'
                    and abs(card('held_bpsk_fast')['symbol_rate_baud'] / 125e3 - 1) < 0.01,
                    f"class {card('held_bpsk_fast')['class']}, {card('held_bpsk_fast')['symbol_rate_baud']}"))
    expect("held 2ASK, carrier off: BPSK, no amplitudes",
           lambda: (card('held_2ask_off')['class'] == 'psk' and card('held_2ask_off')['psk']['amplitude_levels'] is None,
                    f"class {card('held_2ask_off')['class']}, {card('held_2ask_off')['psk']}"))
    expect("held 4ASK, carrier off: ask, amplitudes 1 : 3 within 10 %",
           lambda: (card('held_4ask_off')['class'] == 'ask' and card('held_4ask_off')['psk']['amplitude_levels']
                    and abs(np.divide(*card('held_4ask_off')['psk']['amplitude_levels']) * 3 - 1) < 0.1,
                    f"class {card('held_4ask_off')['class']}, {card('held_4ask_off')['psk']}"))
    expect("FM video with pre-emphasis: flagged; without: not",
           lambda: (card('tv_pre')['tv']['pre_emphasis'] is True and card('tv_fm')['tv']['pre_emphasis'] is False,
                    f"{card('tv_pre')['tv']}, {card('tv_fm')['tv']}"))


GROUPS = [check_rates, check_pulse, check_carrier_off, check_tv, check_held]


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
