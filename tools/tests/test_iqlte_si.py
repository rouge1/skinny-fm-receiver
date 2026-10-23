"""Every SIB of a cell: ``iqlte_decode.read_si`` and the pure functions behind it.

``iq-lte --decode`` hands the outside ``pdsch_ue`` the SI-RNTI in every
subframe (its ``PDSCH_ALL_SF`` change) and gets one transport block per SI
message heard. ``read_si`` parses each distinct block with the outside pycrate
interpreter and builds ``si``: SIB1's schedule (each SI message's period,
SIBs, window, and whether the capture covered it), every SIB heard as plain
JSON, and summaries (SIB5's LTE carriers, SIB16's network time, SIB24's NR
carriers).

Groups:
  A  EARFCN and NR-ARFCN to MHz at the table edges
  B  SIB16 time: the 1900 epoch against the Unix one, GPS, local offset, DST
  C  SIB1's schedule: SIB2 in the first message, the Rel-12 extension after
     the main list, window positions, periods
  D  summarise_si on hand-built parser output: time present, not scheduled,
     scheduled but not covered, no other SI heard, a failed block, SIB5's
     65535 entry taken from the v9e0 list
  E  end to end through pycrate (encoded here, made-up values): SIB1 and an SI
     message with SIB16 and SIB5, plus a broken block. Skipped without a
     Python that imports pycrate (FMRX_TEST_PYCRATE_PYTHON, else this one).
  F  a long span resampled in pieces (a SampleSource) equals the resample of
     the whole array, and no piece is read bigger than it should be

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_si.py   (a few seconds)
"""

import calendar
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import iqlte_decode as dec  # noqa: E402

RESULTS = []
# 2026-01-02 03:04:05.670 UTC as a Unix time; NTP's 1900 epoch is 2208988800 s before 1970.
UNIX_T = calendar.timegm((2026, 1, 2, 3, 4, 5)) + 0.67
UTC_10MS = int(round((UNIX_T + 2208988800) * 100))
UTC_WANT = '2026-01-02T03:04:05.670Z'


def report(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok:
        line += f'  ({detail})'
    print(line)


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def group_frequencies():
    def earfcn():
        got = [dec.earfcn_dl(n) for n in (0, 600, 5200, 66436, 68586, 68935, 99999)]
        want = [(1, 2110.0), (2, 1930.0), (13, 748.0), (66, 2110.0), (71, 617.0),
                (71, 651.9), (None, None)]
        return got == want, got
    expect('A  EARFCN to band and MHz at table edges, None outside', earfcn)

    def nr():
        got = [dec.nr_arfcn_mhz(n) for n in (0, 599999, 600000, 620000, 2016667)]
        want = [0.0, 2999.995, 3000.0, 3300.0, 24250.08]
        return got == want, got
    expect('A  NR-ARFCN to MHz on the three raster ranges', nr)


def sib16(units=UTC_10MS, leap=18, offset=-20, dst=1):
    info = {'timeInfoUTC-r11': units}
    if leap is not None:
        info['leapSeconds-r11'] = leap
    if offset is not None:
        info['localTimeOffset-r11'] = offset
    if dst is not None:
        info['dayLightSavingTime-r11'] = [dst, 2]
    return {'timeInfo-r11': info}


def group_time():
    def full():
        t = dec.sib16_time(sib16())
        want = {'utc': UTC_WANT, 'utc_10ms': UTC_10MS, 'leap_seconds': 18,
                'gps': '2026-01-02T03:04:23.670Z', 'local_offset_minutes': -300,
                'local': '2026-01-01T22:04:05.670-05:00', 'dst_hours': 1}
        return t == want, t
    expect('B  SIB16: UTC from 1900 in 10 ms units, GPS = UTC + leap, local = UTC + 15 min x offset', full)

    def bare():
        t = dec.sib16_time(sib16(leap=None, offset=None, dst=None))
        return t == {'utc': UTC_WANT, 'utc_10ms': UTC_10MS}, t
    expect('B  SIB16 with UTC only: no GPS, local or DST keys', bare)

    def none():
        return dec.sib16_time({'lateNonCriticalExtension': '00'}) is None, 'not None'
    expect('B  SIB16 without timeInfo: None', none)


def sib1(window='ms20', ext=True):
    out = {'schedulingInfoList': [
               {'si-Periodicity': 'rf8', 'sib-MappingInfo': []},
               {'si-Periodicity': 'rf16', 'sib-MappingInfo': ['sibType3', 'sibType16-v1130']},
               {'si-Periodicity': 'rf64', 'sib-MappingInfo': ['sibType5']}],
           'si-WindowLength': window, 'systemInfoValueTag': 5}
    if ext:
        out['nonCriticalExtension'] = {'lateNonCriticalExtension': ['SystemInformationBlockType1-v8h0-IEs', {
            'nonCriticalExtension': {'nonCriticalExtension': {'schedulingInfoListExt-r12': [
                {'si-Periodicity-r12': 'rf512', 'sib-MappingInfo-r12': ['sibType24-v1530']}]}}}]}
    return out


def group_schedule():
    def order():
        window, sched = dec.si_schedule(sib1())
        got = [(m['si'], m['period_frames'], m['sibs']) for m in sched]
        want = [(1, 8, ['sib2']), (2, 16, ['sib3', 'sib16']), (3, 64, ['sib5']), (4, 512, ['sib24'])]
        return window == 20 and got == want, (window, got)
    expect('C  schedule: SIB2 first, Rel-12 extension after the main list, periods in frames', order)

    def windows():
        _w, sched = dec.si_schedule(sib1())
        got20 = [(m['sfn_mod_period'], m['subframe']) for m in sched]
        _w, sched5 = dec.si_schedule(sib1(window='ms5'))
        got5 = [(m['sfn_mod_period'], m['subframe']) for m in sched5]
        ok = got20 == [(0, 0), (2, 0), (4, 0), (6, 0)] and got5 == [(0, 0), (0, 5), (1, 0), (1, 5)]
        return ok, (got20, got5)
    expect('C  window n opens at x = (n-1) w: frame x // 10, subframe x mod 10', windows)


def si_message(*sibs):
    return {'ok': True, 'message': ['c1', ['systemInformation', {'criticalExtensions': [
        'systemInformation-r8', {'sib-TypeAndInfo': [list(s) for s in sibs]}]}]]}


def sib1_message(value):
    return {'ok': True, 'message': ['c1', ['systemInformationBlockType1', value]]}


SIB5 = {'interFreqCarrierFreqList': [
            {'dl-CarrierFreq': 1234, 'cellReselectionPriority': 5},
            {'dl-CarrierFreq': 65535, 'cellReselectionPriority': 3}],
        'lateNonCriticalExtension': ['SystemInformationBlockType5-v8h0-IEs', {
            'nonCriticalExtension': {'interFreqCarrierFreqList-v9e0': [
                {}, {'dl-CarrierFreq-v9e0': 66500}]}}]}


def group_summary():
    def with_time():
        parsed = [(20, 5, sib1_message(sib1())), (2, 2, si_message(('sib16-v1130', sib16()))),
                  (1, 2, si_message(('sib5', SIB5))), (1, 2, {'ok': False, 'error': 'x'})]
        si = dec.summarise_si(parsed, 100)
        carriers = si['sibs']['sib5']['summary']['carriers']
        ok = (si['time'] is not None and si['time']['utc'] == UTC_WANT
              and si['blocks'] == 4 and si['blocks_failed'] == 1
              and si['sibs']['sib16']['count'] == 2 and si['sibs']['sib16']['scheduled']
              and [c['earfcn'] for c in carriers] == [1234, 66500]
              and [c['band'] for c in carriers] == [3, 66]
              and si['value_tag'] == 5 and si['window_ms'] == 20)
        return ok, json.dumps({k: si[k] for k in ('time', 'blocks', 'blocks_failed')})
    expect('D  time from SIB16, counts, a failed block, SIB5 65535 taken from the v9e0 list', with_time)

    def coverage():
        si = dec.summarise_si([(5, 5, sib1_message(sib1())), (1, 2, si_message(('sib5', SIB5)))], 100)
        cov = [(m['si'], m['covered']) for m in si['schedule']]
        heard = [m['heard'] for m in si['schedule']]
        ok = (cov == [(1, True), (2, True), (3, True), (4, False)]
              and heard == [[], [], ['sib5'], []]
              and si['time'] is None and si['time_reason'] == 'SIB16 is scheduled but was not heard')
        return ok, (cov, heard, si.get('time_reason'))
    expect('D  covered = period + one window within the capture; SIB16 scheduled, unheard', coverage)

    def short():
        si = dec.summarise_si([(5, 5, sib1_message(sib1())), (1, 2, si_message(('sib5', SIB5)))], 12)
        reason = si.get('time_reason', '')
        return 'shorter than its period, 16 frames' in reason, reason
    expect('D  SIB16 unheard in a capture shorter than its period: the reason says so', short)

    def not_scheduled():
        value = sib1(ext=False)
        value['schedulingInfoList'][1]['sib-MappingInfo'] = ['sibType3']
        si = dec.summarise_si([(5, 5, sib1_message(value)), (1, 2, si_message(('sib3', {})))], 600)
        reason = si.get('time_reason', '')
        return si['time'] is None and reason.startswith('SIB1 does not schedule SIB16'), reason
    expect('D  SIB16 not in the schedule: no network time, and the reason says so', not_scheduled)

    def sib1_only():
        si = dec.summarise_si([(5, 5, sib1_message(sib1()))], 600)
        return 'PDSCH_ALL_SF' in si.get('note', '') and si['sibs'] == {}, si.get('note')
    expect('D  only SIB1 heard: a note names the outside PDSCH_ALL_SF change', sib1_only)

    def no_sib1():
        si = dec.summarise_si([(1, 2, si_message(('sib5', SIB5)))], 600)
        return si['schedule'] is None and si['time_reason'] == 'no SIB1 among the blocks', si
    expect('D  no SIB1 among the blocks: no schedule', no_sib1)


def pycrate_python():
    cand = os.environ.get('FMRX_TEST_PYCRATE_PYTHON') or sys.executable
    try:
        proc = subprocess.run([cand, '-c', 'from pycrate_asn1dir import RRCLTE'],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cand if proc.returncode == 0 else None


# Runs under the pycrate Python. Made-up values only: PLMN 001-01, TAC 0x1234, EARFCN 1234.
ENCODER = r'''
import json, sys
from pycrate_asn1dir import RRCLTE
M = RRCLTE.EUTRA_RRC_Definitions.BCCH_DL_SCH_Message
units = int(sys.argv[1])
sib1 = {
    'cellAccessRelatedInfo': {
        'plmn-IdentityList': [{'plmn-Identity': {'mcc': [0, 0, 1], 'mnc': [0, 1]},
                               'cellReservedForOperatorUse': 'notReserved'}],
        'trackingAreaCode': (0x1234, 16), 'cellIdentity': (0x0ABCD01, 28),
        'cellBarred': 'notBarred', 'intraFreqReselection': 'allowed', 'csg-Indication': False},
    'cellSelectionInfo': {'q-RxLevMin': -70},
    'freqBandIndicator': 13,
    'schedulingInfoList': [{'si-Periodicity': 'rf8', 'sib-MappingInfo': []},
                           {'si-Periodicity': 'rf16', 'sib-MappingInfo': ['sibType5', 'sibType16-v1130']}],
    'si-WindowLength': 'ms20', 'systemInfoValueTag': 3}
carrier = {'dl-CarrierFreq': 1234, 'q-RxLevMin': -64, 't-ReselectionEUTRA': 2,
           'threshX-High': 2, 'threshX-Low': 0, 'allowedMeasBandwidth': 'mbw50',
           'presenceAntennaPort1': True, 'cellReselectionPriority': 5, 'neighCellConfig': (1, 2)}
time = {'timeInfo-r11': {'timeInfoUTC-r11': units, 'dayLightSavingTime-r11': (1, 2),
                         'leapSeconds-r11': 18, 'localTimeOffset-r11': -20}}
si = {'criticalExtensions': ('systemInformation-r8', {'sib-TypeAndInfo': [
    ('sib5', {'interFreqCarrierFreqList': [carrier]}), ('sib16-v1130', time)]})}
out = []
for body in (('systemInformationBlockType1', sib1), ('systemInformation', si)):
    M.set_val({'message': ('c1', body)})
    out.append(M.to_uper().hex())
print(json.dumps(out))
'''


def record(sub, data):
    return bytes([0xA5, sub, len(data) & 0xFF, len(data) >> 8]) + data


def group_pycrate():
    py = pycrate_python()
    if py is None:
        print('  SKIP E  pycrate end to end (no Python that imports pycrate)')
        return
    tmp = tempfile.mkdtemp(prefix='iqlte-si-test-')
    try:
        proc = subprocess.run([py, '-c', ENCODER, str(UTC_10MS)], capture_output=True, timeout=60)
        if proc.returncode != 0:
            report('E  encode SIB1 and an SI message with pycrate', False,
                   proc.stderr.decode(errors='replace').strip()[-300:])
            return
        sib1_hex, si_hex = json.loads(proc.stdout)
        blob = (record(5, bytes.fromhex(sib1_hex)) * 3 + record(2, bytes.fromhex(si_hex))
                + record(2, bytes([0xFF] * 9)))
        os.makedirs(os.path.join(tmp, 'pyc', 'bin'))
        # A wrapper, not a symlink: a venv's Python finds its packages only from its own path.
        wrapper = os.path.join(tmp, 'pyc', 'bin', 'python')
        with open(wrapper, 'w') as fh:
            fh.write('#!/bin/sh\nexec %s "$@"\n' % shlex.quote(py))
        os.chmod(wrapper, 0o755)

        def end_to_end():
            si, why = dec.read_si(blob, tmp, 40)
            if si is None:
                return False, why
            carriers = si['sibs']['sib5']['summary']['carriers']
            ok = (si['time'] == {'utc': UTC_WANT, 'utc_10ms': UTC_10MS, 'leap_seconds': 18,
                                 'gps': '2026-01-02T03:04:23.670Z', 'local_offset_minutes': -300,
                                 'local': '2026-01-01T22:04:05.670-05:00', 'dst_hours': 1}
                  and carriers == [{'earfcn': 1234, 'band': 3, 'mhz': 1808.4, 'priority': 5}]
                  and si['blocks'] == 3 and si['blocks_failed'] == 1
                  and [m['sibs'] for m in si['schedule']] == [['sib2'], ['sib5', 'sib16']]
                  and si['schedule'][1]['covered'] and si['value_tag'] == 3)
            return ok, json.dumps({k: si.get(k) for k in ('time', 'blocks', 'blocks_failed', 'schedule')})
        expect('E  pycrate: SIB1 schedule, SIB16 time, SIB5 carrier, one broken block counted', end_to_end)

        def missing():
            os.remove(os.path.join(tmp, 'pyc', 'bin', 'python'))
            si, why = dec.read_si(blob, tmp, 40)
            return si is None and 'interpreter is missing' in why, why
        expect('E  no pycrate interpreter: si None with a reason', missing)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def group_pieces():
    import numpy as np
    from fm_receiver import iqlte

    rng = np.random.default_rng(7)
    n, fs, rate = 300_000, 20e6, dec.RATE_10
    spec = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    spec[np.abs(np.fft.fftfreq(n, 1 / fs)) > 0.4 * rate] = 0      # well inside the output band
    x = np.fft.ifft(spec).astype(np.complex64)
    whole = iqlte._resample(x, fs, rate).astype(np.complex64)
    reads = []

    def read(i0, i1):
        reads.append(i1 - i0)
        return x[i0:i1]
    saved = dec._PIECE, dec._GUARD
    dec._PIECE, dec._GUARD = 1 << 15, 1 << 11
    try:
        with tempfile.TemporaryFile() as fh:
            written, power = dec._write_resampled(dec.SampleSource(n, fs, read), rate, fh)
            fh.seek(0)
            got = np.frombuffer(fh.read(), dtype=np.complex64)
    finally:
        dec._PIECE, dec._GUARD = saved

    def matches():
        mid = slice(2000, whole.size - 2000)
        err = float(np.sqrt(np.mean(np.abs(got[mid] - whole[mid]) ** 2) / np.mean(np.abs(whole[mid]) ** 2)))
        ok = written == whole.size == got.size and err < 1e-2 and power > 0
        return ok, f'written {written} want {whole.size} rms error {err:.2e}'
    expect('F  piecewise resample (8 pieces, guards dropped) matches the whole-array resample', matches)

    def bounded():
        biggest = max(reads)
        return len(reads) >= 8 and biggest <= (1 << 15) + 2 * (1 << 11) + 250, f'{len(reads)} reads, largest {biggest}'
    expect('F  no read is longer than a piece and its two guards', bounded)


GROUPS = [group_frequencies, group_time, group_schedule, group_summary, group_pycrate, group_pieces]


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
