"""The LTE structural fingerprint (phase l1): ``fm_receiver/iqlte.py`` against
tools/swarm-records/specs/l1.md.

Signals: ``tests/lte_synth.py`` (an LTE FDD downlink, seeded) and
``tests/lte_negatives.py`` (signals that are not LTE). Each case is built once
and only its fingerprint is kept, so the groups share results without holding
every capture in memory.

The two checks that ran are the CP autocorrelation and the PSS search; they
are taken by ``ran``, not by name, and the gates are order-free (both chances
checked, whatever their order). SSS and CRS are found by name ('sss', 'crs').
Since the CRS check (``crs_search``, tested in test_iqlte_crs.py) both run when
the SSS gives a PCI.

Groups, one counted line each (the gates G1-G5, G7, G8 of the spec):
  A  G1  10 MHz, PCI 0/123/503, 20 and 0 dB, seeds 0-4 (30 cases):
         verdict lte, nid2 == PCI mod 3, nfft 1024
  A  G7  the 20 dB cases of A (15): both checks' chance <= 1e-6
  B  G2  1.4, 3, 5, 10, 20 MHz at 20 dB, PCI 123, seeds 0-2 (15 cases):
         verdict lte, nfft = the class table's N
  C  G3  10 MHz resampled to 20 MS/s, CFO +2 kHz, 10 dB, PCI 123, seeds 0-4
         (5 cases): verdict lte, nid2 == 0; and nfft 1024 (5 cases, its own line)
  D  G4  negatives noise, tone, wifi_like, atsc_like, uplink_like, seeds 0-4
         (25 cases): verdict not lte; and noise: both chances >= 0.01 (5 cases)
  E  G5  control pss=False at 20 dB, seeds 0-4 (5 cases): verdict not lte;
         a 5 ms capture, seeds 0-2 (3 cases): verdict unknown, no exception, pci None
  G8     iqlte.py imports only numpy and the standard library (read with ast)
  interface (ungated): the result's shape; each ran check has passed ==
         (chance <= 1e-3), and the verdict agrees (lte: both, possible: one) on
         three built fingerprints; pci None; cp_autocorr and pss_search return
         dicts with score and chance; captures of 10 ms, of 1.92 MS/s and a 5 kHz
         CFO are not unknown or are lte; unknown captures give only allowed nid2
         and nfft values.
Time F is not tested here: the manager measures it.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iqlte.py
(a few minutes: about 90 synthesised captures, most of them 0.1 s at 15 to 20 MS/s)
"""

import ast
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_negatives  # noqa: E402

try:
    from fm_receiver import iqlte  # noqa: E402
    MISSING = None
except ImportError as exc:       # reported as failing checks, not a crash
    iqlte = None
    MISSING = exc

try:
    from tests import lte_synth  # noqa: E402
    SYNTH_MISSING = None
except ImportError as exc:
    lte_synth = None
    SYNTH_MISSING = exc

TOOLS = os.path.dirname(HERE)
CLASS_N = {1.4: 128, 3: 256, 5: 512, 10: 1024, 15: 1536, 20: 2048}
FP_KEYS = ('checks', 'verdict', 'pci', 'nid2', 'nfft', 'chance')
CHECK_KEYS = ('name', 'ran', 'passed', 'score', 'chance', 'detail')
VERDICTS = ('lte', 'possible', 'not-lte', 'unknown')
NEGATIVES = ['synth_noise', 'synth_tone', 'synth_wifi_like', 'synth_atsc_like',
             'synth_uplink_like']
RESULTS = []
MEMO = {}


def api():
    if iqlte is None:
        raise MISSING
    return iqlte


def synth(**kw):
    if lte_synth is None:
        raise SYNTH_MISSING
    return lte_synth.synth_lte(**kw)


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


def outcome(label, test):
    """One case of a counted group: ``(label, ok, why)``; an exception is a failed case."""
    try:
        ok, why = test()
    except Exception as exc:
        ok, why = False, f"{type(exc).__name__}: {exc}"
    return label, bool(ok), why


def counted(name, outcomes):
    """One ok/FAIL line for a group: every case must pass."""
    passes = sum(1 for _, ok, _ in outcomes if ok)
    bad = [f'{label}: {why}' for label, ok, why in outcomes if not ok]
    report(f'{name}: {passes} of {len(outcomes)} cases', passes == len(outcomes),
           '; '.join(bad[:6]))


def find_check(fp, key):
    """The one check whose name contains ``key`` (case-insensitive). Used for
    SSS and CRS only: the spec names those stages."""
    hits = [c for c in fp['checks'] if key in str(c.get('name', '')).lower()]
    if len(hits) != 1:
        names = [c.get('name') for c in fp['checks']]
        raise AssertionError(f'want one check named like {key!r}, got {names}')
    return hits[0]


def ran_checks(fp):
    """The CP autocorrelation and PSS checks, by name. The SSS check runs now too,
    so it is not one of these; both of these must have run."""
    checks = [find_check(fp, 'cp-autocorr'), find_check(fp, 'pss')]
    not_ran = [c.get('name') for c in checks if not bool(c.get('ran'))]
    if not_ran:
        raise AssertionError(f'want the cp and pss checks to have run, not: {not_ran}')
    return checks


def lte_fp(bw=10, pci=123, snr=20.0, seed=0, fs=None, cfo=0.0, pss=True):
    """The fingerprint of a 10-frame synthetic LTE capture, built and measured once."""
    key = ('lte', bw, pci, snr, seed, fs, cfo, pss)

    def make():
        iq, rate = synth(bw_mhz=bw, n_frames=10, pci=pci, snr_db=snr, seed=seed,
                         cfo_hz=cfo, fs=fs, pss=pss)
        return api().fingerprint(iq, rate)
    return memo(key, make)


def negative_fp(name, seed):
    """The fingerprint of a negative example (20 MS/s, 0.1 s), measured once."""
    def make():
        iq, rate = getattr(lte_negatives, name)(seed=seed)
        return api().fingerprint(iq, rate)
    return memo(('neg', name, seed), make)


def short_fp(seed):
    """The fingerprint of a 5 ms slice of a one-frame 10 MHz capture, measured once."""
    def make():
        iq, rate = synth(bw_mhz=10, n_frames=1, pci=123, snr_db=20.0, seed=seed)
        return api().fingerprint(iq[:int(round(0.005 * rate))], rate)
    return memo(('short', seed), make)


def cheap_lte():
    """A 1.4 MHz LTE capture (1.92 MS/s, 10 frames): the cheap input for the
    two check functions."""
    return memo('cheap-lte', lambda: synth(bw_mhz=1.4, n_frames=10, pci=123,
                                           snr_db=20.0, seed=0))


# ---- group A: G1 and G7 -------------------------------------------------------

def gate_a(pci, snr, seed):
    fp = lte_fp(pci=pci, snr=snr, seed=seed)
    ok = fp['verdict'] == 'lte' and fp['nid2'] == pci % 3 and fp['nfft'] == 1024
    return ok, (f"verdict {fp['verdict']}, nid2 {fp['nid2']} (want {pci % 3}), "
                f"nfft {fp['nfft']} (want 1024)")


def gate_g7(pci, seed):
    fp = lte_fp(pci=pci, snr=20.0, seed=seed)
    checks = ran_checks(fp)
    ok = all(c['chance'] <= 1e-6 for c in checks)
    return ok, '; '.join(f"{c['name']} chance {c['chance']:.3g}" for c in checks)


def group_a():
    outs = [outcome(f'pci {pci} {snr:g} dB seed {seed}',
                    lambda p=pci, s=snr, d=seed: gate_a(p, s, d))
            for pci in (0, 123, 503) for snr in (20.0, 0.0) for seed in range(5)]
    counted('A (G1) 10 MHz: verdict lte, nid2 = PCI mod 3, nfft 1024', outs)

    outs7 = [outcome(f'pci {pci} seed {seed}', lambda p=pci, d=seed: gate_g7(p, d))
             for pci in (0, 123, 503) for seed in range(5)]
    counted('A (G7) the 20 dB cases: both checks chance <= 1e-6', outs7)


# ---- group B: G2 --------------------------------------------------------------

def gate_b(bw, seed):
    fp = lte_fp(bw=bw, pci=123, snr=20.0, seed=seed)
    want = CLASS_N[bw]
    ok = fp['verdict'] == 'lte' and fp['nfft'] == want
    return ok, f"verdict {fp['verdict']}, nfft {fp['nfft']} (want {want})"


def group_b():
    outs = [outcome(f'{bw} MHz seed {seed}', lambda b=bw, d=seed: gate_b(b, d))
            for bw in (1.4, 3, 5, 10, 20) for seed in range(3)]
    counted('B (G2) bandwidth classes at 20 dB: verdict lte, nfft = class N', outs)


# ---- group C: G3 --------------------------------------------------------------

def gate_c(seed):
    fp = lte_fp(bw=10, pci=123, snr=10.0, seed=seed, fs=20e6, cfo=2000.0)
    ok = fp['verdict'] == 'lte' and fp['nid2'] == 0
    return ok, f"verdict {fp['verdict']}, nid2 {fp['nid2']} (want 0)"


def gate_c_nfft(seed):
    fp = lte_fp(bw=10, pci=123, snr=10.0, seed=seed, fs=20e6, cfo=2000.0)
    ok = fp['nfft'] == 1024
    return ok, f"nfft {fp['nfft']} (want 1024)"


def group_c():
    outs = [outcome(f'seed {seed}', lambda d=seed: gate_c(d)) for seed in range(5)]
    counted('C (G3) 10 MHz at 20 MS/s, CFO +2 kHz, 10 dB: verdict lte, nid2 0', outs)

    outs_n = [outcome(f'seed {seed}', lambda d=seed: gate_c_nfft(d)) for seed in range(5)]
    counted('C 10 MHz at 20 MS/s, CFO +2 kHz, 10 dB: nfft 1024', outs_n)


# ---- group D: G4 --------------------------------------------------------------

def gate_d(name, seed):
    fp = negative_fp(name, seed)
    return fp['verdict'] != 'lte', f"verdict {fp['verdict']}"


def gate_noise_chance(seed):
    fp = negative_fp('synth_noise', seed)
    checks = ran_checks(fp)
    ok = all(c['chance'] >= 0.01 for c in checks)
    return ok, '; '.join(f"{c['name']} chance {c['chance']:.3g}" for c in checks)


def group_d():
    outs = [outcome(f'{name} seed {seed}', lambda n=name, d=seed: gate_d(n, d))
            for name in NEGATIVES for seed in range(5)]
    counted('D (G4) negatives: verdict not lte', outs)

    outs_n = [outcome(f'noise seed {seed}', lambda d=seed: gate_noise_chance(d))
              for seed in range(5)]
    counted('D (G4) noise: both checks chance >= 0.01', outs_n)


# ---- group E: G5 --------------------------------------------------------------

def gate_control(seed):
    fp = lte_fp(pci=123, snr=20.0, seed=seed, pss=False)
    return fp['verdict'] != 'lte', f"verdict {fp['verdict']}"


def gate_short(seed):
    fp = short_fp(seed)
    ok = fp['verdict'] == 'unknown' and fp['pci'] is None
    return ok, f"verdict {fp['verdict']}, pci {fp['pci']!r}"


def group_e():
    outs = [outcome(f'seed {seed}', lambda d=seed: gate_control(d)) for seed in range(5)]
    counted('E (G5) control pss=False at 20 dB: verdict not lte', outs)

    outs5 = [outcome(f'seed {seed}', lambda d=seed: gate_short(d)) for seed in range(3)]
    counted('E (G5) 5 ms capture: verdict unknown, no exception, pci None', outs5)


# ---- G8: the imports ----------------------------------------------------------

def check_g8():
    path = os.path.join(TOOLS, 'fm_receiver', 'iqlte.py')
    if not os.path.isfile(path):
        report('G8 iqlte.py imports only numpy and the standard library', False,
               'fm_receiver/iqlte.py is missing')
        return
    with open(path) as fh:
        tree = ast.parse(fh.read(), path)
    stdlib = set(sys.stdlib_module_names)
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                bad.add('a relative import')
                continue
            names = [node.module or '']
        else:
            continue
        for name in names:
            top = name.split('.')[0]
            if top != 'numpy' and top not in stdlib:
                bad.add(name)
    report('G8 iqlte.py imports only numpy and the standard library', not bad,
           f'imports {sorted(bad)}')


# ---- the interface (not gated, but the shape every later stage relies on) ----

def check_interface():
    def fp():
        return lte_fp(pci=123, snr=20.0, seed=0)

    def keys():
        missing = [k for k in FP_KEYS if k not in fp()]
        return not missing, f'missing {missing}'
    expect('fingerprint returns checks, verdict, pci, nid2, nfft, chance', keys)

    def check_keys():
        bad = [c.get('name') for c in fp()['checks'] if any(k not in c for k in CHECK_KEYS)]
        return not bad, f'checks without all of name, ran, passed, score, chance, detail: {bad}'
    expect('every check has name, ran, passed, score, chance, detail', check_keys)

    def verdict():
        v = fp()['verdict']
        return v in VERDICTS, f'verdict {v!r}'
    expect('verdict is lte, possible, not-lte or unknown', verdict)

    expect('pci is 123 for the 10 MHz 20 dB capture (the SSS pci)',
           lambda: (fp()['pci'] == 123, f"pci {fp()['pci']!r}"))

    def later_stages():
        sss, crs = find_check(fp(), 'sss'), find_check(fp(), 'crs')
        return (bool(sss['ran']) and bool(crs['ran']) and bool(crs['passed']),
                f"sss ran {sss['ran']!r}, crs ran {crs['ran']!r} passed {crs['passed']!r}")
    expect('SSS check ran True, and the CRS check ran and passed on the SSS PCI', later_stages)

    def two_ran():
        ran = sorted(c['name'] for c in fp()['checks'] if bool(c.get('ran')))
        return ran == ['cp-autocorr', 'crs', 'duplex', 'pss', 'sss'], f'checks that ran: {ran}'
    expect('cp-autocorr, pss, sss, crs and duplex ran', two_ran)

    def chances():
        bad = []
        for c in ran_checks(fp()):
            try:
                ok = 1e-300 <= float(c['chance']) <= 1.0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                bad.append(f"{c['name']} chance {c['chance']!r}")
        return not bad, f'want a probability floored at 1e-300: {bad}'
    expect('the chances of the checks that ran are probabilities in [1e-300, 1]', chances)

    def consistent(make, label):
        def test():
            fp_ = make()
            ran = ran_checks(fp_)
            bad = [c['name'] for c in ran
                   if bool(c['passed']) != (float(c['chance']) <= 1e-3)]
            n = sum(1 for c in ran if bool(c['passed']))
            v = fp_['verdict']
            if n == 2:
                verdict_ok = v == 'lte'
            elif n == 1:
                verdict_ok = v == 'possible'
            else:
                verdict_ok = v not in ('lte', 'possible')
            return (not bad and verdict_ok,
                    f'passed != (chance <= 1e-3) for {bad}; {n} passed, verdict {v!r}')
        expect(f'consistency on {label}: passed == (chance <= 1e-3), verdict agrees', test)
    consistent(fp, 'a 20 dB 10 MHz capture')
    consistent(lambda: lte_fp(pci=123, snr=20.0, seed=0, pss=False), 'the pss=False control')
    consistent(lambda: negative_fp('synth_noise', 0), 'one noise capture')

    def nid2_nfft():
        r = fp()
        ok = (r['nid2'] in (0, 1, 2)) and r['nfft'] == 1024
        return ok, f"nid2 {r['nid2']!r}, nfft {r['nfft']!r}"
    expect('10 MHz: nid2 is a PSS root (0-2), nfft 1024', nid2_nfft)

    expect('a pci None on one negative (noise seed 0)',
           lambda: (negative_fp('synth_noise', 0)['pci'] is None,
                    f"pci {negative_fp('synth_noise', 0)['pci']!r}"))

    def returns_dict(name):
        def test():
            iq, rate = cheap_lte()
            out = getattr(api(), name)(iq, rate)
            ok = isinstance(out, dict) and 'score' in out and 'chance' in out
            return ok, f'got {type(out).__name__} with keys {sorted(out) if isinstance(out, dict) else out!r}'
        return test
    expect('cp_autocorr returns a dict with score and chance', returns_dict('cp_autocorr'))
    expect('pss_search returns a dict with score and chance', returns_dict('pss_search'))

    def one_frame_10ms():
        iq, rate = memo('one-frame', lambda: synth(bw_mhz=10, n_frames=1, pci=123,
                                                   snr_db=20.0, seed=0))
        v = api().fingerprint(iq, rate)['verdict']
        return v != 'unknown', f'a 10 ms capture gave verdict {v!r}'
    expect('a capture of exactly 10 ms is not unknown (no exception)', one_frame_10ms)

    def rate_1_92():
        iq, rate = cheap_lte()
        v = api().fingerprint(iq, rate)['verdict']
        return v != 'unknown', f'a 1.92 MS/s capture gave verdict {v!r}'
    expect('a 1.92 MS/s capture is not unknown (no exception)', rate_1_92)

    def cfo_5k():
        v = lte_fp(bw=10, pci=123, snr=20.0, seed=0, cfo=5000.0)['verdict']
        return v == 'lte', f'verdict {v!r} at a 5 kHz CFO'
    expect('a 5 kHz CFO at 10 MHz, 20 dB, PCI 123: verdict lte', cfo_5k)

    def unknown_values(make, label):
        def test():
            r = make()
            ok = (r['nid2'] in (None, 0, 1, 2)
                  and (r['nfft'] is None or r['nfft'] in CLASS_N.values()))
            return ok, f"nid2 {r['nid2']!r}, nfft {r['nfft']!r}"
        expect(f'{label}: nid2 and nfft are allowed values', test)
    unknown_values(lambda: short_fp(0), 'the 5 ms capture')
    unknown_values(lambda: api().fingerprint(*lte_negatives.synth_noise(seed=0, fs=1e6)),
                   'the 1 MS/s capture')

    def slow_rate():
        iq, rate = lte_negatives.synth_noise(seed=0, fs=1e6)
        out = api().fingerprint(iq, rate)
        return out['verdict'] == 'unknown', f"verdict {out['verdict']!r} at 1 MS/s"
    expect('a 1 MS/s capture (under 1.92 MS/s) is unknown, with no exception', slow_rate)


GROUPS = [group_a, group_b, group_c, group_d, group_e, check_g8, check_interface]


def main():
    for group in GROUPS:
        try:
            group()
        except Exception as exc:
            report(f'{group.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    failed = [name for name, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    if failed:
        print('failed:')
        for name in failed:
            print(f'  {name}')
        return 1
    print('all checks passed')
    return 0


# ---- phase l2: the SSS check and the physical cell ID (spec l2) ---------------
#
# Each capture is built once: l2_case keeps the sss_search and fingerprint
# results of one synthetic capture, so A's 20 dB cases also serve group F.
#
# Frame convention for the detector's ``frame_offset_samples``: the sample, in
# the capture at the capture's own rate, where the first 10 ms frame starts (the
# first sample of slot 0, symbol 0, prefix included), taken modulo 10 ms.
# ``half_frame`` is 0 when the 5 ms half-frame starting there carries subframe
# 0's SSS, and 1 when the SSS 5 ms later (subframe 5) is the one that starts it.
# The generator draws its frame offset first from default_rng(seed) and rolls the
# frame by it (lte_synth.synth_lte), so the first frame start in the capture is
# that sample, with half_frame 0. A detector may instead report the 5 ms boundary
# it chose with half_frame 1 (subframe 5's SSS starts there): the frame start is
# then that offset minus 5 ms, modulo 10 ms. Both labels are accepted (group F).
# Only native-rate captures are compared here.

L2_PCIS = (0, 1, 2, 3, 123, 250, 251, 500, 503)
L2_SNRS = (20.0, 0.0, -5.0)
L2_SEEDS = (0, 1, 2)
L2_KEYS = ('ran', 'nid1', 'nid2', 'pci', 'half_frame', 'frame_offset_samples',
           'score', 'chance', 'halfframes_used', 'detail')


def l2_case(bw=10, pci=123, snr=20.0, seed=0, fs=None, cfo=0.0, pss=True, sss=True,
            n_frames=10):
    """One synthetic capture, built once: its rate and its sss_search and fingerprint
    results. ``sss=False`` is the generator's SSS-zeroed control, passed only then."""
    key = ('l2', bw, pci, snr, seed, fs, cfo, pss, sss, n_frames)

    def make():
        kw = dict(bw_mhz=bw, n_frames=n_frames, pci=pci, snr_db=snr, seed=seed,
                  cfo_hz=cfo, fs=fs, pss=pss)
        if not sss:
            kw['sss'] = False
        iq, rate = synth(**kw)
        return {'rate': rate, 'sss': api().sss_search(iq, rate),
                'fp': api().fingerprint(iq, rate)}
    return memo(key, make)


def l2_negative(name, seed):
    """sss_search and fingerprint of a negative example (20 MS/s, 0.1 s), built once."""
    def make():
        iq, rate = getattr(lte_negatives, name)(seed=seed)
        return {'sss': api().sss_search(iq, rate), 'fp': api().fingerprint(iq, rate)}
    return memo(('l2-neg', name, seed), make)


def l2_short(seed):
    """A 15 ms slice of a two-frame 10 MHz capture: shorter than the 20 ms the SSS needs."""
    def make():
        iq, rate = synth(bw_mhz=10, n_frames=2, pci=123, snr_db=20.0, seed=seed)
        cut = iq[:int(round(0.015 * rate))]
        return {'sss': api().sss_search(cut, rate), 'fp': api().fingerprint(cut, rate)}
    return memo(('l2-short', seed), make)


def true_offset(bw, seed):
    """Where the generator's first frame starts in its capture at the native rate: its
    first seeded draw (lte_synth.synth_lte), the np.roll that puts frame sample 0 there."""
    import numpy as np
    n_fft, _native, _n_sc = lte_synth._class(bw)
    return int(np.random.default_rng(seed).integers(0, lte_synth._frame_samples(n_fft)))


def frame_gap(got, want, frame):
    """Distance in samples between two frame starts, modulo one 10 ms frame."""
    d = (int(got) - int(want)) % frame
    return min(d, frame - d)


def l2_pci_check(bw, pci, snr, seed, fs=None, cfo=0.0):
    case = l2_case(bw=bw, pci=pci, snr=snr, seed=seed, fs=fs, cfo=cfo)
    out, fp_pci = case['sss'], case['fp']['pci']
    ok = out['pci'] == pci and fp_pci == pci
    return ok, (f"sss pci {out['pci']!r}, fingerprint pci {fp_pci!r} (want {pci}), "
                f"chance {out['chance']:.3g}")


def l2_rule_check(pci, snr, seed):
    """The universal rule: the pci is None, or its chance is at most 1e-3."""
    out = l2_case(pci=pci, snr=snr, seed=seed)['sss']
    ok = out['pci'] is None or float(out['chance']) <= 1e-3
    return ok, f"pci {out['pci']!r} at chance {out['chance']!r}"


def l2_wrong_confident(pci, snr, seed):
    out = l2_case(pci=pci, snr=snr, seed=seed)['sss']
    bad = out['pci'] is not None and out['pci'] != pci and out['chance'] <= 1e-3
    return not bad, f"pci {out['pci']!r} (want {pci}) at chance {out['chance']:.3g}"


def l2_frame_check(pci, seed):
    case = l2_case(pci=pci, snr=20.0, seed=seed)
    out = case['sss']
    frame = int(round(0.010 * case['rate']))
    half = frame // 2
    want = true_offset(10, seed)
    hf = out['half_frame']
    # Frame start implied by the label: half_frame 1 means the offset is 5 ms past it.
    start = (int(out['frame_offset_samples']) - int(hf) * half) if hf in (0, 1) else None
    gap = frame_gap(start, want, frame) if start is not None else None
    ok = gap is not None and gap <= 2
    return ok, (f"half_frame {hf!r}, frame offset {out['frame_offset_samples']!r}, "
                f"frame start {start!r} (want {want}, gap {gap} of {frame})")


def l2_negative_check(name, seed):
    case = l2_negative(name, seed)
    out = case['sss']
    ok = out['pci'] is None and case['fp']['pci'] is None
    return ok, (f"sss pci {out['pci']!r}, fingerprint pci {case['fp']['pci']!r}, "
                f"chance {out['chance']:.3g}")


def l2_control(seed):
    case = l2_case(bw=10, pci=123, snr=20.0, seed=seed, sss=False)
    pss_ok = bool(find_check(case['fp'], 'pss')['passed'])
    ok = case['sss']['pci'] is None and case['fp']['pci'] is None and pss_ok
    return ok, (f"sss pci {case['sss']['pci']!r}, fingerprint pci {case['fp']['pci']!r}, "
                f"pss check passed {pss_ok}")


# ---- groups A to G of phase l2 ------------------------------------------------

def group_l2_a():
    cases = [(pci, snr, seed) for pci in L2_PCIS for snr in L2_SNRS for seed in L2_SEEDS]
    high = [c for c in cases if c[1] > -5.0]
    low = [c for c in cases if c[1] == -5.0]
    outs = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l2_pci_check(10, p, s, d))
            for p, s, d in high]
    counted('L2 A (G1) 10 MHz at 20 and 0 dB: sss pci exact', outs)

    low_outs = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l2_pci_check(10, p, s, d))
                for p, s, d in low]
    n_low = sum(1 for _, ok, _ in low_outs if ok)
    bad_low = [f'{label}: {why}' for label, ok, why in low_outs if not ok]
    report(f'L2 A (G1) 10 MHz at -5 dB: sss pci exact in {n_low} of {len(low_outs)} (gate 24)',
           n_low >= 24, '; '.join(bad_low[:6]))

    wrong = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l2_wrong_confident(p, s, d))
             for p, s, d in cases]
    counted('L2 A (G1) no wrong sss pci at chance <= 1e-3 (all 81)', wrong)

    rule = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l2_rule_check(p, s, d))
            for p, s, d in cases]
    counted('L2 A pci None or chance <= 1e-3 at every SNR (all 81)', rule)


def group_l2_b():
    outs = [outcome(f'{bw} MHz seed {d}', lambda b=bw, d=d: l2_pci_check(b, 301, 20.0, d))
            for bw in (1.4, 3, 5, 15, 20) for d in (0, 1)]
    counted('L2 B (G2) bandwidths 1.4, 3, 5, 15, 20 MHz at 20 dB, PCI 301: sss pci exact', outs)


def group_l2_c():
    outs = [outcome(f'seed {d}', lambda d=d: l2_pci_check(10, 307, 10.0, d, fs=20e6, cfo=2000.0))
            for d in range(5)]
    counted('L2 C (G3) 10 MHz at 20 MS/s, CFO +2 kHz, 10 dB, PCI 307: sss pci exact', outs)


def group_l2_d():
    outs = [outcome(f'{name} seed {d}', lambda n=name, d=d: l2_negative_check(n, d))
            for name in NEGATIVES for d in range(5)]
    counted('L2 D (G4) negatives: sss pci None', outs)


def group_l2_e():
    outs = [outcome(f'seed {d}', lambda d=d: l2_control(d)) for d in range(5)]
    counted('L2 E (G5) control sss=False at 20 dB: sss pci None, pss check passes', outs)

    def short_ok(d):
        case = l2_short(d)
        ok = (isinstance(case['sss'], dict) and isinstance(case['fp'], dict)
              and case['sss']['pci'] is None)
        return ok, f"sss pci {case['sss'].get('pci')!r}"
    outs_s = [outcome(f'seed {d}', lambda d=d: short_ok(d)) for d in range(3)]
    counted('L2 E (G5) 15 ms capture: no exception, sss pci None', outs_s)


def group_l2_f():
    outs = [outcome(f'pci {p} seed {d}', lambda p=p, d=d: l2_frame_check(p, d))
            for p in L2_PCIS for d in L2_SEEDS]
    counted('L2 F (G6) 10 MHz at 20 dB: half_frame 0 and frame offset within 2 samples', outs)


def group_l2_g():
    """G7: fingerprint on 0.1 s at 15.36 MS/s, timed alone (the synthesis is outside the timer)."""
    import time
    iq, rate = synth(bw_mhz=10, n_frames=10, pci=123, snr_db=20.0, seed=0)
    t0 = time.perf_counter()
    api().fingerprint(iq, rate)
    dt = time.perf_counter() - t0
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        load = 0.0
    cores = os.cpu_count() or 1
    report(f'L2 G (G7) fingerprint on 0.1 s at 15.36 MS/s: {dt:.2f} s (gate under 5 s; '
           f'load {load:.2f} on {cores} cores)', dt < 5.0, f'{dt:.2f} s')


def check_l2_interface():
    try:
        case = l2_case(bw=10, pci=123, snr=20.0, seed=0)
    except Exception as exc:
        why = f'{type(exc).__name__}: {exc}'
        expect('the 10 MHz 20 dB capture builds (sss_search and fingerprint run)',
               lambda: (False, why))
        return
    out, fp = case['sss'], case['fp']

    def keys():
        missing = [k for k in L2_KEYS if k not in out]
        return not missing, f'missing {missing}'
    expect('sss_search returns ran, nid1, nid2, pci, half_frame, frame_offset_samples, '
           'score, chance, halfframes_used, detail', keys)

    expect('sss_search gives a pci on the 10 MHz 20 dB capture',
           lambda: (bool(out['ran']) and out['pci'] is not None,
                    f"ran {out['ran']!r}, pci {out['pci']!r}"))

    def formula():
        ok = out['pci'] == 3 * out['nid1'] + out['nid2'] and out['nid2'] in (0, 1, 2)
        return ok, f"pci {out['pci']!r}, nid1 {out['nid1']!r}, nid2 {out['nid2']!r}"
    expect('pci = 3 * nid1 + nid2, with nid2 in 0-2', formula)

    def chance_range():
        c = float(out['chance'])
        return 1e-300 <= c <= 1.0, f'chance {c!r}'
    expect('sss chance is a probability in [1e-300, 1]', chance_range)

    expect('half_frame is 0 or 1', lambda: (out['half_frame'] in (0, 1),
                                            f"half_frame {out['half_frame']!r}"))

    expect('halfframes_used is at least 1',
           lambda: (int(out['halfframes_used']) >= 1, f"halfframes_used {out['halfframes_used']!r}"))

    def sss_check_ran():
        c = find_check(fp, 'sss')
        return c['ran'] is True, f"sss check ran {c['ran']!r}"
    expect('fingerprint: the sss check ran', sss_check_ran)

    expect('fingerprint pci equals the sss pci (None when the sss gives none)',
           lambda: (fp['pci'] == out['pci'], f"fingerprint pci {fp['pci']!r}, sss pci {out['pci']!r}"))

    def nid2_rule():
        pss_root = fp['nid2']
        mismatch = pss_root is not None and out['pci'] is not None and out['pci'] % 3 != pss_root
        want = 'possible' if mismatch else 'lte'
        return fp['verdict'] == want, (f"verdict {fp['verdict']!r}, want {want!r} (sss nid2 "
                                       f"{out['nid2']!r}, pss root {pss_root!r})")
    expect('verdict stays lte; it drops to possible only when the sss nid2 differs from the PSS root',
           nid2_rule)

    def under_rate():
        iq, rate = lte_negatives.synth_noise(seed=0, fs=1e6)
        a = api().sss_search(iq, rate)
        b = api().fingerprint(iq, rate)
        ok = a['pci'] is None and b['verdict'] == 'unknown'
        return ok, f"sss pci {a['pci']!r}, fingerprint verdict {b['verdict']!r}"
    expect('a 1 MS/s capture: sss pci None and fingerprint verdict unknown', under_rate)

    def no_pss():
        c = l2_case(bw=10, pci=123, snr=20.0, seed=0, pss=False)
        ok = c['sss']['pci'] is None and c['fp']['pci'] is None
        return ok, f"sss pci {c['sss']['pci']!r}, fingerprint pci {c['fp']['pci']!r}"
    expect('pss=False control at 20 dB: sss pci None and fingerprint pci None', no_pss)

    def mismatch():
        """A fake sss_search whose N_ID(2) differs from the PSS root of a real capture:
        the verdict must drop to possible, and a check's detail must say so."""
        real_root = fp['nid2']
        other = (real_root + 1) % 3
        fake = {'ran': True, 'nid1': 41, 'nid2': other, 'pci': 3 * 41 + other,
                'half_frame': 0, 'frame_offset_samples': 0, 'score': 1.0, 'chance': 1e-9,
                'halfframes_used': 20, 'detail': 'fake'}
        mod = api()
        had = hasattr(mod, 'sss_search')
        orig = getattr(mod, 'sss_search', None)
        iq, rate = memo('l2-mismatch-iq', lambda: synth(bw_mhz=10, n_frames=10, pci=123,
                                                        snr_db=20.0, seed=0))
        mod.sss_search = lambda iq_, fs_: fake
        try:
            res = mod.fingerprint(iq, rate)
        finally:
            if had:
                mod.sss_search = orig
            else:
                del mod.sss_search
        mentions = any(s in str(c.get('detail', '')).lower()
                       for c in res['checks'] for s in ('nid2', 'mismatch'))
        ok = res['verdict'] == 'possible' and mentions
        return ok, f"verdict {res['verdict']!r}, nid2/mismatch in a detail: {mentions}"
    expect('sss nid2 differing from the PSS root: verdict possible, detail says so', mismatch)


GROUPS.extend([group_l2_a, group_l2_b, group_l2_c, group_l2_d, group_l2_e, group_l2_f,
               group_l2_g, check_l2_interface])


# ---- phase l4: several LTE cells on one carrier (spec l4) ---------------------
#
# The spec's targets A to F and gates G1 to G5. G6 (time) is measured by the
# manager: group F prints the time and gates nothing. Each capture is synthesised,
# run through cells, and only its cell list is kept (the IQ is dropped), so the
# groups share results without holding the captures.
#
# Cases. A pair's first PCI is the strong cell (power 0 dB, delay 0); its second is
# the weak cell, at the power the case names. The weak cell's delay is 0 in "sync".
# In "async" it is drawn as default_rng([seed, strong PCI, weak PCI]).integers(40000,
# 150001), so each case always gets the same delay. Timing: a cell's frame start is
# frame_offset_samples less half_frame * 5 ms, modulo one 10 ms frame (the l2
# convention; both half_frame labels are accepted). The weak start must be the strong
# start plus its delay, modulo the frame, within L4_TOL samples; for sync (delay 0)
# the two starts agree within L4_TOL.

L4_PAIRS = ((61, 288), (0, 1), (12, 300), (3, 4), (410, 61), (500, 7))
L4_TRIPLE = (90, 91, 92)
L4_TOL = 4                    # samples
L4_CELL_KEYS = ('pci', 'nid1', 'nid2', 'frame_offset_samples', 'half_frame',
                'relative_power_db', 'score', 'chance', 'detail')


def synth_multi(**kw):
    """lte_synth.synth_lte_multi, returning (iq, fs) as synth does."""
    if lte_synth is None:
        raise SYNTH_MISSING
    return lte_synth.synth_lte_multi(**kw)


def l4_delay(pair, seed, mode):
    """The weak cell's delay in samples: 0 when sync, else a draw seeded by the case."""
    if mode == 'sync':
        return 0
    import numpy as np
    rng = np.random.default_rng([seed, pair[0], pair[1]])
    return int(rng.integers(40000, 150001))


def _multi_key(spec, seed, snr=20.0):
    return ('l4', tuple(spec), seed, snr)


def _l4_multi(spec, seed, snr):
    """The cells of one 10-frame, 10 MHz capture (see multi_cells). Runs in a worker."""
    cells_in = [dict(pci=int(p), power_db=float(w), delay_samples=int(d))
                for p, w, d in spec]
    iq, rate = synth_multi(cells=cells_in, bw_mhz=10, n_frames=10, snr_db=snr, seed=seed)
    return {'rate': float(rate), 'cells': api().cells(iq, rate)}


def multi_cells(spec, seed, snr=20.0):
    """The cells of one 10-frame, 10 MHz capture. ``spec`` is a list of
    (pci, power_db, delay_samples); the result is {'rate', 'cells'}, measured once."""
    return memo(_multi_key(spec, seed, snr), lambda: _l4_multi(spec, seed, snr))


def pair_spec(pair, power_db, mode, seed):
    """(spec, delay) of a two-cell case: the strong cell first, the weak one at power_db."""
    delay = l4_delay(pair, seed, mode)
    return [(pair[0], 0.0, 0), (pair[1], float(power_db), delay)], delay


def pair_run(pair, power_db, mode, seed):
    """(result, delay) of a two-cell case: the strong cell first, the weak one at power_db."""
    spec, delay = pair_spec(pair, power_db, mode, seed)
    return multi_cells(spec, seed), delay


def reported(res):
    return {c['pci'] for c in res['cells']}


def cell_start(cell, frame):
    """The cell's frame start modulo one frame, or None when half_frame is not 0 or 1."""
    hf = int(cell['half_frame'])
    if hf not in (0, 1):
        return None
    return (int(cell['frame_offset_samples']) - hf * (frame // 2)) % frame


def timing_gap(res, pair, delay):
    """Samples by which the weak cell's frame start misses the strong start plus its
    delay, modulo one 10 ms frame (0 when exact)."""
    frame = int(round(0.010 * res['rate']))
    by = {c['pci']: c for c in res['cells']}
    strong = cell_start(by[pair[0]], frame)
    weak = cell_start(by[pair[1]], frame)
    if strong is None or weak is None:
        raise AssertionError('a reported cell has half_frame other than 0 or 1')
    return frame_gap(weak, strong + delay, frame)


def a_label(case):
    pair, power_db, mode, seed = case
    return f'{pair[0]}+{pair[1]} {mode} {power_db:g} dB seed {seed}'


def l4_a_case(pair, power_db, mode, seed):
    res, _ = pair_run(pair, power_db, mode, seed)
    got = reported(res)
    return set(pair) <= got, f'reported {sorted(got)}'


def l4_both(case):
    """True when both PCIs of the case are reported (a case that raises is not)."""
    try:
        res, _ = pair_run(*case)
    except Exception:
        return False
    return set(case[0]) <= reported(res)


def _run_job(fn, args):
    """Worker side of l4_prefill: what memo would store for fn(*args)."""
    try:
        return True, fn(*args)
    except Exception as exc:
        return False, exc


def l4_prefill():
    """Build every L4 capture of groups A to E in a process pool and store each result in
    MEMO as memo would. A result depends only on its arguments, so the groups read the same
    values as a serial run; a key already in MEMO is not built again. Group F is timed
    after this returns, when no pool is running."""
    jobs = []
    for pci in L2_PCIS:                                       # group D first: the costliest
        for snr in (20.0, 0.0):
            for seed in range(3):
                jobs.append((_single_key(pci, snr, seed), _l4_single, (pci, snr, seed)))
    for pair in L4_PAIRS:                                     # group A
        for power_db in (0.0, -6.0):
            for mode in ('sync', 'async'):
                for seed in (0, 1):
                    spec, _ = pair_spec(pair, power_db, mode, seed)
                    jobs.append((_multi_key(spec, seed), _l4_multi, (spec, seed, 20.0)))
    for pair in L4_PAIRS:                                     # group B
        for mode in ('async', 'sync'):
            spec, _ = pair_spec(pair, -10.0, mode, 0)
            jobs.append((_multi_key(spec, 0), _l4_multi, (spec, 0, 20.0)))
    for seed in (0, 1, 2):                                    # group C
        spec = [(90, 0.0, 0), (91, -3.0, 0), (92, -6.0, 0)]
        jobs.append((_multi_key(spec, seed), _l4_multi, (spec, seed, 20.0)))
    for name in NEGATIVES:                                    # group E
        for seed in range(5):
            jobs.append((_neg_key(name, seed), _l4_neg, (name, seed)))
    for which in ('5ms', '1msps'):
        jobs.append((_short_key(which), _l4_short, (which,)))

    todo, seen = [], set()
    for key, fn, args in jobs:
        if key not in MEMO and key not in seen:
            seen.add(key)
            todo.append((key, fn, args))
    if not todo:
        return
    # One BLAS/FFT thread per worker: each worker otherwise starts a thread pool of its own
    # and 8 of them oversubscribe the cores. The parent has numpy loaded already, so a forked
    # worker would keep that pool; spawn starts each worker fresh, reading these variables
    # before it imports numpy. The parent's values are put back when the pool is done.
    thread_vars = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')
    saved = {var: os.environ.get(var) for var in thread_vars}
    for var in thread_vars:
        os.environ[var] = '1'
    try:
        ctx = multiprocessing.get_context('spawn')
        with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1),
                                 mp_context=ctx) as pool:
            futures = [(key, pool.submit(_run_job, fn, args)) for key, fn, args in todo]
            for key, fut in futures:
                try:
                    MEMO[key] = fut.result()
                except Exception as exc:    # the result could not come back from the worker
                    MEMO[key] = (False, RuntimeError(f'{type(exc).__name__}: {exc}'))
    finally:
        for var, old in saved.items():
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old


def group_l4_a():
    l4_prefill()
    cases = [(pair, p, m, s) for pair in L4_PAIRS for p in (0.0, -6.0)
             for m in ('sync', 'async') for s in (0, 1)]
    outs = [outcome(a_label(c), lambda c=c: l4_a_case(*c)) for c in cases]
    n = sum(1 for _, ok, _ in outs if ok)
    bad = [f'{label}: {why}' for label, ok, why in outs if not ok]
    report(f'L4 A (G1) two cells, 10 MHz: both PCIs reported in {n} of {len(outs)} cases (gate 44)',
           n >= 44, '; '.join(bad[:6]))

    # The timing gap is printed, not gated: G1 gates the both-PCI count only (l4.md:68).
    timed = [c for c in cases if l4_both(c)]
    gaps = []
    for c in timed:
        try:
            res, delay = pair_run(*c)
            gaps.append(timing_gap(res, c[0], delay))
        except Exception:
            pass
    worst = max(gaps) if gaps else None
    report(f'L4 A timing (printed, not gated): {len(timed)} cases with both reported, '
           f'{len(timed) - len(gaps)} without a timing; worst frame-start gap {worst} samples '
           f'(L4_TOL {L4_TOL})', True)


# ---- group B: G2 --------------------------------------------------------------

def l4_b_strong(pair, mode):
    res, _ = pair_run(pair, -10.0, mode, 0)
    got = reported(res)
    return pair[0] in got, f'reported {sorted(got)}'


def l4_b_wrong(pair, mode):
    res, _ = pair_run(pair, -10.0, mode, 0)
    got = reported(res)
    return got <= set(pair), f'reported {sorted(got)}, pair {pair}'


def group_l4_b():
    cases = [(pair, m) for pair in L4_PAIRS for m in ('async', 'sync')]
    labels = [f'{pair[0]}+{pair[1]} {m} -10 dB seed 0' for pair, m in cases]
    strong = [outcome(lab, lambda p=pair, m=m: l4_b_strong(p, m))
              for lab, (pair, m) in zip(labels, cases)]
    wrong = [outcome(lab, lambda p=pair, m=m: l4_b_wrong(p, m))
             for lab, (pair, m) in zip(labels, cases)]
    counted('L4 B (G2) weak cell 10 dB under: the strong PCI reported', strong)
    counted('L4 B (G2) weak cell 10 dB under: no reported PCI outside the pair', wrong)


# ---- group C: G3 --------------------------------------------------------------

def group_l4_c():
    spec = [(90, 0.0, 0), (91, -3.0, 0), (92, -6.0, 0)]

    def all_three(seed):
        got = reported(multi_cells(spec, seed))
        return set(L4_TRIPLE) <= got, f'reported {sorted(got)}'
    outs = [outcome(f'seed {seed}', lambda s=seed: all_three(s)) for seed in (0, 1, 2)]
    n = sum(1 for _, ok, _ in outs if ok)
    bad = [f'{label}: {why}' for label, ok, why in outs if not ok]
    report(f'L4 C (G3) three sectors 90, 91, 92 at 0, -3, -6 dB, sync: all three reported in '
           f'{n} of {len(outs)} cases (gate 2)', n >= 2, '; '.join(bad[:6]))


# ---- group D: G4 --------------------------------------------------------------

def _single_key(pci, snr, seed):
    return ('l4-single', pci, snr, seed)


def _l4_single(pci, snr, seed):
    """cells, the fingerprint's pci and the sss pci of one one-cell 10 MHz capture (see
    l4_single). Runs in a worker. fingerprint() already runs sss_search and cells() on the
    capture and returns both, so the signal is searched once: its 'cells' is cells(iq, rate)
    and its 'pci' is the SSS pci that sss_search gives."""
    iq, rate = synth(bw_mhz=10, n_frames=10, pci=pci, snr_db=snr, seed=seed)
    fp = api().fingerprint(iq, rate)
    return {'cells': fp['cells'], 'fp_pci': fp['pci'], 'sss_pci': fp['pci']}


def l4_single(pci, snr, seed):
    """cells, the fingerprint's pci and the sss_search pci of one one-cell 10 MHz capture,
    measured once (the IQ is dropped). The 20 dB ones are reused by the interface checks."""
    return memo(_single_key(pci, snr, seed), lambda: _l4_single(pci, snr, seed))


def l4_d_cells(pci, snr, seed):
    cells = l4_single(pci, snr, seed)['cells']
    ok = len(cells) == 1 and cells[0]['pci'] == pci
    return ok, f"cells pcis {[c['pci'] for c in cells]} (want exactly [{pci}])"


def l4_d_fp(pci, snr, seed):
    r = l4_single(pci, snr, seed)
    return r['fp_pci'] == pci, f"fingerprint pci {r['fp_pci']!r} (want {pci})"


def group_l4_d():
    cases = [(pci, snr, seed) for pci in L2_PCIS for snr in (20.0, 0.0) for seed in range(3)]
    one = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l4_d_cells(p, s, d))
           for p, s, d in cases]
    n = sum(1 for _, ok, _ in one if ok)
    bad = [f'{label}: {why}' for label, ok, why in one if not ok]
    report(f'L4 D (G4) one-cell regression: cells is exactly one entry, the right PCI, in '
           f'{n} of {len(one)} cases (gate 52)', n >= 52, '; '.join(bad[:6]))

    fp = [outcome(f'pci {p} {s:g} dB seed {d}', lambda p=p, s=s, d=d: l4_d_fp(p, s, d))
          for p, s, d in cases]
    counted('L4 D (G4) one-cell regression: fingerprint pci unchanged (equal to the cell PCI)', fp)


# ---- group E: G5 --------------------------------------------------------------

def _neg_key(name, seed):
    return ('l4-neg', name, seed)


def _l4_neg(name, seed):
    iq, rate = getattr(lte_negatives, name)(seed=seed)
    return api().cells(iq, rate)


def l4_neg(name, seed):
    """cells of one negative example (20 MS/s, 0.1 s), measured once."""
    return memo(_neg_key(name, seed), lambda: _l4_neg(name, seed))


def l4_neg_check(name, seed):
    cells = l4_neg(name, seed)
    return len(cells) == 0, f'cells {[c["pci"] for c in cells]}'


def _short_key(which):
    return ('l4-short', which)


def _l4_short(which):
    if which == '5ms':
        spec = [dict(pci=L4_PAIR_SHORT[0], power_db=0.0, delay_samples=0),
                dict(pci=L4_PAIR_SHORT[1], power_db=-6.0, delay_samples=0)]
        iq, rate = synth_multi(cells=spec, bw_mhz=10, n_frames=1, snr_db=20.0, seed=0)
        return api().cells(iq[:int(round(0.005 * rate))], rate)
    iq, rate = lte_negatives.synth_noise(seed=0, fs=1e6)
    return api().cells(iq, rate)


def l4_short_check(which):
    """cells of a 5 ms slice of a two-cell capture, or of a 1 MS/s noise capture."""
    return memo(_short_key(which), lambda: _l4_short(which))


L4_PAIR_SHORT = (61, 288)


def group_l4_e():
    cases = [(name, seed) for name in NEGATIVES for seed in range(5)]
    outs = [outcome(f'{name} seed {seed}', lambda n=name, s=seed: l4_neg_check(n, s))
            for name, seed in cases]
    counted('L4 E (G5) negatives: cells == [] in 25 of 25', outs)

    def short(which):
        def test():
            cells = l4_short_check(which)
            return len(cells) == 0, f'cells {[c["pci"] for c in cells]}'
        return test
    outs_s = [outcome('5 ms slice of a two-cell capture', short('5ms')),
              outcome('1 MS/s noise capture', short('1msps'))]
    counted('L4 E (G5) short captures: cells == [] with no exception', outs_s)


# ---- group F: timing, printed only (G6 is measured by the manager) -------------

def group_l4_f():
    import time
    try:
        spec = [dict(pci=61, power_db=0.0, delay_samples=0),
                dict(pci=288, power_db=-6.0, delay_samples=0)]
        iq, rate = synth_multi(cells=spec, bw_mhz=10, n_frames=30, snr_db=20.0, seed=0,
                               fs=15.36e6)
        cut = iq[:int(round(0.3 * rate))]
        t0 = time.perf_counter()
        found = api().cells(cut, rate)
        dt = time.perf_counter() - t0
        try:
            load = os.getloadavg()[0]
        except (AttributeError, OSError):
            load = 0.0
        print(f'  time  L4 F (G6, not gated): cells on 0.3 s of two cells at 15.36 MS/s: '
              f'{dt:.2f} s, {len(found)} cells (load {load:.2f} on {os.cpu_count() or 1} cores)')
    except Exception as exc:
        print(f'  time  L4 F (G6, not gated): not measured ({type(exc).__name__}: {exc})')


# ---- the interface (the shape the tool and later phases rely on) ---------------

L4_SPEC3 = [(90, 0.0, 0), (91, -3.0, 0), (92, -6.0, 0)]


def l4_iface_iq():
    """The three-cell capture (seed 0) as IQ, kept for the max_cells and fingerprint checks."""
    def make():
        spec = [dict(pci=p, power_db=w, delay_samples=d) for p, w, d in L4_SPEC3]
        return synth_multi(cells=spec, bw_mhz=10, n_frames=10, snr_db=20.0, seed=0)
    return memo('l4-iface-iq', make)


def cells_keys(cells):
    bad = [i for i, c in enumerate(cells) if not isinstance(c, dict)
           or any(k not in c for k in L4_CELL_KEYS)]
    return not bad, f'entries without every spec key: {bad}'


def cells_order(cells):
    rel = [float(c['relative_power_db']) for c in cells]
    ok = (bool(rel) and rel[0] == 0.0 and all(r <= 0.0 for r in rel)
          and all(a >= b for a, b in zip(rel, rel[1:])))
    return ok, f'relative_power_db {rel} (want strongest first, 0.0 then <= 0)'


def cells_chance(cells):
    bad = [c['pci'] for c in cells if not float(c['chance']) <= 1e-3]
    return not bad, f'chance above 1e-3 for PCIs {bad}'


def cells_formula(cells):
    bad = [c['pci'] for c in cells
           if c['pci'] != 3 * c['nid1'] + c['nid2'] or c['nid2'] not in (0, 1, 2)]
    return not bad, f'pci != 3 * nid1 + nid2, or nid2 not in 0-2: {bad}'


def check_l4_interface():
    lists = [('one cell, PCI 123 at 20 dB', lambda: l4_single(123, 20.0, 0)['cells']),
             ('three cells 90, 91, 92', lambda: multi_cells(L4_SPEC3, 0)['cells'])]
    for label, get in lists:
        expect(f'L4 interface ({label}): a list of dicts with every spec key',
               lambda g=get: cells_keys(g()))
        expect(f'L4 interface ({label}): strongest first, relative_power_db 0.0 then <= 0',
               lambda g=get: cells_order(g()))
        expect(f'L4 interface ({label}): chance <= 1e-3 for every entry',
               lambda g=get: cells_chance(g()))
        expect(f'L4 interface ({label}): pci = 3 * nid1 + nid2, nid2 in 0-2',
               lambda g=get: cells_formula(g()))

    def max_cells(n):
        def test():
            iq, rate = l4_iface_iq()
            got = api().cells(iq, rate, max_cells=n)
            return len(got) <= n, f'{len(got)} entries with max_cells={n}'
        return test
    expect('L4 interface: max_cells=1 gives at most one entry', max_cells(1))
    expect('L4 interface: max_cells=2 gives at most two entries', max_cells(2))

    def fingerprint_cells():
        iq, rate = l4_iface_iq()
        fp = api().fingerprint(iq, rate)
        if 'cells' not in fp:
            return False, 'fingerprint has no cells key'
        want = multi_cells(L4_SPEC3, 0)['cells']
        # fingerprint adds each cell's CRS result; the rest is cells() as it is.
        got = [{k: v for k, v in c.items() if k != 'crs'} for c in fp['cells']]
        same = bool(got == want)
        strongest = want[0]['pci'] if want else None
        ok = same and fp['pci'] == strongest
        return ok, f"cells equal {same}; fingerprint pci {fp['pci']!r}, strongest {strongest!r}"
    expect("L4 interface: fingerprint has 'cells' equal to cells(), and its pci is the strongest's",
           fingerprint_cells)


GROUPS.extend([group_l4_a, group_l4_b, group_l4_c, group_l4_d, group_l4_e, group_l4_f,
               check_l4_interface])


if __name__ == '__main__':
    sys.exit(main())
