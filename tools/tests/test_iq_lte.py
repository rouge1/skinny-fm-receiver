"""The LTE CLI (phase l3): ``tools/iq-lte`` against tools/swarm-records/specs/l3.md.

The tool is run as a subprocess, from a temporary working directory, on synthetic
recordings built here (``lte_synth``, ``lte_negatives``, ``signals``). With ``--decode``
it runs ``$FMRX_LTE_TOOLS/run_pdsch.sh``: here that is a STUB, built by this file in
a temporary folder (a Python script behind a shell wrapper). The stub logs its
arguments, decodes only when its config says to, and appends one canned transport
block to ``$PDSCH_DUMP``. No srsRAN program runs, and the payload holds no bytes
from any real cell.

Groups (each check prints one ``ok``/``FAIL`` line; the gate each one serves is in
brackets, from the spec's "Gates" section):

  A  structure      the 10 MHz PCI 123 block at 20 MS/s, 300 kHz off a 751.0 MHz
                    centre: JSON keys, verdict lte, pci 123, nid2, nid1, nfft,
                    bandwidth, block centre, span, decode null [G1]; without
                    --block-center not lte [G1]; a raw .cfile with --rate and
                    --center; --t0/--t1; a 5 MHz class; an IQ .sdvr [any recording]
  B  negatives      noise, atsc_like, uplink_like: verdict not lte, pci None, exit 0 [G2]
  C  errors         missing file, sweep .sdvr, bad option, bad value: exit 1 [G2]
  D  no tools       --decode with an empty and with a missing FMRX_LTE_TOOLS: exit 2 [G2]
  E  decode, stub   not lte: decode ran false, stub never called, exit 0; the stub's
                    success (mib, tried, offsets, file, sib1) [G1]; a retry after the
                    first offset fails; --ports; a never-decoding stub [G1]
  F  cleanup        temp folder identical before and after --decode; repo tree unchanged
  G  timing         the 0.2 s synthetic file in under 10 s [G3], printed
  H  imports        tools/iq-lte and tools/fm_receiver/iqlte_decode.py import only
                    numpy, the standard library and fm_receiver (read with ast) [G5]

SIB1 parse: when a Python with pycrate imports (``FMRX_TEST_PYCRATE_PYTHON``, else
this interpreter), the test encodes a synthetic SIB1 with it (PLMN 001-01, TAC 0x1234,
cell identity 0x0ABCD01, band 13) and checks the parsed fields. Otherwise it prints
``SKIP sib1 parse (pycrate not available)`` and checks only that ``sib1`` is null with
a reason that names pycrate.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_lte.py
(every subprocess has a 180 s limit; a timeout is a failure)
"""

import ast
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))   # tools/: `tests` and `fm_receiver`

from tests import lte_negatives, lte_synth, signals  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'iq-lte')
IQLTE_DECODE = os.path.join(ROOT, 'tools', 'fm_receiver', 'iqlte_decode.py')
TIMEOUT = 180
SPEED_LIMIT_S = 10.0

FS = 20e6                 # the recordings' sample rate
CENTRE = 751.0e6          # the recordings' centre
OFFSET = 300e3            # the LTE carrier sits this far above the centre
BLOCK = CENTRE + OFFSET   # the right --block-center
BLOCK_S = str(int(BLOCK))
PCI = 123
FRAME = 153600            # samples per 10 ms frame at 15.36 MS/s
HALF = 76800
RATE_DEC = 15.36e6        # what pdsch_ue reads (10 MHz)
KEYS = ('file', 'block_center_hz', 'rate_sps', 'duration_s', 'verdict', 'nid2', 'nid1',
        'pci', 'nfft', 'bandwidth_mhz', 'half_frame', 'frame_offset_samples', 'checks',
        'decode')
MIB_KEYS = ('pci', 'ports', 'prb', 'type', 'cp', 'sfn')
DEC_KEYS = ('mib', 'offset_samples', 'sib1', 'tried')

WORK = None
TMP = None          # the private TMPDIR every tool run is given
EMPTY = None        # an empty tools directory
PYC_PY = None       # a Python that imports pycrate, or None
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
    if not ok:
        line += f'  ({detail})'
    print(line)


def expect(name, test):
    """Run ``test()``, which returns ``(ok, detail)``. An exception is a failure, and the
    other checks still run."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def near(got, want, tol):
    if not isinstance(got, (int, float)) or isinstance(got, bool):
        return False, f'got {got!r}, want {want:.6g} +/- {tol:.3g}'
    return abs(got - want) <= tol, f'got {got:.6g}, want {want:.6g} +/- {tol:.3g}'


def clean_env():
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PDSCH_DUMP', None)
    return env


class Ran:
    """One finished run of the tool: exit code, stdout, stderr (or a timeout)."""

    def __init__(self, code=None, out='', err='', timed_out=False, seconds=0.0):
        self.code = code
        self.out = out
        self.err = err
        self.timed_out = timed_out
        self.seconds = seconds


def run_tool(args, tools=None):
    """``tools/iq-lte`` with ``args``. ``tools`` is the FMRX_LTE_TOOLS it sees; by default
    an empty folder, so the real outside programs can never be reached by mistake."""
    if not os.path.isfile(TOOL):
        return Ran(err='tools/iq-lte does not exist')
    env = clean_env()
    env['FMRX_LTE_TOOLS'] = tools if tools is not None else EMPTY
    env['TMPDIR'] = TMP
    env['FMRX_CONFIG'] = os.path.join(WORK, 'config.json')
    cwd = os.path.join(WORK, 'run-cwd')
    os.makedirs(cwd, exist_ok=True)
    cmd = [sys.executable, TOOL] + [str(a) for a in args]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return Ran(timed_out=True, seconds=time.time() - t0)
    return Ran(proc.returncode, proc.stdout, proc.stderr, seconds=time.time() - t0)


def describe(r):
    if r.timed_out:
        return f'timed out after {TIMEOUT} s'
    lines = r.err.strip().splitlines()
    return f'exit {r.code}: {lines[-1] if lines else "nothing on stderr"}'


def card_of(r):
    """The JSON object a run printed, or None when stdout is not one JSON object."""
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def refusal(r):
    """Exit 1, a message on stderr, and nothing on stdout."""
    ok = (not r.timed_out and r.code == 1 and r.err.strip() != '' and r.out.strip() == ''
          and 'Traceback' not in r.err)
    return ok, describe(r) + f'; stdout {r.out[:60]!r}'


def printed(r, then):
    """``then(card)`` when the run exited 0 and printed one JSON card; else a failure."""
    if r.timed_out or r.code != 0:
        return False, describe(r)
    c = card_of(r)
    if c is None:
        return False, 'stdout is not one JSON object'
    return then(c)


def has_keys(c, keys):
    missing = [k for k in keys if k not in c]
    return not missing, f'missing keys {missing}'


def tree_state(root):
    """(relative path, size, mtime) of every file under ``root``, bytecode aside, so a
    run that writes into the repo shows up."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for name in filenames:
            if name.endswith('.pyc'):
                continue
            full = os.path.join(dirpath, name)
            st = os.stat(full)
            out.append((os.path.relpath(full, root), st.st_size, st.st_mtime_ns))
    return sorted(out)


def listing(folder):
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames + filenames:
            out.append(os.path.relpath(os.path.join(dirpath, name), folder))
    return sorted(out)


# ---- fixtures ------------------------------------------------------------------

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


def lte_iq():
    """0.3 s of a 10 MHz PCI 123 downlink at 20 MS/s, its carrier 300 kHz above 751.0 MHz.
    Seed 0; the frame starts at a seeded offset inside the file."""
    def make():
        iq, _ = lte_synth.synth_lte(bw_mhz=10, n_frames=30, pci=PCI, snr_db=20.0, seed=0, fs=FS)
        t = np.arange(len(iq)) / FS
        return (iq * np.exp(2j * np.pi * OFFSET * t)).astype(np.complex64)
    return once('lte-iq', make)


def lte_file():
    return once('lte-file', lambda: write_recording('lte', lte_iq(), FS, CENTRE))


def lte_short():
    """The first 0.2 s of the LTE capture: a 0.2 s synthetic file for the timing check."""
    return once('lte-short', lambda: write_recording('lte-short', lte_iq()[:int(0.2 * FS)],
                                                    FS, CENTRE))


def lte_raw():
    """The same capture as a bare .cfile, no .sigmf-meta: rate and centre come from options."""
    return once('lte-raw', lambda: write_recording('lte-raw', lte_iq(), FS, CENTRE, meta=False))


def lte_sdvr():
    """The same capture as an IQ Sceptre DVR (quantised, so scaled to avoid clipping)."""
    def make():
        folder = os.path.join(WORK, 'lte-sdvr')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'lte.sdvr')
        signals.write_sdvr(path, 0.3 * lte_iq(), FS, CENTRE)
        return path
    return once('lte-sdvr', make)


def negative_file(name):
    """0.2 s of one of the non-LTE signals, at 20 MS/s, centred on 751.0 MHz."""
    makers = {
        'noise': lambda: lte_negatives.synth_noise(seed=0, fs=FS, dur=0.2),
        'atsc_like': lambda: lte_negatives.synth_atsc_like(seed=0, fs=FS, dur=0.2),
        'uplink_like': lambda: lte_negatives.synth_uplink_like(seed=0, fs=FS, dur=0.2),
    }
    return once('neg-' + name, lambda: write_recording('neg-' + name, makers[name]()[0],
                                                      FS, CENTRE))


def sweep_file():
    def make():
        folder = os.path.join(WORK, 'sweep')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'sweep.sdvr')
        signals.write_sweep_sdvr(path)
        return path
    return once('sweep', make)


def pycrate_python():
    """A Python that imports the pycrate ASN.1 modules, or None.

    ``FMRX_TEST_PYCRATE_PYTHON`` names one; otherwise this interpreter is asked."""
    cand = os.environ.get('FMRX_TEST_PYCRATE_PYTHON') or sys.executable
    try:
        proc = subprocess.run([cand, '-c', 'from pycrate_asn1dir import RRCLTE'],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cand if proc.returncode == 0 else None


# The encoder for the synthetic SIB1. It runs under PYC_PY, the only place pycrate exists.
# It is NOT verified against a real pycrate install (none is available where it was
# written): the field names follow 36.331 and pycrate's generated RRC module.
SIB1_ENCODER = r'''
import sys
from pycrate_asn1dir import RRCLTE
sib1 = RRCLTE.SystemInformationBlockType1()
sib1.set_val({
    'cellAccessRelatedInfo': {
        'plmn-IdentityList': [{
            'plmn-Identity': {'mcc': [0, 0, 1], 'mnc': [0, 1]},
            'cellReservedForOperatorUse': 'notReserved',
        }],
        'trackingAreaCode': (16, 0x1234),
        'cellIdentity': (28, 0x0ABCD01),
        'cellBarred': 'notBarred',
        'intraFreqReselection': 'allowed',
        'csg-Indication': False,
    },
    'cellSelectionInfo': {'q-RxLevMin': -70},
    'freqBandIndicator': 13,
    'schedulingInfoList': [{'si-Periodicity': 'rf8', 'sib-MappingInfo': []}],
    'si-WindowLength': 'ms20',
    'systemInfoValueTag': 0,
})
sys.stdout.buffer.write(sib1.to_uper())
'''


def canned_payload():
    """The 32-byte transport block the stub returns: the encoded SIB1 padded with zeros
    (the decoder takes the most common payload of 32 bytes or longer), or 32 zero bytes
    when there is no pycrate (then no SIB1 is parsed)."""
    def make():
        if PYC_PY is None:
            return bytes(32)
        proc = subprocess.run([PYC_PY, '-c', SIB1_ENCODER], capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError('pycrate could not encode the test SIB1: '
                               + proc.stderr.decode(errors='replace').strip()[-200:])
        data = proc.stdout
        if len(data) > 32:
            raise RuntimeError(f'the encoded SIB1 is {len(data)} bytes, more than 32')
        return data + bytes(32 - len(data))
    return once('payload', make)


# ---- the stub outside program ----------------------------------------------------

# Test stub for run_pdsch.sh. Args: FILE PCI OFFSET NSF PORTS. It logs one JSON line per
# call to calls.jsonl beside itself, with what it decided. It decodes when the config says
# so: mode 'ok' decodes at ports 4 with offset != 0, except offsets in reject; mode 'never'
# decodes nothing. The decoded lines are the ones pdsch_ue prints; the dump record is the
# one the spec describes (0xA5, subframe, nbytes lo, nbytes hi, bytes).
STUB_PY = r'''
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(HERE, 'config.json')) as fh:
        cfg = json.load(fh)
    args = sys.argv[1:]
    if len(args) != 5:
        print('stub: want FILE PCI OFFSET NSF PORTS', file=sys.stderr)
        return 0
    path, pci, offset, nsf, ports = args
    off, prt = int(offset), int(ports)
    size = os.path.getsize(path) if os.path.isfile(path) else -1
    first = os.path.join(HERE, 'input.cf32')
    if size > 0 and not os.path.exists(first):
        shutil.copyfile(path, first)
    decoded = (cfg['mode'] == 'ok' and prt == 4 and off != 0 and off not in cfg['reject'])
    dump = os.environ.get('PDSCH_DUMP')
    with open(os.path.join(HERE, 'calls.jsonl'), 'a') as fh:
        fh.write(json.dumps({'file': path, 'size': size, 'pci': int(pci), 'offset': off,
                             'nsf': int(nsf), 'ports': prt, 'decoded': decoded,
                             'dump': dump}) + '\n')
    if not decoded:
        print('MIB not decoded')
        return 0
    for line in ('Decoded MIB. SFN: 7', ' - PCI:             123', ' - Nof ports:       4',
                 ' - PRB:             50', ' - Type:            FDD'):
        print(line)
    if dump:
        payload = bytes.fromhex(cfg['payload_hex'])
        with open(dump, 'ab') as fh:
            fh.write(bytes([0xA5, 5, len(payload) & 0xFF, len(payload) >> 8]) + payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''


def make_stub(name, mode='ok', reject=()):
    """A tools directory with a stub run_pdsch.sh, a config, and pyc/bin/python. The
    python is the one that has pycrate when there is one, else this interpreter (which
    lacks it), so the SIB1 path is always the one the environment allows."""
    folder = os.path.join(WORK, 'stub-' + name)
    os.makedirs(folder)
    stub_py = os.path.join(folder, 'stub.py')
    with open(stub_py, 'w') as fh:
        fh.write(STUB_PY)
    wrapper = os.path.join(folder, 'run_pdsch.sh')
    with open(wrapper, 'w') as fh:
        fh.write('#!/bin/sh\nexec %s %s "$@"\n' % (shlex.quote(sys.executable),
                                                  shlex.quote(stub_py)))
    os.chmod(wrapper, 0o755)
    with open(os.path.join(folder, 'config.json'), 'w') as fh:
        json.dump({'mode': mode, 'reject': [int(x) for x in reject],
                   'payload_hex': canned_payload().hex()}, fh)
    pyc_bin = os.path.join(folder, 'pyc', 'bin')
    os.makedirs(pyc_bin)
    os.symlink(PYC_PY or sys.executable, os.path.join(pyc_bin, 'python'))
    return folder


def calls_of(folder):
    path = os.path.join(folder, 'calls.jsonl')
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def decode_run(name, file_path, args=(), mode='ok', reject=()):
    """Run the tool with ``--decode`` against a fresh stub. Returns (run, card, calls, stub)."""
    stub = make_stub(name, mode, reject)
    r = run_tool([file_path] + list(args) + ['--decode'], tools=stub)
    return r, card_of(r), calls_of(stub), stub


def default_decode():
    """The positive run: the LTE capture, the right block centre, the stub decoding."""
    return once('decode-default', lambda: decode_run('ok', lte_file(),
                                                     ['--block-center', BLOCK_S]))


def first_offset():
    """The offset the tool tried first (the stub's first logged call)."""
    r, c, calls, _ = default_decode()
    if not calls:
        raise RuntimeError('the default decode made no stub call')
    return calls[0]['offset']


def retry_decode():
    """The stub refuses the first offset, so the tool must go on to the second."""
    return once('decode-retry', lambda: decode_run('retry', lte_file(),
                                                   ['--block-center', BLOCK_S],
                                                   reject=[first_offset()]))


def second_offset(o):
    return (o - HALF) % FRAME


# ---- groups --------------------------------------------------------------------

def group_structure():
    def positive():
        return once('positive', lambda: run_tool([lte_file(), '--block-center', BLOCK_S]))

    expect('A  exit 0 and one JSON object on stdout', lambda: printed(positive(), lambda c: (True, '')))
    expect('A  every JSON key the spec lists is present',
           lambda: printed(positive(), lambda c: has_keys(c, KEYS)))
    expect('A  verdict lte with --block-center', lambda: printed(
        positive(), lambda c: (c.get('verdict') == 'lte', f"verdict {c.get('verdict')!r}")))
    expect('A  pci 123 with --block-center', lambda: printed(
        positive(), lambda c: (c.get('pci') == PCI, f"pci {c.get('pci')!r}")))
    expect('A  nid2 is PCI mod 3 and nid1 is PCI div 3', lambda: printed(
        positive(), lambda c: (c.get('nid2') == PCI % 3 and c.get('nid1') == PCI // 3,
                               f"nid2 {c.get('nid2')!r}, nid1 {c.get('nid1')!r}")))
    expect('A  nfft 1024 and bandwidth_mhz 10', lambda: printed(
        positive(), lambda c: (c.get('nfft') == 1024 and c.get('bandwidth_mhz') == 10,
                               f"nfft {c.get('nfft')!r}, bandwidth {c.get('bandwidth_mhz')!r}")))
    expect('A  block_center_hz is the given block centre (751.3 MHz)', lambda: printed(
        positive(), lambda c: near(c.get('block_center_hz'), BLOCK, 0.5)))
    expect('A  file names the input (basename lte.cfile)', lambda: printed(
        positive(), lambda c: (os.path.basename(str(c.get('file'))) == 'lte.cfile',
                               f"file {c.get('file')!r}")))
    expect('A  rate_sps is a positive number', lambda: printed(
        positive(), lambda c: (isinstance(c.get('rate_sps'), (int, float))
                               and c['rate_sps'] > 0, f"rate_sps {c.get('rate_sps')!r}")))
    expect('A  duration_s is the default 0.2 s span of the 0.3 s file', lambda: printed(
        positive(), lambda c: near(c.get('duration_s'), 0.2, 0.005)))
    expect('A  half_frame and frame_offset_samples are integers', lambda: printed(
        positive(), lambda c: (isinstance(c.get('half_frame'), int)
                               and isinstance(c.get('frame_offset_samples'), int),
                               f"half_frame {c.get('half_frame')!r}, "
                               f"frame_offset_samples {c.get('frame_offset_samples')!r}")))
    def checks_shape(c):
        checks = c.get('checks')
        ok = (isinstance(checks, list) and len(checks) >= 2
              and all(isinstance(k, dict) and {'name', 'ran', 'passed'} <= set(k)
                      for k in checks)
              and {'cp-autocorr', 'pss'} <= {k.get('name') for k in checks if k.get('passed')})
        return ok, f'checks {str(checks)[:300]}'
    expect('A  checks: records with name, ran, passed; cp-autocorr and pss passed',
           lambda: printed(positive(), checks_shape))
    expect('A  decode is null when --decode is not asked', lambda: printed(
        positive(), lambda c: (c.get('decode') is None, f"decode {c.get('decode')!r}")))

    def without_centre():
        r = run_tool([lte_file()])
        return printed(r, lambda c: (
            c.get('verdict') != 'lte' or c.get('pci') is None,
            f"verdict {c.get('verdict')!r}, pci {c.get('pci')!r} without --block-center"))
    expect('A  without --block-center: verdict not lte or pci None', without_centre)

    def default_centre():
        r = run_tool([lte_file()])
        return printed(r, lambda c: near(c.get('block_center_hz'), CENTRE, 0.5))
    expect('A  without --block-center: block_center_hz is the file centre', default_centre)

    def raw_options():
        r = run_tool([lte_raw(), '--rate', str(int(FS)), '--center', str(int(CENTRE)),
                      '--block-center', BLOCK_S])
        return printed(r, lambda c: (c.get('verdict') == 'lte' and c.get('pci') == PCI,
                                     f"verdict {c.get('verdict')!r}, pci {c.get('pci')!r}"))
    expect('A  a bare .cfile with --rate and --center: lte, pci 123', raw_options)

    def span():
        r = run_tool([lte_file(), '--block-center', BLOCK_S, '--t0', '0.1', '--t1', '0.2'])
        return printed(r, lambda c: (near(c.get('duration_s'), 0.1, 0.005)[0]
                                     and c.get('verdict') == 'lte',
                                     f"duration {c.get('duration_s')!r}, "
                                     f"verdict {c.get('verdict')!r}"))
    expect('A  --t0 0.1 --t1 0.2: duration 0.1 s, still lte', span)

    def sdvr_iq():
        r = run_tool([lte_sdvr(), '--block-center', BLOCK_S])
        return printed(r, lambda c: (c.get('verdict') == 'lte' and c.get('pci') == PCI,
                                     f"verdict {c.get('verdict')!r}, pci {c.get('pci')!r}"))
    expect('A  an IQ .sdvr with --block-center: lte, pci 123', sdvr_iq)

    def five_mhz():
        """The 5 MHz class: a 5 MHz PCI 123 downlink with the same 300 kHz offset."""
        def make():
            iq, _ = lte_synth.synth_lte(bw_mhz=5, n_frames=30, pci=PCI, snr_db=20.0, seed=0,
                                        fs=FS)
            t = np.arange(len(iq)) / FS
            return write_recording('lte-5mhz', (iq * np.exp(2j * np.pi * OFFSET * t)), FS, CENTRE)
        path = once('lte-5mhz', make)
        r = run_tool([path, '--block-center', BLOCK_S])
        return printed(r, lambda c: (c.get('verdict') == 'lte' and c.get('nfft') == 512
                                     and c.get('bandwidth_mhz') == 5,
                                     f"verdict {c.get('verdict')!r}, nfft {c.get('nfft')!r}, "
                                     f"bandwidth {c.get('bandwidth_mhz')!r}"))
    expect('A  5 MHz class: nfft 512, bandwidth_mhz 5', five_mhz)


def group_negatives():
    for name in ('noise', 'atsc_like', 'uplink_like'):
        def check(name=name):
            r = run_tool([negative_file(name)])
            return printed(r, lambda c: (
                c.get('verdict') != 'lte' and c.get('pci') is None and has_keys(c, KEYS)[0],
                f"verdict {c.get('verdict')!r}, pci {c.get('pci')!r}"))
        expect(f'B  {name}: exit 0, verdict not lte, pci None', check)


def group_errors():
    expect('C  missing file: exit 1, message on stderr, nothing on stdout',
           lambda: refusal(run_tool([os.path.join(WORK, 'no-such-file.cfile')])))
    expect('C  a sweep .sdvr: exit 1, message on stderr, nothing on stdout',
           lambda: refusal(run_tool([sweep_file()])))
    expect('C  an unknown option: exit 1 (not argparse 2), message on stderr',
           lambda: refusal(run_tool([lte_file(), '--no-such-option'])))
    expect('C  a non-number --block-center: exit 1',
           lambda: refusal(run_tool([lte_file(), '--block-center', 'abc'])))


def group_no_tools():
    def empty():
        r = run_tool([lte_file(), '--block-center', BLOCK_S, '--decode'], tools=EMPTY)
        if r.timed_out or r.code != 2:
            return False, describe(r)
        named = any(n in r.err for n in ('run_pdsch.sh', 'pdsch_ue', 'pyc'))
        return (named and 'Traceback' not in r.err), f'stderr {r.err[-200:]!r}'
    expect('D  --decode, empty FMRX_LTE_TOOLS: exit 2, stderr names the missing program', empty)

    def missing_dir():
        r = run_tool([lte_file(), '--block-center', BLOCK_S, '--decode'],
                     tools=os.path.join(WORK, 'no-such-tools-dir'))
        return (r.code == 2 and not r.timed_out and 'Traceback' not in r.err, describe(r))
    expect('D  --decode, FMRX_LTE_TOOLS names no folder: exit 2', missing_dir)


def group_decode():
    def not_lte():
        r, c, calls, _ = decode_run('notlte', negative_file('noise'))
        if r.timed_out or r.code != 0 or c is None:
            return False, describe(r)
        d = c.get('decode')
        ok = (isinstance(d, dict) and d.get('ran') is False
              and isinstance(d.get('reason'), str) and d['reason'] != '')
        return (ok and calls == [] and c.get('verdict') != 'lte'), \
            f"decode {d!r}, stub calls {len(calls)}"
    expect('E  --decode on a not-lte file: decode {ran: false, reason}, exit 0, stub not called',
           not_lte)

    def decoded_mib():
        r, c, _, _ = default_decode()
        if r.timed_out or r.code != 0 or c is None:
            return False, describe(r)
        d = c.get('decode')
        if not isinstance(d, dict):
            return False, f'decode {d!r}'
        mib = d.get('mib') or {}
        want = {'pci': PCI, 'ports': 4, 'prb': 50, 'type': 'FDD', 'sfn': 7}
        got = {k: mib.get(k) for k in want}
        return (got == want and all(k in mib for k in MIB_KEYS),
                f'mib {mib!r}, want {want}')
    expect('E  decode.mib: pci 123, ports 4, prb 50, FDD, sfn 7, all keys present', decoded_mib)

    def decode_keys():
        r, c, _, _ = default_decode()
        d = c.get('decode') if c else None
        return has_keys(d if isinstance(d, dict) else {}, DEC_KEYS)
    expect('E  decode has mib, offset_samples, sib1, tried', decode_keys)

    def attempts():
        r, c, calls, _ = default_decode()
        d = c.get('decode') if c else None
        tried = d.get('tried') if isinstance(d, dict) else None
        if not isinstance(tried, list) or len(tried) != len(calls):
            return False, f'tried {tried!r}, stub calls {len(calls)}'
        for t, call in zip(tried, calls):
            if not isinstance(t, dict) or (t.get('offset'), t.get('ports'), t.get('decoded')) \
                    != (call['offset'], call['ports'], call['decoded']):
                return False, f'tried entry {t!r} is not the stub call {call!r}'
        if not calls or not calls[-1]['decoded'] or any(x['decoded'] for x in calls[:-1]):
            return False, 'the last attempt must be the only success'
        return (d.get('offset_samples') == calls[-1]['offset'],
                f"offset_samples {d.get('offset_samples')!r}, success at {calls[-1]['offset']}")
    expect('E  tried matches the stub calls (order, ports, offset, outcome), success last', attempts)

    def offsets_and_ports():
        r, c, calls, _ = default_decode()
        if not calls:
            return False, 'no stub call'
        o1 = calls[0]['offset']
        o2 = second_offset(o1)
        if calls[0]['ports'] != 4:
            return False, f"first attempt ports {calls[0]['ports']}, want 4"
        for call in calls:
            if call['offset'] not in (o1, o2) or not 0 <= call['offset'] < FRAME:
                return False, f"offset {call['offset']} is not o or (o - 76800) mod 153600"
            if call['ports'] not in (4, 2, 1):
                return False, f"ports {call['ports']}"
        for off in (o1, o2):
            seq = [c_['ports'] for c_ in calls if c_['offset'] == off]
            if seq != sorted(seq, reverse=True) or len(set(seq)) != len(seq):
                return False, f'ports {seq} at offset {off}, want a 4, 2, 1 order'
        return True, ''
    expect('E  attempts: offsets o and o-76800 mod 153600, ports 4, 2, 1 in order', offsets_and_ports)

    def file_given():
        r, c, calls, stub = default_decode()
        if not calls:
            return False, 'no stub call'
        size = calls[0]['size']
        n_samples = size / 8.0
        if size % 8 != 0:
            return False, f'size {size} is not a multiple of 8 (cf32)'
        if abs(n_samples / RATE_DEC - 0.2) > 0.01:
            return False, f'{n_samples:.0f} samples, want 0.2 s at 15.36 MS/s'
        if any(x['size'] != size for x in calls):
            return False, 'the same file should go to every attempt'
        if any(x['pci'] != PCI for x in calls):
            return False, 'pci argument is not 123'
        if any(x['nsf'] <= 0 for x in calls):
            return False, 'nsf is not positive'
        return True, ''
    expect('E  the file given to run_pdsch.sh: cf32, 0.2 s at 15.36 MS/s, PCI 123', file_given)

    def file_content():
        r, c, calls, stub = default_decode()
        path = os.path.join(stub, 'input.cf32')
        if not os.path.isfile(path):
            return False, 'the stub did not keep its input'
        x = np.fromfile(path, dtype='<c8')
        ok = bool(np.all(np.isfinite(x))) and float(np.mean(np.abs(x) ** 2)) > 0.0
        return ok, f'{x.size} samples, mean power {float(np.mean(np.abs(x) ** 2)):.3g}'
    expect('E  the file given to run_pdsch.sh is finite, non-silent cf32', file_content)

    def retry():
        r, c, calls, _ = retry_decode()
        if r.timed_out or r.code != 0 or c is None:
            return False, describe(r)
        d = c.get('decode')
        tried = d.get('tried') if isinstance(d, dict) else None
        if not calls or not isinstance(tried, list) or len(tried) != len(calls):
            return False, f'tried {tried!r}, stub calls {len(calls)}'
        if not any(not t.get('decoded') for t in tried[:-1]) or not tried[-1].get('decoded'):
            return False, f'want a failed attempt before the success: {tried!r}'
        o1 = first_offset()
        if calls[0]['offset'] != o1 or calls[0]['decoded']:
            return False, f"first attempt {calls[0]!r}, want offset {o1} refused"
        if d.get('offset_samples') != second_offset(o1):
            return False, f"offset_samples {d.get('offset_samples')!r}, want {second_offset(o1)}"
        if any(x['offset'] not in (o1, second_offset(o1)) for x in calls):
            return False, 'offsets are not o and o-76800 mod 153600'
        return True, ''
    expect('E  first offset refused: a failed attempt, then success at o-76800 mod 153600', retry)

    def ports_only_4():
        r, c, calls, _ = decode_run('ports4', lte_file(), ['--block-center', BLOCK_S,
                                                           '--ports', '4'],
                                    reject=[first_offset()])
        if r.timed_out or r.code != 0 or c is None:
            return False, describe(r)
        if [x['ports'] for x in calls] != [4] * len(calls) or len(calls) != 2:
            return False, f"stub calls {[(x['offset'], x['ports']) for x in calls]}"
        return (c['decode'].get('offset_samples') == second_offset(first_offset())
                and calls[-1]['decoded'] and not calls[0]['decoded'],
                f'calls {[(x["offset"], x["ports"], x["decoded"]) for x in calls]}')
    expect('E  --ports 4: only ports 4 is tried', ports_only_4)

    def never():
        r, c, calls, _ = decode_run('never', lte_file(), ['--block-center', BLOCK_S],
                                    mode='never')
        if r.timed_out or r.code != 0 or c is None:
            return False, describe(r)
        if not calls:
            return False, 'the stub was not called'
        o1 = calls[0]['offset']
        want = {(o, p) for o in (o1, second_offset(o1)) for p in (4, 2, 1)}
        got = [(x['offset'], x['ports']) for x in calls]
        return (len(got) == 6 and set(got) == want and not any(x['decoded'] for x in calls),
                f'attempts {got}')
    expect('E  a stub that never decodes: 6 distinct attempts, exit 0', never)

    def sib1():
        r, c, _, _ = default_decode()
        d = c.get('decode') if c else None
        if not isinstance(d, dict):
            return False, f'decode {d!r}'
        sib = d.get('sib1')
        if PYC_PY is None:
            ok = sib is None and isinstance(d.get('sib1_reason'), str) \
                and 'pycrate' in d['sib1_reason'].lower()
            return ok, f'sib1 {sib!r}, sib1_reason {d.get("sib1_reason")!r}'
        if not isinstance(sib, dict):
            return False, f'sib1 {sib!r}'
        want = {'plmns': [{'mcc': '001', 'mnc': '01'}], 'tac': 0x1234,
                'cell_identity': 0x0ABCD01, 'enb_id': 0x0ABCD, 'cell_id': 1, 'freq_band': 13}
        got = {k: sib.get(k) for k in want}
        keys_ok = all(k in sib for k in ('cell_barred', 'q_rx_lev_min'))
        return (got == want and keys_ok), f'sib1 {sib!r}, want {want}'
    if PYC_PY is None:
        print('SKIP sib1 parse (pycrate not available)')
    expect('E  sib1: null with a reason naming pycrate (no pycrate here), or the SIB1 fields',
           sib1)


def group_cleanup():
    def temp_and_repo():
        before = listing(TMP)
        repo_before = tree_state(os.path.join(ROOT, 'tools'))
        stub = make_stub('clean')
        r = run_tool([lte_file(), '--block-center', BLOCK_S, '--decode'], tools=stub)
        after = listing(TMP)
        repo_after = tree_state(os.path.join(ROOT, 'tools'))
        if r.timed_out or r.code != 0:
            return False, describe(r)
        calls = calls_of(stub)
        if not calls:
            return False, 'the stub was not called'
        leftover = [p for p in (calls[0]['file'], calls[0]['dump']) if p]
        if not all(os.path.realpath(p).startswith(os.path.realpath(TMP) + os.sep)
                   for p in leftover):
            return False, f'the cf32 and dump paths are not in TMPDIR: {leftover!r}'
        if any(os.path.exists(p) for p in (calls[0]['file'],)):
            return False, 'the cf32 is still there after the run'
        if before != after:
            return False, f'temp folder changed: before {before!r}, after {after!r}'
        return repo_before == repo_after, 'the tools tree changed during the run'
    expect('F  --decode: temp folder identical before and after; cf32 deleted; repo unchanged',
           temp_and_repo)


def group_timing():
    def timed():
        r = run_tool([lte_short(), '--block-center', BLOCK_S])
        if r.timed_out or r.code != 0:
            return False, describe(r)
        c = card_of(r)
        ok = c is not None and c.get('verdict') == 'lte' and c.get('pci') == PCI
        print(f'  info  iq-lte on the 0.2 s synthetic file: {r.seconds:.2f} s')
        return (ok and r.seconds < SPEED_LIMIT_S,
                f'{r.seconds:.2f} s, verdict {c and c.get("verdict")!r}')
    expect(f'G  the 0.2 s synthetic file in under {SPEED_LIMIT_S:g} s', timed)


def group_imports():
    allowed_std = set(sys.stdlib_module_names)

    def check(path):
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top not in allowed_std and top not in ('numpy', 'fm_receiver'):
                        bad.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                top = (node.module or '').split('.')[0]
                if top not in allowed_std and top not in ('numpy', 'fm_receiver'):
                    bad.append(node.module)
        return (not bad), f'imports outside numpy, stdlib and fm_receiver: {bad}'

    expect('H  tools/iq-lte imports only numpy, the standard library and fm_receiver',
           lambda: check(TOOL) if os.path.isfile(TOOL) else (False, 'tools/iq-lte does not exist'))
    expect('H  tools/fm_receiver/iqlte_decode.py imports only numpy, stdlib and fm_receiver',
           lambda: check(IQLTE_DECODE) if os.path.isfile(IQLTE_DECODE)
           else (False, 'iqlte_decode.py does not exist'))


GROUPS = [group_structure, group_negatives, group_errors, group_no_tools, group_decode,
          group_cleanup, group_timing, group_imports]


def main():
    global WORK, TMP, EMPTY, PYC_PY
    WORK = tempfile.mkdtemp(prefix='iqlte-test-')
    TMP = os.path.join(WORK, 'tmp')
    EMPTY = os.path.join(WORK, 'empty-tools')
    os.makedirs(TMP)
    os.makedirs(EMPTY)
    try:
        PYC_PY = pycrate_python()
        if PYC_PY is None:
            print('  note  no Python with pycrate: the SIB1 field checks are not run')
        for group in GROUPS:
            try:
                group()
            except Exception as exc:
                report(f'{group.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
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
