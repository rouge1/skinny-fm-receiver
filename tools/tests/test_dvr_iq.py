"""Sceptre's BLUE recordings (``.cdif``, ``.fft``) and ``tools/dvr-to-iq``.

No radio and no Sceptre data: files built to the layout the code reads
(``signals.write_blue``, ``signals.write_sdvr``). It must:

- read a BLUE IQ file: its format, rate, centre, start time, keywords of every
  kind (double, int, text) and samples, and a 2-D scalar file's rows; and refuse
  what is not BLUE, is big-endian, or has a format it does not read;
- cut a channel out of a DVR: the whole band as an exact copy; a tone 200 kHz
  off centre landing at 0 Hz at the right level in units of full scale and
  in mW (from the scale the DVR carries); a start and a length in seconds;
- write the ``.cfile`` with its ``.sigmf-meta`` and ``.json``, which this app
  reads back as the same rate and centre and plays sample for sample;
- refuse a channel outside the band, a rate above the DVR's, files that exist,
  and a DVR that is not IQ - with the reason.

Run:  python tools/tests/test_dvr_iq.py [--keep]     (about 15 s)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from gnuradio import blocks, gr  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import dvr_iq, sceptre_blue  # noqa: E402
from fm_receiver.radios import IQFile, read_iq_metadata  # noqa: E402
from tests import signals  # noqa: E402

TOOL = os.path.join(os.path.dirname(HERE), 'dvr-to-iq')
RATE = 2.5e6
CENTRE = 98.4e6
TONE_HZ = 200e3
LEVEL = 0.3                                   # the tone's magnitude, full scale 1.0
REF_DBM = -20.0


def blue_checks(folder):
    rng = np.random.default_rng(5)
    iq = (rng.standard_normal(5000) + 1j * rng.standard_normal(5000)).astype(np.complex64)
    kw = {'RF_FREQ': 403903225.8, 'DATA_GAIN': 100.309, 'SCEPTRE_CHANNEL': 1,
          'TIME_EPOCH': '2026-09-30T10:44:21.000000000000Z', 'IOVERSION': 'Sceptre 5.9.1'}
    path = signals.write_blue(os.path.join(folder, 'a.cdif'), iq, 7e6, keywords=kw)
    b = sceptre_blue.read(path)
    assert b.fmt == 'CF' and b.type == 1001 and b.samples == 5000, (b.fmt, b.samples)
    assert abs(b.rate - 7e6) < 1e-3 and abs(b.center_hz - 403903225.8) < 1e-6
    assert abs(b.keywords['DATA_GAIN'] - 100.309) < 1e-9 and b.keywords['SCEPTRE_CHANNEL'] == 1
    assert b.keywords['IOVERSION'] == 'Sceptre 5.9.1', b.keywords
    assert abs(b.start - (1790765061 + 0.855)) < 1e-3, b.start      # 10:44:21 UTC + xstart
    assert np.array_equal(b.iq(), iq) and np.array_equal(b.iq(10, 20), iq[10:20])

    rows = rng.standard_normal((6, 32)).astype(np.float32)
    p2 = signals.write_blue(os.path.join(folder, 'b.fft'), rows, 4.3, fmt='SF', subsize=32)
    b2 = sceptre_blue.read(p2)
    assert b2.fmt == 'SF' and b2.data.shape == (6, 32) and np.array_equal(b2.data, rows), b2.data.shape
    # rows: the time axis is the second one (a frequency axis runs along a row)
    assert abs(b2.rate - 4.3) < 1e-9 and abs(b2.start - (1790765061 + 0.855)) < 1e-3, (b2.rate, b2.start)
    assert b2.xstart == b2.xdelta == 4882.8125

    def refused(path, *words):
        try:
            sceptre_blue.read(path)
        except sceptre_blue.BlueError as exc:
            assert all(w in str(exc) for w in words), (words, str(exc))
            return
        raise AssertionError(f"{path} should have been refused")

    junk = os.path.join(folder, 'junk.cdif')
    open(junk, 'wb').write(bytes(2048))
    refused(junk, 'not a BLUE')
    big = bytearray(open(path, 'rb').read(600))
    big[4:12] = b'IEEEIEEE'
    open(os.path.join(folder, 'big.cdif'), 'wb').write(big)
    refused(os.path.join(folder, 'big.cdif'), 'big-endian')
    odd = bytearray(open(path, 'rb').read(600))
    odd[52:54] = b'XX'
    open(os.path.join(folder, 'odd.cdif'), 'wb').write(odd)
    refused(os.path.join(folder, 'odd.cdif'), "'XX'")
    print("BLUE: keywords of every kind, rate, centre, start, samples, 2-D rows; three refusals")


def make_dvr(folder, seconds=2.0):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    iq = (LEVEL * np.exp(2j * np.pi * TONE_HZ * t)).astype(np.complex64)
    path = os.path.join(folder, 'tone.sdvr')
    want = signals.write_sdvr(path, iq, RATE, CENTRE, run=1 << 17, ref_dbm=REF_DBM)
    return path, want


def convert_checks(folder, path, want):
    dvr = dvr_iq.Dvr(path)
    assert abs(dvr.full_scale_dbm - (REF_DBM + 10)) < 1e-3, dvr.full_scale_dbm

    whole = dvr.extract(CENTRE, RATE)
    assert np.array_equal(whole, want), 'the whole band is not an exact copy'

    ch = dvr.extract(CENTRE + TONE_HZ, 500e3)               # the tone to 0 Hz
    assert abs(len(ch) - len(want) / RATE * 500e3) < 4, len(ch)
    mid = ch[len(ch) // 10:-len(ch) // 10]
    assert abs(abs(np.mean(mid)) - LEVEL) < 0.01 and np.std(mid) < 0.01, (np.mean(mid), np.std(mid))
    off = dvr.extract(CENTRE, 500e3)                        # the tone 200 kHz away
    mid = off[len(off) // 10:-len(off) // 10]
    assert abs(np.mean(mid)) < 0.01, 'the tone should not be at 0 Hz here'
    mw = dvr.extract(CENTRE + TONE_HZ, 500e3, units='mw')[len(ch) // 10:-len(ch) // 10]
    want_mw = 10 ** ((20 * np.log10(LEVEL) + dvr.full_scale_dbm) / 10)      # 10**(-2.05) mW
    got_mw = np.mean(np.abs(mw) ** 2)
    print(f"channel: tone at 0 Hz, {abs(np.mean(ch[len(ch) // 10:-len(ch) // 10])):.3f} of full scale; in mW "
          f"{10 * np.log10(got_mw):.2f} dBm against {10 * np.log10(want_mw):.2f}")
    assert abs(10 * np.log10(got_mw / want_mw)) < 0.1, (got_mw, want_mw)
    cut = dvr.extract(CENTRE, RATE, start=0.5, seconds=0.25)
    assert len(cut) == int(0.25 * RATE), len(cut)
    assert np.array_equal(cut, want[int(0.5 * RATE):int(0.5 * RATE) + len(cut)])

    for kwargs, word in (({'center_hz': CENTRE + 2e6, 'rate': 500e3}, 'not inside'),
                         ({'center_hz': CENTRE, 'rate': RATE * 2}, 'more than'),
                         ({'center_hz': CENTRE, 'rate': 500e3, 'start': 99.0},
                          'nothing of the DVR')):
        try:
            dvr.extract(**kwargs)
        except ValueError as exc:
            assert word in str(exc), (word, str(exc))
        else:
            raise AssertionError(f"{kwargs} should be refused")


def tool_checks(folder, path, want):
    def run(*args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True,
                              text=True, timeout=200, cwd=folder)

    base = os.path.join(folder, 'chan')
    r = run(path, '--center', '98.6', '--rate', '0.5', '--station', '98.7',
            '--units', 'mw', '-o', base)
    assert r.returncode == 0, r.stderr + r.stdout
    print(r.stdout.strip().splitlines()[-1])
    meta = json.load(open(base + '.sigmf-meta'))['global']
    assert meta['core:datatype'] == 'cf32_le' and meta['fmrx:units'] == 'mw'
    assert abs(meta['fmrx:full_scale_dbm'] - (REF_DBM + 10)) < 1e-3, meta
    info = read_iq_metadata(base + '.sigmf-meta')
    assert abs(info['rate'] - 500e3) < 1e-3 and abs(info['center_hz'] - 98.6e6) < 1e-3, info
    assert abs(info['station_hz'] - 98.7e6) < 1e-3, info
    assert json.load(open(base + '.json'))['center_hz'] == 98.6e6

    # The whole band: bytes equal what the DVR holds, and the app plays them.
    whole = os.path.join(folder, 'whole')
    r = run(path, '-o', whole)
    assert r.returncode == 0, r.stderr + r.stdout
    got = np.fromfile(whole + '.cfile', dtype=np.complex64)
    assert np.array_equal(got, want), 'the whole-band file differs from the DVR'
    radio = IQFile(whole + '.sigmf-meta', repeat=False, throttle=False)
    radio.open()
    assert radio.rate == RATE and radio.center_hz == CENTRE
    tb = gr.top_block()
    head, sink = blocks.head(gr.sizeof_gr_complex, 4096), blocks.vector_sink_c()
    tb.connect(radio.block, head, sink)
    tb.run()
    assert np.array_equal(np.array(sink.data(), dtype=np.complex64), want[:4096])
    print("dvr-to-iq: a channel with its metadata; the whole band identical, and playable here")

    # Refusals.
    r = run(path, '-o', whole)
    assert r.returncode != 0 and 'exists' in r.stderr, r.stderr
    assert run(path, '-o', whole, '--force').returncode == 0
    r = run(path, '--center', '104', '-o', os.path.join(folder, 'x'))
    assert r.returncode != 0 and 'not inside' in r.stderr, r.stderr
    r = run(path, '--rate', '9', '-o', os.path.join(folder, 'y'))
    assert r.returncode != 0 and 'more than' in r.stderr, r.stderr
    sweeps = os.path.join(folder, 'sweeps.sdvr')
    with open(sweeps, 'wb') as fh:
        fh.write(b'SDVR' + bytes(4092) + bytes(4096 * 3))
        for k in range(3):
            fh.write(signals._sdvr_header(b'SB', 1.79e9 + k, 0.2325, 16, bins=64)
                     + signals._pad(bytes(16 * 64)))
    r = run(sweeps, '-o', os.path.join(folder, 'z'))
    assert r.returncode != 0 and 'holds no IQ' in r.stderr, r.stderr
    print("refused: outside the band, above the rate, existing files, a sweep DVR")


def main():
    folder = tempfile.mkdtemp(prefix='fmrx-dvriq-')
    try:
        blue_checks(folder)
        path, want = make_dvr(folder)
        convert_checks(folder, path, want)
        tool_checks(folder, path, want)
    finally:
        if '--keep' in sys.argv:
            print('kept', folder)
        else:
            shutil.rmtree(folder, ignore_errors=True)
    print('OK')


if __name__ == '__main__':
    main()
