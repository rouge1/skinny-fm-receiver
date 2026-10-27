"""Roadmap Round 28.7: a weaker sector's other SI read with the stronger one's bytes as a hypothesis
(``iqnr_phy.known_channel``, ``pdsch_llrs_known``, ``shared_score``; ``iqnr.other_si``'s ``shared``
and ``dci_from``; ``iqnr_decode._shared_matches``).

Synthetic only, with made-up values (PCIs 123 and 321, SSB at 600.15 MHz, random payloads). Two
sectors of one site send an SI message twice a window, in the same slots on the same 6 RBs at MCS
0, the weaker one under the stronger; the stronger one is decoded and cancelled first, as
``own_decode_cells`` does. The real-air checks (the second and third sectors of one site) are in
the ops folder, not here. What the checks pin:

- ``known_channel`` follows a slope, a frequency and gains moving over the slot from a known grid;
  ``shared_score``'s nulls are about 0 +/- 1, its score high where the codeword is sent;
- the same bytes in both sectors, the weaker one at a level where the DM-RS alone give a channel
  too poor to decode a copy (the old code: none of 12): with the channel fitted to the stronger
  one's bytes copies decode alone (6 of 12), each the bytes sent, and every copy scores high;
- the weaker sector sending other bytes: it scores about 0, and its blocks that decode are its own
  bytes, never the hypothesis';
- the weaker sector's PDCCH not sent: its PDSCH is read on the stronger one's DCI (``dci_from``);
- ``_shared_matches``: a message matches only when its summed score clears both the floor and its
  nulls; one decoded block of it, and it is not tested.

Run:  PYTHONPATH=tools python tools/tests/test_iqnr_shared.py   (about two minutes)
"""

import os
import sys

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
MIB = dict(sfn=SFN0, half_frame=0, scs_common_khz=15, ssb_subcarrier_offset=22, dmrs_typeA_position=2,
           coreset0_index=8, search_space0_index=2, cell_barred=False, intra_freq_reselection='allowed')
#: An invented other-SI configuration (what ``iqnr_decode.si_config`` gives from a SIB1).
CONFIG = {'offset_to_point_a': 14, 'tdra': [(0, 'A', 2, 12)], 'window_slots': 20,
          'periods_rf': [8], 'search': {'counts': ((1, 0), (2, 4), (4, 2), (8, 0), (16, 0)),
                                        'first_symbols': (0,), 'period_slots': 1, 'offset_slots': 0}}
SI = dict(rb_start=0, n_rbs=6, mcs=0, tdra=0)
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


def sector(pci, data, pdcch=True):
    """One sector, noiseless and with no offset: the SI message in slot 4 of frames 0 and 1 of
    every 8 (one window), with its DCI at AL 4 (none when ``pdcch`` is False)."""
    cs = P.coreset0(MIB)
    k_ref = 12 * (CONFIG['offset_to_point_a'] - cs['offset_rb'])
    al = P.pdsch_alloc(SI, 2, 48, tdra=CONFIG['tdra'])
    setup = P.dlsch_setup(al['tbs'], al['rate'])
    sent = []

    def pbch(frame_no, half_frame, idx):
        return P.encode_pbch(dict(MIB, sfn=(SFN0 + frame_no) % 1024, half_frame=half_frame), pci, idx)

    def res(frame_no, slot):
        sfn = (SFN0 + frame_no) % 1024
        if sfn % 8 not in (0, 1) or slot != 4:
            return []
        g = P.pdsch_grid(data, setup, al, pci, slot, 0, 48, k_ref=k_ref)
        if pdcch:
            a = dci_bits(48, SI['rb_start'], SI['n_rbs'], sii=1)
            g[:2] = P.coreset_grid(48, 2, pci, slot, 0, [(4, 0, P.encode_pdcch(a, P.SI_RNTI, pci, 4))])
        sent.append((sfn, slot))
        return [(l, cs['k0'], g[l]) for l in range(14)]
    iq, _fs, _truth = nr_synth.synth_nr(pci=pci, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20,
                                        n_hf=0, bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=480, fs=FS, seed=3,
                                        snr_db=float('inf'), pbch=pbch, res=res, rf_hz=CENTRE)
    return iq, sent


def site(weak_db, snr_db, same=True, weak_pdcch=True, seed=7):
    """Both sectors summed, the receiver's offset and noise added once; the stronger one's other
    SI decoded and its blocks made ``known``; the weaker cell as ``ssb_search`` would list it."""
    rng = np.random.default_rng(seed)
    n_bytes = P.pdsch_alloc(SI, 2, 48, tdra=CONFIG['tdra'])['tbs'] // 8
    data_s = bytes(rng.integers(1, 256, n_bytes, dtype=np.uint8))
    data_w = data_s if same else bytes(rng.integers(1, 256, n_bytes, dtype=np.uint8))
    s, sent = sector(STRONG, data_s)
    w, _sent = sector(WEAK, data_w, pdcch=weak_pdcch)
    # the weaker sector's own channel: a delay and a phase the stronger one's does not have
    w = np.fft.ifft(np.fft.fft(w) * np.exp(-2j * np.pi * np.fft.fftfreq(len(w)) * 3.0) * np.exp(1.3j))
    x = (s + 10 ** (weak_db / 20) * w) * np.exp(2j * np.pi * CFO * np.arange(len(s)) / FS)
    x = x + (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * np.sqrt(
        np.mean(np.abs(s) ** 2) * 10 ** (-snr_db / 10) / 2)
    strong = next(c for c in iqnr.ssb_search(x, FS, CENTRE, mib=True) if c['pci'] == STRONG)
    got_s = iqnr.other_si(x, FS, CENTRE, strong, CONFIG, combine=False)
    known = iqnr.known_transmissions(got_s, strong)
    weak = dict(strong, pci=WEAK)
    return x, strong, weak, known, data_s, data_w, sent, got_s


def read_weak(x, weak, known, hypotheses=True):
    """The weaker cell's other SI, each copy alone; with ``hypotheses`` False as the old code
    read it (no stronger cell's bytes tried)."""
    keep = iqnr._shared_si
    if not hypotheses:
        iqnr._shared_si = lambda *a, **k: []
    try:
        return iqnr.other_si(x, FS, CENTRE, weak, CONFIG, combine=False, known=known)
    finally:
        iqnr._shared_si = keep


def decoded(got):
    return [b['pdsch']['hex'] for b in got['blocks'] if (b.get('pdsch') or {}).get('hex')]


# ----- the pieces ---------------------------------------------------------------------------

def check_pieces():
    rng = np.random.default_rng(4)
    al = P.pdsch_alloc(SI, 2, 48, tdra=CONFIG['tdra'])
    setup = P.dlsch_setup(al['tbs'], al['rate'])
    data = bytes(rng.integers(0, 256, al['tbs'] // 8, dtype=np.uint8))
    q = P.pdsch_grid(data, setup, al, WEAK, 4, 0, 48)
    k = np.arange(576)[None, :]
    l = np.arange(14)[:, None]
    h = 0.5 * np.exp(1j * (0.03 * k + 2 * np.pi * 90.0 * l * P._T_SYMBOL + 0.4)) * (1 + 0.02 * (l - 7))

    def channel():
        g = h * q + (rng.standard_normal(q.shape) + 1j * rng.standard_normal(q.shape)) * 0.05
        sy = list(range(al['S'], al['S'] + al['L']))
        est, noise = P.known_channel(g, q, sy, al['prbs'])
        m = q != 0
        err = 10 * np.log10(np.sum(np.abs(est[m] - h[m]) ** 2) / np.sum(np.abs(h[m]) ** 2))
        return err < -25 and 0.5 < noise / (2 * 0.05 ** 2) < 1.5, (round(float(err), 1), noise)
    expect("known_channel: the channel from a known grid, 25 dB under it, and the noise left", channel)

    def score():
        g = h * q + (rng.standard_normal(q.shape) + 1j * rng.standard_normal(q.shape)) * 1.0
        llr = P.pdsch_llrs(g, WEAK, 4, al)[0]
        signs = 1.0 - 2.0 * P.encode_dlsch(data, setup, 0, 2, len(llr))
        held, nulls = P.shared_score(llr, signs, WEAK)
        other = bytes(rng.integers(0, 256, len(data), dtype=np.uint8))
        none, _n = P.shared_score(llr, 1.0 - 2.0 * P.encode_dlsch(other, setup, 0, 2, len(llr)), WEAK)
        return (held > 5 and abs(none) < 3.5 and len(nulls) == 16 and max(abs(v) for v in nulls) < 3.5), (
            round(held, 1), round(none, 1), [round(v, 1) for v in nulls])
    expect("shared_score: high where the codeword is sent (-9 dB per RE), its nulls and other bytes near 0", score)


# ----- end to end ---------------------------------------------------------------------------

#: The weaker sector's level and the noise (to the stronger one): low enough that its DM-RS alone
#: give too poor a channel for a copy to decode.
WEAK_DB, SNR_DB = -12.0, 8.0


def check_end_to_end():
    def same_bytes():
        x, strong, weak, known, data_s, data_w, sent, got_s = site(WEAK_DB, SNR_DB)
        old = read_weak(x, weak, known, hypotheses=False)
        new = read_weak(x, weak, known)
        scores = [b['pdsch']['shared']['score'] for b in new['blocks'] if 'shared' in (b['pdsch'] or {})]
        dec = decoded(new)
        return (len(decoded(got_s)) == len(sent) >= 8 and len(new['blocks']) == len(sent)
                and not decoded(old) and len(dec) >= len(sent) // 3
                and all(h == data_w.hex() for h in dec) and len(scores) == len(sent) and min(scores) > 4), (
            len(sent), len(decoded(got_s)), len(old['blocks']), len(decoded(old)), len(dec),
            [round(v, 1) for v in scores])
    expect("the same bytes: copies the DM-RS alone cannot read decode with the channel fitted to the "
           "stronger one's, each the bytes sent, every copy scoring", same_bytes)

    def other_bytes():
        x, strong, weak, known, data_s, data_w, sent, got_s = site(WEAK_DB + 6.0, SNR_DB, same=False)
        new = read_weak(x, weak, known)
        scores = [b['pdsch']['shared']['score'] for b in new['blocks'] if 'shared' in (b['pdsch'] or {})]
        dec = decoded(new)
        tested = iqnr_decode._shared_matches([dict(b, pdsch=dict(b['pdsch'], hex=None)) for b in new['blocks']])
        return (len(scores) == len(sent) and max(abs(v) for v in scores) < 4 and dec
                and all(h == data_w.hex() for h in dec) and tested and not any(m['matched'] for m in tested)), (
            [round(v, 1) for v in scores], len(dec), [h == data_w.hex() for h in dec], tested)
    expect("other bytes: it scores about 0 and does not match; what decodes is its own bytes", other_bytes)

    def no_pdcch():
        x, strong, weak, known, data_s, data_w, sent, got_s = site(WEAK_DB, SNR_DB, weak_pdcch=False)
        old = read_weak(x, weak, known, hypotheses=False)
        new = read_weak(x, weak, known)
        theirs = [b for b in new['blocks'] if b.get('dci_from') == STRONG]
        tested = iqnr_decode._shared_matches([dict(b, pdsch=dict(b['pdsch'], hex=None)) for b in theirs])
        after = iqnr.known_transmissions(new, weak, {})
        return (not old['blocks'] and len(theirs) == len(new['blocks']) == len(sent)
                and all(h == data_w.hex() for h in decoded(new)) and len(tested) == 1 and tested[0]['matched']
                and not after), (len(old['blocks']), len(theirs), len(decoded(new)), tested, len(after))
    expect("no PDCCH of its own: read on the stronger one's DCI (dci_from), matched, not made known", no_pdcch)


def check_matches():
    def rule():
        rng = np.random.default_rng(9)

        def row(si, score, hexed=None, bias=0.0):
            return {'si': si, 'pdsch': {'hex': hexed, 'shared': {
                'pci': STRONG, 'hex': 'ab' * 3, 'score': score,
                'nulls': list(rng.standard_normal(16) + bias)}}}
        held = [row(1, 3.0) for _ in range(9)]                       # z 9: matched
        faint = [row(2, 2.0) for _ in range(9)]                      # z 6: under the floor
        biased = [row(3, 3.0, bias=1.0) for _ in range(9)]           # z 9, nulls about 3: not twice them
        done = [row(4, 3.0) for _ in range(8)] + [row(4, 0.0, hexed='cd' * 3)]
        got = {m['si']: m for m in iqnr_decode._shared_matches(held + faint + biased + done)}
        return (got[1]['matched'] and got[1]['z'] == 9.0 and got[1]['copies'] == 9 and not got[2]['matched']
                and not got[3]['matched'] and 4 not in got), {k: (v['z'], v['null_z'], v['matched']) for k, v in got.items()}
    expect("_shared_matches: the floor and twice the nulls; a message with a block decoded is not tested", rule)


def main():
    for group in (check_pieces, check_end_to_end, check_matches):
        try:
            group()
        except Exception as exc:
            report(f"{group.__name__} did not run", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
