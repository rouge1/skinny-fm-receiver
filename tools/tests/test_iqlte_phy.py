"""Roadmap Round 29.1: our own LTE PBCH decoder (``fm_receiver/iqlte_phy.py``, ``iqlte_phy.pbch``)
and ``iq-lte --mib``.

Synthetic only, with made-up values (PCIs 123 and 321, random MIB fields). The real-air checks
(every frame's MIB on the Band 13 recording and the 700 MHz pair, equal to the outside
``pdsch_ue``'s ports, bandwidth and SFN) are in the ops folder, not here. What the checks pin:

- the tail-biting code: Viterbi gives the bits back clean and through noise, and the register
  starts as the last six bits (encoding a rotated input rotates the output);
- rate matching: 120 coded bits each sent 16 times in 1920, and recovered;
- the CRC mask names the port count (1, 2, 4);
- SFBC on 2 and 4 ports undone exactly through random channels;
- the scrambling quarter (SFN mod 4) found from the repeats in one frame;
- the CRS on the central 72 subcarriers does not depend on the bandwidth and equals
  ``lte_synth``'s own CRS (written separately);
- end to end: a synthetic 10 MHz cell with 1, 2 or 4 ports, a real PBCH, 300 Hz off, at 0 dB:
  ``iqlte_phy.pbch`` gives its MIB fields, ports and SFN from every frame; a cell with no PBCH (random
  QPSK there) gives no MIB and a reason;
- ``tools/iq-lte FILE --mib`` adds ``own_mib``; ``iqlte_phy`` imports numpy only.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_phy.py   (about 10 s)
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

from tests import lte_synth  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

PCI = 123
FIELDS = dict(dl_bandwidth=3, phich_duration=0, phich_resource=2, sib1_br=0)
SFN0 = 517
RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"))


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


# ----- codes ---------------------------------------------------------------------------------

def check_codes():
    rng = np.random.default_rng(3)

    def viterbi():
        bad = 0
        for _ in range(20):
            c = rng.integers(0, 2, 40).astype(np.uint8)
            d = L.tbcc_encode(c)
            soft = (1 - 2.0 * d) * 2 + rng.standard_normal(d.shape) * 1.5
            bad += not np.array_equal(L.viterbi_tbcc(1 - 2.0 * d), c) or not np.array_equal(L.viterbi_tbcc(soft), c)
        c = rng.integers(0, 2, 40).astype(np.uint8)
        rotated = np.array_equal(L.tbcc_encode(np.roll(c, 5)), np.roll(L.tbcc_encode(c), 5, axis=1))
        return bad == 0 and rotated, (bad, rotated)
    expect("tail-biting code: Viterbi clean and through noise; tail biting (a rotated input rotates the output)",
           viterbi)

    def rate_match():
        d = rng.integers(0, 2, (3, 40)).astype(np.uint8)
        e = L.cc_rate_match(d, 1920)
        back = L.cc_rate_recover(1 - 2.0 * e, 40)
        sel = L._cc_selection(40, 1920)
        counts = np.bincount(sel, minlength=120)
        return (np.array_equal((back < 0).astype(np.uint8), d) and set(counts) == {16}
                and np.array_equal(sel[:120], sel[120:240])), sorted(set(counts))
    expect("rate matching: 120 coded bits each sent 16 times in 1920, and recovered", rate_match)

    def masks():
        out = {}
        for ports in (1, 2, 4):
            a = L.mib_bits(dict(FIELDS, sfn=SFN0))
            c = np.concatenate((a, L._crc(a, '16') ^ L.PORT_MASKS[ports]))
            e = L.cc_rate_match(L.tbcc_encode(c), L.E_PBCH)
            got = L.decode_bch(1 - 2.0 * e)
            out[ports] = got is not None and got[1] == ports and np.array_equal(got[0], a)
        return all(out.values()), out
    expect("the CRC mask names the port count (1, 2, 4)", masks)

    def sfbc():
        bad = []
        for ports in (2, 4):
            d = (rng.choice([-1, 1], 240) + 1j * rng.choice([-1, 1], 240)) / np.sqrt(2)
            y = L.sfbc_encode(d, ports)
            h = np.repeat((rng.standard_normal((ports, 60)) + 1j * rng.standard_normal((ports, 60))), 4, axis=1)
            r = (h * y).sum(axis=0)
            llr = L.soft_bits(r, h, 1.0, ports)
            want = np.empty(480)
            want[0::2], want[1::2] = d.real, d.imag
            if not np.array_equal(np.sign(llr), np.sign(want)):
                bad.append(ports)
        return not bad, bad
    expect("SFBC on 2 and 4 ports undone through random channels", sfbc)

    def quarter():
        ok = []
        for q in range(4):
            y = L.encode_pbch(dict(FIELDS, sfn=516), PCI, 1)[0, q]
            r = y + (rng.standard_normal(240) + 1j * rng.standard_normal(240)) * 0.5
            llr = L.soft_bits(r, np.ones((1, 240)), 0.25, 1)
            s = L.quarter_scores(llr, PCI)
            ok.append(int(np.argmax(s)) == q and s[q] > 0.5 > max(np.delete(s, q)))
        return all(ok), ok
    expect("the scrambling quarter (SFN mod 4) found from the repeats in one frame", quarter)

    def mib():
        f = dict(dl_bandwidth=5, phich_duration=1, phich_resource=3, sfn=1023, sib1_br=17,
                 si_unchanged_br=1, part_earfcn=5, spare=0)
        p = L.parse_mib(L.mib_bits(f), 3)
        return (p['n_rb'] == 100 and p['phich_duration'] == 'extended' and p['phich_resource'] == '2'
                and p['sfn'] == 1023 and p['sib1_br'] == 17 and p['part_earfcn'] == 5), p
    expect("MIB fields round trip (36.331)", mib)

    def crs():
        bad = []
        for pci in (0, 5, PCI, 503):
            for ns in (0, 1, 2, 19):
                for port in range(4):
                    for l in L.crs_symbols(port):
                        kp, v = L.central_crs(pci, ns, l, port)
                        for n_rb in (6, 25, 50, 100):
                            shift = (L.crs_shift(port, l, ns) + pci % 6) % 6
                            full = L.crs_values(pci, ns, l, n_rb)
                            m = (kp + 6 * n_rb - 36 - shift) // 6
                            if not np.allclose(full[m], v):
                                bad.append((pci, ns, port, n_rb))
                        k_s, r_s = lte_synth._crs_port(pci, ns, l, 50, port)
                        sel = (k_s >= 264) & (k_s < 336)
                        if not (np.array_equal(k_s[sel] - 264, kp) and np.allclose(r_s[sel], v)):
                            bad.append(('synth', pci, ns, port))
        return not bad, bad[:4]
    expect("the CRS on the central 72 subcarriers: no bandwidth in it, and lte_synth's own CRS", crs)


# ----- end to end ---------------------------------------------------------------------------

def cell_iq(ports, pbch=True, snr_db=0.0, n_frames=12, cfo_hz=300.0, seed=5):
    res_k = L.pbch_res(PCI)
    n_sc = 600

    def res(f):
        if not pbch:
            return []
        sfn = (SFN0 + f) % 1024
        y = L.encode_pbch(dict(FIELDS, sfn=sfn - sfn % 4), PCI, ports)[:, sfn % 4]
        return [(p, 7 + l, n_sc // 2 - 36 + res_k[res_k[:, 0] == l, 1], y[p, res_k[:, 0] == l])
                for p in range(ports) for l in range(4)]
    gains = [1.0, 0.7 * np.exp(1.1j), 0.6 * np.exp(-2.0j), 0.8 * np.exp(0.4j)][:ports]
    return lte_synth.synth_lte(bw_mhz=10, n_frames=n_frames, pci=PCI, snr_db=snr_db, seed=seed, cfo_hz=cfo_hz,
                               ports=ports, port_gains=gains, res=res, idle=True)


def check_end_to_end():
    def decode():
        out = {}
        for ports in (1, 2, 4):
            iq, fs = cell_iq(ports)
            cell = next(c for c in iqlte.cells(iq, fs) if c['pci'] == PCI)
            got = L.pbch(iq, fs, cell, carrier_hz=700e6)
            mib = got['mib'] or {}
            out[ports] = (got['ports'] == ports and mib.get('sfn') == SFN0 and mib.get('n_rb') == 50
                          and mib.get('phich_resource') == '1' and got['decoded'] >= got['frames'] - 1 >= 9
                          and abs(got['cfo_hz'] - 300.0) < 50, got['decoded'], got['frames'], got['ports'],
                          mib.get('sfn'), round(got['cfo_hz']))
        return all(v[0] for v in out.values()), out
    expect("a 10 MHz cell, 1, 2 and 4 ports, 300 Hz off, 0 dB: its MIB, ports and SFN from every frame",
           decode)

    def none():
        iq, fs = cell_iq(2, pbch=False, snr_db=10.0)
        cell = next(c for c in iqlte.cells(iq, fs) if c['pci'] == PCI)
        got = L.pbch(iq, fs, cell, carrier_hz=700e6)
        return got['mib'] is None and got['reason'], got
    expect("random QPSK where the PBCH should be: no MIB, and a reason", none)

    def cli():
        iq, fs = cell_iq(2, snr_db=6.0)
        folder = tempfile.mkdtemp(prefix='iqlte-mib-')
        try:
            base = os.path.join(folder, 'lte')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                           'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile',
                                '--mib', '--t1', '0.1'], capture_output=True, text=True, timeout=300)
            out = json.loads(r.stdout) if r.returncode == 0 else {}
            own = [c.get('own_mib') or {} for c in out.get('cells', []) if c.get('pci') == PCI]
            return (r.returncode == 0 and own and (own[0].get('mib') or {}).get('n_rb') == 50
                    and own[0].get('ports') == 2), (r.stderr[-300:], own)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-lte FILE --mib: each cell's own_mib", cli)

    def imports():
        code = ("import sys; sys.path.insert(0, %r)\nfrom fm_receiver import iqlte_phy\n"
                "bad = [m for m in ('scipy', 'gnuradio', 'PyQt5', 'matplotlib') if m in sys.modules]\n"
                "print(bad)" % os.path.dirname(HERE))
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
        return r.stdout.strip() == '[]', r.stdout + r.stderr
    expect("iqlte_phy imports numpy only", imports)


def main():
    for group in (check_codes, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
