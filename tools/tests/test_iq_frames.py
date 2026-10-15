"""Bursts, bits and frames: ``iqburst``, ``iqframes``, ``tools/iq-demod`` and ``tools/iq-frames``.

Everything is synthetic (numpy, seeded): FSK bursts with placeholder bytes, a transmitter
clock a few hundred ppm off, and frames built the way a scrambled, CRC-checked network
builds them. No captured data. What the checks pin:

- ``demod_fsk`` returns the transmitted bits exactly (aligned on the sync) for 50, 100, 150
  (16.7 samples per symbol) and 300 kBd, with a clock 200-500 ppm off over 1,000+ symbols,
  and chooses the symbol rate itself by the longest alternating preamble;
- ``find_bursts`` finds every burst of a wideband file at its time and frequency and nothing
  else; ``tools/iq-demod`` on that file returns the bits, writes ``--frames-out``, and refuses
  a bad option with exit 1;
- ``iqframes.search`` finds a per-frame-seed 8-bit LFSR scrambler + CRC-32 with the true seeds,
  a fixed-seed PN9 + CRC-16, and nothing in random frames; byte-identical frames count once;
  an 8-bit CRC under a free seed is not offered as a finding;
- ``align`` finds the sync from the bits the frames share, drops the noise bursts that do not
  carry it, and is byte-aligned (the preamble's last bit is not taken for the sync's first);
- ``tools/iq-frames`` end to end: a cfile of scrambled bursts -> iq-demod -> iq-frames names
  the scrambler and CRC; ``--hex`` frames; exit 1 for bad options and too few frames;
- the new modules import none of scipy, gnuradio, PyQt5.

Run:  PYTHONPATH=tools python tools/tests/test_iq_frames.py     (about two minutes)
"""

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
TOOLS = os.path.join(ROOT, 'tools')
sys.path.insert(0, TOOLS)
from fm_receiver import iqburst, iqcatalog as cat, iqframes  # noqa: E402

DEMOD = os.path.join(TOOLS, 'iq-demod')
FRAMES = os.path.join(TOOLS, 'iq-frames')
RESULTS = []
TIMEOUT = 300
CRC32 = [s for s in cat.CRCS if s.name == 'CRC-32/MPEG-2'][0]
CRC16 = [s for s in cat.CRCS if s.name == 'CRC-16/IBM-3740'][0]
CRC8 = [s for s in cat.CRCS if s.name == 'CRC-8/SMBUS'][0]
SS = [s for s in cat.SCRAMBLERS if s.name == 'ss-lfsr8'][0]
PN9 = [s for s in cat.SCRAMBLERS if s.name == 'pn9'][0]
SYNC = bytes([0xF3, 0xA0])
LENGTHS = (14, 16, 29, 29, 41, 74, 118, 118, 55, 162, 29, 33)


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"))


def run(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    check(name, ok, detail)


# ---- builders -------------------------------------------------------------------------

def bits_of(data):
    return [int(c) for c in ''.join(format(b, '08b') for b in data)]


def fsk_iq(bits, baud, fs, dev_hz, offset_hz=0.0, snr_db=25.0, ppm=0.0, seed=1, lead_s=0.002, tail_s=0.004,
           ramp_s=10e-6):
    """Constant-envelope FSK: +dev for a 1, -dev for a 0, the transmitter's clock ``ppm`` off.

    The power ramps up and down over ``ramp_s`` (raised cosine), as a transmitter's does; an
    abrupt edge would splatter over the whole band. Ten microseconds is a fast power amplifier;
    a ramp that fades the last eight data symbols (40 us at 200-300 kBd) costs the last one or
    two bits, which demod_fsk cannot recover from a signal 20 dB down."""
    rng = np.random.default_rng(seed)
    sps = fs / (baud * (1 + ppm * 1e-6))
    n = int(len(bits) * sps)
    idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
    f = (np.asarray(bits)[idx] * 2 - 1) * dev_hz + offset_hz
    x = np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)
    r = max(1, int(ramp_s * fs))
    if r > 1 and len(x) > 2 * r:
        edge = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
        x[:r] *= edge
        x[-r:] *= edge[::-1]
    y = np.concatenate([np.zeros(int(lead_s * fs)), x, np.zeros(int(tail_s * fs))]).astype(np.complex64)
    if snr_db is None:
        return y
    noise = (rng.normal(size=y.size) + 1j * rng.normal(size=y.size)) * np.sqrt(10 ** (-snr_db / 10) / 2)
    return y + noise.astype(np.complex64)


def ss_frame(rng, length, seed=None, channel=None):
    """sync | channel | 00 | length | scrambled(placeholder PSDU || CRC-32/MPEG-2), seed per frame."""
    psdu = bytes(rng.integers(0, 256, length, dtype=np.uint8))
    seed = int(rng.integers(1, 256)) if seed is None else seed
    channel = int(rng.integers(0, 128)) if channel is None else channel
    body = cat.descramble(psdu + cat.crc_bytes(cat.crc(psdu, CRC32), CRC32), SS, seed)
    return SYNC + bytes([channel, 0x00, length]) + body, seed


def frame_bits(frame_bytes, preamble=56):
    return [1, 0] * (preamble // 2) + bits_of(frame_bytes)


def sync_pos(bitstring):
    return bitstring.find(''.join(format(b, '08b') for b in SYNC))


def body_errors(got, want_bits):
    want = ''.join(map(str, want_bits))
    k, w = sync_pos(got), sync_pos(want)
    if k < 0 or w < 0:
        return None
    n = min(len(got) - k, len(want) - w)
    return sum(a != b for a, b in zip(got[k:k + n], want[w:w + n])), n


# ---- demodulation -----------------------------------------------------------------------

def t_demod_rates():
    rng = np.random.default_rng(5)
    out = []
    for baud, ppm, nbytes in ((50e3, 200, 30), (100e3, 500, 120), (150e3, -400, 60), (300e3, 300, 160)):
        frame = SYNC + bytes(rng.integers(0, 256, nbytes, dtype=np.uint8))
        bits = frame_bits(frame)
        iq = fsk_iq(bits, baud, 2.5e6, 0.25 * baud, 12e3, ppm=ppm, seed=int(baud))
        r = iqburst.demod_fsk(iq, 2.5e6)
        if r is None or r['rate_baud'] != baud:
            out.append(f'{baud:g}: rate {None if r is None else r["rate_baud"]}')
            continue
        e = body_errors(r['bits'], bits)
        if e is None or e[0] != 0 or e[1] < 8 * nbytes:
            out.append(f'{baud:g}: errors {e}')
    return not out, '; '.join(out)


def t_demod_noise():
    rng = np.random.default_rng(1)
    noise = (rng.normal(size=20000) + 1j * rng.normal(size=20000)).astype(np.complex64)
    r = iqburst.demod_fsk(noise, 2.5e6)
    return r is None or not r['bits'] or r['preamble_bits'] == 0, f'noise gave {r and r["preamble_bits"]} preamble bits'


# ---- a wideband file ----------------------------------------------------------------------

def write_cfile(path, iq, rate, centre):
    iq.astype(np.complex64).tofile(path)
    meta = {'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate, 'core:version': '1.0.0'},
            'captures': [{'core:sample_index': 0, 'core:frequency': centre}], 'annotations': []}
    with open(path[:-len('.cfile')] + '.sigmf-meta', 'w') as fh:
        json.dump(meta, fh)


def wide_file(folder, n_frames=12, seed=7, rate=2.5e6, centre=915e6, snr_db=26):
    """A recording of ``n_frames`` scrambled frames, each in its own burst at its own offset."""
    rng = np.random.default_rng(seed)
    parts, truth = [], []
    t = 0.0
    seeds = []
    frames = []
    gap = np.zeros(int(0.006 * rate), dtype=np.complex64)
    parts.append(gap)
    t += len(gap) / rate
    for k in range(n_frames):
        frame, sd = ss_frame(rng, LENGTHS[k % len(LENGTHS)], channel=k)
        frames.append(frame)
        seeds.append(sd)
        baud = (100e3, 200e3, 150e3, 300e3)[k % 4]
        bits = frame_bits(frame)
        off = float(rng.uniform(-700e3, 700e3))
        iq = fsk_iq(bits, baud, rate, 0.25 * baud, off, snr_db=None, ppm=float(rng.uniform(-300, 300)),
                    seed=k, lead_s=0.0, tail_s=0.0)
        truth.append(dict(t0=t, t1=t + len(iq) / rate, centre=centre + off, bits=bits, baud=baud))
        parts.append(iq)
        gap = np.zeros(int(0.006 * rate), dtype=np.complex64)
        parts.append(gap)
        t += len(iq) / rate + len(gap) / rate
    iq = np.concatenate(parts)
    # steady noise over the whole file, as a receiver has: burst amplitude 1, snr_db over its power
    rngn = np.random.default_rng(99)
    sigma = 10 ** (-snr_db / 20) / np.sqrt(2)
    iq = iq + ((rngn.normal(size=iq.size) + 1j * rngn.normal(size=iq.size)) * sigma).astype(np.complex64)
    path = os.path.join(folder, 'wide.cfile')
    write_cfile(path, iq, rate, centre)
    return path, truth, frames, seeds


def t_find_bursts(ctx):
    from fm_receiver import iqmeasure
    path, truth, _, _ = ctx['wide']
    found = iqburst.find_bursts(iqmeasure.open_iq(path))
    if len(found) != len(truth):
        return False, f'{len(found)} bursts found, {len(truth)} sent'
    bad = []
    for f, tr in zip(found, truth):
        if abs(f['t0'] - tr['t0']) > 1.2e-3 or abs(f['centre_hz'] - tr['centre']) > 100e3:
            bad.append((round(f['t0'], 4), round(tr['t0'], 4), round(f['centre_hz'] - tr['centre'])))
    return not bad, f'off by: {bad[:3]}'


def t_iq_demod(ctx):
    path, truth, frames, _ = ctx['wide']
    out = os.path.join(ctx['folder'], 'frames.json')
    p = subprocess.run([sys.executable, DEMOD, path, '--frames-out', out], capture_output=True, text=True,
                       timeout=TIMEOUT)
    if p.returncode != 0:
        return False, f'exit {p.returncode}: {p.stderr[-200:]}'
    doc = json.loads(p.stdout)
    b = doc['bursts']
    if len(b) != len(truth):
        return False, f'{len(b)} bursts, {len(truth)} sent'
    bad = []
    for item, tr in zip(b, truth):
        if item.get('rate_baud') != tr['baud']:
            bad.append(f'rate {item.get("rate_baud")} want {tr["baud"]:g}')
            continue
        e = body_errors(item['bits'], tr['bits'])
        if e is None or e[0] != 0:
            bad.append(f'errors {e}')
    fr = json.load(open(out))
    ok = not bad and len(fr['frames']) == len(truth)
    return ok, f'{bad[:3]}; frames-out {len(fr["frames"])}'


def t_iq_demod_refuses():
    bad = []
    cases = [([DEMOD, '/nonexistent.cfile'], 'missing file'),
             ([DEMOD, '--t0', '2', '--t1', '1', 'x.cfile'], 't0 after t1'),
             ([DEMOD, '--rates', 'fast', 'x.cfile'], 'bad rates'),
             ([DEMOD, '--band', '5', '1', 'x.cfile'], 'inverted band')]
    for cmd, label in cases:
        p = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 1 or p.stdout.strip():
            bad.append(f'{label}: exit {p.returncode}, stdout {p.stdout[:30]!r}')
    return not bad, '; '.join(bad)


# ---- the search -------------------------------------------------------------------------

def ss_set(seed, lengths=LENGTHS):
    rng = np.random.default_rng(seed)
    pairs = [ss_frame(rng, L) for L in lengths]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def t_search_ss():
    frames, seeds = ss_set(11)
    t = time.time()
    r = iqframes.search(frames, length_fields=iqframes.structure(frames)['length_fields'])
    el = time.time() - t
    hits = r['hits']
    top = hits[0] if hits else None
    ok = (top and top['scrambler'] == 'ss-lfsr8' and top['crc'] == 'CRC-32/MPEG-2' and top['scr_start'] == 5
          and top['crc_start'] == 5 and top['frames_matched'] == len(frames) and top['seeds'] == seeds
          and top['p_adjusted'] < 1e-50 and len(hits) == 1 and el < 120)
    return bool(ok), f'{len(hits)} hits, top {top and (top["scrambler"], top["crc"], top["frames_matched"], top["seeds"] == seeds)}, {el:.0f} s'


def t_search_pn9():
    rng = np.random.default_rng(12)
    frames = []
    for L in (12, 20, 20, 33, 33, 47, 60, 80):
        pay = bytes(rng.integers(0, 256, L, dtype=np.uint8))
        frames.append(bytes([0x2D, 0xD4, L]) + cat.descramble(pay + cat.crc_bytes(cat.crc(pay, CRC16), CRC16), PN9, 0x1FF))
    r = iqframes.search(frames)
    top = r['hits'][0] if r['hits'] else None
    ok = (top and top['scrambler'] == 'pn9' and top['crc'] == 'CRC-16/IBM-3740' and top['crc_start'] == 3
          and top['frames_matched'] == len(frames))
    return bool(ok), f'top {top and (top["scrambler"], top["crc"], top["crc_start"], top["frames_matched"])}'


def t_search_none():
    rng = np.random.default_rng(13)
    frames = [bytes(rng.integers(0, 256, L, dtype=np.uint8)) for L in LENGTHS]
    r = iqframes.search(frames, length_fields=iqframes.structure(frames)['length_fields'])
    return not r['hits'], f'{len(r["hits"])} accepted hits in random frames'


def t_search_duplicates():
    frames, _ = ss_set(14, lengths=(29, 41))
    many = [frames[0]] * 10 + [frames[1]]
    r = iqframes.search(many, min_hits=3)
    return not r['hits'] and r['unique_frames'] == 2, f'{len(r["hits"])} hits, {r["unique_frames"]} unique'


def t_search_crc8_free_seed():
    rng = np.random.default_rng(15)
    frames = []
    for L in LENGTHS:
        pay = bytes(rng.integers(0, 256, L, dtype=np.uint8))
        frames.append(SYNC + bytes([3, 0, L]) + cat.descramble(pay + cat.crc_bytes(cat.crc(pay, CRC8), CRC8), SS,
                                                               int(rng.integers(1, 256))))
    r = iqframes.search(frames, length_fields=iqframes.structure(frames)['length_fields'])
    wrong = [h for h in r['hits'] if h['crc'] == CRC8.name and h['scrambler'] == 'ss-lfsr8']
    return not wrong, 'an 8-bit CRC under a free seed was reported (chance matches on every frame)'


def t_structure():
    frames, _ = ss_set(16)
    margin = [f + b'\x00\x00' for f in frames]               # a read runs past the frame's end
    st = iqframes.structure(margin)
    const = {p['pos']: p['value'] for p in st['positions'] if p['constant']}
    ok = const.get(0) == 0xF3 and const.get(1) == 0xA0 and const.get(3) == 0x00
    lf = [fl['pos'] for fl in st['length_fields']]
    return ok and 4 in lf, f'constants {const}, length fields {lf}'


# ---- alignment ----------------------------------------------------------------------------

def t_align():
    frames, _ = ss_set(17)
    rng = np.random.default_rng(18)
    good = [''.join(map(str, frame_bits(f, preamble=48 + 2 * (i % 5)))) for i, f in enumerate(frames)]
    junk = [''.join(map(str, [1, 0] * 20 + list(rng.integers(0, 2, 200)))) for _ in range(4)]
    allf = good + junk
    starts, sync, keep = iqframes.align(allf)
    byts = iqframes._pack(sync, False, False)
    kept_good = [i for i in keep if i < len(good)]
    ok = (byts[:2] == SYNC and len(kept_good) == len(good) and all(i < len(good) for i in keep))
    # byte alignment: the frame bytes from the chosen start begin with the sync
    fb = iqframes.frames_as_bytes([allf[i] for i in keep], [starts[i] for i in keep], False, False)
    ok = ok and all(b[:2] == SYNC for b in fb)
    return ok, f'sync {byts.hex()!r}, kept {len(keep)} of {len(allf)}'


def t_hints():
    rng = np.random.default_rng(19)
    # a body of zeros, scrambled: the keystream itself, an LFSR sequence
    ks = cat.keystream(SS, 77, 24)
    bit_frame = ''.join(map(str, [1, 0] * 8)) + ''.join(format(b, '08b') for b in ks)
    noise = ''.join(map(str, [1, 0] * 8)) + ''.join(map(str, rng.integers(0, 2, 192)))
    h1 = iqframes.lfsr_hints([bit_frame], [16], window=96, step=32)
    h2 = iqframes.lfsr_hints([noise], [16], window=96, step=32)
    lc = iqframes.linear_complexity([int(c) for c in format(int.from_bytes(ks, 'big'), '0192b')][:96])
    return bool(h1) and not h2 and lc <= 8, f'scrambled zeros {len(h1)} windows, noise {len(h2)}, complexity {lc}'


# ---- the tools end to end ----------------------------------------------------------------

def t_chain(ctx):
    path, _, frames, seeds = ctx['wide']
    out = os.path.join(ctx['folder'], 'chain_frames.json')
    p = subprocess.run([sys.executable, DEMOD, path, '--no-bits', '--frames-out', out],
                       capture_output=True, text=True, timeout=TIMEOUT)
    if p.returncode != 0:
        return False, f'iq-demod exit {p.returncode}'
    t = time.time()
    q = subprocess.run([sys.executable, FRAMES, out, '--variants', 'plain', '--no-lfsr'],
                       capture_output=True, text=True, timeout=TIMEOUT)
    if q.returncode != 0:
        return False, f'iq-frames exit {q.returncode}: {q.stderr[-200:]}'
    doc = json.loads(q.stdout)
    v = doc['variants'][0]
    hits = v['search']['hits']
    top = hits[0] if hits else None
    ok = (v.get('sync_hex', '').startswith('f3a0') and top and top['scrambler'] == 'ss-lfsr8'
          and top['crc'] == 'CRC-32/MPEG-2' and top['frames_matched'] >= len(frames) - 2)
    return bool(ok), f'sync {v.get("sync_hex")}, top {top and (top["scrambler"], top["crc"], top["frames_matched"])}, {time.time() - t:.0f} s'


def t_iq_frames_hex(ctx):
    rng = np.random.default_rng(20)
    lines = []
    for L in (12, 20, 20, 33, 33, 47, 60, 80):
        pay = bytes(rng.integers(0, 256, L, dtype=np.uint8))
        lines.append((bytes([0x2D, 0xD4, L]) + cat.descramble(pay + cat.crc_bytes(cat.crc(pay, CRC16), CRC16), PN9, 0x1FF)).hex())
    path = os.path.join(ctx['folder'], 'frames.hex')
    open(path, 'w').write('\n'.join(lines) + '\n')
    q = subprocess.run([sys.executable, FRAMES, '--hex', path, '--variants', 'plain', '--no-lfsr'],
                       capture_output=True, text=True, timeout=TIMEOUT)
    if q.returncode != 0:
        return False, f'exit {q.returncode}: {q.stderr[-200:]}'
    hits = json.loads(q.stdout)['variants'][0]['search']['hits']
    return bool(hits) and hits[0]['scrambler'] == 'pn9' and hits[0]['crc'] == 'CRC-16/IBM-3740', str(hits[:1])


def t_iq_frames_refuses(ctx):
    bad = []
    one = os.path.join(ctx['folder'], 'one.json')
    json.dump({'frames': [{'bits': '10' * 40}]}, open(one, 'w'))
    junk = os.path.join(ctx['folder'], 'junk.json')
    open(junk, 'w').write('{"nothing": 1}')
    for cmd, label in (([FRAMES, '/nonexistent.json'], 'missing file'), ([FRAMES, one], 'too few frames'),
                       ([FRAMES, junk], 'not a frames file'), ([FRAMES, one, '--variants', 'sideways'], 'bad variant')):
        p = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 1 or p.stdout.strip():
            bad.append(f'{label}: exit {p.returncode}')
    return not bad, '; '.join(bad)


def t_imports():
    code = ("import sys; sys.path.insert(0, %r); "
            "from fm_receiver import iqburst, iqframes, iqcatalog; "
            "bad=[m for m in ('scipy','gnuradio','PyQt5','library','radios','matplotlib') if m in sys.modules]; "
            "print(bad)") % TOOLS
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60,
                       env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'})
    return p.stdout.strip() == '[]', p.stdout.strip() + p.stderr[-100:]


def main():
    folder = tempfile.mkdtemp(prefix='iqframes-test-')
    ctx = {'folder': folder}
    try:
        ctx['wide'] = wide_file(folder)
        print('demodulation')
        run('FSK bursts demodulate bit-exact at 50, 100, 150, 300 kBd with the clock off', t_demod_rates)
        run('noise yields no preamble', t_demod_noise)
        print('bursts in a wideband file')
        run('find_bursts: every burst, at its time and frequency, nothing else', lambda: t_find_bursts(ctx))
        run('iq-demod: rate and bits of every burst, --frames-out', lambda: t_iq_demod(ctx))
        run('iq-demod refuses bad options with exit 1 and nothing on stdout', t_iq_demod_refuses)
        print('structure and alignment')
        run('structure: constants and the length byte survive a read margin', t_structure)
        run('align: sync from the common bits, noise bursts dropped, byte-aligned', t_align)
        run('lfsr_hints: scrambled zeros show low linear complexity, noise does not', t_hints)
        print('the catalog search')
        run('per-frame seed LFSR + CRC-32: one hit, true seeds, p < 1e-50', t_search_ss)
        run('fixed-seed PN9 + CRC-16 found', t_search_pn9)
        run('random frames: no accepted hit', t_search_none)
        run('byte-identical frames count once', t_search_duplicates)
        run('an 8-bit CRC under a free seed is not offered', t_search_crc8_free_seed)
        print('the tools')
        run('cfile -> iq-demod -> iq-frames names the scrambler and CRC', lambda: t_chain(ctx))
        run('iq-frames --hex', lambda: t_iq_frames_hex(ctx))
        run('iq-frames refuses bad input with exit 1', lambda: t_iq_frames_refuses(ctx))
        run('the modules import no scipy, gnuradio, Qt', t_imports)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    failed = [n for n, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed')
    if failed:
        print('failed:', *failed, sep='\n  ')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
