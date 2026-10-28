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
5. Every distinct block of both passes is parsed, whatever its ``sii``: a SystemInformation
   message is other SI wherever it came, and ``si.unscheduled`` lists the SIBs heard that
   SIB1 does not schedule. A block of all zero bytes is dropped and counted in
   ``crc_fail``: it passes the CRC (whose register starts at zero) and parses as a
   SystemInformation holding a SIB2 of defaults, but it is the program's output for a block
   it had next to nothing of. On air every "SIB2 in SIB1's slots" of 2026-10-23 was such a
   block: the barred cell's SIB1 repeats with redundancy version 1, which carry no
   systematic bits (our own decoder, 2026-10-24).
6. Pass 1 reads every slot's CORESET 0 for SI-RNTI DCIs, so it sees other SI in its own
   slots too, but cannot decode it (its DM-RS counts from CRB 0, so it needs ``-a``): those
   blocks are ``NR_SI_CRC_FAIL`` / ``NR_SI_ERR`` with ``sii`` 1. When pass 1 had any and
   SIB1 schedules nothing, pass 2 runs all the same (``-a``, and ``-t`` when SIB1 has a
   list). Pass 1's ``sii`` 0 failures are SIB1: its repeats with redundancy versions 1, 2
   and 3, which do not decode alone, and copies with version 0 that failed, not another
   message.

Time (SIB9 ``timeInfo``): ``timeInfoUTC`` counts 10 ms units from 1900-01-01 00:00:00
UTC, exactly as LTE SIB16, so ``iqlte_decode.sib16_time`` does the arithmetic.
``referenceTimeInfo-r16``, when present, is given as its raw fields plus ``gps_reading``:
GPS epoch 1980-01-06 + refDays days + refSeconds s + refMilliSeconds ms +
refTenNanoSeconds x 10 ns. That is the reading that agreed with timeInfoUTC on air;
check against 38.331.

Wrong turns:

- Point A is not in SIB1 (``FrequencyInfoDL-SIB`` has only the offset), so it is worked
  out from the SSB (38.211 4.4.4.2, 7.4.3.1; FR1): ``ssb_hz - 120 * ssb_scs - k_SSB * 15 kHz
  - offsetToPointA * 12 * 15 kHz``, k_SSB being the MIB's ``ssb_subcarrier_offset``; the
  ARFCN is that on the global raster (``point_a_arfcn``). The formula is FR1, SSB at 15
  or 30 kHz via the scs field; FR2 is out of scope for this rule. ``NR_CORESET0`` ``pointA_hz`` is the outside
  program's stand-in grid (5.4 MHz off on air) and is not used. Without
  ``offsetToPointA`` or the MIB's k_SSB the carrier is null.
- ``blocks.crc_fail`` is the largest count of ``NR_SI_CRC_FAIL`` lines over the passes,
  not the sum: both passes read the same span, so the same blocks fail twice.
  ``NR_SI_ERR`` lines are diagnostic and not counted.
- The pycrate interpreter comes from the tools folder only. An environment override
  would let a test's "no pycrate" folder still find one.
- The outside program decodes the strongest cell on the SSB's frequency unless given
  ``-P PCI``. A weaker sector there (another PCI, same SSB frequency) came back with the
  strongest cell's MIB and SIB1 under its own PCI. When the MIB's PCI is not the entry's,
  pass 1 is run again with ``-P`` and the entry's PCI, and that run (and pass 2, with
  ``-P`` too) is the decode; its MIB has ``targeted`` true and the strongest cell's PCI.
  When the retry reads no MIB of that PCI, or the program refuses ``-P`` (an older one),
  the decode stops with ``decoded_pci`` (the PCI pass 1 read), MIB and SIB1 null, and a
  ``reason`` naming both PCIs. On air a sector 7 dB under the strongest gave its MIB this
  way but no SIB1: its SIB1 comes in the same slots and RBs as the stronger cell's. The
  program's ``snr_db`` and ``cfo_hz`` for an aimed cell were wrong (-21 dB, -552 Hz), so
  they are null in its ``mib``; the SSB entry's ``relative_power_db`` and ``cfo_hz`` stand.
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
                                      _write_resampled, require_program,
                                      sib16_time, tools_dir)

RATE = 23040000                      # the outside program's sample rate: 106 PRB at 15 kHz
DEFAULT_SECONDS = 6.0
MAX_SECONDS = 10.0
_GPS_EPOCH = datetime.datetime(1980, 1, 6, tzinfo=datetime.timezone.utc)
_MIB_KEYS = ('pci', 'sfn', 'ssb_index', 'half_frame', 'scs_common_khz', 'ssb_subcarrier_offset',
             'dmrs_typeA_position', 'coreset0_index', 'search_space0_index', 'cell_barred',
             'intra_freq_reselection', 'ssb_sample', 'cfo_hz', 'snr_db')

__all__ = ['SampleSource', 'ToolsMissing', 'cap_seconds', 'decode_ssb', 'own_decode', 'point_a_arfcn', 'RATE', 'si_config',
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
        self.osi_failed = 0                    # sii 1 blocks that did not decode
        self.zero = 0                          # "decoded" blocks of all zero bytes, dropped
        for tag, obj in _tagged(lines):
            if tag == 'NR_MIB':
                self.mib = obj
            elif tag == 'NR_NO_MIB':
                self.no_mib = obj
            elif tag == 'NR_CORESET0':
                self.coreset0 = obj
            elif tag == 'NR_SI' and isinstance(obj.get('hex'), str):
                block = obj['hex'].strip().lower()
                if block and not block.strip('0'):
                    # All zero bytes pass a CRC whose register starts at zero: the program's
                    # answer for a block it had next to nothing of, not a message.
                    self.crc_fail += 1
                    self.zero += 1
                    self.osi_failed += 1 if obj.get('sii') == 1 else 0
                    continue
                self.blocks.append((1 if obj.get('sii') else 0, block))
            elif tag == 'NR_SI_CRC_FAIL':
                self.crc_fail += 1
                self.osi_failed += 1 if obj.get('sii') == 1 else 0
            elif tag == 'NR_SI_ERR':
                self.osi_failed += 1 if obj.get('sii') == 1 else 0
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


def _sliv(value):
    """(S, L) of a startSymbolAndLength (38.214 5.1.2.1), or None."""
    a, b = divmod(int(value), 14)
    for s, l in ((b, a + 1), (13 - b, 15 - a)):
        if 0 <= s < 14 and 1 <= l <= 14 - s and int(value) == (
                14 * (l - 1) + s if l - 1 <= 7 else 14 * (14 - l + 1) + (13 - s)):
            return s, l
    return None


def si_config(sib1):
    """What our own other-SI decode (``iqnr.other_si``) needs from a parsed SIB1, or None:
    ``offset_to_point_a``, ``tdra`` ((K0, mapping, S, L) rows of pdsch-ConfigCommon), the SI
    ``window_slots``, ``periods_rf`` (each message's periodicity, in order) and ``search``,
    the search space for other SI (searchSpaceOtherSystemInformation in commonSearchSpaceList;
    0 or none is search space 0's Type0 candidates on symbol 0 of every slot)."""
    if not isinstance(sib1, dict):
        return None
    window, entries = _schedule(sib1)
    offset = _offset_to_point_a(sib1)
    if not entries or window is None or offset is None:
        return None
    found = _find_key(sib1, 'pdsch-TimeDomainAllocationList')
    if isinstance(found, list) and len(found) == 2 and isinstance(found[0], str):
        found = found[1]
    tdra = []
    for row in found if isinstance(found, list) else []:
        sl = _sliv(row.get('startSymbolAndLength', -1)) if isinstance(row, dict) else None
        if sl is None:
            tdra = []
            break
        kind = 'B' if str(row.get('mappingType')).lower().endswith('b') else 'A'
        tdra.append((int(row.get('k0') or 0), kind, sl[0], sl[1]))
    search = {'counts': ((4, 4), (8, 2), (16, 1)), 'first_symbols': (0,), 'period_slots': 1,
              'offset_slots': 0}
    ss_id = _find_key(sib1, 'searchSpaceOtherSystemInformation')
    for ss in _find_key(sib1, 'commonSearchSpaceList') or []:
        if not isinstance(ss, dict) or ss.get('searchSpaceId') != ss_id or not ss_id:
            continue
        cands = ss.get('nrofCandidates') or {}
        counts = []
        for level in (1, 2, 4, 8, 16):
            n = str(cands.get('aggregationLevel%d' % level, 'n0'))
            counts.append((level, int(n[1:]) if n[1:].isdigit() else 0))
        per = ss.get('monitoringSlotPeriodicityAndOffset')
        period, off = 1, 0
        if isinstance(per, list) and len(per) == 2 and str(per[0]).startswith('sl'):
            period, off = int(str(per[0])[2:]), int(per[1] or 0)
        sym = ss.get('monitoringSymbolsWithinSlot')
        first = (0,)
        if isinstance(sym, list) and len(sym) == 2:
            value, width = int(sym[0]), int(sym[1])
            first = tuple(i for i in range(width) if (value >> (width - 1 - i)) & 1) or (0,)
        search = {'counts': tuple(counts), 'first_symbols': first, 'period_slots': period,
                  'offset_slots': off}
    return {'offset_to_point_a': offset, 'tdra': tdra, 'window_slots': window,
            'periods_rf': [e['periodicity_rf'] for e in entries], 'search': search}


def _offset_to_point_a(sib1):
    value = _find_key(sib1, 'offsetToPointA')
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _point_a_hz(ssb_hz, ssb_scs_hz, k_ssb, offset):
    """Point A (FR1) from the SSB centre, its spacing, the MIB k_SSB and offsetToPointA."""
    return ssb_hz - 120 * ssb_scs_hz - k_ssb * 15e3 - offset * 12 * 15e3


def _carrier(sib1, point_a_hz):
    if point_a_hz is None:
        return None
    freq = _find_key(sib1, 'frequencyInfoDL')
    if not isinstance(freq, dict):
        return None
    bands = [b.get('freqBandIndicatorNR') for b in freq.get('frequencyBandList') or []
             if isinstance(b, dict) and b.get('freqBandIndicatorNR') is not None]
    first = next((c for c in freq.get('scs-SpecificCarrierList') or [] if isinstance(c, dict)), {})
    return {'point_a_arfcn': point_a_arfcn(point_a_hz),
            'point_a_mhz': round(point_a_hz / 1e6, 3),
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
    out.pop('local', None)
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

def _common(blocks, sii=None):
    """Distinct hex blocks of one ``sii`` (of every ``sii`` when None) as ``[(count, hex)]``,
    most common first (first seen on ties)."""
    hexes = [h for s, h in blocks if sii is None or s == sii]
    count = collections.Counter(hexes)
    order = list(dict.fromkeys(hexes))
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


def _heard(si_blocks, tools, notes):
    """``(heard, sibs)`` of SI blocks ``[(copies, hex)]``: each SIB's copies, and its first
    content; a parse failure goes to ``notes``."""
    heard, sibs = {}, {}
    if not si_blocks:
        return heard, sibs
    results, why = _parse_blocks([h for _c, h in si_blocks], tools)
    if results is None:
        notes.append(why)
        return heard, sibs
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
    return heard, sibs


#: ``_shared_matches``: a message's summed score against the stronger cell's bytes must reach
#: this, and this many times its largest null (one copy scores about 0 +/- 1 without them).
_MATCH_Z = 8.0
_MATCH_OVER = 2.0


def _shared_matches(blocks):
    """The other SI messages none of whose blocks decoded, but whose copies hold what a stronger
    cell sent in the same slots (``other_si``'s ``shared``): per message, the scores summed over
    its copies (z = sum / sqrt n) against the same sums at each null scrambling. Sectors of one
    site send their SI on the same RBs; a block a few bits off the hypothesis would still score
    (one changed bit changes a tenth to a third of the codeword), so a match says the cell sends
    that message as the stronger one does, near enough, not every bit. ``[{si, pci, copies, z,
    null_z, matched, hex}]`` for every message tested (``hex`` the hypothesis most often tested)."""
    decoded = {b['si'] for b in blocks if (b.get('pdsch') or {}).get('hex')}
    by_si = {}
    for b in blocks:
        sh = (b.get('pdsch') or {}).get('shared')
        if sh and b['si'] not in decoded:
            by_si.setdefault(b['si'], []).append(sh)
    out = []
    for si, rows in sorted(by_si.items()):
        n = len(rows)
        z = sum(r['score'] for r in rows) / n ** 0.5
        null_z = max(abs(sum(col)) for col in zip(*[r['nulls'] for r in rows])) / n ** 0.5
        hexes = [r['hex'] for r in rows]
        out.append({'si': si, 'pci': max(set(r['pci'] for r in rows), key=[r['pci'] for r in rows].count),
                    'copies': n, 'z': round(z, 1), 'null_z': round(null_z, 1),
                    'matched': bool(z >= _MATCH_Z and z >= _MATCH_OVER * null_z),
                    'hex': max(set(hexes), key=hexes.count)})
    return out


def own_decode(iq, fs, centre_hz, cell, tools=None, known=None, progress=None):
    """SIB1 and the other SI of one ``iqnr.ssb_search(..., mib=True)`` entry by our own decoder
    (``iqnr.type0_pdcch`` with ``pdsch``, then ``iqnr.other_si`` configured from that SIB1),
    over the whole of ``iq``. Only the RRC parse is outside: pycrate, as for ``decode_ssb``.

    Returns ``sib1`` (the summary ``decode_ssb`` gives, or None), ``sib1_hex`` (the block most
    often decoded), ``blocks`` (``sib1``: copies decoded and DCIs found; ``other``: the same
    for other SI; ``combined``: how many of those blocks, SIB1's and other SI's, took more than
    one copy summed), ``si`` (``sibs_heard``, ``sibs``, ``scheduled``, ``missing``,
    ``unscheduled``, as ``decode_ssb``; None when SIB1 schedules nothing and none was heard),
    ``time`` (SIB9's), ``time_reason`` and ``reason``.

    ``known`` (``iqnr.known_transmissions``' dict): what stronger cells on the same carrier were
    decoded sending, cancelled first; this cell's blocks are added to it. ``cancelled`` gives
    the slots something was taken out of ([SIB1's, other SI's]) and ``summed`` SIB1's DCI found
    from summed occasions (``iqnr.type0_pdcch``), or None. Where stronger cells' other SI was
    tried as a hypothesis (``iqnr.other_si``'s ``shared``), ``shared`` gives the copies
    ``tested``, those read ``on_their_dci`` ([decoded, read]; not counted in ``blocks.other``)
    and the ``messages`` none of whose blocks decoded (``_shared_matches``); the SIBs of those
    matched go to ``si.sibs_matched`` (by copies) and out of ``si.missing``.

    ``progress(stage, done, total)``, when given, follows the read: stage 'SIB1', then
    'other SI', a call at each frame."""
    from fm_receiver import iqnr
    tools = tools if tools is not None else tools_dir()
    seconds = len(iq) / float(fs) if fs else 0.0

    def follow(stage):
        return None if progress is None else (lambda done, total: progress(stage, done, total))
    out = {'ran': True, 'decoder': 'ours', 'sib1': None, 'sib1_hex': None, 'si': None, 'time': None,
           'time_reason': None, 'reason': None,
           'blocks': {'sib1': [0, 0], 'other': [0, 0], 'combined': [0, 0]}, 'cancelled': [0, 0],
           'summed': None}
    notes = []
    got = iqnr.type0_pdcch(iq, fs, centre_hz, cell, pdsch=True, known=known, progress=follow('SIB1'))
    if got is None:
        out['time_reason'] = 'no SIB1'
        out['reason'] = 'no MIB, or a cell this decoder does not read (15 kHz only)'
        return out
    hexes = [d['pdsch']['hex'] for d in got['dcis'] if d.get('pdsch') and d['pdsch']['hex']]
    out['blocks']['sib1'] = [len(hexes), len(got['dcis'])]
    out['blocks']['combined'][0] = sum(1 for d in got['dcis'] if (d.get('pdsch') or {}).get('copies', 0) > 1)
    out['cancelled'][0] = got.get('cancelled', 0)
    out['summed'] = got.get('summed')
    if known is not None:
        iqnr.known_transmissions(got, cell, known)
    if not hexes:
        out['time_reason'] = 'no SIB1'
        out['reason'] = ('no SIB1 decoded from %d DCIs in %g s' % (len(got['dcis']), round(seconds, 3))
                         if got['dcis'] else 'no SIB1 DCI in %g s' % round(seconds, 3))
        return out
    best = max(set(hexes), key=hexes.count)
    out['sib1_hex'] = best
    results, why = _parse_blocks([best], tools)
    sib1 = None
    if results is None:
        notes.append(why)
    else:
        kind, value = _body(results[0])
        sib1 = value if kind == 'systemInformationBlockType1' and isinstance(value, dict) else None
        if sib1 is None:
            notes.append('the SIB1 block did not parse')
    if sib1 is None:
        out['time_reason'] = 'no SIB1'
        out['reason'] = _join(*notes)
        return out
    mib = cell['mib']
    offset = _offset_to_point_a(sib1)
    point_a = None
    if offset is not None and mib.get('ssb_subcarrier_offset') is not None:
        point_a = _point_a_hz(float(cell['ssb_hz']), 15e3, mib['ssb_subcarrier_offset'], offset)
    out['sib1'] = _summarise_sib1(sib1, mib, point_a)
    config = si_config(sib1)
    other = (iqnr.other_si(iq, fs, centre_hz, cell, config, known=known, progress=follow('other SI'))
             if config else None)
    blocks = (other or {}).get('blocks') or []
    out['cancelled'][1] = (other or {}).get('cancelled', 0)
    if known is not None and other:
        iqnr.known_transmissions(other, cell, known)
    ok = [b['pdsch']['hex'] for b in blocks if b.get('pdsch') and b['pdsch']['hex']]
    own_dcis = [b for b in blocks if 'dci_from' not in b]
    out['blocks']['other'] = [sum(1 for b in own_dcis if (b.get('pdsch') or {}).get('hex')), len(own_dcis)]
    out['blocks']['combined'][1] = sum(1 for b in blocks if (b.get('pdsch') or {}).get('copies', 0) > 1)
    counted = {}
    for h in ok:
        counted[h] = counted.get(h, 0) + 1
    heard, sibs = _heard([(n, h) for h, n in counted.items()], tools, notes)
    tested = _shared_matches(blocks)
    matched = [m for m in tested if m['matched']]
    if any('shared' in (b.get('pdsch') or {}) for b in blocks):
        out['shared'] = {'tested': sum(1 for b in blocks if 'shared' in (b.get('pdsch') or {})),
                         'on_their_dci': [sum(1 for b in blocks if 'dci_from' in b and (b['pdsch'] or {}).get('hex')),
                                          sum(1 for b in blocks if 'dci_from' in b)],
                         'messages': [{k: v for k, v in m.items() if k != 'hex'} for m in tested]}
    sibs_matched = {}
    if matched:
        sibs_matched, _values = _heard([(m['copies'], m['hex']) for m in matched], tools, notes)
        sibs_matched = {n: c for n, c in sibs_matched.items() if n not in heard}
    entries = out['sib1']['si_schedule']
    if entries or heard:
        scheduled = []
        for entry in entries:
            scheduled += [n for n in entry['sibs'] if n not in scheduled]
        on_air = [n for e in entries if str(e.get('broadcast_status')).lower() != 'notbroadcasting'
                  for n in e['sibs']]
        out['si'] = {'sibs_heard': heard, 'sibs': sibs, 'scheduled': scheduled,
                     'missing': [n for n in dict.fromkeys(on_air) if n not in heard and n not in sibs_matched],
                     'unscheduled': [n for n in heard if n not in scheduled]}
        if sibs_matched:
            out['si']['sibs_matched'] = sibs_matched
            notes.append('sent as the stronger cell sends them (its bytes found in this cell\'s soft bits, '
                         'not decoded): %s' % ', '.join(sibs_matched))
        if out['si']['missing']:
            notes.append('not heard in %g s: %s' % (round(seconds, 3), ', '.join(out['si']['missing'])))
            if out['cancelled'][1] or out['cancelled'][0]:
                # Under a stronger sector the SI it shares lands on the same RBs: a SIB not heard
                # here may well be sent, just not decoded.
                out['si']['missing_unsure'] = True
                notes.append('other SI read under a stronger cell (%d of %d DCIs decoded): a missing '
                             'SIB may be sent but too weak here' % tuple(out['blocks']['other']))
        if 'sib9' in sibs:
            out['time'] = _sib9_time(sibs['sib9'])
    if out['time'] is None:
        out['time_reason'] = _time_reason(out['sib1'], out['si'], seconds, True)
    out['reason'] = _join(*notes)
    return out


def own_decode_cells(iq, fs, centre_hz, cells, tools=None, progress=None):
    """``own_decode`` for each of ``cells`` (``iqnr.ssb_search`` entries, strongest first; one
    without a MIB gets None), weaker cells after stronger ones on the same SSB frequency.

    Sectors of one site share a carrier and send their SIB1 and SI in the same slots and RBs, so
    each cell's decoded transmissions are cancelled from the slots of the weaker cells on its SSB
    frequency (``known``). Such a cell is read at the strongest one's frequency offset: the
    offset is mostly the recorder's, and the strongest cell's SSB measures it best. Each result
    gains ``cfo_hz`` (the offset used) and ``cfo_from`` ('own SSB', or 'strongest on this SSB
    frequency: PCI n').

    ``progress(i, stage, done, total, own=None)``, when given, follows cell ``i`` (its place in
    ``cells``) through ``own_decode``'s stages, then once with stage 'done' and its result."""
    groups = []                                    # [ssb_hz, strongest cell, known]
    out = []
    for cell in cells:
        if not isinstance(cell, dict) or not cell.get('mib'):
            out.append(None)
            continue
        group = next((g for g in groups if abs(g[0] - float(cell['ssb_hz'])) < 1e3), None)
        if group is None:
            group = [float(cell['ssb_hz']), cell, {}]
            groups.append(group)
            used, source = cell, 'own SSB'
        else:
            used = dict(cell, cfo_hz=group[1].get('cfo_hz'))
            source = 'strongest on this SSB frequency: PCI %s' % group[1]['pci']
        i = len(out)
        step = None if progress is None else (lambda stage, done, total: progress(i, stage, done, total))
        own = own_decode(iq, fs, centre_hz, used, tools=tools, known=group[2], progress=step)
        own['cfo_hz'] = used.get('cfo_hz')
        own['cfo_from'] = source
        out.append(own)
        if progress is not None:
            progress(i, 'done', 1, 1, own=own)
    return out


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
        mib_pci, want_pci = first.mib.get('pci'), ssb.get('pci')
        if mib_pci is not None and want_pci is not None and int(mib_pci) != int(want_pci):
            # Another, stronger cell on this SSB frequency: aim at this one's PCI.
            base += ['-P', int(want_pci)]
            aimed = _Pass(*_run(script, base, timeout))
            if aimed.mib is None or aimed.mib.get('pci') is None or int(aimed.mib['pci']) != int(want_pci):
                passes = [aimed]               # the stronger cell's blocks are not this one's
                out['decoded_pci'] = int(mib_pci)
                out['time_reason'] = 'another cell was decoded'
                why = aimed.error or 'no MIB (%s)' % ((aimed.no_mib or {}).get('reason') or 'none read')
                return finish('the decoder read PCI %d, a stronger cell on this SSB frequency, '
                              'not PCI %d; aimed at PCI %d: %s'
                              % (int(mib_pci), int(want_pci), int(want_pci), why))
            notes.append('PCI %d, a stronger cell, shares this SSB frequency: decoded by aiming at PCI %d'
                         % (int(mib_pci), int(want_pci)))
            first = aimed
            passes = [first]
        out['mib'] = {k: first.mib.get(k) for k in _MIB_KEYS}
        out['mib'].update({k: v for k, v in first.mib.items() if k not in out['mib']})
        if out['mib'].get('targeted'):
            # The program's level and offset for an aimed cell are its own search's, made
            # under the stronger cell (on air -21 dB and -552 Hz where iq-nr measured 7 dB
            # under the strongest and +252 Hz): the SSB entry's own values stand.
            out['mib']['snr_db'] = out['mib']['cfo_hz'] = None
            notes.append("the level and offset of an aimed cell are iq-nr's, in the SSB entry")
        done_reason = (first.done or {}).get('reason')

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
        offset = _offset_to_point_a(sib1)
        k_ssb = out['mib'].get('ssb_subcarrier_offset')
        point_a = None
        if offset is not None and k_ssb is not None:
            point_a = _point_a_hz(ssb_hz, scs * 1000.0, k_ssb, offset)
        out['sib1'] = _summarise_sib1(sib1, out['mib'], point_a)

        _window, entries = _schedule(sib1)
        searched = False
        if not entries and first.osi_failed and offset is not None:
            notes.append('pass 1 saw %d other SI block%s that SIB1 does not schedule: searched with '
                         'offsetToPointA' % (first.osi_failed, '' if first.osi_failed == 1 else 's'))
        if (entries or first.osi_failed) and offset is not None:
            args = list(base)
            slivs = _tdra(sib1)
            if slivs:
                args += ['-t', ','.join(str(v) for v in slivs)]
            args += ['-a', offset]
            second = _Pass(*_run(script, args, timeout))
            passes.append(second)
            searched = True
            notes.append(second.error)

        # Every block, both indicators: other SI may come in SIB1's slots (step 5).
        heard, sibs = _heard(_common([b for p in passes for b in p.blocks]), tools, notes)

        if entries or heard:
            scheduled = []
            for entry in out['sib1']['si_schedule']:
                scheduled += [n for n in entry['sibs'] if n not in scheduled]
            on_air = [n for e in out['sib1']['si_schedule']
                      if str(e.get('broadcast_status')).lower() != 'notbroadcasting' for n in e['sibs']]
            out['si'] = {'sibs_heard': heard, 'sibs': sibs, 'scheduled': scheduled,
                         'missing': [n for n in dict.fromkeys(on_air) if n not in heard],
                         'unscheduled': [n for n in heard if n not in scheduled]}
            if out['si']['unscheduled']:
                notes.append('heard SIBs that SIB1 does not schedule: %s'
                             % ', '.join(out['si']['unscheduled']))
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
