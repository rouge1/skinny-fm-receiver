"""The measurement core (phase m1): ``fm_receiver/iqmeasure.py`` against CONTRACTS-measure.md.

Every signal is built here with numpy, seeded, at 1 MS/s (complex64), with the
SNR as the noise power against the signal's mean power. What the checks pin:

- the class of each signal, and the first class rule (noise below 6 dB);
- occupied bandwidth within 15 %: CW under 5 kHz; AM 2 kHz (its carrier and
  the two sidebands at 1 kHz); FM the 99 % width of its Bessel lines (75 kHz
  deviation, 1 kHz tone); OOK and FSK the 99 % width of the waveform itself,
  as random data has no closed form: the narrowest band holding 99 % of the
  power of a rectangular-window periodogram of the noiseless waveform (a
  Hann window moves it by under 1 %, so the window does not matter here);
- tones: the two of a two-tone signal within 2 bins, strongest first, at most
  8; a bin is taken as rate / 1024, so an implementation with finer bins passes;
- symbol rate within 5 %; burst duration within 5 %; repeat interval within 2 %
  and None under three bursts;
- power_dbm = power_dbfs + full_scale_dbm within 0.1 dB;
- the card's form: schema 2, its keys, six significant digits, JSON-safe (the lte and nr
  sections, and their classes, are tested in test_iq_measure_cells.py);
- the readers (a .cfile with and without its .sigmf-meta, .sdvr, .cdif) and the
  refusals (sweep .sdvr, .fft, a missing, short, unknown or junk file);
- write_card: beside a .cfile only, never over a file that is not a card;
- reading and measuring write nothing (a folder snapshot before and after);
- G5: iqmeasure imports none of gnuradio, PyQt5, scipy, library, radios, dsp
  in a clean subprocess, even after a measurement.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_measure.py
(about a minute)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy import special

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import signals  # noqa: E402

try:
    from fm_receiver import iqmeasure  # noqa: E402
    MISSING = None
except ImportError as exc:       # reported as failing checks, not a crash
    iqmeasure = None
    MISSING = exc

RATE = 1e6
CENTRE = 100e6
CLASSES = {'noise', 'lte', 'nr', 'lte+nr', 'carrier', 'ook', 'ask', 'fsk', 'fm', 'am', 'unknown'}
BANNED = ['gnuradio', 'PyQt5', 'scipy', 'library', 'radios', 'dsp']
FOLDER = None
RESULTS = []
MEMO = {}


def api():
    if iqmeasure is None:
        raise MISSING
    return iqmeasure


def memo(key, make):
    """Run ``make()`` once; a failure is kept and raised again for each check."""
    if key not in MEMO:
        try:
            MEMO[key] = (True, make())
        except Exception as exc:
            MEMO[key] = (False, exc)
    ok, value = MEMO[key]
    if not ok:
        raise value
    return value


def fresh(name):
    path = os.path.join(FOLDER, name)
    os.makedirs(path, exist_ok=True)
    return path


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok:
        line += f"  ({detail})"
    print(line)


def expect(name, test):
    """Run ``test()``, which returns ``(ok, detail)``. An exception is a failure,
    and the other checks still run."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


def eq(got, want):
    return got == want, f'got {got!r}, want {want!r}'


def near(got, want, tol):
    if got is None:
        return False, f'got None, want {want:.6g}'
    return abs(got - want) <= tol, f'got {got:.6g}, want {want:.6g} +/- {tol:.3g}'


def rel_near(got, want, frac):
    return near(got, want, frac * abs(want))


def below(got, bound):
    return got is not None and got < bound, f'got {got}, want under {bound:g}'


def refused(call, error):
    try:
        call()
    except error:
        return True, ''
    except Exception as exc:
        return False, f'raised {type(exc).__name__}: {exc}, want {error.__name__}'
    return False, f'accepted, want {error.__name__}'


def tones_found(card, freq, rate=RATE):
    """Whether a tone of the card is within 2 bins (rate / 1024) of ``freq``."""
    found = [t['freq_hz'] for t in card['tones']]
    ok = any(abs(f - freq) <= 2 * rate / 1024 for f in found)
    return ok, f'want a tone near {freq / 1e6:.4f} MHz, tones at {[round(f) for f in found]}'


# ---- synthetic signals -------------------------------------------------------

def _t(seconds, rate=RATE):
    return np.arange(int(round(seconds * rate))) / rate


def _power_noise(rng, n, power):
    return np.sqrt(power / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def _with_noise(sig, snr_db, seed, power=None):
    """``sig`` plus white noise ``snr_db`` below ``power`` (default: its mean power);
    ``snr_db`` None adds nothing."""
    if snr_db is None:
        return sig.astype(np.complex64)
    p = np.mean(np.abs(sig) ** 2) if power is None else power
    rng = np.random.default_rng(seed)
    return (sig + _power_noise(rng, len(sig), p / 10 ** (snr_db / 10))).astype(np.complex64)


def cw_iq(seconds=1.0, offset=50e3, amp=1.0, snr_db=30.0, rate=RATE, seed=1):
    t = _t(seconds, rate)
    return _with_noise(amp * np.exp(2j * np.pi * offset * t), snr_db, seed)


def _run_limited_bits(rng, count, longest=3):
    """Random bits with no run longer than ``longest``, so gaps inside a burst are short."""
    bits = np.zeros(count, dtype=np.uint8)
    for i in range(count):
        bit = int(rng.integers(0, 2))
        if i >= longest and np.all(bits[i - longest:i] == bit):
            bit = 1 - bit
        bits[i] = bit
    return bits


def ook_iq(bursts=12, snr_db=30.0, seed=2, noisy=True, low=0.0):
    """OOK at 4,000 baud on a carrier 20 kHz up: bursts of 40 ms (160 symbols of
    random data) starting every 100 ms from 50 ms, the carrier off between.
    ``low`` is the level of a 0 bit (0 for OOK; above 10 % of the 1 level for ASK)."""
    rng = np.random.default_rng(seed)
    t = _t(1.3)
    keyed = np.zeros(len(t))
    for k in range(bursts):
        bits = _run_limited_bits(rng, 160)
        start = int(round((0.05 + 0.1 * k) * RATE))
        keyed[start:start + 160 * 250] = low + (1 - low) * np.repeat(bits, 250)
    sig = keyed * np.exp(2j * np.pi * 20e3 * t)
    return _with_noise(sig, snr_db if noisy else None, seed + 100, power=1.0)


def fsk_iq(seconds=1.0, snr_db=30.0, seed=3, noisy=True):
    """FSK at 9,600 baud, 5 kHz either side of the centre, continuous phase."""
    rng = np.random.default_rng(seed)
    t = _t(seconds)
    bits = rng.integers(0, 2, int(np.ceil(seconds * 9600)) + 1)
    freq = np.where(bits[np.floor(t * 9600).astype(int)] == 1, 5e3, -5e3)
    sig = np.exp(2j * np.pi * np.cumsum(freq) / RATE)
    return _with_noise(sig, snr_db if noisy else None, seed + 100)


def fm_iq(seconds=1.0, snr_db=30.0, seed=4):
    """FM, 75 kHz deviation by a 1 kHz tone (index 75), the carrier at the centre."""
    t = _t(seconds)
    return _with_noise(np.exp(1j * 75.0 * np.sin(2 * np.pi * 1e3 * t)), snr_db, seed)


def am_iq(seconds=1.0, snr_db=30.0, seed=5):
    """AM, 80 % depth by a 1 kHz tone, the carrier 20 kHz up."""
    t = _t(seconds)
    sig = (1 + 0.8 * np.cos(2 * np.pi * 1e3 * t)) * np.exp(2j * np.pi * 20e3 * t)
    return _with_noise(sig, snr_db, seed)


def tones_iq(offsets, amps, seconds=1.0, snr_db=30.0, seed=6):
    """Tones at ``offsets`` (Hz) with ``amps``, in white noise ``snr_db`` below their power."""
    t = _t(seconds)
    sig = np.zeros(len(t), dtype=np.complex128)
    for f, a in zip(offsets, amps):
        sig += a * np.exp(2j * np.pi * f * t)
    return _with_noise(sig, snr_db, seed)


def two_tone_iq(seconds=1.0, snr_db=30.0, seed=7):
    """Two equal tones, +20 kHz and -60 kHz."""
    return tones_iq([20e3, -60e3], [0.5, 0.5], seconds, snr_db, seed)


def noise_iq(seconds=1.0, seed=8):
    return _power_noise(np.random.default_rng(seed), len(_t(seconds)), 1.0).astype(np.complex64)


MAKERS = {
    'cw': lambda: cw_iq(),
    'cw_0db': lambda: cw_iq(snr_db=0.0, seed=9),
    'ook': lambda: ook_iq(),
    'ook2': lambda: ook_iq(bursts=2),
    'ook3': lambda: ook_iq(bursts=3),
    'ook_noisy': lambda: ook_iq(snr_db=0.0, seed=12),
    'ask': lambda: ook_iq(low=0.5, seed=10),
    'fsk': lambda: fsk_iq(),
    'fm': lambda: fm_iq(),
    'am': lambda: am_iq(),
    'noise': lambda: noise_iq(),
    'two_tone': lambda: two_tone_iq(),
    'tones_levels': lambda: tones_iq([50e3, 150e3, -200e3], [1.0, 0.5, 0.25]),
    'tones_many': lambda: tones_iq(np.arange(-240e3, 241e3, 60e3), [0.1] * 9),
    # Amplitude 0.0095 is about 13.5 dB over the median floor of a 1,024-bin
    # Hann periodogram of this noise (scalloping keeps it under 15 dB).
    'tone_14db': lambda: tones_iq([50e3, -300e3], [1.0, 0.0095]),
}


def iq(name):
    return memo(('iq', name), MAKERS[name])


def card(name):
    """The card of a synthetic signal, measured once at the centre."""
    return memo(('card', name), lambda: api().measure(iq(name), RATE, center_hz=CENTRE))


# ---- the references the bandwidth checks compare with ------------------------

def _percentile_width(freq, power):
    """The 0.5 % to 99.5 % width of a power spectrum (points ``freq``, weights ``power``)."""
    freq, power = np.asarray(freq, float), np.asarray(power, float)
    order = np.argsort(freq)
    cum = np.cumsum(power[order]) / np.sum(power)
    f = freq[order]
    return float(f[np.searchsorted(cum, 0.995)] - f[np.searchsorted(cum, 0.005)])


def _narrowest_width(freq, power, mass=0.99):
    """The width of the narrowest band (contiguous points) holding ``mass`` of the power."""
    order = np.argsort(freq)
    f = np.asarray(freq, float)[order]
    p = np.asarray(power, float)[order]
    c = np.concatenate([[0.0], np.cumsum(p)])
    j = np.searchsorted(c, c[:-1] + mass * c[-1])      # first end index reaching the mass
    i = np.flatnonzero(j <= len(p))
    return float(np.min(f[j[i] - 1] - f[i]))


def waveform_reference(x, rate):
    """The 99 % width of a waveform's own spectrum (rectangular periodogram, no noise)."""
    spec = np.abs(np.fft.fft(x)) ** 2
    return _narrowest_width(np.fft.fftfreq(len(x), 1 / rate), spec)


def fm_reference(beta=75.0):
    """The 99 % width of FM's Bessel lines: power J_n(beta)^2 at n kHz."""
    n = np.arange(-200, 201)
    return _percentile_width(n * 1e3, special.jv(n, beta) ** 2)


def am_reference(depth=0.8):
    """AM by a 1 kHz tone: the carrier, and sidebands of power (depth/2)^2 at +-1 kHz."""
    return _percentile_width([-1e3, 0.0, 1e3], [(depth / 2) ** 2, 1.0, (depth / 2) ** 2])


# ---- the card's form ---------------------------------------------------------

TOP_KEYS = ('schema', 'source', 'level', 'occupied', 'tones', 'bursts', 'envelope',
            'frequency', 'symbol_rate_baud', 'lte', 'nr', 'class', 'evidence')
SECTION_KEYS = {
    'source': ('path', 'kind', 'rate_sps', 'center_hz', 't0_s', 't1_s', 'samples'),
    'level': ('power_dbfs', 'power_dbm', 'noise_floor_dbfs', 'snr_db'),
    'occupied': ('bandwidth_hz', 'bandwidth_20db_hz', 'centre_hz'),
    'bursts': ('count', 'durations_s', 'median_duration_s', 'gaps_s',
               'repeat_interval_s', 'duty'),
    'envelope': ('levels', 'depth', 'keyed'),
    'frequency': ('levels_hz', 'deviation_hz', 'keyed'),
    'lte': ('searched', 'reason'),
    'nr': ('searched', 'reason'),
}


def _floats(value):
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _floats(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _floats(v)


def shape_problems(card_):
    """What is wrong with a card's form, as sentences (an empty list when right)."""
    if not isinstance(card_, dict):
        return ['the card is not a dict']
    bad = [f'missing {k}' for k in TOP_KEYS if k not in card_]
    for sec, keys in SECTION_KEYS.items():
        part = card_.get(sec)
        if not isinstance(part, dict):
            bad.append(f'{sec} is not a dict')
            continue
        bad += [f'missing {sec}.{k}' for k in keys if k not in part]
    if card_.get('schema') != 2:
        bad.append('schema is not 2')
    if card_.get('class') not in CLASSES:
        bad.append(f"class {card_.get('class')!r} is not one of the eleven")
    ev = card_.get('evidence')
    if not (isinstance(ev, list) and ev and all(
            isinstance(e, str) and e.strip() and len(e) <= 200 for e in ev)):
        bad.append('evidence is not a non-empty list of short sentences')
    try:
        json.dumps(card_, allow_nan=False)
    except (TypeError, ValueError) as exc:
        bad.append(f'not JSON-safe: {exc}')
    for x in _floats(card_):
        if not (x == 0 or float(f'{x:.6g}') == x):
            bad.append(f'{x!r} is not rounded to 6 significant digits')
            break
    tones = card_.get('tones')
    if not isinstance(tones, list) or len(tones) > 8:
        bad.append('tones is not a list of at most 8')
    else:
        levels = [t['level_db'] for t in tones]
        if levels != sorted(levels, reverse=True):
            bad.append('tones are not strongest first')
        if any(lv <= 15 for lv in levels):
            bad.append('a tone is not more than 15 dB above the floor')
    bursts = card_.get('bursts', {})
    if isinstance(bursts, dict):
        count = bursts.get('count')
        if count is not None and count < 3 and bursts.get('repeat_interval_s') is not None:
            bad.append('repeat_interval_s is set under 3 bursts')
        if count is not None and count >= 3 and bursts.get('repeat_interval_s') is None:
            bad.append('repeat_interval_s is None with 3 or more bursts')
        if len(bursts.get('durations_s') or []) > 32 or len(bursts.get('gaps_s') or []) > 32:
            bad.append('more than 32 durations or gaps listed')
        duty = bursts.get('duty')
        if duty is not None and not 0 <= duty <= 1:
            bad.append('duty is not in 0..1')
    env = card_.get('envelope', {})
    if isinstance(env, dict):
        if not (isinstance(env.get('levels'), list) and 1 <= len(env['levels']) <= 2
                and env['levels'] == sorted(env['levels'])):
            bad.append('envelope.levels is not 1 or 2 levels, low first')
        if env.get('depth') is not None and not 0 <= env['depth'] <= 1:
            bad.append('envelope.depth is not in 0..1')
        if not isinstance(env.get('keyed'), bool):
            bad.append('envelope.keyed is not a bool')
    freq = card_.get('frequency', {})
    if isinstance(freq, dict):
        if not (isinstance(freq.get('levels_hz'), list) and 1 <= len(freq['levels_hz']) <= 2
                and freq['levels_hz'] == sorted(freq['levels_hz'])):
            bad.append('frequency.levels_hz is not 1 or 2 levels, low first')
        if not isinstance(freq.get('keyed'), bool):
            bad.append('frequency.keyed is not a bool')
    rate = card_.get('symbol_rate_baud')
    if rate is not None and not isinstance(rate, (int, float)):
        bad.append('symbol_rate_baud is not a number or None')
    return bad


# ---- files for the reader and write checks -----------------------------------

def snapshot(folder):
    """Every file of ``folder``: its digest, size and modification time."""
    state = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        st = os.stat(path)
        with open(path, 'rb') as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        state[name] = (digest, st.st_size, st.st_mtime_ns)
    return state


def write_cfile(path, data):
    np.asarray(data, dtype=np.complex64).tofile(path)
    return path


def write_sigmf(base, rate, centre):
    with open(base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate},
                   'captures': [{'core:sample_start': 0, 'core:frequency': centre}],
                   'annotations': []}, fh)


def write_noise_seconds(path, seconds, seed):
    """A file of ``seconds`` whole seconds of noise, written a second at a time."""
    rng = np.random.default_rng(seed)
    with open(path, 'wb') as fh:
        for _ in range(int(seconds)):
            _power_noise(rng, int(RATE), 1e-2).astype(np.complex64).tofile(fh)
    return path


def reader_files():
    """The files the reader and write checks use, written once into ``readers/``."""
    def build():
        folder = fresh('readers')
        f = {}
        base = os.path.join(folder, 'sig')
        f['sigmf'] = write_cfile(base + '.cfile', cw_iq(seconds=0.5, rate=2e6, amp=0.5, seed=11))
        write_sigmf(base, 2e6, 98.4e6)
        f['sigmf_meta'] = base + '.sigmf-meta'
        f['bare'] = write_cfile(os.path.join(folder, 'bare.cfile'),
                                cw_iq(seconds=0.5, amp=0.5, seed=12))
        short = cw_iq(seconds=0.002, amp=0.5, seed=13)
        f['short'] = write_cfile(os.path.join(folder, 'short.cfile'), short[:1023])
        f['ok1024'] = write_cfile(os.path.join(folder, 'ok1024.cfile'), short[:1024])
        f['long15'] = write_cfile(os.path.join(folder, 'long15.cfile'),
                                  cw_iq(seconds=1.5, amp=0.5, seed=16))
        f['long31'] = write_noise_seconds(os.path.join(folder, 'long31.cfile'), 31, 21)
        f['sdvr'] = os.path.join(folder, 'iq.sdvr')
        f['sdvr_data'] = signals.write_sdvr(f['sdvr'], cw_iq(seconds=0.5, amp=0.5, seed=14),
                                            RATE, CENTRE)
        f['cdif'] = os.path.join(folder, 'iq.cdif')
        f['cdif_data'] = cw_iq(seconds=0.5, amp=0.5, seed=15)
        signals.write_blue(f['cdif'], f['cdif_data'], RATE, keywords={'RF_FREQ': CENTRE})
        f['sweep'] = os.path.join(folder, 'sweep.sdvr')
        signals.write_sweep_sdvr(f['sweep'], tiles=8, bins=4096)
        f['fft'] = os.path.join(folder, 'spectrum.fft')
        spectrum = np.abs(np.random.default_rng(17).standard_normal(64 * 8)).astype(np.float32)
        signals.write_blue(f['fft'], spectrum, RATE, fmt='SF', subsize=64)
        f['junk_sdvr'] = os.path.join(folder, 'junk.sdvr')
        with open(f['junk_sdvr'], 'wb') as fh:
            fh.write(np.random.default_rng(18).bytes(8192))
        f['junk_bin'] = os.path.join(folder, 'thing.bin')
        with open(f['junk_bin'], 'wb') as fh:
            fh.write(np.random.default_rng(19).bytes(8192))
        f['missing'] = os.path.join(folder, 'nothere.cfile')
        return f
    return memo('files', build)


def source(key, path, **kw):
    """``open_iq`` of a reader file, opened once."""
    return memo(('source', key), lambda: api().open_iq(path, **kw))


def measured(key, path, **kw):
    """``measure_file`` of a reader file, measured once."""
    return memo(('measure', key), lambda: api().measure_file(path, **kw))


# ---- the checks --------------------------------------------------------------

def check_classes():
    for name, want in [('cw', 'carrier'), ('ook', 'ook'), ('fsk', 'fsk'), ('fm', 'fm'),
                       ('am', 'am'), ('noise', 'noise'), ('two_tone', 'unknown'),
                       ('cw_0db', 'noise'), ('ask', 'ask'), ('ook_noisy', 'noise')]:
        expect(f'{name}: class is {want}',
               lambda n=name, w=want: eq(card(n)['class'], w))


def check_bandwidth():
    expect('CW: occupied bandwidth under 5 kHz',
           lambda: below(card('cw')['occupied']['bandwidth_hz'], 5e3))
    expect('AM: occupied bandwidth within 15 % of 2 kHz',
           lambda: rel_near(card('am')['occupied']['bandwidth_hz'],
                            memo('ref-am', am_reference), 0.15))
    expect('FM: occupied bandwidth within 15 % of the Bessel-line 99 % width',
           lambda: rel_near(card('fm')['occupied']['bandwidth_hz'],
                            memo('ref-fm', fm_reference), 0.15))
    expect('OOK: occupied bandwidth within 15 % of the waveform 99 % width',
           lambda: rel_near(card('ook')['occupied']['bandwidth_hz'],
                            memo('ref-ook', lambda: waveform_reference(
                                ook_iq(noisy=False), RATE)), 0.15))
    expect('FSK: occupied bandwidth within 15 % of the waveform 99 % width',
           lambda: rel_near(card('fsk')['occupied']['bandwidth_hz'],
                            memo('ref-fsk', lambda: waveform_reference(
                                fsk_iq(noisy=False), RATE)), 0.15))


def check_tones_and_centre():
    expect('CW: strongest tone at +50 kHz (within 2 bins)',
           lambda: tones_found(card('cw'), CENTRE + 50e3))
    expect('CW: power centre within 2 kHz of +50 kHz (absolute)',
           lambda: near(card('cw')['occupied']['centre_hz'], CENTRE + 50e3, 2e3))
    expect('two-tone: the +20 kHz tone is found (within 2 bins)',
           lambda: tones_found(card('two_tone'), CENTRE + 20e3))
    expect('two-tone: the -60 kHz tone is found (within 2 bins)',
           lambda: tones_found(card('two_tone'), CENTRE - 60e3))

    def ordered():
        tones = card('tones_levels')['tones']
        want = [CENTRE + 50e3, CENTRE + 150e3, CENTRE - 200e3]
        if len(tones) < 3:
            return False, f'{len(tones)} tones, want 3'
        for t, w in zip(tones, want):
            if abs(t['freq_hz'] - w) > 2 * RATE / 1024:
                return False, f"tones {[round(t['freq_hz']) for t in tones]}, want {want}"
        return True, ''
    expect('three tones, 1.0 / 0.5 / 0.25: listed strongest first', ordered)

    expect('nine tones: at most 8 listed',
           lambda: (1 <= len(card('tones_many')['tones']) <= 8,
                    f"{len(card('tones_many')['tones'])} tones listed"))

    def band_card():
        return memo('band', lambda: api().measure(
            iq('two_tone'), RATE, center_hz=CENTRE, band=(CENTRE + 5e3, CENTRE + 200e3)))

    def band_restricted():
        ok_in, why_in = tones_found(band_card(), CENTRE + 20e3)
        ok_out = not tones_found(band_card(), CENTRE - 60e3)[0]
        return ok_in and ok_out, why_in + ('' if ok_out else '; the -60 kHz tone is in the band')
    expect('band (+5 kHz to +200 kHz): only the +20 kHz tone is analysed', band_restricted)
    expect('band (+5 kHz to +200 kHz): the surviving tone is narrow (under 5 kHz)',
           lambda: below(band_card()['occupied']['bandwidth_hz'], 5e3))

    def weak_absent():
        c = card('tone_14db')
        listed, _ = tones_found(c, CENTRE - 300e3)
        return (not listed and tones_found(c, CENTRE + 50e3)[0],
                'the tone 14 dB over the floor is listed, or the strong tone is missing')
    expect('a tone about 14 dB above the floor (with a strong tone) is not listed', weak_absent)


def check_symbols_and_bursts():
    expect('OOK: symbol rate 4,000 baud within 5 %',
           lambda: rel_near(card('ook')['symbol_rate_baud'], 4000.0, 0.05))
    expect('FSK: symbol rate 9,600 baud within 5 %',
           lambda: rel_near(card('fsk')['symbol_rate_baud'], 9600.0, 0.05))
    expect('OOK: burst median duration 40 ms within 5 %',
           lambda: rel_near(card('ook')['bursts']['median_duration_s'], 0.040, 0.05))
    expect('OOK: repeat interval 100 ms within 2 %',
           lambda: rel_near(card('ook')['bursts']['repeat_interval_s'], 0.100, 0.02))
    expect('OOK, 3 bursts: repeat interval 100 ms within 2 %',
           lambda: rel_near(card('ook3')['bursts']['repeat_interval_s'], 0.100, 0.02))
    expect('OOK, 2 bursts: repeat interval is None',
           lambda: eq(card('ook2')['bursts']['repeat_interval_s'], None))
    expect('OOK: 12 bursts counted',
           lambda: eq(card('ook')['bursts']['count'], 12))
    expect('OOK: gap between bursts 60 ms within 5 %',
           lambda: rel_near(float(np.median(card('ook')['bursts']['gaps_s'])), 0.060, 0.05))
    for name in ['cw', 'fm', 'am', 'noise']:
        expect(f'{name}: no symbol rate (symbol_rate_baud is None)',
               lambda n=name: eq(card(n)['symbol_rate_baud'], None))


def check_level():
    def relation():
        c = memo('fs', lambda: api().measure(iq('cw'), RATE, center_hz=CENTRE,
                                             full_scale_dbm=-10.0))['level']
        return near(c['power_dbm'], c['power_dbfs'] - 10.0, 0.1)
    expect('power_dbm = power_dbfs + full_scale_dbm (-10 dBm) within 0.1 dB', relation)
    expect('power_dbm is None without a full-scale level',
           lambda: eq(card('cw')['level']['power_dbm'], None))


def check_measure_inputs():
    def src():
        return card('cw')['source']

    expect('array input: source rate and centre read back',
           lambda: (eq(src()['rate_sps'], RATE)[0] and eq(src()['center_hz'], CENTRE)[0],
                    f"rate {src()['rate_sps']}, centre {src()['center_hz']}"))
    expect('array input: t0 0 and t1 1 s for one second of samples',
           lambda: (near(src()['t0_s'], 0.0, 1e-6)[0] and near(src()['t1_s'], 1.0, 1e-6)[0],
                    f"t0 {src()['t0_s']}, t1 {src()['t1_s']}"))

    def t0_argument():
        c = memo('t0', lambda: api().measure(iq('cw'), RATE, center_hz=CENTRE, t0=2.0))
        return near(c['source']['t0_s'], 2.0, 1e-6)
    expect('t0 argument: source t0_s is the given time', t0_argument)

    expect('complex128 input: a CW is still a carrier',
           lambda: eq(api().measure(iq('cw').astype(np.complex128), RATE,
                                    center_hz=CENTRE)['class'], 'carrier'))


def check_shape():
    for name in ['cw', 'cw_0db', 'ook', 'ook2', 'ook3', 'ook_noisy', 'ask', 'fsk', 'fm',
                 'am', 'noise', 'two_tone', 'tones_levels', 'tones_many', 'tone_14db']:
        expect(f'{name}: card has the spec form',
               lambda n=name: (not shape_problems(card(n)),
                               '; '.join(shape_problems(card(n))[:3])))


def check_readers():
    f = reader_files
    err = lambda: api().IQSourceError       # noqa: E731

    sig = lambda: source('sigmf', f()['sigmf'])     # noqa: E731
    expect('.cfile with .sigmf-meta: kind, rate and centre read back',
           lambda: (eq(sig().kind, 'cfile')[0] and eq(sig().rate, 2e6)[0]
                    and eq(sig().center_hz, 98.4e6)[0],
                    f'kind {sig().kind}, rate {sig().rate}, centre {sig().center_hz}'))
    expect('.cfile with .sigmf-meta: samples and seconds of the file',
           lambda: (eq(sig().samples, 1000000)[0] and near(sig().seconds, 0.5, 1e-9)[0],
                    f'samples {sig().samples}, seconds {sig().seconds}'))
    expect('.cfile with .sigmf-meta: no full-scale level',
           lambda: eq(sig().full_scale_dbm, None))
    expect('.cfile: read() gives the file exactly, as complex64',
           lambda: (np.array_equal(sig().read(0, sig().samples),
                                   np.fromfile(f()['sigmf'], dtype=np.complex64))
                    and sig().read(0, 1).dtype == np.complex64,
                    'samples differ from the file'))
    expect('read() is clipped to the file',
           lambda: (eq(len(sig().read(sig().samples - 10, 100)), 10)[0]
                    and eq(len(sig().read(sig().samples, 5)), 0)[0],
                    'a read past the end did not clip'))

    expect('bare .cfile without rate= is refused',
           lambda: refused(lambda: api().open_iq(f()['bare']), err()))
    bare = lambda: source('bare', f()['bare'], rate=RATE)       # noqa: E731
    expect('bare .cfile with rate= is accepted',
           lambda: (eq(bare().kind, 'cfile')[0] and eq(bare().samples, 500000)[0],
                    f'kind {bare().kind}, samples {bare().samples}'))
    expect('bare .cfile with rate= reports that rate',
           lambda: near(bare().rate, RATE, 1e-3))
    expect('bare .cfile with rate= and center_hz= takes the centre',
           lambda: eq(api().open_iq(f()['bare'], rate=RATE, center_hz=CENTRE).center_hz,
                      CENTRE))

    dvr = lambda: source('sdvr', f()['sdvr'])                  # noqa: E731
    expect('IQ .sdvr: kind, rate and centre',
           lambda: (eq(dvr().kind, 'sdvr')[0] and near(dvr().rate, RATE, 1e-3)[0]
                    and eq(dvr().center_hz, CENTRE)[0],
                    f'kind {dvr().kind}, rate {dvr().rate}, centre {dvr().center_hz}'))
    expect('IQ .sdvr: samples and the full-scale level (-10 dBm)',
           lambda: (eq(dvr().samples, len(f()['sdvr_data']))[0]
                    and near(dvr().full_scale_dbm, -10.0, 0.1)[0],
                    f'samples {dvr().samples}, full scale {dvr().full_scale_dbm}'))
    expect('IQ .sdvr: read() gives the samples written',
           lambda: (np.allclose(dvr().read(0, dvr().samples), f()['sdvr_data'], atol=1e-6),
                    'samples differ'))
    expect('IQ .sdvr: path is the file opened',
           lambda: eq(os.path.abspath(dvr().path), os.path.abspath(f()['sdvr'])))
    expect('IQ .sdvr: seconds are samples over rate',
           lambda: near(dvr().seconds, dvr().samples / dvr().rate, 1e-6))

    cdif = lambda: source('cdif', f()['cdif'])                  # noqa: E731
    expect('.cdif: kind and rate',
           lambda: (eq(cdif().kind, 'cdif')[0] and near(cdif().rate, RATE, 0.5)[0],
                    f'kind {cdif().kind}, rate {cdif().rate}'))
    expect('.cdif: centre is RF_FREQ, 100 MHz',
           lambda: eq(cdif().center_hz, CENTRE))
    expect('.cdif: path is the file opened',
           lambda: eq(os.path.abspath(cdif().path), os.path.abspath(f()['cdif'])))
    expect('.cdif: seconds are samples over rate',
           lambda: near(cdif().seconds, cdif().samples / cdif().rate, 1e-6))
    expect('.cdif: samples and read() give the data written',
           lambda: (eq(cdif().samples, len(f()['cdif_data']))[0]
                    and np.allclose(cdif().read(0, cdif().samples), f()['cdif_data'],
                                    atol=1e-6), 'samples differ'))

    expect('a file of 1,024 samples is accepted',
           lambda: eq(api().open_iq(f()['ok1024'], rate=RATE).samples, 1024))
    expect('a file of 1,023 samples is refused',
           lambda: refused(lambda: api().open_iq(f()['short'], rate=RATE), err()))
    for name, key in [('a sweep .sdvr', 'sweep'), ('a .fft', 'fft'),
                      ('a .sdvr that is not a DVR', 'junk_sdvr'),
                      ('a file of unknown type', 'junk_bin'),
                      ('a missing file', 'missing')]:
        expect(f'{name} is refused',
               lambda k=key: refused(lambda: api().open_iq(f()[k]), err()))

    sigmf_m = lambda: measured('sigmf', f()['sigmf'])           # noqa: E731
    expect('measure_file .cfile with .sigmf-meta: a carrier, no dBm',
           lambda: (sigmf_m()['source']['kind'] == 'cfile' and sigmf_m()['class'] == 'carrier'
                    and sigmf_m()['level']['power_dbm'] is None,
                    f"kind {sigmf_m()['source']['kind']}, class {sigmf_m()['class']}"))
    expect('measure_file .cfile at 2 MS/s: tone at +50 kHz from 98.4 MHz',
           lambda: tones_found(sigmf_m(), 98.4e6 + 50e3, rate=2e6))

    dvr_m = lambda: measured('sdvr', f()['sdvr'])               # noqa: E731
    expect('measure_file .sdvr: a carrier, power in dBm from the DVR',
           lambda: (dvr_m()['source']['kind'] == 'sdvr' and dvr_m()['class'] == 'carrier'
                    and eq(dvr_m()['source']['center_hz'], CENTRE)[0]
                    and near(dvr_m()['level']['power_dbm'] - dvr_m()['level']['power_dbfs'],
                             -10.0, 0.1)[0],
                    f"kind {dvr_m()['source']['kind']}, class {dvr_m()['class']}, "
                    f"dBm {dvr_m()['level']['power_dbm']}"))
    expect('measure_file .sdvr: tone at +50 kHz from the DVR centre',
           lambda: tones_found(dvr_m(), dvr_m()['source']['center_hz'] + 50e3))

    cdif_m = lambda: measured('cdif', f()['cdif'])              # noqa: E731
    expect('measure_file .cdif: a carrier',
           lambda: (cdif_m()['source']['kind'] == 'cdif' and cdif_m()['class'] == 'carrier',
                    f"kind {cdif_m()['source']['kind']}, class {cdif_m()['class']}"))
    expect('measure_file .cdif: tone at +50 kHz from 100 MHz',
           lambda: tones_found(cdif_m(), CENTRE + 50e3))
    expect('measure_file .cdif: no dBm (no calibration)',
           lambda: eq(cdif_m()['level']['power_dbm'], None))

    expect('measure_file of a bare .cfile without rate= is refused',
           lambda: refused(lambda: api().measure_file(f()['bare']), err()))
    for name, key, kw in [('a sweep .sdvr', 'sweep', {}), ('a .fft', 'fft', {}),
                          ('a file under 1,024 samples', 'short', {'rate': RATE}),
                          ('a missing file', 'missing', {})]:
        expect(f'measure_file refuses {name}',
               lambda k=key, a=kw: refused(lambda: api().measure_file(f()[k], **a), err()))
    bare_m = lambda: measured('bare', f()['bare'], rate=RATE, center_hz=CENTRE)  # noqa: E731
    expect('measure_file of a bare .cfile with rate= and centre: tone at CENTRE + 50 kHz',
           lambda: tones_found(bare_m(), CENTRE + 50e3))

    long15 = lambda: measured('long15', f()['long15'], rate=RATE)      # noqa: E731
    expect('measure_file: default span is the whole file (1.5 s)',
           lambda: (near(long15()['source']['t0_s'], 0.0, 1e-6)[0]
                    and near(long15()['source']['t1_s'], 1.5, 1e-6)[0],
                    f"t0 {long15()['source']['t0_s']}, t1 {long15()['source']['t1_s']}"))
    span = lambda: measured('span', f()['long15'], t0=0.25, t1=0.75, rate=RATE)  # noqa: E731
    expect('measure_file: t0 and t1 are seconds into the file',
           lambda: (near(span()['source']['t0_s'], 0.25, 1e-6)[0]
                    and near(span()['source']['t1_s'], 0.75, 1e-6)[0],
                    f"t0 {span()['source']['t0_s']}, t1 {span()['source']['t1_s']}"))
    long31 = lambda: measured('long31', f()['long31'], rate=RATE)      # noqa: E731
    expect('measure_file: the default span is at most 30 s of a 31 s file',
           lambda: (near(long31()['source']['t0_s'], 0.0, 1e-6)[0]
                    and near(long31()['source']['t1_s'], 30.0, 1e-6)[0],
                    f"t1 {long31()['source']['t1_s']}"))


def check_write_card():
    def card_paths():
        base = os.path.join(FOLDER, 'x')
        return (api().card_path(base + '.cfile') == base + '.card.json',
                api().card_path(base + '.sdvr') is None,
                api().card_path(base + '.cdif') is None,
                api().card_path(base + '.fft') is None)
    names = ['a .cfile', 'a .sdvr', 'a .cdif', 'a .fft']
    expect("card_path: '<base>.card.json' beside a .cfile",
           lambda: (card_paths()[0], 'card_path of a .cfile is not <base>.card.json'))
    for i, label in enumerate(names[1:], 1):
        expect(f'card_path: None for {label}',
               lambda k=i, l=label: (card_paths()[k], f'card_path of {l} is not None'))
    expect('card_path: None for a .bin',
           lambda: (api().card_path(os.path.join(FOLDER, 'x.bin')) is None,
                    'card_path of a .bin is not None'))

    def read_back():
        folder = fresh('card-beside')
        cfile = write_cfile(os.path.join(folder, 'cw.cfile'), cw_iq(seconds=0.5, seed=31))
        written = api().write_card(cfile, card('cw'))
        with open(api().card_path(cfile)) as fh:
            loaded = json.load(fh)
        same = json.loads(json.dumps(card('cw'), allow_nan=False))
        return (os.path.abspath(written) == os.path.abspath(api().card_path(cfile))
                and loaded == same and loaded.get('schema') == 2,
                'the file read back is not the card')
    expect('write_card beside a .cfile: the file read back equals the card', read_back)

    for label, key in [('a .sdvr', 'sdvr'), ('a .cdif', 'cdif'), ('a .fft', 'fft')]:
        def refuse(k=key, what=label):
            folder = fresh('refuse-' + k)
            src = reader_files()[k]
            shutil.copy(src, folder)
            path = os.path.join(folder, os.path.basename(src))
            before = snapshot(folder)
            ok, why = refused(lambda: api().write_card(path, card('cw')), api().IQSourceError)
            after = snapshot(folder)
            return ok and before == after, why or f'{what}: something was written beside it'
        expect(f'write_card refuses {label}, and writes nothing beside it', refuse)

    def keep_other_json():
        folder = fresh('keep-json')
        cfile = write_cfile(os.path.join(folder, 'k.cfile'), cw_iq(seconds=0.5, seed=32))
        card_file = api().card_path(cfile)
        with open(card_file, 'w') as fh:
            fh.write('{"other": 1}')
        try:
            api().write_card(cfile, card('cw'))
        except Exception:
            pass
        with open(card_file) as fh:
            return fh.read() == '{"other": 1}', 'a file that is not a card was overwritten'
    expect('write_card never overwrites a JSON file that is not a card', keep_other_json)

    def keep_garbage():
        folder = fresh('keep-garbage')
        cfile = write_cfile(os.path.join(folder, 'g.cfile'), cw_iq(seconds=0.5, seed=33))
        card_file = api().card_path(cfile)
        with open(card_file, 'wb') as fh:
            fh.write(b'\x00\x01 not json')
        try:
            api().write_card(cfile, card('cw'))
        except Exception:
            pass
        with open(card_file, 'rb') as fh:
            return fh.read() == b'\x00\x01 not json', 'a file that is not JSON was overwritten'
    expect('write_card never overwrites a file that is not JSON', keep_garbage)

    def replace_card():
        folder = fresh('replace-card')
        cfile = write_cfile(os.path.join(folder, 'r.cfile'), cw_iq(seconds=0.5, seed=34))
        api().write_card(cfile, card('cw'))
        api().write_card(cfile, card('fsk'))
        with open(api().card_path(cfile)) as fh:
            loaded = json.load(fh)
        return (loaded == json.loads(json.dumps(card('fsk'), allow_nan=False)),
                'the second card did not replace the first')
    expect('write_card replaces an earlier card of its own', replace_card)

    def junk_extension():
        folder = fresh('junk-ext')
        path = os.path.join(folder, 'thing.bin')
        with open(path, 'wb') as fh:
            fh.write(b'\x01' * 4096)
        write = api().write_card
        before = snapshot(folder)
        try:
            write(path, card('cw'))
        except Exception:
            pass
        return snapshot(folder) == before, 'something was written beside a .bin'
    expect('write_card writes nothing beside a file that is not a .cfile', junk_extension)

    def reading_writes_nothing():
        folder = fresh('read-only')
        f = reader_files()
        for path in [f['sdvr'], f['cdif'], f['sigmf'], f['sigmf_meta']]:
            shutil.copy(path, folder)
        before = snapshot(folder)
        for path in [os.path.join(folder, n) for n in ('iq.sdvr', 'iq.cdif', 'sig.cfile')]:
            src = api().open_iq(path)
            src.read(0, min(src.samples, 1000))
            api().measure_file(path)
        return snapshot(folder) == before, 'reading or measuring wrote into the folder'
    expect('reading and measuring write nothing beside the file', reading_writes_nothing)


def check_import_clean():
    probe = (
        "import json, os, shutil, sys, tempfile\n"
        "import numpy as np\n"
        "sys.path.insert(0, %r)\n"
        "from fm_receiver import iqmeasure\n"
        "rng = np.random.default_rng(0)\n"
        "iq = (rng.standard_normal(8192) + 1j * rng.standard_normal(8192)).astype(np.complex64)\n"
        "iqmeasure.measure(iq, 1e6, center_hz=1e8)\n"
        "folder = tempfile.mkdtemp()\n"
        "path = os.path.join(folder, 'probe.cfile')\n"
        "iq.tofile(path)\n"
        "iqmeasure.open_iq(path, rate=1e6).read(0, 1024)\n"
        "iqmeasure.measure_file(path, rate=1e6)\n"
        "shutil.rmtree(folder)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    ) % os.path.dirname(HERE)

    def clean():
        proc = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                              text=True, timeout=120)
        if proc.returncode != 0:
            tail = (proc.stderr.strip().splitlines() or ['no output'])[-1]
            return False, f'the subprocess failed: {tail}'
        mods = json.loads(proc.stdout.strip().splitlines()[-1])
        hits = sorted({m for m in mods if any(part in BANNED for part in m.split('.'))})
        return not hits, f'loaded {hits}'
    expect('iqmeasure imports none of gnuradio, PyQt5, scipy, library, radios, dsp', clean)


def dbm_files():
    """A .cdif of a 1 mW tone (raw amplitude 1000, DATA_GAIN 60) in noise 30 dB down,
    the same samples without the keyword, and with a DATA_GAIN that is not a number."""
    def build():
        folder = fresh('dbm')
        raw = cw_iq(seconds=0.5, amp=1000.0, snr_db=30.0, seed=41)
        f = {'raw': raw,
             'gain': os.path.join(folder, 'gain.cdif'),
             'plain': os.path.join(folder, 'plain.cdif'),
             'text': os.path.join(folder, 'text.cdif')}
        signals.write_blue(f['gain'], raw, RATE, keywords={'RF_FREQ': CENTRE, 'DATA_GAIN': 60.0})
        signals.write_blue(f['plain'], raw, RATE, keywords={'RF_FREQ': CENTRE})
        signals.write_blue(f['text'], raw, RATE, keywords={'RF_FREQ': CENTRE, 'DATA_GAIN': 'high'})
        return f
    return memo('dbm-files', build)


def check_data_gain():
    f = dbm_files
    gain_m = lambda: measured('dbm-gain', f()['gain'])                  # noqa: E731
    gain_src = lambda: source('dbm-gain', f()['gain'])                  # noqa: E731
    expect('.cdif with DATA_GAIN 60: a 1 mW tone measures 0 dBm within 0.1 dB',
           lambda: near(gain_m()['level']['power_dbm'], 0.0, 0.1))
    expect('.cdif with DATA_GAIN 60: power_dbfs equals power_dbm',
           lambda: eq(gain_m()['level']['power_dbfs'], gain_m()['level']['power_dbm']))
    expect('.cdif with DATA_GAIN 60: full_scale_dbm is 0.0',
           lambda: eq(gain_src().full_scale_dbm, 0.0))
    expect('.cdif with DATA_GAIN 60: read() gives the counts scaled by 10 ** (-60 / 20)',
           lambda: (np.allclose(gain_src().read(0, 1000), f()['raw'][:1000] * 1e-3,
                                rtol=1e-5, atol=1e-9), 'samples differ'))
    expect('.cdif with DATA_GAIN 60: the tone is still found at +50 kHz',
           lambda: tones_found(gain_m(), CENTRE + 50e3))

    plain_m = lambda: measured('dbm-plain', f()['plain'])               # noqa: E731
    plain_src = lambda: source('dbm-plain', f()['plain'])               # noqa: E731
    expect('.cdif without DATA_GAIN: power_dbm is None',
           lambda: eq(plain_m()['level']['power_dbm'], None))
    expect('.cdif without DATA_GAIN: full_scale_dbm is None and read() gives the raw samples',
           lambda: (eq(plain_src().full_scale_dbm, None)[0]
                    and np.allclose(plain_src().read(0, 1000), f()['raw'][:1000], rtol=1e-6),
                    'raw samples were scaled or the full scale is set'))

    text_m = lambda: measured('dbm-text', f()['text'])                  # noqa: E731
    expect('.cdif with a DATA_GAIN that is not a number: power_dbm is None',
           lambda: eq(text_m()['level']['power_dbm'], None))


def wide_cw():
    """A CW at +50 kHz in white noise 40 dB down, at 28 MS/s over a 28 MHz band.
    0.15 s: enough for the noise to fill the whole band."""
    return memo('wide-cw', lambda: _with_noise(
        np.exp(2j * np.pi * 50e3 * _t(0.15, 28e6)), 40.0, seed=52, power=1.0))


def check_single_tone_wide_and_sidelobes():
    # A CW on an exact bin centre (40 of 1,024 bins) at 120 dB SNR: its window
    # sidelobes sit about 23 dB over the floor, 117 dB under the tone. Off a bin
    # centre, and at 100 to 108 dB, this synthetic tone lists no sidelobe at all,
    # so only this case tells the old tone rule from the fixed one.
    def cw_120db_bin():
        return memo('cw-120db-bin', lambda: api().measure(
            cw_iq(offset=40 * RATE / 1024, snr_db=120.0, seed=51), RATE, center_hz=CENTRE))

    def cw_28():
        return memo('cw-28', lambda: api().measure(wide_cw(), 28e6, center_hz=CENTRE))

    expect('CW at 120 dB SNR on a bin centre (1 MS/s): class carrier',
           lambda: eq(cw_120db_bin()['class'], 'carrier'))
    expect('CW at 120 dB SNR on a bin centre (1 MS/s): exactly one tone (window sidelobes are not tones)',
           lambda: eq(len(cw_120db_bin()['tones']), 1))

    # A tone at 108 dB SNR sits about 127 dB over the floor, so a spur 65 dB under
    # it is about 62 dB over the floor: listed unless the 60 dB rule drops it.
    def spur_65db():
        return memo('spur-65db', lambda: api().measure(
            tones_iq([50e3, 300e3], [1.0, 10 ** (-65 / 20)], snr_db=108, seed=54),
            RATE, center_hz=CENTRE))
    expect('CW at 108 dB SNR with a spur 65 dB below it (62 dB over the floor): class carrier',
           lambda: eq(spur_65db()['class'], 'carrier'))
    expect('CW at 108 dB SNR with a spur 65 dB below it: exactly one tone',
           lambda: eq(len(spur_65db()['tones']), 1))
    expect('CW in a 28 MHz band at 28 MS/s, 40 dB SNR: class carrier',
           lambda: eq(cw_28()['class'], 'carrier'))


# ---- the cost of a measurement (m5) ------------------------------------------

TOOLS = os.path.dirname(HERE)
WIDE_RATE = 28e6
BLOCK_SAMPLES = 1 << 22


def write_wide_cw(path, seconds, rate, offset=50e3, snr_db=40.0, seed=61):
    """A CW at ``offset`` in white noise ``snr_db`` down, written a block at a time,
    so the file is never held whole in memory."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds * rate))
    with open(path, 'wb') as fh:
        for s in range(0, n, BLOCK_SAMPLES):
            m = min(BLOCK_SAMPLES, n - s)
            sig = np.exp(2j * np.pi * offset * (np.arange(s, s + m) / rate))
            (sig + _power_noise(rng, m, 10 ** (-snr_db / 10))).astype(np.complex64).tofile(fh)
    return path


def wide_file(name, seconds, rate, snr_db=40.0, seed=61):
    return memo(('wide', name), lambda: write_wide_cw(
        os.path.join(fresh('cost'), name + '.cfile'), seconds, rate, snr_db=snr_db, seed=seed))


def probe_file(path, rate):
    """``measure_file`` of ``path`` in a fresh interpreter: its wall time, and the growth
    of the peak resident memory (MB) over a baseline taken after the imports.

    On Linux the peak is VmHWM, reset after the imports: a child process's
    ``ru_maxrss`` starts at its parent's peak, which a test process that has
    held big arrays would hide the growth behind. Elsewhere it is ``ru_maxrss``."""
    code = (
        "import json, resource, time\n"
        "import numpy\n"
        "from fm_receiver import iqmeasure\n"
        "def peak_kb():\n"
        "    try:\n"
        "        with open('/proc/self/status') as fh:\n"
        "            for line in fh:\n"
        "                if line.startswith('VmHWM:'):\n"
        "                    return int(line.split()[1])\n"
        "    except OSError:\n"
        "        pass\n"
        "    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "try:\n"
        "    open('/proc/self/clear_refs', 'w').write('5')\n"
        "except OSError:\n"
        "    pass\n"
        "base = peak_kb()\n"
        "start = time.perf_counter()\n"
        "card = iqmeasure.measure_file(%r, rate=%r, center_hz=%r)\n"
        "wall = time.perf_counter() - start\n"
        "grown = peak_kb() - base\n"
        "print(json.dumps({'class': card['class'], 'wall_s': wall, 'grown_mb': grown / 1024}))\n"
    ) % (path, rate, CENTRE)

    def run():
        proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                              timeout=600, env=dict(os.environ, PYTHONPATH=TOOLS))
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr.strip().splitlines() or ['no output'])[-1])
        return json.loads(proc.stdout.strip().splitlines()[-1])
    return memo(('probe', path), run)


def info(text):
    print(f'      {text}')


def check_cost_of_measuring():
    import tracemalloc

    def one_second():
        r = probe_file(wide_file('cw1s', 1.0, WIDE_RATE), WIDE_RATE)
        info(f"28 MS/s, 1 s: class {r['class']}, {r['wall_s']:.2f} s, "
             f"+{r['grown_mb']:.0f} MB peak resident")
        ok = r['class'] == 'carrier' and r['wall_s'] < 4.0 and r['grown_mb'] < 400
        return ok, (f"class {r['class']} (want carrier), {r['wall_s']:.2f} s (want under 4), "
                    f"+{r['grown_mb']:.0f} MB (want under 400)")
    expect('28 MS/s CW, 1 s: measure_file is a carrier in under 4 s and under 400 MB extra',
           one_second)

    def reads():
        path = wide_file('cw03', 0.3, WIDE_RATE)
        api_ = api()
        real_open = api_.open_iq
        requests = []

        def counting_open(p, rate=None, center_hz=None):
            src = real_open(p, rate=rate, center_hz=center_hz)
            inner = src.read

            def counted(start, count):
                requests.append(int(count))
                return inner(start, count)
            src.read = counted
            return src
        api_.open_iq = counting_open
        try:
            n = api_.measure_file(path, rate=WIDE_RATE, center_hz=CENTRE)['source']['samples']
        finally:
            api_.open_iq = real_open
        biggest, total = max(requests, default=0), sum(requests)
        info(f"28 MS/s, 0.3 s: {len(requests)} reads, largest {biggest}, total {total} "
             f"for a span of {n} samples")
        return (biggest <= BLOCK_SAMPLES and total <= 2 * n,
                f'largest request {biggest} samples (want <= {BLOCK_SAMPLES}), '
                f'total {total} for a span of {n} (want <= {2 * n})')
    expect('28 MS/s, 0.3 s: no read asks for more than 2**22 samples, the file is read at most twice',
           reads)

    def memory():
        path = wide_file('cw03', 0.3, WIDE_RATE)
        span_bytes = 8 * (os.path.getsize(path) // 8)
        tracemalloc.start()
        try:
            api().measure_file(path, rate=WIDE_RATE, center_hz=CENTRE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        bound = 0.25 * span_bytes
        info(f"28 MS/s, 0.3 s: peak traced {peak / 1e6:.1f} MB, "
             f"span as complex64 {span_bytes / 1e6:.1f} MB")
        return peak < bound, (f'peak traced {peak / 1e6:.1f} MB, want under {bound / 1e6:.1f} MB '
                              f'(0.25 x the span as complex64)')
    expect('28 MS/s, 0.3 s: peak traced memory under 0.25 x the span (no whole-span array)',
           memory)

    def agree():
        arr = iq('cw')
        path = write_cfile(os.path.join(fresh('agree'), 'cw.cfile'), arr)
        from_array = card('cw')
        from_file = memo('agree-file',
                         lambda: api().measure_file(path, rate=RATE, center_hz=CENTRE))
        same = from_array['class'] == from_file['class']
        power_gap = abs(from_array['level']['power_dbfs'] - from_file['level']['power_dbfs'])
        tone_gap = None
        if from_array['tones'] and from_file['tones']:
            tone_gap = abs(from_array['tones'][0]['freq_hz'] - from_file['tones'][0]['freq_hz'])
        ok = (same and power_gap <= 0.1 and tone_gap is not None
              and tone_gap <= 2 * RATE / 1024)
        return ok, (f"class {from_array['class']} vs {from_file['class']}, power gap "
                    f"{power_gap:.3g} dB (want 0.1), tone gap {tone_gap} Hz (want 2 bins)")
    expect('measure and measure_file of the same 1 MS/s samples: same class, power and tone',
           agree)

    def long_file():
        r = probe_file(wide_file('cw30s', 30.0, RATE, snr_db=30.0, seed=63), RATE)
        info(f"1 MS/s, 30 s: class {r['class']}, {r['wall_s']:.2f} s, "
             f"+{r['grown_mb']:.0f} MB peak resident")
        ok = r['wall_s'] < 5.0 and r['grown_mb'] < 1024
        return ok, (f"{r['wall_s']:.2f} s (want under 5), +{r['grown_mb']:.0f} MB "
                    f"(want under 1024)")
    expect('1 MS/s, 30 s: measure_file in under 5 s and under 1 GB extra', long_file)


GROUPS = [check_classes, check_bandwidth, check_tones_and_centre,
          check_symbols_and_bursts, check_level, check_measure_inputs, check_shape,
          check_readers, check_write_card, check_import_clean, check_data_gain,
          check_single_tone_wide_and_sidelobes, check_cost_of_measuring]


def main():
    global FOLDER
    parent = '/tmp/opencode' if os.path.isdir('/tmp/opencode') else None
    FOLDER = tempfile.mkdtemp(prefix='iqmeasure-test-', dir=parent)
    try:
        for group in GROUPS:
            try:
                group()
            except Exception as exc:
                report(f'{group.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    finally:
        shutil.rmtree(FOLDER, ignore_errors=True)
    failed = [name for name, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    if failed:
        print('failed:')
        for name in failed:
            print(f'  {name}')
        return 1
    print('all checks passed')
    return 0


def check_narrow_band():
    """A 5 Hz band round a CW in 10 s at 1 MS/s: no read or transform over 2**22
    samples, and the class is the unbanded one (the band's filter is wide enough)."""
    path = wide_file('cw10s', 10.0, RATE, snr_db=30.0, seed=64)

    def narrow():
        api_ = api()
        real_open = api_.open_iq
        sizes = []

        def counting_open(p, rate=None, center_hz=None):
            src = real_open(p, rate=rate, center_hz=center_hz)
            inner = src.read

            def counted(start, count):
                sizes.append(int(count))
                return inner(start, count)
            src.read = counted
            return src
        api_.open_iq = counting_open
        tone = CENTRE + 50e3
        try:
            banded = api_.measure_file(path, rate=RATE, center_hz=CENTRE,
                                       band=(tone - 2.5, tone + 2.5))
        finally:
            api_.open_iq = real_open
        whole = memo('cw10s-whole', lambda: api_.measure_file(path, rate=RATE, center_hz=CENTRE))
        biggest = max(sizes, default=0)
        info(f"5 Hz band, 10 s at 1 MS/s: {len(sizes)} reads, largest {biggest}, "
             f"class {banded['class']} banded vs {whole['class']} whole")
        return (biggest <= 2 ** 22 and banded['class'] == whole['class'],
                f"largest read {biggest} samples (want <= {2 ** 22}), class {banded['class']} "
                f"banded vs {whole['class']} whole")
    expect('5 Hz band round a CW (10 s, 1 MS/s): no read over 2**22 samples, class as unbanded',
           narrow)


GROUPS.append(check_narrow_band)


def _cw_card(rate, seconds, snr_db, seed, offset=100003.3):
    """A CW at ``offset`` written in blocks, SNR over the whole recorded band."""
    path = write_wide_cw(os.path.join(fresh('lowsnr'), f'cw-{rate:.0f}-{snr_db:.0f}.cfile'),
                         seconds, rate, offset=offset, snr_db=snr_db, seed=seed)
    return api().measure_file(path, rate=rate, center_hz=CENTRE)


def _carrier_ok(card, dev_bound=100.0):
    freq = card['frequency']
    one = len(card['tones']) == 1 and len(freq['levels_hz']) == 1
    ok = (card['class'] == 'carrier' and freq['keyed'] is False
          and freq['deviation_hz'] < dev_bound and one)
    detail = (f"class {card['class']}, keyed {freq['keyed']}, "
              f"dev {freq['deviation_hz']} (want < {dev_bound}), "
              f"levels {freq['levels_hz']}, tones {len(card['tones'])}")
    return ok, detail


#: Half-range of a steady tone's frequency scatter, as noise alone makes it: the
#: measurement tracks the tone in at most 1 MHz, so the noise it sees is the
#: recorded band's, narrowed to 1 MHz above 1 MS/s, and the scatter falls 10x
#: for every 20 dB of SNR. Fitted on 1, 2 and 10 MS/s, 15 to 40 dB, 4 seeds, 50 ms
#: (953 Hz at 0 dB; 105-110 Hz at 1 MS/s and 19 dB, 69-79 Hz at 2 MS/s).
CW_NOISE_HZ_AT_0_DB = 953.0
#: What does not shrink with SNR (the splice of the band-limit's blocks): 28 Hz at
#: 28 MS/s and 40 dB. Above 1 MS/s the tone is narrowed to 1 MHz.
CW_FLOOR_HZ = 35.0
CW_MARGIN = 1.2


def cw_deviation_bound(rate, snr_db):
    """The deviation (Hz) a steady CW may show: the noise's scatter at the SNR the
    tracker sees, 20 % over, plus the floor. Instead of a number fitted to a seed."""
    seen = snr_db + 10 * np.log10(max(rate / 1e6, 1.0))
    return CW_MARGIN * CW_NOISE_HZ_AT_0_DB * 10 ** (-seen / 20) + CW_FLOOR_HZ


def check_low_snr_wide_cw():
    """A CW at low SNR in a wide band stays a carrier.

    50 ms, not 3 s: past about 55 ms the edge cluster is only ~2 % of the
    frequency blocks and loses the split, so the misclassification hides.
    50 ms still classes the 28 MS/s tone as FSK. A tone at +64750 Hz keeps a
    single level out to 1 s, but the splice widens deviation to about 143 Hz
    (22 Hz when the overlap pad is 16384). At 1 MS/s the half-range of a
    steady tone is 109 to 113 Hz on this code and on the pre-m5 filter
    (644289d, same three seeds): the bound there is derived from the SNR
    (``cw_deviation_bound``), not fixed at 100 or 130 Hz.
    """
    import time
    seconds = 0.05

    def wide(rate, snr, seed, bound=None):
        bound = cw_deviation_bound(rate, snr) if bound is None else bound
        start = time.perf_counter()
        card = _cw_card(rate, seconds, snr, seed)
        wall = time.perf_counter() - start
        ok, detail = _carrier_ok(card, bound)
        info(f"{rate / 1e6:.0f} MS/s, {snr:.0f} dB, {seconds} s: {wall:.2f} s, {detail}")
        return ok, f'{wall:.2f} s, {detail}'

    expect('CW +100003.3 Hz, 28 MS/s, 19 dB over 28 MHz: carrier, one tone, deviation < noise bound',
           lambda: wide(28e6, 19.0, 1))
    expect('CW +100003.3 Hz, 10 MS/s, 19 dB over the band: carrier, one tone, deviation < noise bound',
           lambda: wide(10e6, 19.0, 72))
    expect('CW +100003.3 Hz, 1 MS/s, 19 dB over the band: carrier, one tone, deviation < noise bound',
           lambda: wide(1e6, 19.0, 73))
    expect('CW +100003.3 Hz, 2 MS/s, 25 dB over the band: carrier, one tone, deviation < noise bound',
           lambda: wide(2e6, 25.0, 74))
    expect('CW +100003.3 Hz, 28 MS/s, 40 dB over 28 MHz: carrier, one tone, deviation < noise bound',
           lambda: wide(28e6, 40.0, 1))

    def wide_at(rate, span, snr, seed, offset, bound=100.0):
        start = time.perf_counter()
        card = _cw_card(rate, span, snr, seed, offset=offset)
        wall = time.perf_counter() - start
        ok, detail = _carrier_ok(card, bound)
        info(f"{rate / 1e6:.0f} MS/s, {snr:.0f} dB, {span} s, offset {offset}: "
             f"{wall:.2f} s, {detail}")
        return ok, f'{wall:.2f} s, {detail}'

    expect('CW +64750 Hz, 28 MS/s, 1 s, 19 dB: carrier, one tone, deviation < 100',
           lambda: wide_at(28e6, 1.0, 19.0, 1, 64750.0))

    def blocks():
        path = write_wide_cw(os.path.join(fresh('lowsnr'), 'cw-blocks.cfile'),
                             seconds, 28e6, offset=100003.3, snr_db=19.0, seed=1)
        nominal = api().FILTER_BLOCK
        levels = []
        start = time.perf_counter()
        try:
            for factor in (0.5, 1.0, 2.0):
                api().FILTER_BLOCK = int(nominal * factor)
                card = api().measure_file(path, rate=28e6, center_hz=CENTRE)
                levels.append(card['frequency']['levels_hz'])
        finally:
            api().FILTER_BLOCK = nominal
        wall = time.perf_counter() - start
        singles = [lv[0] for lv in levels if len(lv) == 1]
        ok = len(singles) == 3 and max(singles) - min(singles) <= 20.0
        info(f"FILTER_BLOCK x1/2, x1, x2: {wall:.2f} s, levels {levels}")
        return ok, f'{wall:.2f} s, levels {levels}'
    expect('CW at 19 dB, 28 MS/s: frequency level within 20 Hz at half and double FILTER_BLOCK',
           blocks)


GROUPS.append(check_low_snr_wide_cw)


# ---- the span extracted for a decoder (m6) ----------------------------------

def _tone_file(name, rate, seconds, tones, sigmf=True):
    """A .cfile at ``rate`` of ``tones`` (offset Hz, amplitude) from CENTRE, with its
    .sigmf-meta giving the rate and CENTRE unless ``sigmf`` is False."""
    base = os.path.join(fresh('extract'), name)
    t = _t(seconds, rate)
    sig = np.zeros(len(t), dtype=np.complex128)
    for f, a in tones:
        sig += a * np.exp(2j * np.pi * f * t)
    write_cfile(base + '.cfile', sig)
    if sigmf:
        write_sigmf(base, rate, CENTRE)
    return base + '.cfile'


def _peak_and_rest(iq, rate):
    """The frequency of the strongest line of ``iq`` (Hz) and how many dB the strongest
    line more than 1 kHz away is under it."""
    x = np.asarray(iq, dtype=np.complex128) * np.hanning(len(iq))
    p = np.abs(np.fft.fft(x)) ** 2
    f = np.fft.fftfreq(len(x), 1 / rate)
    k = int(np.argmax(p))
    far = np.abs(f - f[k]) > 1e3
    return float(f[k]), float(10 * np.log10(p[k] / max(float(p[far].max()), 1e-300)))


def check_extract():
    def pair():
        return memo('extract-pair', lambda: api().extract(
            _tone_file('pair', 2e6, 0.2, [(200e3, 1.0), (-600e3, 1.0)]),
            band=(CENTRE + 150e3, CENTRE + 250e3), max_rate=250000))

    expect('extract band 100.15 to 100.25 MHz: centre is 100.2 MHz (within 1 Hz)',
           lambda: near(pair().center_hz, CENTRE + 200e3, 1.0))
    expect('extract band 100.15 to 100.25 MHz, max_rate 250 kS/s: rate between 2x the band and 250 kS/s',
           lambda: (200e3 <= pair().rate <= 250000, f'rate {pair().rate}'))

    def pair_tones():
        f, below = _peak_and_rest(pair().iq, pair().rate)
        return (abs(f) <= 200 and below >= 40,
                f'strongest at {f:.1f} Hz, the rest {below:.1f} dB under it (want 0 Hz, 40 dB)')
    expect('extract band 100.15 to 100.25 MHz: +200 kHz tone at 0 Hz, -600 kHz tone 40 dB down',
           pair_tones)

    def whole():
        return memo('extract-whole', lambda: api().extract(
            _tone_file('whole', 2e6, 0.2, [(100e3, 1.0), (400e3, 1.0)])))

    expect('extract with no band at 2 MS/s, max_rate 1 MS/s: centre is the file centre',
           lambda: eq(whole().center_hz, CENTRE))
    expect('extract with no band at 2 MS/s, max_rate 1 MS/s: rate at most 1 MS/s and spans 500 kHz',
           lambda: (5e5 <= whole().rate <= 1e6, f'rate {whole().rate}'))

    def whole_tones():
        f, below = _peak_and_rest(whole().iq, whole().rate)
        return (abs(f - 100e3) <= 200 and below >= 40,
                f'strongest at {f:.1f} Hz, the rest {below:.1f} dB under it (want 100 kHz, 40 dB)')
    expect('extract with no band: +100 kHz tone at +100 kHz, +400 kHz tone 40 dB down',
           whole_tones)

    def slow():
        def make():
            data = _power_noise(np.random.default_rng(72), 50000, 1.0).astype(np.complex64)
            base = os.path.join(fresh('extract'), 'slow')
            write_cfile(base + '.cfile', data)
            write_sigmf(base, 250000, CENTRE)
            return data, api().extract(base + '.cfile', max_rate=1e6)
        return memo('extract-slow', make)

    expect('extract of a 250 kS/s file with max_rate 1 MS/s: rate is 250000',
           lambda: eq(slow()[1].rate, 250000.0))
    expect('extract of a 250 kS/s file with max_rate 1 MS/s: length is the file\'s samples',
           lambda: eq(len(slow()[1].iq), len(slow()[0])))
    expect('extract of a 250 kS/s file with max_rate 1 MS/s: samples are the file\'s',
           lambda: (np.allclose(slow()[1].iq, slow()[0]) and slow()[1].iq.dtype == np.complex64,
                    'samples differ from the file'))

    def span():
        return memo('extract-span', lambda: api().extract(
            _tone_file('span', 2e6, 0.2, [(100e3, 1.0)]), t0=0.05, t1=0.1))

    expect('extract t0=0.05 t1=0.1: t0 and t1 returned as given (within 1 sample)',
           lambda: (near(span().t0, 0.05, 1 / 2e6)[0] and near(span().t1, 0.1, 1 / 2e6)[0],
                    f't0 {span().t0}, t1 {span().t1}'))
    expect('extract t0=0.05 t1=0.1: length is (t1 - t0) x the output rate (within 1 %)',
           lambda: rel_near(len(span().iq), (span().t1 - span().t0) * span().rate, 0.01))

    def counted_reads():
        path = _tone_file('reads', 2e6, 0.5, [(200e3, 1.0)])
        n = os.path.getsize(path) // 8
        api_ = api()
        real_open = api_.open_iq
        sizes = []

        def counting_open(p, rate=None, center_hz=None):
            src = real_open(p, rate=rate, center_hz=center_hz)
            inner = src.read

            def counted(start, count):
                sizes.append(int(count))
                return inner(start, count)
            src.read = counted
            return src
        api_.open_iq = counting_open
        try:
            api_.extract(path, band=(CENTRE + 195e3, CENTRE + 205e3))
        finally:
            api_.open_iq = real_open
        biggest, total = max(sizes, default=0), sum(sizes)
        info(f"2 MS/s, 0.5 s, 10 kHz band: {len(sizes)} reads, largest {biggest}, total {total}")
        return (0 < len(sizes) and biggest <= 2 ** 22 and total <= 1.1 * n,
                f'largest read {biggest} (want <= {2 ** 22}), total {total} for a span of {n} '
                f'(want <= {1.1 * n:.0f})')
    expect('extract 2 MS/s, 0.5 s, narrow band: no read over 2**22 samples, total at most 1.1 x span',
           counted_reads)

    def too_big():
        api_ = api()
        real_open = api_.open_iq
        calls = []

        def fetch(a, b):
            calls.append((a, b))
            raise RuntimeError('read before the size check')

        def huge_open(p, rate=None, center_hz=None):
            return api_.IQSource(p, 'cfile', 1e6, CENTRE, 3 * 10 ** 8, None, fetch)
        api_.open_iq = huge_open
        try:
            ok, why = refused(lambda: api_.extract(os.path.join(FOLDER, 'huge.cfile')),
                              api_.IQSourceError)
        finally:
            api_.open_iq = real_open
        return ok and not calls, why or 'it read before refusing'
    expect('extract of more than 50 million output samples: IQSourceError before any read',
           too_big)

    def bare():
        folder = fresh('extract-bare')
        path = write_cfile(os.path.join(folder, 'bare.cfile'),
                           cw_iq(seconds=0.2, offset=100e3, amp=0.5, snr_db=30.0, rate=2e6,
                                 seed=74))
        return api().extract(path, rate=2e6, center_hz=CENTRE)

    expect('extract of a bare .cfile with rate= and center_hz=: centre and rate read back',
           lambda: (eq(bare().center_hz, CENTRE)[0] and bare().rate <= 1e6,
                    f'centre {bare().center_hz}, rate {bare().rate}'))
    expect('extract of a bare .cfile with rate= and center_hz=: +100 kHz tone at +100 kHz',
           lambda: (abs(_peak_and_rest(bare().iq, bare().rate)[0] - 100e3) <= 200,
                    f'strongest at {_peak_and_rest(bare().iq, bare().rate)[0]:.1f} Hz'))


GROUPS.append(check_extract)


# ---- extract's output rate, the output cap and max_rate (m6 fix round) ------

def _stub_source(seconds, fetch_calls, rate=2e6):
    """An IQSource of ``seconds`` of zeros: its fetch returns zeros, chunk by chunk, and
    counts the requests in ``fetch_calls``."""
    def fetch(a, b):
        fetch_calls.append(b - a)
        return np.zeros(b - a, dtype=np.complex64)
    return api().IQSource('stub.cfile', 'cfile', rate, CENTRE, int(round(seconds * rate)),
                          None, fetch)


def _with_source(src, call):
    """``call()`` with ``open_iq`` answering ``src`` for every path."""
    api_ = api()
    real_open = api_.open_iq
    api_.open_iq = lambda p, rate=None, center_hz=None: src
    try:
        return call()
    finally:
        api_.open_iq = real_open


def check_extract_rates():
    def odd_defaults():
        return memo('extract-2.4-defaults', lambda: api().extract(
            _tone_file('odd24', 2.4e6, 0.2, [(100e3, 1.0), (400e3, 1.0)])))

    expect('extract 2.4 MS/s, defaults: rate at most 1 MS/s (no integer decimation fits 2 x 500 kHz)',
           lambda: (odd_defaults().rate <= 1e6, f'rate {odd_defaults().rate}'))
    expect('extract 2.4 MS/s, defaults: band narrowed about the file centre, so centre unchanged',
           lambda: eq(odd_defaults().center_hz, CENTRE))

    def odd_tones():
        e = odd_defaults()
        f, below = _peak_and_rest(e.iq, e.rate)
        return (abs(f - 100e3) <= 200 and below >= 40,
                f'strongest at {f:.1f} Hz, the rest {below:.1f} dB under it (want 100 kHz, 40 dB)')
    expect('extract 2.4 MS/s, defaults: +100 kHz tone at +100 kHz, +400 kHz tone 40 dB down',
           odd_tones)

    def band100():
        return memo('extract-2.4-100k', lambda: api().extract(
            _tone_file('band24', 2.4e6, 0.2, [(100e3, 1.0)]),
            band=(CENTRE + 50e3, CENTRE + 150e3), max_rate=1e6))
    expect('extract 2.4 MS/s, 100 kHz band, max_rate 1 MS/s: rate in [200 kS/s, 1 MS/s]',
           lambda: (200e3 <= band100().rate <= 1e6, f'rate {band100().rate}'))
    expect('extract 2.4 MS/s, 100 kHz band, max_rate 1 MS/s: the largest integer decimation (200 kS/s)',
           lambda: rel_near(band100().rate, 200e3, 1e-6))
    expect('extract 2.4 MS/s, 100 kHz band: the tone at the band centre is at 0 Hz',
           lambda: (abs(_peak_and_rest(band100().iq, band100().rate)[0]) <= 200,
                    f'strongest at {_peak_and_rest(band100().iq, band100().rate)[0]:.1f} Hz'))

    def long_narrow():
        def run():
            calls = []
            src = _stub_source(60.0, calls)
            band = (CENTRE + 195e3, CENTRE + 205e3)
            return _with_source(src, lambda: api().extract('stub.cfile', band=band, max_rate=1e6))
        return memo('extract-60s-narrow', run)
    expect('extract 60 s at 2 MS/s, 10 kHz band, max_rate 1 MS/s: no error (output under the cap)',
           lambda: (long_narrow() is not None, 'raised'))
    expect('extract 60 s at 2 MS/s, 10 kHz band: about 60 s x 20 kS/s out, rate 20 kS/s',
           lambda: (eq(long_narrow().rate, 20e3)[0]
                    and rel_near(len(long_narrow().iq), 60 * 20e3, 0.001)[0],
                    f'rate {long_narrow().rate}, samples {len(long_narrow().iq)}'))

    def wide_refused():
        calls = []
        src = _stub_source(60.0, calls)
        ok, why = _with_source(src, lambda: refused(
            lambda: api().extract('stub.cfile', max_rate=1e9), api().IQSourceError))
        return ok and not calls, why or f'it fetched {len(calls)} chunks before refusing'
    expect('extract 60 s at 2 MS/s, no band, max_rate 1 GS/s: IQSourceError before any read',
           wide_refused)

    path = _tone_file('rate-arg', 2e6, 0.2, [(100e3, 1.0)])
    for bad in (0, -5, float('nan'), float('inf')):
        expect(f'extract max_rate={bad}: IQSourceError',
               lambda b=bad: refused(lambda: api().extract(path, max_rate=b), api().IQSourceError))


GROUPS.append(check_extract_rates)


# ---- the fallback band at rates that no integer decimation fits (m6 fix round 2) ----

def _line_near(iq, rate, f):
    """(frequency, dB) of the strongest line within 1 kHz of ``f`` (Hann window)."""
    x = np.asarray(iq, dtype=np.complex128) * np.hanning(len(iq))
    p = np.abs(np.fft.fft(x)) ** 2
    freqs = np.fft.fftfreq(len(x), 1 / rate)
    near = np.flatnonzero(np.abs(freqs - f) <= 1e3)
    k = near[int(np.argmax(p[near]))]
    return float(freqs[k]), float(10 * np.log10(p[k]))


def _line_over_rest(iq, rate, f):
    """How many dB the strongest line within 1 kHz of ``f`` is over the strongest
    line more than 1 kHz away from it (Hann window)."""
    x = np.asarray(iq, dtype=np.complex128) * np.hanning(len(iq))
    p = np.abs(np.fft.fft(x)) ** 2
    freqs = np.fft.fftfreq(len(x), 1 / rate)
    dist = np.abs(freqs - f)
    near = np.flatnonzero(dist <= 1e3)
    rest = dist > 1e3
    return float(10 * np.log10(p[near].max() / max(float(p[rest].max()), 1e-300)))


def check_extract_fallback():
    def fallback(name, rate, tones):
        return memo(('extract-fallback', name), lambda: api().extract(
            _tone_file(name, rate, 0.2, tones)))

    p24 = lambda: fallback('fb24-230', 2.4e6, [(100e3, 1.0), (230e3, 1.0)])     # noqa: E731
    expect('extract 2.4 MS/s, no band: +230 kHz tone present at +230 kHz (within 200 Hz)',
           lambda: (abs(_line_near(p24().iq, p24().rate, 230e3)[0] - 230e3) <= 200,
                    f'strongest near 230 kHz at {_line_near(p24().iq, p24().rate, 230e3)[0]:.1f} Hz'))
    expect('extract 2.4 MS/s, no band: +230 kHz tone within 6 dB of the +100 kHz tone',
           lambda: (abs(_line_near(p24().iq, p24().rate, 230e3)[1]
                        - _line_near(p24().iq, p24().rate, 100e3)[1]) <= 6,
                    f"+230 kHz {_line_near(p24().iq, p24().rate, 230e3)[1]:.1f} dB, "
                    f"+100 kHz {_line_near(p24().iq, p24().rate, 100e3)[1]:.1f} dB"))

    p24_450 = lambda: fallback('fb24-450', 2.4e6, [(100e3, 1.0), (450e3, 1.0)])  # noqa: E731
    expect('extract 2.4 MS/s, no band: +450 kHz tone at least 40 dB down from the +100 kHz tone',
           lambda: (_line_over_rest(p24_450().iq, p24_450().rate, 100e3) >= 40,
                    f'the rest is {_line_over_rest(p24_450().iq, p24_450().rate, 100e3):.1f} dB under'))

    p2048 = lambda: fallback('fb2048-200', 2.048e6, [(100e3, 1.0), (200e3, 1.0)])  # noqa: E731
    expect('extract 2.048 MS/s, no band: +200 kHz tone present at +200 kHz (within 200 Hz)',
           lambda: (abs(_line_near(p2048().iq, p2048().rate, 200e3)[0] - 200e3) <= 200,
                    f'strongest near 200 kHz at {_line_near(p2048().iq, p2048().rate, 200e3)[0]:.1f} Hz'))
    expect('extract 2.048 MS/s, no band: +200 kHz tone within 6 dB of the +100 kHz tone',
           lambda: (abs(_line_near(p2048().iq, p2048().rate, 200e3)[1]
                        - _line_near(p2048().iq, p2048().rate, 100e3)[1]) <= 6,
                    f"+200 kHz {_line_near(p2048().iq, p2048().rate, 200e3)[1]:.1f} dB, "
                    f"+100 kHz {_line_near(p2048().iq, p2048().rate, 100e3)[1]:.1f} dB"))


GROUPS.append(check_extract_fallback)


if __name__ == '__main__':
    sys.exit(main())
