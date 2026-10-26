"""The LTE tools' memory (2026-10-26): ``iqlte._resample`` lean and the same to the bit,
``iqlte.fingerprint`` resampling its capture once, and ``iqlte_phy``'s known cells keeping a
few rebuilt subframes, not the span's.

Synthetic only (PCI 123). What the checks pin:

- ``_resample`` equals the whole-array version it replaced (kept here as ``reference``), bit
  for bit, for odd, even and tiny lengths, several rates, an exact class rate, and a chunk
  forced small; its peak (``tracemalloc``) is at most 8 times the complex64 input (the old one
  took 23);
- ``fingerprint`` resamples a 20 MS/s capture once, not once a check, and gives the same result
  as with no sharing (``_fingerprint`` called alone); the shared blocks are read-only and gone
  after the call;
- a known cell keeps at most ``_KNOWN_CACHE`` rebuilt subframes, however long the span.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_memory.py   (about 10 s)
"""

import os
import sys
import tracemalloc

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_synth, test_iqlte_cancel as C  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ''), flush=True)


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:                                 # a crash is a failure, with its reason
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def reference(iq, fs_in, fs_out):
    """``iqlte._resample`` before 2026-10-26: one mask on ``fftfreq``, one whole-array cubic."""
    if abs(fs_in - fs_out) <= 1e-3:
        return np.ascontiguousarray(iq, dtype=np.complex128)
    x = np.ascontiguousarray(iq, dtype=np.complex128)
    n = x.size
    n_out = int(round(n * fs_out / fs_in))
    if n_out < 8 or n < 8:
        return x[:0]
    spec = np.fft.fft(x)
    freqs = np.fft.fftfreq(n, d=1.0 / fs_in)
    spec[np.abs(freqs) > (0.96 * fs_out * 0.5)] = 0.0
    y = np.fft.ifft(spec)
    y = np.concatenate((y[:1], y, y[-1:]))
    idx = np.arange(n_out) * (fs_in / fs_out) + 1.0
    i0 = np.floor(idx).astype(np.int64)
    i0 = np.clip(i0, 1, n - 1)
    f = idx - i0
    ym1, y0, y1, y2 = y[i0 - 1], y[i0], y[i0 + 1], y[np.minimum(i0 + 2, n)]
    f2 = f * f
    f3 = f2 * f
    return 0.5 * ((2.0 * y0) + (-ym1 + y1) * f + (2.0 * ym1 - 5.0 * y0 + 4.0 * y1 - y2) * f2
                  + (-ym1 + 3.0 * y0 - 3.0 * y1 + y2) * f3)


def check_resample():
    rng = np.random.default_rng(3)
    rates = ((20e6, 15.36e6), (20e6, 1.92e6), (12.5e6, 7.68e6), (25e6, 23.04e6), (15.36e6, 15.36e6),
             (2e6, 1.92e6), (40e6, 30.72e6))

    def equal():
        bad = []
        for n in (5, 8, 9, 100, 1001, 4096, 30001, 200000):
            for fi, fo in rates:
                x = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
                a, b = reference(x, fi, fo), iqlte._resample(x, fi, fo)
                if a.shape != b.shape or not np.array_equal(a, b):
                    bad.append((n, fi, fo))
        keep = iqlte._RESAMPLE_CHUNK
        iqlte._RESAMPLE_CHUNK = 7
        try:
            x = (rng.standard_normal(30001) + 1j * rng.standard_normal(30001)).astype(np.complex64)
            if not np.array_equal(reference(x, 20e6, 15.36e6), iqlte._resample(x, 20e6, 15.36e6)):
                bad.append('chunk 7')
        finally:
            iqlte._RESAMPLE_CHUNK = keep
        return not bad, bad
    expect("_resample: the same to the bit as the whole-array version (lengths, rates, chunks)", equal)

    def lean():
        x = (rng.standard_normal(2_000_000) * (1 + 1j)).astype(np.complex64)
        iqlte._resample(x, 20e6, 15.36e6)                  # numpy's FFT tables for this length first
        tracemalloc.start()
        try:
            iqlte._resample(x, 20e6, 15.36e6)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return peak <= 8 * x.nbytes, f'peak {peak / x.nbytes:.1f} x the input'
    expect("_resample: peak at most 8 x its complex64 input (the old one: 23)", lean)


def check_fingerprint():
    iq, fs = lte_synth.synth_lte(bw_mhz=10, n_frames=6, pci=123, snr_db=10.0, seed=2, fs=20e6, cfo_hz=300.0)

    def once():
        calls = []
        real = iqlte._resample

        def counting(x, fs_in, fs_out):
            calls.append((len(x), fs_in, fs_out))
            return real(x, fs_in, fs_out)
        iqlte._resample = counting
        try:
            shared = iqlte.fingerprint(iq, fs)
            n_shared = len(calls)
            del calls[:]
            alone = iqlte._fingerprint(iq, fs)
            n_alone = len(calls)
        finally:
            iqlte._resample = real
        return (n_shared == 1 and n_alone > 1 and shared == alone and shared['verdict'] == 'lte'
                and shared['pci'] == 123 and iqlte._BLOCKS is None), (n_shared, n_alone, shared['verdict'])
    expect("fingerprint: the capture resampled once (not once a check), the same result as unshared", once)

    def read_only():
        iqlte._BLOCKS = {}
        try:
            block = iqlte._prepare(iq, fs)[0]
            again = iqlte._prepare(iq, fs)[0]
            try:
                block[0] = 0
                wrote = True
            except ValueError:
                wrote = False
        finally:
            iqlte._BLOCKS = None
        return block is again and not wrote, ''
    expect("the shared class-rate block is one array, read-only", read_only)


def check_known_cache():
    def bounded():
        rng = np.random.default_rng(6)
        iq, fs = C.pair_iq([rng.integers(0, 2, 256).astype(np.uint8) for _ in range(2)], n_frames=24)
        cells = {c['pci']: c for c in iqlte.cells(iq, fs)}
        pb = L.pbch(iq, fs, cells[C.STRONG], carrier_hz=700e6)
        kc = L.known_cell(fs, cells[C.STRONG], pb, carrier_hz=700e6)
        got = L.pbch(iq, fs, cells[C.WEAK], carrier_hz=700e6, known=[kc])
        return (got.get('mib') is not None and 0 < len(kc['cache']) <= L._KNOWN_CACHE
                and got['frames'] > L._KNOWN_CACHE), (len(kc['cache']), got.get('frames'))
    expect("a known cell keeps at most _KNOWN_CACHE rebuilt subframes over a 24-frame span", bounded)


def main():
    check_resample()
    check_fingerprint()
    check_known_cache()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} ok, {len(failed)} FAIL of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
