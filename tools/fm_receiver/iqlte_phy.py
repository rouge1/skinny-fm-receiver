"""LTE broadcast decoding, numpy only: the PBCH and its MIB (roadmap Round 29.1).

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

Wrong turns
-----------
(none yet)
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
