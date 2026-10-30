"""Keying of more than two levels in the card (roadmap Round 31, phase 1).

Every signal is synthetic and seeded, built here with numpy. What the checks pin:

- 8ASK (carrier on, 40 kBd) and 4ASK (carrier on, 10 kBd, rectangular and root-raised
  cosine) are 'ask' with ``envelope.order`` 8 and 4, each level within 3 % of the top one,
  the symbol rate within 1 %; their 16-sample blocks call them 'am';
- 4FSK (4.8 kBd, rectangular) is 'fsk' with ``frequency.order`` 4, its levels within
  150 Hz, and lists no subcarriers; Gaussian 4FSK at 19 kBd (BT 0.5), above the blocks'
  reach, is 'fsk' of order 4 at its rate; so is Gaussian 4FSK at BT 0.3 (19 kBd at 9 and
  15 kHz, 4.8 kBd at 3.6 kHz), whose shaping smears every level until each neighbour's
  share is taken off, its levels within 3 % of the deviation;
- a two-level ASK keeps order 2, as the blocks found it;
- what must not move: FM and AM by two tones (whose samples at their difference repeat a
  few values) and FM and AM by noise stay 'fm' and 'am', with no order.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure_levels.py
(about half a minute)
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqmeasure  # noqa: E402
from tests.test_iq_measure_classes import expect, noisy, report, rrc, RESULTS  # noqa: E402

CENTRE = 400e6
MEMO = {}


def ask_iq(levels, baud, beta=None, rate=2e6, seconds=0.5, offset=30e3, seed=1):
    """Amplitude keying between ``levels`` at random, rectangular or (``beta``) RRC."""
    rng = np.random.default_rng(seed)
    sps = int(rate // baud)
    count = int(seconds * baud)
    a = np.asarray(levels, float)[rng.integers(0, len(levels), count)]
    if beta:
        up = np.zeros(count * sps)
        up[::sps] = a
        x = np.convolve(up, rrc(beta, sps), 'same') * math.sqrt(sps)
    else:
        x = np.repeat(a, sps)
    x = x * np.exp(2j * np.pi * offset * np.arange(len(x)) / rate)
    return noisy(x, 25.0, seed + 100), rate


def fsk4_iq(baud, deviation, bt=None, rate=2e6, seconds=0.5, offset=-20e3, seed=2):
    """4FSK at -dev, -dev/3, dev/3, dev; with ``bt`` the frequency is Gaussian-filtered."""
    rng = np.random.default_rng(seed)
    sps = int(rate // baud)
    count = int(seconds * baud)
    f = np.repeat(np.array([-3, -1, 1, 3.0])[rng.integers(0, 4, count)], sps) * deviation / 3
    if bt:
        t = np.arange(-2 * sps, 2 * sps + 1) / sps
        g = np.exp(-t ** 2 / (2 * (math.sqrt(math.log(2)) / (2 * math.pi * bt)) ** 2))
        f = np.convolve(f, g / g.sum(), 'same')
    return noisy(np.exp(2j * np.pi * np.cumsum(f + offset) / rate), 25.0, seed + 100), rate


def tones_iq(kind, rate=1e6, seconds=0.5, seed=3):
    """FM or AM by two tones, 1 kHz and 1.7 kHz, of unequal size."""
    t = np.arange(int(seconds * rate)) / rate
    if kind == 'fm':
        x = np.exp(1j * (2 * np.pi * 20e3 * t + 3 * np.sin(2 * np.pi * 1e3 * t)
                         + 1.5 * np.sin(2 * np.pi * 1.7e3 * t)))
    else:
        x = (1 + 0.5 * np.cos(2 * np.pi * 1e3 * t) + 0.25 * np.cos(2 * np.pi * 1.7e3 * t)) \
            * np.exp(2j * np.pi * 20e3 * t)
    return noisy(x, 30.0, seed), rate


def noise_mod_iq(kind, rate=1e6, seconds=0.5, seed=4):
    """FM or AM by low-passed noise, a stand-in for speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    m = np.convolve(rng.standard_normal(t.size), np.ones(300) / 300, 'same')
    m /= np.abs(m).max()
    if kind == 'fm':
        x = np.exp(1j * (2 * np.pi * 20e3 * t + 2 * np.pi * np.cumsum(5e3 * m) / rate))
    else:
        x = (1 + 0.8 * m) * np.exp(2j * np.pi * 20e3 * t)
    return noisy(x, 30.0, seed), rate


MAKERS = {
    '8ask': lambda: ask_iq(np.linspace(0.3, 1.0, 8), 40e3),
    '4ask': lambda: ask_iq(np.linspace(0.25, 1.0, 4), 10e3),
    '4ask_rrc': lambda: ask_iq(np.linspace(0.25, 1.0, 4), 10e3, beta=0.35),
    '2ask': lambda: ask_iq([0.3, 1.0], 10e3),
    '4fsk': lambda: fsk4_iq(4.8e3, 3.6e3),
    '4gfsk_fast': lambda: fsk4_iq(19e3, 9e3, bt=0.5),
    'gfsk03_19k': lambda: fsk4_iq(19e3, 9e3, bt=0.3),
    'gfsk03_19k_wide': lambda: fsk4_iq(19e3, 15e3, bt=0.3),
    'gfsk03_4k8': lambda: fsk4_iq(4.8e3, 3.6e3, bt=0.3),
    'fm_tones': lambda: tones_iq('fm'),
    'am_tones': lambda: tones_iq('am'),
    'fm_noise': lambda: noise_mod_iq('fm'),
    'am_noise': lambda: noise_mod_iq('am'),
}


def card(name):
    if name not in MEMO:
        x, rate = MAKERS[name]()
        MEMO[name] = iqmeasure.measure(x, rate, center_hz=CENTRE)
    return MEMO[name]


def check_ask():
    for name, order, top, baud in (('8ask', 8, 1.0, 40e3), ('4ask', 4, 1.0, 10e3), ('4ask_rrc', 4, None, 10e3)):
        c = lambda n=name: card(n)
        expect(f"{name}: class ask, envelope order {order}",
               lambda c=c, o=order: (c()['class'] == 'ask' and c()['envelope']['order'] == o,
                                     f"class {c()['class']}, order {c()['envelope']['order']}"))
        expect(f"{name}: symbol rate within 1 %",
               lambda c=c, b=baud: (c()['symbol_rate_baud'] is not None
                                    and abs(c()['symbol_rate_baud'] / b - 1) < 0.01, f"{c()['symbol_rate_baud']}"))
        # The levels evenly spaced from a quarter (or 0.3) of the top one: within 3 % of it.
        lv = lambda c=c: np.asarray(c()['envelope']['levels'])
        expect(f"{name}: levels evenly spaced within 3 % of the top",
               lambda lv=lv, o=order: (len(lv()) == o and np.max(np.abs(np.diff(lv(), 2))) < 0.03 * lv()[-1],
                                       f"{lv().tolist()}"))
    expect("2ask: order 2, levels 0.3 and 1",
           lambda: (card('2ask')['class'] == 'ask' and card('2ask')['envelope']['order'] == 2
                    and np.allclose(card('2ask')['envelope']['levels'], [0.3, 1.0], atol=0.03),
                    f"class {card('2ask')['class']}, {card('2ask')['envelope']}"))


def check_fsk():
    f = lambda n: card(n)['frequency']
    expect("4fsk: class fsk, frequency order 4",
           lambda: (card('4fsk')['class'] == 'fsk' and f('4fsk')['order'] == 4,
                    f"class {card('4fsk')['class']}, order {f('4fsk')['order']}"))
    expect("4fsk: levels at -3.6, -1.2, 1.2, 3.6 kHz about -20 kHz within 150 Hz",
           lambda: (len(f('4fsk')['levels_hz']) == 4
                    and np.allclose(f('4fsk')['levels_hz'], [-23.6e3, -21.2e3, -18.8e3, -16.4e3], atol=150),
                    f"{f('4fsk')['levels_hz']}"))
    expect("4fsk: symbol rate within 1 %",
           lambda: (abs(card('4fsk')['symbol_rate_baud'] / (2e6 / int(2e6 // 4.8e3)) - 1) < 0.01,
                    f"{card('4fsk')['symbol_rate_baud']}"))
    expect("4fsk: no subcarriers", lambda: (f('4fsk')['subcarriers'] == [], f"{f('4fsk')['subcarriers']}"))
    expect("Gaussian 4FSK at 19 kBd (BT 0.5): fsk, order 4, rate within 1 %",
           lambda: (card('4gfsk_fast')['class'] == 'fsk' and f('4gfsk_fast')['order'] == 4
                    and abs(card('4gfsk_fast')['symbol_rate_baud'] / (2e6 / int(2e6 // 19e3)) - 1) < 0.01,
                    f"class {card('4gfsk_fast')['class']}, order {f('4gfsk_fast')['order']}, "
                    f"{card('4gfsk_fast')['symbol_rate_baud']}"))


def check_gaussian():
    for name, baud, dev in (('gfsk03_19k', 19e3, 9e3), ('gfsk03_19k_wide', 19e3, 15e3), ('gfsk03_4k8', 4.8e3, 3.6e3)):
        true = 2e6 / int(2e6 // baud)
        lv = lambda n=name: np.asarray(card(n)['frequency']['levels_hz']) + 20e3
        expect(f"{name} (BT 0.3): fsk, order 4, rate within 1 %",
               lambda n=name, t=true: (card(n)['class'] == 'fsk' and card(n)['frequency']['order'] == 4
                                       and abs(card(n)['symbol_rate_baud'] / t - 1) < 0.01,
                                       f"class {card(n)['class']}, order {card(n)['frequency']['order']}, "
                                       f"{card(n)['symbol_rate_baud']}"))
        expect(f"{name} (BT 0.3): levels within 3 % of the deviation",
               lambda lv=lv, d=dev: (len(lv()) == 4 and np.allclose(lv(), [-d, -d / 3, d / 3, d], atol=0.03 * d),
                                     f"{lv().tolist()}"))


def check_unmoved():
    for name, cls in (('fm_tones', 'fm'), ('am_tones', 'am'), ('fm_noise', 'fm'), ('am_noise', 'am')):
        expect(f"{name}: still {cls}, no order",
               lambda n=name, k=cls: (card(n)['class'] == k and card(n)['envelope']['order'] is None
                                      and card(n)['frequency']['order'] is None,
                                      f"class {card(n)['class']}, orders {card(n)['envelope']['order']}, "
                                      f"{card(n)['frequency']['order']}"))


GROUPS = [check_ask, check_fsk, check_gaussian, check_unmoved]


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
