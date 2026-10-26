"""A known LTE cell of another timing (another site) cancelled in the samples: ``iqlte_phy``'s
``_PbFrames.cut``, ``_known_estimate`` and ``_known_samples``.

Synthetic only, with made-up values (PCIs 123, 321 and 322, random transport blocks); every cell
``quiet`` (no random data: what a cell sends and nobody rebuilds would set the floor, not the
cancelling). What the checks pin:

- the rebuild alone: one cell, read at a timing 2345.6 samples off its own with itself known, its
  subframe grid and its PBCH grid (the 1.92 MS/s cut, narrower than the cell) lose at least 25 dB;
  ``subframe(cancel=False)`` is the grid as it is;
- a pair, the weaker 10 dB down, 2500 samples later and 180 Hz off: without cancellation the
  weaker's SIB1 copies fail alone; with the stronger known, its MIB in every frame, every DCI and
  every SIB1 copy alone, its own bytes; the stronger read with the weaker known: unchanged;
- two sectors of one site (frames aligned, the second 4 dB down) and a cell of another site 10 dB
  down: with only the stronger sector known its SIB1 copies fail alone, with both (fitted
  together, one timing) every copy decodes alone;
- ``tools/iq-lte FILE --pdsch`` on those three: the other site's cell read with both sectors known;
  with it listed before the second sector (5 dB down, the sector 7) and some SIB1 copy missed
  alone, read once more with both known, every cell's SIB1 its own.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_timing.py   (about 40 s)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_synth, test_iqlte_cancel as C  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

A, B, W = 123, 321, 322
APART = 2500                                      # native samples (15.36 MS/s): 2.3 symbols
GAINS = [[1.0, 0.7 * np.exp(1.1j)], [0.9 * np.exp(0.5j), 0.6 * np.exp(-2.0j)],
         [0.8 * np.exp(-0.3j), 0.9 * np.exp(2.2j)]]
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


def blocks(n, seed=21):
    rng = np.random.default_rng(seed)
    tbs = [rng.integers(0, 2, 256).astype(np.uint8) for _ in range(n)]
    return tbs, [np.packbits(t).tobytes().hex() for t in tbs]


def cell(pci, db, delay, tb, gains, **kw):
    return dict(pci=pci, power_db=db, delay_samples=delay, ports=2, port_gains=gains, res=C.cell_res(pci, tb),
                quiet=True, **kw)


def alone(s, hx):
    """Every SIB1 copy decoded alone, each its own bytes."""
    return (s is not None and s['decoded'] == s['tried'] >= 8
            and {b['hex'] for b in s['blocks'] if b.get('hex')} == {hx})


def power_db(a, b):
    return 10 * np.log10(np.sum(np.abs(a) ** 2) / np.sum(np.abs(b) ** 2))


def check_rebuild():
    tbs, _hexes = blocks(1)
    iq, fs = lte_synth.synth_lte_multi([cell(A, 0.0, 0, tbs[0], GAINS[0])], bw_mhz=10, n_frames=12, snr_db=40.0,
                                       seed=4, cfo_hz=250.0, fs=20e6)
    c = [c for c in iqlte.cells(iq, fs) if c['pci'] == A][0]
    pb, d, s = C.decode(iq, fs, c)
    kc = L.known_cell(fs, c, pb, d, s, carrier_hz=700e6)
    far = dict(c, frame_offset_samples=c['frame_offset_samples'] + 2345.6)
    known = L._cell_frames(fs, far, pb, 0.0, 700e6, [kc])
    plain = L._cell_frames(fs, far, pb, 0.0, 700e6)

    def deep():
        drops = [power_db(plain.subframe(iq, f, sf, 50), known.subframe(iq, f, sf, 50))
                 for f, sf in ((3, 0), (4, 5), (6, 3))]
        drops += [power_db(plain.grid(iq, f), known.grid(iq, f)) for f in (3, 6)]
        return min(drops) >= 25.0, ' '.join('%.1f' % v for v in drops)
    expect("the rebuild alone: a cell read 2345.6 samples off its timing, itself known: grids down >= 25 dB", deep)

    def raw():
        return np.array_equal(known.subframe(iq, 4, 5, 50, cancel=False), plain.subframe(iq, 4, 5, 50)), ''
    expect("subframe(cancel=False): the samples as they are, nothing of another timing taken out", raw)


def check_pair():
    tbs, hexes = blocks(2)
    iq, fs = lte_synth.synth_lte_multi([cell(A, 0.0, 0, tbs[0], GAINS[0]),
                                        cell(W, -10.0, APART, tbs[1], GAINS[1], cfo_hz=180.0)],
                                       bw_mhz=10, n_frames=18, snr_db=15.0, seed=4, cfo_hz=250.0)
    cells = {c['pci']: c for c in iqlte.cells(iq, fs)}
    state = {}

    def found():
        return (A in cells and W in cells and cells[W]['frame_offset_samples']
                - cells[A]['frame_offset_samples'] == APART), sorted(cells)
    expect("both cells found, 2500 samples apart", found)

    def without():
        state['a'] = C.decode(iq, fs, cells[A])
        s = C.decode(iq, fs, cells[W])[2]
        return alone(state['a'][2], hexes[0]) and (s is None or s['decoded'] <= s['tried'] // 2), \
            s and (s['decoded'], s['tried'])
    expect("without cancellation: the weaker cell's SIB1 copies fail alone", without)

    def with_known():
        kc = L.known_cell(fs, cells[A], *state['a'], carrier_hz=700e6)
        pb, d, s = C.decode(iq, fs, cells[W], known=[kc])
        state['w'] = (pb, d, s)
        return (pb.get('mib') and pb['decoded'] == pb['frames'] and len(d['dcis']) == d['subframes'] >= 8
                and alone(s, hexes[1])), (pb.get('decoded'), pb.get('frames'), s and (s['decoded'], s['tried']))
    expect("the stronger known: the weaker's MIB in every frame, every DCI, every SIB1 copy alone", with_known)

    def strong_same():
        kc = L.known_cell(fs, cells[W], *state['w'], carrier_hz=700e6)
        pb, d, s = C.decode(iq, fs, cells[A], known=[kc])
        return alone(s, hexes[0]) and pb['decoded'] == pb['frames'], (s['decoded'], s['tried'])
    expect("the stronger read with the weaker known: unchanged", strong_same)


def sites_iq(b_db, w_db, tbs):
    return lte_synth.synth_lte_multi([cell(A, 0.0, 0, tbs[0], GAINS[0]), cell(B, b_db, 0, tbs[1], GAINS[1]),
                                      cell(W, w_db, APART, tbs[2], GAINS[2], cfo_hz=180.0)],
                                     bw_mhz=10, n_frames=18, snr_db=15.0, seed=4, cfo_hz=250.0)


def run_cli(iq, fs):
    """``tools/iq-lte FILE --pdsch`` on ``iq``: its cells by PCI, and its stderr's tail."""
    folder = tempfile.mkdtemp(prefix='iqlte-timing-')
    try:
        base = os.path.join(folder, 'lte')
        iq.astype(np.complex64).tofile(base + '.cfile')
        with open(base + '.sigmf-meta', 'w') as fh:
            json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                       'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
        env = dict(os.environ, FMRX_LTE_TOOLS=folder)              # no pycrate: bytes only
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile',
                            '--pdsch', '--t1', '0.18'], capture_output=True, text=True, timeout=600, env=env)
        out = json.loads(r.stdout) if r.returncode == 0 else {}
        return {c.get('pci'): c for c in out.get('cells', [])}, r.stderr[-300:]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_sites():
    tbs, hexes = blocks(3)
    iq, fs = sites_iq(-4.0, -10.0, tbs)
    cells = {c['pci']: c for c in iqlte.cells(iq, fs)}
    state = {}

    def one():
        ra = C.decode(iq, fs, cells[A])
        ka = L.known_cell(fs, cells[A], *ra, carrier_hz=700e6)
        rb = C.decode(iq, fs, cells[B], known=[ka])
        state['known'] = [ka, L.known_cell(fs, cells[B], *rb, carrier_hz=700e6)]
        s = C.decode(iq, fs, cells[W], known=[ka])[2]
        return alone(rb[2], hexes[1]) and (s is None or s['decoded'] <= s['tried'] // 2), \
            s and (s['decoded'], s['tried'])
    expect("two sectors and another site: with one sector known, the other site's SIB1 copies fail alone", one)

    def both():
        s = C.decode(iq, fs, cells[W], known=state['known'])[2]
        return alone(s, hexes[2]), s and (s['decoded'], s['tried'])
    expect("both sectors known (fitted together): every SIB1 copy of the other site alone, its own bytes", both)

    def cli():
        got, err = run_cli(iq, fs)
        sib = (got.get(W, {}).get('own_sib1') or {})
        return (sib.get('sib1') == hexes[2] and got[W].get('own_known') == [A, B]), \
            (err, sib.get('decoded'), sib.get('tried'), got.get(W, {}).get('own_known'))
    expect("iq-lte FILE --pdsch: the other site's cell read with both sectors known", cli)

    def first():
        iq2, fs2 = sites_iq(-7.0, -5.0, tbs)            # the other site's cell above the second sector
        got, err = run_cli(iq2, fs2)
        order = [p for p in got]
        sib = {p: (got.get(p, {}).get('own_sib1') or {}) for p in (A, B, W)}
        return (order.index(W) < order.index(B) and all(sib[p].get('sib1') == h for p, h in zip((A, B, W), hexes))
                and got[W].get('own_known') == [A, B] and got[B].get('own_known') == [A, W]), \
            (err, order, {p: got.get(p, {}).get('own_known') for p in (B, W)})
    expect("iq-lte: an other-site cell listed before the second sector, left short, read again with both known",
           first)


def main():
    check_rebuild()
    check_pair()
    check_sites()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} ok, {len(failed)} FAIL of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
