"""The decode tool (phase m6): ``tools/iq-decode`` against CONTRACTS-measure.md, "Decoding with rtl_433".

The tool is run as a subprocess, from a temporary working directory, on a synthetic
Nexus-TH frame built here (numpy, seeded) in a temporary folder. The frame is 36 bits of
OOK at 250 kS/s, repeated 12 times, as in the spec (tools/swarm-records/specs/m6.md).
What the checks pin:

- a frame .cfile with its .sigmf-meta decodes (packets, Nexus-TH id 181, 19.0 C), with the
  card's keys; a frame scaled down by 0.005 and mixed 30 kHz off decodes too, with the
  gain the contract reports, and rtl_433 run directly on that file decodes nothing (the
  negative control: if it decodes, the control is invalid);
- a frame in a 2 MS/s file 200 kHz above the centre decodes with ``--band``, and the
  centre reported is the band's;
- a noise-only file decodes 0 packets; a missing file and a Sceptre .sdvr of garbage exit
  1 with a message; rtl_433 absent exits 2 with an install hint;
- ``--keep`` keeps the cf32 file, and without it the temp folder is left empty;
- rtl_433's stdout goes to a file (no pipe), the decode is reproducible, and 5 s at 2 MS/s
  decodes in under 10 s.

Without rtl_433 on PATH the file prints SKIP and exits 0.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_decode.py
(every subprocess has a 120 s limit; a timeout is a failure)
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
TOOL = os.path.join(ROOT, 'tools', 'iq-decode')
RTL = 'rtl_433'
TIMEOUT = 120
SPEED_LIMIT_S = 10.0

FS = 250000.0
FS_WIDE = 2000000.0
CENTRE = 433.92e6
WIDE_OFFSET = 200e3
WIDE_BAND = ('434.07e6', '434.17e6')
WIDE_CENTRE = 434.12e6
MARK = 124        # 496 us at 250 kS/s
GAP_ONE = 486     # 1944 us
GAP_ZERO = 241    # 964 us
ROW_GAP = 1000    # 4000 us after a row's trailing mark
LAST_GAP = 6250   # 25000 us after the last row
LEAD = 1250       # 5 ms of silence before the first
RAMP = 4          # 16 us edge ramp
ROWS = 12
ROW = '101101011000000010111110111101000111'
KEYS = ('file', 'center_hz', 'rate_sps', 'gain_db', 'rtl_433', 'protocols', 'packets',
        'decodes', 'summary')
FOLDER = None
WORK = None
FRAME = None
SCALED = None
SCALED_GAIN = None
WIDE = None
NOISE = None
LONG = None
RESULTS = []
MEMO = {}


# ---- harness -----------------------------------------------------------------

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


def near(got, want, tol):
    if got is None:
        return False, f'got None, want {want:.6g}'
    return abs(got - want) <= tol, f'got {got:.6g}, want {want:.6g} +/- {tol:.3g}'


def clean_env():
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    return env


class Ran:
    """One finished run of a tool: exit code, stdout, stderr (or a timeout)."""

    def __init__(self, code=None, out='', err='', timed_out=False):
        self.code = code
        self.out = out
        self.err = err
        self.timed_out = timed_out


def run(args, cwd, env=None):
    """``tools/iq-decode`` with ``args``. A missing tool is a run that exited with no code,
    so every check that needs it fails rather than the file crashing."""
    if not os.path.isfile(TOOL):
        return Ran(err='tools/iq-decode does not exist')
    cmd = [sys.executable, TOOL] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env or clean_env(), capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return Ran(timed_out=True)
    return Ran(proc.returncode, proc.stdout, proc.stderr)


def last_line(text):
    lines = text.strip().splitlines()
    return lines[-1] if lines else 'nothing on stderr'


def describe(r):
    if r.timed_out:
        return f'timed out after {TIMEOUT} s'
    return f'exit {r.code}: {last_line(r.err)}'


def card_of(r):
    """The card a run printed, or None when stdout is not one JSON object."""
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def printed(r, then):
    """``then(card)`` when the run exited 0 and printed one card; else a failure."""
    if r.timed_out or r.code != 0:
        return False, describe(r)
    c = card_of(r)
    if c is None:
        return False, 'stdout is not only one JSON card'
    return then(c)


def refusal(r):
    """Exit 1, a message on stderr, and nothing on stdout."""
    return (not r.timed_out and r.code == 1 and r.err.strip() != '' and r.out.strip() == '',
            describe(r) + f'; stdout {r.out[:60]!r}')


def packets_ok(c):
    n = c.get('packets')
    return isinstance(n, int) and n >= 1, f'packets {n!r}'


def nexus_ok(c):
    found = [s for s in c.get('summary', []) if isinstance(s, dict)
             and s.get('model') == 'Nexus-TH' and s.get('id') == 181]
    return bool(found), f"summary {c.get('summary')!r}, want Nexus-TH id 181"


# ---- synthetic fixture (the spec's Nexus-TH frame, built here) -------------------

def clean_frame():
    """The carrier envelope of the frame at 250 kS/s: 0.4 while the carrier is on (constant
    phase), 0 in the gaps, edges ramped over 16 us. Complex, so it can be mixed."""
    levels = [np.zeros(LEAD)]
    for k in range(ROWS):
        for bit in ROW:
            levels.append(np.ones(MARK))
            levels.append(np.zeros(GAP_ONE if bit == '1' else GAP_ZERO))
        levels.append(np.ones(MARK))
        levels.append(np.zeros(LAST_GAP if k == ROWS - 1 else ROW_GAP))
    env = np.convolve(np.concatenate(levels), np.ones(RAMP) / RAMP, mode='same')
    return (0.4 * env).astype(np.complex128)


def with_noise(sig, seed):
    """Complex noise of 0.003 per component, seeded."""
    rng = np.random.default_rng(seed)
    noise = 0.003 * (rng.standard_normal(len(sig)) + 1j * rng.standard_normal(len(sig)))
    return sig + noise


def mix(sig, rate, offset):
    """``sig`` moved ``offset`` Hz up."""
    t = np.arange(len(sig)) / rate
    return sig * np.exp(2j * np.pi * offset * t)


def write_sigmf(base, rate, centre):
    with open(base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate},
                   'captures': [{'core:sample_start': 0, 'core:frequency': centre}],
                   'annotations': []}, fh)


def write_recording(folder, name, iq, rate, centre):
    """A .cfile of ``iq`` (complex64) with its .sigmf-meta beside it; returns its path."""
    base = os.path.join(folder, name)
    np.asarray(iq, dtype=np.complex64).tofile(base + '.cfile')
    write_sigmf(base, rate, centre)
    return base + '.cfile'


def build_fixtures():
    """The recordings the checks read, in FOLDER (each in a folder of its own)."""
    global FRAME, SCALED, SCALED_GAIN, WIDE, NOISE, LONG
    clean = clean_frame()
    FRAME = write_recording(fresh('fix-frame'), 'frame', with_noise(clean, 1), FS, CENTRE)
    scaled = mix(with_noise(clean, 1) * 0.005, FS, 30e3)
    SCALED = write_recording(fresh('fix-scaled'), 'scaled', scaled, FS, CENTRE)
    SCALED_GAIN = 20 * np.log10(0.5 / np.percentile(np.abs(np.asarray(scaled, np.complex64)), 99.9))
    # The frame lasts about 0.9 s at 250 kS/s (not 0.36 s), so the 2 MS/s file is sized to
    # hold all of it, with 0.1 s of silence after.
    padded = np.concatenate([np.repeat(clean, 8), np.zeros(int(0.1 * FS_WIDE))])
    WIDE = write_recording(fresh('fix-wide'), 'wide',
                           with_noise(mix(padded, FS_WIDE, WIDE_OFFSET), 2), FS_WIDE, CENTRE)
    NOISE = write_recording(fresh('fix-noise'), 'noise', with_noise(np.zeros(int(FS)), 3),
                            FS, CENTRE)
    n = int(5 * FS_WIDE)
    tile = np.repeat(clean, 8)
    filled = np.tile(tile, -(-n // len(tile)))[:n]
    LONG = write_recording(fresh('fix-long'), 'long', with_noise(filled, 5), FS_WIDE, CENTRE)


def frame_decode():
    return memo(('decode', 'frame'), lambda: run([FRAME], WORK))


# ---- the checks --------------------------------------------------------------------

def check_frame():
    expect('1 frame .cfile + .sigmf-meta: exit 0, stdout is one JSON card with the spec keys',
           lambda: printed(frame_decode(), lambda c: (
               not [k for k in KEYS if k not in c],
               f'missing {[k for k in KEYS if k not in c]}; keys {sorted(c)}')))
    expect('1 frame: packets >= 1', lambda: printed(frame_decode(), packets_ok))
    expect('1 frame: summary has Nexus-TH id 181', lambda: printed(frame_decode(), nexus_ok))
    expect('1 frame: decodes[0].temperature_C is 19.0', lambda: printed(frame_decode(), lambda c: (
        isinstance(c.get('decodes'), list) and len(c['decodes']) > 0
        and c['decodes'][0].get('temperature_C') == 19.0,
        f"decodes[0] {c.get('decodes', [None])[0]!r}")))


def scaled_decode():
    return memo(('decode', 'scaled'), lambda: run([SCALED], WORK))


def count_model_lines(path):
    count = 0
    with open(path) as fh:
        for line in fh:
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict) and 'model' in doc:
                count += 1
    return count


def control_decodes():
    """rtl_433 run directly on the scaled file, stdout to a file. Returns (exit code or
    None when it did not finish, whether the stdout file exists, the count of its decodes:
    lines with a model key)."""
    def make():
        out = os.path.join(fresh('control'), 'rtl433.json')
        cmd = [RTL, '-r', 'cf32:' + SCALED, '-s', str(int(FS)), '-f', str(int(CENTRE)), '-F', 'json']
        try:
            with open(out, 'w') as fh:
                proc = subprocess.run(cmd, cwd=WORK, stdout=fh, stderr=subprocess.PIPE,
                                      text=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, os.path.isfile(out), 0, f'{type(exc).__name__}: {exc}'
        exists = os.path.isfile(out)
        return proc.returncode, exists, (count_model_lines(out) if exists else 0), ''
    return memo('control', make)


def check_scaled():
    expect('2 frame scaled by 0.005 and mixed 30 kHz: packets >= 1',
           lambda: printed(scaled_decode(), packets_ok))
    expect('2 scaled frame: summary has Nexus-TH id 181',
           lambda: printed(scaled_decode(), nexus_ok))

    def gain():
        return printed(scaled_decode(), lambda c: near(c.get('gain_db'), SCALED_GAIN, 1.0))
    expect(f'2 scaled frame: gain_db is what the contract gives (99.9th percentile to 0.5, '
           f'about {SCALED_GAIN:+.1f} dB)', gain)

    def control():
        code, exists, count, why = control_decodes()
        if code != 0 or not exists:
            return False, (f'the control did not run: rtl_433 exit {code}, stdout file '
                           f'{"exists" if exists else "missing"} {why}').rstrip()
        return count == 0, (f'control invalid: rtl_433 decoded {count} line(s) with a model '
                            f'key on the scaled file; the control must decode 0')
    expect('2 negative control: rtl_433 run directly on the scaled file exits 0 and decodes '
           '0 lines with a model key', control)


def wide_decode():
    return memo(('decode', 'wide'), lambda: run([WIDE, '--band', *WIDE_BAND], WORK))


def check_wide():
    expect('3 frame in a 2 MS/s file, 200 kHz above the centre, --band: packets >= 1',
           lambda: printed(wide_decode(), packets_ok))
    expect('3 frame in a 2 MS/s file: summary has Nexus-TH id 181',
           lambda: printed(wide_decode(), nexus_ok))
    expect('3 frame in a 2 MS/s file: center_hz within 1 kHz of 434.12 MHz',
           lambda: printed(wide_decode(), lambda c: near(c.get('center_hz'), WIDE_CENTRE, 1e3)))


def check_noise():
    r = memo(('decode', 'noise'), lambda: run([NOISE], WORK))
    expect('4 noise-only file: exit 0 and stdout is one JSON card',
           lambda: printed(r, lambda c: (True, '')))
    expect('4 noise-only file: packets == 0 and decodes == []',
           lambda: printed(r, lambda c: (
               c.get('packets') == 0 and c.get('decodes') == [],
               f"packets {c.get('packets')!r}, decodes {c.get('decodes')!r}")))


def check_refusals():
    expect('5 missing file: exit 1, a message on stderr, nothing on stdout',
           lambda: refusal(run([os.path.join(FOLDER, 'nothere.cfile')], WORK)))

    def junk_sdvr():
        folder = fresh('junk')
        path = os.path.join(folder, 'junk.sdvr')
        with open(path, 'wb') as fh:
            fh.write(np.random.default_rng(19).bytes(8192))
        return refusal(run([path], WORK))
    expect('5 a garbage .sdvr (8 KB of random bytes; a real sweep DVR is covered by '
           'test_iq_measure.py): exit 1, a message on stderr', junk_sdvr)

    def absent():
        empty = fresh('no-rtl433')
        env = clean_env()
        env['PATH'] = empty
        r = run([FRAME], WORK, env=env)
        ok = (not r.timed_out and r.code == 2 and 'rtl_433' in r.err
              and 'install' in r.err.lower())
        return ok, describe(r) + '; want exit 2 with rtl_433 and how to install it'
    expect('6 rtl_433 not on PATH: exit 2, the message names rtl_433 and how to install it',
           absent)


def check_keep():
    def keep():
        path = os.path.join(fresh('keep'), 'kept.cf32')
        r = run([FRAME, '--keep', path], WORK)
        ok = (not r.timed_out and r.code == 0 and os.path.isfile(path)
              and os.path.getsize(path) > 0)
        return ok, describe(r) + f'; kept file exists {os.path.isfile(path)}'
    expect('7 --keep PATH: exit 0 and the cf32 file is kept (exists, size > 0)', keep)

    def no_keep():
        tmp = fresh('tmpdir-empty')
        env = clean_env()
        env['TMPDIR'] = tmp
        fixture = os.path.dirname(FRAME)
        before = sorted(os.listdir(fixture))
        r = run([FRAME], WORK, env=env)
        left = sorted(os.listdir(tmp))
        after = sorted(os.listdir(fixture))
        new = sorted(set(after) - set(before))
        ok = not r.timed_out and r.code == 0 and left == [] and before == after
        return ok, (describe(r) + f'; temp folder holds {left}; new beside the input {new}'
                    if not ok else '')
    expect('7 without --keep: the temp folder is empty afterwards and nothing new appears '
           'beside the input (no cf32 left behind)', no_keep)


def _name_of(node):
    """The name a Name or Attribute node refers to (``x`` or ``m.x`` gives ``x``)."""
    return node.id if isinstance(node, ast.Name) else node.attr


def _is_name(node, name):
    return isinstance(node, (ast.Name, ast.Attribute)) and _name_of(node) == name


def _is_false(node):
    return isinstance(node, ast.Constant) and node.value is False


def pipe_problems(text):
    """What the tool's code (comments and docstrings are not code, so ast ignores them)
    pipes or captures: (problems, redirects). A problem is a PIPE in any form, or
    capture_output set. A redirect is a subprocess call given a stdout that is not PIPE
    or DEVNULL: the file rtl_433 writes to."""
    tree = ast.parse(text)
    problems, redirects = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
            if any(alias.name == 'PIPE' for alias in node.names):
                problems.append(f'imports PIPE from subprocess (line {node.lineno})')
        elif isinstance(node, (ast.Name, ast.Attribute)) and _name_of(node) == 'PIPE':
            problems.append(f'uses PIPE (line {node.lineno})')
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == 'capture_output' and not _is_false(kw.value):
                    problems.append(f'capture_output (line {node.lineno})')
            if _runs_rtl(node):
                for kw in node.keywords:
                    if kw.arg == 'stdout' and not _is_name(kw.value, 'DEVNULL'):
                        redirects += 1
    return problems, redirects


def _runs_rtl(call):
    """A subprocess call (run, Popen, call, ...) whose arguments name rtl_433."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in (
            'run', 'Popen', 'call', 'check_call', 'check_output')):
        return False
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and 'rtl_433' in n.value
               for n in ast.walk(call))


def check_source():
    def no_pipe():
        if not os.path.isfile(TOOL):
            return False, 'tools/iq-decode does not exist'
        with open(TOOL) as fh:
            text = fh.read()
        try:
            problems, redirects = pipe_problems(text)
        except SyntaxError as exc:
            return False, f'tools/iq-decode does not parse: {exc}'
        if problems:
            return False, 'rtl_433 output may be piped: ' + '; '.join(problems)
        return redirects >= 1, ('no subprocess call with a stdout redirect to a file '
                                '(stdout= not PIPE or DEVNULL)')
    expect("8 source (ast, comments ignored): rtl_433's stdout is redirected to a file, and "
           "no PIPE, capture_output or PIPE import is used", no_pipe)

    def again():
        r = run([FRAME], WORK)
        return printed(r, lambda c: (
            packets_ok(c)[0] and nexus_ok(c)[0] and c.get('decodes')[0].get('temperature_C') == 19.0,
            f"packets {c.get('packets')!r}, summary {c.get('summary')!r}"))
    expect('8 the frame decode is reproducible: a second run decodes Nexus-TH id 181 again', again)


def check_speed():
    t0 = time.monotonic()
    r = run([LONG], WORK)
    wall = time.monotonic() - t0
    expect('9 speed: 5 s at 2 MS/s decodes in under 10 s wall, packets >= 1',
           lambda: printed(r, lambda c: (
               packets_ok(c)[0] and wall < SPEED_LIMIT_S,
               f"wall {wall:.1f} s, {packets_ok(c)[1]}")))


def check_flags():
    """The contract's other flags, each on the 250 kS/s frame (a cheap run apiece)."""
    def flag(key, args):
        return memo(('flag', key), lambda: run(args, WORK))

    def keys_only(c):
        missing = [k for k in KEYS if k not in c]
        return not missing, f'missing {missing}; keys {sorted(c)}'

    r19 = flag('r19', [FRAME, '-R', '19'])
    expect('4 -R 19: exit 0, protocols is the -R list [19] (Nexus-TH is protocol 19 in rtl_433)',
           lambda: printed(r19, lambda c: (c.get('protocols') == [19],
                                           f"protocols {c.get('protocols')!r}")))
    expect('4 -R 19: still decodes Nexus-TH id 181', lambda: printed(r19, nexus_ok))
    expect('4 --t0 0.0 --t1 0.2: exit 0 and the JSON document has the spec keys',
           lambda: printed(flag('span', [FRAME, '--t0', '0.0', '--t1', '0.2']), keys_only))
    expect('4 --out-rate 125000: rate_sps is above 0 and at most 125000', lambda: printed(
        flag('out-rate', [FRAME, '--out-rate', '125000']), lambda c: (
            isinstance(c.get('rate_sps'), (int, float)) and 0 < c['rate_sps'] <= 125000,
            f"rate_sps {c.get('rate_sps')!r}")))

    def bare():
        folder = fresh('bare-cfile')
        path = os.path.join(folder, 'bare.cfile')
        shutil.copy(FRAME, path)
        r = run([path, '--rate', '250000', '--center', '433920000'], WORK)
        return printed(r, lambda c: (
            c.get('center_hz') == 433920000 and packets_ok(c)[0] and nexus_ok(c)[0],
            f"center_hz {c.get('center_hz')!r}, {packets_ok(c)[1]}, "
            f"summary {c.get('summary')!r}"))
    expect('4 bare .cfile (no .sigmf-meta) with --rate 250000 --center 433920000: center_hz '
           '433920000 and Nexus-TH id 181 decoded', bare)


GROUPS = [check_frame, check_scaled, check_wide, check_noise, check_refusals, check_keep,
          check_source, check_flags, check_speed]


def main():
    global FOLDER, WORK
    if shutil.which(RTL) is None:
        print(f'SKIP: {RTL} is not on PATH (install it to run the iq-decode checks)')
        return 0
    parent = '/tmp/opencode' if os.path.isdir('/tmp/opencode') else None
    FOLDER = tempfile.mkdtemp(prefix='iqdecode-test-', dir=parent)
    WORK = fresh('work')
    try:
        try:
            build_fixtures()
        except Exception as exc:
            report('the synthetic fixtures are built', False, f'{type(exc).__name__}: {exc}')
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


if __name__ == '__main__':
    sys.exit(main())
