"""rtl_433 on the app's samples, with no radio: a synthetic OOK remote in
an IQ recording, played through the real :class:`Engine` into rtl_433.

- :func:`rtl433.plan` puts each radio's LO clear of the band passed on,
  at its lowest rate that holds it;
- the pipe to rtl_433 never blocks the flowgraph, drops only whole blocks
  of samples (so what arrives stays aligned), and shrugs off rtl_433
  going away;
- the samples are raised until the noise is an RTL-SDR's, never past full
  scale and never turned down;
- a burst 300 kHz off an IQ recording's centre is decoded, by a flex
  decoder given for it (``-X``), with its level; logged as JSON lines -
  recorded at the BB60D's level, 60 dB under an RTL-SDR's, which rtl_433
  given the samples as they are does not decode;
- the whole band, in slices: each radio's plan, and two remotes in one
  recording both decoded, the one on a slice boundary heard by both slices
  and kept once;
- Receive's band too (its rtl_433 card), at a rate wider than twelve
  slices: the middle cut down before the channelizer, what is in it
  decoded and what is outside not, levels in the radio's dBFS, and a moved
  LO putting right the frequencies reported;
- the same burst from two slices kept once, with the stronger one's level;
  an on-off burst put at its slice's centre;
- closing the chain ends rtl_433;
- rtl_433 is given the frequency before the rate (after it, over 800 MHz,
  the rate goes back to rtl_433's own and nothing decodes);
- with ``FMRX_RTL433_TESTS`` pointing at a checkout of rtl_433's own test
  recordings (github.com/merbanan/rtl_433_tests), a real FSK sensor -
  a Bresser 5-in-1, 150 kHz wide - decoded in the whole band wherever it
  sits: mid-slice, on a boundary, off a centre.

Needs rtl_433 on the PATH; says so and passes if it is not.

Run:  python tools/tests/test_rtl433.py     (about 10 s)
"""

import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import rtl433  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import BB60, HackRF, IQFile, RTLSDR  # noqa: E402

RATE = 2e6
CENTER = 433.62e6
FREQ = 433.92e6
BITS = 'a5c3f0'
#: Short pulse a 1, long a 0, every bit 1400 us; rows 4 ms apart.
FLEX = ('n=fmrx_test,m=OOK_PWM,s=400,l=1000,r=6000,g=2500,t=150,'
        'bits=24,repeats>=3')


def ook_burst(rate, offset_hz, bits_hex, repeats=5, snr_db=30.0, seed=2, amplitude=0.3):
    """The IQ of an OOK PWM remote: ``repeats`` rows of ``bits_hex``,
    in silence either side, ``offset_hz`` off centre."""
    us = rate / 1e6
    bits = bin(int(bits_hex, 16))[2:].zfill(len(bits_hex) * 4)
    env = [np.zeros(int(200e3 * us))]
    for _ in range(repeats):
        for b in bits:
            on = 400 if b == '1' else 1000
            env += [np.ones(int(on * us)), np.zeros(int((1400 - on) * us))]
        env.append(np.zeros(int(4000 * us)))
    env.append(np.zeros(int(300e3 * us)))
    env = np.concatenate(env)
    t = np.arange(len(env)) / rate
    iq = amplitude * env * np.exp(2j * np.pi * offset_hz * t)
    rng = np.random.default_rng(seed)
    sigma = amplitude / 10 ** (snr_db / 20) / np.sqrt(2)
    iq = iq + sigma * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    return iq.astype(np.complex64)


def plans():
    # HackRF: its DC spike 225 kHz below 433.92 at 2 MS/s; 1 MHz needs 4.
    rate, lo, offset = rtl433.plan(HackRF(), FREQ, 250e3)
    assert (rate, offset) == (2e6, 225e3) and abs(lo + offset - FREQ) < 1, (rate, lo, offset)
    rate, lo, offset = rtl433.plan(HackRF(), FREQ, 1e6)
    assert (rate, offset) == (4e6, 600e3), (rate, offset)
    # An RTL-SDR's 2.4 MS/s cannot hold 1 MHz clear of its LO: as far off as fits.
    rate, lo, offset = rtl433.plan(RTLSDR(), FREQ, 1e6)
    assert rate == 2.4e6 and 0 < offset <= 460e3 + 1, (rate, offset)
    assert rtl433.decimation(2.4e6, 250e3) == 9 and rtl433.decimation(2e6, 250e3) == 8
    # The BB60D has no spike: just the half width, at its lowest usable rate.
    rate, lo, offset = rtl433.plan(BB60(), 868.3e6, 250e3)
    assert offset == 125e3 and rate == min(BB60.usable_receive_rates()), (rate, offset)
    # The whole band: the LO on the frequency, on a boundary between slices.
    rate, lo, m, w, centres = rtl433.band_plan(RTLSDR(), FREQ)
    assert (rate, m, w, lo) == (2.4e6, 10, 240e3, FREQ), (rate, m, w, lo)
    assert len(centres) == 8 and centres[3] == -120e3 and centres[4] == 120e3, centres
    assert len(centres) * w >= 1.74e6                     # the 433 ISM band, all of it
    rate, lo, m, w, centres = rtl433.band_plan(HackRF(), FREQ)
    assert (rate, len(centres)) == (4e6, 12), (rate, len(centres))
    rate, lo, m, w, centres = rtl433.band_plan(BB60(), FREQ)
    assert len(centres) <= rtl433.MAX_SLICES and rate / m == w
    try:
        rtl433.band_plan(RTLSDR(), 2400e6)
    except ValueError:
        pass
    else:
        raise AssertionError("2.4 GHz accepted for an RTL-SDR's whole band")
    try:
        rtl433.plan(RTLSDR(), 2400e6, 250e3)
    except ValueError:
        pass
    else:
        raise AssertionError("2.4 GHz accepted for an RTL-SDR")
    # Receive's band, at the rate picked there: its middle, cut down to it
    # before the channelizer when much wider.
    m, w, centres = rtl433.band_slices(BB60(), 10e6)
    assert (m, len(centres)) == (40, 12) and rtl433.predecimation(m, 6 + 2) == 2
    assert rtl433.predecimation(160, 8) == 10 and rtl433.predecimation(16, 8) == 1
    m, w, centres = rtl433.band_slices(RTLSDR(), 2.4e6)
    assert (m, len(centres)) == (10, 8) and rtl433.predecimation(m, 4 + 2) == 1
    args = rtl433.command('rtl_433', 250e3, 868.3e6)
    assert args.index('-f') < args.index('-s'), args
    print("plan: LO clear of the band for each radio; the whole band 1.92 MHz in 8 "
          "slices on an RTL-SDR, 3 MHz in 12 on a HackRF; out of range refused")


def pipe():
    sink = rtl433.Pipe()
    r, w = os.pipe()
    os.set_blocking(w, False)
    sink.set_fd(w)
    # Odd, not a page; every sample its own phase, under full scale.
    block = (0.5 * np.exp(2j * np.pi * np.arange(8191) / 8191)).astype(np.complex64)
    t0 = time.monotonic()
    for _ in range(200):
        sink.feed(block)
    assert time.monotonic() - t0 < 2.0, "the pipe blocked"
    assert sink.dropped > 0 and sink.dropped % len(block) == 0, sink.dropped
    # rtl_433 catches up: what arrives is every block sent, whole.
    os.set_blocking(r, False)
    got = bytearray()
    while True:
        try:
            got += os.read(r, 1 << 20)
            continue
        except BlockingIOError:
            pass
        with sink._lock:
            sink._flush()
            if not sink._pending:
                break
    try:
        got += os.read(r, 1 << 20)
    except BlockingIOError:
        pass
    assert len(got) == sink.sent * 8, (len(got), sink.sent * 8)
    # Levelled, but each sample's phase kept: a block out of step would
    # show as the wrong phases.
    x = np.frombuffer(bytes(got), np.complex64).reshape(-1, len(block))
    assert np.allclose(np.angle(x), np.angle(block), atol=1e-5), "out of step"
    os.close(r)
    sink.feed(block)
    sink.feed(block)
    assert sink.broken, "a closed reader not noticed"
    os.close(w)
    print(f"pipe: {sink.sent} samples sent, {sink.dropped} dropped in whole blocks, "
          "none blocked; a closed reader noticed")


class _Said:
    """An rtl_433 that has said ``said``: [(heard, message)]."""

    def __init__(self, said):
        self.said = list(said)
        self.decoded = len(self.said)

    def take(self):
        said, self.said = self.said, []
        return said


def duplicates():
    """The same burst from two slices is kept once, and with the stronger
    slice's level and frequency, whichever said so first - in place, as
    the window may already show it."""
    chain = object.__new__(rtl433.DecodeChain)
    now = time.time()
    pipes = [rtl433.Pipe(), rtl433.Pipe()]
    pipes[0].history.append((now - 0.2, 2.0, 1e-4, 0.45))     # heard it 6 dB down
    pipes[1].history.append((now - 0.2, 1.0, 1e-4, 0.9))
    chain.slices = [(433.745e6, pipes[0]), (433.995e6, pipes[1])]
    chain._moves, chain._recent, chain._log, chain.duplicates = [(0.0, 0.0)], {}, None, 0
    msg = {'model': 'X', 'id': 1, 'mod': 'FSK', 'freq': 433.8, 'rssi': -30.0}
    chain.procs = [_Said([(now, dict(msg))]), _Said([])]
    first = chain.take()
    assert len(first) == 1 and first[0][1]['level_dbfs'] == -36.0, first
    chain.procs[1].said = [(now + 0.01, dict(msg, freq=433.95))]
    assert chain.take() == [] and chain.duplicates == 1
    kept = first[0][1]
    assert kept['level_dbfs'] == -30.0 and kept['freq'] == 433.95, kept
    # An on-off burst is put at its slice's centre.
    chain.procs[0].said = [(now + 2.0, dict(msg, id=2, mod='ASK', freq=433.9))]
    assert chain.take()[0][1]['freq'] == 433.745
    print("duplicates: kept once, with the stronger slice's level; an on-off "
          "burst at its slice's centre")


def levels():
    rng = np.random.default_rng(3)
    for sigma in (1e-5, 1e-3):                   # a BB60D's noise, then a HackRF's
        sink = rtl433.Pipe()
        for _ in range(40):
            x = (sigma * (rng.standard_normal(4096) + 1j * rng.standard_normal(4096))
                 ).astype(np.complex64)
            y = sink.level(x)
        rms = np.sqrt(np.mean(np.abs(y) ** 2))
        assert abs(20 * np.log10(rms / rtl433.NOISE_LEVEL)) < 1.0, (sigma, rms)
    burst = np.full(4096, 0.5 + 0.5j, np.complex64)   # far over the noise: at full scale
    y = sink.level(burst).view(np.float32)
    assert np.abs(y).max() <= 1.0 and y.min() >= -1.0
    loud = rtl433.Pipe()
    for _ in range(3):
        x = (0.3 * (rng.standard_normal(4096) + 1j * rng.standard_normal(4096))
             ).astype(np.complex64)
        loud.level(x)
    assert loud.gain == 1.0, loud.gain            # never turned down
    # A long burst in a channelizer's small blocks: a second of noise, then
    # 400 ms of a carrier 40 dB over it, 512 samples a block (500 kS/s).
    # The noise is still the noise, and the burst raised as far as fits.
    sink = rtl433.Pipe()
    sigma, tone = 1e-4, 1e-2
    for n in range(1000 + 400):
        x = (sigma * (rng.standard_normal(512) + 1j * rng.standard_normal(512))
             ).astype(np.complex64)
        if n >= 1000:
            x += np.complex64(tone)
        sink.level(x)
    assert abs(20 * np.log10(sink.noise / (sigma * np.sqrt(2)))) < 1.0, sink.noise
    assert sink.applied > 0.8 * rtl433.PEAK / (tone + 4 * sigma), sink.applied
    print("levels: noise raised to an RTL-SDR's from 1e-5 and 1e-3, bursts held "
          "at full scale, loud noise left alone, a 400 ms burst in small blocks not "
          "taken for noise")


def decode(program, folder):
    base = os.path.join(folder, 'remote')
    # At the BB60D's level: the noise about 1e-5 of full scale.
    ook_burst(RATE, FREQ - CENTER, BITS, amplitude=3e-4).tofile(base + '.cfile')
    with open(base + '.json', 'w') as fh:
        json.dump({'rate': RATE, 'center_hz': CENTER, 'offset_hz': FREQ - CENTER,
                   'station_hz': FREQ}, fh)
    engine = Engine(want_audio=False)
    engine.use_radio(IQFile(base + '.cfile'))
    try:
        engine.start_decode(FREQ, 250e3, program, extra_args=('-R', '0', '-X', FLEX))
        chain = engine.decoder
        assert abs(engine.station_hz - FREQ) < 1 and chain.out_rate == 250e3
        log = os.path.join(folder, 'decoded.jsonl')
        chain.set_log(log)
        got = []
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not got:
            time.sleep(0.2)
            got = [m for _, m in chain.take() if m.get('model') == 'fmrx_test']
        assert got, f"nothing decoded; rtl_433 said: {list(chain.proc.messages)}"
        msg = got[0]
        assert msg['rows'][0]['data'] == BITS, msg
        assert 'rssi' in msg and 'snr' in msg, msg
        assert chain.problem() is None
        assert rtl433.device_key(msg)[0] == 'fmrx_test'
        proc = chain.proc.proc
        gain = chain.gain
    finally:
        engine.close()
    assert proc.poll() is not None, "rtl_433 still running after close"
    lines = [json.loads(l) for l in open(log)]
    assert lines and lines[0]['model'] == 'fmrx_test' and 'heard' in lines[0]
    print(f"decode: {BITS} at the BB60D's level read back by rtl_433 "
          f"{rtl433.program_version(program)} (SNR {msg['snr']:.0f} dB, gain "
          f"{20 * np.log10(gain):.0f} dB), logged; rtl_433 ended on close")


def decode_band(program, folder):
    """Two remotes, one after the other: A 300 kHz over the centre, in the
    middle of a slice; B at 250 kHz, on the boundary between two."""
    a_hz, b_hz, b_bits = FREQ, CENTER + 250e3, 'c3a55a'
    burst_a = ook_burst(RATE, a_hz - CENTER, BITS, amplitude=0.1)
    burst_b = ook_burst(RATE, b_hz - CENTER, b_bits, amplitude=0.1, seed=4)
    base = os.path.join(folder, 'two')
    np.concatenate([burst_a, burst_b]).tofile(base + '.cfile')
    with open(base + '.json', 'w') as fh:
        json.dump({'rate': RATE, 'center_hz': CENTER, 'offset_hz': 0.0,
                   'station_hz': CENTER}, fh)
    loop_s = 2 * len(burst_a) / RATE
    engine = Engine(want_audio=False)
    engine.use_radio(IQFile(base + '.cfile'))
    try:
        engine.start_decode(FREQ, rtl433.WHOLE_BAND, program,
                            extra_args=('-R', '0', '-X', FLEX))
        chain = engine.decoder
        n = len(chain.slices)
        assert n == 6 and len(chain.procs) == n, (n, len(chain.procs))
        assert abs(chain.low_hz - (CENTER - 750e3)) < 1 and abs(chain.high_hz - (CENTER + 750e3)) < 1
        got = []
        deadline = time.monotonic() + 3 * loop_s + 4.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
            got += chain.take()
            codes = {m['rows'][0]['data'] for _, m in got if m.get('model') == 'fmrx_test'}
            if {BITS, b_bits} <= codes and chain.duplicates:
                break
        assert {BITS, b_bits} <= codes, (codes, [list(p.messages)[-2:] for p in chain.procs])
        assert chain.duplicates > 0, "the boundary burst was not heard by two slices"
        heard_b = sorted(h for h, m in got if m.get('rows', [{}])[0].get('data') == b_bits)
        assert all(b - a >= rtl433.DUP_S for a, b in zip(heard_b, heard_b[1:])), heard_b
        # An on-off burst is put at the centre of the slice that heard it:
        # rtl_433's own frequency for one is not where it is. And the slices
        # overlap, so the one that heard it first may be the next one over.
        freqs = {m['rows'][0]['data']: m['freq'] for _, m in got if m.get('model') == 'fmrx_test'}
        near = (0.8 * chain.width + 20e3) / 1e6
        assert abs(freqs[BITS] - a_hz / 1e6) < near and abs(freqs[b_bits] - b_hz / 1e6) < near, freqs
        procs = [p.proc for p in chain.procs]
    finally:
        engine.close()
    assert all(p.poll() is not None for p in procs), "an rtl_433 still running after close"
    print(f"whole band: {n} slices from {chain.low_hz / 1e6:.3f} to {chain.high_hz / 1e6:.3f} "
          f"MHz; A at {freqs[BITS]:.3f} and B on a boundary at {freqs[b_bits]:.3f} decoded, "
          f"B's second copy dropped {chain.duplicates} times; all {n} rtl_433 ended on close")


def decode_receive(program, folder):
    """Receive's rtl_433 at 8 MS/s: the middle 3 MHz, cut down by 2 before
    the channelizer. Two remotes in it decoded, one outside it not; the
    level in the radio's dBFS; moving the LO puts right what is reported."""
    rate = 8e6
    trim = slice(int(0.18 * rate), None)       # 300 ms quiet after: the noise

    bursts = {'a5c3f0': (300e3, 0.1), 'c3a55a': (-600e3, 0.03), '5a0ff0': (2.5e6, 0.1)}
    iq = sum(ook_burst(rate, off, bits, repeats=3, amplitude=amp, seed=i)[trim]
             for i, (bits, (off, amp)) in enumerate(bursts.items()))
    base = os.path.join(folder, 'wide')
    iq.astype(np.complex64).tofile(base + '.cfile')
    with open(base + '.json', 'w') as fh:
        json.dump({'rate': rate, 'center_hz': CENTER, 'offset_hz': 100e3,
                   'station_hz': CENTER + 100e3}, fh)
    engine = Engine(want_audio=False)
    engine.use_radio(IQFile(base + '.cfile'))
    try:
        engine.start_receive(CENTER + 100e3, rate, center_hz=CENTER,
                             decode=(program, ('-R', '0', '-X', FLEX)))
        chain = engine.decoder
        assert engine.rx is not None and engine.decode_error is None, engine.decode_error
        assert len(chain.slices) == 12 and chain.predecim == 2, (len(chain.slices), chain.predecim)
        got = {}
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and (len(got) < 2 or not chain.duplicates):
            time.sleep(0.2)
            for _, m in chain.take():
                if m.get('model') == 'fmrx_test':
                    got.setdefault(m['rows'][0]['data'], m)
        assert set(got) == {'a5c3f0', 'c3a55a'}, (set(got), chain.problem())
        # B, 25 kHz from a slice's centre, is heard 6 dB down by the next
        # one too, which may be the only one to decode a loop of the
        # recording: the strongest of a few seconds is B's level.
        best = {bits: [m] for bits, m in got.items()}
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
            for _, m in chain.take():
                if m.get('model') == 'fmrx_test':
                    best.setdefault(m['rows'][0]['data'], []).append(m)
        for bits, msgs in best.items():
            want = 20 * np.log10(bursts[bits][1])
            msg = max(msgs, key=lambda m: m['level_dbfs'])
            assert abs(msg['level_dbfs'] - want) < 1.5, (bits, msg['level_dbfs'], want)
            assert msg['level_dbfs'] - msg['floor_dbfs'] > 20, msg
        a_mhz = got['a5c3f0']['freq']
        chain.move(CENTER + 1e6)
        moved = None
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and moved is None:
            time.sleep(0.2)
            moved = next((m['freq'] for _, m in chain.take()
                          if m.get('rows', [{}])[0].get('data') == 'a5c3f0'), None)
        # Either of the two slices A is in may say so first.
        assert moved is not None and abs(moved - a_mhz - 1.0) <= chain.width / 1e6 + 1e-6, \
            (a_mhz, moved)
        procs = [p.proc for p in chain.procs]
    finally:
        engine.close()
    assert all(p.poll() is not None for p in procs), "an rtl_433 still running after close"
    level = max(m['level_dbfs'] for m in best['a5c3f0'])
    print(f"Receive at 8 MS/s: 12 slices, cut down by 2 first; A at "
          f"{level:.1f} dBFS (-20 put in) and B decoded, the one "
          "outside not; a moved LO moves what is reported")


def bresser(program):
    """A real Bresser 5-in-1 (FSK, 868.3 MHz, 250 kS/s) from rtl_433's test
    recordings, put at 2 MS/s in the middle of a slice, on a boundary and
    75 kHz off a centre: decoded in the whole band each time."""
    root = os.environ.get('FMRX_RTL433_TESTS')
    path = os.path.join(root or '', 'tests', 'bresser_5in1', '01', 'g001_868.3M_250k.cu8')
    if not root or not os.path.exists(path):
        print("Bresser 5-in-1: skipped (set FMRX_RTL433_TESTS to a checkout of "
              "rtl_433_tests to run it)")
        return
    from scipy import signal as sps
    raw = np.fromfile(path, np.uint8).astype(np.float32)
    x = ((raw[0::2] - 127.4) + 1j * (raw[1::2] - 127.4)) / 128.0
    up = sps.resample_poly(x, 8, 1).astype(np.complex64)
    centre = 868.0e6
    folder = tempfile.mkdtemp(prefix='fmrx-bresser-')
    try:
        for where in (868.375e6, 868.25e6, 868.30e6):
            t = np.arange(len(up)) / RATE
            pad = np.zeros(int(0.3 * RATE), np.complex64)
            y = np.concatenate([pad, up * np.exp(2j * np.pi * (where - centre) * t), pad])
            rng = np.random.default_rng(1)
            y = y + 0.002 * (rng.standard_normal(len(y)) + 1j * rng.standard_normal(len(y)))
            base = os.path.join(folder, 'b')
            y.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.json', 'w') as fh:
                json.dump({'rate': RATE, 'center_hz': centre, 'offset_hz': 0.0,
                           'station_hz': centre}, fh)
            engine = Engine(want_audio=False)
            engine.use_radio(IQFile(base + '.cfile'))
            try:
                engine.start_decode(centre, rtl433.WHOLE_BAND, program)
                got = []
                t0 = time.monotonic()
                while time.monotonic() - t0 < 4 and not got:
                    time.sleep(0.2)
                    got = [m for _, m in engine.decoder.take() if m.get('model') == 'Bresser-5in1']
                assert got and got[0]['temperature_C'] == 8.0, (where, got)
            finally:
                engine.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("Bresser 5-in-1 (a real FSK recording): decoded mid-slice, on a boundary "
          "and 75 kHz off a centre")


def main():
    plans()
    duplicates()
    levels()
    pipe()
    program = rtl433.find_program()
    if program is None:
        print(f"rtl_433 not found ({rtl433.install_hint()}): decode not tested")
        return 0
    folder = tempfile.mkdtemp(prefix='fmrx-433-')
    try:
        decode(program, folder)
        decode_band(program, folder)
        decode_receive(program, folder)
        bresser(program)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
