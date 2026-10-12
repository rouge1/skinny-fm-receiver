"""Phase 3, the spectrum reader: ``fm_receiver.spectra`` opens a Sceptre spectrum
recording - a sweep DVR (``.sdvr``) or a spectrum ``.fft`` - and reads any
stretch of it, any band, as dBm. Synthetic files only (tests/signals.py).

Run: python tools/tests/test_spectra.py
"""

import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import signals  # noqa: E402
from fm_receiver import sceptre_dvr, spectra  # noqa: E402

BINS = 4096
START_HZ = 4882.8125
BIN_HZ = 4882.8125
TILES, SWEEPS, INTERVAL = 8, 16, 0.2325


def spectrum():
    """The one spectrum every sweep of the synthetic DVR holds: a floor with a
    slow ripple and three narrow carriers, inside what a tile's byte can hold
    (-105 to -62 dBm)."""
    k = np.arange(BINS)
    db = -95.0 + 6.0 * np.sin(k / 97.0)
    for bin_, level in ((600, -65.0), (1500, -70.0), (3000, -75.0)):
        db[bin_] = level
    return np.clip(db, -105.0, -62.0)


def make_dvr(folder):
    path = os.path.join(folder, 'sweep.sdvr')
    signals.write_sweep_sdvr(path, tiles=TILES, bins=BINS, start_hz=START_HZ, bin_hz=BIN_HZ,
                             interval=INTERVAL, sweeps=SWEEPS, dbm=spectrum())
    return path


def make_fft(folder, rows=40, bins=2048, name='spec.fft'):
    """A spectrum ``.fft``: float32 dBm, a row a sweep, row ``r`` the
    spectrum ``-100 + r`` plus a ramp across the row."""
    data = (-100.0 + np.arange(rows)[:, None] + 0.01 * np.arange(bins)[None, :]).astype('<f4')
    path = os.path.join(folder, name)
    signals.write_blue(path, data, rate=1.0 / 0.2322876, fmt='SF', subsize=bins,
                       keywords={'RF_FREQ': 3.0e9, 'TIME_EPOCH': '2026-09-29T23:24:27Z'})
    return path, data


def test_sdvr(folder):
    path = make_dvr(folder)
    before = os.stat(path).st_mtime_ns
    f = spectra.open_spectra(path)
    assert f.kind == 'dvr-sweep', f.kind
    assert f.rows == TILES * SWEEPS, f.rows
    assert f.bins == BINS and abs(f.start_hz - START_HZ) < 1e-6 and abs(f.bin_hz - BIN_HZ) < 1e-6
    assert abs(f.row_seconds - INTERVAL) < 1e-9
    assert abs(f.seconds - TILES * SWEEPS * INTERVAL) < 1e-6, f.seconds
    assert abs(f.stop_hz - (START_HZ + (BINS - 1) * BIN_HZ)) < 1e-6
    assert 1.7e9 < f.start < 2.0e9, f.start

    # Everything: the injected spectrum, in dBm, to the byte's own step.
    times, freqs, db = f.read()
    assert db.shape == (TILES * SWEEPS, BINS) and db.dtype == np.float32, (db.shape, db.dtype)
    assert freqs.shape == (BINS,) and abs(freqs[0] - START_HZ) < 1e-6
    assert times.shape == (TILES * SWEEPS,) and times[0] == 0.0
    assert np.all(np.diff(times) > 0) and abs(times[1] - INTERVAL) < 1e-9
    err = np.abs(db - spectrum()[None, :])
    assert err.max() < 0.7, ('worst bin off by', err.max())          # a byte is 0.52 to 0.63 dB
    assert np.median(err) < 0.35, np.median(err)

    # A stretch: rows whose time is in [t0, t1), times counted from the start.
    t0, t1 = 2 * SWEEPS * INTERVAL, 3 * SWEEPS * INTERVAL
    times, _, part = f.read(t0, t1)
    assert part.shape[0] == SWEEPS, part.shape
    assert abs(times[0] - t0) < 1e-6 and times[-1] < t1
    _, _, whole = f.read()
    assert np.array_equal(part, whole[2 * SWEEPS:3 * SWEEPS])
    # Asked past the end, or for nothing: empty, not an error.
    assert f.read(f.seconds + 5.0, f.seconds + 9.0)[2].shape == (0, BINS)
    assert f.read(3.0, 3.0)[2].shape[0] == 0

    # A band: bins from f_lo to f_hi inclusive of the bin each falls in.
    lo, hi = START_HZ + 1000 * BIN_HZ, START_HZ + 1999 * BIN_HZ
    _, freqs, band = f.read(f_lo=lo, f_hi=hi)
    assert band.shape[1] == 1000 and abs(freqs[0] - lo) < 1e-6 and abs(freqs[-1] - hi) < 1e-6
    assert np.array_equal(band, whole[:, 1000:2000])
    # The carrier at bin 1500 is where it should be, in dBm.
    assert abs(band[0, 500] - (-70.0)) < 0.7

    # Thinned: at most max_rows rows, evenly; at most max_cols columns, each
    # the loudest of the bins it covers (a narrow carrier must not vanish).
    times, freqs, small = f.read(max_rows=10, max_cols=64)
    assert small.shape == (10, 64), small.shape
    assert times[0] == 0.0 and times[-1] > 0.8 * f.seconds
    assert small.max() > -66.0, 'the -65 dBm carrier was averaged away'
    assert abs(freqs[0] - START_HZ) < BIN_HZ * BINS / 64
    assert f.read(max_rows=500)[2].shape[0] == TILES * SWEEPS      # fewer than asked: all
    assert os.stat(path).st_mtime_ns == before, 'the file was written'


def test_sdvr_reads_only_what_it_needs(folder):
    """A 1 GB ring holds hundreds of tiles: a stretch of two seconds must read
    the tiles it touches, not the file."""
    f = spectra.open_spectra(make_dvr(folder))
    seen = []
    real = sceptre_dvr.Sweeps.read

    def spy(self, *a, **k):
        seen.append((k.get('first', a[2] if len(a) > 2 else 0),
                     k.get('stop', a[3] if len(a) > 3 else None)))
        return real(self, *a, **k)

    sceptre_dvr.Sweeps.read = spy
    try:
        f.read(2 * SWEEPS * INTERVAL + 0.1, 2 * SWEEPS * INTERVAL + 1.0)
    finally:
        sceptre_dvr.Sweeps.read = real
    assert seen, 'it did not go through sceptre_dvr.Sweeps.read'
    for first, stop in seen:
        assert stop is not None and 0 < stop - first <= 2, (first, stop)


def test_fft(folder):
    path, data = make_fft(folder)
    before = os.stat(path).st_mtime_ns
    f = spectra.open_spectra(path)
    assert f.kind == 'fft', f.kind
    assert f.rows == 40 and f.bins == 2048
    assert abs(f.row_seconds - 0.2322876) < 1e-6
    assert abs(f.seconds - 40 * 0.2322876) < 1e-4
    assert abs(f.start_hz - 4882.8125) < 1e-3 and abs(f.bin_hz - 4882.8125) < 1e-3
    assert f.start is not None and 1.7e9 < f.start < 2.0e9
    times, freqs, db = f.read()
    assert db.shape == (40, 2048) and np.array_equal(db, data)         # float32 dBm as stored
    assert abs(times[3] - 3 * 0.2322876) < 1e-6
    _, _, part = f.read(10 * f.row_seconds, 20 * f.row_seconds)
    assert part.shape == (10, 2048) and np.array_equal(part, data[10:20])
    _, freqs, band = f.read(f_lo=freqs[100], f_hi=freqs[199])
    assert band.shape == (40, 100) and np.array_equal(band, data[:, 100:200])
    _, _, small = f.read(max_rows=8, max_cols=32)
    assert small.shape == (8, 32)
    assert os.stat(path).st_mtime_ns == before


def test_refused(folder):
    # An IQ-only DVR holds no spectrum tiles.
    rng = np.random.default_rng(3)
    iq = (rng.standard_normal(1 << 17) + 1j * rng.standard_normal(1 << 17)).astype(np.complex64)
    iq_path = os.path.join(folder, 'iq.sdvr')
    signals.write_sdvr(iq_path, iq * 0.1, 28e6, 100e6, tiles=False)
    for path in (iq_path,):
        try:
            spectra.open_spectra(path)
        except spectra.SpectraError:
            pass
        else:
            raise AssertionError('an IQ-only DVR opened as spectra')
    # An IQ BLUE file is not a spectrum, and neither is a file of another kind.
    cdif = os.path.join(folder, 'x.cdif')
    signals.write_blue(cdif, iq[:4096], 7e6, fmt='CF', keywords={'RF_FREQ': 99.3e6})
    junk = os.path.join(folder, 'junk.fft')
    with open(junk, 'wb') as fh:
        fh.write(b'not a blue file' * 100)
    text = os.path.join(folder, 'a.txt')
    with open(text, 'w') as fh:
        fh.write('hi')
    for path in (cdif, junk, text, os.path.join(folder, 'missing.sdvr')):
        try:
            spectra.open_spectra(path)
        except spectra.SpectraError:
            pass
        else:
            raise AssertionError(f'{os.path.basename(path)} opened as spectra')


def main():
    with tempfile.TemporaryDirectory() as folder:
        test_sdvr(folder)
        print('spectra: a sweep DVR read in dBm, stretched, banded, thinned')
        test_sdvr_reads_only_what_it_needs(folder)
        print('spectra: only the tiles a stretch touches are read')
        test_fft(folder)
        print('spectra: a .fft read as stored')
        test_refused(folder)
        print('spectra: IQ-only, IQ BLUE, junk and missing files refused')
    print('spectra: all checks passed')


if __name__ == '__main__':
    main()
