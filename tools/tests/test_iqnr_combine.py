"""Roadmap Round 28.4: soft combining of a broadcast block's repeats (``iqnr_phy.SoftCombiner``,
``iqnr.type0_pdcch(pdsch=True, combine=True)``, ``iqnr.other_si(combine=True)``).

Synthetic only, with made-up values (PCI 123, SSB at 600.15 MHz, random payloads, an invented SI
configuration). The block is the barred cell's kind: one RB at MCS 9 (code rate 0.66), its
copies at redundancy versions 0, 2, 3, 1 on RBs that move every time, as real air sends it. The
real-air checks (the barred cell's SIB1 in every 160 ms period, the gain measured against its known
codeword; the second sector held back by the first's coherent repeat) are in the ops folder,
not here. What the checks pin:

- four copies at RVs 0, 2, 3, 1 on four RB allocations, at 2 dB per RE: none decodes alone, the
  four summed always do (a single copy needs about 7 dB);
- a parity-only copy (RV 1, at 20 dB) gives no block alone and is kept: with it, an RV 0 copy
  too weak alone decodes, from 2 copies;
- copies are summed within their group first: an older content's copies kept under the same key
  do not stop the new one decoding from its own period, and a pass empties the key;
- copies of a different transport block size never meet;
- end to end: a synthetic cell whose SIB1 copies (RVs cycling, RBs moving) and two other-SI
  copies in one SI window are each too weak alone: with ``combine`` both decode, with the bytes
  sent, and without it neither does.

Run:  PYTHONPATH=tools python tools/tests/test_iqnr_combine.py   (about 10 s)
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import nr_synth  # noqa: E402
from fm_receiver import iqnr, iqnr_phy as P  # noqa: E402

PCI = 123
FS = 20e6
CENTRE = 600.0e6
SFN0 = 517
MIB = dict(sfn=SFN0, half_frame=0, scs_common_khz=15, ssb_subcarrier_offset=22, dmrs_typeA_position=2,
           coreset0_index=8, search_space0_index=2, cell_barred=False, intra_freq_reselection='allowed')
#: An invented other-SI configuration (what ``iqnr_decode.si_config`` gives from a SIB1).
CONFIG = {'offset_to_point_a': 14, 'tdra': [(0, 'A', 2, 12)], 'window_slots': 20,
          'periods_rf': [8], 'search': {'counts': ((1, 0), (2, 4), (4, 2), (8, 0), (16, 0)),
                                        'first_symbols': (0,), 'period_slots': 1, 'offset_slots': 0}}
#: (RV, first RB) of successive copies: the barred cell's pattern, RBs moving every time.
COPIES = ((0, 16), (2, 43), (3, 42), (1, 15), (0, 14))
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


def dci_bits(n_rb, rb_start, n_rbs, tdra=0, mcs=0, rv=0, sii=0):
    """A DCI 1_0 SI-RNTI payload (38.212 7.3.1.2.1) with these fields; the RIV of 38.214 5.1.2.2.2."""
    riv = n_rb * (n_rbs - 1) + rb_start if n_rbs - 1 <= n_rb // 2 else n_rb * (n_rb - n_rbs + 1) + (n_rb - 1 - rb_start)
    width = (n_rb * (n_rb + 1) // 2 - 1).bit_length()
    fields = ((riv, width), (tdra, 4), (0, 1), (mcs, 5), (rv, 2), (sii, 1), (0, 15))
    return np.array([(v >> (w - 1 - i)) & 1 for v, w in fields for i in range(w)], dtype=np.uint8)


def noisy(g, snr_db, rng):
    return g + (rng.standard_normal(g.shape) + 1j * rng.standard_normal(g.shape)) * np.sqrt(10 ** (-snr_db / 10) / 2)


def copy_soft(data, rv, rb, snr_db, rng, n_rbs=1, mcs=9):
    """One copy's soft buffers and coding, through the slot grid (DM-RS from CORESET 0)."""
    al = P.pdsch_alloc(dict(mcs=mcs, tdra=0, rb_start=rb, n_rbs=n_rbs), 2, 48)
    setup = P.dlsch_setup(al['tbs'], al['rate'])
    g = P.pdsch_grid(data, setup, al, PCI, 5, rv, 48)
    llr, _snr, _slope = P.pdsch_llrs(noisy(g, snr_db, rng), PCI, 5, al)
    return P.dlsch_soft(llr, setup, rv, al['qm']), setup


def block_bytes(rng, n_rbs=1, mcs=9):
    al = P.pdsch_alloc(dict(mcs=mcs, tdra=0, rb_start=0, n_rbs=n_rbs), 2, 48)
    return bytes(rng.integers(1, 256, al['tbs'] // 8, dtype=np.uint8))


# ----- the combiner -------------------------------------------------------------------------

def check_combiner():
    rng = np.random.default_rng(5)

    def four():
        alone = summed = 0
        trials = 6
        for _ in range(trials):
            data = block_bytes(rng)
            comb = P.SoftCombiner()
            got = (None, 0)
            for rv, rb in COPIES[:4]:
                soft, setup = copy_soft(data, rv, rb, 2.0, rng)
                alone += P.dlsch_decode(soft, setup) is not None
                if got[0] is None:
                    got = comb.add('sib1', 0, soft, setup)
            summed += got[0] == data and got[1] >= 2
        return alone == 0 and summed == trials, (alone, summed)
    expect("RVs 0, 2, 3, 1 on four RB allocations at 2 dB: none alone, summed always", four)

    def parity_first():
        data = block_bytes(rng)
        comb = P.SoftCombiner()
        soft1, setup = copy_soft(data, 1, 30, 20.0, rng)
        first = comb.add('sib1', 0, soft1, setup)
        weak = None
        for _ in range(10):                  # an RV 0 copy too weak to decode alone
            soft0, _s = copy_soft(data, 0, 7, 3.0, rng)
            if P.dlsch_decode(soft0, setup) is None:
                weak = soft0
                break
        second = comb.add('sib1', 0, weak, setup) if weak is not None else (None, 0)
        return first == (None, 0) and second == (data, 2), (first, second[1])
    expect("a parity-only copy (RV 1, 20 dB) gives no block alone; kept, it lifts a weak RV 0", parity_first)

    def groups():
        old, new = block_bytes(rng), block_bytes(rng)
        comb = P.SoftCombiner()
        kept = [comb.add('sib1', 7, *copy_soft(old, rv, rb, -3.0, rng)) for rv, rb in COPIES[:2]]
        got = (None, 0)
        for rv, rb in COPIES[:4]:
            soft, setup = copy_soft(new, rv, rb, 2.0, rng)
            got = comb.add('sib1', 8, soft, setup)
            if got[0] is not None:
                break
        return (all(k == (None, 0) for k in kept) and got[0] == new and 2 <= got[1] <= 4
                and not comb._kept), (kept, got[1], len(comb._kept))
    expect("summed within the period first: an older content's copies do not block the new; a pass empties", groups)

    def sizes():
        data = block_bytes(rng)
        other = block_bytes(rng, n_rbs=2)
        comb = P.SoftCombiner()
        soft_a, setup_a = copy_soft(data, 0, 3, 2.0, rng)
        soft_b, setup_b = copy_soft(other, 2, 9, 2.0, rng, n_rbs=2)
        comb.add('sib1', 0, soft_a, setup_a)
        comb.add('sib1', 0, soft_b, setup_b)
        sums = sorted(len(v) for v in comb._kept.values())
        return setup_a['tbs'] != setup_b['tbs'] and sums == [1, 1], (setup_a['tbs'], setup_b['tbs'], sums)
    expect("copies of another transport block size are kept apart", sizes)


# ----- end to end ---------------------------------------------------------------------------

def synth_cell(snr_db):
    rng = np.random.default_rng(11)
    cs = P.coreset0(MIB)
    occ = P.type0_occasion(MIB['search_space0_index'], 0, cs['n_symb'])
    data1 = block_bytes(rng)
    data2 = block_bytes(rng)
    k_ref = 12 * (CONFIG['offset_to_point_a'] - cs['offset_rb'])
    sent = {'sib1': 0, 'osi': []}

    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), PCI, idx)

    def pdsch(data, rv, rb, slot, sii, kr):
        a = dci_bits(48, rb, 1, mcs=9, rv=rv, sii=sii)
        al = P.pdsch_alloc(dict(mcs=9, tdra=0, rb_start=rb, n_rbs=1), 2, 48)
        setup = P.dlsch_setup(al['tbs'], al['rate'])
        g = P.pdsch_grid(data, setup, al, PCI, slot, rv, 48, k_ref=kr)
        level, cce = (4, 0) if not sii else (2, 4)
        g[:2] = P.coreset_grid(48, 2, PCI, slot, 0, [(level, cce, P.encode_pdcch(a, P.SI_RNTI, PCI, level))])
        return g

    def res(frame_no, slot):
        sfn = (SFN0 + frame_no) % 1024
        if sfn % 2 == occ['parity'] and slot == occ['n0'] + 1:
            rv, rb = COPIES[sent['sib1'] % len(COPIES)]
            g = pdsch(data1, rv, rb, slot, 0, 0)
            sent['sib1'] += 1
        elif sfn % 8 in (0, 1) and slot == 4:          # two copies in the SI window, RVs 0 and 2
            rv, rb = ((0, 4), (2, 20))[sfn % 8]
            g = pdsch(data2, rv, rb, slot, 1, k_ref)
            sent['osi'].append((sfn, slot))
        else:
            return []
        return [(l, cs['k0'], g[l]) for l in range(14)]
    iq, _fs, _truth = nr_synth.synth_nr(pci=PCI, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20,
                                        n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=100, fs=FS, seed=3,
                                        snr_db=snr_db, cfo_hz=700.0, pbch=pbch, res=res, rf_hz=CENTRE)
    return iq, data1, data2, sent


def check_end_to_end():
    iq, data1, data2, sent = synth_cell(2.0)
    holder = {}

    def sib1():
        cell = next((e for e in iqnr.ssb_search(iq, FS, CENTRE, mib=True) if e['pci'] == PCI), None)
        if cell is None or cell.get('mib') is None:
            return False, 'no MIB'
        holder['cell'] = cell
        apart = iqnr.type0_pdcch(iq, FS, CENTRE, cell, pdsch=True, combine=False)
        summed = iqnr.type0_pdcch(iq, FS, CENTRE, cell, pdsch=True)
        alone = [d['pdsch']['hex'] for d in apart['dcis'] if d.get('pdsch') and d['pdsch']['hex']]
        got = [(d['pdsch']['hex'], d['pdsch']['copies']) for d in summed['dcis'] if d.get('pdsch') and d['pdsch']['hex']]
        return (4 <= len(apart['dcis']) <= sent['sib1'] and not alone and got
                and all(h == data1.hex() and n >= 2 for h, n in got)), (sent['sib1'], len(apart['dcis']), alone, got)
    expect("type0_pdcch: SIB1 copies too weak alone (RVs cycling, RBs moving) decode summed", sib1)

    def other():
        cell = holder.get('cell')
        if cell is None:
            return False, 'no cell'
        apart = iqnr.other_si(iq, FS, CENTRE, cell, CONFIG, combine=False)
        summed = iqnr.other_si(iq, FS, CENTRE, cell, CONFIG)
        alone = [b for b in apart['blocks'] if (b['pdsch'] or {}).get('hex')]
        got = [((b['sfn'], b['slot']), b['pdsch']['hex'], b['pdsch']['copies']) for b in summed['blocks']
               if (b['pdsch'] or {}).get('hex')]
        return (len(summed['blocks']) == 2 == len(sent['osi']) and not alone
                and [(k, h, n) for k, h, n in got] == [(sent['osi'][1], data2.hex(), 2)]), (sent['osi'], alone, got)
    expect("other_si: two copies in one SI window, each too weak alone, decode summed", other)


def main():
    for group in (check_combiner, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
