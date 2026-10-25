"""Roadmap Round 28.5: cancelling a stronger sector's known transmissions before a weaker one is
read (``iqnr_phy.known_grid``, ``fit_known``, ``cancel``, ``pdcch_codeword``, ``codeword_score``;
``iqnr.type0_pdcch(known=)``, ``iqnr.known_transmissions``, ``iqnr_decode.own_decode_cells``).

Synthetic only, with made-up values (PCIs 123 and 321, SSB at 600.15 MHz, random payloads). Two
sectors of one site: the same frame timing and carrier, SSB indices 0 and 1, so the weaker
one's Type0 occasion is the stronger one's SIB1 slot, and both send SIB1 there on the same RBs
(34 RBs at MCS 0), the weaker 13 dB down; a 700 Hz offset that is the receiver's, added once to
the sum. The real-air checks (the second and third sectors of one site read under the first) are
in the ops folder, not here. What the checks pin:

- ``fit_known`` follows a known grid through a delay slope, a frequency and gains that move
  over the slot: what it leaves of it is 30 dB down;
- ``codeword_score``: about 0 +/- 1 for soft bits that do not hold the codeword, high for ones
  that do;
- at 15 dB: without cancellation the weaker cell's SIB1 does not decode; with the stronger
  one's transmissions (``known_transmissions``) cancelled, every copy decodes alone, with the
  bytes sent;
- at 6 dB no SIB1 DCI of the weaker cell decodes alone even after cancelling: from candidates
  summed over the occasions the DCI is found, held in exactly the occasions it was sent in,
  and SIB1 decodes from those summed;
- ``own_decode_cells``: the weaker cell is read at the stronger one's offset (``cfo_from``) with
  its blocks cancelled, and gives its own SIB1 bytes.

Run:  PYTHONPATH=tools python tools/tests/test_iqnr_cancel.py   (about a minute)
"""

import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import nr_synth  # noqa: E402
from fm_receiver import iqnr, iqnr_decode, iqnr_phy as P  # noqa: E402

STRONG, WEAK = 123, 321
FS = 20e6
CENTRE = 600.0e6
SFN0 = 517
CFO = 700.0
WEAK_DB = -13.0
MIB = dict(sfn=SFN0, half_frame=0, scs_common_khz=15, ssb_subcarrier_offset=22, dmrs_typeA_position=2,
           coreset0_index=8, search_space0_index=2, cell_barred=False, intra_freq_reselection='allowed')
SIB1 = dict(rb_start=0, n_rbs=34, mcs=0, tdra=0)
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


def sector(pci, ssb_index, data, slot_of, dur_ms):
    """One sector, noiseless and with no offset: SIB1 every 40 ms in slot ``slot_of``."""
    cs = P.coreset0(MIB)
    al = P.pdsch_alloc(SIB1, 2, 48)
    setup = P.dlsch_setup(al['tbs'], al['rate'])
    sent = []

    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), pci, idx)

    def res(frame_no, slot):
        sfn = (SFN0 + frame_no) % 1024
        if sfn % 4 != 0 or slot != slot_of:
            return []
        g = P.pdsch_grid(data, setup, al, pci, slot, 0, 48)
        a = dci_bits(48, SIB1['rb_start'], SIB1['n_rbs'])
        g[:2] = P.coreset_grid(48, 2, pci, slot, 0, [(4, 0, P.encode_pdcch(a, P.SI_RNTI, pci, 4))])
        sent.append(sfn)
        return [(l, cs['k0'], g[l]) for l in range(14)]
    iq, _fs, _truth = nr_synth.synth_nr(pci=pci, scs_khz=15, case='A', l_max=4, ssb_sent=(ssb_index,), period_ms=20,
                                        n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=dur_ms, fs=FS, seed=3,
                                        snr_db=float('inf'), pbch=pbch, res=res, rf_hz=CENTRE)
    return iq, sent


def site(snr_db, dur_ms):
    """Both sectors summed, the receiver's offset and noise added once; the two cells as
    ``ssb_search`` would list them (the weaker one's MIB is the stronger one's at SSB index 1)."""
    cs = P.coreset0(MIB)
    occ0 = P.type0_occasion(MIB['search_space0_index'], 0, cs['n_symb'])
    occ1 = P.type0_occasion(MIB['search_space0_index'], 1, cs['n_symb'])
    assert occ0['n0'] + 1 == occ1['n0'] and occ0['parity'] == occ1['parity']
    rng = np.random.default_rng(7)
    n_bytes = P.pdsch_alloc(SIB1, 2, 48)['tbs'] // 8
    data_s = bytes(rng.integers(1, 256, n_bytes, dtype=np.uint8))
    data_w = bytes(rng.integers(1, 256, n_bytes, dtype=np.uint8))
    s, sent = sector(STRONG, 0, data_s, occ0['n0'] + 1, dur_ms)
    w, _sent = sector(WEAK, 1, data_w, occ1['n0'], dur_ms)
    x = (s + 10 ** (WEAK_DB / 20) * w) * np.exp(2j * np.pi * CFO * np.arange(len(s)) / FS)
    x = x + (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * np.sqrt(
        np.mean(np.abs(x) ** 2) * 10 ** (-snr_db / 10) / 2)
    strong = next(c for c in iqnr.ssb_search(x, FS, CENTRE, mib=True) if c['pci'] == STRONG)
    m = strong['mib']
    step = (iqnr._symbol_start(iqnr._CASE_A[1]) - iqnr._symbol_start(iqnr._CASE_A[0])) / iqnr._TS_RATE
    weak = dict(strong, pci=WEAK, cfo_hz=strong['cfo_hz'] + 90.0,       # a weak SSB's offset is worse
                relative_power_db=WEAK_DB, mib=dict(m, ssb_index=1, ssb_s=m['ssb_s'] + step))
    return x, strong, weak, data_s, data_w, sent


def blocks(got, data):
    return [((d.get('pdsch') or {}).get('hex') == data.hex(), (d.get('pdsch') or {}).get('copies'))
            for d in got['dcis'] if (d.get('pdsch') or {}).get('hex')]


# ----- the pieces ---------------------------------------------------------------------------

def check_pieces():
    rng = np.random.default_rng(4)

    def fit():
        al = P.pdsch_alloc(SIB1, 2, 48)
        setup = P.dlsch_setup(al['tbs'], al['rate'])
        q = P.pdsch_grid(bytes(rng.integers(0, 256, al['tbs'] // 8, dtype=np.uint8)), setup, al, STRONG, 3, 0, 48)
        k = np.arange(576)[None, :]
        l = np.arange(14)[:, None]
        h = (0.8 * np.exp(1j * (0.03 * k + 2 * np.pi * 120.0 * l * P._T_SYMBOL + 0.5))
             * (1 + 0.02 * (l - 7)) * np.exp(0.2j * np.cos(k / 40.0)))
        other = (rng.standard_normal(q.shape) + 1j * rng.standard_normal(q.shape)) * 0.05 * (q != 0)
        g = h * q + other
        sy = list(range(al['S'], al['S'] + al['L']))
        left = P.cancel(g, [(q, [(sy, al['prbs'])])]) - other
        db = 10 * np.log10(np.sum(np.abs(left) ** 2) / np.sum(np.abs(h * q) ** 2))
        return db < -30, round(float(db), 1)
    expect("fit_known follows a slope, 120 Hz and gains moving over the slot: 30 dB of it cancelled", fit)

    def score():
        a = dci_bits(48, 0, 34)
        signs = P.pdcch_codeword(a, P.SI_RNTI, 4)
        noise = [P.codeword_score(rng.standard_normal(len(signs)), signs) for _ in range(400)]
        held = P.codeword_score(signs * 0.3 + rng.standard_normal(len(signs)), signs)
        return (abs(np.mean(noise)) < 0.2 and 0.85 < np.std(noise) < 1.15 and held > 5), (
            round(float(np.mean(noise)), 2), round(float(np.std(noise)), 2), round(held, 1))
    expect("codeword_score: about 0 +/- 1 without the codeword, over 5 with it at -10 dB per bit", score)


# ----- end to end ---------------------------------------------------------------------------

def check_end_to_end():
    def strong_signal():
        x, strong, weak, data_s, data_w, sent = site(15.0, 100)
        got_s = iqnr.type0_pdcch(x, FS, CENTRE, strong, pdsch=True)
        known = iqnr.known_transmissions(got_s, strong)
        apart = iqnr.type0_pdcch(x, FS, CENTRE, weak, pdsch=True)
        clean = iqnr.type0_pdcch(x, FS, CENTRE, weak, pdsch=True, known=known)
        ok_s = blocks(got_s, data_s)
        return (len(ok_s) == len(sent) >= 3 and all(o for o, _n in ok_s) and sorted(known) == sorted(
            (d['sfn'], d['slot']) for d in got_s['dcis'])
                and not blocks(apart, data_w) and clean['cancelled'] == len(sent)
                and blocks(clean, data_w) == [(True, 1)] * len(sent)), (sent, ok_s, blocks(apart, data_w),
                                                                       blocks(clean, data_w), clean['cancelled'])
    expect("15 dB: the weaker sector's SIB1 decodes only with the stronger one's cancelled, every copy alone",
           strong_signal)

    def weak_signal():
        x, strong, weak, data_s, data_w, sent = site(6.0, 300)
        known = iqnr.known_transmissions(iqnr.type0_pdcch(x, FS, CENTRE, strong, pdsch=True), strong)
        got = iqnr.type0_pdcch(x, FS, CENTRE, weak, pdsch=True, known=known)
        held = sorted(d['sfn'] for d in got['dcis'] if 'summed' in d)
        alone = [d for d in got['dcis'] if 'summed' not in d and d.get('sii') == 0]
        ok = blocks(got, data_w)
        s = got['summed'] or {}
        return (not alone and s.get('from') == 'summed' and held == sorted(sent) and ok
                and all(o and n >= 2 for o, n in ok)), (sent, held, len(alone), ok, {k: v for k, v in s.items() if k != 'scores'})
    expect("6 dB: no DCI alone; summed over occasions it is found, held where sent, and SIB1 decodes summed",
           weak_signal)

    def cells():
        x, strong, weak, data_s, data_w, sent = site(15.0, 100)
        folder = tempfile.mkdtemp(prefix='iqnr-cancel-')
        owns = iqnr_decode.own_decode_cells(x, FS, CENTRE, [strong, weak], tools=os.path.join(folder, 'no-tools'))
        a, b = owns
        return (a['sib1_hex'] == data_s.hex() and a['cfo_from'] == 'own SSB' and b['sib1_hex'] == data_w.hex()
                and b['cfo_hz'] == strong['cfo_hz'] and b['cfo_from'].startswith('strongest on this SSB frequency')
                and b['cancelled'][0] == len(sent)), ({k: a.get(k) for k in ('cfo_from', 'blocks')},
                                                      {k: b.get(k) for k in ('cfo_hz', 'cfo_from', 'blocks', 'cancelled')})
    expect("own_decode_cells: the weaker cell read at the stronger one's offset, its SIB1 bytes", cells)


def main():
    for group in (check_pieces, check_end_to_end):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
