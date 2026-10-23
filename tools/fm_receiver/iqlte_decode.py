"""Hand a centred LTE block to an outside pdsch_ue and read the broadcast SI.

``tools/iq-lte --decode`` is the only caller. The outside program is
``$FMRX_LTE_TOOLS/run_pdsch.sh`` (srsRAN 4G, not part of this repo). It reads
a cf32 file at the 10 MHz class rate, 15.36 MS/s, already aligned to a frame
start. This module writes that file in a private temporary directory, tries
the two half-frame alignments and the antenna-port counts, and stops at the
first run whose stdout contains ``Decoded MIB``.

Only the MIB text and the SI-RNTI transport blocks in ``$PDSCH_DUMP`` are
read. The dump records are ``0xA5, subframe, nbytes_lo, nbytes_hi, bytes``.
The SIB1 is the most common subframe-5 payload of 32 bytes or more, decoded
with ``$FMRX_LTE_TOOLS/pyc/bin/python`` (pycrate). That interpreter is a
subprocess: this module does not import pycrate.

Every other distinct block is parsed too (``read_si``): SIB1's schedule, each
SIB heard (SIB2 to SIB33, as plain JSON), and summaries of the ones that say
most (SIB5's LTE carriers, SIB16's network time, SIB24's NR carriers). The
outside ``pdsch_ue`` searches every subframe only with its local
``PDSCH_ALL_SF`` change; a stock build hears SIB1 alone, and ``si`` says so.
"""

import collections
import datetime
import fractions
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time

import numpy as np

from fm_receiver import iqlte

# 10 MHz class: 1024-point FFT, 15.36 MS/s, 153600 samples in a 10 ms frame.
RATE_10 = 15.36e6
FRAME_10 = 153600
HALF_10 = 76800
NFFT_10 = 1024
_SUBFRAME = 15360
NFFT_15 = 1536
# FFT size -> (sample rate, samples per frame, per half-frame, per subframe, PRBs)
_GEOMETRY = {
    NFFT_10: (RATE_10, FRAME_10, HALF_10, _SUBFRAME, 50),
    NFFT_15: (23.04e6, 230400, 115200, 23040, 75),
}
SUPPORTED_NFFT = tuple(_GEOMETRY)
_SIB1_MIN = 32
_DUMP_MARK = 0xA5

# Runs under the outside interpreter. pycrate stays in that process.
_SIB1_HELPER = r'''
import json
import sys

def _digits(seq):
    return "".join(str(int(d)) for d in seq)

def _bitint(value):
    """A pycrate BIT STRING is (int, nbits): the value first, then its length. A plain int is itself."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
        return int(value[0])
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, "big")
    raise TypeError("not an integer field: %r" % (value,))

def _name(value):
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return value

def _lookup(mod, name):
    """The ASN.1 object itself, not an instance. pycrate uses the object in place."""
    for path in (("EUTRA_RRC_Definitions", name), (name,)):
        obj = mod
        for part in path:
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None:
            return obj
    return None

def _decode(obj, data):
    """from_uper into ``obj``. Trailing 0x00 pad is dropped until it decodes.

    The object is the pycrate singleton: calling it (or get_val) returns the
    value. A shorter buffer is tried only while the tail is a zero pad byte.
    """
    last = None
    blob = data
    seen = []
    while blob and blob not in seen:
        seen.append(blob)
        try:
            obj.from_uper(blob)
            val = obj()
            if val is None and hasattr(obj, "get_val"):
                val = obj.get_val()
            if val is not None:
                return val
        except Exception as exc:
            last = exc
        if not blob.endswith(b"\x00"):
            break
        blob = blob[:-1]
    if last is not None:
        raise last
    raise RuntimeError("empty SIB1")

def _is_sib1(val):
    return (isinstance(val, dict) and "cellAccessRelatedInfo" in val
            and "freqBandIndicator" in val)

def _find_sib1(val, depth=0):
    """SIB1 dict inside a BCCH-DL-SCH-Message, or the bare SIB1 value."""
    if depth > 8 or val is None:
        return None
    if _is_sib1(val):
        return val
    if isinstance(val, (list, tuple)):
        if (len(val) == 2 and isinstance(val[0], str)
                and val[0].lower() == "systeminformationblocktype1"):
            found = _find_sib1(val[1], depth + 1)
            if found is not None:
                return found
        for item in val:
            found = _find_sib1(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(val, dict):
        for key, item in val.items():
            if isinstance(key, str) and key.lower() == "systeminformationblocktype1":
                found = _find_sib1(item, depth + 1)
                if found is not None:
                    return found
        for item in val.values():
            found = _find_sib1(item, depth + 1)
            if found is not None:
                return found
    return None

def _try(mod, name, data):
    obj = _lookup(mod, name)
    if obj is None:
        return None
    try:
        return _decode(obj, data)
    except Exception:
        return None

def main():
    try:
        from pycrate_asn1dir import RRCLTE
    except ImportError as exc:
        sys.stderr.write("pycrate is not available: %s\n" % exc)
        return 1
    data = open(sys.argv[1], "rb").read()
    # On the air the block is a BCCH-DL-SCH-Message whose c1 choice is
    # systemInformationBlockType1. The test encoder writes a bare SIB1, so
    # that type is the fallback when the wrapper is absent or does not decode.
    val = _try(RRCLTE, "BCCH_DL_SCH_Message", data)
    sib = _find_sib1(val)
    if sib is None:
        val = _try(RRCLTE, "SystemInformationBlockType1", data)
        sib = _find_sib1(val)
    if sib is None:
        sys.stderr.write("SIB1 parse failed: no systemInformationBlockType1\n")
        return 1
    access = sib["cellAccessRelatedInfo"]
    plmns = []
    for item in access["plmn-IdentityList"]:
        ident = item["plmn-Identity"]
        mcc = ident.get("mcc") if isinstance(ident, dict) else None
        mnc = ident["mnc"]
        plmns.append({
            "mcc": _digits(mcc) if mcc is not None else "",
            "mnc": _digits(mnc),
        })
    cell_identity = _bitint(access["cellIdentity"])
    out = {
        "plmns": plmns,
        "tac": _bitint(access["trackingAreaCode"]),
        "cell_identity": cell_identity,
        "enb_id": cell_identity >> 8,
        "cell_id": cell_identity & 0xFF,
        "freq_band": int(sib["freqBandIndicator"]),
        "cell_barred": _name(access["cellBarred"]),
        "q_rx_lev_min": int(sib["cellSelectionInfo"]["q-RxLevMin"]),
    }
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("SIB1 parse failed: %s\n" % exc)
        sys.exit(1)
'''

# Runs under the outside interpreter: every block in the JSON list of hex strings
# argv[1] as a BCCH-DL-SCH-Message, printed as plain JSON (a tuple, which is a CHOICE
# or a BIT STRING, becomes a list; bytes become hex).
_SI_HELPER = r'''
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
        from pycrate_asn1dir import RRCLTE
    except ImportError as exc:
        sys.stderr.write("pycrate is not available: %s\n" % exc)
        return 1
    msg = RRCLTE.EUTRA_RRC_Definitions.BCCH_DL_SCH_Message
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

# Downlink EARFCN to MHz (TS 36.101 table 5.7.3-1): band -> (F_DL_low MHz, N_Offs-DL,
# last EARFCN). The bands used in the US and a few common elsewhere; others give None.
_EARFCN_DL = (
    (1, 2110.0, 0, 599), (2, 1930.0, 600, 1199), (3, 1805.0, 1200, 1949),
    (4, 2110.0, 1950, 2399), (5, 869.0, 2400, 2649), (7, 2620.0, 2750, 3449),
    (8, 925.0, 3450, 3799), (12, 729.0, 5010, 5179), (13, 746.0, 5180, 5279),
    (14, 758.0, 5280, 5379), (17, 734.0, 5730, 5849), (20, 791.0, 6150, 6449),
    (25, 1930.0, 8040, 8689), (26, 859.0, 8690, 9039), (29, 717.0, 9660, 9769),
    (30, 2350.0, 9770, 9869), (38, 2570.0, 37750, 38249), (40, 2300.0, 38650, 39649),
    (41, 2496.0, 39650, 41589), (46, 5150.0, 46790, 54539), (48, 3550.0, 55240, 56739),
    (66, 2110.0, 66436, 67335), (71, 617.0, 68586, 68935),
)
_MAX_EARFCN = 65535          # SIB5's "see the v9e0 list" value
_NTP_1900 = datetime.datetime(1900, 1, 1, tzinfo=datetime.timezone.utc)


def earfcn_dl(earfcn):
    """``(band, MHz)`` of a downlink EARFCN, or ``(None, None)`` outside the table."""
    n = int(earfcn)
    for band, low, offs, last in _EARFCN_DL:
        if offs <= n <= last:
            return band, round(low + 0.1 * (n - offs), 1)
    return None, None


def nr_arfcn_mhz(arfcn):
    """MHz of an NR-ARFCN (TS 38.104 5.4.2.1, the global raster)."""
    n = int(arfcn)
    if n < 600000:
        return round(0.005 * n, 3)
    if n < 2016667:
        return round(3000.0 + 0.015 * (n - 600000), 3)
    return round(24250.08 + 0.06 * (n - 2016667), 3)


def _iso(when):
    return when.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def sib16_time(sib16):
    """The network time in a SIB16 value, or None when it carries no ``timeInfo-r11``.

    ``timeInfoUTC-r11`` counts 10 ms units from 00:00:00 UTC on 1 January 1900, at the
    frame boundary that ends the SI window that carried it; ``leapSeconds-r11`` is GPS
    minus UTC; ``localTimeOffset-r11`` is in 15-minute steps (local = UTC + offset);
    ``dayLightSavingTime-r11`` is two bits, the hours already in that offset (0, 1, 2).
    """
    info = sib16.get('timeInfo-r11') if isinstance(sib16, dict) else None
    if not isinstance(info, dict) or 'timeInfoUTC-r11' not in info:
        return None
    units = int(info['timeInfoUTC-r11'])
    utc = _NTP_1900 + datetime.timedelta(milliseconds=10 * units)
    out = {'utc': _iso(utc), 'utc_10ms': units}
    if info.get('leapSeconds-r11') is not None:
        leap = int(info['leapSeconds-r11'])
        out['leap_seconds'] = leap
        out['gps'] = _iso(utc + datetime.timedelta(seconds=leap))
    if info.get('localTimeOffset-r11') is not None:
        minutes = 15 * int(info['localTimeOffset-r11'])
        out['local_offset_minutes'] = minutes
        zone = datetime.timezone(datetime.timedelta(minutes=minutes))
        out['local'] = utc.astimezone(zone).isoformat(timespec='milliseconds')
    dst = info.get('dayLightSavingTime-r11')
    if dst is not None:
        out['dst_hours'] = int(dst[0] if isinstance(dst, list) else dst)
    return out


def _sib_name(name):
    """``sibType24-v1530`` or ``sib24-v1530`` as ``sib24``; anything else as it is."""
    match = re.match(r'sib(?:Type)?(\d+a?)', str(name))
    return 'sib' + match.group(1) if match else str(name)


def _find_key(val, key, depth=0):
    """The first value under ``key`` anywhere in ``val`` (a SIB1's extension chain)."""
    if depth > 30:
        return None
    if isinstance(val, dict):
        if key in val:
            return val[key]
        items = val.values()
    elif isinstance(val, list):
        items = val
    else:
        return None
    for item in items:
        found = _find_key(item, key, depth + 1)
        if found is not None:
            return found
    return None


def _frames(enum, prefix):
    """``rf16`` -> 16, ``ms10`` -> 10; None when it is not that form."""
    match = re.match(prefix + r'(\d+)$', str(enum))
    return int(match.group(1)) if match else None


def si_schedule(sib1):
    """SIB1's SI messages in order: ``si`` (1-based), ``period_frames``, ``sibs``, and
    where its window opens (``sfn_mod_period``, ``subframe``; TS 36.331 5.2.3, FDD).

    The first message carries SIB2 as well. Messages in ``schedulingInfoListExt-r12``
    follow those of ``schedulingInfoList``. Returns ``(window_ms, messages)``.
    """
    window = _frames(sib1.get('si-WindowLength'), 'ms')
    entries = []
    for item in sib1.get('schedulingInfoList') or []:
        entries.append((item.get('si-Periodicity'), item.get('sib-MappingInfo') or []))
    for item in _find_key(sib1, 'schedulingInfoListExt-r12') or []:
        entries.append((item.get('si-Periodicity-r12'), item.get('sib-MappingInfo-r12') or []))
    out = []
    for n, (period, mapping) in enumerate(entries, start=1):
        sibs = ['sib2'] if n == 1 else []
        sibs += [_sib_name(m) for m in mapping]
        msg = {'si': n, 'period_frames': _frames(period, 'rf'), 'sibs': sibs}
        if window:
            x = (n - 1) * window
            msg['sfn_mod_period'] = x // 10
            msg['subframe'] = x % 10
        out.append(msg)
    return window, out


def _sib5_carriers(sib5):
    carriers = sib5.get('interFreqCarrierFreqList') or []
    ext = _find_key(sib5, 'interFreqCarrierFreqList-v9e0') or []
    out = []
    for i, item in enumerate(carriers):
        earfcn = item.get('dl-CarrierFreq')
        if earfcn == _MAX_EARFCN and i < len(ext) and isinstance(ext[i], dict):
            earfcn = ext[i].get('dl-CarrierFreq-v9e0', earfcn)
        band, mhz = earfcn_dl(earfcn) if earfcn is not None else (None, None)
        out.append({'earfcn': earfcn, 'band': band, 'mhz': mhz,
                    'priority': item.get('cellReselectionPriority')})
    return out


def _sib24_carriers(sib24):
    out = []
    for item in sib24.get('carrierFreqListNR-r15') or []:
        arfcn = item.get('carrierFreq-r15')
        scs = _frames(item.get('subcarrierSpacingSSB-r15'), 'kHz')
        out.append({'nr_arfcn': arfcn, 'mhz': nr_arfcn_mhz(arfcn) if arfcn is not None else None,
                    'ssb_khz': scs, 'priority': item.get('cellReselectionPriority-r15')})
    return out


def _summary(name, value):
    """A short reading of the SIBs that say most; None for the rest."""
    if not isinstance(value, dict):
        return None
    if name == 'sib2':
        return {'ul_earfcn': _find_key(value.get('freqInfo') or {}, 'ul-CarrierFreq')}
    if name == 'sib3':
        return {'priority': _find_key(value, 'cellReselectionPriority')}
    if name == 'sib4':
        return {'neighbour_pcis': [c.get('physCellId') for c in value.get('intraFreqNeighCellList') or []]}
    if name == 'sib5':
        return {'carriers': _sib5_carriers(value)}
    if name == 'sib16':
        return sib16_time(value)
    if name == 'sib24':
        return {'nr_carriers': _sib24_carriers(value)}
    return None


def summarise_si(parsed, capture_frames):
    """The ``si`` object from parsed blocks.

    ``parsed`` is ``(count, subframe, helper result)`` per distinct block, the helper
    result being ``{"ok": true, "message": [...]}`` or ``{"ok": false, "error": ...}``.
    ``capture_frames`` is how many 10 ms frames were decoded: a scheduled message is
    ``covered`` when that is at least its period plus one window.
    """
    sib1, sib1_count = None, -1
    heard = {}
    failed = 0
    for count, _sub, res in parsed:
        if not res.get('ok'):
            failed += 1
            continue
        msg = res.get('message')
        body = msg[1] if isinstance(msg, list) and len(msg) == 2 else None
        if not (isinstance(body, list) and len(body) == 2):
            failed += 1
            continue
        kind, value = body
        if kind == 'systemInformationBlockType1':
            if count > sib1_count:
                sib1, sib1_count = value, count
            continue
        ext = value.get('criticalExtensions') if isinstance(value, dict) else None
        ies = ext[1] if isinstance(ext, list) and len(ext) == 2 else {}
        for item in (ies.get('sib-TypeAndInfo') or []) if isinstance(ies, dict) else []:
            if isinstance(item, list) and len(item) == 2:
                name = _sib_name(item[0])
                entry = heard.setdefault(name, {'count': 0, 'value': item[1]})
                entry['count'] += count
    out = {'blocks': len(parsed), 'blocks_failed': failed,
           'capture_frames': int(capture_frames)}
    if sib1 is None:
        out.update({'schedule': None, 'sibs': {}, 'time': None,
                    'time_reason': 'no SIB1 among the blocks'})
        return out
    window, schedule = si_schedule(sib1)
    window_frames = int(math.ceil((window or 10) / 10.0))
    scheduled = set()
    for msg in schedule:
        period = msg['period_frames']
        msg['covered'] = bool(period) and capture_frames >= period + window_frames
        msg['heard'] = [n for n in msg['sibs'] if n in heard]
        scheduled.update(msg['sibs'])
    out['window_ms'] = window
    out['value_tag'] = sib1.get('systemInfoValueTag')
    out['schedule'] = schedule
    sibs = {}
    for name in sorted(heard, key=lambda n: (len(n), n)):
        entry = {'count': heard[name]['count'], 'scheduled': name in scheduled}
        summ = _summary(name, heard[name]['value'])
        if summ is not None:
            entry['summary'] = summ
        entry['value'] = heard[name]['value']
        sibs[name] = entry
    out['sibs'] = sibs
    if not heard:
        out['note'] = ('no SI message besides SIB1 was decoded: the outside pdsch_ue searches '
                       'other subframes only with its PDSCH_ALL_SF change (lte-verify.md)')
    alerts = [n for n in ('sib10', 'sib11', 'sib12') if n in heard]
    if alerts:
        out['alerts'] = alerts
    if 'sib16' in heard and sibs['sib16'].get('summary'):
        out['time'] = sibs['sib16']['summary']
    else:
        out['time'] = None
        if 'sib16' in heard:
            out['time_reason'] = 'SIB16 was heard but carries no timeInfo'
        elif 'sib16' in scheduled:
            msg = next(m for m in schedule if 'sib16' in m['sibs'])
            out['time_reason'] = 'SIB16 is scheduled but was not heard' + (
                '' if msg['covered'] else
                ' (the capture is shorter than its period, %s frames, plus a window)' % msg['period_frames'])
        else:
            out['time_reason'] = 'SIB1 does not schedule SIB16: this cell broadcasts no network time'
    return out


def distinct_blocks(blob):
    """``(count, subframe, payload)`` per distinct payload in a dump, first-seen order."""
    seen = {}
    order = []
    for sub, payload in dump_payloads(blob):
        if payload not in seen:
            seen[payload] = [0, sub]
            order.append(payload)
        seen[payload][0] += 1
    return [(seen[p][0], seen[p][1], p) for p in order]


def read_si(blob, tools, capture_frames):
    """``(si, None)`` or ``(None, reason)``: every block of a dump, via pycrate."""
    blocks = distinct_blocks(blob)
    if not blocks:
        return None, 'no transport block in the dump'
    python = os.path.join(tools, 'pyc', 'bin', 'python') if tools else ''
    if not python or not os.path.isfile(python):
        return None, 'pycrate interpreter is missing (%s)' % (python or 'pyc/bin/python')
    tmp = tempfile.mkdtemp(prefix='iqlte-si-')
    try:
        script = os.path.join(tmp, 'si_parse.py')
        listing = os.path.join(tmp, 'blocks.json')
        with open(script, 'w') as fh:
            fh.write(_SI_HELPER)
        with open(listing, 'w') as fh:
            json.dump([p.hex() for _c, _s, p in blocks], fh)
        try:
            proc = subprocess.run([python, script, listing], capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None, 'SI parse failed: timed out'
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
            return None, 'SI parse failed: the helper did not print JSON'
        if not isinstance(results, list) or len(results) != len(blocks):
            return None, 'SI parse failed: the helper gave %s results for %d blocks' % (
                len(results) if isinstance(results, list) else 'no', len(blocks))
        parsed = [(c, s, r if isinstance(r, dict) else {'ok': False})
                  for (c, s, _p), r in zip(blocks, results)]
        return summarise_si(parsed, capture_frames), None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class ToolsMissing(Exception):
    """``FMRX_LTE_TOOLS`` or a program under it is not there. ``name`` is the missing piece."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


def tools_dir():
    """``$FMRX_LTE_TOOLS``, or ``/data/python/lte-tools`` when it is unset."""
    return os.environ.get('FMRX_LTE_TOOLS') or '/data/python/lte-tools'


def require_program(tools, name):
    """The path of ``name`` under ``tools``, or :class:`ToolsMissing`."""
    if not tools or not os.path.isdir(tools):
        raise ToolsMissing(tools or 'FMRX_LTE_TOOLS')
    path = os.path.join(tools, name)
    if not os.path.isfile(path):
        raise ToolsMissing(name)
    return path


def to_10mhz(iq, fs_in, nfft=NFFT_10):
    """``iq`` at ``fs_in`` as complex64 at the carrier's decoder rate (15.36 MS/s for 10 MHz,
    23.04 for 15 MHz). Already-there rates are a view cast."""
    fs_in = float(fs_in)
    rate = _GEOMETRY[nfft][0]
    if abs(fs_in - rate) <= 1.0:
        out = np.ascontiguousarray(iq, dtype=np.complex64)
    else:
        out = np.ascontiguousarray(iqlte._resample(iq, fs_in, rate), dtype=np.complex64)
    bad = ~np.isfinite(out.real) | ~np.isfinite(out.imag)
    if bad.any():
        out = out.copy()
        out[bad] = 0
    return out


# A long span (seconds at 20 MS/s or more) given without holding it whole: ``n``
# samples at ``fs`` Hz, ``read(i0, i1)`` returning samples i0..i1 as complex64,
# already mixed so the carrier sits at 0 Hz.
SampleSource = collections.namedtuple('SampleSource', 'n fs read')
# Resampling a SampleSource: pieces of about this many input samples, with this
# many more on each side, cut off after the resample (the FFT's wrap-around).
_PIECE = 1 << 21
_GUARD = 1 << 13


def _write_resampled(src, rate, fh):
    """``src`` (a :data:`SampleSource`) resampled to ``rate`` and written to ``fh`` in
    pieces. Returns ``(samples written, mean power)``.

    Pieces start on input samples that map onto whole output samples (the rate
    ratio as a fraction p/q: every p inputs give q outputs), so the joins line
    up exactly; each piece is resampled with a guard on both sides that is then
    dropped.
    """
    ratio = fractions.Fraction(float(src.fs) / float(rate)).limit_denominator(100000)
    p, q = ratio.numerator, ratio.denominator
    step = max(p, (_PIECE // p) * p)
    guard = max(p, (_GUARD // p) * p)
    n_out = int(round(src.n * float(rate) / float(src.fs)))
    written = 0
    power = 0.0
    for a in range(0, src.n, step):
        lo = max(0, a - guard)
        hi = min(src.n, a + step + guard)
        piece = iqlte._resample(src.read(lo, hi), float(src.fs), float(rate))
        skip = (a - lo) * q // p
        want = min(step * q // p, n_out - written)
        out = np.ascontiguousarray(piece[skip:skip + want], dtype=np.complex64)
        bad = ~np.isfinite(out.real) | ~np.isfinite(out.imag)
        if bad.any():
            out[bad] = 0
        out.tofile(fh)
        written += out.size
        power += float(np.sum(np.abs(out) ** 2))
        if written >= n_out:
            break
    return written, (power / written if written else 0.0)


def decoder_offsets(frame_offset, fs_in, nfft=NFFT_10):
    """``(o, (o - half-frame) mod frame)`` at the decoder rate (15.36 MS/s for 10 MHz).

    ``frame_offset`` is the SSS frame start in samples of the capture rate
    (modulo 10 ms). The half-frame label does not say which of the two is
    subframe 0, so both are tried.
    """
    rate, frame, half = _GEOMETRY[nfft][:3]
    if frame_offset is None or not fs_in:
        base = 0
    else:
        base = int(round(float(frame_offset) * rate / float(fs_in))) % frame
    return base, (base - half) % frame


def parse_mib(text):
    """The MIB fields printed by pdsch_ue, or None when the MIB was not decoded.

    ``cp`` is None when the text has no CP line. Every key is present.
    """
    if 'Decoded MIB' not in text:
        return None

    def grab(pattern):
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def num(pattern):
        raw = grab(pattern)
        return None if raw is None else int(raw)

    return {
        'pci': num(r'PCI:\s*(\d+)'),
        'ports': num(r'Nof ports:\s*(\d+)'),
        'prb': num(r'PRB:\s*(\d+)'),
        'type': grab(r'Type:\s*(\S+)'),
        'cp': grab(r'\bCP:\s*(\S+)'),
        'sfn': num(r'SFN:\s*(\d+)'),
    }


def dump_payloads(blob):
    """``(subframe, payload)`` records in a PDSCH dump. A bad marker is skipped."""
    data = bytes(blob)
    out = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        if data[i] != _DUMP_MARK:
            i += 1
            continue
        sub = data[i + 1]
        nbytes = data[i + 2] | (data[i + 3] << 8)
        end = i + 4 + nbytes
        if end > n:
            break
        out.append((sub, data[i + 4:end]))
        i = end
    return out


def common_sib1(blob):
    """The most common subframe-5 payload of at least 32 bytes, or None."""
    counts = {}
    order = []
    for sub, payload in dump_payloads(blob):
        if sub != 5 or len(payload) < _SIB1_MIN:
            continue
        if payload not in counts:
            counts[payload] = 0
            order.append(payload)
        counts[payload] += 1
    if not order:
        return None
    best = order[0]
    for payload in order[1:]:
        if counts[payload] > counts[best]:
            best = payload
    return best


def parse_sib1(payload, tools):
    """SIB1 fields from ``payload``, via the outside pycrate interpreter.

    Returns ``(fields, None)`` or ``(None, reason)``. A missing interpreter, a
    failed spawn, a timeout, or a missing pycrate is a reason (the text names
    pycrate or the interpreter). The MIB stays valid and the process still
    exits 0. Exit 2 is only a missing tools directory or ``run_pdsch.sh``.
    """
    python = os.path.join(tools, 'pyc', 'bin', 'python') if tools else ''
    if not python or not os.path.isfile(python):
        return None, 'pycrate interpreter is missing (%s)' % (python or 'pyc/bin/python')
    tmp = tempfile.mkdtemp(prefix='iqlte-sib1-')
    try:
        script = os.path.join(tmp, 'sib1_parse.py')
        blob = os.path.join(tmp, 'sib1.bin')
        with open(script, 'w') as fh:
            fh.write(_SIB1_HELPER)
        with open(blob, 'wb') as fh:
            fh.write(payload)
        try:
            proc = subprocess.run(
                [python, script, blob],
                capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None, 'SIB1 parse failed: timed out'
        except OSError as exc:
            return None, 'pycrate interpreter failed to start (%s)' % exc
        err = proc.stderr.decode('utf-8', errors='replace').strip()
        if proc.returncode != 0:
            if 'pycrate' in err.lower() or 'ModuleNotFoundError' in err:
                tail = err.splitlines()[-1] if err else 'import failed'
                return None, 'pycrate is not available (%s)' % tail
            tail = err.splitlines()[-1] if err else 'exit %d' % proc.returncode
            return None, 'SIB1 parse failed: %s' % tail
        try:
            fields = json.loads(proc.stdout.decode('utf-8'))
        except ValueError:
            return None, 'SIB1 parse failed: the helper did not print JSON'
        if not isinstance(fields, dict) or 'cell_identity' not in fields:
            return None, 'SIB1 parse failed: the helper JSON has no cell_identity'
        cid = int(fields['cell_identity'])
        fields['cell_identity'] = cid
        fields['enb_id'] = cid >> 8
        fields['cell_id'] = cid & 0xFF
        return fields, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_once(script, cf32, pci, offset, nsf, ports, dump, timeout, nfft=NFFT_10):
    env = dict(os.environ)
    env['PDSCH_DUMP'] = dump
    env['PRB'] = str(_GEOMETRY[nfft][4])
    env['VERBOSE'] = '1'          # the wrapper's default filter drops the MIB block's PCI, ports and PRB lines
    env['PDSCH_ALL_SF'] = '1'     # the outside build's change: SI-RNTI in every subframe, not only SIB1's
    env['PDSCH_TIMEOUT'] = str(int(math.ceil(timeout)) + 5)   # the wrapper's own cap, past ours
    try:
        proc = subprocess.run(
            [script, cf32, str(int(pci)), str(int(offset)), str(int(nsf)), str(int(ports))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'pdsch_ue timed out', False
    except OSError as exc:
        raise ToolsMissing('run_pdsch.sh') from exc
    text = proc.stdout.decode('utf-8', errors='replace')
    return text, 'Decoded MIB' in text


def _call_timeout(timeout, nsf):
    """One call's cap: ``timeout``, or 50 ms per subframe when that is longer (every
    subframe is searched, so a 6 s capture takes minutes)."""
    return max(float(timeout), 0.05 * nsf)


def _empty_block():
    return {
        'ran': False,
        'reason': 'the 15.36 MS/s block is empty',
        'mib': None,
        'offset_samples': None,
        'sib1': None,
        'si': None,
        'tried': [],
    }


def _budget_used():
    return {'ran': False, 'reason': 'decode time budget used'}


def _search_offsets(script, cf32, pci, offsets, nsf, ports, tmp, timeout, deadline=None, nfft=NFFT_10):
    """Try ``ports`` at each frame offset. The first decoded MIB stops the search.

    ``deadline`` is a ``time.monotonic`` instant. Each ``pdsch_ue`` call is
    capped at the time still left before it, and at ``timeout``. A call is
    not started once that instant has passed; attempts already made stay in
    ``tried``.
    """
    tried = []
    success = None
    success_dump = None
    for offset in offsets:
        for count in ports:
            call_timeout = float(timeout)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return success, success_dump, tried
                call_timeout = min(call_timeout, remaining)
            dump = os.path.join(tmp, 'dump-%s-%d-%d.bin' % (int(pci), int(offset), int(count)))
            text, ok = _run_once(script, cf32, pci, offset, nsf, count, dump, call_timeout, nfft)
            tried.append({'offset': int(offset), 'ports': int(count), 'decoded': bool(ok)})
            if ok:
                success = parse_mib(text)
                success_dump = dump
                break
        if success is not None:
            break
    return success, success_dump, tried


def _decode_result(success, success_dump, tried, tools, nsf=0):
    """The ``decode`` object once the port search has finished. ``nsf`` is the
    subframes decoded, for ``si``'s coverage."""
    if success is None:
        return {
            'ran': True,
            'reason': 'MIB not decoded',
            'mib': None,
            'offset_samples': None,
            'sib1': None,
            'sib1_reason': 'MIB not decoded',
            'si': None,
            'si_reason': 'MIB not decoded',
            'tried': tried,
        }
    blob = b''
    if success_dump and os.path.isfile(success_dump):
        with open(success_dump, 'rb') as fh:
            blob = fh.read()
    payload = common_sib1(blob)
    sib1 = None
    sib1_reason = None
    if payload is None:
        sib1_reason = 'no subframe-5 transport block of 32 bytes or more'
    else:
        sib1, sib1_reason = parse_sib1(payload, tools)
    out = {
        'ran': True,
        'mib': success,
        'offset_samples': tried[-1]['offset'],
        'sib1': sib1,
        'tried': tried,
    }
    if sib1 is None:
        out['sib1_reason'] = sib1_reason
    si, si_reason = read_si(blob, tools, nsf // 10)
    out['si'] = si
    if si is None:
        out['si_reason'] = si_reason
    return out


def _prepare_block(iq, fs, nfft, path):
    """Write the block at the decoder rate (15.36 MS/s for 10 MHz) to ``path``.

    ``iq`` is an array, or a :data:`SampleSource` for a long span, which is
    resampled and written in pieces so it is never held whole. Returns
    ``(tools, script, samples)``; ``samples`` is 0 when the block is empty or
    silent, which the caller turns into the empty-block decode object. Raises
    :class:`ToolsMissing` when the tools directory or ``run_pdsch.sh`` is absent
    (before anything is written).
    """
    tools = tools_dir()
    script = require_program(tools, 'run_pdsch.sh')
    rate = _GEOMETRY[nfft][0]
    with open(path, 'wb') as fh:
        if isinstance(iq, SampleSource):
            if abs(float(iq.fs) - rate) <= 1.0:
                src = SampleSource(iq.n, rate, iq.read)
                written, power = _write_resampled(src, rate, fh)
            else:
                written, power = _write_resampled(iq, rate, fh)
        else:
            block = to_10mhz(iq, float(fs), nfft)
            written = block.size
            power = float(np.mean(np.abs(block) ** 2)) if written else 0.0
            block.tofile(fh)
    if written < 8 or power == 0.0:
        return tools, script, 0
    return tools, script, written


def decode_cell(iq, fs, pci, frame_offset, ports, timeout=120, nfft=NFFT_10):
    """MIB and SIB1 for a centred block whose fingerprint already says LTE.

    ``ports`` is the port counts to try, in that order, at each of the two
    frame offsets. Returns the ``decode`` object. Raises :class:`ToolsMissing`
    when the tools directory or ``run_pdsch.sh`` is absent. A missing pycrate
    interpreter leaves ``sib1`` null and still returns the MIB.

    ``nfft`` picks the carrier: 1024 (10 MHz, the default) or 1536 (15 MHz).
    """
    nsf_rate = float(iq.fs if isinstance(iq, SampleSource) else fs)
    # Bandwidth gate is the caller's. Here, only the 10 MHz file format is written.
    tmp = tempfile.mkdtemp(prefix='iqlte-')
    try:
        cf32 = os.path.join(tmp, 'block.cf32')
        tools, script, samples = _prepare_block(iq, nsf_rate, nfft, cf32)
        if not samples:
            return _empty_block()
        o1, o2 = decoder_offsets(frame_offset, nsf_rate, nfft)
        nsf = max(1, int(round(samples / float(_GEOMETRY[nfft][3]))))
        timeout = _call_timeout(timeout, nsf)
        success, success_dump, tried = _search_offsets(
            script, cf32, pci, (o1, o2), nsf, ports, tmp, timeout, nfft=nfft)
        return _decode_result(success, success_dump, tried, tools, nsf)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def decode_cells(iq, fs, specs, ports, timeout=120, budget=15 * 60, nfft=NFFT_10):
    """One ``decode`` object per ``(pci, frame_offset, half_frame)`` in ``specs``.

    Specs are decoded in the order given. The caller passes the strongest
    cell first, so that cell spends the budget before a weaker one.

    ``frame_offset`` is in samples of ``fs``. ``half_frame`` is accepted and
    not used: ``cells`` reports ``half_frame`` 0 always, and the offset is
    only known modulo the 5 ms half-frame. Both boundaries are tried
    (:func:`decoder_offsets` scales the reported offset to 15.36 MS/s and
    also tries 5 ms away), the same as ``decode_cell``.

    ``timeout`` caps one ``pdsch_ue`` call. ``budget`` is the wall-clock
    allowance for every cell together (15 minutes unless the caller says
    otherwise). Each call is also capped at the time still left. A cell
    that has not been started when the budget is gone gets
    ``{"ran": false, "reason": "decode time budget used"}``.

    The resampled file is written once and every cell is read from it. ``iq``
    may be a :data:`SampleSource` (a long span read in pieces; ``fs`` is then
    its own). An empty ``specs`` returns ``[]`` and does not look for the
    outside tools.
    """
    if not specs:
        return []
    nsf_rate = float(iq.fs if isinstance(iq, SampleSource) else fs)
    tmp = tempfile.mkdtemp(prefix='iqlte-')
    try:
        cf32 = os.path.join(tmp, 'block.cf32')
        tools, script, samples = _prepare_block(iq, nsf_rate, nfft, cf32)
        if not samples:
            return [_empty_block() for _ in specs]
        nsf = max(1, int(round(samples / float(_GEOMETRY[nfft][3]))))
        timeout = _call_timeout(timeout, nsf)
        deadline = time.monotonic() + float(budget)
        out = []
        for pci, frame_offset, _half_frame in specs:
            if deadline - time.monotonic() <= 0.0:
                out.append(_budget_used())
                continue
            o1, o2 = decoder_offsets(frame_offset, nsf_rate, nfft)
            success, success_dump, tried = _search_offsets(
                script, cf32, pci, (o1, o2), nsf, ports, tmp, timeout, deadline, nfft)
            if not tried and success is None:
                out.append(_budget_used())
            else:
                out.append(_decode_result(success, success_dump, tried, tools, nsf))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
