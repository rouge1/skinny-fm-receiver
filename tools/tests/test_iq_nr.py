"""The 5G NR sync finder command (phase n1): ``tools/iq-nr`` against
tools/swarm-records/specs/n1.md, the command half: acceptance J, and the
command's peak memory from I.

The tool runs as a subprocess (``sys.executable`` and the script path, from a
temporary working directory) on recordings synthesised here, in a temporary
folder that is deleted afterwards. Nothing outside the repo is read or written
except that folder; FMRX_CONFIG points at a temporary file.

Checks (one ok/FAIL line each; the gate in brackets):
  J  a NR .cfile with its .sigmf-meta: exit 0, the JSON fields, verdict nr,
     rate and centre read from the meta, the strongest SSB PCI 123 at GSCN 1500,
     every SSB entry with the spec's fields [G10]
  J  a noise .cfile: exit 0, verdict none, ssbs empty [G10]
  J  --seconds 5 on a 2.5 s file: seconds 2 and a reason [G10]
  J  a bare .cfile with no centre: exit 1, message on stderr, nothing on stdout [G10]
  J  a missing file, a sweep .sdvr, an unknown option, a bad --seconds value:
     each exit 1 with a message and nothing on stdout [G10]
  I  peak RSS of tools/iq-nr on a 1 s, 40 MS/s .cfile with --seconds 0.1: under
     700 MB (resource.getrusage(RUSAGE_CHILDREN).ru_maxrss of a wrapper process
     whose only child is the tool; KB on Linux, bytes on macOS) [G9]
  G10 tools/iq-nr imports only numpy, the standard library and fm_receiver (ast)

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_nr.py
(every subprocess has a 180 s limit; a timeout is a failure)
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))   # tools/: `tests` and `fm_receiver`

from tests import lte_negatives, nr_synth, signals  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'iq-nr')
TIMEOUT = 180
RSS_LIMIT_MB = 700.0
KEYS = ('file', 'rate', 'center_hz', 't0', 'seconds', 'verdict', 'reason', 'ssbs')
SSB_FIELDS = ('ssb_hz', 'gscn', 'scs_khz', 'pci', 'nid1', 'nid2', 'ssb_index', 'half_frame',
              'l_max', 'period_ms', 'bursts', 'first_ssb_s', 'cfo_hz', 'pss_score', 'sss_score',
              'sss_next', 'chance', 'dmrs_score', 'dmrs_chance', 'relative_power_db', 'detail')
WRAP = ("import json, resource, subprocess, sys\n"
        "r = subprocess.run([sys.executable] + sys.argv[1:], capture_output=True, text=True)\n"
        "rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
        "print(json.dumps({'code': r.returncode, 'out': r.stdout, 'err': r.stderr[-300:], 'rss': rss}))\n")

WORK = None
TMP = None
RESULTS = []
MEMO = {}


# ---- harness -----------------------------------------------------------------

def once(key, make):
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
    RESULTS.append((name, bool(ok)))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok and detail:
        line += f'  ({detail})'
    print(line, flush=True)


def expect(name, test):
    """``test()`` returns ``(ok, detail)``; an exception is a failed check."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


class Ran:
    """One finished run of the tool: exit code, stdout, stderr (or a timeout)."""

    def __init__(self, code=None, out='', err='', timed_out=False):
        self.code = code
        self.out = out
        self.err = err
        self.timed_out = timed_out


def clean_env():
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['FMRX_CONFIG'] = os.path.join(WORK, 'config.json')
    env['TMPDIR'] = TMP
    return env


def run_tool(args):
    if not os.path.isfile(TOOL):
        return Ran(err='tools/iq-nr does not exist')
    cwd = os.path.join(WORK, 'run-cwd')
    os.makedirs(cwd, exist_ok=True)
    cmd = [sys.executable, TOOL] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=clean_env(), capture_output=True, text=True,
                              timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return Ran(timed_out=True)
    return Ran(proc.returncode, proc.stdout, proc.stderr)


def describe(r):
    if r.timed_out:
        return f'timed out after {TIMEOUT} s'
    lines = r.err.strip().splitlines()
    return f'exit {r.code}: {lines[-1] if lines else "nothing on stderr"}'


def card_of(r):
    """``(doc, why)``: the one JSON object a run printed, or why there is none."""
    if r.timed_out or r.code != 0:
        return None, describe(r)
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None, 'stdout is not one JSON document'
    if not isinstance(doc, dict):
        return None, 'stdout JSON is not an object'
    return doc, ''


def refusal(r):
    """Exit 1, a message on stderr, nothing on stdout, no traceback."""
    ok = (not r.timed_out and r.code == 1 and r.err.strip() != '' and r.out.strip() == ''
          and 'Traceback' not in r.err)
    return ok, describe(r) + f'; stdout {r.out[:60]!r}'


def near(got, want, tol):
    if not isinstance(got, (int, float)) or isinstance(got, bool):
        return False, f'got {got!r}, want {want:.6g} +/- {tol:.3g}'
    return abs(got - want) <= tol, f'got {got:.6g}, want {want:.6g} +/- {tol:.3g}'


# ---- recordings (temporary, synthetic) --------------------------------------------

def write_recording(name, iq, rate, centre, meta=True):
    """``iq`` as ``WORK/<name>/<name>.cfile`` (complex64), with its .sigmf-meta beside it
    unless ``meta`` is False. Returns the .cfile path."""
    folder = os.path.join(WORK, name)
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, name)
    np.asarray(iq, dtype=np.complex64).tofile(base + '.cfile')
    if meta:
        with open(base + '.sigmf-meta', 'w') as fh:
            json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate},
                       'captures': [{'core:sample_start': 0, 'core:frequency': centre}],
                       'annotations': []}, fh)
    return base + '.cfile'


def nr_file():
    """100 ms of an NR cell at 20 MS/s: PCI 123, its SSB at 600.15 MHz, centre 600.0 MHz."""
    def make():
        iq, _, _ = nr_synth.synth_nr(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                     period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                     dur_ms=100, fs=20e6, seed=0)
        return write_recording('nr', iq, 20e6, 600.0e6)
    return once('nr', make)


def noise_file():
    """100 ms of white noise at 20 MS/s, bare .cfile (no meta)."""
    def make():
        iq, _ = lte_negatives.synth_noise(seed=0, fs=20e6, dur=0.1)
        return write_recording('noise', iq, 20e6, 600.0e6, meta=False)
    return once('noise', make)


def long_file():
    """2.5 s of white noise at 1 MS/s, bare .cfile: longer than the 2 s limit."""
    def make():
        rng = np.random.default_rng(0)
        n = 2500000
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2.0)
        return write_recording('long', iq, 1e6, 600.0e6, meta=False)
    return once('long', make)


def sweep_file():
    def make():
        folder = os.path.join(WORK, 'sweep')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'sweep.sdvr')
        signals.write_sweep_sdvr(path)
        return path
    return once('sweep', make)


# ---- the groups -----------------------------------------------------------------

def group_positive():
    doc, why = card_of(run_tool([nr_file()]))
    doc = doc or {}

    def on_doc(test):
        """The check runs on the printed document; with no document it fails with why."""
        return (lambda: (False, why)) if not doc else test

    expect('J exit 0 and one JSON document with the spec fields',
           lambda: (bool(doc) and all(k in doc for k in KEYS), why or f'keys {sorted(doc)}'))
    expect('J verdict nr for a NR .cfile with its .sigmf-meta',
           on_doc(lambda: (doc['verdict'] == 'nr', f"verdict {doc['verdict']!r}")))
    expect('J rate and centre read from the .sigmf-meta',
           on_doc(lambda: (near(doc['rate'], 20e6, 1.0)[0] and near(doc['center_hz'], 600.0e6, 1.0)[0],
                           f"rate {doc['rate']}, centre {doc['center_hz']}")))
    ssbs = doc.get('ssbs') or []
    expect('J the strongest SSB is PCI 123 at GSCN 1500',
           on_doc(lambda: ((bool(ssbs) and ssbs[0]['pci'] == 123 and ssbs[0]['gscn'] == 1500),
                           f"strongest {ssbs[0] if ssbs else None}")))
    expect('J every SSB entry has the spec fields',
           on_doc(lambda: (bool(ssbs) and all(all(k in e for k in SSB_FIELDS) for e in ssbs),
                           f'{len(ssbs)} entries')))


def group_noise():
    doc, why = card_of(run_tool([noise_file(), '--rate', '20e6', '--center', '600e6']))
    expect('J noise .cfile: exit 0, verdict none, ssbs empty',
           lambda: (doc is not None and doc.get('verdict') == 'none' and doc.get('ssbs') == [],
                    why or f"verdict {doc and doc.get('verdict')!r}, ssbs {doc and doc.get('ssbs')}"))


def group_cut():
    doc, why = card_of(run_tool([long_file(), '--rate', '1e6', '--center', '600e6', '--seconds', '5']))
    expect('J --seconds 5 on a longer file: seconds 2 and a reason',
           lambda: (doc is not None and near(doc.get('seconds'), 2.0, 1e-9)[0]
                    and isinstance(doc.get('reason'), str) and doc['reason'] != '',
                    why or f"seconds {doc and doc.get('seconds')}, reason {doc and doc.get('reason')!r}"))


def group_errors():
    expect('J a bare .cfile with no centre: exit 1',
           lambda: refusal(run_tool([noise_file(), '--rate', '20e6'])))
    expect('J a missing file: exit 1',
           lambda: refusal(run_tool([os.path.join(WORK, 'no-such-file.cfile')])))
    expect('J a sweep .sdvr: exit 1',
           lambda: refusal(run_tool([sweep_file()])))
    expect('J an unknown option: exit 1',
           lambda: refusal(run_tool([nr_file(), '--no-such-option'])))
    expect('J a bad --seconds value: exit 1',
           lambda: refusal(run_tool([nr_file(), '--seconds', 'abc'])))


def group_memory():
    """I: the command's peak RSS on a 1 s, 40 MS/s file, read only for 0.1 s."""
    folder = tempfile.mkdtemp(prefix='iqnr-mem-', dir=WORK)
    try:
        path = os.path.join(folder, 'one-second.cfile')
        iq, _, _ = nr_synth.synth_nr(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                     period_ms=20, n_hf=0, bw_mhz=10, ssb_offset_hz=0.0,
                                     dur_ms=100, fs=40e6, seed=0)
        block = np.asarray(iq, dtype=np.complex64)
        with open(path, 'wb') as fh:
            for _ in range(10):                  # 1 s: ten copies of the 100 ms block
                block.tofile(fh)
        cmd = [sys.executable, '-c', WRAP, TOOL, path, '--rate', '40e6', '--center', '600e6',
               '--seconds', '0.1']
        proc = subprocess.run(cmd, env=clean_env(), capture_output=True, text=True, timeout=TIMEOUT)
        wrapped = json.loads(proc.stdout.strip().splitlines()[-1])
        doc, why = card_of(Ran(wrapped['code'], wrapped['out'], wrapped['err']))
        expect('I iq-nr on a 1 s, 40 MS/s .cfile with --seconds 0.1: exit 0 and one JSON document',
               lambda: (doc is not None, why or 'ok'))
        # ru_maxrss is KB on Linux and bytes on macOS. A run that failed (no tool, a crash)
        # is not a memory measurement, so it fails here too.
        rss = wrapped['rss']
        mb = rss / (1024.0 * 1024.0) if sys.platform == 'darwin' else rss / 1024.0
        ran_ok = doc is not None
        report(f'I iq-nr peak RSS, 0.1 s of a 1 s file: {mb:.0f} MB (need under {RSS_LIMIT_MB:.0f} MB)',
               ran_ok and mb < RSS_LIMIT_MB,
               f'{mb:.0f} MB' + ('' if ran_ok else f'; the run did not complete: {why}'))
    except Exception as exc:
        report('I iq-nr peak RSS ran', False, f'{type(exc).__name__}: {exc}')
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def group_imports():
    try:
        with open(TOOL) as fh:
            tree = ast.parse(fh.read(), TOOL)
    except OSError as exc:
        report('G10 tools/iq-nr imports only numpy, the standard library and fm_receiver', False,
               f'{type(exc).__name__}: {exc}')
        return
    stdlib = set(sys.stdlib_module_names)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module.split('.')[0] if node.module and node.level == 0 else '<relative>')
    bad = sorted(m for m in mods if m not in stdlib | {'numpy', 'fm_receiver'})
    report('G10 tools/iq-nr imports only numpy, the standard library and fm_receiver', not bad,
           f'imports {bad}')


def main():
    global WORK, TMP
    WORK = tempfile.mkdtemp(prefix='iqnr-test-')
    TMP = os.path.join(WORK, 'tmp')
    os.makedirs(TMP)
    t0 = time.time()
    try:
        for group in (group_positive, group_noise, group_cut, group_errors, group_memory, group_imports):
            try:
                group()
            except Exception as exc:
                report(f'{group.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    failed = [name for name, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed in {time.time() - t0:.0f} s')
    if failed:
        print('failed:')
        for name in failed:
            print(f'  {name}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
