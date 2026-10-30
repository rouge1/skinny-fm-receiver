"""What rides on an FM carrier: subcarriers read, and stereo only with L-R (roadmap Round 31, phase 3).

Every signal is synthetic and seeded, built here with numpy (the broadcast multiplex with
``signals.multiplex``). What the checks pin:

- a 92 kHz subcarrier in an FM signal's multiplex, under a 19 kHz pilot and 1 kHz audio,
  is read for what it carries: FM (by a tone and by speech-like noise), AM, USB and LSB
  with their carrier (by speech-like noise and by a tone), and DSB with the carrier
  suppressed (by noise and by a tone, two equal lines as stereo's L-R makes); USB whose
  carrier holds nearly all its power (98 %, as the bench's subcarrier did) is still USB;
  the pilot carries nothing named;
- ``broadcast``: a station with a pilot is stereo when its L-R carries something (left
  1 kHz, right 2.5 kHz, at 35 and 15 dB SNR) and not when left and right are the same
  (a mono programme sent with a pilot, as the bench transmitter does), with or without an
  SCA at 67 kHz beside it; with no pilot there is no ``broadcast`` section.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure_carriers.py
(about half a minute)
"""

import os
import sys

import numpy as np
from scipy import signal as sps

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fm_receiver import iqmeasure  # noqa: E402
from tests.test_iq_measure_classes import expect, noisy, report, RESULTS  # noqa: E402
import signals  # noqa: E402

RATE = 1e6
SUB_HZ = 92e3
MEMO = {}


def _speech(t, seed=7):
    """Speech-like noise: 300 Hz to 3 kHz, about a third of full scale."""
    rng = np.random.default_rng(seed)
    x = sps.lfilter(sps.firwin(801, [300, 3000], pass_zero=False, fs=RATE), 1, rng.standard_normal(t.size))
    return x / (3 * np.std(x))


def sub_iq(kind, seconds=1.0, seed=3):
    """FM at 75 kHz deviation: a 9 % pilot, 1 kHz audio and a 92 kHz subcarrier of ``kind``."""
    t = np.arange(int(seconds * RATE)) / RATE
    w = 2 * np.pi * SUB_HZ * t
    sp = _speech(t)
    hil = sps.hilbert(sp)
    sub = {
        'fm_tone': 0.1 * np.cos(w + 5 * np.sin(2 * np.pi * 1e3 * t)),
        'fm_speech': 0.1 * np.cos(w + 2 * np.pi * np.cumsum(4e3 * sp) / RATE),
        'am': 0.1 * (1 + 0.8 * sp) * np.cos(w),
        'usb': 0.1 * (np.cos(w) + np.real(hil * np.exp(1j * w))),
        'lsb': 0.1 * (np.cos(w) + np.real(np.conj(hil) * np.exp(1j * w))),
        'usb_tone': 0.1 * (np.cos(w) + 0.5 * np.cos(w + 2 * np.pi * 1e3 * t)),
        'dsb': 0.3 * sp * np.cos(w),
        'dsb_tone': 0.1 * np.cos(2 * np.pi * 1e3 * t) * np.cos(w),
        'usb_strong': 0.1 * (np.cos(w) + 0.15 * np.real(hil * np.exp(1j * w))),
    }[kind]
    mpx = 0.09 * np.sin(2 * np.pi * 19e3 * t) + 0.4 * np.sin(2 * np.pi * 1e3 * t) + sub
    return noisy(np.exp(2j * np.pi * 75e3 * np.cumsum(mpx) / RATE), 35.0, seed), RATE


def station_iq(left_hz, right_hz, pilot=0.09, snr_db=35.0, sca=False, seconds=1.0):
    left, right = signals.tones(seconds, left_hz, right_hz)
    mpx = signals.multiplex(seconds, left, right, pilot_level=pilot)
    if sca:
        t = np.arange(len(mpx)) / signals.MPX_RATE
        mpx = mpx + 0.1 * np.cos(2 * np.pi * 67e3 * t + 5 * np.sin(2 * np.pi * 1e3 * t))
    return signals.fm_iq(mpx, RATE, 0.0, snr_db=snr_db), RATE


MAKERS = {
    **{f'sub_{k}': (lambda k=k: sub_iq(k)) for k in ('fm_tone', 'fm_speech', 'am', 'usb', 'lsb', 'usb_tone', 'dsb',
                                                            'dsb_tone', 'usb_strong')},
    'stereo': lambda: station_iq(1e3, 2.5e3),
    'stereo_15db': lambda: station_iq(1e3, 2.5e3, snr_db=15.0),
    'mono_pilot': lambda: station_iq(1e3, 1e3),
    'mono_pilot_15db': lambda: station_iq(1e3, 1e3, snr_db=15.0),
    'stereo_sca': lambda: station_iq(1e3, 2.5e3, sca=True),
    'mono_sca': lambda: station_iq(1e3, 1e3, sca=True),
    'mono_no_pilot': lambda: station_iq(1e3, 1e3, pilot=0.0),
}


def card(name):
    if name not in MEMO:
        x, rate = MAKERS[name]()
        MEMO[name] = iqmeasure.measure(x, rate, center_hz=100e6)
    return MEMO[name]


def at(name, hz, near=1500.0):
    """The subcarrier listed nearest ``hz`` (its strongest line or its centre), or None."""
    subs = [s for s in card(name)['frequency']['subcarriers']
            if min(abs(s['peak_hz'] - hz), abs(s['freq_hz'] - hz)) <= near]
    return subs[0] if subs else None


def check_subcarriers():
    for kind, want in (('fm_tone', 'fm'), ('fm_speech', 'fm'), ('am', 'am'), ('usb', 'usb'),
                       ('lsb', 'lsb'), ('usb_tone', 'usb'), ('dsb', 'dsb'), ('dsb_tone', 'dsb'),
                       ('usb_strong', 'usb')):
        name = f'sub_{kind}'
        expect(f"92 kHz subcarrier, {kind}: read as {want}",
               lambda n=name, w=want: (at(n, SUB_HZ, 4500.0) is not None and at(n, SUB_HZ, 4500.0)['carries'] == w,
                                       f"{card(n)['frequency']['subcarriers']}"))
    expect("the pilot carries nothing named",
           lambda: (at('sub_am', 19e3) is not None and at('sub_am', 19e3)['carries'] is None,
                    f"{at('sub_am', 19e3)}"))
    expect("SSB: the carrier, not the power centre, is its strongest line (within 50 Hz)",
           lambda: (abs(at('sub_usb', SUB_HZ, 4500.0)['peak_hz'] - SUB_HZ) <= 50,
                    f"{at('sub_usb', SUB_HZ, 4500.0)}"))


def check_broadcast():
    b = lambda n: card(n)['broadcast']
    for name in ('stereo', 'stereo_15db', 'stereo_sca'):
        expect(f"{name}: a pilot and L-R: stereo",
               lambda n=name: (b(n) is not None and b(n)['stereo'] is True, f"{b(n)}"))
    for name in ('mono_pilot', 'mono_pilot_15db', 'mono_sca'):
        expect(f"{name}: a pilot but L-R empty: not stereo, L-R within 3 dB of its noise",
               lambda n=name: (b(n) is not None and b(n)['stereo'] is False and abs(b(n)['l_minus_r_db']) < 3,
                               f"{b(n)}"))
    expect("the stations' RDS is seen", lambda: (b('stereo')['rds'] and b('mono_pilot')['rds'], f"{b('stereo')}"))
    expect("no pilot: no broadcast section",
           lambda: (b('mono_no_pilot') is None, f"{b('mono_no_pilot')}"))
    expect("a mono programme's evidence says it is sent with a pilot",
           lambda: ('mono programme' in card('mono_pilot')['evidence'][0], card('mono_pilot')['evidence'][0]))


GROUPS = [check_subcarriers, check_broadcast]


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
