"""LTE broadcast decoding, numpy only: the PBCH and its MIB (roadmap Round 29.1), the
PCFICH and PDCCH that schedule SIB1 (29.2), and SIB1's PDSCH and DL-SCH (29.3).

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

The PDSCH and DL-SCH (29.3): the DCI's RIV gives the VRBs (36.213 7.1.6.3; 1C in steps of 2
or 4), localized or distributed (36.211 6.2.3.2: a 4-column interleaver with nulls, the second
slot moved by half, then the gap), and its MCS the TBS (36.213 7.1.7.2, I_TBS = I_MCS: 1A's
column N_PRB^1A, 1C's own table). The REs from the CFI's symbol on, k first over the PRBs, then
l, skip the CRS of the cell's ports, the synchronisation signals' central 72 subcarriers and
the PBCH's (6.3.5, 6.4). SFBC as the PBCH; scrambling c_init = RNTI 2^14 + subframe 2^9 + PCI
(6.3.1). The transport block: CRC 24A, segmentation into code blocks of the turbo sizes
(36.212 5.1.2, CRC 24B on each when more than one), the rate 1/3 turbo code (5.1.3.2: two
8-state constituent codes 13 and 15 octal, the QPP interleaver of table 5.1.3-3, 12 tail
bits), rate matching (5.1.4.1: the 32-column sub-block interleaver, d(2)'s shifted by one, the
parity streams interlaced into a circular buffer read from k0 by the redundancy version). The
decoder is max-log-MAP with its extrinsic scaled by 0.75, up to 8 iterations, stopping when
the CRC passes. SIB1's RV is 1A's field or, for 1C, ceil(3 k / 2) mod 4 with k = (SFN / 2)
mod 4 (36.321 5.3.1).

Combining (29.4): SIB1's copies of one 80 ms period (and then of a few periods) summed in the
soft buffers; SIB1's DCIs summed by k = (SFN / 2) mod 4, as DCI 1A carries the RV.

Cancellation (29.5): sectors of one site start their frames together and send their PBCH, SIB1
DCI and SIB1 on the same REs. What a decoded cell sent is rebuilt (``known_cell``: its CRS, PSS,
SSS and PBCH from its PCI and MIB; its PCFICH, SIB1 DCI and SIB1 PDSCH where each passed its
CRC), fitted to the reading cell's grid per RB (a gain and a slope in time for each port and for
the synchronisation signals; a port's PBCH and data on its CRS channel times the cell's own power
ratio, ``_rest_ratio``) and subtracted before the weaker cell's grid is read (``known=`` on
``pbch``, ``sib1_dcis`` and ``sib1``), when their frames start within 1 us. A known cell of another
timing (another site) is taken out of the samples instead (``_PbFrames.cut``): its subframes read
at its own timing, fitted there (with the other known cells of that timing), modulated again at the
reader's sample times and subtracted before the reader's FFT (``_known_samples``).

Wrong turns
-----------
- A level-8 candidate passed the CRC where the cell sent level 4 (Band 13, 2026-10-25): the
  circular buffer read on puts the level-4 bits first in the level-8 ones. Level 4 is tried
  first, and level 8 only when CCEs 4-7 carry the continuation.
- Taking the stronger cell out turned one marginal PCFICH of the weaker (the 700 MHz pair,
  2026-10-26: CFI 1 read as 3, its DCI lost). A subframe with no DCI after cancelling is read
  again as it is.
- One gain a port for the CRS and the rest together left the other 700 MHz carrier's stronger
  cell in (it sends its CRS 1.5 to 3 dB under the rest); a gain of its own for the rest in each
  RB followed the weaker cell on the few REs a PDCCH has there and took it out too (a DCI lost in
  the synthetic test). The rest now rides on the CRS channel at one measured ratio a port.
"""

from fractions import Fraction

import numpy as np

from . import iqlte
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
    """The 24 MIB bits of ``fields`` (``parse_mib``'s names; ``sfn`` gives ``sfn_msb``; the PHICH
    fields as numbers or as ``parse_mib`` gives them, so a decoded MIB re-encodes)."""
    f = dict(fields)
    if 'sfn' in f:
        f['sfn_msb'] = int(f['sfn']) >> 2
    if isinstance(f.get('phich_duration'), str):
        f['phich_duration'] = int(f['phich_duration'] == 'extended')
    if isinstance(f.get('phich_resource'), str):
        f['phich_resource'] = PHICH_RESOURCE.index(f['phich_resource'])
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


def dci_1c_si(n_rb, riv, tbs_index, gap2=0):
    """A DCI 1C payload with these fields (36.212 5.3.3.1.4), for the tests."""
    size = dci_1c_bits(n_rb)
    w = size - 5 - (1 if n_rb >= 50 else 0)
    fields = ((gap2, 1),) if n_rb >= 50 else ()
    fields += ((riv, w), (tbs_index, 5))
    return np.array([(v >> (n - 1 - i)) & 1 for v, n in fields for i in range(n)], dtype=np.uint8)


# ----- the PDSCH and DL-SCH: SIB1 (36.211 6.2.3, 6.3-6.4; 36.212 5.1.1-5.1.4.1, 5.3.2; 36.213 7.1.6-7.1.7)

#: Turbo code internal interleaver parameters f1, f2 (36.212 table 5.1.3-3), for K = 40 .. 6144
#: in the table's order (``turbo_k``).
_QPP_F1 = (3, 7, 19, 7, 7, 11, 5, 11, 7, 41, 103, 15, 9, 17, 9, 21, 101, 21, 57, 23, 13, 27, 11, 27,
           85, 29, 33, 15, 17, 33, 103, 19, 19, 37, 19, 21, 21, 115, 193, 21, 133, 81, 45, 23, 243, 151,
           155, 25, 51, 47, 91, 29, 29, 247, 29, 89, 91, 157, 55, 31, 17, 35, 227, 65, 19, 37, 41, 39, 185,
           43, 21, 155, 79, 139, 23, 217, 25, 17, 127, 25, 239, 17, 137, 215, 29, 15, 147, 29, 59, 65, 55,
           31, 17, 171, 67, 35, 19, 39, 19, 199, 21, 211, 21, 43, 149, 45, 49, 71, 13, 17, 25, 183, 55,
           127, 27, 29, 29, 57, 45, 31, 59, 185, 113, 31, 17, 171, 209, 253, 367, 265, 181, 39, 27, 127,
           143, 43, 29, 45, 157, 47, 13, 111, 443, 51, 51, 451, 257, 57, 313, 271, 179, 331, 363, 375, 127,
           31, 33, 43, 33, 477, 35, 233, 357, 337, 37, 71, 71, 37, 39, 127, 39, 39, 31, 113, 41, 251, 43,
           21, 43, 45, 45, 161, 89, 323, 47, 23, 47, 263)
_QPP_F2 = (10, 12, 42, 16, 18, 20, 22, 24, 26, 84, 90, 32, 34, 108, 38, 120, 84, 44, 46, 48, 50, 52,
           36, 56, 58, 60, 62, 32, 198, 68, 210, 36, 74, 76, 78, 120, 82, 84, 86, 44, 90, 46, 94, 48, 98,
           40, 102, 52, 106, 72, 110, 168, 114, 58, 118, 180, 122, 62, 84, 64, 66, 68, 420, 96, 74, 76,
           234, 80, 82, 252, 86, 44, 120, 92, 94, 48, 98, 80, 102, 52, 106, 48, 110, 112, 114, 58, 118, 60,
           122, 124, 84, 64, 66, 204, 140, 72, 74, 76, 78, 240, 82, 252, 86, 88, 60, 92, 846, 48, 28, 80,
           102, 104, 954, 96, 110, 112, 114, 116, 354, 120, 610, 124, 420, 64, 66, 136, 420, 216, 444, 456,
           468, 80, 164, 504, 172, 88, 300, 92, 188, 96, 28, 240, 204, 104, 212, 192, 220, 336, 228, 232,
           236, 120, 244, 248, 168, 64, 130, 264, 134, 408, 138, 280, 142, 480, 146, 444, 120, 152, 462,
           234, 158, 80, 96, 902, 166, 336, 170, 86, 174, 176, 178, 120, 182, 184, 186, 94, 190, 480)
#: Sub-block interleaver column permutation for turbo codes (36.212 table 5.1.4-1).
_P_TC = (0, 16, 8, 24, 4, 20, 12, 28, 2, 18, 10, 26, 6, 22, 14, 30,
         1, 17, 9, 25, 5, 21, 13, 29, 3, 19, 11, 27, 7, 23, 15, 31)
#: TBS for P-RNTI, RA-RNTI and SI-RNTI: format 1A's columns N_PRB = N_PRB^1A = 2 and 3 of 36.213
#: table 7.1.7.2.1-1 (I_TBS = I_MCS, 0 .. 26), and format 1C's table 7.1.7.2.3-1.
TBS_1A = {2: (32, 56, 72, 104, 120, 144, 176, 224, 256, 296, 328, 376, 440, 488, 552, 600, 632, 696, 776,
              840, 904, 1000, 1064, 1128, 1192, 1256, 1480),
          3: (56, 88, 144, 176, 208, 224, 256, 328, 392, 456, 504, 584, 680, 744, 840, 904, 968, 1064, 1160,
              1288, 1384, 1480, 1608, 1736, 1800, 1864, 2216)}
TBS_1C = (40, 56, 72, 120, 136, 144, 176, 208, 224, 256, 280, 296, 328, 336, 392, 488,
          552, 600, 632, 696, 776, 840, 904, 1000, 1064, 1128, 1224, 1288, 1384, 1480, 1608, 1736)
#: N_gap,2 by bandwidth (36.211 table 6.2.3.2-1), 50 RB and up.
_GAP2 = ((50, 9), (64, 16), (80, 16))
#: Extrinsic information is scaled by this between the two max-log-MAP decoders.
_EXTRINSIC = 0.75
_TURBO_ITERATIONS = 8


def turbo_k():
    """The turbo code's block sizes K (36.212 table 5.1.3-3), 40 .. 6144."""
    return np.concatenate((np.arange(40, 512, 8), np.arange(512, 1024, 16), np.arange(1024, 2048, 32),
                           np.arange(2048, 6145, 64)))


def qpp(k):
    """The turbo code internal interleaver Pi(i) = (f1 i + f2 i^2) mod K (36.212 5.1.3.2.3)."""
    j = int(np.searchsorted(turbo_k(), k))
    i = np.arange(k, dtype=np.int64)
    return (_QPP_F1[j] * i + _QPP_F2[j] * i * i) % k


def segmentation(b):
    """Code block segmentation of ``b`` bits (36.212 5.1.2): (C, [K_r], F, L)."""
    z = 6144
    if b <= z:
        c, l, bp = 1, 0, b
    else:
        l = 24
        c = -(-b // (z - l))
        bp = b + c * l
    ks = turbo_k()
    kp = int(ks[np.searchsorted(ks, -(-bp // c))])
    if c == 1:
        sizes = [kp]
    else:
        km = int(ks[ks < kp][-1])
        cm = (c * kp - bp) // (kp - km)
        sizes = [km] * cm + [kp] * (c - cm)
    return c, sizes, sum(sizes) - bp, l


_RSC_NEXT = np.zeros((8, 2), dtype=np.int64)       # state r1 r2 r3 (r1 the newest, bit 2)
_RSC_PARITY = np.zeros((8, 2), dtype=np.int64)
for _s in range(8):
    _r1, _r2, _r3 = (_s >> 2) & 1, (_s >> 1) & 1, _s & 1
    for _u in (0, 1):
        _a = _u ^ _r2 ^ _r3                              # g0 = 1 + D^2 + D^3, the feedback
        _RSC_NEXT[_s, _u] = (_a << 2) | (_r1 << 1) | _r2
        _RSC_PARITY[_s, _u] = _a ^ _r1 ^ _r3             # g1 = 1 + D + D^3
_RSC_PRED = [np.array([(s, u) for s in range(8) for u in (0, 1) if _RSC_NEXT[s, u] == n]) for n in range(8)]
_RSC_PS = np.array([p[:, 0] for p in _RSC_PRED])      # (8, 2): the two states before n
_RSC_PU = np.array([p[:, 1] for p in _RSC_PRED])      # and their inputs


def _rsc(c):
    """One constituent encoder (36.212 5.1.3.2.1): the parity bits z of ``c``, then the three
    tail steps' (x, z)."""
    s, z = 0, np.zeros(len(c), dtype=np.uint8)
    for i, u in enumerate(c):
        z[i] = _RSC_PARITY[s, int(u)]
        s = _RSC_NEXT[s, int(u)]
    tail = []
    for _ in range(3):
        u = ((s >> 1) ^ s) & 1                           # the feedback: the register fills with 0
        tail.append((u, int(_RSC_PARITY[s, u])))
        s = _RSC_NEXT[s, u]
    return z, tail


def turbo_encode(c):
    """The rate 1/3 turbo code (36.212 5.1.3.2): d(0), d(1), d(2) of K + 4 bits each, the tail
    as 5.1.3.2.2 (filler bits given as 0)."""
    c = np.asarray(c, dtype=np.uint8)
    k = len(c)
    z, t1 = _rsc(c)
    z2, t2 = _rsc(c[qpp(k)])
    d = np.zeros((3, k + 4), dtype=np.uint8)
    d[0, :k], d[1, :k], d[2, :k] = c, z, z2
    (x0, z0), (x1, z1), (x2, z2_) = t1
    (y0, w0), (y1, w1), (y2, w2) = t2
    d[:, k] = x0, z0, x1
    d[:, k + 1] = z1, x2, z2_
    d[:, k + 2] = y0, w0, y1
    d[:, k + 3] = w1, y2, w2
    return d


def _tc_subblock(d, third):
    """For each of the turbo sub-block interleaver's R x 32 outputs (36.212 5.1.4.1.1), the
    input index or -1 (dummy); ``third`` the d(2) stream's rule."""
    r = -(-d // 32)
    kp = 32 * r
    nd = kp - d
    k = np.arange(kp)
    if third:
        y = (np.array(_P_TC)[k // r] + 32 * (k % r) + 1) % kp
    else:
        y = (k % r) * 32 + np.array(_P_TC)[k // r]
    return np.where(y < nd, -1, y - nd)


def _tc_buffer(k, filler=0):
    """The circular buffer w of a code block of K bits (36.212 5.1.4.1.2): for each of its
    K_w = 3 K_Pi bits, stream * (K + 4) + index, or -1 for a <NULL> (dummies and filler)."""
    d = k + 4
    v = [_tc_subblock(d, i == 2) for i in range(3)]
    w = np.empty(3 * len(v[0]), dtype=np.int64)
    kp = len(v[0])
    w[:kp] = v[0]
    w[kp::2] = np.where(v[1] < 0, -1, d + v[1])
    w[kp + 1::2] = np.where(v[2] < 0, -1, 2 * d + v[2])
    if filler:
        w[(w >= 0) & (w % d < filler) & (w < 2 * d)] = -1   # d(0) and d(1)'s filler are <NULL>
    return w


def _tc_selection(k, e_len, rv, filler=0):
    """Which coded bit (stream * (K + 4) + index) each of the ``e_len`` bits sent with
    redundancy version ``rv`` carries (36.212 5.1.4.1.2, N_cb = K_w)."""
    w = _tc_buffer(k, filler)
    r = len(w) // 96
    k0 = r * (2 * int(np.ceil(len(w) / (8 * r))) * int(rv) + 2)
    order = np.roll(w, -k0)
    valid = order[order >= 0]
    return valid[np.arange(e_len) % len(valid)]


def tc_rate_match(d, e_len, rv, filler=0):
    """The ``e_len`` bits sent of a turbo code block ``d`` (3, K + 4) with redundancy version ``rv``."""
    d = np.asarray(d, dtype=np.uint8)
    return d.reshape(-1)[_tc_selection(d.shape[1] - 4, e_len, rv, filler)]


def tc_rate_recover(llr_e, k, rv, buffer=None, filler=0):
    """A code block's soft bits (3, K + 4) from rate-matched ``llr_e``, added into ``buffer``
    (soft combining of redundancy versions) when given."""
    llr_e = np.asarray(llr_e, dtype=np.float64)
    out = np.zeros(3 * (k + 4)) if buffer is None else np.asarray(buffer, dtype=np.float64).reshape(-1).copy()
    np.add.at(out, _tc_selection(k, len(llr_e), rv, filler), llr_e)
    return out.reshape(3, k + 4)


def _max_log_map(ls, lp, la, tail_s, tail_p):
    """A posteriori LLRs of a constituent code's K inputs (max-log-MAP; positive means 0)."""
    k = len(ls)
    xs = np.array([1.0, -1.0])
    xp = 1.0 - 2.0 * _RSC_PARITY
    sys_ = np.concatenate((ls + la, tail_s))
    par = np.concatenate((lp, tail_p))
    gamma = 0.5 * (sys_[:, None, None] * xs[None, None, :] + par[:, None, None] * xp[None, :, :])
    n = k + 3
    alpha = np.full((n + 1, 8), -1e30)
    alpha[0, 0] = 0.0
    for i in range(n):
        a = alpha[i][_RSC_PS] + gamma[i][_RSC_PS, _RSC_PU]
        m = a.max(axis=1)
        alpha[i + 1] = m - m.max()
    beta = np.full(8, -1e30)
    beta[0] = 0.0
    out = np.empty(k)
    for i in range(n - 1, -1, -1):
        b = gamma[i] + beta[_RSC_NEXT]                 # (8, 2)
        if i < k:
            t = alpha[i][:, None] + b
            out[i] = t[:, 0].max() - t[:, 1].max()
        m = b.max(axis=1)
        beta = m - m.max()
    return out


def turbo_decode(llr, k, check=None, iterations=_TURBO_ITERATIONS, filler=0):
    """The K bits of a turbo code block from its soft bits (3, K + 4) (positive means 0):
    iterated max-log-MAP. ``check(bits)`` (a CRC, say) stops early when true. Returns
    ``(bits, iterations used, passed)``."""
    llr = np.asarray(llr, dtype=np.float64)
    ls, lp1, lp2 = llr[0, :k].copy(), llr[1, :k], llr[2, :k]
    if filler:
        ls[:filler] = 1e3                                # known zeros
    t = llr[:, k:]
    t1s, t1p = np.array([t[0, 0], t[2, 0], t[1, 1]]), np.array([t[1, 0], t[0, 1], t[2, 1]])
    t2s, t2p = np.array([t[0, 2], t[2, 2], t[1, 3]]), np.array([t[1, 2], t[0, 3], t[2, 3]])
    pi = qpp(k)
    la = np.zeros(k)
    bits = (ls < 0).astype(np.uint8)
    for it in range(1, iterations + 1):
        l1 = _max_log_map(ls, lp1, la, t1s, t1p)
        e1 = (l1 - ls - la) * _EXTRINSIC
        la2 = e1[pi]
        l2 = _max_log_map(ls[pi], lp2, la2, t2s, t2p)
        e2 = (l2 - ls[pi] - la2) * _EXTRINSIC
        la = np.empty(k)
        la[pi] = e2
        app = np.empty(k)
        app[pi] = l2
        bits = (app < 0).astype(np.uint8)
        if check is not None and check(bits):
            return bits, it, True
    return bits, iterations, check is None


def _block_e(g, c, r, unit):
    """E of code block ``r`` of ``c`` from G bits, in units of N_L Q_m bits (36.212 5.1.4.1.2)."""
    gp = g // unit
    return unit * (gp // c if r <= c - (gp % c) - 1 else -(-gp // c))


def dlsch_encode(tb, e_len, rv, ports=1):
    """The ``e_len`` bits a transport block ``tb`` is sent as (36.212 5.3.2: CRC 24A,
    segmentation, turbo code, rate matching with ``rv``), QPSK on ``ports`` (N_L 2 for
    transmit diversity)."""
    tb = np.asarray(tb, dtype=np.uint8)
    b = np.concatenate((tb, _crc(tb, '24A')))
    c, sizes, f, l = segmentation(len(b))
    out, at = [], 0
    unit = 2 if ports == 1 else 4
    for r, kr in enumerate(sizes):
        n = kr - l - (f if r == 0 else 0)
        blk = b[at:at + n]
        at += n
        if r == 0 and f:
            blk = np.concatenate((np.zeros(f, dtype=np.uint8), blk))
        if l:
            blk = np.concatenate((blk, _crc(blk, '24B')))
        e = _block_e(e_len, c, r, unit)
        out.append(tc_rate_match(turbo_encode(blk), e, rv, f if r == 0 else 0))
    return np.concatenate(out)


def dlsch_buffers(llr, tbs, rv, buffers=None, ports=1):
    """The code blocks' soft buffers (a list, one (3, K + 4) array a block) of a transport block
    of ``tbs`` bits, its descrambled soft bits ``llr`` (sent with ``rv``) added into
    ``buffers`` when given (soft combining)."""
    llr = np.asarray(llr, dtype=np.float64)
    c, sizes, f, _l = segmentation(int(tbs) + 24)
    unit = 2 if ports == 1 else 4
    buffers = [None] * c if buffers is None else list(buffers)
    at = 0
    for r, kr in enumerate(sizes):
        e = _block_e(len(llr), c, r, unit)
        buffers[r] = tc_rate_recover(llr[at:at + e], kr, rv, buffers[r], f if r == 0 else 0)
        at += e
    return buffers


def dlsch_decode(llr, tbs, rv, buffers=None, ports=1):
    """A transport block of ``tbs`` bits from its descrambled soft bits (sent with ``rv``), or
    None when the CRC fails, after adding them into ``buffers`` (``dlsch_buffers``; None for a
    copy alone). Returns ``(bits or None, buffers)``."""
    _c, sizes, f, l = segmentation(int(tbs) + 24)
    buffers = dlsch_buffers(llr, tbs, rv, buffers, ports)
    blocks = []
    for r, kr in enumerate(sizes):
        fr = f if r == 0 else 0
        kind = '24B' if l else '24A'
        check = (lambda x, fr=fr, kind=kind: np.array_equal(_crc(x[fr:-24], kind), x[-24:]))
        bits, _it, ok = turbo_decode(buffers[r], kr, check, filler=fr)
        if not ok:
            return None, buffers
        blocks.append(bits[fr:kr - l])
    b = np.concatenate(blocks)
    if not np.array_equal(_crc(b[:-24], '24A'), b[-24:]):
        return None, buffers
    return b[:-24], buffers


def riv_decode(riv, n):
    """(RB_start, L_CRBs) from a type 2 resource indication value over ``n`` RBs (36.213 7.1.6.3)."""
    a, b = divmod(int(riv), int(n))
    if a + b < n:
        return b, a + 1
    return n - 1 - b, n - a + 1


def n_gap2(n_rb):
    """N_gap,2 (36.211 table 6.2.3.2-1), 50 RB and up."""
    return [g for lo, g in _GAP2 if n_rb >= lo][-1]


def n_vrb(n_rb, gap2=False):
    """N_VRB^DL of distributed VRBs (36.211 6.2.3.2) with the first or second gap."""
    if not gap2:
        g = n_gap1(n_rb)
        return 2 * min(g, n_rb - g)
    g = n_gap2(n_rb)
    return (n_rb // (2 * g)) * 2 * g


def rbg_size(n_rb):
    """P, the resource block group size (36.213 table 7.1.6.1-1)."""
    return 1 if n_rb <= 10 else 2 if n_rb <= 26 else 3 if n_rb <= 63 else 4


def distributed_prb(v, n_rb, gap2, slot):
    """The PRB distributed VRB ``v`` is sent on in slot ``slot`` (0 or 1) of the subframe
    (36.211 6.2.3.2)."""
    ngap = n_gap2(n_rb) if gap2 else n_gap1(n_rb)
    nt = 2 * ngap if gap2 else n_vrb(n_rb, False)
    p = rbg_size(n_rb)
    n_row = -(-nt // (4 * p)) * p
    n_null = 4 * n_row - nt
    vt = v % nt
    base = nt * (v // nt)
    p1 = 2 * n_row * (vt % 2) + vt // 2 + base
    p2 = n_row * (vt % 4) + vt // 4 + base
    if n_null and vt >= nt - n_null:
        x = p1 - n_row + (n_null // 2 if vt % 2 == 0 else 0)
    elif n_null and vt % 4 >= 2:
        x = p2 - n_null // 2
    else:
        x = p2
    if slot:
        x = (x + nt // 2) % nt + base
    return x if x < nt // 2 else x + ngap - nt // 2


def si_allocation(dci, n_rb):
    """A SIB or SI DCI's PRBs in each slot, ([slot 0 PRBs], [slot 1 PRBs]) (36.213 7.1.6.3)."""
    if dci['format'] == '1C':
        step = 2 if n_rb < 50 else 4
        nv = n_vrb(n_rb, dci['gap2'])
        start, length = riv_decode(dci['riv'], nv // step)
        vrbs, dist = range(start * step, (start + length) * step), True
    else:
        start, length = riv_decode(dci['riv'], n_rb)
        vrbs, dist = range(start, start + length), bool(dci['distributed'])
    if not dist:
        return list(vrbs), list(vrbs)
    return tuple(sorted(distributed_prb(v, n_rb, dci['gap2'], s) for v in vrbs) for s in (0, 1))


def si_tbs(dci):
    """The transport block size of a SIB or SI DCI (36.213 7.1.7.2: I_TBS = I_MCS)."""
    if dci['format'] == '1C':
        return TBS_1C[dci['tbs_index']]
    return TBS_1A[dci['n_prb_1a']][dci['mcs']] if dci['mcs'] < 27 else None


def sib1_rv(sfn):
    """SIB1's redundancy version by its frame for format 1C (36.321 5.3.1): ceil(3 k / 2) mod 4,
    k = (SFN / 2) mod 4."""
    k = (int(sfn) // 2) % 4
    return (-(-3 * k // 2)) % 4


def pdsch_res(pci, n_rb, ports, cfi, subframe, prbs):
    """The REs a PDSCH is sent on, in mapping order (36.211 6.3.5, 6.4), (l array, k array):
    k first over the PRBs of each symbol's slot (``prbs``, one list a slot), then l, from
    symbol ``cfi`` (more than 10 RB), leaving out the CRS of the cell's ports, the
    synchronisation signals' 72 subcarriers in symbols 5 and 6 of subframes 0 and 5, and the
    PBCH's in symbols 7 to 10 of subframe 0."""
    ls, ks = [], []
    lo, hi = 6 * n_rb - 36, 6 * n_rb + 36
    start = cfi if n_rb > 10 else cfi + 1
    for l in range(start, 14):
        slot, s_ = divmod(l, 7)
        crs = set()
        for port in range(ports):
            if s_ in crs_symbols(port):
                crs.add((crs_shift(port, s_, 2 * subframe + slot) + pci % 6) % 6)
        k = np.concatenate([12 * p + np.arange(12) for p in prbs[slot]]) if len(prbs[slot]) else np.zeros(0, int)
        keep = ~np.isin(k % 6, list(crs))
        if (subframe in (0, 5) and l in (5, 6)) or (subframe == 0 and 7 <= l <= 10):
            keep &= (k < lo) | (k >= hi)
        ks.append(k[keep])
        ls.append(np.full(int(keep.sum()), l))
    return np.concatenate(ls).astype(int), np.concatenate(ks).astype(int)


def pdsch_scrambling(pci, subframe, rnti, nbits, q=0):
    """The PDSCH's scrambling (36.211 6.3.1): c_init = n_RNTI 2^14 + q 2^13 + subframe 2^9 + PCI."""
    return _gold(int(rnti) * 16384 + int(q) * 8192 + int(subframe) * 512 + int(pci), nbits)


def pdsch_res_values(tb, pci, n_rb, ports, cfi, subframe, prbs, rv, rnti=SI_RNTI):
    """The transmitter of a SIB's PDSCH, for the tests: [(port, l array, k array, values)]."""
    ls, ks = pdsch_res(pci, n_rb, ports, cfi, subframe, prbs)
    e = dlsch_encode(tb, 2 * len(ls), rv, ports) ^ pdsch_scrambling(pci, subframe, rnti, 2 * len(ls))
    d = ((1 - 2.0 * e[0::2]) + 1j * (1 - 2.0 * e[1::2])) / np.sqrt(2.0)
    y = sfbc_encode(d, ports)
    return [(p, ls, ks, y[p]) for p in range(ports)]


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

    def __init__(self, fs, center_offset_hz, frame_offset, cfo_hz, carrier_hz, known=None):
        self.known = known                              # ``known_cell``s taken out of each grid
        self.apart = None                               # those of other timings, by timing (``_apart``)
        self.fs = float(fs)
        self.shift = float(center_offset_hz) + float(cfo_hz)
        self.t0 = float(frame_offset) / self.fs
        self.scale = 1.0 - float(cfo_hz) / float(carrier_hz) if carrier_hz else 1.0

    def start(self, f):
        return self.t0 + f * 0.010 * self.scale

    def count(self, n_total):
        return max(0, int((n_total / self.fs - self.t0 - 0.0012) / (0.010 * self.scale)))

    def cut(self, x, i0, n_in, rate, cancel=True):
        """``iqnr._cut`` of ``x[i0:i0 + n_in]`` to ``rate`` at this cell's shift, the known cells
        of other timings taken out of it (unless not ``cancel``): ``_known_samples``."""
        y = _cut(x, self.fs, i0, n_in, self.shift, rate)
        if self.known and cancel:
            if self.apart is None:
                self.apart = _apart(self.known, self)
            for group in self.apart:
                y = y - _known_samples(group, x, i0 / self.fs, len(y), rate, self.shift)
        return y

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
        y = self.cut(x, i0, n_in, _PB_RATE)
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
        return _cancel_grid(out, self.known, t_f, 0, _PB_SYMBOLS, central=True) if self.known else out

    def subframe(self, x, f, sf, n_rb, n_symb=14, cancel=True):
        """Frame ``f``'s subframe ``sf``: its first ``n_symb`` symbols on every subcarrier,
        (n_symb, 12 n_rb), k = 0 lowest (DC not counted), the known cells taken out (unless not
        ``cancel``), or None."""
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
        y = self.cut(x, i0, n_in, rate, cancel)
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
        return _cancel_grid(out, self.known, self.start(f), sf, n_symb) if self.known and cancel else out


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


def pbch(iq, fs, cell, center_offset_hz=0.0, carrier_hz=None, cfo_hz=None, known=None):
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
    frames = _PbFrames(fs, center_offset_hz, offset, cfo_hz, carrier_hz, known)
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


def _cell_frames(fs, cell, pbch_result, center_offset_hz, carrier_hz, known=None):
    offset = float(cell['frame_offset_samples'])
    if int(cell.get('half_frame') or 0) == 1:
        offset -= 0.005 * float(fs)
    offset %= 0.010 * float(fs)
    return _PbFrames(fs, center_offset_hz, offset, pbch_result['cfo_hz'], carrier_hz, known)


def _dci_sum(run, use, n_rb, rnti):
    """The DCI (format, payload bits, fields, level, first CCE) the soft bits of ``run`` hold
    summed, candidate by candidate, or None."""
    n_cce = min(len(m[1]) for m in run) // 72
    for level, cce0 in common_candidates(n_cce):
        seg = sum(m[1][72 * cce0:72 * (cce0 + level)] for m in run)
        for fmt, size in use:
            a = decode_dci(seg, size, rnti)
            if a is None or codeword_score(seg, 1.0 - 2.0 * encode_dci(a, rnti, level)) < _DCI_SUM_SCORE:
                continue
            fields = parse_dci_1a(a, n_rb) if fmt == '1A' else parse_dci_1c(a, n_rb)
            if fmt == '1A' and not fields['format1a']:
                continue
            return fmt, a, fields, level, cce0
    return None


def _dci_sums(missed, sizes, n_rb, rnti):
    """SIB1 DCIs from the subframes none was found in, ``missed`` [(sfn, llr, power, cfi)] (the
    descrambled PDCCH soft bits in CCE order, each CCE's mean |h|^2 / noise, the CFI), their
    candidates summed (roadmap Round 29.4). Format 1A carries the RV, which follows
    k = (SFN / 2) mod 4, so its copies are summed by k; 1C's by k, then all together. Up to
    ``_COMBINE_PERIODS`` copies in a row, a decode after each; a sum is kept when its CRC passes
    and its soft bits hold the re-encoded DCI (``_DCI_SUM_SCORE``). Returns DCI dicts, one per
    subframe of a sum (``combined``: the sum's SFNs)."""
    groups = [(sizes, [[m for m in missed if (m[0] // 2) % 4 == k] for k in range(4)]),
              ([s for s in sizes if s[0] == '1C'], [missed])]
    out, done = [], set()
    for use, runs in groups:
        for items in runs:
            run = []
            for item in items:
                if item[0] in done:
                    continue
                run = run[-(_COMBINE_PERIODS - 1):] + [item]
                got = _dci_sum(run, use, n_rb, rnti) if len(run) > 1 else None
                if got is None:
                    continue
                _fmt, a, fields, level, cce0 = got
                sfns = [m[0] for m in run]
                for m in run:
                    out.append(dict(fields, sfn=m[0], level=level, cce=cce0, cfi=m[3],
                                    bits=''.join(str(int(b)) for b in a), combined=sfns,
                                    snr_db=float(10 * np.log10(max(np.mean(m[2][cce0:cce0 + level]), 1e-12)))))
                done.update(sfns)
                run = []
    return out


def _read_control(g, pci, n_rb, ports, mib, quads, sizes, rnti):
    """One subframe's control region (``g``, its first symbols): the CFI from the PCFICH, then
    the common search space's candidates for both ``sizes``. Returns ``(cfi, dcis, miss)``:
    each DCI's fields with ``level``, ``cce``, ``bits``, ``snr_db``; ``miss`` the soft bits in
    CCE order and each CCE's |h|^2 / noise, for summing (``_dci_sums``)."""
    h, noise = subframe_channel(g, pci, 5, ports, list(range(4)))
    pk = [(0, k + j) for k in pcfich_starts(pci, n_rb) for j in range(6) if (k + j) % 3 != pci % 3]
    r = np.array([g[l, k] for l, k in pk])
    hh = np.array([[h[p, l, k] for l, k in pk] for p in range(4)])
    cfi, _score = decode_pcfich(r, hh, noise, pci, 5, ports)
    if cfi not in quads:
        quads[cfi] = pdcch_quads(pci, n_rb, ports, cfi, mib['phich_resource'], mib['phich_duration'])
    q = quads[cfi]
    ls = np.array([l for l, ks in q for _k in ks])
    ks = np.array([k for _l, kk in q for k in kk])
    r = g[ls, ks]
    hh = h[:, ls, ks]
    llr = soft_bits(r, hh[:ports], noise, ports) * (1.0 - 2.0 * pdcch_scrambling(pci, 5, 2 * len(r)))
    n_cce = len(q) // 9
    taken, found = set(), []
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
            found.append(dict(fields, level=level, cce=cce0, bits=''.join(str(int(b)) for b in a),
                              snr_db=float(10 * np.log10(max(sig / noise, 1e-12)))))
            break
    power = np.abs(hh[0]) ** 2
    power = np.array([np.mean(power[36 * c:36 * (c + 1)]) for c in range(n_cce)]) / noise
    return cfi, found, (llr[:72 * n_cce], power)


def sib1_dcis(iq, fs, cell, pbch_result, center_offset_hz=0.0, carrier_hz=None, rnti=SI_RNTI, combine=True,
              known=None):
    """SIB1's DCIs (formats 1A and 1C with CRC scrambled by SI-RNTI) of one cell, in subframe 5
    of every even frame, by our own PCFICH and PDCCH decode (roadmap Round 29.2); with
    ``combine``, the subframes none was found in summed (``_dci_sums``, 29.4).

    ``pbch_result`` is ``pbch``'s for the cell (its MIB: bandwidth, PHICH, ports, SFN). Each
    subframe: the CFI from the PCFICH, then the common search space's candidates (level 8,
    then 4) for both sizes. Returns a dict: ``cfi`` (counts), ``subframes`` (read) and ``dcis``,
    one per DCI: ``sfn``, ``level``, ``cce``, ``format`` and its fields, ``bits``, ``snr_db``
    (``combined``, the SFNs summed, on one found by summing), in SFN order of the span."""
    mib = (pbch_result or {}).get('mib')
    if not mib or not mib.get('n_rb') or mib['n_rb'] not in _NFFT_FOR_RB:
        return None
    pci, n_rb, ports = int(cell['pci']), int(mib['n_rb']), int(pbch_result['ports'])
    frames = _cell_frames(fs, cell, pbch_result, center_offset_hz, carrier_hz, known)
    sizes = (('1A', dci_1a_bits(n_rb)), ('1C', dci_1c_bits(n_rb)))
    quads, missed = {}, []
    out = {'cfi': {}, 'subframes': 0, 'dcis': []}
    for f in range(frames.count(len(iq))):
        sfn = (int(mib['sfn']) + f) % 1024
        if sfn % 2:
            continue
        g = frames.subframe(iq, f, 5, n_rb, n_symb=9)
        if g is None:
            continue
        out['subframes'] += 1
        cfi, found, miss = _read_control(g, pci, n_rb, ports, mib, quads, sizes, rnti)
        if not found and known:
            # A subframe the known cells' fit left nothing in is read as it is too: taking them
            # out turned a marginal PCFICH once (the 700 MHz pair, 2026-10-26).
            again = _read_control(frames.subframe(iq, f, 5, n_rb, n_symb=9, cancel=False), pci, n_rb, ports,
                                  mib, quads, sizes, rnti)
            if again[1]:
                cfi, found = again[0], again[1]
        out['cfi'][cfi] = out['cfi'].get(cfi, 0) + 1
        for d in found:
            out['dcis'].append(dict(d, sfn=sfn, cfi=cfi))
        if not found:
            missed.append((sfn,) + miss + (cfi,))
    if combine and len(missed) > 1:
        out['dcis'] += _dci_sums(missed, sizes, n_rb, rnti)
        start = int(mib['sfn'])                           # the span's order: the SFN may wrap
        out['dcis'].sort(key=lambda d: (d['sfn'] - start) % 1024)
    return out


#: The most 80 ms periods summed across (``sib1``; copies of one k for the PDCCH, ``_dci_sums``),
#: so a SIB1 or DCI that changed drops out of the sum.
_COMBINE_PERIODS = 4
#: How strongly a summed candidate's soft bits must hold its re-encoded DCI (``codeword_score``,
#: about 0 +/- 1 where none is sent): a guard on the 16-bit CRC, tried many times over in sums.
_DCI_SUM_SCORE = 5.0


def _sum_copies(items, ports, every=True):
    """One transport block from copies ``items`` [(sfn, tbs, rv, llr)] of the same TBS, their
    soft buffers summed: a decode after each copy (``every``) or once at the end. Returns
    ``{'sfns', 'tbs', 'hex'}`` or None."""
    buffers = None
    for i, (_sfn, tbs, rv, llr) in enumerate(items):
        if tbs != items[0][1]:
            return None
        if not every and i < len(items) - 1:
            buffers = dlsch_buffers(llr, tbs, rv, buffers, ports)
            continue
        bits, buffers = dlsch_decode(llr, tbs, rv, buffers, ports)
        if bits is not None and i > 0:
            return {'sfns': [it[0] for it in items[:i + 1]], 'tbs': int(tbs),
                    'hex': np.packbits(bits).tobytes().hex()}
    return None


def sib1(iq, fs, cell, pbch_result, dci_result=None, center_offset_hz=0.0, carrier_hz=None, rnti=SI_RNTI,
         combine=True, known=None):
    """SIB1 of one cell by our own PDSCH and DL-SCH decode (roadmap Round 29.3): each SIB1 DCI
    (``sib1_dcis``, run here when ``dci_result`` is None) decoded alone, in its subframe 5;
    then, with ``combine``, copies summed (29.4).

    The DCI gives the PRBs (type 2, localized or distributed), the TBS (I_TBS = I_MCS: 1A's
    column N_PRB^1A, 1C's table) and the redundancy version (1A's field; 1C's from the SFN).
    Combining: SIB1 is one transport block for 80 ms (frames SFN mod 8 = 0 .. 7, four copies
    in their redundancy versions), so the soft buffers of a period none of whose copies passed
    are summed, a decode tried after each; the periods that still fail are then summed in turn,
    up to the last ``_COMBINE_PERIODS``, while the TBS holds (SIB1 changes rarely; the CRC is
    the guard).
    Returns a dict: ``blocks``, one per DCI (``sfn``, ``rv``, ``tbs``, ``n_prb``, ``crc``, ``hex``
    when it passed), ``decoded``, ``tried``, ``combined`` (one per summed decode: ``sfns``,
    ``tbs``, ``hex``), ``sib1`` (the hex most decodes agree on, or None), ``dcis`` (the DCI
    result)."""
    mib = (pbch_result or {}).get('mib')
    if dci_result is None:
        dci_result = sib1_dcis(iq, fs, cell, pbch_result, center_offset_hz, carrier_hz, rnti, known=known)
    if not dci_result:
        return None
    pci, n_rb, ports = int(cell['pci']), int(mib['n_rb']), int(pbch_result['ports'])
    frames = _cell_frames(fs, cell, pbch_result, center_offset_hz, carrier_hz, known)
    out = {'blocks': [], 'decoded': 0, 'tried': 0, 'combined': [], 'sib1': None, 'dcis': dci_result}
    soft = []                                            # (sfn, tbs, rv, llr) of the copies that failed
    for d in dci_result['dcis']:
        tbs = si_tbs(d)
        if tbs is None:
            continue
        sfn = int(d['sfn'])
        rv = int(d['rv']) if d['format'] == '1A' else sib1_rv(sfn)
        prbs = si_allocation(d, n_rb)
        g = frames.subframe(iq, (sfn - int(mib['sfn'])) % 1024, 5, n_rb, 14)
        if g is None:
            continue
        out['tried'] += 1
        h, noise = subframe_channel(g, pci, 5, ports, list(range(14)))
        ls, ks = pdsch_res(pci, n_rb, ports, int(d['cfi']), 5, prbs)
        llr = soft_bits(g[ls, ks], h[:ports, ls, ks], noise, ports)
        llr *= 1.0 - 2.0 * pdsch_scrambling(pci, 5, rnti, len(llr))
        bits, _buf = dlsch_decode(llr, tbs, rv, ports=ports)
        block = {'sfn': sfn, 'rv': rv, 'tbs': tbs, 'n_prb': len(prbs[0]), 'crc': bits is not None}
        if bits is not None:
            block['hex'] = np.packbits(bits).tobytes().hex()
            out['decoded'] += 1
        else:
            soft.append((sfn, tbs, rv, llr))
        out['blocks'].append(block)
    if combine:
        passed = {b['sfn'] // 8 for b in out['blocks'] if b['crc']}
        periods = {}
        for item in soft:
            if item[0] // 8 not in passed:
                periods.setdefault(item[0] // 8, []).append(item)
        left = []
        for items in periods.values():                   # in time order, across the SFN wrap too
            got = _sum_copies(items, ports)
            if got:
                out['combined'].append(got)
            else:
                left.append(items)
        run = []
        for items in left:                               # periods that failed even summed, in turn
            if run and run[0][0][1] != items[0][1]:
                run = []
            run = run[-(_COMBINE_PERIODS - 1):] + [items]  # the last few: SIB1 may have changed
            if len(run) > 1:
                got = _sum_copies([it for period in run for it in period], ports, every=False)
                if got:
                    out['combined'].append(got)
                    run = []
    seen = {}
    for b in out['blocks'] + out['combined']:
        if b.get('hex'):
            seen[b['hex']] = seen.get(b['hex'], 0) + 1
    if seen:
        out['sib1'] = max(seen, key=seen.get)
    return out


# ----- cancellation (roadmap Round 29.5) -----------------------------------------------------

#: How far apart (s) a known cell's frames and the reading cell's may start for the known cell
#: to be taken out of its grids: sectors of one site start together, another site does not.
_KNOWN_ALIGN_S = 1e-6
#: Subframes a known cell keeps rebuilt (``_known_subframe``; about 0.7 MB each at 50 RB and 2
#: ports): a frame's 0 and 1 for the PBCH and its 5, a few frames back, not the whole span.
_KNOWN_CACHE = 12


def known_cell(fs, cell, pbch_result, dci_result=None, sib1_result=None, center_offset_hz=0.0,
               carrier_hz=None):
    """What a decoded cell is known to send, for cancelling it under a weaker one (roadmap Round
    29.5): from its PCI and MIB its CRS, PSS, SSS and PBCH in every frame; from ``dci_result``
    (``sib1_dcis``) its PCFICH and SIB1 DCI where a DCI passed its CRC; from ``sib1_result``
    (``sib1``) its SIB1 PDSCH where that copy, alone or summed, passed, and the other copies of
    the same 80 ms TTI (one transport block) where their DCI passed. A DCI found only by
    summing is left out unless its subframe's SIB1 passed alone: no CRC checked that subframe's
    CFI. None without a MIB."""
    mib = (pbch_result or {}).get('mib')
    if not mib or mib.get('n_rb') not in _NFFT_FOR_RB:
        return None
    tbs, alone = {}, set()
    for b in (sib1_result or {}).get('blocks', []):
        if b.get('hex'):
            tbs[int(b['sfn'])] = b['hex']
            alone.add(int(b['sfn']))
    dcis = {int(d['sfn']): d for d in (dci_result or {}).get('dcis', [])
            if 'combined' not in d or int(d['sfn']) in alone}
    for c in (sib1_result or {}).get('combined', []):
        for sfn in c['sfns']:
            tbs[int(sfn)] = c['hex']
    # SIB1's four copies in an 80 ms TTI are one transport block (36.331 5.2.1.2): one known
    # copy rebuilds the others whose DCI passed, where that DCI's size is the block's. Left
    # in the samples, a weaker cell's failed copies held a third cell's under them.
    by_tti = {}
    for sfn, hx in tbs.items():
        by_tti.setdefault(sfn // 8, hx)
    for sfn, d in dcis.items():
        hx = by_tti.get(sfn // 8)
        if sfn not in tbs and hx and si_tbs(d) == len(hx) * 4:
            tbs[sfn] = hx
    return {'pci': int(cell['pci']), 'mib': mib, 'ports': int(pbch_result['ports']),
            'frames': _cell_frames(fs, cell, pbch_result, center_offset_hz, carrier_hz),
            'dcis': dcis, 'tbs': tbs, 'cache': {}}


def _known_subframe(kc, sfn, sf):
    """What a known cell sends in subframe ``sf`` of frame ``sfn``, each (14, 12 N_RB): per
    antenna port its CRS, then per port the rest (PBCH, PCFICH and PDCCH, PDSCH), then the
    synchronisation signals. The CRS is kept apart: a cell may send it at another power than the
    rest (36.213 5.2), which ``_rest_ratio`` measures."""
    key = (sfn, sf)
    if key in kc['cache']:
        return kc['cache'][key]
    pci, mib, ports = kc['pci'], kc['mib'], kc['ports']
    n_rb = int(mib['n_rb'])
    out = [np.zeros((14, 12 * n_rb), dtype=np.complex128) for _ in range(2 * ports + 1)]
    crs, rest, sync = out[:ports], out[ports:2 * ports], out[2 * ports]
    for p in range(ports):
        for l in range(14):
            slot, s_ = divmod(l, 7)
            if s_ not in crs_symbols(p):
                continue
            ns = 2 * sf + slot
            k = 6 * np.arange(2 * n_rb) + (crs_shift(p, s_, ns) + pci % 6) % 6
            crs[p][l, k] = crs_values(pci, ns, s_, n_rb)
    mid = 6 * n_rb - 36
    if sf in (0, 5):
        nid1, nid2 = pci // 3, pci % 3
        sync[6, mid + 5:mid + 67] = iqlte._pss_seqs()[nid2]
        sync[5, mid + 5:mid + 67] = iqlte._sss_templates()[nid2, nid1, 0 if sf == 0 else 1]
    if sf == 0:
        y = encode_pbch(dict(mib, sfn=sfn - sfn % 4), pci, ports)[:, sfn % 4]
        res = pbch_res(pci)
        for p in range(ports):
            rest[p][7 + res[:, 0], mid + res[:, 1]] = y[p]
    d = kc['dcis'].get(sfn) if sf == 5 else None
    if d is not None:
        a = np.array([int(b) for b in d['bits']], dtype=np.uint8)
        for p, l, ks, vals in control_res(pci, n_rb, ports, d['cfi'], mib['phich_resource'],
                                          mib['phich_duration'], 5, [(a, d['level'], d['cce'])]):
            rest[p][l, ks] = vals
        hx, tbs = kc['tbs'].get(sfn), si_tbs(d)
        if hx and tbs and len(hx) * 4 >= tbs:
            tb = np.unpackbits(np.frombuffer(bytes.fromhex(hx), dtype=np.uint8))[:tbs]
            rv = sib1_rv(sfn) if d['format'] == '1C' else int(d['rv'])
            for p, ls, ks, vals in pdsch_res_values(tb, pci, n_rb, ports, d['cfi'], 5,
                                                    si_allocation(d, n_rb), rv):
                rest[p][ls, ks] = vals
    kc['cache'][key] = out
    while len(kc['cache']) > _KNOWN_CACHE:                # the oldest out: the readers go in time order
        kc['cache'].pop(next(iter(kc['cache'])))
    return out


def _fit_known(g, sources, tilt=False):
    """The part of grid ``g`` the known ``sources`` (each g's shape) explain: per RB, a gain
    a + b (l - mean l) for each source (with ``tilt``, + c (k - 5.5) across the RB's subcarriers
    too), by least squares on the REs any of them sends on."""
    est = np.zeros(g.shape, dtype=np.complex128)
    mask = np.zeros(g.shape, dtype=bool)
    for q in sources:
        mask |= q != 0
    tl = np.arange(g.shape[0], dtype=float)[:, None] - (g.shape[0] - 1) / 2.0
    tl = np.broadcast_to(tl, g.shape)
    tk = np.broadcast_to(np.arange(12, dtype=float)[None, :] - 5.5, (g.shape[0], 12))
    for c0 in range(0, g.shape[1], 12):
        c = slice(c0, c0 + 12)
        m = mask[:, c]
        if not m.any():
            continue
        cols = []
        for q in sources:
            qq = q[:, c][m]
            if np.any(qq != 0):
                cols += [qq, qq * tl[:, c][m]] + ([qq * tk[m]] if tilt else [])
        a = np.stack(cols, axis=1)
        coef, *_ = np.linalg.lstsq(a, g[:, c][m], rcond=None)
        e = np.zeros(m.shape, dtype=np.complex128)
        e[m] = a @ coef
        est[:, c] = e
    return est


#: An RB shows a port's power ratio of the rest to its CRS (``_rest_ratio``) when it holds at least
#: this many of the rest's REs (the PBCH's RBs, the PDSCH's), and 8 of its CRS.
_RATIO_RES = 24


def _rest_ratio(g, crs, rest):
    """The amplitude ratio of a port's other REs to its CRS in grid ``g``: per RB dense enough
    (``_RATIO_RES``) one gain fitted to each, their ratio, the median over those RBs; None when
    no RB shows it."""
    out = []
    for c0 in range(0, g.shape[1], 12):
        c = slice(c0, c0 + 12)
        qc, qr, gc = crs[:, c], rest[:, c], g[:, c]
        mc, mr = qc != 0, qr != 0
        if mc.sum() < 8 or mr.sum() < _RATIO_RES:
            continue
        a = np.vdot(qc[mc], gc[mc]) / np.vdot(qc[mc], qc[mc])
        b = np.vdot(qr[mr], gc[mr]) / np.vdot(qr[mr], qr[mr])
        if abs(a) > 0:
            out.append(float((b / a).real))
    return float(np.median(out)) if out else None


def _known_frame(kc, t):
    """The known cell's frame starting at time ``t`` (s), as its SFN, or None when none starts
    within ``_KNOWN_ALIGN_S``."""
    fr = kc['frames']
    j = int(round((t - fr.t0) / (0.010 * fr.scale)))
    if abs(fr.start(j) - t) > _KNOWN_ALIGN_S:
        return None
    return (int(kc['mib']['sfn']) + j) % 1024


def _cancel_grid(g, known, t, sf, n_symb, central=False):
    """Grid ``g`` (``n_symb`` symbols of subframe ``sf`` of the frame at ``t``; the PBCH grid's
    16 symbols on the central 72 subcarriers with ``central``) less each known cell, in turn."""
    for kc in known or ():
        sfn = _known_frame(kc, t)
        if sfn is None:
            continue
        n_rb = int(kc['mib']['n_rb'])
        if central:
            nxt = _known_subframe(kc, sfn, 1)
            mid = 6 * n_rb - 36
            sources = [np.concatenate((a, b[:2]))[:, mid:mid + 72]
                       for a, b in zip(_known_subframe(kc, sfn, 0), nxt)]
        else:
            if g.shape[1] != 12 * n_rb:
                continue
            sources = [q[:n_symb] for q in _known_subframe(kc, sfn, sf)]
        g = g - _fit_known(g, _merged(kc, g, sources))
    return g


def _merged(kc, g, sources, key='ratios'):
    """A known cell's sources (``_known_subframe``'s, cut to grid ``g``) as it is fitted: per port
    its CRS plus the rest at the cell's power ratio (kept on the cell under ``key``), then the
    synchronisation signals."""
    # The rest rides on its port's CRS channel at the cell's own power ratio (one number a
    # port, from every dense RB so far), not a gain of its own: fitted RB by RB on the few
    # REs a PDCCH has there, it would follow the weaker cell on them and take it out too.
    ports = kc['ports']
    ratios = kc.setdefault(key, [[] for _ in range(ports)])
    merged = []
    for p in range(ports):
        r = _rest_ratio(g, sources[p], sources[ports + p])
        if r is not None and r > 0:
            ratios[p].append(r)
        merged.append(sources[p] + (float(np.median(ratios[p])) if ratios[p] else 1.0) * sources[ports + p])
    return merged + [sources[2 * ports]]


# ----- known cells of another timing (another site): taken out of the samples ---------------

#: A known cell of another timing is taken out only on the reading cell's carrier: their shifts
#: (the carrier's offset and its CFO) within this (Hz), so the subcarriers line up.
_KNOWN_CARRIER_HZ = 2e3


def _apart(known, frames):
    """The known cells on ``frames``' carrier whose frames start elsewhere (more than
    ``_KNOWN_ALIGN_S`` off, modulo a frame), grouped by timing: cells of one site send their
    broadcast on the same REs and are fitted together."""
    def gap(a, b):
        d = (a.t0 - b.t0) % 0.010
        return min(d, 0.010 - d)
    groups = []
    for kc in known:
        fr = kc['frames']
        if abs(fr.shift - frames.shift) > _KNOWN_CARRIER_HZ or gap(fr, frames) <= _KNOWN_ALIGN_S:
            continue
        for grp in groups:
            if gap(grp[0]['frames'], fr) <= _KNOWN_ALIGN_S and grp[0]['mib']['n_rb'] == kc['mib']['n_rb']:
                grp.append(kc)
                break
        else:
            groups.append([kc])
    return groups


#: The other-timing fit's tilt across each RB (``_fit_known``).
_KNOWN_TILT = True


def _known_estimate(group, x, j, sf):
    """What a group of known cells (one timing) sends in their frame ``j``'s subframe ``sf`` as
    received: their grid read at their own timing, their sources fitted together (``_fit_known``),
    (14, 12 N_RB); None off the capture."""
    lead = group[0]
    key = (tuple(kc['pci'] for kc in group), j, sf)
    cache = lead.setdefault('estimates', {})
    if key in cache:
        return cache[key]
    n_rb = int(lead['mib']['n_rb'])
    g = lead['frames'].subframe(x, j, sf, n_rb)
    est = None
    if g is not None:
        # Fitted together; each cell's power ratio measured with those before it taken out (on
        # their own grid, so kept apart from the ratios measured on a reading cell's).
        sources, rest = [], g
        for kc in group:
            merged = _merged(kc, rest, _known_subframe(kc, (int(kc['mib']['sfn']) + j) % 1024, sf), 'apart_ratios')
            sources += merged
            if len(group) > 1:
                rest = rest - _fit_known(rest, merged, _KNOWN_TILT)
        est = _fit_known(g, sources, _KNOWN_TILT)
    cache[key] = est
    while len(cache) > _KNOWN_CACHE:
        cache.pop(next(iter(cache)))
    return est


def _known_samples(group, x, t_a, n, rate, shift):
    """What a group of known cells of another timing adds to a reader's cut: ``n`` samples at
    ``rate`` (a multiple of 15 kHz) from time ``t_a`` (s), mixed by -``shift``. Each of their
    subframes the span touches is estimated at their own timing (``_known_estimate``) and
    modulated again (36.211 6.12) at the reader's sample times: symbol l is
    sum_k a_k exp(j 2 pi k 15 kHz (t - tau_l)) / nfft over its cyclic prefix and useful part, tau_l
    its useful part's start. A reader's band narrower than theirs (the PBCH's 1.92 MS/s) is
    modulated at a multiple of its rate holding all their subcarriers, then cut as ``iqnr._cut``
    cuts the samples: the leakage of the subcarriers outside goes out with them."""
    fr = group[0]['frames']
    n_rb = int(group[0]['mib']['n_rb'])
    nfft = _NFFT_FOR_RB[n_rb]
    rate_k = nfft * 15e3
    cp0, cp1 = 160 * nfft // 2048, 144 * nfft // 2048
    n_r = int(round(rate / 15e3))
    up = nfft // n_r if nfft > n_r and nfft % n_r == 0 else 1
    n_r, rate, n_out, n = n_r * up, rate * up, n, n * up
    k = np.arange(12 * n_rb)
    bins = np.where(k < 6 * n_rb, k - 6 * n_rb, k - 6 * n_rb + 1)
    keep = np.abs(bins) < n_r // 2
    bins = bins[keep]
    out = np.zeros(n, dtype=np.complex128)
    sub = 1e-3 * fr.scale
    s0 = max(0, int(np.floor((t_a - fr.t0) / sub)))
    s1 = int(np.floor((t_a + n / rate - fr.t0) / sub))
    for s in range(s0, s1 + 1):
        j, sf = divmod(s, 10)
        est = _known_estimate(group, x, j, sf)
        if est is None:
            continue
        t_sf = fr.start(j) + sf * sub
        for l in range(14):
            slot, s_ = divmod(l, 7)
            u = slot * (7 * nfft + cp0 + 6 * cp1) + (cp0 + nfft + (s_ - 1) * (cp1 + nfft) + cp1 if s_ else cp0)
            cp = cp0 if s_ == 0 else cp1
            jt = (t_sf + u / rate_k - t_a) * rate            # tau_l in reader samples
            ja = max(0, int(np.ceil(jt - cp * rate / rate_k - 1e-9)))
            jb = min(n, int(np.ceil(jt + n_r - 1e-9)))
            if ja >= jb or not np.any(est[l]):
                continue
            a = int(np.floor(jt))
            spec = np.zeros(n_r, dtype=np.complex128)
            spec[bins % n_r] = est[l, keep] * np.exp(-2j * np.pi * bins * (jt - a) / n_r)
            w = np.fft.ifft(spec) * (n_r / nfft)
            out[ja:jb] += w[(np.arange(ja, jb) - a) % n_r]
    d = fr.shift - shift
    if abs(d) >= 1e-3 and out.any():
        out *= np.exp(2j * np.pi * ((d * (t_a + np.arange(n) / rate)) % 1.0))
    if up > 1:
        q = np.arange(-(n_out // 2), n_out - n_out // 2)
        out = np.fft.ifft(np.roll(np.fft.fft(out).take(q, mode='wrap'), -(n_out // 2))) * (n_out / float(n))
    return out
