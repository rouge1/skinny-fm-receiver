"""LTE broadcast decoding, numpy only: the PBCH and its MIB (roadmap Round 29.1), and the
PCFICH and PDCCH that schedule SIB1 (29.2).

Written from the 3GPP specifications (TS 36.211, 36.212 and 36.331 V19, read in ETSI's PDFs,
which keep the equations the 3GPP Word files hold as pictures). No srsRAN code is used: srsRAN
is AGPL and stays outside this repo as the referee (``pdsch_ue``'s MIBs). Broadcast only.

The PBCH (36.212 5.3.1, 36.211 6.6) carries the 24-bit MIB once every 40 ms. Transmitter, in
order:

1. CRC 16 (36.212 5.1.1, the polynomial NR's CRC 16 shares), its bits XORed with a mask that
   says how many CRS antenna ports the cell has (table 5.3.1.1-1: 1 port all zeros, 2 ports all
   ones, 4 ports 0101...).
2. The tail-biting convolutional code (5.1.3.1): constraint length 7, rate 1/3, generators
   133, 171 and 165 (octal, figure 5.1.3-1), the register starting as the last six input bits.
3. Rate matching (5.1.4.2): each of the three streams through the 32-column sub-block
   interleaver (dummy bits first, columns permuted by table 5.1.4-2, read column by column),
   the three concatenated into a circular buffer, and E = 1920 bits read from it, skipping the
   dummies: 40 bits x 3 = 120 coded bits, each sent 16 times.
4. Scrambling (36.211 6.6.1) by the Gold sequence with c_init = PCI, restarted in frames whose
   SFN is a multiple of 4: frame SFN mod 4 = i carries bits 480 i to 480 i + 479. So one frame
   holds every coded bit four times, and which quarter it is gives the SFN's two low bits.
5. QPSK, then transmit diversity (6.3.3.3, 6.3.4.3) on 2 or 4 ports: SFBC, y0(2i) = x0,
   y1(2i) = -x1*, y0(2i+1) = x1, y1(2i+1) = x0* (over sqrt 2); with 4 ports the pairs 4i, 4i+1
   go on ports 0 and 2 and 4i+2, 4i+3 on ports 1 and 3.
6. The REs (6.6.4): the central 72 subcarriers (k = 6 N_RB - 36 + k', DC not counted), symbols
   0 to 3 of slot 1 of subframe 0, k first, leaving out the CRS REs of all four ports whatever
   the cell has: 240 REs, 480 bits a frame.

The CRS (6.10.1): r(m) from the Gold sequence with c_init = 2^10 (7 (n_s + 1) + l + 1)
(2 PCI + 1) + 2 PCI + N_CP, on k = 6 m + (v + PCI mod 6) mod 6, m' = m + 110 - N_RB; ports 0
and 1 in symbols 0 and 4 of each slot, ports 2 and 3 in symbol 1. In the central 72 subcarriers
m' does not depend on N_RB, so the PBCH's channel needs no bandwidth.

LTE upconverts each antenna port's signal continuously (36.211 6.13, figure 6.13-1): there is
no per-symbol phase term as NR's 38.211 5.4.

``decode_pbch_frame`` takes one frame's PBCH symbols and the channel of each port, and tries 1,
2 and 4 ports. Before any Viterbi run, which quarter of the scrambling the frame carries is
found from the repeats: after descrambling with the right quarter, the four copies of each coded
bit in the frame agree (``quarter_scores``). Then the bits are rate-recovered, Viterbi decoded
(wrap-around) and the CRC with each port mask says the port count.

The control region (29.2): REGs (36.211 6.2.4) of four symbol REs; the PCFICH's four in symbol 0
at k = 6 (PCI mod 2 N_RB) + floor(i N_RB / 2) 6 (6.7.4), its 32 bits scrambled with c_init =
(subframe + 1)(2 PCI + 1) 2^9 + PCI; the PHICH's 3 REGs per group, ceil(N_g N_RB / 8) groups
(6.9, 6.9.3); the rest, k first then l, take the PDCCH's symbol quadruplets after the
sub-block interleaver (36.212 5.1.4.2.1) and a cyclic shift by the PCI (6.8.5); the PDCCH's
bits are scrambled with c_init = subframe 2^9 + PCI (6.8.2), 72 to a CCE. A DCI is CRC 16 whose
last 16 bits are XORed with the RNTI (36.212 5.3.3.2), then the PBCH's code and rate matching.

Wrong turns
-----------
- A level-8 candidate passed the CRC where the cell sent level 4 (Band 13, 2026-10-25): the
  circular buffer read on puts the level-4 bits first in the level-8 ones. Level 4 is tried
  first, and level 8 only when CCEs 4-7 carry the continuation.
"""

from fractions import Fraction

import numpy as np

from .iqnr import _cut
from .iqnr_ldpc import crc as _crc
from .iqnr_phy import _gold

#: The MIB and the PBCH (36.212 5.3.1, 36.211 6.6.1).
A_MIB = 24
K_BCH = A_MIB + 16
E_PBCH = 1920                              # normal cyclic prefix, all four frames
E_FRAME = 480
N_PBCH_RE = 240
_N_RB_MAX = 110

#: PBCH CRC masks by antenna port count (36.212 table 5.3.1.1-1).
PORT_MASKS = {1: np.zeros(16, dtype=np.uint8), 2: np.ones(16, dtype=np.uint8),
              4: np.tile(np.array([0, 1], dtype=np.uint8), 8)}

#: Convolutional code generators (36.212 figure 5.1.3-1), octal 133, 171, 165: bit 6 is the
#: input c_k, bit 5 - j the register s_j.
_GENERATORS = (0o133, 0o171, 0o165)

#: Sub-block interleaver column permutation for convolutional codes (36.212 table 5.1.4-2).
_P_CC = (1, 17, 9, 25, 5, 21, 13, 29, 3, 19, 11, 27, 7, 23, 15, 31,
         0, 16, 8, 24, 4, 20, 12, 28, 2, 18, 10, 26, 6, 22, 14, 30)

#: MIB fields in order (36.331 MasterInformationBlock): name, bits.
_MIB_FIELDS = (('dl_bandwidth', 3), ('phich_duration', 1), ('phich_resource', 2), ('sfn_msb', 8),
               ('sib1_br', 5), ('si_unchanged_br', 1), ('part_earfcn', 3), ('spare', 1))
DL_BANDWIDTH_RB = (6, 15, 25, 50, 75, 100)
PHICH_RESOURCE = ('1/6', '1/2', '1', '2')


# ----- the CRS -------------------------------------------------------------------------------

def crs_values(pci, ns, l, n_rb, ncp=1):
    """r(m') of the CRS in symbol ``l`` of slot ``ns`` for ``m`` = 0 .. 2 n_rb - 1 (36.211 6.10.1.1)."""
    c_init = (1 << 10) * (7 * (ns + 1) + l + 1) * (2 * pci + 1) + 2 * pci + ncp
    c = _gold(c_init, 4 * _N_RB_MAX).astype(np.float64)
    m = np.arange(2 * n_rb) + _N_RB_MAX - n_rb
    return ((1 - 2 * c[2 * m]) + 1j * (1 - 2 * c[2 * m + 1])) / np.sqrt(2.0)


def crs_symbols(port):
    """The symbols of a slot that hold port ``port``'s CRS (normal CP)."""
    return (0, 4) if port in (0, 1) else (1,)


def crs_shift(port, l, ns):
    """v of 36.211 6.10.1.2 (before v_shift)."""
    if port == 0:
        return 0 if l == 0 else 3
    if port == 1:
        return 3 if l == 0 else 0
    return 3 * (ns % 2) if port == 2 else 3 + 3 * (ns % 2)


def central_crs(pci, ns, l, port):
    """``(k', values)``: port ``port``'s CRS in symbol ``l`` of slot ``ns`` on the central 72
    subcarriers (k' = 0..71 from 36 below DC), which do not depend on the bandwidth."""
    shift = (crs_shift(port, l, ns) + pci % 6) % 6
    kp = np.arange(shift, 72, 6)
    r = crs_values(pci, ns, l, 6)                      # n_rb 6: m' = m + 104, the central 12
    return kp, r[(kp - shift) // 6]


# ----- the PBCH resource elements -------------------------------------------------------------

def pbch_res(pci):
    """``(l, k')`` of the 240 PBCH REs of a frame in mapping order: symbols 0..3 of slot 1 of
    subframe 0, k' = 0..71 first, the CRS REs of all four ports left out."""
    v = pci % 3
    out = []
    for l in range(4):
        for k in range(72):
            if l in (0, 1) and k % 3 == v:
                continue
            out.append((l, k))
    return np.array(out)


# ----- the convolutional code ----------------------------------------------------------------

def _tap_bits(g):
    return np.array([(g >> (6 - i)) & 1 for i in range(7)], dtype=np.uint8)   # c_k, s0..s5


_TAPS = np.stack([_tap_bits(g) for g in _GENERATORS])


def tbcc_encode(c):
    """The three streams d(0), d(1), d(2) of the tail-biting code of ``c`` (36.212 5.1.3.1)."""
    c = np.asarray(c, dtype=np.uint8)
    k = len(c)
    s = [int(c[k - 1 - i]) for i in range(6)]          # s_i = c_(K-1-i)
    out = np.zeros((3, k), dtype=np.uint8)
    for n in range(k):
        reg = np.array([c[n]] + s, dtype=np.uint8)
        out[:, n] = (_TAPS @ reg) & 1
        s = [int(c[n])] + s[:5]
    return out


def _trellis():
    """For each state (bit j = s_j) and input: the next state and the three output bits."""
    nxt = np.zeros((64, 2), dtype=np.int64)
    outs = np.zeros((64, 2, 3), dtype=np.int8)
    for st in range(64):
        s = [(st >> j) & 1 for j in range(6)]
        for b in (0, 1):
            reg = np.array([b] + s, dtype=np.uint8)
            outs[st, b] = (_TAPS @ reg) & 1
            nxt[st, b] = ((st << 1) | b) & 63
    return nxt, outs


_NEXT, _OUTS = _trellis()


def viterbi_tbcc(llr):
    """The K bits of a tail-biting code from its soft streams ``llr`` (3 x K, positive means
    0): wrap-around Viterbi over three turns, the middle turn kept."""
    llr = np.asarray(llr, dtype=np.float64)
    k = llr.shape[1]
    sign = 1.0 - 2.0 * _OUTS                           # (64, 2, 3): +1 for a 0 bit
    metric = np.zeros(64)
    seq = np.concatenate((llr, llr, llr), axis=1)
    back = np.zeros((3 * k, 64), dtype=np.int64)
    ns = np.arange(64)
    b = ns & 1                                         # the input that leads to state ns
    p0, p1 = ns >> 1, (ns >> 1) | 32                   # its two predecessors
    for n in range(3 * k):
        bm = np.einsum('sbi,i->sb', sign, seq[:, n])
        m0 = metric[p0] + bm[p0, b]
        m1 = metric[p1] + bm[p1, b]
        pick = m1 > m0
        new = np.where(pick, m1, m0)
        back[n] = np.where(pick, p1, p0)
        metric = new - new.max()
    st = int(np.argmax(metric))
    bits = np.zeros(3 * k, dtype=np.uint8)
    for n in range(3 * k - 1, -1, -1):
        bits[n] = st & 1
        st = int(back[n, st])
    return bits[k:2 * k]


# ----- rate matching for convolutional codes ------------------------------------------------

def _subblock_positions(d):
    """For each of the sub-block interleaver's R x 32 outputs, the input index or -1 (dummy)."""
    r = -(-d // 32)
    nd = 32 * r - d
    j = np.arange(32 * r)
    y = (j % r) * 32 + np.array(_P_CC)[j // r]
    return np.where(y < nd, -1, y - nd)


def _cc_selection(d, e_len):
    """For each of the ``e_len`` rate-matched bits, its (stream, index) as stream * d + index."""
    v = _subblock_positions(d)
    w = np.concatenate([np.where(v < 0, -1, i * d + v) for i in range(3)])
    valid = w[w >= 0]
    return valid[np.arange(e_len) % len(valid)]


def cc_rate_match(streams, e_len):
    """The ``e_len`` bits sent of three coded streams (36.212 5.1.4.2)."""
    streams = np.asarray(streams, dtype=np.uint8)
    return streams.reshape(-1)[_cc_selection(streams.shape[1], e_len)]


def cc_rate_recover(llr_e, d, buffer=None):
    """The three streams' soft bits (3 x d) from rate-matched ``llr_e``, repeats summed (into
    ``buffer`` when given)."""
    llr_e = np.asarray(llr_e, dtype=np.float64)
    out = np.zeros(3 * d) if buffer is None else np.asarray(buffer, dtype=np.float64).reshape(-1).copy()
    np.add.at(out, _cc_selection(d, len(llr_e)), llr_e)
    return out.reshape(3, d)


# ----- the MIB ---------------------------------------------------------------------------------

def mib_bits(fields):
    """The 24 MIB bits of ``fields`` (``parse_mib``'s names; ``sfn`` gives ``sfn_msb``)."""
    f = dict(fields)
    if 'sfn' in f:
        f['sfn_msb'] = int(f['sfn']) >> 2
    out = []
    for name, width in _MIB_FIELDS:
        v = int(f.get(name, 0))
        out += [(v >> (width - 1 - i)) & 1 for i in range(width)]
    return np.array(out, dtype=np.uint8)


def parse_mib(a, quarter):
    """The MIB's fields from its 24 bits, with ``sfn`` from its eight bits and the frame's
    ``quarter`` of the 40 ms TTI (SFN mod 4)."""
    out, at = {}, 0
    for name, width in _MIB_FIELDS:
        v = 0
        for b in a[at:at + width]:
            v = (v << 1) | int(b)
        out[name] = v
        at += width
    out['n_rb'] = DL_BANDWIDTH_RB[out['dl_bandwidth']] if out['dl_bandwidth'] < 6 else None
    out['phich_duration'] = 'extended' if out['phich_duration'] else 'normal'
    out['phich_resource'] = PHICH_RESOURCE[out['phich_resource']]
    out['sfn'] = 4 * out['sfn_msb'] + int(quarter)
    return out


# ----- the transmitter (for the tests) --------------------------------------------------------

def encode_pbch(fields, pci, ports):
    """Per port, the 4 x 240 PBCH symbols of the 40 ms TTI whose first frame has SFN
    ``fields['sfn']`` (a multiple of 4), ``ports`` 1, 2 or 4: an array (ports, 4, 240)."""
    a = mib_bits(fields)
    c = np.concatenate((a, _crc(a, '16') ^ PORT_MASKS[ports]))
    e = cc_rate_match(tbcc_encode(c), E_PBCH) ^ _gold(int(pci), E_PBCH)
    d = ((1 - 2.0 * e[0::2]) + 1j * (1 - 2.0 * e[1::2])) / np.sqrt(2.0)
    return sfbc_encode(d, ports).reshape(ports, 4, N_PBCH_RE)


def sfbc_encode(d, ports):
    """36.211 6.3.3.3 and 6.3.4.3: ``d`` on ``ports`` (1, 2 or 4), an array (ports, len(d))."""
    d = np.asarray(d, dtype=np.complex128)
    if ports == 1:
        return d[None, :]
    y = np.zeros((ports, len(d)), dtype=np.complex128)
    if ports == 2:
        x0, x1 = d[0::2], d[1::2]
        y[0, 0::2], y[1, 0::2], y[0, 1::2], y[1, 1::2] = x0, -np.conj(x1), x1, np.conj(x0)
    else:
        x0, x1, x2, x3 = d[0::4], d[1::4], d[2::4], d[3::4]
        y[0, 0::4], y[2, 0::4], y[0, 1::4], y[2, 1::4] = x0, -np.conj(x1), x1, np.conj(x0)
        y[1, 2::4], y[3, 2::4], y[1, 3::4], y[3, 3::4] = x2, -np.conj(x3), x3, np.conj(x2)
    return y / np.sqrt(2.0)


# ----- the receiver -------------------------------------------------------------------------

def soft_bits(r, h, noise, ports):
    """The descrambled-to-be soft bits (positive means 0) of QPSK symbols received as ``r``
    through channels ``h`` (ports x len(r)) with noise variance ``noise`` per RE: a matched
    filter for 1 port, Alamouti pairs for 2 and 4 (36.211 6.3.4.3)."""
    r = np.asarray(r, dtype=np.complex128)
    h = np.asarray(h, dtype=np.complex128)
    if ports == 1:
        z = r * np.conj(h[0]) * np.sqrt(2.0)
    else:
        pairs = [(0, 1, 0, 2)] if ports == 2 else [(0, 2, 0, 4), (1, 3, 2, 4)]
        z = np.zeros(len(r), dtype=np.complex128)
        for pa, pb, first, step in pairs:
            i0 = np.arange(first, len(r), step)
            i1 = i0 + 1
            h0 = 0.5 * (h[pa, i0] + h[pa, i1])           # one channel over the pair
            h1 = 0.5 * (h[pb, i0] + h[pb, i1])
            z[i0] = np.conj(h0) * r[i0] + h1 * np.conj(r[i1])
            z[i1] = np.conj(h0) * r[i1] - h1 * np.conj(r[i0])
    out = np.empty(2 * len(r))
    out[0::2] = 2.0 * z.real / noise
    out[1::2] = 2.0 * z.imag / noise
    return out


def quarter_scores(llr, pci):
    """For each quarter i of the scrambling: how well the frame's 480 soft bits, descrambled
    with bits 480 i .. 480 i + 479, agree with themselves across the four copies of each coded
    bit (1 perfect, about 0 for the wrong quarter)."""
    llr = np.asarray(llr, dtype=np.float64)
    c = 1.0 - 2.0 * _gold(int(pci), E_PBCH).astype(np.float64)
    out = []
    for i in range(4):
        u = (llr * c[E_FRAME * i:E_FRAME * (i + 1)]).reshape(4, 120)
        num = float(np.sum(np.abs(u.sum(axis=0))) - np.sum(np.abs(u)) / 2)
        out.append(num / max(float(np.sum(np.abs(u))) / 2, 1e-12))
    return np.array(out)


def frame_llrs(llr, pci, quarter):
    """The frame's soft bits descrambled with its ``quarter`` of the sequence, placed at their
    place in the 1920 (zeros elsewhere), for ``cc_rate_recover``."""
    c = 1.0 - 2.0 * _gold(int(pci), E_PBCH).astype(np.float64)
    e = np.zeros(E_PBCH)
    sl = slice(E_FRAME * quarter, E_FRAME * (quarter + 1))
    e[sl] = np.asarray(llr, dtype=np.float64) * c[sl]
    return e


def decode_bch(e_llr):
    """``(mib bits, ports)`` from the 1920 descrambled soft bits (any frames of one TTI summed,
    others zero), or None when no port mask's CRC holds."""
    streams = cc_rate_recover(e_llr, K_BCH)
    c = viterbi_tbcc(streams)
    a, p = c[:A_MIB], c[A_MIB:]
    want = _crc(a, '16')
    for ports, mask in PORT_MASKS.items():
        if np.array_equal(p ^ mask, want):
            return a, ports
    return None


def decode_pbch_frame(r, h, noise, pci, hypotheses=(2, 1, 4)):
    """One frame's MIB: ``r`` the 240 PBCH REs (``pbch_res`` order), ``h`` (4, 240) the
    channel of ports 0..3 there, ``noise`` per RE. Tries each port count of ``hypotheses`` (the
    CRC mask must agree with it). Returns None or a dict: the MIB's fields, ``ports``,
    ``quarter``, ``agreement`` (its ``quarter_scores``), ``llr`` (the frame's 480 soft bits)."""
    for ports in hypotheses:
        llr = soft_bits(r, h[:ports], noise, ports)
        scores = quarter_scores(llr, pci)
        q = int(np.argmax(scores))
        got = decode_bch(frame_llrs(llr, pci, q))
        if got is not None and got[1] == ports:
            a, _p = got
            return dict(parse_mib(a, q), ports=ports, quarter=q, agreement=float(scores[q]), llr=llr,
                        bits=''.join(str(int(b)) for b in a))
    return None


# ----- the control region: PCFICH, PHICH's REGs, PDCCH (36.211 6.2.4, 6.7-6.9; 36.212 5.3.3-4)

#: CFI code words (36.212 table 5.3.4-1), CFI 1, 2, 3.
CFI_CODEWORDS = {cfi: np.array([int(b) for b in word], dtype=np.uint8) for cfi, word in (
    (1, '01101101101101101101101101101101'),
    (2, '10110110110110110110110110110110'),
    (3, '11011011011011011011011011011011'))}
SI_RNTI = 0xFFFF
#: The common search space (36.213 9.1.1, table 9.1.1-1; Y = 0): aggregation level, candidates.
#: Level 4 is tried first: a level-8 candidate holds the level-4 one at its start (the circular
#: buffer read on), so it passes the CRC too; ``wider`` tells them apart.
COMMON_SEARCH = ((4, 4), (8, 2))
#: Ambiguous DCI sizes (36.212 table 5.3.3.1.2-1): one zero bit is appended to a 1A of these.
_AMBIGUOUS = (12, 14, 16, 20, 24, 26, 32, 40, 44, 56)
#: N_gap,1 by bandwidth (36.211 table 6.2.3.2-1): (lowest N_RB of the row, gap).
_GAP1 = ((6, None), (11, 4), (20, 8), (27, 12), (45, 18), (50, 27), (64, 32), (80, 48))


def _riv_bits(n):
    return int(np.ceil(np.log2(n * (n + 1) / 2)))


def n_gap1(n_rb):
    """N_gap,1 (36.211 table 6.2.3.2-1; 6-10 RB: ceil(N_RB / 2))."""
    if n_rb <= 10:
        return -(-n_rb // 2)
    return [g for lo, g in _GAP1 if n_rb >= lo][-1]


def dci_1a_bits(n_rb, n_rb_ul=None):
    """DCI format 1A's size (36.212 5.3.3.1.3) for a common search space, FDD, no carrier
    indicator: grown to format 0's size (5.3.3.1.1) and past the ambiguous sizes."""
    ul = n_rb if n_rb_ul is None else n_rb_ul
    a = 1 + 1 + _riv_bits(n_rb) + 5 + 3 + 1 + 2 + 2
    f0 = 1 + 1 + _riv_bits(ul) + 5 + 1 + 2 + 3 + 1 + (1 if ul <= n_rb else 0)
    a = max(a, f0)
    return a + 1 if a in _AMBIGUOUS else a


def dci_1c_bits(n_rb):
    """DCI format 1C's size (36.212 5.3.3.1.4) for scheduling one PDSCH codeword."""
    step = 2 if n_rb < 50 else 4
    g = n_gap1(n_rb)
    nvrb = 2 * min(g, n_rb - g)
    m = nvrb // step
    return (1 if n_rb >= 50 else 0) + int(np.ceil(np.log2(m * (m + 1) / 2))) + 5


def regs(n_rb, ports, n_symb):
    """The REGs of the first ``n_symb`` symbols (36.211 6.2.4), as (l, k of the lowest RE, the
    four REs that carry symbols): with 1 or 2 ports the CRS of ports 0 and 1 in symbol 0; with 4,
    also ports 2 and 3 in symbol 1. ``v`` (the CRS's k mod 3) is filled in by ``cell_regs``."""
    out = []
    for l in range(n_symb):
        six = l == 0 or (l == 1 and ports == 4)
        for rb in range(n_rb):
            k0 = 12 * rb
            for start in ((k0, k0 + 6) if six else (k0, k0 + 4, k0 + 8)):
                out.append((l, start, six))
    return out


def cell_regs(pci, n_rb, ports, n_symb):
    """``regs`` with each REG's data REs: [(l, k_lowest, [k of its four symbols])]."""
    v = pci % 3
    out = []
    for l, start, six in regs(n_rb, ports, n_symb):
        ks = [k for k in range(start, start + (6 if six else 4)) if not (six and k % 3 == v)]
        out.append((l, start, ks))
    return out


def pcfich_starts(pci, n_rb):
    """The lowest k of the PCFICH's four REGs in symbol 0 (36.211 6.7.4)."""
    kbar = 6 * (pci % (2 * n_rb))
    return [(kbar + (i * n_rb // 2) * 6) % (12 * n_rb) for i in range(4)]


def phich_groups(n_rb, ng):
    """N_PHICH^group for normal CP (36.211 6.9): ceil(N_g N_RB / 8), N_g one of 1/6, 1/2, 1, 2."""
    from fractions import Fraction
    g = {'1/6': Fraction(1, 6), '1/2': Fraction(1, 2), '1': Fraction(1), '2': Fraction(2)}[ng]
    return int(-(-g * n_rb // 8))


def phich_starts(pci, n_rb, ports, ng, duration='normal'):
    """(l, lowest k) of the PHICH's REGs (36.211 6.9.3, normal CP, a normal subframe)."""
    taken = set(pcfich_starts(pci, n_rb))
    per = {}
    for l in range(3 if duration == 'extended' else 1):
        per[l] = [start for ll, start, _ks in cell_regs(pci, n_rb, ports, l + 1) if ll == l
                  and not (l == 0 and start in taken)]
    n0 = len(per[0])
    out = []
    for m in range(phich_groups(n_rb, ng)):
        for i in range(3):
            l = i if duration == 'extended' else 0
            n_l = len(per[l])
            nbar = ((pci * n_l) // n0 + m + (i * n_l) // 3) % n_l
            out.append((l, per[l][nbar]))
    return out


def pdcch_quads(pci, n_rb, ports, cfi, ng, duration='normal'):
    """For each PDCCH symbol quadruplet z(i), the REs it is sent on, [(l, [k x 4])]: the REGs
    of the first ``cfi`` symbols left by the PCFICH and PHICH, taken k first then l (36.211
    6.8.5 steps 1-10), the quadruplets through the sub-block interleaver (36.212 5.1.4.2.1)
    and turned by the PCI."""
    used = {(0, k) for k in pcfich_starts(pci, n_rb)} | set(phich_starts(pci, n_rb, ports, ng, duration))
    free = [(l, start, ks) for l, start, ks in cell_regs(pci, n_rb, ports, cfi) if (l, start) not in used]
    free.sort(key=lambda r: (r[1], r[0]))
    m = len(free)
    w = _subblock_positions(m)
    w = w[w >= 0]                                     # w(j): which z the j-th output carries
    out = [None] * m
    for mp in range(m):                               # REG m' holds w((m' + PCI) mod M)
        out[w[(mp + pci) % m]] = (free[mp][0], free[mp][2])
    return out


def encode_dci(a, rnti, level):
    """The 72 x ``level`` bits a DCI ``a`` is sent as: CRC 16 with its last 16 bits XORed with
    the RNTI (36.212 5.3.3.2), the tail-biting code and rate matching."""
    a = np.asarray(a, dtype=np.uint8)
    x = np.array([(int(rnti) >> (15 - i)) & 1 for i in range(16)], dtype=np.uint8)
    c = np.concatenate((a, _crc(a, '16') ^ x))
    return cc_rate_match(tbcc_encode(c), 72 * int(level))


def decode_dci(llr, size, rnti):
    """The ``size`` payload bits from a candidate's descrambled soft bits, or None."""
    k = int(size) + 16
    c = viterbi_tbcc(cc_rate_recover(llr, k))
    x = np.array([(int(rnti) >> (15 - i)) & 1 for i in range(16)], dtype=np.uint8)
    a = c[:size]
    return a if np.array_equal(_crc(a, '16') ^ x, c[size:]) else None


def parse_dci_1a(a, n_rb):
    """DCI format 1A with CRC scrambled by SI-RNTI (36.212 5.3.3.1.3)."""
    w = _riv_bits(n_rb)
    names = (('format1a', 1), ('distributed', 1), ('riv', w), ('mcs', 5), ('harq', 3), ('ndi', 1),
             ('rv', 2), ('tpc', 2))
    out, at = {'format': '1A'}, 0
    for name, width in names:
        v = 0
        for b in a[at:at + width]:
            v = (v << 1) | int(b)
        out[name] = v
        at += width
    out['n_prb_1a'] = 3 if out['tpc'] & 1 else 2
    out['gap2'] = bool(out['ndi']) if out['distributed'] and n_rb >= 50 else False
    return out


def parse_dci_1c(a, n_rb):
    """DCI format 1C (very compact scheduling, 36.212 5.3.3.1.4)."""
    out, at = {'format': '1C', 'gap2': False}, 0
    if n_rb >= 50:
        out['gap2'] = bool(a[0])
        at = 1
    w = dci_1c_bits(n_rb) - at - 5
    v = 0
    for b in a[at:at + w]:
        v = (v << 1) | int(b)
    out['riv'] = v
    t = 0
    for b in a[at + w:at + w + 5]:
        t = (t << 1) | int(b)
    out['tbs_index'] = t
    return out


def pdcch_scrambling(pci, subframe, nbits):
    """The PDCCH's scrambling sequence (36.211 6.8.2): c_init = subframe 2^9 + PCI."""
    return _gold(int(subframe) * 512 + int(pci), nbits)


def pcfich_scrambling(pci, subframe):
    """The PCFICH's scrambling (36.211 6.7.1): c_init = (subframe + 1)(2 PCI + 1) 2^9 + PCI."""
    return _gold((int(subframe) + 1) * (2 * int(pci) + 1) * 512 + int(pci), 32)


def decode_pcfich(r, h, noise, pci, subframe, ports):
    """``(cfi, score)`` from the PCFICH's 16 REs (``pcfich_starts`` order), 1-3, its correlation
    with the code words (1 perfect)."""
    llr = soft_bits(r, h[:ports], noise, ports) * (1.0 - 2.0 * pcfich_scrambling(pci, subframe))
    scores = {cfi: float(np.dot(llr, 1.0 - 2.0 * w)) / max(float(np.sum(np.abs(llr))), 1e-12)
              for cfi, w in CFI_CODEWORDS.items()}
    cfi = max(scores, key=scores.get)
    return cfi, scores[cfi]


#: Score (``codeword_score``, about 0 +/- 1 where nothing of it is sent) over which the CCEs
#: after a level-4 decode are taken to carry its level-8 continuation.
_WIDER = 5.0


def codeword_score(llr, signs):
    """How strongly soft bits ``llr`` hold the codeword ``signs``: their correlation over its
    spread, about 0 +/- 1 where it is not sent."""
    llr = np.asarray(llr, dtype=np.float64)
    spread = float(np.sqrt(np.sum(llr ** 2)))
    return float(np.dot(llr, signs)) / spread if spread > 0 else 0.0


def common_candidates(n_cce):
    """(level, first CCE) of the common search space (36.213 9.1.1, Y = 0), level 4 first."""
    out = []
    for level, count in COMMON_SEARCH:
        n = n_cce // level
        for m in range(min(count, n)):
            out.append((level, level * (m % n)))
    return out


def control_res(pci, n_rb, ports, cfi, ng, duration, subframe, pdcchs, rnti=SI_RNTI):
    """The transmitter of a subframe's PCFICH and PDCCHs, for the tests: per port, the REs and
    symbols, [(port, l, k array, values)]. ``pdcchs`` is [(DCI bits, level, first CCE)]; the
    other CCEs are left empty (<NIL>)."""
    out = []
    pk = [(0, k + j) for k in pcfich_starts(pci, n_rb) for j in range(6) if (k + j) % 3 != pci % 3]
    b = CFI_CODEWORDS[cfi] ^ pcfich_scrambling(pci, subframe)
    d = ((1 - 2.0 * b[0::2]) + 1j * (1 - 2.0 * b[1::2])) / np.sqrt(2.0)
    y = sfbc_encode(d, ports)
    for p in range(ports):
        out.append((p, 0, np.array([k for _l, k in pk]), y[p]))
    q = pdcch_quads(pci, n_rb, ports, cfi, ng, duration)
    n_bits = 8 * len(q)
    bits = np.zeros(n_bits)
    sent = np.zeros(n_bits, dtype=bool)
    for a, level, cce0 in pdcchs:
        e = encode_dci(a, rnti, level)
        bits[72 * cce0:72 * cce0 + len(e)] = e
        sent[72 * cce0:72 * cce0 + len(e)] = True
    bits = (bits.astype(np.uint8) ^ pdcch_scrambling(pci, subframe, n_bits)) * sent
    d = ((1 - 2.0 * bits[0::2]) + 1j * (1 - 2.0 * bits[1::2])) / np.sqrt(2.0) * sent[0::2]
    y = sfbc_encode(d, ports)
    for p in range(ports):
        for l in range(cfi):
            idx = [(4 * i + j, k) for i, (ll, ks) in enumerate(q) if ll == l for j, k in enumerate(ks)]
            if idx:
                out.append((p, l, np.array([k for _i, k in idx]), y[p, [i for i, _k in idx]]))
    return out


def dci_1a_si(n_rb, riv, mcs, rv, n_prb_1a=3, distributed=0):
    """A DCI 1A payload for SI-RNTI with these fields (36.212 5.3.3.1.3), for the tests."""
    size = dci_1a_bits(n_rb)
    fields = ((1, 1), (distributed, 1), (riv, _riv_bits(n_rb)), (mcs, 5), (0, 3), (0, 1), (rv, 2),
              (1 if n_prb_1a == 3 else 0, 2))
    a = [(v >> (w - 1 - i)) & 1 for v, w in fields for i in range(w)]
    return np.array(a + [0] * (size - len(a)), dtype=np.uint8)


# ----- a capture's frames: the PBCH's grid, its channel, the MIB ---------------------------

#: The PBCH's band is read at 1.92 MS/s: a 128-point FFT, its central 72 subcarriers.
_PB_RATE = 1.92e6
_PB_N = 128
_PB_CP0, _PB_CP = 10, 9
#: Symbols of a frame read for the PBCH and its channel: subframe 0 and the first two
#: symbols of slot 2 (ports 0 and 1 at 0, 4, 7, 11, 14; ports 2 and 3 at 1, 8, 15).
_PB_SYMBOLS = 16
_PB_BINS = np.concatenate((np.arange(-36, 0), np.arange(1, 37)))      # k' 0..71, DC skipped
_PB_SLOPES = np.linspace(-0.4, 0.4, 161)                                 # rad per subcarrier


def _pb_start(l):
    """A frame's symbol ``l`` (0.. across slots): its useful part's start, samples at 1.92 MS/s."""
    slot, s = divmod(l, 7)
    return slot * 960 + _PB_CP0 + _PB_N + (s - 1) * (_PB_CP + _PB_N) + _PB_CP if s else slot * 960 + _PB_CP0


def lte_cfo(iq, fs, frame_offset, n_frames=20):
    """The carrier's frequency offset (Hz) from the cyclic prefixes of ``n_frames`` frames at the
    cell's timing (``frame_offset`` in samples of ``fs``): within +/- 7.5 kHz."""
    ratio = Fraction(int(round(_PB_RATE)), int(round(float(fs))))
    acc = 0j
    frame = int(round(0.010 * float(fs)))
    for f in range(n_frames):
        i0 = int(frame_offset) + f * frame
        n_in = frame + (-frame) % ratio.denominator
        if i0 + n_in > len(iq):
            break
        y = _cut(iq, float(fs), i0, n_in, 0.0, _PB_RATE)
        for l in range(140):
            u = _pb_start(l)
            cp = _PB_CP0 if l % 7 == 0 else _PB_CP
            if u + _PB_N > len(y):
                break
            acc += np.vdot(y[u - cp:u], y[u - cp + _PB_N:u + _PB_N])
    return float(np.angle(acc) / (2 * np.pi * _PB_N / _PB_RATE)) if acc else 0.0


class _PbFrames:
    """A cell's frames in a capture, for its PBCH: frame f's subframe 0 at ``t0 + 10 ms f``,
    the clock scaled by the offset as ``iqnr._Timing`` (the recorder's clock and tuner share one
    reference)."""

    def __init__(self, fs, center_offset_hz, frame_offset, cfo_hz, carrier_hz):
        self.fs = float(fs)
        self.shift = float(center_offset_hz) + float(cfo_hz)
        self.t0 = float(frame_offset) / self.fs
        self.scale = 1.0 - float(cfo_hz) / float(carrier_hz) if carrier_hz else 1.0

    def start(self, f):
        return self.t0 + f * 0.010 * self.scale

    def count(self, n_total):
        return max(0, int((n_total / self.fs - self.t0 - 0.0012) / (0.010 * self.scale)))

    def grid(self, x, f):
        """Frame ``f``'s first 16 symbols on the central 72 subcarriers, (16, 72), or None."""
        ratio = Fraction(int(round(_PB_RATE)), int(round(self.fs)))
        t_f = self.start(f)
        margin = 32 / _PB_RATE
        i0 = int(np.floor((t_f - margin) * self.fs))
        n_in = int(np.ceil((_pb_start(_PB_SYMBOLS - 1) + _PB_N + 64) / _PB_RATE * self.fs))
        n_in += (-n_in) % ratio.denominator
        if i0 < 0 or i0 + n_in > len(x):
            return None
        y = _cut(x, self.fs, i0, n_in, self.shift, _PB_RATE)
        out = np.zeros((_PB_SYMBOLS, 72), dtype=np.complex128)
        for l in range(_PB_SYMBOLS):
            cp = _PB_CP0 if l % 7 == 0 else _PB_CP
            start = (t_f - i0 / self.fs) * _PB_RATE + _pb_start(l) - cp / 2
            p = int(np.floor(start))
            if p < 0 or p + _PB_N > len(y):
                return None
            spec = np.fft.fft(y[p:p + _PB_N])
            d = start + cp / 2 - p                         # the window opens d samples early
            out[l] = spec[_PB_BINS % _PB_N] * np.exp(2j * np.pi * _PB_BINS * d / _PB_N)
        return out

    def subframe(self, x, f, sf, n_rb, n_symb=14):
        """Frame ``f``'s subframe ``sf``: its first ``n_symb`` symbols on every subcarrier,
        (n_symb, 12 n_rb), k = 0 lowest (DC not counted), or None."""
        nfft = _NFFT_FOR_RB[n_rb]
        rate = nfft * 15e3
        cp0, cp1 = 160 * nfft // 2048, 144 * nfft // 2048
        ratio = Fraction(int(round(rate)), int(round(self.fs)))
        t_sf = self.start(f) + sf * 1e-3 * self.scale
        i0 = int(np.floor((t_sf - 32 / rate) * self.fs))
        n_in = int(np.ceil(((n_symb + 1) * (nfft + cp1) + cp0 + 64) / rate * self.fs))
        n_in += (-n_in) % ratio.denominator
        if i0 < 0 or i0 + n_in > len(x):
            return None
        y = _cut(x, self.fs, i0, n_in, self.shift, rate)
        k = np.arange(12 * n_rb)
        bins = np.where(k < 6 * n_rb, k - 6 * n_rb, k - 6 * n_rb + 1)
        out = np.zeros((n_symb, 12 * n_rb), dtype=np.complex128)
        for l in range(n_symb):
            slot, s_ = divmod(l, 7)
            u = slot * (7 * nfft + cp0 + 6 * cp1) + (cp0 + nfft + (s_ - 1) * (cp1 + nfft) + cp1 if s_ else cp0)
            cp = cp0 if s_ == 0 else cp1
            start = (t_sf - i0 / self.fs) * rate + u - cp / 2
            p = int(np.floor(start))
            if p < 0 or p + nfft > len(y):
                return None
            spec = np.fft.fft(y[p:p + nfft])
            d = start + cp / 2 - p
            out[l] = spec[bins % nfft] * np.exp(2j * np.pi * bins * d / nfft)
        return out


#: The FFT size of each bandwidth (36.104): N_RB -> nfft.
_NFFT_FOR_RB = {6: 128, 15: 256, 25: 512, 50: 1024, 75: 1536, 100: 2048}


def subframe_channel(g, pci, sf, ports, symbols, smooth=5):
    """The channel of ports 0 .. ``ports`` - 1 on ``symbols`` of a subframe grid
    (``_PbFrames.subframe``), (4, len(symbols), 12 N_RB), and the noise per RE: a delay slope
    from the CRS, then each CRS symbol's pilots averaged over ``smooth`` neighbours and
    interpolated across the band, linear in time between CRS symbols of the port."""
    n_sc = g.shape[1]
    n_rb = n_sc // 12
    k_all = np.arange(n_sc)
    pilots = []                                         # (port, l, k, z)
    for port in range(ports):
        for l in range(g.shape[0]):
            slot, s_ = divmod(l, 7)
            if s_ not in crs_symbols(port):
                continue
            ns = 2 * sf + slot
            shift = (crs_shift(port, s_, ns) + pci % 6) % 6
            k = 6 * np.arange(2 * n_rb) + shift
            pilots.append((port, l, k, g[l, k] * np.conj(crs_values(pci, ns, s_, n_rb))))
    score = np.zeros(len(_PB_SLOPES))
    for _p, _l, k, z in pilots:
        score += np.abs(np.exp(-1j * np.outer(_PB_SLOPES, k)) @ z) ** 2
    slope = float(_PB_SLOPES[int(np.argmax(score))])
    kernel = np.ones(smooth) / smooth
    rows, resid, dof = {}, 0.0, 0
    for port, l, k, z in pilots:
        zf = z * np.exp(-1j * slope * k)
        num = np.convolve(zf, kernel, mode='same')
        den = np.convolve(np.ones(len(zf)), kernel, mode='same')
        sm = num / den
        resid += float(np.sum(np.abs(zf - sm) ** 2))
        dof += len(zf) * (1 - 1.0 / smooth)
        h = (np.interp(k_all, k, sm.real) + 1j * np.interp(k_all, k, sm.imag)) * np.exp(1j * slope * k_all)
        rows.setdefault(port, []).append((l, h))
    out = np.zeros((4, len(symbols), n_sc), dtype=np.complex128)
    for port, rr in rows.items():
        ls = np.array([l for l, _h in rr], dtype=float)
        hs = np.array([h for _l, h in rr])
        for i, l in enumerate(symbols):
            if len(ls) == 1:
                out[port, i] = hs[0]
                continue
            j = int(np.clip(np.searchsorted(ls, l), 1, len(ls) - 1))
            w = (l - ls[j - 1]) / (ls[j] - ls[j - 1])
            out[port, i] = (1 - w) * hs[j - 1] + w * hs[j]
    return out, max(resid / max(dof, 1.0), 1e-12)


def _pb_channel(g, pci, ports=4):
    """The channel of ports 0 .. ``ports`` - 1 on symbols 7..10 of a frame grid
    (``_PbFrames.grid``), (4, 4, 72) (zeros for the others), and the noise per RE: one delay
    slope from their CRS, then per port and CRS symbol a gain and a tilt across the 72
    subcarriers, linear in time between CRS symbols. Only the hypothesis's own ports: on a
    2-port cell, ports 2 and 3's places hold the control region's data, not pilots."""
    pilots = []                                         # (port, symbol, k', z)
    for port in range(ports):
        for l in range(_PB_SYMBOLS):
            slot, s = divmod(l, 7)
            if s not in crs_symbols(port):
                continue
            kp, r = central_crs(pci, slot, s, port)
            pilots.append((port, l, kp, g[l, kp] * np.conj(r)))
    score = np.zeros(len(_PB_SLOPES))
    for _p, _l, kp, z in pilots:
        score += np.abs(np.exp(-1j * np.outer(_PB_SLOPES, kp)) @ z) ** 2
    slope = float(_PB_SLOPES[int(np.argmax(score))])
    k = np.arange(72)
    fits, resid, dof = {}, 0.0, 0
    for port, l, kp, z in pilots:
        zf = z * np.exp(-1j * slope * kp)
        a = np.stack([np.ones(len(kp)), kp - 35.5], axis=1).astype(complex)
        coef, *_ = np.linalg.lstsq(a, zf, rcond=None)
        resid += float(np.sum(np.abs(zf - a @ coef) ** 2))
        dof += len(kp) - 2
        fits.setdefault(port, []).append((l, (coef[0] + coef[1] * (k - 35.5)) * np.exp(1j * slope * k)))
    h = np.zeros((4, 4, 72), dtype=np.complex128)
    for port, rows in fits.items():
        ls = np.array([l for l, _v in rows], dtype=float)
        vs = np.array([v for _l, v in rows])
        for i, l in enumerate(range(7, 11)):
            j = int(np.clip(np.searchsorted(ls, l), 1, len(ls) - 1))
            w = (l - ls[j - 1]) / (ls[j] - ls[j - 1])
            h[port, i] = (1 - w) * vs[j - 1] + w * vs[j]
    return h, max(resid / max(dof, 1), 1e-12)


def pbch(iq, fs, cell, center_offset_hz=0.0, carrier_hz=None, cfo_hz=None):
    """Our own MIB decode of one cell (``cells`` entry: ``pci``, ``frame_offset_samples`` with
    ``half_frame`` 0) over every frame of ``iq``: ``iqlte_phy``, numpy only.

    ``center_offset_hz`` is the carrier's offset from the capture's centre, ``carrier_hz`` its
    frequency (for the clock's drift), ``cfo_hz`` the frequency offset (measured from the cyclic
    prefixes when None). Each frame is decoded alone; frames of one 40 ms TTI that fail alone
    are summed. A MIB is reported only when two or more separate decodes (a frame alone, or one
    TTI summed) agree on its fields with their SFNs one apart per frame: a 16-bit CRC passes by
    chance once in 65536 checks, about once in 20000 frames over the three port hypotheses.

    Returns a dict: ``mib`` (the agreed fields: ``n_rb``, ``phich_duration``, ``phich_resource``,
    ``sfn`` of the first frame, the Rel-13 to 17 fields; or None), ``ports``, ``frames`` (read),
    ``decoded`` (frames agreeing), ``alone`` and ``summed`` (how those decoded), ``cfo_hz``,
    ``snr_db`` (median per-RE SNR of the CRS fits, on the frames decoded alone, with the
    decoded port count's own CRS), ``reason``."""
    pci = int(cell['pci'])
    offset = float(cell['frame_offset_samples'])
    if int(cell.get('half_frame') or 0) == 1:
        offset -= 0.005 * float(fs)
    frame = 0.010 * float(fs)
    offset %= frame
    if cfo_hz is None:
        cfo_hz = lte_cfo(iq, fs, offset)
    frames = _PbFrames(fs, center_offset_hz, offset, cfo_hz, carrier_hz)
    res = pbch_res(pci)
    got, snrs, tti = [], [], {}
    n = frames.count(len(iq))
    for f in range(n):
        g = frames.grid(iq, f)
        if g is None:
            continue
        r = g[7 + res[:, 0], res[:, 1]]
        hyp = {}
        one = None
        for ports in (2, 1, 4):
            h, noise = _pb_channel(g, pci, ports)
            hyp[ports] = (h[:, res[:, 0], res[:, 1]], noise)
            one = decode_pbch_frame(r, hyp[ports][0], noise, pci, (ports,))
            if one is not None:
                snrs.append(10 * np.log10(max(float(np.mean(np.abs(h[0]) ** 2)) / noise, 1e-12)))
                got.append((f, dict(one, how='alone', trial=('frame', f))))
                break
        if one is not None:
            continue
        llr = soft_bits(r, hyp[2][0][:2], hyp[2][1], 2)       # the TTI quarter, as 2 ports
        q = int(np.argmax(quarter_scores(llr, pci)))
        tti.setdefault(f - q, []).append((f, q, r, hyp))
    for start, items in tti.items():
        if len(items) < 2:
            continue
        for ports in (2, 1, 4):
            e = sum(frame_llrs(soft_bits(r, hyp[ports][0][:ports], hyp[ports][1], ports), pci, q)
                    for _f, q, r, hyp in items)
            dec = decode_bch(e)
            if dec is not None and dec[1] == ports:
                for f, q, *_rest in items:
                    got.append((f, dict(parse_mib(dec[0], q), ports=ports, quarter=q, how='summed',
                                        bits=''.join(str(int(b)) for b in dec[0]), trial=('tti', start))))
                break
    groups = {}
    for f, m in got:
        key = (m['bits'][:6] + m['bits'][14:], m['ports'], (m['sfn'] - f) % 1024)
        groups.setdefault(key, []).append((f, m))
    # Agreement counts separate CRC passes: a summed TTI is one pass however many frames it fills.
    best = max(groups.values(), key=lambda v: len({m['trial'] for _f, m in v})) if groups else []
    passes = len({m['trial'] for _f, m in best})
    out = {'mib': None, 'ports': None, 'frames': n, 'decoded': len(best), 'alone': 0, 'summed': 0,
           'cfo_hz': float(cfo_hz), 'snr_db': float(np.median(snrs)) if snrs else None, 'reason': None}
    if passes < 2:
        out['reason'] = ('no MIB in %d frames' % n if not got else
                         'no two separate decodes agree on a MIB (%d frames decoded)' % len(got))
        return out
    f0, m0 = min(best, key=lambda t: t[0])
    mib = {k: v for k, v in m0.items() if k not in ('llr', 'bits', 'how', 'quarter', 'agreement', 'ports', 'sfn_msb',
                                                     'trial')}
    mib['sfn'] = int((m0['sfn'] - f0) % 1024)                       # frame 0's SFN
    out.update(mib=mib, ports=m0['ports'], alone=sum(1 for _f, m in best if m['how'] == 'alone'),
               summed=sum(1 for _f, m in best if m['how'] == 'summed'))
    return out


def sib1_dcis(iq, fs, cell, pbch_result, center_offset_hz=0.0, carrier_hz=None, rnti=SI_RNTI):
    """SIB1's DCIs (formats 1A and 1C with CRC scrambled by SI-RNTI) of one cell, in subframe 5
    of every even frame, by our own PCFICH and PDCCH decode (roadmap Round 29.2).

    ``pbch_result`` is ``pbch``'s for the cell (its MIB: bandwidth, PHICH, ports, SFN). Each
    subframe: the CFI from the PCFICH, then the common search space's candidates (level 8,
    then 4) for both sizes. Returns a dict: ``cfi`` (counts), ``subframes`` (read) and ``dcis``,
    one per DCI: ``sfn``, ``level``, ``cce``, ``format`` and its fields, ``bits``, ``snr_db``."""
    mib = (pbch_result or {}).get('mib')
    if not mib or not mib.get('n_rb') or mib['n_rb'] not in _NFFT_FOR_RB:
        return None
    pci, n_rb, ports = int(cell['pci']), int(mib['n_rb']), int(pbch_result['ports'])
    offset = float(cell['frame_offset_samples'])
    if int(cell.get('half_frame') or 0) == 1:
        offset -= 0.005 * float(fs)
    offset %= 0.010 * float(fs)
    frames = _PbFrames(fs, center_offset_hz, offset, pbch_result['cfo_hz'], carrier_hz)
    sizes = (('1A', dci_1a_bits(n_rb)), ('1C', dci_1c_bits(n_rb)))
    quads = {}
    out = {'cfi': {}, 'subframes': 0, 'dcis': []}
    for f in range(frames.count(len(iq))):
        sfn = (int(mib['sfn']) + f) % 1024
        if sfn % 2:
            continue
        g = frames.subframe(iq, f, 5, n_rb, n_symb=9)
        if g is None:
            continue
        out['subframes'] += 1
        h, noise = subframe_channel(g, pci, 5, ports, list(range(4)))
        pk = [(0, k + j) for k in pcfich_starts(pci, n_rb) for j in range(6) if (k + j) % 3 != pci % 3]
        r = np.array([g[l, k] for l, k in pk])
        hh = np.array([[h[p, l, k] for l, k in pk] for p in range(4)])
        cfi, _score = decode_pcfich(r, hh, noise, pci, 5, ports)
        out['cfi'][cfi] = out['cfi'].get(cfi, 0) + 1
        key = cfi
        if key not in quads:
            quads[key] = pdcch_quads(pci, n_rb, ports, cfi, mib['phich_resource'], mib['phich_duration'])
        q = quads[key]
        ls = np.array([l for l, ks in q for _k in ks])
        ks = np.array([k for _l, kk in q for k in kk])
        r = g[ls, ks]
        hh = h[:, ls, ks]
        llr = soft_bits(r, hh[:ports], noise, ports) * (1.0 - 2.0 * pdcch_scrambling(pci, 5, 2 * len(r)))
        n_cce = len(q) // 9
        taken = set()
        for level, cce0 in common_candidates(n_cce):
            if set(range(cce0, cce0 + level)) & taken:
                continue
            seg = llr[72 * cce0:72 * (cce0 + level)]
            for fmt, size in sizes:
                a = decode_dci(seg, size, rnti)
                if a is None:
                    continue
                fields = parse_dci_1a(a, n_rb) if fmt == '1A' else parse_dci_1c(a, n_rb)
                if fmt == '1A' and not fields['format1a']:
                    continue
                if level == 4 and cce0 % 8 == 0 and 72 * (cce0 + 8) <= len(llr):
                    more = encode_dci(a, rnti, 8)[288:]
                    if codeword_score(llr[72 * (cce0 + 4):72 * (cce0 + 8)], 1.0 - 2.0 * more) > _WIDER:
                        level = 8                          # CCEs 4-7 carry the continuation
                taken |= set(range(cce0, cce0 + level))
                sig = float(np.mean(np.abs(hh[0, 36 * cce0:36 * (cce0 + level)]) ** 2))
                out['dcis'].append(dict(fields, sfn=sfn, level=level, cce=cce0, cfi=cfi,
                                        bits=''.join(str(int(b)) for b in a),
                                        snr_db=float(10 * np.log10(max(sig / noise, 1e-12)))))
                break
    return out
