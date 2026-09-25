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
- closing the chain ends rtl_433.

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
    try:
        rtl433.plan(RTLSDR(), 2400e6, 250e3)
    except ValueError:
        pass
    else:
        raise AssertionError("2.4 GHz accepted for an RTL-SDR")
    print("plan: LO clear of the band for each radio; out of range refused")


def pipe():
    sink = rtl433.pipe_sink()
    r, w = os.pipe()
    os.set_blocking(w, False)
    sink.set_fd(w)
    block = (np.arange(8191) + 1j).astype(np.complex64)   # odd: not a page
    t0 = time.monotonic()
    for _ in range(200):
        assert sink.work([block], []) == len(block)
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
    # Levelled (all limited to full scale here), but each sample's phase
    # kept: a block out of step would show as the wrong phases.
    x = np.frombuffer(bytes(got), np.complex64).reshape(-1, len(block))
    assert np.allclose(np.angle(x), np.angle(block), atol=1e-5), "out of step"
    os.close(r)
    sink.work([block], [])
    sink.work([block], [])
    assert sink.broken, "a closed reader not noticed"
    os.close(w)
    print(f"pipe: {sink.sent} samples sent, {sink.dropped} dropped in whole blocks, "
          "none blocked; a closed reader noticed")


def levels():
    rng = np.random.default_rng(3)
    for sigma in (1e-5, 1e-3):                   # a BB60D's noise, then a HackRF's
        sink = rtl433.pipe_sink()
        for _ in range(40):
            x = (sigma * (rng.standard_normal(4096) + 1j * rng.standard_normal(4096))
                 ).astype(np.complex64)
            y = sink.level(x)
        rms = np.sqrt(np.mean(np.abs(y) ** 2))
        assert abs(20 * np.log10(rms / rtl433.NOISE_LEVEL)) < 1.0, (sigma, rms)
    burst = np.full(4096, 0.5, np.complex64)     # far over the noise: held at full scale
    assert np.abs(sink.level(burst)).max() <= 0.99 + 1e-6
    loud = rtl433.pipe_sink()
    x = (0.3 * (rng.standard_normal(4096) + 1j * rng.standard_normal(4096))).astype(np.complex64)
    loud.level(x)
    assert loud.gain == 1.0, loud.gain            # never turned down
    print("levels: noise raised to an RTL-SDR's from 1e-5 and 1e-3, bursts held "
          "at full scale, loud noise left alone")


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
        chain.proc.set_log(log)
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
        gain = chain.pipe.gain
    finally:
        engine.close()
    assert proc.poll() is not None, "rtl_433 still running after close"
    lines = [json.loads(l) for l in open(log)]
    assert lines and lines[0]['model'] == 'fmrx_test' and 'heard' in lines[0]
    print(f"decode: {BITS} at the BB60D's level read back by rtl_433 "
          f"{rtl433.program_version(program)} (SNR {msg['snr']:.0f} dB, gain "
          f"{20 * np.log10(gain):.0f} dB), logged; rtl_433 ended on close")


def main():
    plans()
    levels()
    pipe()
    program = rtl433.find_program()
    if program is None:
        print(f"rtl_433 not found ({rtl433.install_hint()}): decode not tested")
        return 0
    folder = tempfile.mkdtemp(prefix='fmrx-433-')
    try:
        decode(program, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
