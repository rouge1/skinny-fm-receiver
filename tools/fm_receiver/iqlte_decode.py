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
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from fm_receiver import iqlte

# 10 MHz class: 1024-point FFT, 15.36 MS/s, 153600 samples in a 10 ms frame.
RATE_10 = 15.36e6
FRAME_10 = 153600
HALF_10 = 76800
NFFT_10 = 1024
_SUBFRAME = 15360
_SIB1_MIN = 32
_DUMP_MARK = 0xA5

# Runs under the outside interpreter. pycrate stays in that process.
_SIB1_HELPER = r'''
import json
import sys

def _digits(seq):
    return "".join(str(int(d)) for d in seq)

def _bitint(value):
    """A pycrate BIT STRING is (nbits, int); a plain int is itself."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        return int(value[1])
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, "big")
    raise TypeError("not an integer field: %r" % (value,))

def _name(value):
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return value

def main():
    try:
        from pycrate_asn1dir import RRCLTE
    except ImportError as exc:
        sys.stderr.write("pycrate is not available: %s\n" % exc)
        return 1
    data = open(sys.argv[1], "rb").read()
    sib = RRCLTE.SystemInformationBlockType1()
    # Trailing pad (the dump block is at least 32 bytes) is left unread.
    sib.from_uper(data)
    val = sib()
    access = val["cellAccessRelatedInfo"]
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
        "freq_band": int(val["freqBandIndicator"]),
        "cell_barred": _name(access["cellBarred"]),
        "q_rx_lev_min": int(val["cellSelectionInfo"]["q-RxLevMin"]),
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


def to_10mhz(iq, fs_in):
    """``iq`` at ``fs_in`` as complex64 at 15.36 MS/s. Already-there rates are a view cast."""
    fs_in = float(fs_in)
    if abs(fs_in - RATE_10) <= 1.0:
        out = np.ascontiguousarray(iq, dtype=np.complex64)
    else:
        out = np.ascontiguousarray(iqlte._resample(iq, fs_in, RATE_10), dtype=np.complex64)
    bad = ~np.isfinite(out.real) | ~np.isfinite(out.imag)
    if bad.any():
        out = out.copy()
        out[bad] = 0
    return out


def decoder_offsets(frame_offset, fs_in):
    """``(o, (o - 76800) mod 153600)`` at 15.36 MS/s.

    ``frame_offset`` is the SSS frame start in samples of the capture rate
    (modulo 10 ms). The half-frame label does not say which of the two is
    subframe 0, so both are tried.
    """
    if frame_offset is None or not fs_in:
        base = 0
    else:
        base = int(round(float(frame_offset) * RATE_10 / float(fs_in))) % FRAME_10
    return base, (base - HALF_10) % FRAME_10


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

    Returns ``(fields, None)`` or ``(None, reason)``. A missing interpreter is
    :class:`ToolsMissing`. A missing pycrate is a reason that names pycrate,
    not an error exit: the MIB is still valid.
    """
    python = require_program(tools, os.path.join('pyc', 'bin', 'python'))
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
        except OSError as exc:
            raise ToolsMissing('pyc/bin/python') from exc
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


def _run_once(script, cf32, pci, offset, nsf, ports, dump, timeout):
    env = dict(os.environ)
    env['PDSCH_DUMP'] = dump
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


def decode_cell(iq, fs, pci, frame_offset, ports, timeout=120):
    """MIB and SIB1 for a centred block whose fingerprint already says LTE.

    ``ports`` is the port counts to try, in that order, at each of the two
    frame offsets. Returns the ``decode`` object. Raises :class:`ToolsMissing`
    when the tools directory or ``run_pdsch.sh`` (or the pycrate interpreter,
    once a SIB1 payload is in hand) is absent.

    Bandwidths other than 10 MHz are not decoded in this phase.
    """
    tools = tools_dir()
    nsf_rate = float(fs)
    # Bandwidth gate is the caller's. Here, only the 10 MHz file format is written.
    script = require_program(tools, 'run_pdsch.sh')
    block = to_10mhz(iq, nsf_rate)
    if block.size < 8 or float(np.mean(np.abs(block) ** 2)) == 0.0:
        return {
            'ran': False,
            'reason': 'the 15.36 MS/s block is empty',
            'mib': None,
            'offset_samples': None,
            'sib1': None,
            'tried': [],
        }
    o1, o2 = decoder_offsets(frame_offset, nsf_rate)
    nsf = max(1, int(round(block.size / float(_SUBFRAME))))
    tried = []
    tmp = tempfile.mkdtemp(prefix='iqlte-')
    try:
        cf32 = os.path.join(tmp, 'block.cf32')
        with open(cf32, 'wb') as fh:
            block.tofile(fh)
        success = None
        success_dump = None
        for offset in (o1, o2):
            for count in ports:
                dump = os.path.join(tmp, 'dump-%d-%d.bin' % (offset, int(count)))
                text, ok = _run_once(script, cf32, pci, offset, nsf, count, dump, timeout)
                tried.append({'offset': int(offset), 'ports': int(count), 'decoded': bool(ok)})
                if ok:
                    success = parse_mib(text)
                    success_dump = dump
                    break
            if success is not None:
                break
        if success is None:
            return {
                'ran': True,
                'reason': 'MIB not decoded',
                'mib': None,
                'offset_samples': None,
                'sib1': None,
                'sib1_reason': 'MIB not decoded',
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
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
