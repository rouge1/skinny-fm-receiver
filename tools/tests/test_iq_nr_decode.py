"""Phase n2 acceptance: ``tools/iq-nr --decode`` (MIB, SIB1, other SI, SIB9 network time).

Spec: ``tools/swarm-records/specs/n2.md``. The decoder (``tools/fm_receiver/iqnr_decode.py``)
and the ``--decode`` option do not exist yet, so these checks are expected to fail (or skip)
until they do. The tests are the spec: they pin only what the spec pins.

Nothing outside the repo's tools runs but a STUB. ``FMRX_LTE_TOOLS`` always names a temporary
folder: a stub ``run_nr.sh`` (a Python script behind an sh wrapper) that prints the frozen
contract lines, records its argv and the file it was given, and serves the blocks the test
encodes. The real outside program is never named, run or searched for. An empty
``FMRX_LTE_TOOLS`` would fall back to the real tools folder in the decoder, so it is never
left empty.

The pycrate encoder (``pycrate_asn1dir.RRCNR``) runs in a subprocess under the interpreter
``FMRX_TEST_PYCRATE_PYTHON`` (else this one). Checks that need an encoded SIB1 or SI message
SKIP when no such interpreter imports pycrate; they do not fail for want of it.

Checks (one behaviour each; ok / FAIL / SKIP lines):
  S0  pycrate encodes the fixture blocks and decodes them back to the made-up values
      (no decoder code is used here)
  A   the stub contract: five positional args, cf32 at 23.04 MS/s, the SSB at 0 Hz, the span
      asked for to 1 ms; the decoder's temporary files gone; the repo unchanged
  B   pass 2 (-t and -a) only when SIB1 schedules other SI
  C   the MIB copied; PLMNs; TAC; cell identity; cell barred; carrier; SI schedule
  D   network time: utc, gps, local offset, DST, reference_time.gps_reading
  E   reasons: time null with the matching time_reason, exit 0
  F   missing tools: no run_nr.sh gives exit 2 naming it; no pycrate gives sib1 null with a
      reason naming pycrate
  G   options: --decode-seconds 20 cut to 10 with a reason; 0 and negative rejected; output
      without --decode equals a fixed baseline (sha256 and length hardcoded below)
  H   child peak RSS on a 2 s, 40 MS/s file under 900 MB
  R1-R4 robustness probes (R1-R3 need pycrate, R4 does not)
  P   the program read another PCI than the SSB entry's (a stronger cell on the same SSB
      frequency) and refuses -P (an older program): mib and sib1 null, decoded_pci and a
      reason naming both, no pass 2
  T   the same, with a program that takes -P PCI: the retry aimed at the PCI is this cell's
      decode (pass 2 keeps -P); a retry without a MIB ends as P
  U   other SI that SIB1 does not schedule, sent in SIB1's slots with sii 0 (as one cell did
      on air): heard, listed in si.unscheduled, its SIB9 gives the time, no second call;
      sent in its own slots (sii 1 blocks failing in pass 1): pass 2 runs and hears it

Decisions where the spec is open (also in the report):
  - Point A (spec amendment 2026-10-23 (b)): point_a_hz = ssb_hz - 120*ssb_scs_hz - k_SSB*15e3
    - offsetToPointA*12*15e3, k_SSB being the MIB's ssb_subcarrier_offset. SIB1 carries
    offsetToPointA only; the stub's NR_CORESET0 pointA_hz is a stand-in at 600.0 MHz, not the
    cell's point A, and the decode must not take it as the source (check C fails until it doesn't).
  - A missing pycrate interpreter: the decode still exits 0 and reports the MIB (acceptance F,
    as the brief says); the spec's Command section says exit 2, which is the missing
    run_nr.sh case only.
  - The SIB9 fields follow the spec's names (timeInfoUTC, leapSeconds, localTimeOffset,
    dayLightSavingTime; referenceTimeInfo-r16 with refDays, refSeconds, refMilliSeconds,
    refTenNanoSeconds). The pycrate nesting of the r16 field is not checked here: the first
    S0 run with pycrate is the check of it.

Run:  systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 env QT_QPA_PLATFORM=offscreen
      PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_nr_decode.py
(about two minutes; the pytest form, ``python -m pytest tools/tests/test_iq_nr_decode.py``, runs the
same functions). FMRX_TEST_PYCRATE_PYTHON unset: S0, B, C, D, E1-E3, R1-R3 skip here.
"""

import atexit
import calendar
import datetime
import hashlib
import itertools
import json
import os
import re
import shlex
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))   # tools/: `tests` and `fm_receiver`

from tests import nr_synth  # noqa: E402
from fm_receiver import iqnr  # noqa: E402

try:
    import pytest
    SkipTest = pytest.skip.Exception
except ImportError:                          # run as a script
    class SkipTest(Exception):
        pass

TOOL = os.path.join(ROOT, 'tools', 'iq-nr')
TIMEOUT = 180
RATE = 23040000.0                            # the outside program's sample rate
RSS_LIMIT_MB = 900.0
RSS_UNIT = 1 if sys.platform == 'darwin' else 1024   # ru_maxrss: bytes on macOS, KB on Linux
DECODE_ARGS = ('--decode', '--decode-seconds', '0.25')

# ---- made-up fixtures (tools/swarm-records/specs/n2.md) -------------------------------------

PCI = 123
SSB_HZ = 600.15e6
CENTRE_HZ = 600.0e6
POINT_A_ARFCN = 119310                       # 596.550 MHz
TRUE_POINT_A_HZ = 596.550e6                  # the cell's carrier point A (k_SSB 0)
STUB_CORESET0_POINT_A_HZ = 600.0e6           # the stub's NR_CORESET0 pointA_hz: a stand-in, not point A
OFFSET_TO_POINT_A = 10
K_SSB_K6 = 6                                 # the MIB copy for the k_SSB 6 case
OFFSET_K6_TO_POINT_A = 13
POINT_A_K6_ARFCN = 119184                    # 595.920 MHz
POINT_A_K6_MHZ = 595.920
TIME_ALLOC = (86, 46)                        # SLIVs in 0..127: 14*(L-1)+S for (S,L)=(2,7),(4,4)
PLMNS = ['001-01', '001-001']
TAC = 0x1234                                 # 4660
CELL_IDENTITY = 0x123456789                  # 4886718345
SI_WINDOW_SLOTS = 20
# SIB9 timeInfoUTC: 2030-01-02 03:04:05.670 UTC, in 10 ms units from 1900-01-01 (NTP epoch)
UNIX_2030 = calendar.timegm((2030, 1, 2, 3, 4, 5))
NTP_EPOCH_OFFSET_S = 2208988800
UTC_UNITS = (UNIX_2030 + NTP_EPOCH_OFFSET_S) * 100 + 67
UTC_ISO = '2030-01-02T03:04:05.670+00:00'
GPS_ISO = '2030-01-02T03:04:23.670+00:00'    # UTC + leapSeconds 18
LEAP = 18
LOCAL_OFFSET_UNITS = -20                     # 15 minutes each: -5 h
DST_HOURS = 1
GPS_EPOCH = datetime.date(1980, 1, 6)
REF_DAYS = (datetime.date(2030, 1, 2) - GPS_EPOCH).days
REF_SECONDS = 3 * 3600 + 4 * 60 + 23
REF_MS = 670
REF_TENNS = 0

# The no-decode baseline: tools/iq-nr on the 100 ms file below, run from its own folder as
# ``nr.cfile`` (the output names the file, so the name must not depend on the temp folder).
# Hardcoded from the tool as it stood before this phase, twice identical.
BASELINE_SHA256 = 'c4b7872bbff86abf18db1c52cf286a8f0e782d17095ae774530cf33bcaa4c396'
BASELINE_BYTES = 799

MIB = {'pci': PCI, 'sfn': 0, 'ssb_index': 0, 'half_frame': 0, 'scs_common_khz': 15,
       'ssb_subcarrier_offset': 0, 'dmrs_typeA_position': 'pos2', 'coreset0_index': 0,
       'search_space0_index': 0, 'cell_barred': False, 'intra_freq_reselection': 'allowed',
       'ssb_sample': 1000, 'cfo_hz': 150.0, 'snr_db': 25.0}
PLACEHOLDER_HEX = '00' * 8                   # a block that does not parse, for tests that need none

# ---- harness ----------------------------------------------------------------------------------

_MEMO = {}
_COUNTER = itertools.count()
_WORK = None


def work():
    """The one temporary folder of this run; removed at exit."""
    global _WORK
    with _GUARD:
        if _WORK is None:
            _WORK = tempfile.mkdtemp(prefix='n2-tests-')
            atexit.register(shutil.rmtree, _WORK, True)
    return _WORK


_LOCKS = {}
_GUARD = threading.Lock()


def memo(key, make):
    """Run ``make()`` once; a failure (or a skip) is kept and raised again for each check.

    Tests run in threads: one lock per key, so a shared decode runs once and the others wait.
    """
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        if key not in _MEMO:
            try:
                _MEMO[key] = (True, make())
            except Exception as exc:         # noqa: BLE001 - kept and re-raised
                _MEMO[key] = (False, exc)
        ok, value = _MEMO[key]
    if not ok:
        raise value
    return value


def skip(why):
    raise SkipTest(why)


def check_code(run, want, what):
    if run.code != want:
        tail = run.err.strip().splitlines()[-1:] if run.err else ['nothing on stderr']
        raise AssertionError('%s: want exit %s, got %s (%s)' % (what, want, run.code, tail[0]))


def decode_of(card):
    """The ``decode`` object of the SSB with PCI 123, or None (no fallback to another SSB)."""
    for entry in card.get('ssbs') or []:
        if entry.get('pci') == PCI:
            return entry.get('decode')
    return None


def need_decode(run):
    """The decode object of a successful run; fails with the run's own message otherwise."""
    if run.code != 0 or run.card is None:
        raise AssertionError('iq-nr --decode: exit %s, %s' % (run.code, run.err.strip()[-200:]
                                                              or run.out.strip()[:200]))
    dec = decode_of(run.card)
    if dec is None:
        raise AssertionError('no SSB with a decode key in the output (ssbs: %d)'
                             % len(run.card.get('ssbs') or []))
    return dec


def repo_status():
    """``git status``, ignored files included (a stray .cf32 is ignored by .gitignore), less
    Python's caches."""
    proc = subprocess.run(['git', '-C', ROOT, 'status', '--porcelain', '--ignored'],
                          capture_output=True, text=True)
    return '\n'.join(line for line in proc.stdout.splitlines() if '__pycache__' not in line)


def clean_env(tools, tmp):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['FMRX_LTE_TOOLS'] = tools            # never empty: an empty value means the real tools
    env['FMRX_CONFIG'] = os.path.join(work(), 'config.json')
    env['TMPDIR'] = tmp
    return env


class Run:
    """One finished run of the tool: exit code, stdout, stderr, the stub's calls, what is left."""

    def __init__(self, code, out, err, calls, tmp, tools, status):
        self.code = code
        self.out = out
        self.err = err
        self.calls = calls
        self.tmp = tmp
        self.tools = tools
        self.status = status
        self.card = None
        if code == 0:
            try:
                self.card = json.loads(out)
            except ValueError:
                self.card = None


def write_cfile(folder, name, iq, rate, centre):
    base = os.path.join(folder, name)
    np.asarray(iq, dtype=np.complex64).tofile(base + '.cfile')
    with open(base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate},
                   'captures': [{'core:sample_start': 0, 'core:frequency': centre}],
                   'annotations': []}, fh)
    return base + '.cfile'


def capture():
    """300 ms of an NR cell at 20 MS/s: PCI 123, SSB at 600.15 MHz, centre 600.0 MHz."""
    def make():
        folder = os.path.join(work(), 'capture')
        os.makedirs(folder)
        iq, _, _ = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                     period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                     dur_ms=300, fs=20e6, seed=0)
        write_cfile(folder, 'nr', iq, 20e6, CENTRE_HZ)
        return folder
    return memo('capture', make)


# ---- the pycrate interpreter and the encoded fixtures -----------------------------------------

def pycrate_python():
    """The interpreter that imports pycrate's RRCNR, or None."""
    def make():
        cand = os.environ.get('FMRX_TEST_PYCRATE_PYTHON') or sys.executable
        try:
            proc = subprocess.run([cand, '-c', 'from pycrate_asn1dir import RRCNR'],
                                  capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return cand if proc.returncode == 0 else None
    return memo('pycrate', make)


# Runs under the pycrate interpreter. Builds each fixture block as a BCCH-DL-SCH-Message
# (UPER), decodes it back and prints both, one object per job. Made-up values only. A job that
# fails reports its own traceback tail; the others still run.
ENCODER = r'''
import json, sys, traceback
from pycrate_asn1dir import RRCNR

MSG = RRCNR.NR_RRC_Definitions.BCCH_DL_SCH_Message


def plain(v):
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def plmn(mcc, mnc):
    return {'mcc': [int(d) for d in mcc], 'mnc': [int(d) for d in mnc]}


def info(plmns):
    return {'plmn-IdentityList': [plmn(m, n) for m, n in plmns],
            'trackingAreaCode': (0x1234, 24),
            'cellIdentity': (0x123456789, 36),
            'cellReservedForOperatorUse': 'notReserved'}


def scc_full(offset=10):
    return {'downlinkConfigCommon': {
                'pcch-Config': {'defaultPagingCycle': 'rf32',
                                'nAndPagingFrameOffset': ('quarterT', 1), 'ns': 'four'},
                'bcch-Config': {'modificationPeriodCoeff': 'n2'},
                'frequencyInfoDL': {
                    'frequencyBandList': [{'freqBandIndicatorNR': 78}],
                    'offsetToPointA': offset,
                    'scs-SpecificCarrierList': [{'offsetToCarrier': 0, 'subcarrierSpacing': 'kHz15',
                                                 'carrierBandwidth': 106}]},
                'initialDownlinkBWP': {
                    'genericParameters': {'locationAndBandwidth': 13750, 'subcarrierSpacing': 'kHz15'},
                    'pdsch-ConfigCommon': ('setup', {'pdsch-TimeDomainAllocationList': [
                        {'k0': 0, 'mappingType': 'typeA', 'startSymbolAndLength': 86},
                        {'k0': 0, 'mappingType': 'typeA', 'startSymbolAndLength': 46}]})}},
            'ssb-PositionsInBurst': {'inOneGroup': (0xA0, 8)},
            'ssb-PeriodicityServingCell': 'ms20',
            'ss-PBCH-BlockPower': 0}


def scc(tdra=True, offset=10):
    out = scc_full(offset)
    if not tdra:
        del out['downlinkConfigCommon']['initialDownlinkBWP']['pdsch-ConfigCommon']
    return out


def sched(variant):
    sib2 = {'si-Periodicity': 'rf16', 'sib-MappingInfo': [{'type': 'sibType2'}],
            'si-BroadcastStatus': 'broadcasting'}
    sib9 = {'si-Periodicity': 'rf32', 'sib-MappingInfo': [{'type': 'sibType9'}],
            'si-BroadcastStatus': 'notBroadcasting' if variant == 'not_broadcast' else 'broadcasting'}
    if variant == 'no_sib9':
        return [sib2]
    return [sib2, sib9]


def sib1(variant):
    if variant == 'three_plmn':
        infos = [info([('001', '01')]), info([('001', '001')]), info([('999', '02')])]
    else:
        infos = [info([('001', '01'), ('001', '001')])]
    out = {'cellAccessRelatedInfo': {'plmn-IdentityInfoList': infos},
           'servingCellConfigCommon': scc(variant != 'no_tdra', 13 if variant == 'offset13' else 10)}
    if variant != 'none':
        out['si-SchedulingInfo'] = {'schedulingInfoList': sched(variant), 'si-WindowLength': 's20'}
    return out


def sib2():
    return {'cellReselectionInfoCommon': {'q-Hyst': 'dB4'},
            'cellReselectionServingFreqInfo': {'threshServingLowP': 10, 'cellReselectionPriority': 4},
            'intraFreqCellReselectionInfo': {'q-RxLevMin': -60, 's-IntraSearchP': 10,
                                             'deriveSSB-IndexFromCell': False, 't-ReselectionNR': 2}}


def sib9(payload, with_ref):
    out = {'timeInfo': {'timeInfoUTC': payload['utc'], 'dayLightSavingTime': (1, 2),
                        'leapSeconds': 18, 'localTimeOffset': -20}}
    if with_ref:
        r = payload['ref']
        out['referenceTimeInfo-r16'] = {'time-r16': {'refDays-r16': r[0], 'refSeconds-r16': r[1],
                                                     'refMilliSeconds-r16': r[2],
                                                     'refTenNanoSeconds-r16': r[3]}}
    return out


def si(payload, variant):
    entries = [('sib2', sib2())]
    if variant == 'sib2_sib9':
        entries.append(('sib9', sib9(payload, True)))
    elif variant == 'sib2_sib9_noref':
        entries.append(('sib9', sib9(payload, False)))
    return ('systemInformation', {'criticalExtensions': ('systemInformation',
                                                         {'sib-TypeAndInfo': entries})})


payload = json.loads(sys.argv[1])
out = []
for key, kind, variant in payload['jobs']:
    try:
        if kind == 'sib1':
            body = ('systemInformationBlockType1', sib1(variant))
        else:
            body = si(payload, variant)
        MSG.set_val({'message': ('c1', body)})
        data = MSG.to_uper()
        MSG.from_uper(data)
        out.append({'key': key, 'hex': data.hex(), 'plain': plain(MSG.get_val())})
    except Exception:
        sys.stderr.write('encode %s failed:\n%s' % (key, traceback.format_exc()))
        out.append({'key': key, 'error': traceback.format_exc()[-400:]})
print(json.dumps(out))
'''

JOBS = [
    ('sib1_full', 'sib1', 'full'),
    ('sib1_no_sib9', 'sib1', 'no_sib9'),
    ('sib1_not_broadcast', 'sib1', 'not_broadcast'),
    ('sib1_none', 'sib1', 'none'),
    ('sib1_three_plmn', 'sib1', 'three_plmn'),
    ('sib1_no_tdra', 'sib1', 'no_tdra'),
    ('sib1_offset13', 'sib1', 'offset13'),
    ('si_sib2_sib9', 'si', 'sib2_sib9'),
    ('si_sib2_sib9_noref', 'si', 'sib2_sib9_noref'),
    ('si_sib2', 'si', 'sib2'),
]


def fixtures():
    """``{key: {'hex', 'plain'}`` or ``{key: {'error'}}``, or None when no pycrate interpreter."""
    def make():
        py = pycrate_python()
        if py is None:
            return None
        payload = {'utc': UTC_UNITS,
                   'ref': [REF_DAYS, REF_SECONDS, REF_MS, REF_TENNS],
                   'jobs': [list(job) for job in JOBS]}
        try:
            proc = subprocess.run([py, '-c', ENCODER, json.dumps(payload)], capture_output=True,
                                  text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {job[0]: {'error': 'the pycrate encoder timed out'} for job in JOBS}
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-400:]
            return {job[0]: {'error': 'the pycrate encoder failed: ' + tail} for job in JOBS}
        if proc.stderr.strip():
            sys.stderr.write(proc.stderr)        # full pycrate tracebacks, one per failed block
        try:
            rows = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {job[0]: {'error': 'the pycrate encoder printed no JSON'} for job in JOBS}
        return {row['key']: row for row in rows}
    return memo('fixtures', make)


def need(*keys):
    """The encoded fixtures ``keys``; skips without pycrate, fails with the encoder's error."""
    fx = fixtures()
    if fx is None:
        skip('no pycrate interpreter imports RRCNR (set FMRX_TEST_PYCRATE_PYTHON)')
    for key in keys:
        row = fx.get(key, {})
        if 'error' in row or 'hex' not in row:
            raise AssertionError('fixture %s did not encode: %s'
                                 % (key, str(row.get('error', 'missing'))[-400:]))
    return fx


def fixture_hex(key):
    """The encoded block, or a placeholder that does not parse (for tests that need none)."""
    fx = fixtures()
    row = (fx or {}).get(key, {})
    return row.get('hex', PLACEHOLDER_HEX)


# Decoded-plain helpers: the encoder's output is data; these only find values by name.

def walk(obj):
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def find_key(obj, key):
    for node in walk(obj):
        if isinstance(node, dict) and key in node:
            return node[key]
    return None


def find_all_key(obj, key):
    return [node[key] for node in walk(obj) if isinstance(node, dict) and key in node]


def choice(value):
    """A decoded CHOICE ``[name, value]`` as its value; anything else as it is."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def find_choice(obj, name):
    for node in walk(obj):
        if isinstance(node, list) and len(node) == 2 and node[0] == name:
            return node[1]
    return None


def bitint(value):
    """A decoded BIT STRING as an integer: (value, nbits), a bare integer, or bytes."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        return value[0]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value, 16)
    raise AssertionError('not a bit string or integer: %r' % (value,))


def digits(seq):
    return ''.join(str(int(d)) for d in seq)


def plmn_strings(plain_sib1):
    out = []
    for infos in find_all_key(plain_sib1, 'plmn-IdentityInfoList'):
        for info_ in infos:
            for ident in info_.get('plmn-IdentityList', []):
                out.append('%s-%s' % (digits(ident['mcc']), digits(ident['mnc'])))
    return out


# ---- the stub outside program -----------------------------------------------------------------

# Stub for run_nr.sh. Args: FILE SRATE SSB_OFFSET_HZ SSB_ABS_HZ SCS_KHZ [-m MS] [-t LIST] [-a N]
# [-P PCI]. -P is refused (exit 2, as an older program does) unless the config has target_mib:
# then a call with -P says that MIB (null: NR_NO_MIB) and sends the same blocks.
# Logs one JSON line per call to calls.jsonl beside itself: argv, file, its size, and the first
# 20 ms (head-N.cf32). Config (config.json): mib (null: NR_NO_MIB), point_a_hz, sib1_hex (SIB1
# sent on calls without -a), si_hex (other SI sent when -t and -a are both given), repeat (each
# block sent this many times), done_reason, junk (unknown lines, and a truncated last line).
STUB_PY = r'''
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
HEAD_BYTES = 460800 * 8                       # 20 ms at 23.04 MS/s, complex64 (one SSB burst)
FLAGS = ('-m', '-t', '-a')


def log(rec):
    with open(os.path.join(HERE, 'calls.jsonl'), 'a') as fh:
        fh.write(json.dumps(rec) + '\n')


def logged():
    path = os.path.join(HERE, 'calls.jsonl')
    if not os.path.isfile(path):
        return 0
    with open(path) as fh:
        return sum(1 for line in fh if line.strip())


def say(tag, obj):
    sys.stdout.write('%s %s\n' % (tag, json.dumps(obj)))


def main():
    with open(os.path.join(HERE, 'config.json')) as fh:
        cfg = json.load(fh)
    argv = sys.argv[1:]
    n = logged()
    ok = len(argv) >= 5 and (len(argv) - 5) % 2 == 0
    opts = {}
    flags = FLAGS + (('-P',) if 'target_mib' in cfg else ())
    if ok:
        rest = argv[5:]
        for i in range(0, len(rest), 2):
            if rest[i] not in flags:
                ok = False
                break
            opts[rest[i]] = rest[i + 1]
    if not ok:
        log({'argv': argv, 'bad': True})
        sys.stderr.write('stub: want FILE SRATE SSB_OFFSET_HZ SSB_ABS_HZ SCS_KHZ [-m MS] [-t LIST] [-a N]\n')
        return 2
    path = argv[0]
    rec = {'argv': argv, 'file': path, 'size': os.path.getsize(path) if os.path.isfile(path) else -1,
           'opts': opts}
    if rec['size'] >= 0:
        head = 'head-%d.cf32' % n
        with open(path, 'rb') as src, open(os.path.join(HERE, head), 'wb') as dst:
            dst.write(src.read(HEAD_BYTES))
        rec['head'] = head
    log(rec)

    if cfg.get('junk'):
        sys.stdout.write('stub: an unknown line\n')
        sys.stdout.write('NR_XYZ {"unexpected": true}\n')
    mib = cfg['target_mib'] if '-P' in opts else cfg.get('mib')
    if mib is None:
        say('NR_NO_MIB', {'tries': 3, 'reason': 'no PBCH decoded'})
        sent = 0
    else:
        say('NR_MIB', mib)
        say('NR_CORESET0', {'offset_rb': 0, 'n_rb': 48, 'symbols': 2, 'grid_prb': 0,
                            'pointA_hz': cfg['point_a_hz'], 'f0_hz': 599900000.0})
        reps = int(cfg.get('repeat', 1))
        blocks = []
        if '-a' not in opts and cfg.get('sib1_hex'):
            blocks += [(0, cfg['sib1_hex'])] * int(cfg.get('sib1_repeat', 1))
            if cfg.get('minor_sib1_hex'):
                blocks = [(0, cfg['minor_sib1_hex'])] + blocks
        if '-a' not in opts:
            blocks += [(0, hexed) for hexed in cfg.get('sii0_si_hex', [])]
        if '-a' in opts:
            blocks += [(1, hexed) for hexed in cfg.get('si_hex', [])]
        sent = 0
        for sii, hexed in blocks:
            for k in range(reps):
                say('NR_SI', {'sfn': k, 'slot': 0, 'sii': sii, 'vrb': 0,
                              'tbs': len(hexed) // 2, 'hex': hexed})
                sent += 1
    crc = int(cfg.get('crc_fail', 0)) if '-a' not in opts or not cfg.get('crc_pass1_only') else 0
    for k in range(crc):
        say('NR_SI_CRC_FAIL', {'sfn': k, 'slot': 0, 'sii': 1, 'vrb': 0, 'tbs': 0})
    if crc:
        say('NR_SI_ERR', {'reason': 'stub: a block the decoder could not parse'})
    done = {'slots': 100, 'dci': 1, 'si': sent, 'crc_fail': crc}
    if cfg.get('done_reason'):
        done['reason'] = cfg['done_reason']
    say('NR_DONE', done)
    if cfg.get('junk'):
        sys.stdout.write('NR_SI {"sfn": 3, "sii"')   # truncated, and no newline: the last line
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''


def make_tools(key, cfg, pyc=True):
    """A tools folder: run_nr.sh (the stub) and its config; with ``pyc``, pyc/bin/python
    (the pycrate interpreter, else this one). Without ``pyc`` there is no pycrate interpreter."""
    folder = os.path.join(work(), 'tools-%s-%d' % (key, next(_COUNTER)))
    os.makedirs(folder)
    stub_py = os.path.join(folder, 'stub.py')
    with open(stub_py, 'w') as fh:
        fh.write(STUB_PY)
    with open(os.path.join(folder, 'config.json'), 'w') as fh:
        json.dump(cfg, fh)
    wrapper = os.path.join(folder, 'run_nr.sh')
    with open(wrapper, 'w') as fh:
        fh.write('#!/bin/sh\nexec %s %s "$@"\n' % (shlex.quote(sys.executable), shlex.quote(stub_py)))
    os.chmod(wrapper, 0o755)
    if pyc:
        pyc_bin = os.path.join(folder, 'pyc', 'bin')
        os.makedirs(pyc_bin)
        # A wrapper, not a symlink: a venv's Python finds its packages only from its own path.
        target = os.environ.get('FMRX_TEST_PYCRATE_PYTHON') or sys.executable
        with open(os.path.join(pyc_bin, 'python'), 'w') as fh:
            fh.write('#!/bin/sh\nexec %s "$@"\n' % shlex.quote(target))
        os.chmod(os.path.join(pyc_bin, 'python'), 0o755)
    return folder


def calls_of(folder):
    path = os.path.join(folder, 'calls.jsonl')
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run_decode(key, cfg, args=DECODE_ARGS, pyc=True, tools=None, cwd=None):
    """``iq-nr nr.cfile <args>`` on the capture, against a stub built from ``cfg`` (memoised)."""
    def make():
        folder = tools if tools is not None else make_tools(key, cfg, pyc)
        tmp = tempfile.mkdtemp(prefix='tmp-%s-' % key, dir=work())
        before = repo_status()
        try:
            proc = subprocess.run([sys.executable, TOOL, 'nr.cfile'] + list(args), cwd=cwd or capture(),
                                  env=clean_env(folder, tmp), capture_output=True, text=True,
                                  timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return Run(None, '', 'timed out after %d s' % TIMEOUT, calls_of(folder), tmp, folder, None)
        after = repo_status()
        return Run(proc.returncode, proc.stdout, proc.stderr, calls_of(folder), tmp, folder,
                   (before, after))
    return memo(('decode', key, tuple(args), pyc, tools, cwd), make)


def case_cfg(name):
    """The stub's config for a named case. Fixture blocks, or placeholders without pycrate."""
    full = {'mib': MIB, 'point_a_hz': STUB_CORESET0_POINT_A_HZ, 'repeat': 1}
    cases = {
        'full': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')]),
        'no_sib9': dict(sib1=fixture_hex('sib1_no_sib9'), si=[]),
        'not_broadcast': dict(sib1=fixture_hex('sib1_not_broadcast'), si=[]),
        'never_sent': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2')]),
        'no_si': dict(sib1=fixture_hex('sib1_none'), si=[]),
        'three_plmn': dict(sib1=fixture_hex('sib1_three_plmn'), si=[fixture_hex('si_sib2_sib9')]),
        'noref': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9_noref')]),
        'repeat30': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')], repeat=30),
        'junk': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')], junk=True),
        'no_mib': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')], mib=None),
        'k_ssb': dict(sib1=None, si=[], done_reason=(
            'k_SSB 5 ... no CORESET 0, the cell sends no SIB1')),
        'minor': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')], sib1_repeat=3,
                      minor_sib1=fixture_hex('sib1_three_plmn')),
        'crc': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')], crc_fail=2),
        'no_tdra': dict(sib1=fixture_hex('sib1_no_tdra'), si=[fixture_hex('si_sib2_sib9')]),
        'other_cell': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')],
                           mib=dict(MIB, pci=PCI + 3)),
        'targeted': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')],
                         mib=dict(MIB, pci=PCI + 3),
                         target_mib=dict(MIB, targeted=True, strongest_pci=PCI + 3)),
        'targeted_no_mib': dict(sib1=fixture_hex('sib1_full'), si=[fixture_hex('si_sib2_sib9')],
                                mib=dict(MIB, pci=PCI + 3), target_mib=None),
        'unscheduled': dict(sib1=fixture_hex('sib1_none'), si=[],
                            sii0_si=[fixture_hex('si_sib2_sib9')]),
        'osi_own_slots': dict(sib1=fixture_hex('sib1_none'), si=[fixture_hex('si_sib2_sib9')],
                              crc_fail=2, crc_pass1_only=True),
        'sii0_scheduled': dict(sib1=fixture_hex('sib1_full'), si=[],
                               sii0_si=[fixture_hex('si_sib2_sib9')]),
    }[name]
    cfg = dict(full)
    cfg['sib1_hex'] = cases.get('sib1')
    cfg['si_hex'] = cases.get('si', [])
    for k in ('repeat', 'junk', 'done_reason', 'mib', 'sib1_repeat', 'crc_fail', 'target_mib',
              'crc_pass1_only'):
        if k in cases:
            cfg[k] = cases[k]
    if 'minor_sib1' in cases:
        cfg['minor_sib1_hex'] = cases['minor_sib1']
    if 'sii0_si' in cases:
        cfg['sii0_si_hex'] = cases['sii0_si']
    return cfg


def decode_case(name, args=DECODE_ARGS):
    return run_decode(name, case_cfg(name), args=args)


# ---- checks -----------------------------------------------------------------------------------

def test_s0_pycrate_encodes_each_fixture_block():
    """S0: pycrate encodes every fixture block (SIB1 variants, SI with SIB2 and SIB9)."""
    fx = need(*[job[0] for job in JOBS])
    bad = {key: row['error'] for key, row in fx.items() if 'error' in row}
    assert not bad, 'encode failed: %s' % {k: v[-400:] for k, v in bad.items()}


def test_s0_sib1_fixture_decodes_to_made_up_values():
    """S0: the SIB1 fixture decodes back to the PLMNs, TAC, cell identity, schedule, offset, TDRA."""
    fx = need('sib1_full')
    sib1 = fx['sib1_full']['plain']
    assert plmn_strings(sib1) == PLMNS, 'PLMNs %r' % plmn_strings(sib1)
    assert bitint(find_key(sib1, 'trackingAreaCode')) == TAC, 'TAC'
    assert bitint(find_key(sib1, 'cellIdentity')) == CELL_IDENTITY, 'cell identity'
    sched = find_key(sib1, 'schedulingInfoList')
    assert [e['si-Periodicity'] for e in sched] == ['rf16', 'rf32'], 'periodicities %r' % sched
    assert [e['sib-MappingInfo'][0]['type'] for e in sched] == ['sibType2', 'sibType9'], 'SIB types'
    assert find_key(sib1, 'offsetToPointA') == OFFSET_TO_POINT_A, 'offsetToPointA'
    tdra = choice(find_key(sib1, 'pdsch-TimeDomainAllocationList'))
    assert [row['startSymbolAndLength'] for row in tdra] == list(TIME_ALLOC), 'time allocation'


def test_s0_si_message_decodes_sib2_and_sib9_time():
    """S0: the SI message carries SIB2 and SIB9; the SIB9 time is the made-up instant, GPS, DST."""
    fx = need('si_sib2_sib9')
    plain_si = fx['si_sib2_sib9']['plain']
    assert find_choice(plain_si, 'sib2') is not None, 'no SIB2 in the SI message'
    sib9 = find_choice(plain_si, 'sib9')
    assert sib9 is not None, 'no SIB9 in the SI message'
    info_ = find_key(sib9, 'timeInfo')
    assert bitint(info_['timeInfoUTC']) == UTC_UNITS, 'timeInfoUTC %r' % info_.get('timeInfoUTC')
    assert info_['leapSeconds'] == LEAP, 'leapSeconds'
    assert info_['localTimeOffset'] == LOCAL_OFFSET_UNITS, 'localTimeOffset'
    assert bitint(info_['dayLightSavingTime']) == DST_HOURS, 'dayLightSavingTime'
    ref = find_key(sib9, 'referenceTimeInfo-r16')
    assert ref is not None, 'no referenceTimeInfo-r16 in the SIB9'
    rt = ref['time-r16']
    assert (rt['refDays-r16'], rt['refSeconds-r16'], rt['refMilliSeconds-r16'], rt['refTenNanoSeconds-r16']) == (
        REF_DAYS, REF_SECONDS, REF_MS, REF_TENNS), 'referenceTimeInfo-r16 fields'


def test_a_stub_called_once_with_five_positional_args():
    """A: pass 1 calls run_nr.sh once with FILE 23040000 0 <ssb_hz> 15 (exit 0)."""
    run = decode_case('full', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) >= 1, 'run_nr.sh was never called (stderr: %s)' % run.err.strip()[-200:]
    first = run.calls[0]
    argv = first['argv']
    assert len(argv) == 5, 'pass 1 wants five positional arguments and no options, got %r' % argv
    assert argv[1] == '23040000', 'sample rate argument %r' % argv[1]
    assert argv[2] == '0', 'SSB offset argument %r (the SSB must be mixed to 0 Hz)' % argv[2]
    assert abs(float(argv[3]) - SSB_HZ) <= 1e3, 'SSB absolute frequency %r, want 600.15 MHz' % argv[3]
    assert argv[4] == '15', 'SCS argument %r' % argv[4]


def test_a_file_is_cf32_span_asked_for_to_1_ms():
    """A: the file given to run_nr.sh is the 0.25 s asked for, to 1 ms, at 8 bytes a sample."""
    run = decode_case('full', args=DECODE_ARGS)
    assert run.calls, 'run_nr.sh was never called'
    size = run.calls[0]['size']
    assert size > 0 and size % 8 == 0, 'the file is not complex64 (%d bytes)' % size
    seconds = (size // 8) / RATE
    assert abs(seconds - 0.25) <= 1e-3, 'span %.6f s, asked for 0.25 s' % seconds


def _ssb_grid_peak(head):
    """The PSS-score winner over SSB frequencies -30..30 kHz in 15 kHz steps for PCI 123, or None."""
    scores = {}
    for shift in (-30e3, -15e3, 0.0, 15e3, 30e3):
        for entry in iqnr.ssb_search(head, RATE, 0.0, ssb_hz=shift, scs_khz=15, max_cells=4):
            if entry.get('pci') == PCI:
                scores[shift] = max(scores.get(shift, -np.inf), float(entry.get('pss_score', 0.0)))
    return max(scores, key=scores.get) if scores else None


def test_a_ssb_at_zero_hz_in_the_file_given():
    """A: the SSB is at 0 Hz in the file (the strongest PSS for PCI 123 is at 0, within 15 kHz)."""
    run = decode_case('full', args=DECODE_ARGS)
    assert run.calls and run.calls[0].get('head'), 'no head of the file was logged'
    head = np.fromfile(os.path.join(run.tools, run.calls[0]['head']), dtype=np.complex64)
    peak = _ssb_grid_peak(head)
    assert peak is not None, 'no PCI 123 SSB found in the head of the file given to run_nr.sh'
    assert abs(peak) <= 15e3, 'SSB strongest at %.0f Hz, want 0 Hz (within 1 subcarrier)' % peak


def test_a_temporary_files_gone_after_the_run():
    """A: the decoder deletes its temporary file (the one the stub was given) and its folder."""
    run = decode_case('full', args=DECODE_ARGS)
    assert run.calls, 'run_nr.sh was never called'
    assert not os.path.exists(run.calls[0]['file']), 'the file given to run_nr.sh is still there'
    left = os.listdir(run.tmp)
    assert not left, 'temporary folder not empty after the run: %s' % left[:5]


def test_a_repo_unchanged_by_the_run():
    """A: git status is the same before and after the decode run (which must call the stub)."""
    run = decode_case('full', args=DECODE_ARGS)
    assert run.calls, 'run_nr.sh was never called, so the check proves nothing'
    before, after = run.status
    assert before == after, 'git status changed during the run'


def test_b_pass_2_with_t_and_a_when_sib1_schedules_si():
    """B: SIB1 schedules SIB2 and SIB9, so a second call adds -t (the SLIVs in list order) and -a."""
    need('sib1_full')
    run = decode_case('full', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 2, 'want two calls (pass 1, pass 2), got %d' % len(run.calls)
    assert run.calls[0]['opts'] == {}, 'pass 1 carried options: %r' % run.calls[0]['opts']
    opts = run.calls[1]['opts']
    assert opts.get('-t') == ','.join(str(v) for v in TIME_ALLOC), 'pass 2 -t %r' % opts.get('-t')
    assert opts.get('-a') == str(OFFSET_TO_POINT_A), 'pass 2 -a %r' % opts.get('-a')


def test_b_no_second_call_when_sib1_schedules_no_si():
    """B: SIB1 with no si-SchedulingInfo: one call only."""
    need('sib1_none')
    run = decode_case('no_si', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 1, 'want one call, got %d' % len(run.calls)


def test_c_mib_fields_copied():
    """C: the MIB fields are copied from the stub's NR_MIB line."""
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    mib = dec.get('mib') or {}
    for key, want in MIB.items():
        got = mib.get(key)
        if isinstance(want, float):
            assert got is not None and abs(float(got) - want) <= 1e-6, 'mib.%s %r, want %r' % (key, got, want)
        else:
            assert got == want, 'mib.%s %r, want %r' % (key, got, want)


def test_c_plmns_from_every_entry():
    """C: sib1.plmns is ['001-01', '001-001'] (MNC with its own digit count)."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    sib1 = dec.get('sib1') or {}
    assert sib1.get('plmns') == PLMNS, 'plmns %r, want %r' % (sib1.get('plmns'), PLMNS)


def test_c_tac_and_cell_identity():
    """C: sib1.tac is 4660 (0x1234) and sib1.cell_identity 4886718345 (0x123456789)."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    sib1 = dec.get('sib1') or {}
    assert sib1.get('tac') == TAC, 'tac %r, want %d' % (sib1.get('tac'), TAC)
    assert sib1.get('cell_identity') == CELL_IDENTITY, 'cell_identity %r, want %d' % (
        sib1.get('cell_identity'), CELL_IDENTITY)


def test_c_cell_barred_copied_from_mib():
    """C: sib1.cell_barred is the MIB's cell_barred (False here)."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    sib1 = dec.get('sib1') or {}
    assert sib1.get('cell_barred') is False, 'cell_barred %r, want False' % sib1.get('cell_barred')


def test_c_carrier_point_a_119310_596_550_mhz():
    """C: carrier point A is ARFCN 119310 (596.550 MHz), with offset_to_point_a 10."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    carrier = (dec.get('sib1') or {}).get('carrier') or {}
    assert carrier.get('point_a_arfcn') == POINT_A_ARFCN, 'point_a_arfcn %r' % carrier.get('point_a_arfcn')
    mhz = carrier.get('point_a_mhz')
    assert mhz is not None and abs(float(mhz) - 596.550) <= 1e-3, 'point_a_mhz %r, want 596.550' % mhz
    assert carrier.get('offset_to_point_a') == OFFSET_TO_POINT_A, 'offset_to_point_a %r' % (
        carrier.get('offset_to_point_a'))


def test_c_carrier_point_a_119184_595_920_mhz_k_ssb_6():
    """C: with k_SSB 6 (a copy of the MIB) and offsetToPointA 13, point A is ARFCN 119184 (595.920 MHz)."""
    need('sib1_offset13')
    cfg = case_cfg('full')
    cfg['mib'] = dict(MIB, ssb_subcarrier_offset=K_SSB_K6)
    cfg['sib1_hex'] = fixture_hex('sib1_offset13')
    dec = need_decode(run_decode('full-k6-offset13', cfg, args=DECODE_ARGS))
    carrier = (dec.get('sib1') or {}).get('carrier') or {}
    assert carrier.get('point_a_arfcn') == POINT_A_K6_ARFCN, 'point_a_arfcn %r' % carrier.get('point_a_arfcn')
    mhz = carrier.get('point_a_mhz')
    assert mhz is not None and abs(float(mhz) - POINT_A_K6_MHZ) <= 1e-3, 'point_a_mhz %r, want 595.920' % mhz
    assert carrier.get('offset_to_point_a') == OFFSET_K6_TO_POINT_A, 'offset_to_point_a %r' % (
        carrier.get('offset_to_point_a'))


def test_c_si_schedule_and_window():
    """C: si_schedule is SIB2 every 16 frames, then SIB9 every 32; the window is 20 slots."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    sib1 = dec.get('sib1') or {}
    assert sib1.get('si_window_slots') == SI_WINDOW_SLOTS, 'si_window_slots %r' % sib1.get('si_window_slots')
    sched = sib1.get('si_schedule') or []
    assert [e.get('periodicity_rf') for e in sched] == [16, 32], 'periodicity_rf %r' % (
        [e.get('periodicity_rf') for e in sched])
    assert [e.get('sibs') for e in sched] == [['sib2'], ['sib9']], 'sibs %r' % [e.get('sibs') for e in sched]


def _time(dec):
    time_ = dec.get('time')
    assert time_ is not None, 'time is null (time_reason %r)' % dec.get('time_reason')
    return time_


def test_d_utc_is_the_made_up_instant():
    """D: time.utc is 2030-01-02T03:04:05.670 UTC (millisecond ISO 8601)."""
    need('si_sib2_sib9')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    utc = _time(dec).get('utc')
    assert utc is not None and datetime.datetime.fromisoformat(utc) == datetime.datetime.fromisoformat(UTC_ISO), (
        'time.utc %r, want %s' % (utc, UTC_ISO))


def test_d_gps_is_utc_plus_18_seconds():
    """D: time.gps is UTC plus leapSeconds 18."""
    need('si_sib2_sib9')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    gps = _time(dec).get('gps')
    assert gps is not None and datetime.datetime.fromisoformat(gps) == datetime.datetime.fromisoformat(GPS_ISO), (
        'time.gps %r, want %s' % (gps, GPS_ISO))
    assert _time(dec).get('leap_seconds') == LEAP, 'leap_seconds %r' % _time(dec).get('leap_seconds')


def test_d_local_offset_minutes_and_dst_hours():
    """D: local_offset_minutes is -300 (localTimeOffset -20 x 15), dst_hours 1."""
    need('si_sib2_sib9')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    t = _time(dec)
    assert t.get('local_offset_minutes') == -300, 'local_offset_minutes %r' % t.get('local_offset_minutes')
    assert t.get('dst_hours') == DST_HOURS, 'dst_hours %r' % t.get('dst_hours')


def test_d_reference_time_gps_reading_equals_gps():
    """D: reference_time.gps_reading equals time.gps to 1 ms."""
    need('si_sib2_sib9')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    t = _time(dec)
    ref = t.get('reference_time')
    assert ref is not None and ref.get('gps_reading'), 'reference_time missing: %r' % ref
    got = datetime.datetime.fromisoformat(ref['gps_reading'])
    want = datetime.datetime.fromisoformat(GPS_ISO)
    assert abs((got - want).total_seconds()) <= 1e-3, 'gps_reading %s, time.gps %s' % (got, want)


def test_p_another_cells_mib_is_not_reported_as_this_one():
    """P: the MIB's PCI is not the SSB's: mib and sib1 null, decoded_pci and reason say so."""
    run = decode_case('other_cell', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    # the retry aimed at the PCI is refused (an older program), and there is no pass 2
    assert len(run.calls) == 2, 'want two calls (pass 1 and the -P retry), got %d' % len(run.calls)
    assert run.calls[1].get('bad') and run.calls[1]['argv'][-2:] == ['-P', str(PCI)], run.calls[1]
    dec = need_decode(run)
    assert dec.get('mib') is None and dec.get('sib1') is None, 'another cell reported as this one'
    assert dec.get('decoded_pci') == PCI + 3, 'decoded_pci %r' % dec.get('decoded_pci')
    reason = dec.get('reason') or ''
    assert 'PCI %d' % (PCI + 3) in reason and 'PCI %d' % PCI in reason, 'reason %r' % reason
    assert dec.get('time') is None and dec.get('time_reason') == 'another cell was decoded', dec


def test_t_weaker_sector_decoded_by_aiming_at_its_pci():
    """T: pass 1 reads a stronger cell; the retry with -P PCI reads this one, and pass 2 keeps -P."""
    need('sib1_full', 'si_sib2_sib9')
    run = decode_case('targeted', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 3, 'want pass 1, the -P retry and pass 2, got %d' % len(run.calls)
    assert all(c.get('opts', {}).get('-P') == str(PCI) for c in run.calls[1:]), run.calls
    dec = need_decode(run)
    assert (dec.get('mib') or {}).get('pci') == PCI and dec['mib'].get('targeted') is True, dec.get('mib')
    assert 'decoded_pci' not in dec, 'decoded_pci %r' % dec.get('decoded_pci')
    assert (dec.get('sib1') or {}).get('tac') == TAC, 'sib1 %r' % dec.get('sib1')
    assert sorted((dec.get('si') or {}).get('sibs_heard') or {}) == ['sib2', 'sib9'], dec.get('si')
    reason = dec.get('reason') or ''
    assert 'PCI %d' % (PCI + 3) in reason and 'aiming' in reason, 'reason %r' % reason


def test_t_weaker_sector_without_a_mib_keeps_decoded_pci():
    """T: the -P retry finds no MIB: as P (mib and sib1 null, decoded_pci), the reason says so."""
    run = decode_case('targeted_no_mib', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 2, 'want pass 1 and the -P retry, got %d' % len(run.calls)
    dec = need_decode(run)
    assert dec.get('mib') is None and dec.get('sib1') is None, 'another cell reported as this one'
    assert dec.get('decoded_pci') == PCI + 3, 'decoded_pci %r' % dec.get('decoded_pci')
    reason = dec.get('reason') or ''
    assert 'PCI %d' % (PCI + 3) in reason and 'no MIB' in reason, 'reason %r' % reason
    assert dec.get('time_reason') == 'another cell was decoded', dec
    assert (dec.get('blocks') or {}).get('sib1') == 0, 'the stronger cell\'s blocks counted: %r' % dec.get('blocks')


def test_u_unscheduled_si_in_sib1_slots_is_heard():
    """U: SIB1 schedules nothing, SIB2 and SIB9 come with sii 0: heard and listed as unscheduled."""
    need('sib1_none', 'si_sib2_sib9')
    run = decode_case('unscheduled', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 1, 'want one call (no schedule, no pass 2), got %d' % len(run.calls)
    dec = need_decode(run)
    si_out = dec.get('si') or {}
    assert sorted(si_out.get('sibs_heard') or {}) == ['sib2', 'sib9'], 'heard %r' % si_out.get('sibs_heard')
    assert si_out.get('scheduled') == [], 'scheduled %r' % si_out.get('scheduled')
    assert sorted(si_out.get('unscheduled') or []) == ['sib2', 'sib9'], (
        'unscheduled %r' % si_out.get('unscheduled'))
    assert 'does not schedule' in (dec.get('reason') or ''), 'reason %r' % dec.get('reason')
    assert dec.get('sib1') is not None, 'the SI block was taken for SIB1'


def test_u_unscheduled_sib9_gives_the_time():
    """U: an unscheduled SIB9 still gives the network time."""
    need('sib1_none', 'si_sib2_sib9')
    dec = need_decode(decode_case('unscheduled', args=DECODE_ARGS))
    utc = _time(dec).get('utc')
    assert utc is not None and (datetime.datetime.fromisoformat(utc)
                                == datetime.datetime.fromisoformat(UTC_ISO)), 'utc %r' % utc


def test_u_scheduled_si_with_sii_0_is_not_unscheduled():
    """U: scheduled SIB2 and SIB9 that come with sii 0 are heard, and none is unscheduled."""
    need('sib1_full', 'si_sib2_sib9')
    dec = need_decode(decode_case('sii0_scheduled', args=DECODE_ARGS))
    si_out = dec.get('si') or {}
    assert sorted(si_out.get('sibs_heard') or {}) == ['sib2', 'sib9'], 'heard %r' % si_out.get('sibs_heard')
    assert si_out.get('unscheduled') == [] and si_out.get('missing') == [], si_out


def test_u_unscheduled_si_in_its_own_slots_is_searched():
    """U: SIB1 schedules nothing, pass 1 sees sii 1 blocks fail: pass 2 runs and hears them."""
    need('sib1_none', 'si_sib2_sib9')
    run = decode_case('osi_own_slots', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 2, 'want pass 1 and pass 2, got %d' % len(run.calls)
    assert '-a' in run.calls[1].get('opts', {}), run.calls[1]
    dec = need_decode(run)
    si_out = dec.get('si') or {}
    assert sorted(si_out.get('unscheduled') or []) == ['sib2', 'sib9'], si_out
    assert 'pass 1 saw 2 other SI blocks' in (dec.get('reason') or ''), 'reason %r' % dec.get('reason')
    assert dec.get('time') is not None, 'no time from the unscheduled SIB9'


def test_e_no_sib9_scheduled_gives_reason():
    """E: SIB1 does not schedule SIB9: time null, time_reason says so, exit 0."""
    need('sib1_no_sib9')
    run = decode_case('no_sib9', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('time') is None, 'time should be null'
    assert (dec.get('time_reason') or '').startswith('SIB1 does not schedule SIB9'), (
        'time_reason %r' % dec.get('time_reason'))


def test_e_sib9_scheduled_but_never_sent_gives_reason():
    """E: SIB9 scheduled but the stub never sends it: time null, 'not heard in N s'."""
    need('sib1_full')
    run = decode_case('never_sent', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('time') is None, 'time should be null'
    assert (dec.get('time_reason') or '').startswith('SIB9 scheduled but not heard in'), (
        'time_reason %r' % dec.get('time_reason'))


def test_e_not_broadcasting_gives_reason():
    """E: SIB9 with si-BroadcastStatus notBroadcasting: time null with the on-demand reason."""
    need('sib1_not_broadcast')
    run = decode_case('not_broadcast', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('time') is None, 'time should be null'
    want = 'SIB9 is not broadcast (si-BroadcastStatus notBroadcasting: on demand)'
    assert dec.get('time_reason') == want, 'time_reason %r, want %r' % (dec.get('time_reason'), want)


def test_e_no_mib_gives_reason():
    """E: NR_NO_MIB: no MIB, time null, time_reason 'no MIB', exit 0."""
    run = decode_case('no_mib', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('time') is None, 'time should be null'
    assert dec.get('time_reason') == 'no MIB', 'time_reason %r' % dec.get('time_reason')
    assert dec.get('sib1') is None, 'sib1 should be null without a MIB'
    assert os.listdir(run.tmp) == [], 'temporary folder not empty: %s' % os.listdir(run.tmp)


def test_e_done_with_k_ssb_reason_gives_no_sib1():
    """E: NR_DONE with a k_SSB reason (no CORESET 0): time null, time_reason 'no SIB1', exit 0."""
    run = decode_case('k_ssb', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('time') is None, 'time should be null'
    assert dec.get('time_reason') == 'no SIB1', 'time_reason %r' % dec.get('time_reason')
    assert dec.get('sib1') is None, 'sib1 should be null without SIB1'


def test_f_no_run_nr_exits_2_naming_it():
    """F: no run_nr.sh under FMRX_LTE_TOOLS: exit 2, stderr names run_nr.sh."""
    folder = make_tools('no-run-nr', case_cfg('full'))
    os.remove(os.path.join(folder, 'run_nr.sh'))
    run = run_decode('no-run-nr', {}, tools=folder)
    assert run.code == 2, 'want exit 2, got %s (%s)' % (run.code, run.err.strip()[-200:])
    assert 'run_nr.sh' in run.err, 'stderr does not name run_nr.sh: %r' % run.err.strip()[-200:]


def test_f_no_pycrate_gives_sib1_null_with_reason_naming_pycrate():
    """F: no pycrate interpreter: sib1 null with a reason naming pycrate; exit 0; MIB reported."""
    run = run_decode('no-pycrate', case_cfg('full'), pyc=False)
    check_code(run, 0, 'iq-nr --decode')
    dec = need_decode(run)
    assert dec.get('mib') and dec['mib'].get('pci') == PCI, 'the MIB is not reported'
    assert os.listdir(run.tmp) == [], 'temporary folder not empty: %s' % os.listdir(run.tmp)
    assert dec.get('sib1') is None, 'sib1 should be null'
    reason = dec.get('reason') or ''
    assert 'pycrate' in reason.lower(), 'reason does not name pycrate: %r' % reason


def test_g_decode_seconds_20_is_cut_to_10_with_reason():
    """G: --decode-seconds 20 is read as 10 s, and the decode reason says so."""
    run = decode_case('full', args=('--decode', '--decode-seconds', '20'))
    check_code(run, 0, 'iq-nr --decode --decode-seconds 20')
    reason = need_decode(run).get('reason') or ''
    assert '20' in reason and '10' in reason, 'reason does not say the cut to 10 s: %r' % reason


def test_g_decode_seconds_zero_rejected():
    """G: --decode-seconds 0: exit 1, stderr names the option, nothing on stdout."""
    run = decode_case('full', args=('--decode', '--decode-seconds', '0'))
    assert run.code == 1, 'want exit 1, got %s' % run.code
    assert '--decode-seconds' in run.err and 'unrecognized' not in run.err, (
        'stderr: %r' % run.err.strip()[-200:])
    assert run.out.strip() == '', 'stdout is not empty'


def test_g_decode_seconds_negative_rejected():
    """G: --decode-seconds -3: exit 1, stderr names the option, nothing on stdout."""
    run = decode_case('full', args=('--decode', '--decode-seconds', '-3'))
    assert run.code == 1, 'want exit 1, got %s' % run.code
    assert '--decode-seconds' in run.err and 'unrecognized' not in run.err, (
        'stderr: %r' % run.err.strip()[-200:])
    assert run.out.strip() == '', 'stdout is not empty'


def test_g_no_decode_output_matches_baseline():
    """G: tools/iq-nr without --decode is byte-identical to the baseline (hardcoded sha256)."""
    folder = os.path.join(work(), 'baseline')
    os.makedirs(folder)
    # The same synthesis as the baseline was taken from: 100 ms, 20 MS/s, seed 0.
    iq, _, _ = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                 period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                 dur_ms=100, fs=20e6, seed=0)
    write_cfile(folder, 'nr', iq, 20e6, CENTRE_HZ)
    empty = os.path.join(work(), 'baseline-tools')
    os.makedirs(empty, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='tmp-baseline-', dir=work())
    proc = subprocess.run([sys.executable, TOOL, 'nr.cfile'], cwd=folder,
                          env=clean_env(empty, tmp), capture_output=True, timeout=TIMEOUT)
    assert proc.returncode == 0, 'exit %d: %s' % (proc.returncode, proc.stderr[-200:])
    digest = hashlib.sha256(proc.stdout).hexdigest()
    assert len(proc.stdout) == BASELINE_BYTES, 'output is %d bytes, baseline %d' % (
        len(proc.stdout), BASELINE_BYTES)
    assert digest == BASELINE_SHA256, 'output sha256 %s, baseline %s' % (digest, BASELINE_SHA256)


def _h_file():
    """A 2 s, 40 MS/s cf32 file, written in chunks: a 100 ms synthetic block tiled 20 times
    (the block holds a whole number of frames, so the tiling keeps the SSB timing)."""
    folder = os.path.join(work(), 'h')
    os.makedirs(folder)
    block, _, _ = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                    period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                    dur_ms=100, fs=40e6, seed=0)
    block = np.asarray(block, dtype=np.complex64)
    path = os.path.join(folder, 'nr40.cfile')
    with open(path, 'wb') as fh:
        for _ in range(20):
            block.tofile(fh)
    with open(os.path.join(folder, 'nr40.sigmf-meta'), 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': 40e6},
                   'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE_HZ}],
                   'annotations': []}, fh)
    return folder, path


WRAP = ("import json, resource, subprocess, sys\n"
        "r = subprocess.run([sys.executable] + sys.argv[1:], capture_output=True, text=True)\n"
        "rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
        "print(json.dumps({'code': r.returncode, 'out': r.stdout, 'err': r.stderr[-300:], 'rss': rss}))\n")


def _h_run():
    def make():
        folder, path = _h_file()
        try:
            tools = make_tools('h', case_cfg('full'))
            tmp = tempfile.mkdtemp(prefix='tmp-h-', dir=work())
            proc = subprocess.run([sys.executable, '-c', WRAP, TOOL, 'nr40.cfile', '--decode',
                                   '--decode-seconds', '2'], cwd=folder,
                                  env=clean_env(tools, tmp), capture_output=True, text=True,
                                  timeout=TIMEOUT)
            if not proc.stdout.strip():
                raise AssertionError('the wrapper printed nothing: %s' % proc.stderr[-300:])
            wrapped = json.loads(proc.stdout.strip().splitlines()[-1])
            return dict(wrapped, calls=calls_of(tools))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return memo('h-run', make)


def test_h_child_peak_rss_under_900_mb():
    """H: iq-nr --decode on a 2 s, 40 MS/s file (--decode-seconds 2): peak RSS under 900 MB."""
    got = _h_run()
    assert got['code'] == 0, 'exit %s: %s' % (got['code'], got['err'][-200:])
    card = json.loads(got['out'])
    assert decode_of(card) is not None, 'no decode key in the output'
    assert got['calls'], 'run_nr.sh was never called, so the decode did not run'
    span = (got['calls'][0]['size'] // 8) / RATE
    assert abs(span - 2.0) <= 1e-3, 'run_nr.sh was given %.6f s, asked for 2 s' % span
    rss_mb = got['rss'] * RSS_UNIT / 1e6
    assert rss_mb < RSS_LIMIT_MB, 'peak RSS %.0f MB, limit %.0f MB' % (rss_mb, RSS_LIMIT_MB)


def test_r1_three_plmn_entries_with_2_and_3_digit_mnc():
    """R1: three PLMN entries (2-digit and 3-digit MNC) all appear in sib1.plmns, in order."""
    need('sib1_three_plmn')
    dec = need_decode(decode_case('three_plmn', args=DECODE_ARGS))
    want = ['001-01', '001-001', '999-02']
    assert (dec.get('sib1') or {}).get('plmns') == want, 'plmns %r, want %r' % (
        (dec.get('sib1') or {}).get('plmns'), want)


def test_r2_sib9_without_reference_time():
    """R2: a SIB9 with no referenceTimeInfo: time still set, reference_time absent or null."""
    need('si_sib2_sib9_noref')
    dec = need_decode(decode_case('noref', args=DECODE_ARGS))
    t = _time(dec)
    assert t.get('utc') is not None and datetime.datetime.fromisoformat(t['utc']) == (
        datetime.datetime.fromisoformat(UTC_ISO)), 'time.utc %r' % t.get('utc')
    assert t.get('reference_time') is None, 'reference_time %r, want null' % (t.get('reference_time'),)


def test_r3_blocks_repeated_30_times_match_single_block_run():
    """R3: the same blocks sent 30 times give the same sib1 and time as one of each."""
    need('sib1_full', 'si_sib2_sib9')
    single = need_decode(decode_case('full', args=DECODE_ARGS))
    many = decode_case('repeat30', args=DECODE_ARGS)
    dec = need_decode(many)
    assert dec.get('sib1') == single.get('sib1'), 'sib1 differs with 30 copies'
    assert dec.get('time') == single.get('time'), 'time differs with 30 copies'


def test_r4_unknown_lines_and_truncated_last_line():
    """R4: unknown lines and a truncated last line: exit 0, MIB still reported, no traceback."""
    run = decode_case('junk', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode with junk on the stub')
    assert 'Traceback' not in run.err, 'a traceback on stderr'
    dec = need_decode(run)
    assert (dec.get('mib') or {}).get('pci') == PCI, 'the MIB is not reported'


def long_capture():
    """6 s: the capture's 100 ms block tiled 60 times at 20 MS/s, written in chunks."""
    def make():
        folder = os.path.join(work(), 'long')
        os.makedirs(folder)
        block, _, _ = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,),
                                        period_ms=20, n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6,
                                        dur_ms=100, fs=20e6, seed=0)
        block = np.asarray(block, dtype=np.complex64)
        with open(os.path.join(folder, 'nr.cfile'), 'wb') as fh:
            for _ in range(60):
                block.tofile(fh)
        with open(os.path.join(folder, 'nr.sigmf-meta'), 'w') as fh:
            json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': 20e6},
                       'captures': [{'core:sample_start': 0, 'core:frequency': CENTRE_HZ}],
                       'annotations': []}, fh)
        return folder
    return memo('long', make)


def test_a_success_fields_on_the_full_run():
    """A: the success path gives ran true, seconds 0.25, the block counts, and reason null (uncut)."""
    need('sib1_full')
    dec = need_decode(decode_case('full', args=DECODE_ARGS))
    assert dec.get('ran') is True, 'ran %r' % dec.get('ran')
    assert abs(float(dec.get('seconds', -1)) - 0.25) <= 1e-3, 'seconds %r' % dec.get('seconds')
    blocks = dec.get('blocks') or {}
    assert set(blocks) >= {'sib1', 'other', 'crc_fail'}, 'blocks %r' % blocks
    assert dec.get('reason') is None, 'reason %r, want null (the span was not cut)' % dec.get('reason')


def test_a_crc_fail_lines_are_counted():
    """A: NR_SI_CRC_FAIL and NR_SI_ERR lines are counted in blocks.crc_fail, and not decoded."""
    dec = need_decode(decode_case('crc', args=DECODE_ARGS))
    assert (dec.get('blocks') or {}).get('crc_fail') == 2, 'blocks %r' % dec.get('blocks')


def test_b_minority_sib1_block_loses_to_the_most_common():
    """B: a sii 0 block sent once does not beat the SIB1 sent three times."""
    need('sib1_full', 'sib1_three_plmn')
    dec = need_decode(decode_case('minor', args=DECODE_ARGS))
    assert (dec.get('sib1') or {}).get('plmns') == PLMNS, 'plmns %r' % (dec.get('sib1') or {}).get('plmns')


def test_b_no_tdra_second_call_without_t():
    """B: SIB1 with no TDRA list: pass 2 has -a and no -t."""
    need('sib1_no_tdra')
    run = decode_case('no_tdra', args=DECODE_ARGS)
    check_code(run, 0, 'iq-nr --decode')
    assert len(run.calls) == 2, 'want two calls, got %d' % len(run.calls)
    opts = run.calls[1]['opts']
    assert '-a' in opts and '-t' not in opts, 'pass 2 options %r' % opts


def test_c_si_message_sibs_heard_scheduled_missing():
    """C: the full SI case: si.sibs_heard and si.sibs name SIB2 and SIB9; nothing is missing."""
    need('si_sib2_sib9')
    si = need_decode(decode_case('full', args=DECODE_ARGS)).get('si') or {}
    heard = si.get('sibs_heard') or {}
    assert heard.get('sib2') and heard.get('sib9'), 'sibs_heard %r' % heard
    assert 'sib2' in (si.get('sibs') or {}) and 'sib9' in (si.get('sibs') or {}), 'si.sibs keys'
    assert 'sib9' in (si.get('scheduled') or []), 'si.scheduled %r' % si.get('scheduled')
    assert si.get('missing') == [], 'si.missing %r' % si.get('missing')


def test_c_carrier_bands_prb_and_scs():
    """C: carrier bands [78], carrier_prb 106 and scs_khz 15, from the SIB1 fixture."""
    need('sib1_full')
    carrier = (need_decode(decode_case('full', args=DECODE_ARGS)).get('sib1') or {}).get('carrier') or {}
    assert carrier.get('bands') == [78], 'bands %r' % carrier.get('bands')
    assert carrier.get('carrier_prb') == 106, 'carrier_prb %r' % carrier.get('carrier_prb')
    assert carrier.get('scs_khz') == 15, 'scs_khz %r' % carrier.get('scs_khz')


def test_c_si_schedule_broadcast_status_each_entry():
    """C: si_schedule: each entry has broadcast_status broadcasting (the full fixture)."""
    need('sib1_full')
    sched = (need_decode(decode_case('full', args=DECODE_ARGS)).get('sib1') or {}).get('si_schedule') or []
    states = [str(e.get('broadcast_status', '')).lower() for e in sched]
    assert len(states) == 2 and all(st.endswith('broadcasting') and 'notbroadcasting' not in st
                                    for st in states), 'broadcast_status %r' % states


def test_e_not_broadcast_schedule_entry_says_not_broadcasting():
    """E: the SIB9 schedule entry of the not_broadcast fixture says notBroadcasting."""
    need('sib1_not_broadcast')
    sched = (need_decode(decode_case('not_broadcast', args=DECODE_ARGS)).get('sib1') or {}).get('si_schedule') or []
    sib9 = [e for e in sched if e.get('sibs') == ['sib9']]
    assert sib9, 'no SIB9 entry in si_schedule %r' % sched
    assert 'notbroadcasting' in str(sib9[0].get('broadcast_status', '')).lower(), (
        'broadcast_status %r' % sib9[0].get('broadcast_status'))


def test_e_never_sent_names_sib9_missing_and_the_span():
    """E: SIB9 never sent: si.missing names sib9, and time_reason gives the span in seconds."""
    need('sib1_full')
    dec = need_decode(decode_case('never_sent', args=DECODE_ARGS))
    assert 'sib9' in ((dec.get('si') or {}).get('missing') or []), 'si.missing %r' % (dec.get('si') or {}).get('missing')
    m = re.search(r'not heard in ([0-9.]+) s', dec.get('time_reason') or '')
    assert m and abs(float(m.group(1)) - 0.25) <= 0.01, 'time_reason %r' % dec.get('time_reason')


def test_g_default_span_is_6_s():
    """G: with no --decode-seconds the span is 6 s: run_nr.sh is given 6 s and the decode says 6."""
    run = run_decode('default-span', case_cfg('full'), args=('--decode',), cwd=long_capture())
    check_code(run, 0, 'iq-nr --decode (default span)')
    dec = need_decode(run)
    assert run.calls, 'run_nr.sh was never called'
    span = (run.calls[0]['size'] // 8) / RATE
    assert abs(span - 6.0) <= 1e-3, 'run_nr.sh was given %.6f s, want 6 s' % span
    assert abs(float(dec.get('seconds', -1)) - 6.0) <= 1e-3, 'seconds %r' % dec.get('seconds')


def test_g_max_cells_1_lists_at_most_one_ssb():
    """G: --max-cells 1 lists at most one SSB."""
    run = run_decode('max-cells-1', case_cfg('full'), args=DECODE_ARGS + ('--max-cells', '1'))
    check_code(run, 0, 'iq-nr --decode --max-cells 1')
    assert len(run.card.get('ssbs') or []) <= 1, 'ssbs %d' % len(run.card.get('ssbs') or [])


# ---- runner -----------------------------------------------------------------------------------

WORKERS = 4                                  # tests run side by side; decodes are subprocesses


def _one(name, fn):
    """``(label, status, detail)`` for one test: status ok, skip or fail."""
    label = (fn.__doc__ or name).strip().splitlines()[0]
    try:
        fn()
    except SkipTest as exc:
        return label, 'skip', str(exc)
    except AssertionError as exc:
        return label, 'fail', str(exc)
    except Exception as exc:                 # noqa: BLE001 - reported as a failure
        tail = traceback.format_exc().strip().splitlines()[-1]
        return label, 'fail', f'{type(exc).__name__}: {exc}; {tail}'
    return label, 'ok', ''


def _run_all():
    tests = [(name, fn) for name, fn in globals().items() if name.startswith('test_') and callable(fn)]
    failed = 0
    skipped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_one, name, fn) for name, fn in tests]
        for fut in concurrent.futures.as_completed(futures):
            label, status, detail = fut.result()
            if status == 'ok':
                print(f'  ok   {label}', flush=True)
            elif status == 'skip':
                skipped += 1
                print(f'  SKIP {label}  ({detail})', flush=True)
            else:
                failed += 1
                print(f'  FAIL {label}  ({detail})', flush=True)
    print(f'{len(tests) - failed - skipped} ok, {failed} FAIL, {skipped} SKIP of {len(tests)}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
