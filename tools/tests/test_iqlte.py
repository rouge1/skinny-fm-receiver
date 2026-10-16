"""The LTE structural fingerprint (phase l1): ``fm_receiver/iqlte.py`` against
tools/swarm-records/specs/l1.md.

Signals: ``tests/lte_synth.py`` (an LTE FDD downlink, seeded) and
``tests/lte_negatives.py`` (signals that are not LTE). Each case is built once
and only its fingerprint is kept, so the groups share results without holding
every capture in memory.

The checks are found by name: the check whose name contains 'cp' is the CP
autocorrelation, 'pss' the PSS search, and 'sss' and 'crs' the two later stages
(these must be present with ran False).

Groups, one counted line each (the gates G1-G5, G7, G8 of the spec):
  A  G1  10 MHz, PCI 0/123/503, 20 and 0 dB, seeds 0-4 (30 cases):
         verdict lte, nid2 == PCI mod 3, nfft 1024
  A  G7  the 20 dB cases of A (15): both checks' chance <= 1e-6
  B  G2  1.4, 3, 5, 10, 20 MHz at 20 dB, PCI 123, seeds 0-2 (15 cases):
         verdict lte, nfft = the class table's N
  C  G3  10 MHz resampled to 20 MS/s, CFO +2 kHz, 10 dB, PCI 123, seeds 0-4
         (5 cases): verdict lte, nid2 == 0
  D  G4  negatives noise, tone, wifi_like, atsc_like, uplink_like, seeds 0-4
         (25 cases): verdict not lte; and noise: both chances >= 0.01 (5 cases)
  E  G5  control pss=False at 20 dB, seeds 0-4 (5 cases): verdict not lte;
         a 5 ms capture, seeds 0-2 (3 cases): verdict unknown, no exception
  G8     iqlte.py imports only numpy and the standard library (read with ast)
  interface: the result's shape, cp_autocorr and pss_search return dicts with
         score and chance, a 1 MS/s capture is unknown with no exception.
Time F is not tested here: the manager measures it.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iqlte.py
(a few minutes: about 90 synthesised captures, most of them 0.1 s at 15 to 20 MS/s)
"""

import ast
import os
import sys

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
    return label, ok, why


def counted(name, outcomes):
    """One ok/FAIL line for a group: every case must pass."""
    passes = sum(1 for _, ok, _ in outcomes if ok)
    bad = [f'{label}: {why}' for label, ok, why in outcomes if not ok]
    report(f'{name}: {passes} of {len(outcomes)} cases', passes == len(outcomes),
           '; '.join(bad[:6]))


def find_check(fp, key):
    """The one check whose name contains ``key`` (case-insensitive)."""
    hits = [c for c in fp['checks'] if key in str(c.get('name', '')).lower()]
    if len(hits) != 1:
        names = [c.get('name') for c in fp['checks']]
        raise AssertionError(f'want one check named like {key!r}, got {names}')
    return hits[0]


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
    cp, pss = find_check(fp, 'cp'), find_check(fp, 'pss')
    ok = cp['chance'] <= 1e-6 and pss['chance'] <= 1e-6
    return ok, f"cp chance {cp['chance']:.3g}, pss chance {pss['chance']:.3g}"


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


def group_c():
    outs = [outcome(f'seed {seed}', lambda d=seed: gate_c(d)) for seed in range(5)]
    counted('C (G3) 10 MHz at 20 MS/s, CFO +2 kHz, 10 dB: verdict lte, nid2 0', outs)


# ---- group D: G4 --------------------------------------------------------------

def gate_d(name, seed):
    fp = negative_fp(name, seed)
    return fp['verdict'] != 'lte', f"verdict {fp['verdict']}"


def gate_noise_chance(seed):
    fp = negative_fp('synth_noise', seed)
    cp, pss = find_check(fp, 'cp'), find_check(fp, 'pss')
    ok = cp['chance'] >= 0.01 and pss['chance'] >= 0.01
    return ok, f"cp chance {cp['chance']:.3g}, pss chance {pss['chance']:.3g}"


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
    iq, rate = synth(bw_mhz=10, n_frames=1, pci=123, snr_db=20.0, seed=seed)
    fp = api().fingerprint(iq[:int(round(0.005 * rate))], rate)
    return fp['verdict'] == 'unknown', f"verdict {fp['verdict']}"


def group_e():
    outs = [outcome(f'seed {seed}', lambda d=seed: gate_control(d)) for seed in range(5)]
    counted('E (G5) control pss=False at 20 dB: verdict not lte', outs)

    outs5 = [outcome(f'seed {seed}', lambda d=seed: gate_short(d)) for seed in range(3)]
    counted('E (G5) 5 ms capture: verdict unknown, no exception', outs5)


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

    expect('pci is None this phase', lambda: (fp()['pci'] is None, f"pci {fp()['pci']!r}"))

    def later_stages():
        sss, crs = find_check(fp(), 'sss'), find_check(fp(), 'crs')
        return (sss['ran'] is False and crs['ran'] is False,
                f"sss ran {sss['ran']!r}, crs ran {crs['ran']!r}")
    expect('SSS and CRS are checks with ran False', later_stages)

    def ran_checks():
        cp, pss = find_check(fp(), 'cp'), find_check(fp(), 'pss')
        return (cp['ran'] is True and pss['ran'] is True,
                f"cp ran {cp['ran']!r}, pss ran {pss['ran']!r}")
    expect('the CP and PSS checks ran', ran_checks)

    def chances():
        bad = []
        for key in ('cp', 'pss'):
            c = find_check(fp(), key)
            try:
                ok = 1e-300 <= float(c['chance']) <= 1.0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                bad.append(f"{key} chance {c['chance']!r}")
        return not bad, f'want a probability floored at 1e-300: {bad}'
    expect('CP and PSS chance are probabilities in [1e-300, 1]', chances)

    def nid2_nfft():
        r = fp()
        ok = (r['nid2'] in (0, 1, 2)) and r['nfft'] == 1024
        return ok, f"nid2 {r['nid2']!r}, nfft {r['nfft']!r}"
    expect('10 MHz: nid2 is a PSS root (0-2), nfft 1024', nid2_nfft)

    def returns_dict(name):
        def test():
            iq, rate = cheap_lte()
            out = getattr(api(), name)(iq, rate)
            ok = isinstance(out, dict) and 'score' in out and 'chance' in out
            return ok, f'got {type(out).__name__} with keys {sorted(out) if isinstance(out, dict) else out!r}'
        return test
    expect('cp_autocorr returns a dict with score and chance', returns_dict('cp_autocorr'))
    expect('pss_search returns a dict with score and chance', returns_dict('pss_search'))

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


if __name__ == '__main__':
    sys.exit(main())
