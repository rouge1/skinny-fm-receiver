"""5G NR broadcast decoding, numpy only: the PBCH and its MIB (roadmap Round 28.1).

Written from the 3GPP specifications (TS 38.211 V19, 38.212 V19.4.0); the four
tables below are copied from 38.212. No srsRAN code is used: srsRAN is AGPL and
stays outside this repo, as the referee (``nr_si``) the results are checked
against. Broadcast only: this decodes what every phone in range is sent.

The PBCH (38.212 7.1, 38.211 7.3.3) carries 32 bits: the 24-bit BCCH-BCH
message (the MIB) and 8 bits of timing (the SFN's four LSBs, the half-frame
bit, the MSB of k_SSB and two reserved bits). Transmitter, in order:

1. Payload interleaving (7.1.1): the bits are placed by the pattern G, SFN
   bits first, then the half-frame bit, the SSB-index bits, then the rest.
2. Payload scrambling (7.1.2): by the Gold sequence of N_ID = PCI from
   ``v * M`` (``v`` from the SFN's 3rd and 2nd LSBs, M = 29 at L_max 4); the
   SFN's 3rd and 2nd LSBs and the half-frame bit are not scrambled.
3. CRC 24C (7.1.3), then the polar code (7.1.4, 5.3.1): the 56 bits are
   interleaved (pattern Pi, I_IL = 1), put on the 56 most reliable of N = 512
   positions (sequence Q), and encoded by the kernel F^(x)9.
4. Rate matching (7.1.5, 5.4.1): sub-block interleaving (pattern P) and, as
   E = 864 > N, repetition.
5. PBCH scrambling (38.211 7.3.3.1) by the Gold sequence of N_ID from
   ``v * 864`` (``v`` the SSB index's two LSBs at L_max 4), and QPSK onto the
   432 PBCH data REs of symbols 1 to 3 of the SSB, k first, then l.

``decode_pbch`` undoes these on one SSB's grid (``iqnr``'s burst spectrum):
a per-symbol channel estimate from the PBCH DM-RS, QPSK soft bits, PBCH
descrambling, the repeated bits summed, the sub-block interleaving undone, a
successive-cancellation list decoder (list 8) with the CRC choosing the path,
then steps 2 and 1 undone. ``encode_pbch`` is the transmitter, for the tests.

Wrong turns
-----------
(none yet)
"""

import numpy as np

#: PBCH sizes: payload A, CRC, polar K and N, rate-matched E (38.212 7.1).
A_PBCH = 32
L_CRC = 24
K_PBCH = A_PBCH + L_CRC
N_PBCH = 512
E_PBCH = 864
#: The polar list size.
LIST = 8
#: CRC 24C generator, D^24 + D^23 + D^21 + D^20 + D^17 + D^15 + D^13 + D^12 + D^8 + D^4 + D^2 + D + 1.
_CRC24C = (1, 1, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 1)
_SSB_SC = 240
_REF_K = 120
_NFFT = 256

#: Polar sequence Q_0..Q_1023, least reliable first (38.212 table 5.3.1.2-1).
_Q = (
    0, 1, 2, 4, 8, 16, 32, 3, 5, 64, 9, 6, 17, 10, 18, 128, 12, 33, 65, 20, 256, 34, 24, 36,
    7, 129, 66, 512, 11, 40, 68, 130, 19, 13, 48, 14, 72, 257, 21, 132, 35, 258, 26, 513, 80,
    37, 25, 22, 136, 260, 264, 38, 514, 96, 67, 41, 144, 28, 69, 42, 516, 49, 74, 272, 160,
    520, 288, 528, 192, 544, 70, 44, 131, 81, 50, 73, 15, 320, 133, 52, 23, 134, 384, 76, 137,
    82, 56, 27, 97, 39, 259, 84, 138, 145, 261, 29, 43, 98, 515, 88, 140, 30, 146, 71, 262,
    265, 161, 576, 45, 100, 640, 51, 148, 46, 75, 266, 273, 517, 104, 162, 53, 193, 152, 77,
    164, 768, 268, 274, 518, 54, 83, 57, 521, 112, 135, 78, 289, 194, 85, 276, 522, 58, 168,
    139, 99, 86, 60, 280, 89, 290, 529, 524, 196, 141, 101, 147, 176, 142, 530, 321, 31, 200,
    90, 545, 292, 322, 532, 263, 149, 102, 105, 304, 296, 163, 92, 47, 267, 385, 546, 324,
    208, 386, 150, 153, 165, 106, 55, 328, 536, 577, 548, 113, 154, 79, 269, 108, 578, 224,
    166, 519, 552, 195, 270, 641, 523, 275, 580, 291, 59, 169, 560, 114, 277, 156, 87, 197,
    116, 170, 61, 531, 525, 642, 281, 278, 526, 177, 293, 388, 91, 584, 769, 198, 172, 120,
    201, 336, 62, 282, 143, 103, 178, 294, 93, 644, 202, 592, 323, 392, 297, 770, 107, 180,
    151, 209, 284, 648, 94, 204, 298, 400, 608, 352, 325, 533, 155, 210, 305, 547, 300, 109,
    184, 534, 537, 115, 167, 225, 326, 306, 772, 157, 656, 329, 110, 117, 212, 171, 776, 330,
    226, 549, 538, 387, 308, 216, 416, 271, 279, 158, 337, 550, 672, 118, 332, 579, 540, 389,
    173, 121, 553, 199, 784, 179, 228, 338, 312, 704, 390, 174, 554, 581, 393, 283, 122, 448,
    353, 561, 203, 63, 340, 394, 527, 582, 556, 181, 295, 285, 232, 124, 205, 182, 643, 562,
    286, 585, 299, 354, 211, 401, 185, 396, 344, 586, 645, 593, 535, 240, 206, 95, 327, 564,
    800, 402, 356, 307, 301, 417, 213, 568, 832, 588, 186, 646, 404, 227, 896, 594, 418, 302,
    649, 771, 360, 539, 111, 331, 214, 309, 188, 449, 217, 408, 609, 596, 551, 650, 229, 159,
    420, 310, 541, 773, 610, 657, 333, 119, 600, 339, 218, 368, 652, 230, 391, 313, 450, 542,
    334, 233, 555, 774, 175, 123, 658, 612, 341, 777, 220, 314, 424, 395, 673, 583, 355, 287,
    183, 234, 125, 557, 660, 616, 342, 316, 241, 778, 563, 345, 452, 397, 403, 207, 674, 558,
    785, 432, 357, 187, 236, 664, 624, 587, 780, 705, 126, 242, 565, 398, 346, 456, 358, 405,
    303, 569, 244, 595, 189, 566, 676, 361, 706, 589, 215, 786, 647, 348, 419, 406, 464, 680,
    801, 362, 590, 409, 570, 788, 597, 572, 219, 311, 708, 598, 601, 651, 421, 792, 802, 611,
    602, 410, 231, 688, 653, 248, 369, 190, 364, 654, 659, 335, 480, 315, 221, 370, 613, 422,
    425, 451, 614, 543, 235, 412, 343, 372, 775, 317, 222, 426, 453, 237, 559, 833, 804, 712,
    834, 661, 808, 779, 617, 604, 433, 720, 816, 836, 347, 897, 243, 662, 454, 318, 675, 618,
    898, 781, 376, 428, 665, 736, 567, 840, 625, 238, 359, 457, 399, 787, 591, 678, 434, 677,
    349, 245, 458, 666, 620, 363, 127, 191, 782, 407, 436, 626, 571, 465, 681, 246, 707, 350,
    599, 668, 790, 460, 249, 682, 573, 411, 803, 789, 709, 365, 440, 628, 689, 374, 423, 466,
    793, 250, 371, 481, 574, 413, 603, 366, 468, 655, 900, 805, 615, 684, 710, 429, 794, 252,
    373, 605, 848, 690, 713, 632, 482, 806, 427, 904, 414, 223, 663, 692, 835, 619, 472, 455,
    796, 809, 714, 721, 837, 716, 864, 810, 606, 912, 722, 696, 377, 435, 817, 319, 621, 812,
    484, 430, 838, 667, 488, 239, 378, 459, 622, 627, 437, 380, 818, 461, 496, 669, 679, 724,
    841, 629, 351, 467, 438, 737, 251, 462, 442, 441, 469, 247, 683, 842, 738, 899, 670, 783,
    849, 820, 728, 928, 791, 367, 901, 630, 685, 844, 633, 711, 253, 691, 824, 902, 686, 740,
    850, 375, 444, 470, 483, 415, 485, 905, 795, 473, 634, 744, 852, 960, 865, 693, 797, 906,
    715, 807, 474, 636, 694, 254, 717, 575, 913, 798, 811, 379, 697, 431, 607, 489, 866, 723,
    486, 908, 718, 813, 476, 856, 839, 725, 698, 914, 752, 868, 819, 814, 439, 929, 490, 623,
    671, 739, 916, 463, 843, 381, 497, 930, 821, 726, 961, 872, 492, 631, 729, 700, 443, 741,
    845, 920, 382, 822, 851, 730, 498, 880, 742, 445, 471, 635, 932, 687, 903, 825, 500, 846,
    745, 826, 732, 446, 962, 936, 475, 853, 867, 637, 907, 487, 695, 746, 828, 753, 854, 857,
    504, 799, 255, 964, 909, 719, 477, 915, 638, 748, 944, 869, 491, 699, 754, 858, 478, 968,
    383, 910, 815, 976, 870, 917, 727, 493, 873, 701, 931, 756, 860, 499, 731, 823, 922, 874,
    918, 502, 933, 743, 760, 881, 494, 702, 921, 501, 876, 847, 992, 447, 733, 827, 934, 882,
    937, 963, 747, 505, 855, 924, 734, 829, 965, 938, 884, 506, 749, 945, 966, 755, 859, 940,
    830, 911, 871, 639, 888, 479, 946, 750, 969, 508, 861, 757, 970, 919, 875, 862, 758, 948,
    977, 923, 972, 761, 877, 952, 495, 703, 935, 978, 883, 762, 503, 925, 878, 735, 993, 885,
    939, 994, 980, 926, 764, 941, 967, 886, 831, 947, 507, 889, 984, 751, 942, 996, 971, 890,
    509, 949, 973, 1000, 892, 950, 863, 759, 1008, 510, 979, 953, 763, 974, 954, 879, 981,
    982, 927, 995, 765, 956, 887, 985, 997, 986, 943, 891, 998, 766, 511, 988, 1001, 951,
    1002, 893, 975, 894, 1009, 955, 1004, 1010, 957, 983, 958, 987, 1012, 999, 1016, 767, 989,
    1003, 990, 1005, 959, 1011, 1013, 895, 1006, 1014, 1017, 1018, 991, 1020, 1007, 1015,
    1019, 1021, 1022, 1023,
)

#: Polar input-bit interleaver pattern, K_IL^max = 164 (38.212 table 5.3.1.1-1).
_PI_IL_MAX = (
    0, 2, 4, 7, 9, 14, 19, 20, 24, 25, 26, 28, 31, 34, 42, 45, 49, 50, 51, 53, 54, 56, 58, 59,
    61, 62, 65, 66, 67, 69, 70, 71, 72, 76, 77, 81, 82, 83, 87, 88, 89, 91, 93, 95, 98, 101,
    104, 106, 108, 110, 111, 113, 115, 118, 119, 120, 122, 123, 126, 127, 129, 132, 134, 138,
    139, 140, 1, 3, 5, 8, 10, 15, 21, 27, 29, 32, 35, 43, 46, 52, 55, 57, 60, 63, 68, 73, 78,
    84, 90, 92, 94, 96, 99, 102, 105, 107, 109, 112, 114, 116, 121, 124, 128, 130, 133, 135,
    141, 6, 11, 16, 22, 30, 33, 36, 44, 47, 64, 74, 79, 85, 97, 100, 103, 117, 125, 131, 136,
    142, 12, 17, 23, 37, 48, 75, 80, 86, 137, 143, 13, 18, 38, 144, 39, 145, 40, 146, 41, 147,
    148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163,
)

#: Sub-block interleaver pattern P(i) (38.212 table 5.4.1.1-1).
_P_SUB = (
    0, 1, 2, 4, 3, 5, 6, 7, 8, 16, 9, 17, 10, 18, 11, 19, 12, 20, 13, 21, 14, 22, 15, 23, 24,
    25, 26, 28, 27, 29, 30, 31,
)

#: PBCH payload interleaver pattern G(j) (38.212 table 7.1.1-1).
_G_PBCH = (
    16, 23, 18, 17, 8, 30, 10, 6, 24, 7, 0, 5, 3, 2, 1, 4, 9, 11, 12, 13, 14, 15, 19, 20, 21,
    22, 25, 26, 27, 28, 29, 31,
)


# ----- sequences and CRC ------------------------------------------------------------------

_GOLD_CACHE = {}


def _gold(c_init, nbits):
    """The length-31 Gold sequence c(n), n = 0..nbits-1 (38.211 5.2.1), cached per c_init."""
    have = _GOLD_CACHE.get(c_init)
    if have is not None and len(have) >= nbits:
        return have[:nbits]
    total = 1600 + nbits
    x1 = np.zeros(total + 31, dtype=np.uint8)
    x1[0] = 1
    x2 = np.zeros(total + 31, dtype=np.uint8)
    x2[:31] = (int(c_init) >> np.arange(31)) & 1
    for n in range(total):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    c = (x1 ^ x2)[1600:1600 + nbits]
    if len(_GOLD_CACHE) > 256:
        _GOLD_CACHE.clear()
    _GOLD_CACHE[c_init] = c
    return c


def crc24c(bits):
    """The 24 CRC bits of ``bits`` (38.212 5.1, gCRC24C, register starting at zero)."""
    r = [int(b) & 1 for b in bits] + [0] * L_CRC
    for i in range(len(bits)):
        if r[i]:
            for j, g in enumerate(_CRC24C):
                r[i + j] ^= g
    return np.array(r[-L_CRC:], dtype=np.uint8)


def pbch_dmrs(pci, i_bar):
    """The 144 PBCH DM-RS values (38.211 7.4.1.4.1) for symbols 1, 2 and 3: 60, 24, 60."""
    c_init = (2 ** 11) * (i_bar + 1) * (pci // 4 + 1) + (2 ** 6) * (i_bar + 1) + (pci % 4)
    c = _gold(c_init, 288).astype(np.float64)
    r = ((1 - 2 * c[0::2]) + 1j * (1 - 2 * c[1::2])) / np.sqrt(2.0)
    return r[:60], r[60:84], r[84:]


def _ssb_layout(pci):
    """For SSB symbols 1, 2, 3: (DM-RS subcarriers, data subcarriers), k = 0..239 (38.211 7.4.3.1)."""
    v = pci % 4
    out = []
    for sym in (1, 2, 3):
        ks = np.arange(_SSB_SC) if sym != 2 else np.concatenate((np.arange(48), np.arange(192, 240)))
        dm = ks[(ks % 4) == v]
        out.append((dm, ks[(ks % 4) != v]))
    return out


# ----- polar code (38.212 5.3.1, 5.4.1) ---------------------------------------------------

def _info_set(k, n):
    """The ``k`` most reliable of ``n`` positions, in increasing order (no puncturing: E >= N)."""
    qn = [q for q in _Q if q < n]
    return np.sort(np.array(qn[n - k:], dtype=np.int64))


def _input_interleaver(k):
    """Pi(0..k-1) of 38.212 5.3.1.1 for I_IL = 1: c'_m = c_Pi(m)."""
    return np.array([p - (164 - k) for p in _PI_IL_MAX if p >= 164 - k], dtype=np.int64)


def _subblock(n):
    """J(0..n-1) of 38.212 5.4.1.1: y_m = d_J(m)."""
    m = np.arange(n)
    return np.array(_P_SUB, dtype=np.int64)[(32 * m) // n] * (n // 32) + m % (n // 32)


def _transform(u):
    """x = u F^(x)n over GF(2), F = [[1, 0], [1, 1]], natural order (38.212 5.3.1.2)."""
    x = np.array(u, dtype=np.uint8).copy()
    n = len(x)
    step = 1
    while step < n:
        v = x.reshape(-1, 2, step)
        v[:, 0, :] ^= v[:, 1, :]
        step *= 2
    return x


def polar_encode(c, n, e):
    """Rate-matched polar codeword (``e`` bits) of the ``k = len(c)`` bits ``c``, I_IL = 1, E >= N."""
    k = len(c)
    u = np.zeros(n, dtype=np.uint8)
    u[_info_set(k, n)] = np.asarray(c, dtype=np.uint8)[_input_interleaver(k)]
    y = _transform(u)[_subblock(n)]
    return y[np.arange(e) % n]


class _SCL:
    """Successive-cancellation list decoding of one polar codeword, LLRs in natural order."""

    def __init__(self, frozen, size):
        self.frozen = frozen
        self.size = size
        self.pm = np.full(size, 1e12)
        self.pm[0] = 0.0
        self.u = np.zeros((size, len(frozen)), dtype=np.uint8)
        self.i = 0

    def leaf(self, lam):
        i = self.i
        self.i += 1
        if self.frozen[i]:
            self.pm += np.where(lam < 0, np.abs(lam), 0.0)
            return np.zeros((self.size, 1), dtype=np.uint8), np.arange(self.size)
        both = np.concatenate((self.pm + np.where(lam < 0, np.abs(lam), 0.0),
                               self.pm + np.where(lam > 0, np.abs(lam), 0.0)))
        pick = np.argsort(both, kind='stable')[:self.size]
        parent, bit = pick % self.size, (pick // self.size).astype(np.uint8)
        self.pm = both[pick]
        self.u = self.u[parent]
        self.u[:, i] = bit
        return bit[:, None], parent

    def node(self, llr):
        """(the re-encoded bits of this subtree per path, the path order it leaves behind)."""
        if llr.shape[1] == 1:
            return self.leaf(llr[:, 0])
        h = llr.shape[1] // 2
        a, b = llr[:, :h], llr[:, h:]
        left = np.sign(a) * np.sign(b) * np.minimum(np.abs(a), np.abs(b))
        xl, p1 = self.node(left)
        a, b = a[p1], b[p1]
        xr, p2 = self.node(b + (1.0 - 2.0 * xl) * a)
        return np.concatenate((xl[p2] ^ xr, xr), axis=1), p1[p2]


def polar_decode(llr_e, k, n, list_size=LIST):
    """``(c, metric)``: the ``k`` bits whose CRC 24C (the last 24) checks, from the ``e`` rate-matched
    LLRs (positive means 0), or ``(None, None)``. The list's paths are tried best first."""
    e = len(llr_e)
    y = np.zeros(n)
    np.add.at(y, np.arange(e) % n, np.asarray(llr_e, dtype=np.float64))
    d = np.zeros(n)
    d[_subblock(n)] = y
    info = _info_set(k, n)
    frozen = np.ones(n, dtype=bool)
    frozen[info] = False
    dec = _SCL(frozen, list_size)
    dec.node(np.tile(d, (list_size, 1)))
    pi = _input_interleaver(k)
    for p in np.argsort(dec.pm, kind='stable'):
        if dec.pm[p] >= 1e11:
            break
        c = np.zeros(k, dtype=np.uint8)
        c[pi] = dec.u[p, info]
        if np.array_equal(crc24c(c[:k - L_CRC]), c[k - L_CRC:]):
            return c, float(dec.pm[p])
    return None, None


# ----- the PBCH payload (38.212 7.1.1, 7.1.2) ---------------------------------------------

#: Positions in the 32-bit payload a: the SFN's bits (6 MSBs in the MIB, then its 4th to 1st
#: LSBs), the half-frame bit, the three bits after it, and the others, in the order 7.1.1 reads them.
_SFN_POS = (1, 2, 3, 4, 5, 6, 24, 25, 26, 27)
_HRF_POS = 28
_SSB_POS = (29, 30, 31)
_OTHER_POS = (0,) + tuple(range(7, 24))
#: M of 7.1.2 at L_max 4 (A - 3).
_M_PAYLOAD = A_PBCH - 3


def _payload_map():
    """dst[i]: where 7.1.1 puts payload bit a_i in a'."""
    g = _G_PBCH
    dst = np.zeros(A_PBCH, dtype=np.int64)
    for j, i in enumerate(_SFN_POS):
        dst[i] = g[j]
    dst[_HRF_POS] = g[10]
    for j, i in enumerate(_SSB_POS):
        dst[i] = g[11 + j]
    for j, i in enumerate(_OTHER_POS):
        dst[i] = g[14 + j]
    return dst


def _payload_scrambling(pci, v):
    """s_0..s_31 of 7.1.2 at L_max 4: the SFN's 3rd and 2nd LSBs and the half-frame bit are not
    scrambled; the rest take c(j + v M) in turn."""
    g = _G_PBCH
    keep = {g[7], g[8], g[10]}
    c = _gold(int(pci), 4 * _M_PAYLOAD + A_PBCH)
    s = np.zeros(A_PBCH, dtype=np.uint8)
    j = 0
    for i in range(A_PBCH):
        if i in keep:
            continue
        s[i] = c[j + v * _M_PAYLOAD]
        j += 1
    return s


def mib_bits(mib):
    """The 24 bits a_0..a_23 of a BCCH-BCH-Message holding ``mib`` (a dict as ``decode_pbch`` gives)."""
    def bits(value, width):
        return [(int(value) >> (width - 1 - b)) & 1 for b in range(width)]
    out = [0]                                                     # message: mib
    out += bits(int(mib['sfn']) >> 4, 6)
    out += [0 if int(mib['scs_common_khz']) in (15, 60) else 1]
    out += bits(int(mib['ssb_subcarrier_offset']) & 15, 4)
    out += [0 if int(mib['dmrs_typeA_position']) == 2 else 1]
    out += bits(mib['coreset0_index'], 4) + bits(mib['search_space0_index'], 4)
    out += [0 if mib['cell_barred'] else 1]
    out += [0 if mib['intra_freq_reselection'] == 'allowed' else 1]
    out += [0]                                                    # spare
    return out


def encode_pbch(mib, pci, ssb_index):
    """The 432 PBCH QPSK symbols of one SSB (L_max 4) sending ``mib``, whose ``sfn`` and
    ``half_frame`` are this SSB's."""
    sfn = int(mib['sfn'])
    a = np.array(mib_bits(mib) + [(sfn >> 3) & 1, (sfn >> 2) & 1, (sfn >> 1) & 1, sfn & 1,
                                  int(mib['half_frame']), (int(mib['ssb_subcarrier_offset']) >> 4) & 1,
                                  0, 0], dtype=np.uint8)
    ap = np.zeros(A_PBCH, dtype=np.uint8)
    ap[_payload_map()] = a
    v = 2 * ((sfn >> 2) & 1) + ((sfn >> 1) & 1)
    ap ^= _payload_scrambling(pci, v)
    c = np.concatenate((ap, crc24c(ap)))
    e = polar_encode(c, N_PBCH, E_PBCH)
    e ^= _gold(int(pci), 4 * E_PBCH)[(ssb_index % 4) * E_PBCH:(ssb_index % 4 + 1) * E_PBCH]
    return ((1 - 2.0 * e[0::2]) + 1j * (1 - 2.0 * e[1::2])) / np.sqrt(2.0)


def ssb_grid(pbch_symbols, pci, i_bar, pss, sss):
    """The 4 x 240 SSB grid (k = 0..239) with ``pss`` and ``sss`` (127 each) on k = 56..182, the
    DM-RS of ``i_bar`` and the PBCH symbols, for tests."""
    g = np.zeros((4, _SSB_SC), dtype=np.complex128)
    g[0, 56:183] = pss
    g[2, 56:183] = sss
    dm = pbch_dmrs(pci, i_bar)
    at = 0
    for sym, ((ks_dm, ks_data), r) in enumerate(zip(_ssb_layout(pci), dm), start=1):
        g[sym, ks_dm] = r
        g[sym, ks_data] = pbch_symbols[at:at + len(ks_data)]
        at += len(ks_data)
    return g


# ----- decoding ---------------------------------------------------------------------------

#: DM-RS values averaged either side for the channel estimate (each is 4 subcarriers apart).
_SMOOTH = 2


def _estimate(y_dm, r, k_dm, k_data):
    """Channel at ``k_data`` from the DM-RS (least squares, a moving average of +/- _SMOOTH, linear
    between), and the noise variance per RE from what the average leaves."""
    h_ls = y_dm * np.conj(r)
    kern = np.ones(2 * _SMOOTH + 1)
    num = np.convolve(h_ls, kern, mode='same')
    den = np.convolve(np.ones(len(h_ls)), kern, mode='same')
    h_s = num / den
    resid = h_ls - h_s
    w = 2 * _SMOOTH + 1
    noise = float(np.mean(np.abs(resid) ** 2)) * w / (w - 1)
    h = np.interp(k_data, k_dm, h_s.real) + 1j * np.interp(k_data, k_dm, h_s.imag)
    return h, noise


def pbch_llrs(xc, pci, i_bar):
    """The 864 descrambled PBCH soft bits (positive means 0) of one SSB, and the SNR per RE in dB.

    ``xc`` is 4 x 256: the SSB's four symbols as spectra with bin (k - 120) mod 256 for subcarrier
    k, as ``iqnr`` cuts them (timing ramp removed)."""
    xc = np.asarray(xc)
    dm = pbch_dmrs(pci, i_bar)
    soft = []
    sig = noise_sum = 0.0
    for sym, ((k_dm, k_data), r) in enumerate(zip(_ssb_layout(pci), dm), start=1):
        row = xc[sym]
        y_dm = row[(k_dm - _REF_K) % _NFFT]
        y = row[(k_data - _REF_K) % _NFFT]
        h, noise = _estimate(y_dm, r, k_dm, k_data)
        noise = max(noise, 1e-12)
        z = y * np.conj(h)
        scale = 2.0 * np.sqrt(2.0) / noise
        pair = np.empty(2 * len(z))
        pair[0::2] = scale * z.real
        pair[1::2] = scale * z.imag
        soft.append(pair)
        sig += float(np.sum(np.abs(h) ** 2))
        noise_sum += noise * len(h)
    llr = np.concatenate(soft)
    c = _gold(int(pci), 4 * E_PBCH)
    v = i_bar % 4
    llr *= 1.0 - 2.0 * c[v * E_PBCH:(v + 1) * E_PBCH]
    snr = 10 * np.log10(max(sig / max(noise_sum, 1e-30), 1e-12))
    return llr, snr


def decode_pbch(xc, pci, i_bar, list_size=LIST):
    """The MIB of one SSB (L_max 4), or None when no path's CRC checks.

    Returns a dict: ``pci``, ``sfn``, ``ssb_index``, ``half_frame``, ``scs_common_khz``,
    ``ssb_subcarrier_offset`` (k_SSB, 0..31 with the payload's MSB), ``dmrs_typeA_position``,
    ``coreset0_index``, ``search_space0_index``, ``cell_barred``, ``intra_freq_reselection``
    ('allowed' or 'notAllowed'), and ``snr_db`` (per PBCH RE) and ``metric`` (the path metric).
    """
    llr, snr = pbch_llrs(xc, pci, i_bar)
    c, metric = polar_decode(llr, K_PBCH, N_PBCH, list_size)
    if c is None:
        return None
    ap = c[:A_PBCH].copy()
    g = _G_PBCH
    v = 2 * int(ap[g[7]]) + int(ap[g[8]])
    ap ^= _payload_scrambling(pci, v)
    a = ap[_payload_map()]

    def num(bits):
        out = 0
        for b in bits:
            out = (out << 1) | int(b)
        return out
    sfn = (num(a[1:7]) << 4) | num(a[24:28])
    k_ssb = (int(a[29]) << 4) | num(a[8:12])
    return {
        'pci': int(pci),
        'sfn': int(sfn),
        'ssb_index': int(i_bar % 4),
        'half_frame': int(a[28]),
        'scs_common_khz': 15 if a[7] == 0 else 30,
        'ssb_subcarrier_offset': int(k_ssb),
        'dmrs_typeA_position': 2 if a[12] == 0 else 3,
        'coreset0_index': num(a[13:17]),
        'search_space0_index': num(a[17:21]),
        'cell_barred': bool(a[21] == 0),
        'intra_freq_reselection': 'allowed' if a[22] == 0 else 'notAllowed',
        'message_class_extension': bool(a[0]),
        'snr_db': float(snr),
        'metric': metric,
    }
