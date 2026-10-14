"""The measurement tools (phase m2): ``tools/iq-measure`` and ``tools/iq-picture`` against CONTRACTS-measure.md.

Both tools are run as subprocesses, from a temporary working directory, on synthetic
recordings built here (numpy, seeded, 1 MS/s, complex64, 1 s each) in a temporary
folder. The builders are copies of the ones in test_iq_measure.py and signals.py.
What the checks pin:

- iq-measure: stdout is only the card (JSON) and its class is the signal's, for CW,
  OOK, FSK, FM, AM and noise; --t0/--t1 narrow the span the card reports; --band
  restricts the analysis; a bare .cfile needs --rate (and takes --center), else
  exit 1 with a message on stderr and nothing on stdout; an IQ .sdvr and a .cdif of
  a CW tone are read, with the centre read back; --card writes <base>.card.json
  beside a .cfile (the file read back is the printed card), and for .sdvr, .cdif,
  a sweep .sdvr, a .fft or a missing file exits 1 and writes nothing; it never
  overwrites a file that is not a card; without --card nothing is written and no
  input is changed.
- iq-picture: a valid PNG of at least 800 x 500 px for a .cfile (with and without
  its .sigmf-meta), an IQ .sdvr and a .cdif; exit 1, a message and no PNG for a
  sweep .sdvr, a .fft, a missing file and a bare .cfile without --rate; it writes
  only the PNG it is told to; and its peak panel (``peak_panel``, the module loaded
  here) shows both tones of a two-tone file as local maxima.
- the import rule: a clean subprocess that loads each tool as a module lists none of
  gnuradio, PyQt5, library, radios (iq-measure also not scipy or matplotlib).

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python tools/tests/test_iq_tools.py
(every subprocess has a 120 s limit; a timeout is a failure)
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MEASURE = os.path.join(ROOT, 'tools', 'iq-measure')
PICTURE = os.path.join(ROOT, 'tools', 'iq-picture')

RATE = 1e6
CENTRE = 100e6
TIMEOUT = 120
MIN_W, MIN_H = 800, 500
CLASS_OF = {'cw': 'carrier', 'ook': 'ook', 'ask': 'ask', 'fsk': 'fsk', 'fm': 'fm', 'am': 'am',
            'noise': 'noise', 'two_tone': 'unknown'}
TOP_KEYS = ('schema', 'source', 'level', 'occupied', 'tones', 'bursts', 'envelope',
            'frequency', 'symbol_rate_baud', 'class', 'evidence')
SOURCE_KEYS = ('path', 'kind', 'rate_sps', 'center_hz', 't0_s', 't1_s', 'samples')
BANNED = ['gnuradio', 'PyQt5', 'library', 'radios']
FOLDER = None
SIG = None
WORK = None
SIG_STATE = None
WORK_STATE = None
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


def eq(got, want):
    return got == want, f'got {got!r}, want {want!r}'


def near(got, want, tol):
    if got is None:
        return False, f'got None, want {want:.6g}'
    return abs(got - want) <= tol, f'got {got:.6g}, want {want:.6g} +/- {tol:.3g}'


def snapshot(folder):
    """Every file of ``folder``: its digest, size and modification time."""
    state = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        st = os.stat(path)
        with open(path, 'rb') as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        state[name] = (digest, st.st_size, st.st_mtime_ns)
    return state


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


def run(tool, args, cwd):
    cmd = [sys.executable, tool] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=clean_env(), capture_output=True,
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


def tones_found(card, freq, rate=RATE):
    """Whether a tone of the card is within 2 bins (rate / 1024) of ``freq``."""
    found = [t['freq_hz'] for t in card['tones']]
    ok = any(abs(f - freq) <= 2 * rate / 1024 for f in found)
    return ok, f'want a tone near {freq / 1e6:.4f} MHz, tones at {[round(f) for f in found]}'


# ---- synthetic signals (copied from test_iq_measure.py and signals.py) ----------

def _t(seconds, rate=RATE):
    return np.arange(int(round(seconds * rate))) / rate


def _power_noise(rng, n, power):
    return np.sqrt(power / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def _with_noise(sig, snr_db, seed, power=None):
    p = np.mean(np.abs(sig) ** 2) if power is None else power
    rng = np.random.default_rng(seed)
    return (sig + _power_noise(rng, len(sig), p / 10 ** (snr_db / 10))).astype(np.complex64)


def cw_iq(seconds=1.0, offset=50e3, amp=1.0, snr_db=30.0, seed=1):
    t = _t(seconds)
    return _with_noise(amp * np.exp(2j * np.pi * offset * t), snr_db, seed)


def _run_limited_bits(rng, count, longest=3):
    bits = np.zeros(count, dtype=np.uint8)
    for i in range(count):
        bit = int(rng.integers(0, 2))
        if i >= longest and np.all(bits[i - longest:i] == bit):
            bit = 1 - bit
        bits[i] = bit
    return bits


def ook_iq(bursts=12, snr_db=30.0, seed=2, low=0.0):
    """OOK at 4,000 baud on a carrier 20 kHz up: bursts of 40 ms every 100 ms from 50 ms.
    ``low`` is the level of a 0 bit (0 for OOK; above 10 % of the 1 level for ASK)."""
    rng = np.random.default_rng(seed)
    t = _t(1.3)
    keyed = np.zeros(len(t))
    for k in range(bursts):
        bits = _run_limited_bits(rng, 160)
        start = int(round((0.05 + 0.1 * k) * RATE))
        keyed[start:start + 160 * 250] = low + (1 - low) * np.repeat(bits, 250)
    sig = keyed * np.exp(2j * np.pi * 20e3 * t)
    return _with_noise(sig, snr_db, seed + 100, power=1.0)


def fsk_iq(seconds=1.0, snr_db=30.0, seed=3):
    """FSK at 9,600 baud, 5 kHz either side of the centre, continuous phase."""
    rng = np.random.default_rng(seed)
    t = _t(seconds)
    bits = rng.integers(0, 2, int(np.ceil(seconds * 9600)) + 1)
    freq = np.where(bits[np.floor(t * 9600).astype(int)] == 1, 5e3, -5e3)
    sig = np.exp(2j * np.pi * np.cumsum(freq) / RATE)
    return _with_noise(sig, snr_db, seed + 100)


def fm_iq(seconds=1.0, snr_db=30.0, seed=4):
    """FM, 75 kHz deviation by a 1 kHz tone (index 75), the carrier at the centre."""
    t = _t(seconds)
    return _with_noise(np.exp(1j * 75.0 * np.sin(2 * np.pi * 1e3 * t)), snr_db, seed)


def am_iq(seconds=1.0, snr_db=30.0, seed=5):
    """AM, 80 % depth by a 1 kHz tone, the carrier 20 kHz up."""
    t = _t(seconds)
    sig = (1 + 0.8 * np.cos(2 * np.pi * 1e3 * t)) * np.exp(2j * np.pi * 20e3 * t)
    return _with_noise(sig, snr_db, seed)


def tones_iq(offsets, amps, seconds=1.0, snr_db=30.0, seed=6):
    t = _t(seconds)
    sig = np.zeros(len(t), dtype=np.complex128)
    for f, a in zip(offsets, amps):
        sig += a * np.exp(2j * np.pi * f * t)
    return _with_noise(sig, snr_db, seed)


def two_tone_iq(seconds=1.0, snr_db=30.0, seed=7):
    """Two equal tones, +20 kHz and -60 kHz."""
    return tones_iq([20e3, -60e3], [0.5, 0.5], seconds, snr_db, seed)


def noise_iq(seconds=1.0, seed=8):
    return _power_noise(np.random.default_rng(seed), len(_t(seconds)), 1.0).astype(np.complex64)


def write_cfile(path, data):
    np.asarray(data, dtype=np.complex64).tofile(path)
    return path


def write_sigmf(base, rate, centre):
    with open(base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate},
                   'captures': [{'core:sample_start': 0, 'core:frequency': centre}],
                   'annotations': []}, fh)


def _sdvr_header(tag, when, interval, count, centre=0.0, bins=0, scale=0.0,
                 start_hz=0.0, bin_hz=0.0, gain=0.0, offset=0.0):
    """One chunk's header page of a Sceptre DVR file (see sceptre_dvr.py)."""
    page = bytearray(4096)
    struct.pack_into('<d', page, 0, centre)
    struct.pack_into('<f', page, 8, scale)
    page[0x10:0x12] = tag
    page[0x12:0x14] = b'\x00\x01' if tag == b'CI' else b'\x00\x02'
    struct.pack_into('<dd', page, 0x14, math.floor(when), when - math.floor(when))
    struct.pack_into('<d', page, 0x24, interval)
    struct.pack_into('<I', page, 0x2c, count)
    if tag == b'SB':
        struct.pack_into('<ff', page, 8, gain, offset)
        struct.pack_into('<dd', page, 0x34, start_hz, bin_hz)
        struct.pack_into('<I', page, 0x44, bins)
    return bytes(page)


def _pad(data):
    return data + bytes(-len(data) % 4096)


def _age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


def write_sdvr(path, iq, rate, center_hz, run=1 << 15, start=1.7907e9 + 0.25, ref_dbm=-20.0,
               age=60.0):
    """Write ``iq`` (complex, |x| < 1) as an IQ Sceptre DVR, ``path``: runs of int16 I, Q,
    each with its header page, spectrum tiles between them, the ring wrapped and a
    stale run at the end, as the real files are. The scale makes full scale
    ``ref_dbm + 10`` dBm."""
    n = len(iq) // run * run
    q = np.empty((n, 2), dtype='<i2')
    q[:, 0] = np.clip(np.round(iq[:n].real * 32768.0), -32768, 32767)
    q[:, 1] = np.clip(np.round(iq[:n].imag * 32768.0), -32768, 32767)
    interval = 1.0 / rate
    scale = 10 ** ((ref_dbm + 10) / 20) / 32768.0
    chunks = []
    for k in range(n // run):
        when = start + k * run * interval
        chunks.append(_sdvr_header(b'CI', when, interval, run, center_hz, scale=scale)
                      + _pad(q[k * run:(k + 1) * run].tobytes()))
        for j in range((0, 1, 2)[k % 3]):
            chunks.append(_sdvr_header(b'SB', when, 2.34e-3, 16, bins=64)
                          + _pad(bytes((k * 7 + j) % 251 for _ in range(16 * 64))))
    cut = len(chunks) * 2 // 3
    chunks = chunks[cut:] + chunks[:cut]
    chunks.append(_sdvr_header(b'CI', 1.6e9, interval, run, center_hz)
                  + _pad(bytes(run * 4)))
    end = start + n * interval
    head = bytearray(4096)
    head[:4] = b'SDVR'
    stream = bytearray(4096)
    struct.pack_into('<dd', stream, 0, math.floor(start), start - math.floor(start))
    struct.pack_into('<dd', stream, 16, math.floor(end), end - math.floor(end))
    struct.pack_into('<dd', stream, 32, center_hz - rate / 2, center_hz + rate / 2)
    with open(path, 'wb') as fh:
        fh.write(bytes(head) + bytes(stream) + bytes(stream) + bytes(4096))
        for c in chunks:
            fh.write(c)
    _age(path, age)
    return path


def sweep_tile_bytes(k, bins, sweeps=16):
    b = np.arange(bins)[None, :]
    w = np.arange(sweeps)[:, None]
    return ((b * 3 + w * 5 + k * 7) % 251 - 125).astype(np.int8)


def write_sweep_sdvr(path, tiles=8, bins=4096, start_hz=4882.8125, bin_hz=4882.8125,
                     interval=0.2325, sweeps=16, start=1.7907e9 + 0.25, age=60.0):
    """A sweep DVR: ``tiles`` tiles of ``sweeps`` sweeps of ``bins`` signed bytes, with
    a stale tile of an earlier IQ recording at the end."""
    chunks = []
    for k in range(tiles):
        when = start + k * sweeps * interval
        chunks.append(_sdvr_header(b'SB', when, interval, sweeps, bins=bins,
                                   start_hz=start_hz, bin_hz=bin_hz)
                      + _pad(sweep_tile_bytes(k, bins, sweeps).T.tobytes()))
    cut = len(chunks) * 2 // 3
    chunks = chunks[cut:] + chunks[:cut]
    chunks.append(_sdvr_header(b'SB', start - 3600.0, interval, sweeps, bins=bins,
                               start_hz=start_hz, bin_hz=bin_hz)
                  + _pad(bytes(bins * sweeps)))
    head = bytearray(4096)
    head[:4] = b'SDVR'
    stream = bytearray(4096)
    old = start - 20000.0
    struct.pack_into('<dd', stream, 0, math.floor(old), 0.0)
    struct.pack_into('<dd', stream, 16, math.floor(old) + 8, 0.0)
    struct.pack_into('<dd', stream, 32, 86e6, 114e6)
    with open(path, 'wb') as fh:
        fh.write(bytes(head) + bytes(stream) + bytes(stream) + bytes(4096))
        for c in chunks:
            fh.write(c)
    _age(path, age)
    return path


def write_blue(path, data, rate, fmt='CF', keywords=None, epoch='2026-09-30T10:44:21Z',
               subsize=0):
    """Write ``data`` as a little-endian BLUE file, laid out as Sceptre's are (see
    sceptre_blue.py). Complex data is float32 pairs; ``subsize`` makes it 2-D (type 2001)."""
    raw = np.asarray(data)
    if fmt[0] == 'C':
        raw = np.column_stack([raw.real, raw.imag])
    raw = raw.astype('<f4').tobytes()
    ext = bytearray()
    for tag, value in (keywords or {}).items():
        if isinstance(value, str):
            kind, body = b'A', value.encode() + b'\0'
        elif isinstance(value, int):
            kind, body = b'L', struct.pack('<i', value)
        else:
            kind, body = b'D', struct.pack('<d', value)
        t = tag.encode()
        lkey = -(-(8 + len(body) + len(t)) // 8) * 8
        ext += struct.pack('<ihbc', lkey, lkey - len(body), len(t), kind)
        ext += body + t + bytes(lkey - 8 - len(body) - len(t))
    head = bytearray(512)
    head[:4] = b'BLUE'
    head[4:12] = b'EEEIEEEI'
    ext_start = -(-(512 + len(raw)) // 512)
    struct.pack_into('<ii', head, 24, ext_start, len(ext))
    struct.pack_into('<dd', head, 32, 512.0, float(len(raw)))
    struct.pack_into('<i', head, 48, 2001 if subsize else 1001)
    head[52:54] = fmt.encode()
    struct.pack_into('<d', head, 56, 2421917061.0)
    if subsize:
        struct.pack_into('<dd', head, 256, 4882.8125, 4882.8125)
        struct.pack_into('<i', head, 272, 3)
        struct.pack_into('<dd', head, 280, 0.855, 1.0 / rate)
        struct.pack_into('<i', head, 296, 1)
    else:
        struct.pack_into('<dd', head, 256, 0.855, 1.0 / rate)
        struct.pack_into('<i', head, 272, 1)
    struct.pack_into('<i', head, 276, subsize)
    with open(path, 'wb') as fh:
        fh.write(head + raw)
        fh.write(bytes(ext_start * 512 - 512 - len(raw)))
        fh.write(bytes(ext))
    return path


def build_signals():
    """The shared synthetic recordings, in SIG: one .cfile with its .sigmf-meta per
    signal, a bare .cfile, an IQ .sdvr and a .cdif of a CW tone, a sweep .sdvr, a .fft."""
    os.makedirs(SIG)
    data = {'cw': cw_iq(amp=0.5), 'ook': ook_iq(), 'ask': ook_iq(low=0.5, seed=10),
            'fsk': fsk_iq(), 'fm': fm_iq(), 'am': am_iq(), 'noise': noise_iq(),
            'two_tone': two_tone_iq()}
    for name, arr in data.items():
        base = os.path.join(SIG, name)
        write_cfile(base + '.cfile', arr)
        write_sigmf(base, RATE, CENTRE)
    write_cfile(os.path.join(SIG, 'bare.cfile'), data['cw'])
    write_cfile(os.path.join(SIG, 'short.cfile'), data['cw'][:1000])
    with open(os.path.join(SIG, 'thing.bin'), 'wb') as fh:
        fh.write(np.random.default_rng(19).bytes(8192))
    write_sdvr(os.path.join(SIG, 'iq.sdvr'), data['cw'], RATE, CENTRE)
    write_blue(os.path.join(SIG, 'iq.cdif'), data['cw'], RATE, keywords={'RF_FREQ': CENTRE})
    write_sweep_sdvr(os.path.join(SIG, 'sweep.sdvr'))
    spectrum = np.abs(np.random.default_rng(17).standard_normal(64 * 8)).astype(np.float32)
    write_blue(os.path.join(SIG, 'spectrum.fft'), spectrum, RATE, fmt='SF', subsize=64)


def sig(name):
    return os.path.join(SIG, name + '.cfile')


def copy_into(folder, *names):
    """Copy the named files of SIG (a .cfile name, or a name with its extension) into ``folder``."""
    for name in names:
        shutil.copy(os.path.join(SIG, name), folder)


def copy_cw(folder):
    """A .cfile of the CW tone with its .sigmf-meta, copied into ``folder``."""
    copy_into(folder, 'cw.cfile', 'cw.sigmf-meta')
    return os.path.join(folder, 'cw.cfile')


# ---- the runs ----------------------------------------------------------------

def measure_run(key, args):
    """``iq-measure`` on ``args`` from WORK, run once per key."""
    return memo(('measure', key), lambda: run(MEASURE, args, WORK))


def picture(key, args):
    """``iq-picture`` on ``args`` (the file, then options), with a fresh output folder
    holding only ``pic.png``; run once per key. Returns (run, png path, folder)."""
    def make():
        folder = fresh('pic-' + key)
        out = os.path.join(folder, 'pic.png')
        return run(PICTURE, [args[0], out] + list(args[1:]), WORK), out, folder
    return memo(('picture', key), make)


def png_size(path):
    """(width, height) of a PNG, read from its header, or (None, reason)."""
    with open(path, 'rb') as fh:
        data = fh.read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None, 'not a PNG (no signature)'
    if data[12:16] != b'IHDR' or len(data) < 24:
        return None, 'no IHDR chunk'
    if data[-8:-4] != b'IEND':
        return None, 'no IEND chunk at the end'
    w, h = struct.unpack('>II', data[16:24])
    return (w, h), ''


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def picture_module():
    return memo('picture-module', lambda: load_module('iq_picture_under_test', PICTURE))


def panel():
    """``peak_panel`` of the two-tone file, called once."""
    return memo('panel', lambda: picture_module().peak_panel(sig('two_tone')))


def tone_line(freq, db, want):
    """Whether the peak panel has a local maximum within 2 bins of ``want``, at least
    15 dB above the median of the panel. The bin is rate / the panel's length."""
    bin_hz = RATE / len(db)
    k = int(np.argmin(np.abs(freq - want)))
    lo, hi = max(0, k - 2), min(len(db), k + 3)
    j = lo + int(np.argmax(db[lo:hi]))
    is_peak = 0 < j < len(db) - 1 and db[j] >= db[j - 1] and db[j] >= db[j + 1]
    level = float(db[j] - np.median(db))
    ok = is_peak and abs(freq[j] - want) <= 2 * bin_hz and level >= 15
    return ok, (f'peak at {freq[j] / 1e6:.6f} MHz, {level:.1f} dB over the median, '
                f'local maximum {is_peak}; want near {want / 1e6:.6f} MHz (bin {bin_hz:.0f} Hz)')


# ---- iq-measure: the card of each signal -------------------------------------

def check_measure_classes():
    for name, want in CLASS_OF.items():
        def test(n=name, w=want):
            r = measure_run(n, [sig(n)])
            return printed(r, lambda c: eq(c.get('class'), w))
        expect(f'iq-measure {name}.cfile: exit 0, stdout is only the card, class {want}', test)


def check_measure_card_form():
    def test():
        r = measure_run('cw', [sig('cw')])
        return printed(r, lambda c: (
            not [k for k in TOP_KEYS if k not in c] and c.get('schema') == 1
            and not [k for k in SOURCE_KEYS if k not in c.get('source', {})],
            f"keys {sorted(c)}, schema {c.get('schema')}"))
    expect('iq-measure: the printed card has the spec keys and schema 1', test)


def check_measure_options():
    def span():
        r = measure_run('span', [sig('cw'), '--t0', '0.25', '--t1', '0.75'])
        return printed(r, lambda c: (
            near(c['source']['t0_s'], 0.25, 1e-6)[0] and near(c['source']['t1_s'], 0.75, 1e-6)[0]
            and c['source']['samples'] == 500000,
            f"t0 {c['source']['t0_s']}, t1 {c['source']['t1_s']}, samples {c['source']['samples']}"))
    expect('--t0 0.25 --t1 0.75: the card reports that span (source t0_s, t1_s)', span)

    def band():
        r = measure_run('band', [sig('two_tone'), '--band', f'{CENTRE + 5e3:.0f}',
                                 f'{CENTRE + 200e3:.0f}'])

        def then(c):
            has_in, why_in = tones_found(c, CENTRE + 20e3)
            has_out = tones_found(c, CENTRE - 60e3)[0]
            return (has_in and not has_out,
                    why_in + ('' if not has_out else '; the -60 kHz tone is in the band'))
        return printed(r, then)
    expect('--band (+5 kHz to +200 kHz): the +20 kHz tone is kept, the -60 kHz tone is not', band)


def check_measure_inputs():
    expect('bare .cfile without --rate: exit 1, a message on stderr, nothing on stdout',
           lambda: refusal(measure_run('bare-norate', [os.path.join(SIG, 'bare.cfile')])))
    bare_path = os.path.join(SIG, 'bare.cfile')
    expect('bare .cfile with --center alone (no rate): exit 1, a message, nothing on stdout',
           lambda: refusal(measure_run('bare-center-only', [bare_path, '--center', str(int(CENTRE))])))

    def rate_only():
        r = measure_run('bare-rate-only', [bare_path, '--rate', str(int(RATE))])
        return printed(r, lambda c: (
            c['class'] == 'carrier' and near(c['source']['rate_sps'], RATE, 1e-3)[0],
            f"class {c['class']}, rate {c['source']['rate_sps']}"))
    expect('bare .cfile with --rate alone: exit 0, a carrier, the rate read back', rate_only)

    def span_class():
        span = printed(measure_run('ook-span', [sig('ook'), '--t0', '0', '--t1', '0.04']),
                       lambda c: eq(c['class'], 'noise'))
        whole = printed(measure_run('ook', [sig('ook')]), lambda c: eq(c['class'], 'ook'))
        return (span[0] and whole[0], f'span: {span[1]}; whole file: {whole[1]}')
    expect('a --t0/--t1 span before the first burst is noise, while the whole OOK file is ook',
           span_class)

    def bare():
        r = measure_run('bare-rate', [os.path.join(SIG, 'bare.cfile'), '--rate', str(int(RATE)),
                                      '--center', str(int(CENTRE))])
        return printed(r, lambda c: (
            c['class'] == 'carrier' and c['source']['kind'] == 'cfile'
            and near(c['source']['rate_sps'], RATE, 1e-3)[0]
            and near(c['source']['center_hz'], CENTRE, 1.0)[0],
            f"class {c['class']}, rate {c['source']['rate_sps']}, "
            f"centre {c['source']['center_hz']}"))
    expect('bare .cfile with --rate and --center: a carrier, the rate and centre read back', bare)

    def kind(key, name, want_kind):
        def test():
            r = measure_run(key, [os.path.join(SIG, name)])
            return printed(r, lambda c: (
                c['class'] == 'carrier' and c['source']['kind'] == want_kind
                and near(c['source']['center_hz'], CENTRE, 1.0)[0],
                f"class {c['class']}, kind {c['source']['kind']}, "
                f"centre {c['source']['center_hz']}"))
        return test
    expect('IQ .sdvr of a CW tone: exit 0, a carrier, centre 100 MHz read back',
           kind('sdvr', 'iq.sdvr', 'sdvr'))
    expect('.cdif of a CW tone: exit 0, a carrier, centre 100 MHz read back',
           kind('cdif', 'iq.cdif', 'cdif'))


def refuse_measure(key, name, extra):
    """A sweep .sdvr, a .fft or a missing file: exit 1 with a message, and no file written."""
    def test():
        folder = fresh(f'refuse-{key}-{len(extra)}')
        src = os.path.join(SIG, name)
        if os.path.isfile(src):
            shutil.copy(src, folder)
        path = os.path.join(folder, name)
        before = snapshot(folder)
        r = run(MEASURE, [path] + list(extra), WORK)
        after = snapshot(folder)
        ok = not r.timed_out and r.code == 1 and r.err.strip() != '' and before == after
        if not extra:
            ok = ok and r.out.strip() == ''
        return ok, describe(r) + ('' if before == after else '; something was written')
    return test


def check_measure_refusals():
    cases = [('a sweep .sdvr', 'sweep', 'sweep.sdvr'), ('a .fft', 'fft', 'spectrum.fft'),
             ('a missing file', 'missing', 'nothere.cfile'),
             ('a file of 1,000 samples', 'short', 'short.cfile'),
             ('a file that is not a .cfile, .sdvr or .cdif', 'junk', 'thing.bin'),
             ('a bare .cfile without --rate', 'bare', 'bare.cfile')]
    for label, key, name in cases:
        expect(f'iq-measure refuses {label}: exit 1, a message, nothing printed',
               refuse_measure(key, name, []))
        expect(f'iq-measure --card refuses {label}: exit 1, nothing written',
               refuse_measure(key, name, ['--card']))

    def options_refused(key, args):
        return lambda: refusal(measure_run(key, args))
    expect('--band with its low edge above its high edge: exit 1, a message, nothing on stdout',
           options_refused('band-inverted', [sig('two_tone'), '--band', f'{CENTRE + 200e3:.0f}',
                                             f'{CENTRE + 5e3:.0f}']))
    expect('--band outside the recording (2 MHz to 3 MHz off centre): exit 1, a message',
           options_refused('band-outside', [sig('two_tone'), '--band', f'{CENTRE + 2e6:.0f}',
                                            f'{CENTRE + 3e6:.0f}']))
    expect('--t0/--t1 span under 1,024 samples: exit 1, a message, nothing on stdout',
           options_refused('span-short', [sig('cw'), '--t0', '0', '--t1', '0.0005']))


# ---- iq-measure: --card ----------------------------------------------------

def check_card_writes():
    def beside():
        folder = fresh('card-beside')
        cfile = copy_cw(folder)
        r = run(MEASURE, [cfile, '--card'], WORK)
        if r.timed_out or r.code != 0:
            return False, describe(r)
        printed_card = card_of(r)
        target = os.path.join(folder, 'cw.card.json')
        if printed_card is None:
            return False, 'stdout is not only one JSON card'
        if not os.path.isfile(target):
            return False, 'no cw.card.json beside the .cfile'
        if os.path.exists(os.path.join(WORK, 'cw.card.json')):
            return False, 'the card was written in the working directory'
        with open(target) as fh:
            written = json.load(fh)
        return written == printed_card, 'the card file read back is not the printed card'
    expect('--card beside a .cfile (not in the working directory): <base>.card.json is '
           'written and equals the printed card', beside)

    for key, name, label in [('sdvr', 'iq.sdvr', 'an IQ .sdvr'), ('cdif', 'iq.cdif', 'a .cdif')]:
        def refuse(k=key, n=name):
            folder = fresh('card-refuse-' + k)
            copy_into(folder, n)
            path = os.path.join(folder, n)
            before = snapshot(folder)
            r = run(MEASURE, [path, '--card'], WORK)
            after = snapshot(folder)
            ok = not r.timed_out and r.code == 1 and r.err.strip() != '' and before == after
            return ok, describe(r) + ('' if before == after else '; something was written')
        expect(f'--card refuses {label}: exit 1 and writes nothing beside it', refuse)

    def keep_other_json():
        folder = fresh('card-keep')
        cfile = copy_cw(folder)
        target = os.path.join(folder, 'cw.card.json')
        with open(target, 'w') as fh:
            fh.write('{"other": 1}')
        r = run(MEASURE, [cfile, '--card'], WORK)
        with open(target) as fh:
            text = fh.read()
        return (not r.timed_out and r.code == 1 and text == '{"other": 1}',
                f'exit {r.code}, file now {text[:40]!r}; a file that is not a card was '
                'overwritten or the run did not refuse')
    expect('--card never overwrites a JSON file that is not a card: exit 1, file untouched',
           keep_other_json)

    def keep_garbage():
        folder = fresh('card-garbage')
        cfile = copy_cw(folder)
        target = os.path.join(folder, 'cw.card.json')
        with open(target, 'wb') as fh:
            fh.write(b'\x00\x01 not json')
        r = run(MEASURE, [cfile, '--card'], WORK)
        with open(target, 'rb') as fh:
            data = fh.read()
        return (not r.timed_out and r.code == 1 and data == b'\x00\x01 not json',
                f'exit {r.code}; a file that is not JSON was overwritten or the run did not refuse')
    expect('--card never overwrites a file that is not JSON: exit 1, file untouched', keep_garbage)

    def replace_card():
        folder = fresh('card-replace')
        cfile = copy_cw(folder)
        target = os.path.join(folder, 'cw.card.json')
        with open(target, 'w') as fh:
            json.dump({'schema': 1, 'old': True}, fh)
        r = run(MEASURE, [cfile, '--card'], WORK)
        if r.timed_out or r.code != 0:
            return False, describe(r)
        printed_card = card_of(r)
        if printed_card is None:
            return False, 'stdout is not only one JSON card'
        with open(target) as fh:
            written = json.load(fh)
        return written == printed_card, 'an earlier card of its own was not replaced'
    expect('--card replaces an earlier card of its own (a file with a schema key)', replace_card)

    def no_card_no_write():
        folder = fresh('no-card')
        cfile = copy_cw(folder)
        before = snapshot(folder)
        work_before = snapshot(WORK)
        r = run(MEASURE, [cfile, '--t0', '0.1', '--t1', '0.6'], WORK)
        after = snapshot(folder)
        return (not r.timed_out and r.code == 0 and before == after
                and snapshot(WORK) == work_before,
                'a run without --card wrote a file or changed its input')
    expect('without --card: nothing is written beside the file or in the working directory',
           no_card_no_write)


# ---- iq-picture ----------------------------------------------------------------

def check_picture_pngs():
    def png_check(key, args):
        def test():
            r, out, _ = picture(key, args)
            if r.timed_out or r.code != 0:
                return False, describe(r)
            if not os.path.isfile(out):
                return False, 'no PNG written'
            size, why = png_size(out)
            if size is None:
                return False, why
            w, h = size
            return (w >= MIN_W and h >= MIN_H, f'{w} x {h} px, want at least {MIN_W} x {MIN_H}')
        return test

    expect('iq-picture of a .cfile (with .sigmf-meta): exit 0, a PNG of at least 800 x 500',
           png_check('cfile', [sig('cw')]))
    expect('iq-picture of a bare .cfile with --rate and --center: a PNG of at least 800 x 500',
           png_check('bare', [os.path.join(SIG, 'bare.cfile'), '--rate', str(int(RATE)),
                              '--center', str(int(CENTRE))]))
    expect('iq-picture of an IQ .sdvr: a PNG of at least 800 x 500',
           png_check('sdvr', [os.path.join(SIG, 'iq.sdvr')]))
    expect('iq-picture of a .cdif: a PNG of at least 800 x 500',
           png_check('cdif', [os.path.join(SIG, 'iq.cdif')]))
    expect('iq-picture with --t0, --t1, --band and --nfft: exit 0, a PNG of at least 800 x 500',
           png_check('options', [sig('two_tone'), '--t0', '0.1', '--t1', '0.6',
                                 '--band', f'{CENTRE - 100e3:.0f}', f'{CENTRE + 100e3:.0f}',
                                 '--nfft', '2048']))


def picture_refusal(key, name, extra=()):
    """Exit 1, a message on stderr, and the refusal folder unchanged (no PNG, no other file)."""
    def test():
        folder = fresh('refuse-pic-' + key)
        src = os.path.join(SIG, name)
        if os.path.isfile(src):
            shutil.copy(src, folder)
        path = os.path.join(folder, name)
        out = os.path.join(folder, 'pic.png')
        before = snapshot(folder)
        r = run(PICTURE, [path, out] + list(extra), WORK)
        after = snapshot(folder)
        ok = not r.timed_out and r.code == 1 and r.err.strip() != '' and before == after
        return ok, describe(r) + ('' if before == after else '; something was written')
    return test


def check_picture_refusals():
    expect('iq-picture refuses a sweep .sdvr: exit 1, a message, no PNG',
           picture_refusal('sweep', 'sweep.sdvr'))
    expect('iq-picture refuses a .fft: exit 1, a message, no PNG',
           picture_refusal('fft', 'spectrum.fft'))
    expect('iq-picture refuses a missing file: exit 1, a message, no PNG',
           picture_refusal('missing', 'nothere.cfile'))
    expect('iq-picture refuses a bare .cfile without --rate: exit 1, a message, no PNG',
           picture_refusal('bare', 'bare.cfile'))


def check_picture_writes_only_png():
    def test():
        r, out, folder = picture('only', [sig('cw')])
        if r.timed_out or r.code != 0:
            return False, describe(r)
        names = sorted(os.listdir(folder))
        return names == ['pic.png'], f'the output folder holds {names}, want only pic.png'
    expect('iq-picture writes only the PNG it is told to (the output folder holds only it)', test)


def check_peak_panel():
    def shapes():
        freq, db = panel()
        ok = (isinstance(freq, np.ndarray) and isinstance(db, np.ndarray)
              and freq.ndim == 1 and db.ndim == 1 and len(freq) == len(db) and len(db) > 0)
        return ok, (f'types {type(freq).__name__} and {type(db).__name__}, '
                    f'lengths {getattr(freq, "shape", None)} and {getattr(db, "shape", None)}')
    expect('peak_panel returns two equal-length numpy arrays (frequency, peak level)', shapes)

    def line(want, label):
        def test():
            freq, db = panel()
            return tone_line(np.asarray(freq, float), np.asarray(db, float), want)
        expect(f'two-tone peak panel: the {label} tone is a local maximum 15 dB over the median, '
               'within 2 bins', test)
    line(CENTRE + 20e3, '+20 kHz')
    line(CENTRE - 60e3, '-60 kHz')


# ---- the import rule ---------------------------------------------------------

def probe_code(modules):
    lines = ['import importlib.machinery, importlib.util, json, sys']
    for name, path in modules:
        lines.append(f'loader = importlib.machinery.SourceFileLoader({name!r}, {path!r})')
        lines.append(f'spec = importlib.util.spec_from_loader({name!r}, loader)')
        lines.append('module = importlib.util.module_from_spec(spec)')
        lines.append('loader.exec_module(module)')
    lines.append('print(json.dumps(sorted(sys.modules)))')
    return '\n'.join(lines)


def clean_import(modules, banned):
    def test():
        try:
            proc = subprocess.run([sys.executable, '-c', probe_code(modules)], cwd=WORK,
                                  env=clean_env(), capture_output=True, text=True,
                                  timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, f'timed out after {TIMEOUT} s'
        if proc.returncode != 0:
            return False, f'the subprocess failed: {last_line(proc.stderr)}'
        mods = json.loads(proc.stdout.strip().splitlines()[-1])
        hits = sorted({m for m in mods if any(part in banned for part in m.split('.'))})
        return not hits, f'loaded {hits}'
    return test


def check_import_clean():
    expect('iq-picture imports none of gnuradio, PyQt5, library, radios (clean subprocess)',
           clean_import([('iq_picture_probe', PICTURE)], BANNED))
    expect('iq-measure imports none of gnuradio, PyQt5, library, radios, dsp, scipy, matplotlib',
           clean_import([('iq_measure_probe', MEASURE)], BANNED + ['dsp', 'scipy', 'matplotlib']))


def check_inputs_unchanged():
    expect('no run changed the recordings it read or wrote anything beside them',
           lambda: (snapshot(SIG) == SIG_STATE, 'a file in the recordings folder changed'))
    expect('no run wrote into the working directory',
           lambda: (snapshot(WORK) == WORK_STATE, 'the working directory changed'))


def dbm_pair():
    """A .cdif of a 1 mW tone (raw amplitude 1000, DATA_GAIN 60) and the same samples
    without the keyword, in a folder of their own (not the recordings folder)."""
    def build():
        folder = fresh('dbm')
        raw = cw_iq(amp=1000.0, seed=41)
        gain = os.path.join(folder, 'gain.cdif')
        plain = os.path.join(folder, 'plain.cdif')
        write_blue(gain, raw, RATE, keywords={'RF_FREQ': CENTRE, 'DATA_GAIN': 60.0})
        write_blue(plain, raw, RATE, keywords={'RF_FREQ': CENTRE})
        return gain, plain
    return memo('dbm-pair', build)


def check_picture_dbm():
    def axis(path, want):
        def test():
            src = picture_module()._open(path, None, None)
            unit = picture_module()._db(np.ones(1), src.full_scale_dbm)[1]
            return eq(unit, want)
        return test
    expect('iq-picture of a .cdif with DATA_GAIN: the level axis is labelled dBm',
           axis(dbm_pair()[0], 'dBm'))
    expect('iq-picture of a .cdif without DATA_GAIN: the level axis stays dBFS',
           axis(dbm_pair()[1], 'dBFS'))

    # Tone-calibrated, as the app's sweep is: the 1 mW tone reads 0 dBm (+/- 1).
    expect('peak_panel: a 1 mW tone with DATA_GAIN 60 peaks at 0 dBm (+/- 1)',
           lambda: near(float(np.max(picture_module().peak_panel(dbm_pair()[0])[1])), 0.0, 1.0))
    def keyword_scales():
        _, gain_db = picture_module().peak_panel(dbm_pair()[0])
        _, plain_db = picture_module().peak_panel(dbm_pair()[1])
        drop = float(np.max(plain_db) - np.max(gain_db))
        return near(drop, 60.0, 0.5)
    expect('peak_panel: DATA_GAIN 60 lowers the peak 60 dB (+/- 0.5) from the same samples without it',
           keyword_scales)

    def picture_run():
        r, out, _ = picture('cdif-gain', [dbm_pair()[0]])
        if r.timed_out or r.code != 0:
            return False, describe(r)
        size, why = png_size(out)
        return (size is not None and size[0] >= MIN_W and size[1] >= MIN_H, why or str(size))
    expect('iq-picture of a .cdif with DATA_GAIN: exit 0, a PNG of at least 800 x 500',
           picture_run)


GROUPS = [check_measure_classes, check_measure_card_form, check_measure_options,
          check_measure_inputs, check_measure_refusals, check_card_writes,
          check_picture_pngs, check_picture_refusals, check_picture_writes_only_png,
          check_peak_panel, check_import_clean, check_inputs_unchanged, check_picture_dbm]


def main():
    global FOLDER, SIG, WORK, SIG_STATE, WORK_STATE
    parent = '/tmp/opencode' if os.path.isdir('/tmp/opencode') else None
    FOLDER = tempfile.mkdtemp(prefix='iqtools-test-', dir=parent)
    SIG = os.path.join(FOLDER, 'signals')
    WORK = fresh('work')
    try:
        try:
            build_signals()
            SIG_STATE = snapshot(SIG)
            WORK_STATE = snapshot(WORK)
        except Exception as exc:
            report('the synthetic recordings are built', False, f'{type(exc).__name__}: {exc}')
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
