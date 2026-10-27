"""A weaker sector's SI read under a stronger one's, and SI DCIs summed (roadmap Round 29.7):
``iqlte_phy.known_si`` and ``si_messages``' DCI sums.

Synthetic only, made-up values (PCIs 123 and 321, random transport blocks): the cells of
``test_iqlte_own_si`` (MIB, SIB1, two SI messages in 20 ms windows). What the checks pin:

- two sectors of one site, the second 6 dB down, sending their SI in the same subframes on the
  same RBs: with the first known by its signals and SIB1 only, the second's SI copies all fail;
  with the first's SI added (``known_si``), every copy decodes alone, its own bytes;
- one cell at -13 dB, its SI DCIs too weak alone: found by summing a message's subframes at
  one place in its windows, and the blocks they give (alone or summed) all the message's bytes.

Run:  PYTHONPATH=tools python tools/tests/test_iqlte_si_cancel.py   (about a minute)
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_synth, test_iqlte_cancel as C, test_iqlte_own_si as O  # noqa: E402
from fm_receiver import iqlte, iqlte_phy as L  # noqa: E402

A, B = 123, 321
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


def counts(si):
    return si['decoded'], si['tried'], sum(1 for b in si['blocks'] if 'combined' in b), len(si['combined'])


def check_sectors():
    rng = np.random.default_rng(11)
    sib1 = [rng.integers(0, 2, 256).astype(np.uint8) for _ in range(2)]
    ta, _ha = O.blocks(3)
    tb, hb = O.blocks(5)
    cells = [dict(pci=A, power_db=0.0, delay_samples=0, ports=2, port_gains=[1.0, 0.7 * np.exp(1.1j)],
                  res=O.cell_res(sib1[0], ta, pci=A)),
             dict(pci=B, power_db=-6.0, delay_samples=0, ports=2, port_gains=[0.9 * np.exp(0.5j), 0.6 * np.exp(-2.0j)],
                  res=O.cell_res(sib1[1], tb, pci=B))]
    iq, fs = lte_synth.synth_lte_multi(cells, bw_mhz=10, n_frames=36, snr_db=15.0, seed=4, cfo_hz=250.0, idle=True)
    found = {c['pci']: c for c in iqlte.cells(iq, fs)}
    state = {}

    def without():
        ra = C.decode(iq, fs, found[A])
        state['si_a'] = L.si_messages(iq, fs, found[A], ra[0], O.MESSAGES, O.WINDOW, carrier_hz=700e6)
        state['ka'] = L.known_cell(fs, found[A], *ra, carrier_hz=700e6)
        state['rb'] = C.decode(iq, fs, found[B], known=[state['ka']])
        si = L.si_messages(iq, fs, found[B], state['rb'][0], O.MESSAGES, O.WINDOW, carrier_hz=700e6,
                           known=[state['ka']])
        return (state['si_a']['decoded'] == state['si_a']['tried'] >= 12 and si['decoded'] <= si['tried'] // 4
                and si['tried'] >= 12), (counts(state['si_a']), counts(si))
    expect("two sectors, the second 6 dB down: the first known without its SI, the second's SI copies fail",
           without)

    def with_si():
        L.known_si(state['ka'], state['si_a'])
        si = L.si_messages(iq, fs, found[B], state['rb'][0], O.MESSAGES, O.WINDOW, carrier_hz=700e6,
                           known=[state['ka']])
        return (si['decoded'] == si['tried'] >= 12
                and all(b['hex'] == hb[b['si']] for b in si['blocks'])), counts(si)
    expect("the first's SI added (known_si): every SI copy of the second alone, its own bytes", with_si)


def check_dci_sums():
    iq, fs, _sib1, hexes = O.capture(-13.0, n_frames=36)

    def summed():
        _pb, _s, si = O.read(iq, fs)
        by_sum = [b for b in si['blocks'] if 'combined' in b]
        wrong = ([b for b in si['blocks'] if b.get('hex') and b['hex'] != hexes[b['si']]]
                 + [c for c in si['combined'] if c['hex'] != hexes[c['si']]])
        ok = len(by_sum) >= 4 and not wrong and (any(b.get('hex') for b in by_sum) or si['combined'])
        return ok, (counts(si), len(wrong))
    expect("-13 dB: SI DCIs found by summing their windows' place, every block's bytes the message's", summed)


def main():
    check_sectors()
    check_dci_sums()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} ok, {len(failed)} FAIL of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
