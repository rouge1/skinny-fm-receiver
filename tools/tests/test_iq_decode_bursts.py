"""The opt-in ``--bursts`` path of ``tools/iq-decode`` (tests/test_iq_decode.py pins the default path).

rtl_433 is a stub on PATH, a short script that logs what it was called with and the size of
the file it was given, so the checks are about what iq-decode hands over, not about what any
decoder does. The recording is the synthetic wideband file of test_iq_frames.py: twelve
FSK bursts, each at its own frequency, with a steady noise floor. What the checks pin:

- with ``-R N`` the sample rate given to rtl_433 is the one ``rtl_433 -R help`` prints as
  ``(-s RATE)`` for N; with no such protocol (or no ``-R``) it falls back to ``--out-rate``'s
  default, 250000;
- the cf32 file holds the twelve bursts and silence between them: twelve separate runs of
  non-zero samples, each scaled so that the burst's own level is about 0.5;
- without ``--bursts`` the call is the old one: the recording as one slice, the stub told
  ``-s`` of at most 250000, and no ``bursts`` key in the output;
- ``--bursts`` on noise runs rtl_433 once on a short silent file and reports 0 packets;
  ``--max-bursts`` limits the bursts written; ``--max-bursts 0`` is refused with exit 1.

Run:  PYTHONPATH=tools python tools/tests/test_iq_decode_bursts.py
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import test_iq_frames as F  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'iq-decode')
RESULTS = []

STUB = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
log = os.environ['STUB_LOG']
if '-R' in a and a[a.index('-R') + 1] == 'help':
    print("  [01]  Silvercrest Remote Control")
    print("  [19]  Nexus, FreeTec NC-7345, NX-3980, Solight TE82S, TFA 30.3209")
    print("  [383]  Silver Spring Networks mesh endpoint (-s 1600k)")
    print("  [390]* Imaginary 2.4M decoder (-s 2.4M)")
    sys.exit(0)
rec = {'args': a}
if '-r' in a:
    path = a[a.index('-r') + 1].split(':', 1)[-1]
    rec['bytes'] = os.path.getsize(path)
with open(log, 'a') as fh:
    fh.write(json.dumps(rec) + '\n')
sys.stderr.write('rtl_433 version stub\n')
print(json.dumps({'model': 'Stub', 'id': 1}))
'''


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"))


def run_tool(args, env, timeout=300):
    return subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True, timeout=timeout, env=env)


def last_call(log):
    lines = [json.loads(x) for x in open(log).read().splitlines() if x.strip()]
    return lines[-1] if lines else None


def runs_of_signal(iq, rate, gap_s=2e-3):
    """Start and stop sample of each stretch of non-zero samples, joined across gaps shorter than ``gap_s``."""
    nz = np.flatnonzero(np.abs(iq) > 1e-6)
    if nz.size == 0:
        return []
    cut = np.flatnonzero(np.diff(nz) > gap_s * rate)
    starts = np.concatenate(([nz[0]], nz[cut + 1]))
    stops = np.concatenate((nz[cut], [nz[-1]]))
    return list(zip(starts, stops))


def main():
    folder = tempfile.mkdtemp(prefix='iqdecode-bursts-')
    try:
        bindir = os.path.join(folder, 'bin')
        os.makedirs(bindir)
        stub = os.path.join(bindir, 'rtl_433')
        with open(stub, 'w') as fh:
            fh.write(STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        log = os.path.join(folder, 'calls.jsonl')
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ['PATH'], STUB_LOG=log)
        env.pop('PYTHONPATH', None)
        path, truth, _, _ = F.wide_file(folder)

        print('--bursts')
        keep = os.path.join(folder, 'kept.cf32')
        p = run_tool([path, '--bursts', '-R', '383', '--keep', keep], env)
        call = last_call(log) if os.path.exists(log) else None
        doc = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else {}
        check('exit 0 with a JSON document', p.returncode == 0 and bool(doc), p.stderr[-200:])
        s_val = call['args'][call['args'].index('-s') + 1] if call else None
        check('-R 383 asks rtl_433 for the 1600k its help line gives', s_val == '1600000', f'-s {s_val}')
        check('the document reports rate_sps 1600000 and bursts 12',
              doc.get('rate_sps') == 1600000 and doc.get('bursts') == 12, f'{doc.get("rate_sps")}, {doc.get("bursts")}')
        iq = np.fromfile(keep, dtype=np.complex64)
        runs = runs_of_signal(iq, 1.6e6)
        check('the file holds twelve separate bursts with silence between', len(runs) == 12, f'{len(runs)} runs')
        peaks = [float(np.percentile(np.abs(iq[a:b + 1]), 99.0)) for a, b in runs]
        check('each burst is scaled by its own level (99th percentile near 0.5)',
              bool(peaks) and all(0.3 < v < 0.9 for v in peaks), f'{[round(v, 2) for v in peaks]}')
        check('the packet the stub decoded is passed through', doc.get('packets') == 1, str(doc.get('packets')))

        p = run_tool([path, '--bursts', '-R', '999'], env)
        call = last_call(log)
        s_val = call['args'][call['args'].index('-s') + 1]
        check('an unknown protocol falls back to 250000', p.returncode == 0 and s_val == '250000', f'-s {s_val}')

        p = run_tool([path, '--bursts', '-R', '383', '--max-bursts', '3', '--keep', keep], env)
        iq3 = np.fromfile(keep, dtype=np.complex64)
        check('--max-bursts 3 writes three bursts', len(runs_of_signal(iq3, 1.6e6)) == 3,
              f'{len(runs_of_signal(iq3, 1.6e6))} runs')
        p = run_tool([path, '--bursts', '--max-bursts', '0'], env)
        check('--max-bursts 0 is refused with exit 1', p.returncode == 1 and not p.stdout.strip(), f'exit {p.returncode}')

        print('the default path')
        p = run_tool([path, '-R', '19'], env)
        call = last_call(log)
        doc = json.loads(p.stdout) if p.returncode == 0 else {}
        s_val = float(call['args'][call['args'].index('-s') + 1])
        check('without --bursts: one slice at no more than 250000 and no bursts key',
              p.returncode == 0 and s_val <= 250000 and 'bursts' not in doc, f'-s {s_val}, keys {sorted(doc)}')

        print('no bursts')
        rng = np.random.default_rng(3)
        noise = ((rng.normal(size=120000) + 1j * rng.normal(size=120000)) * 0.01).astype(np.complex64)
        npath = os.path.join(folder, 'noise.cfile')
        F.write_cfile(npath, noise, 2.5e6, 915e6)
        p = run_tool([npath, '--bursts', '-R', '383'], env)
        doc = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else {}
        check('noise: exit 0, no bursts, rtl_433 run once on a silent file',
              p.returncode == 0 and doc.get('bursts') == 0, f'exit {p.returncode}, {doc.get("bursts")}')
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    failed = [n for n, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed')
    if failed:
        print('failed:', *failed, sep='\n  ')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
