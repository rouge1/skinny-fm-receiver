"""Hand an NR cell's SSB to an outside decoder and read its broadcast: MIB, SIB1, the
other SI and the network time in SIB9.

``tools/iq-nr --decode`` is the only caller. The outside program is
``$FMRX_LTE_TOOLS/run_nr.sh`` (srsRAN 4G's NR library, AGPL, not part of this repo).
Nothing here decodes: this module cuts and resamples the samples, runs that program,
reads its tagged lines, and parses the RRC messages it hands back with pycrate in a
subprocess (``$FMRX_LTE_TOOLS/pyc/bin/python``; this module never imports pycrate).
Only the broadcast channel is read: the program searches the SI-RNTI (0xFFFF) and
nothing else.

Method, for one SSB entry of ``iqnr.ssb_search`` (``decode_ssb``):

1. A :data:`SampleSource` over the span is mixed so the SSB sits at 0 Hz, band-limited
   and resampled piece by piece to 23.04 MS/s (a 106-PRB grid at 15 kHz) into a cf32
   file in a private temporary folder (``iqlte_decode._write_resampled``; the span is
   never held whole: 6 s at 40 MS/s is 1.9 GB as complex64). The folder is deleted in
   every case.
2. Pass 1: ``run_nr.sh FILE 23040000 0 SSB_HZ SCS``. Lines are ``TAG {json}``; any
   other line, or one that does not parse (a truncated last line), is ignored.
   ``NR_MIB`` / ``NR_NO_MIB``, ``NR_CORESET0`` (point A), ``NR_SI`` (``sii`` 0 is SIB1,
   1 another SI message), ``NR_SI_CRC_FAIL``, ``NR_DONE`` (with a ``reason`` when it
   stopped early). SIB1 is the most common ``sii`` 0 block.
3. SIB1 (and later every other block) is parsed by ``_NR_SI_HELPER`` under the pycrate
   interpreter: ``RRCNR.NR_RRC_Definitions.BCCH_DL_SCH_Message``, ``from_uper``.
4. When SIB1 schedules other SI and carries ``offsetToPointA``, pass 2 adds ``-a`` (that
   offset) and ``-t`` (the ``pdsch-TimeDomainAllocationList`` ``startSymbolAndLength``
   values in list order; left out when SIB1 has none). Both passes' distinct blocks
   are parsed and summarised.

Time (SIB9 ``timeInfo``): ``timeInfoUTC`` counts 10 ms units from 1900-01-01 00:00:00
UTC, exactly as LTE SIB16, so ``iqlte_decode.sib16_time`` does the arithmetic.
``referenceTimeInfo-r16``, when present, is given as its raw fields plus ``gps_reading``:
GPS epoch 1980-01-06 + refDays days + refSeconds s + refMilliSeconds ms +
refTenNanoSeconds x 10 ns. That is the reading that agreed with timeInfoUTC on air;
check against 38.331.

Wrong turns:

- The ARFCN of point A is not in SIB1 (``FrequencyInfoDL-SIB`` has only the offset), so
  it is worked back from ``NR_CORESET0`` ``pointA_hz`` through the global raster.
- ``blocks.crc_fail`` is the largest count of ``NR_SI_CRC_FAIL`` lines over the passes,
  not the sum: both passes read the same span, so the same blocks fail twice.
  ``NR_SI_ERR`` lines are diagnostic and not counted.
- The pycrate interpreter comes from the tools folder only. An environment override
  would let a test's "no pycrate" folder still find one.
- A SIB1 that fails to parse is not the end: the next most common ``sii`` 0 block is
  tried, so one corrupt block that passed the CRC does not hide the cell.
"""

import collections
import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from fm_receiver.iqlte_decode import (SampleSource, ToolsMissing, _find_key, _iso, _sib_name,
                                      _write_resampled, nr_arfcn_mhz, require_program,
                                      sib16_time, tools_dir)

RATE = 23040000                      # the outside program's sample rate: 106 PRB at 15 kHz
DEFAULT_SECONDS = 6.0
MAX_SECONDS = 10.0
_GPS_EPOCH = datetime.datetime(1980, 1, 6, tzinfo=datetime.timezone.utc)
_MIB_KEYS = ('pci', 'sfn', 'ssb_index', 'half_frame', 'scs_common_khz', 'ssb_subcarrier_offset',
             'dmrs_typeA_position', 'coreset0_index', 'search_space0_index', 'cell_barred',
             'intra_freq_reselection', 'ssb_sample', 'cfo_hz', 'snr_db')

__all__ = ['SampleSource', 'ToolsMissing', 'cap_seconds', 'decode_ssb', 'point_a_arfcn', 'RATE',
           'DEFAULT_SECONDS', 'MAX_SECONDS']

# Runs under the pycrate interpreter. argv[1] is a JSON file holding a list of hex strings,
# each a BCCH-DL-SCH-Message (UPER, padded). Prints one result per block as plain JSON
# (a tuple, which is a CHOICE or a BIT STRING, becomes a list; bytes become hex).
_NR_SI_HELPER = r'''
import json
import sys

def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return value

def _decode(obj, data):
    """from_uper, dropping a trailing 0x00 pad until it decodes."""
    blob = data
    while True:
        try:
            obj.from_uper(blob)
            return obj.get_val()
        except Exception:
            if not blob.endswith(b"\x00") or len(blob) < 2:
                raise
            blob = blob[:-1]

def main():
    try:
        from pycrate_asn1dir import RRCNR
    except ImportError as exc:
        sys.stderr.write("pycrate is not available: %s\n" % exc)
        return 1
    msg = RRCNR.NR_RRC_Definitions.BCCH_DL_SCH_Message
    out = []
    for hexed in json.load(open(sys.argv[1])):
        try:
            val = _decode(msg, bytes.fromhex(hexed))
            out.append({"ok": True, "message": _plain(val["message"])})
        except Exception as exc:
            out.append({"ok": False, "error": str(exc)[:200]})
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def cap_seconds(seconds):
    """``(seconds, reason)``: ``seconds`` cut to :data:`MAX_SECONDS` with a reason, or as is."""
    seconds = float(seconds)
    if seconds > MAX_SECONDS:
        return MAX_SECONDS, 'requested %g s; decoding at most %g s' % (seconds, MAX_SECONDS)
    return seconds, None


def point_a_arfcn(hz):
    """The NR-ARFCN of ``hz`` on the global raster (the inverse of ``nr_arfcn_mhz``)."""
    mhz = float(hz) / 1e6
    if mhz < 3000.0:
        return int(round(mhz / 0.005))
    if mhz < 24250.08:
        return int(round(600000 + (mhz - 3000.0) / 0.015))
    return int(round(2016667 + (mhz - 24250.08) / 0.06))


# ---- the outside program ----------------------------------------------------------------------

def _run(script, args, timeout):
    """``(lines, error)``: the stdout lines of one run_nr.sh call, and why it failed (or None)."""
    try:
        proc = subprocess.run([script] + [str(a) for a in args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], 'run_nr.sh timed out after %d s' % timeout
    except OSError as exc:
        return [], 'run_nr.sh could not start (%s)' % exc
    text = proc.stdout.decode('utf-8', errors='replace')
    err = None
    if proc.returncode != 0:
        tail = proc.stderr.decode('utf-8', errors='replace').strip().splitlines()
        err = 'run_nr.sh exited %d%s' % (proc.returncode, ': ' + tail[-1] if tail else '')
    return text.split('\n')[:-1] if text.endswith('\n') else text.split('\n'), err


def _tagged(lines):
    """``[(tag, object)]`` for the lines that are ``TAG {json}``; every other line is dropped."""
    out = []
    for line in lines:
        tag, _, rest = line.partition(' ')
        if not tag.startswith('NR_') or not rest:
            continue
        try:
            obj = json.loads(rest)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append((tag, obj))
    return out


class _Pass:
    """What one run_nr.sh call said."""

    def __init__(self, lines, error):
        self.error = error
        self.mib = None
        self.no_mib = None
        self.coreset0 = None
        self.done = None
        self.blocks = []                       # (sii, hex)
        self.crc_fail = 0
        for tag, obj in _tagged(lines):
            if tag == 'NR_MIB':
                self.mib = obj
            elif tag == 'NR_NO_MIB':
                self.no_mib = obj
            elif tag == 'NR_CORESET0':
                self.coreset0 = obj
            elif tag == 'NR_SI' and isinstance(obj.get('hex'), str):
                self.blocks.append((1 if obj.get('sii') else 0, obj['hex'].strip().lower()))
            elif tag == 'NR_SI_CRC_FAIL':
                self.crc_fail += 1
            elif tag == 'NR_DONE':
                self.done = obj


# ---- pycrate ----------------------------------------------------------------------------------

def _parse_blocks(hexes, tools):
    """``(results, None)``, one ``{"ok", "message"|"error"}`` per hex string, or ``(None, reason)``."""
    python = os.path.join(tools, 'pyc', 'bin', 'python') if tools else ''
    if not python or not os.path.isfile(python):
        return None, 'pycrate interpreter is missing (%s)' % (python or 'pyc/bin/python')
    tmp = tempfile.mkdtemp(prefix='iqnr-si-')
    try:
        script = os.path.join(tmp, 'si_parse.py')
        listing = os.path.join(tmp, 'blocks.json')
        with open(script, 'w') as fh:
            fh.write(_NR_SI_HELPER)
        with open(listing, 'w') as fh:
            json.dump(list(hexes), fh)
        try:
            proc = subprocess.run([python, script, listing], capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None, 'SI parse failed: pycrate timed out'
        except OSError as exc:
            return None, 'pycrate interpreter failed to start (%s)' % exc
        if proc.returncode != 0:
            err = proc.stderr.decode('utf-8', errors='replace').strip()
            tail = err.splitlines()[-1] if err else 'exit %d' % proc.returncode
            if 'pycrate' in err.lower() or 'ModuleNotFoundError' in err:
                return None, 'pycrate is not available (%s)' % tail
            return None, 'SI parse failed: %s' % tail
        try:
            results = json.loads(proc.stdout.decode('utf-8'))
        except ValueError:
            return None, 'SI parse failed: pycrate printed no JSON'
        if not isinstance(results, list) or len(results) != len(hexes):
            return None, 'SI parse failed: pycrate gave the wrong number of results'
        return [r if isinstance(r, dict) else {'ok': False} for r in results], None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _body(result):
    """``(kind, value)`` of a parsed message: ``systemInformationBlockType1`` or ``systemInformation``."""
    msg = result.get('message') if result.get('ok') else None
    if isinstance(msg, list) and len(msg) == 2 and isinstance(msg[1], list) and len(msg[1]) == 2:
        return msg[1][0], msg[1][1]
    return None, None


# ---- SIB1 -------------------------------------------------------------------------------------

def _int(value):
    """An INTEGER, or a BIT STRING ``[value, nbits]`` or hex string, as an int; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return value[0]
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def _digits(seq):
    return ''.join(str(int(d)) for d in seq)


def _plmns(sib1):
    """Every ``"MCC-MNC"`` of every ``plmn-IdentityInfoList`` entry (an omitted MCC repeats the last)."""
    out = []
    mcc = None
    access = sib1.get('cellAccessRelatedInfo') if isinstance(sib1, dict) else None
    for info in (access or {}).get('plmn-IdentityInfoList') or []:
        for ident in (info or {}).get('plmn-IdentityList') or []:
            if ident.get('mcc'):
                mcc = _digits(ident['mcc'])
            if mcc is not None and ident.get('mnc'):
                out.append('%s-%s' % (mcc, _digits(ident['mnc'])))
    return out


def _first_info(sib1, key):
    access = sib1.get('cellAccessRelatedInfo') if isinstance(sib1, dict) else None
    for info in (access or {}).get('plmn-IdentityInfoList') or []:
        value = _int((info or {}).get(key))
        if value is not None:
            return value
    return None


def _scs(enum):
    match = re.match(r'kHz(\d+)$', str(enum))
    return int(match.group(1)) if match else None


def _prefixed(enum, prefix):
    """``rf16`` -> 16, ``s20`` -> 20; None when it is not that form."""
    match = re.match(prefix + r'(\d+)$', str(enum))
    return int(match.group(1)) if match else None


def _tdra(sib1):
    """SIB1's ``startSymbolAndLength`` values in list order (empty when it has no list)."""
    found = _find_key(sib1, 'pdsch-TimeDomainAllocationList')
    if isinstance(found, list) and len(found) == 2 and isinstance(found[0], str):
        found = found[1]                       # setup / release choice
    out = []
    for row in found if isinstance(found, list) else []:
        if isinstance(row, dict) and isinstance(row.get('startSymbolAndLength'), int):
            out.append(row['startSymbolAndLength'])
    return out


def _schedule(sib1):
    """``(window_slots, entries)``: SIB1's SI messages in order."""
    info = sib1.get('si-SchedulingInfo') if isinstance(sib1, dict) else None
    info = info if isinstance(info, dict) else {}
    entries = []
    for item in info.get('schedulingInfoList') or []:
        sibs = []
        for mapping in item.get('sib-MappingInfo') or []:
            name = _sib_name(mapping.get('type'))
            if name not in sibs:
                sibs.append(name)
        entries.append({'periodicity_rf': _prefixed(item.get('si-Periodicity'), 'rf'),
                        'broadcast_status': item.get('si-BroadcastStatus'), 'sibs': sibs})
    return _prefixed(info.get('si-WindowLength'), 's'), entries


def _offset_to_point_a(sib1):
    value = _find_key(sib1, 'offsetToPointA')
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _carrier(sib1, point_a_hz):
    freq = _find_key(sib1, 'frequencyInfoDL')
    if not isinstance(freq, dict):
        return None
    bands = [b.get('freqBandIndicatorNR') for b in freq.get('frequencyBandList') or []
             if isinstance(b, dict) and b.get('freqBandIndicatorNR') is not None]
    first = next((c for c in freq.get('scs-SpecificCarrierList') or [] if isinstance(c, dict)), {})
    arfcn = point_a_arfcn(point_a_hz) if point_a_hz else None
    return {'point_a_arfcn': arfcn, 'point_a_mhz': nr_arfcn_mhz(arfcn) if arfcn is not None else None,
            'bands': bands, 'carrier_prb': first.get('carrierBandwidth'),
            'scs_khz': _scs(first.get('subcarrierSpacing')),
            'offset_to_point_a': _offset_to_point_a(sib1)}


def _summarise_sib1(sib1, mib, point_a_hz):
    window, entries = _schedule(sib1)
    return {'plmns': _plmns(sib1), 'tac': _first_info(sib1, 'trackingAreaCode'),
            'cell_identity': _first_info(sib1, 'cellIdentity'),
            'cell_barred': mib.get('cell_barred'), 'si_window_slots': window,
            'si_schedule': entries, 'carrier': _carrier(sib1, point_a_hz)}


# ---- the other SI and SIB9 --------------------------------------------------------------------

def _gps_reading(ref):
    """ISO 8601 GPS time of a ``time-r16`` value, or None."""
    fields = [ref.get(k) for k in ('refDays-r16', 'refSeconds-r16', 'refMilliSeconds-r16',
                                   'refTenNanoSeconds-r16')]
    if not all(isinstance(v, int) for v in fields):
        return None
    days, secs, ms, tens = fields
    when = _GPS_EPOCH + datetime.timedelta(days=days, seconds=secs, milliseconds=ms,
                                           microseconds=tens * 10 / 1000.0)
    return _iso(when)


def _sib9_time(sib9):
    """The ``time`` object of a SIB9 value, or None when it carries no ``timeInfo``."""
    info = sib9.get('timeInfo') if isinstance(sib9, dict) else None
    units = _int(info.get('timeInfoUTC')) if isinstance(info, dict) else None
    if units is None:
        return None
    as_lte = {'timeInfoUTC-r11': units}
    for key in ('dayLightSavingTime', 'leapSeconds', 'localTimeOffset'):
        if info.get(key) is not None:
            as_lte[key + '-r11'] = info[key]
    out = sib16_time({'timeInfo-r11': as_lte})
    if out is None:
        return None
    out.pop('utc_10ms', None)
    ref = _find_key(sib9, 'referenceTimeInfo-r16')
    if isinstance(ref, dict):
        raw = dict(ref.get('time-r16') or {})
        raw.update({k: v for k, v in ref.items() if k != 'time-r16'})
        reading = _gps_reading(ref.get('time-r16') or {})
        raw['gps_reading'] = reading
        out['reference_time'] = raw
    return out


def _time_reason(sib1_out, si_out, seconds, searched):
    """Why there is no ``time``."""
    entries = [e for e in sib1_out['si_schedule'] if 'sib9' in e['sibs']]
    if not entries:
        return 'SIB1 does not schedule SIB9'
    if all(str(e.get('broadcast_status')).lower() == 'notbroadcasting' for e in entries):
        return 'SIB9 is not broadcast (si-BroadcastStatus notBroadcasting: on demand)'
    if si_out and 'sib9' in si_out['sibs']:
        return 'SIB9 was heard but carries no timeInfo'
    reason = 'SIB9 scheduled but not heard in %g s' % round(seconds, 3)
    if not searched:
        reason += ' (the other SI were not searched: ' + searched_why(sib1_out) + ')'
    return reason


def searched_why(sib1_out):
    carrier = sib1_out.get('carrier') or {}
    if carrier.get('offset_to_point_a') is None:
        return 'SIB1 has no offsetToPointA'
    return 'pass 2 did not run'


# ---- the decode -------------------------------------------------------------------------------

def _common(blocks, sii):
    """Distinct hex blocks of one ``sii`` as ``[(count, hex)]``, most common first (first seen on ties)."""
    count = collections.Counter(h for s, h in blocks if s == sii)
    order = list(dict.fromkeys(h for s, h in blocks if s == sii))
    return sorted(((count[h], h) for h in order), key=lambda ch: -ch[0])


def _write_span(src, centre_hz, ssb_hz, path):
    """The span of ``src`` mixed so ``ssb_hz`` is at 0 Hz and resampled to :data:`RATE`."""
    delta = float(ssb_hz) - float(centre_hz)
    fs = float(src.fs)

    def read(i0, i1):
        part = np.asarray(src.read(i0, i1), dtype=np.complex64)
        if abs(delta) >= 1e-3:
            turns = (delta * np.arange(i0, i0 + part.size, dtype=np.float64) / fs) % 1.0
            part = (part * np.exp(-2j * np.pi * turns)).astype(np.complex64)
        return part

    with open(path, 'wb') as fh:
        written, _power = _write_resampled(SampleSource(src.n, fs, read), RATE, fh)
    return written


def _join(*parts):
    return '; '.join(p for p in parts if p) or None


def decode_ssb(src, centre_hz, ssb, tools=None, cut_reason=None):
    """The ``decode`` object for one ``iqnr.ssb_search`` entry.

    ``src`` is a :data:`SampleSource` of the span to read (the file's own samples, centre
    ``centre_hz``); ``ssb`` has ``ssb_hz`` and ``scs_khz``. ``cut_reason`` (from
    :func:`cap_seconds`) is carried into ``reason``. Raises :class:`ToolsMissing` when
    ``run_nr.sh`` is not under ``tools`` (default ``$FMRX_LTE_TOOLS``). A missing pycrate
    interpreter is not an error: ``sib1`` is null and ``reason`` names it.
    """
    tools = tools if tools is not None else tools_dir()
    script = require_program(tools, 'run_nr.sh')
    seconds = src.n / float(src.fs) if src.fs else 0.0
    timeout = max(120, int(30 * seconds))
    ssb_hz = float(ssb['ssb_hz'])
    scs = int(ssb.get('scs_khz') or 15)
    tmp = tempfile.mkdtemp(prefix='iqnr-')
    try:
        path = os.path.join(tmp, 'ssb.cf32')
        _write_span(src, centre_hz, ssb_hz, path)
        base = [path, RATE, 0, int(round(ssb_hz)), scs]
        first = _Pass(*_run(script, base, timeout))
        passes = [first]

        out = {'ran': True, 'mib': None, 'sib1': None, 'si': None, 'time': None,
               'time_reason': None, 'reason': None,
               'blocks': {'sib1': 0, 'other': 0, 'crc_fail': 0}, 'seconds': round(seconds, 6)}
        notes = [cut_reason, first.error]

        def finish(reason_extra=None):
            blocks = [b for p in passes for b in p.blocks]
            out['blocks'] = {'sib1': sum(1 for s, _h in blocks if s == 0),
                             'other': sum(1 for s, _h in blocks if s != 0),
                             'crc_fail': max(p.crc_fail for p in passes)}
            out['reason'] = _join(*notes, reason_extra)
            return out

        if first.mib is None:
            out['time_reason'] = 'no MIB'
            return finish((first.no_mib or {}).get('reason') or 'no MIB decoded')
        out['mib'] = {k: first.mib.get(k) for k in _MIB_KEYS}
        out['mib'].update({k: v for k, v in first.mib.items() if k not in out['mib']})
        done_reason = (first.done or {}).get('reason')
        point_a = (first.coreset0 or {}).get('pointA_hz')

        sib1 = None
        candidates = _common(first.blocks, 0)
        if candidates:
            results, why = _parse_blocks([h for _c, h in candidates], tools)
            if results is None:
                notes.append(why)
            else:
                for (_c, _h), res in zip(candidates, results):
                    kind, value = _body(res)
                    if kind == 'systemInformationBlockType1' and isinstance(value, dict):
                        sib1 = value
                        break
                if sib1 is None:
                    notes.append('no SIB1 block parsed')
        if sib1 is None:
            out['time_reason'] = 'no SIB1'
            return finish(done_reason or (None if candidates else
                                          'no SIB1 heard in %g s' % round(seconds, 3)))
        out['sib1'] = _summarise_sib1(sib1, out['mib'], point_a)

        _window, entries = _schedule(sib1)
        offset = _offset_to_point_a(sib1)
        searched = False
        if entries and offset is not None:
            args = list(base)
            slivs = _tdra(sib1)
            if slivs:
                args += ['-t', ','.join(str(v) for v in slivs)]
            args += ['-a', offset]
            second = _Pass(*_run(script, args, timeout))
            passes.append(second)
            searched = True
            notes.append(second.error)

        if entries:
            si_blocks = _common([b for p in passes for b in p.blocks], 1)
            heard, sibs = {}, {}
            if si_blocks:
                results, why = _parse_blocks([h for _c, h in si_blocks], tools)
                if results is None:
                    notes.append(why)
                else:
                    for (count, _h), res in zip(si_blocks, results):
                        kind, value = _body(res)
                        if kind != 'systemInformation':
                            continue
                        ext = value.get('criticalExtensions') if isinstance(value, dict) else None
                        ies = ext[1] if isinstance(ext, list) and len(ext) == 2 else {}
                        for item in (ies.get('sib-TypeAndInfo') or []) if isinstance(ies, dict) else []:
                            if isinstance(item, list) and len(item) == 2:
                                name = _sib_name(item[0])
                                heard[name] = heard.get(name, 0) + count
                                sibs.setdefault(name, item[1])
            scheduled = []
            for entry in out['sib1']['si_schedule']:
                scheduled += [n for n in entry['sibs'] if n not in scheduled]
            on_air = [n for e in out['sib1']['si_schedule']
                      if str(e.get('broadcast_status')).lower() != 'notbroadcasting' for n in e['sibs']]
            out['si'] = {'sibs_heard': heard, 'sibs': sibs, 'scheduled': scheduled,
                         'missing': [n for n in dict.fromkeys(on_air) if n not in heard]}
            if out['si']['missing'] and not searched:
                notes.append('the other SI were not searched (%s)' % searched_why(out['sib1']))
            elif out['si']['missing']:
                notes.append('not heard in %g s: %s' % (round(seconds, 3), ', '.join(out['si']['missing'])))
            if 'sib9' in sibs:
                out['time'] = _sib9_time(sibs['sib9'])

        if out['time'] is None:
            out['time_reason'] = _time_reason(out['sib1'], out['si'], seconds, searched or not entries)
        return finish()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
