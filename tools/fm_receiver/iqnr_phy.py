"""5G NR broadcast decoding, numpy only: the PBCH and its MIB, the PDCCH that schedules SIB1
and the other SI, and their PDSCH (roadmap Rounds 28.1 to 28.3).

Written from the 3GPP specifications (TS 38.211 V19, 38.212 V19.4.0, 38.213 V19.5.0,
38.214); the tables below are copied from 38.212 and 38.213. No srsRAN code is used: srsRAN
is AGPL and stays outside this repo, as the referee (``nr_si``) the results are checked
against. Broadcast only: this decodes what every phone in range is sent, and only the SI-RNTI.

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

SIB1's PDCCH (Round 28.2). The MIB names CORESET 0 and search space 0:

- ``coreset0``: 38.213 table 13-1 ({15, 15} kHz) gives N_RB, N_symb and the
  offset in RBs from the CORESET's lowest RB to the common RB holding the
  SSB's first subcarrier, which is k_SSB above that RB's start.
- ``type0_occasion``: table 13-11 gives O, M and the first symbol; slots
  n0 = (O 2^mu + floor(i M)) mod 10 and n0 + 1 of frames whose SFN parity is
  floor((O 2^mu + floor(i M)) / 10) mod 2 (38.213 13), i the SSB index.
- ``cce_rbs``: 38.211 7.3.2.2. REGs are numbered time first, a bundle is 6
  REGs (6 / N_symb RBs over every symbol), and CCE j is bundle f(j),
  f(x) = (r C + c + n_shift) mod (N_REG / 6) with x = c R + r, R = 2 and
  n_shift = PCI. So the PCI moves every CCE by one bundle per unit.
- ``type0_candidates``: table 10.1-1 (AL 4, 8, 16 with 4, 2, 1 candidates)
  and the hashing of 38.213 10.1 with Y = 0 in a common search space.
- The transmitter (``encode_pdcch``): DCI format 1_0 with CRC scrambled by
  SI-RNTI, 39 bits at 48 RBs (38.212 7.3.1.2.1, no format identifier); the
  CRC 24C of 24 ones and the payload, its last 16 bits XORed with the RNTI
  (7.3.2); the polar code with n_max 9 and no parity-check bits (7.3.3), N
  from 5.3.1; rate matching to E = 108 x AL (7.3.4): E < N is punctured when
  K / E <= 7/16 (the AL4 case, E 432 against N 512: the first N - E
  sub-block-interleaved bits are dropped and more positions frozen, 5.3.1.2)
  and shortened otherwise; scrambling with c_init = PCI (n_RNTI 0 in a common
  search space, 38.211 7.3.2.3), QPSK, and the REs of the candidate's REGs
  that are not DM-RS, k first across all its RBs, then l (7.3.2.5).
- ``decode_pdcch`` undoes these on one occasion's CORESET grid, every
  candidate, largest first. Per candidate the PDCCH DM-RS (38.211 7.4.1.3,
  N_ID = PCI, subcarriers 1, 5, 9 of each RB, m counted from the CORESET's
  lowest RB) gives a common delay (a matched search over the phase slope)
  and a gain per REG bundle and symbol, since precoding may change between
  bundles. ``parse_dci_1_0_si`` reads the fields, ``riv_to_rbs`` the RBs
  (38.214 5.1.2.2.2).

SIB1's and the other SI's PDSCH (Round 28.3; the channel code is ``iqnr_ldpc``):

- ``pdsch_alloc``: the DCI's time row (38.214 table 5.1.2.1.1-2, default A, for
  SIB1; SIB1's own pdsch-TimeDomainAllocationList for other SI), MCS table 1,
  RBs (VRB n is PRB n from CORESET 0's lowest, or interleaved in bundles of 2,
  38.211 7.3.1.6), DM-RS (single symbol, type 1, port 1000, pos2 as a UE
  assumes for DCI 1_0, 38.214 5.1.6.2; table 7.4.1.1.2-3 with l_d = S + L for
  mapping type A), no data on DM-RS symbols (two CDM groups, data -3 dB to the
  DM-RS), and the TBS (38.214 5.1.3.2: N_RE = min(156, 12 L - 12 x DM-RS
  symbols) per PRB, no overhead for SI-RNTI).
- ``pdsch_llrs``: DM-RS r(m) with the PDCCH's c_init formula (n_SCID 0), m = k / 2
  for even k from the reference point: CORESET 0's subcarrier 0 for SIB1,
  common RB 0 for other SI (``k_ref``). A gain per PRB and DM-RS symbol after
  the PDCCH's delay (one PRB cannot tell a delay from its own gain), linear in
  time between DM-RS symbols; max-log soft bits (QPSK, 16QAM, 64QAM);
  descrambling with c_init = RNTI x 2^15 + PCI.
- ``dlsch_setup``, ``dlsch_soft``, ``dlsch_decode``: 38.212 7.2 (CRC 16 or 24A,
  base graph, segmentation, E per code block) around ``iqnr_ldpc``. The soft
  buffers can be summed across transmissions before ``dlsch_decode``. A block
  of all zero bits is refused: it passes the CRC (whose register starts at 0),
  and is what a decoder settles on when it was given next to nothing.
- ``pdsch_grid`` and ``encode_dlsch`` are the transmitter, for the tests.

Only 15 kHz SSB and common spacing (table 13-1) is covered; other tables are
not copied yet.

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

def polar_n(k, e, n_max):
    """N of 38.212 5.3.1 for ``k`` bits rate-matched to ``e`` (R_min 1/8, n_min 5)."""
    ce = (int(e) - 1).bit_length()                               # ceil(log2 E)
    n1 = ce - 1 if (8 * e <= 9 * 2 ** (ce - 1) and 16 * k < 9 * e) else ce
    n2 = (8 * int(k) - 1).bit_length()                           # ceil(log2(K / R_min))
    return 2 ** max(min(n1, n2, int(n_max)), 5)


def _punctured(k, n, e):
    """True when E < N is met by puncturing (K/E <= 7/16), False by shortening (5.4.1.2)."""
    return 16 * k <= 7 * e


def _info_set(k, n, e=None):
    """The ``k`` most reliable of ``n`` positions, in increasing order, leaving out those that
    rate matching to ``e`` < ``n`` freezes (Q_F,tmp of 38.212 5.3.1.2)."""
    frozen = set()
    if e is not None and e < n:
        j = _subblock(n)
        if _punctured(k, n, e):
            frozen.update(int(v) for v in j[:n - e])
            if 4 * e >= 3 * n:
                top = -((2 * e - 3 * n) // 4)                     # ceil(3N/4 - E/2)
            else:
                top = -((4 * e - 9 * n) // 16)                    # ceil(9N/16 - E/4)
            frozen.update(range(top))
        else:
            frozen.update(int(v) for v in j[e:])
    qn = [q for q in _Q if q < n and q not in frozen]
    return np.sort(np.array(qn[len(qn) - k:], dtype=np.int64))


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
    """Rate-matched polar codeword (``e`` bits) of the ``k = len(c)`` bits ``c``, I_IL = 1, no
    parity-check bits: repetition when E >= N, else puncturing or shortening (5.4.1.2)."""
    k = len(c)
    u = np.zeros(n, dtype=np.uint8)
    u[_info_set(k, n, e)] = np.asarray(c, dtype=np.uint8)[_input_interleaver(k)]
    y = _transform(u)[_subblock(n)]
    if e >= n:
        return y[np.arange(e) % n]
    return y[n - e:] if _punctured(k, n, e) else y[:e]


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


#: The LLR given a shortened bit, known to be 0.
_SURE = 1e4


def polar_decode(llr_e, k, n, list_size=LIST, check=None):
    """``(c, metric)``: the ``k`` bits whose CRC checks, from the ``e`` rate-matched LLRs
    (positive means 0), or ``(None, None)``. The list's paths are tried best first. ``check``
    takes the ``k`` bits and says whether they pass; by default the last 24 are their CRC 24C."""
    llr_e = np.asarray(llr_e, dtype=np.float64)
    e = len(llr_e)
    y = np.zeros(n)
    if e >= n:
        np.add.at(y, np.arange(e) % n, llr_e)
    elif _punctured(k, n, e):
        y[n - e:] = llr_e                                         # the punctured bits stay at 0
    else:
        y[:e] = llr_e
        y[e:] = _SURE
    d = np.zeros(n)
    d[_subblock(n)] = y
    info = _info_set(k, n, e)
    frozen = np.ones(n, dtype=bool)
    frozen[info] = False
    dec = _SCL(frozen, list_size)
    dec.node(np.tile(d, (list_size, 1)))
    pi = _input_interleaver(k)
    if check is None:
        def check(bits):
            return np.array_equal(crc24c(bits[:k - L_CRC]), bits[k - L_CRC:])
    for p in np.argsort(dec.pm, kind='stable'):
        if dec.pm[p] >= 1e11:
            break
        c = np.zeros(k, dtype=np.uint8)
        c[pi] = dec.u[p, info]
        if check(c):
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


# ----- CORESET 0 and the Type0-PDCCH search space (38.213 13, 10.1; 38.211 7.3.2) ----------

#: 38.213 table 13-1, {SS/PBCH block, PDCCH} SCS {15, 15} kHz: index -> (N_RB, N_symb, offset
#: in RBs), all with multiplexing pattern 1; index 15 is reserved.
CORESET0_15_15 = ((24, 2, 0), (24, 2, 2), (24, 2, 4), (24, 3, 0), (24, 3, 2), (24, 3, 4),
                  (48, 1, 12), (48, 1, 16), (48, 2, 12), (48, 2, 16), (48, 3, 12), (48, 3, 16),
                  (96, 1, 38), (96, 2, 38), (96, 3, 38), None)
#: 38.213 table 13-11 (pattern 1, FR1): index -> (O, search space sets per slot, M, first
#: symbol); a first symbol of None is 0 for an even SSB index and N_symb for an odd one.
SS0_FR1 = ((0, 1, 1, 0), (0, 2, 0.5, None), (2, 1, 1, 0), (2, 2, 0.5, None),
           (5, 1, 1, 0), (5, 2, 0.5, None), (7, 1, 1, 0), (7, 2, 0.5, None),
           (0, 1, 2, 0), (5, 1, 2, 0), (0, 1, 1, 1), (0, 1, 1, 2),
           (2, 1, 1, 1), (2, 1, 1, 2), (5, 1, 1, 1), (5, 1, 1, 2))
#: 38.213 table 10.1-1: (aggregation level, candidates) of the Type0-PDCCH CSS set.
TYPE0_CANDIDATES = ((4, 4), (8, 2), (16, 1))
#: The SI-RNTI (38.321 7.1).
SI_RNTI = 0xFFFF
#: Polar n_max of a DCI (38.212 7.3.3).
N_MAX_DCI = 9
#: Subcarriers of a REG that carry PDCCH data, and those that carry its DM-RS (38.211 7.4.1.3.2).
_REG_DATA = np.array([0, 2, 3, 4, 6, 7, 8, 10, 11])
_REG_DMRS = np.array([1, 5, 9])


def coreset0(mib):
    """CORESET 0 of a MIB (``decode_pbch``'s dict) whose SSB and common spacing are 15 kHz:
    ``n_rb``, ``n_symb``, ``offset_rb`` (from table 13-1) and ``k0``, its lowest subcarrier
    counted from the SSB's subcarrier 0 (-k_SSB - 12 offset). None for another spacing or a
    reserved index."""
    if int(mib.get('scs_common_khz') or 0) != 15:
        return None
    row = CORESET0_15_15[int(mib['coreset0_index']) & 15]
    if row is None:
        return None
    n_rb, n_symb, offset = row
    return {'n_rb': n_rb, 'n_symb': n_symb, 'offset_rb': offset,
            'k0': -int(mib['ssb_subcarrier_offset']) - 12 * offset}


def type0_occasion(search_space0_index, ssb_index, n_symb, mu=0):
    """Where the Type0-PDCCH CSS of SSB ``ssb_index`` is monitored (38.213 13, table 13-11):
    ``n0`` (the first of two consecutive slots), ``parity`` (of the SFN of n0's frame),
    ``first_symbol``, ``sets_per_slot``."""
    o, sets, m, first = SS0_FR1[int(search_space0_index) & 15]
    n_slot = 10 * 2 ** mu
    v = int(o * 2 ** mu) + int(np.floor(ssb_index * m))
    if first is None:
        first = 0 if ssb_index % 2 == 0 else int(n_symb)
    return {'n0': v % n_slot, 'parity': (v // n_slot) % 2, 'first_symbol': int(first),
            'sets_per_slot': sets}


def cce_rbs(n_rb, n_symb, pci):
    """For each CCE of CORESET 0, the RBs (counted from the CORESET's lowest) of its one REG
    bundle (L = 6, R = 2, n_shift = PCI, interleaved; 38.211 7.3.2.2). A bundle is 6 / N_symb
    RBs over all N_symb symbols, since REGs are numbered time first."""
    n_bundle = n_rb * n_symb // 6
    c = n_bundle // 2
    out = []
    for x in range(n_bundle):
        f = ((x % 2) * c + x // 2 + int(pci)) % n_bundle
        rb0 = 6 * f // n_symb
        out.append(list(range(rb0, rb0 + 6 // n_symb)))
    return out


def type0_candidates(n_cce, counts=TYPE0_CANDIDATES):
    """(aggregation level, first CCE) of each candidate of a common search space with
    ``counts`` ((level, candidates), by default table 10.1-1's for Type0): Y = 0 in a common
    search space (38.213 10.1); a repeated candidate is listed once."""
    out = []
    for level, count in counts:
        if count <= 0:
            continue
        if level > n_cce:
            continue
        for m in range(count):
            first = level * ((m * n_cce) // (level * count) % (n_cce // level))
            if (level, first) not in out:
                out.append((level, first))
    return out


def pdcch_dmrs(pci, slot, symbol, n_rb):
    """The 3 x ``n_rb`` PDCCH DM-RS values of one symbol (38.211 7.4.1.3.1, N_ID = PCI), from
    the CORESET's lowest RB (CORESET 0's reference point)."""
    c_init = ((1 << 17) * (14 * int(slot) + int(symbol) + 1) * (2 * int(pci) + 1) + 2 * int(pci)) % (1 << 31)
    c = _gold(c_init, 6 * n_rb).astype(np.float64)
    return ((1 - 2 * c[0::2]) + 1j * (1 - 2 * c[1::2])) / np.sqrt(2.0)


def _candidate_res(rbs, n_symb):
    """(data RE (l, k) pairs in mapping order: k first, then l; DM-RS (l, k, m) triples)."""
    rbs = sorted(rbs)
    data = [(l, 12 * rb + k) for l in range(n_symb) for rb in rbs for k in _REG_DATA]
    dmrs = [(l, 12 * rb + k, 3 * rb + j) for l in range(n_symb) for rb in rbs for j, k in enumerate(_REG_DMRS)]
    return np.array(data), np.array(dmrs)


# ----- DCI (38.212 7.3) -------------------------------------------------------------------

def dci_1_0_si_bits(n_rb):
    """Payload size of DCI format 1_0 with CRC scrambled by SI-RNTI (38.212 7.3.1.2.1, FR1, no
    shared spectrum): frequency assignment ceil(log2(N(N+1)/2)), then 4 + 1 + 5 + 2 + 1 + 15."""
    return (n_rb * (n_rb + 1) // 2 - 1).bit_length() + 28


def dci_crc(a, rnti):
    """The 24 CRC bits of a DCI payload ``a``: CRC 24C of 24 ones then ``a``, the last 16
    scrambled by ``rnti``, MSB first (38.212 7.3.2)."""
    p = crc24c(np.concatenate((np.ones(L_CRC, dtype=np.uint8), np.asarray(a, dtype=np.uint8))))
    p[8:] ^= ((int(rnti) >> np.arange(15, -1, -1)) & 1).astype(np.uint8)
    return p


def encode_pdcch(a, rnti, pci, level):
    """The 54 x ``level`` QPSK symbols of a PDCCH carrying DCI ``a`` (CRC, polar with n_max 9
    and rate matching to E = 108 x level; scrambled with c_init = PCI, n_RNTI 0 in a common
    search space, 38.211 7.3.2.3)."""
    a = np.asarray(a, dtype=np.uint8)
    c = np.concatenate((a, dci_crc(a, rnti)))
    e_len = 108 * int(level)
    b = polar_encode(c, polar_n(len(c), e_len, N_MAX_DCI), e_len)
    b ^= _gold(int(pci), e_len)
    return ((1 - 2.0 * b[0::2]) + 1j * (1 - 2.0 * b[1::2])) / np.sqrt(2.0)


def coreset_grid(n_rb, n_symb, pci, slot, first_symbol, pdcchs):
    """The N_symb x 12 N_rb CORESET grid holding ``pdcchs``, (level, first CCE, symbols) each,
    with their DM-RS (in their own REGs only), for tests."""
    g = np.zeros((n_symb, 12 * n_rb), dtype=np.complex128)
    rbs_of = cce_rbs(n_rb, n_symb, pci)
    dm = [pdcch_dmrs(pci, slot, first_symbol + l, n_rb) for l in range(n_symb)]
    for level, cce0, sym in pdcchs:
        rbs = [rb for j in range(cce0, cce0 + level) for rb in rbs_of[j]]
        data, dmrs = _candidate_res(rbs, n_symb)
        g[data[:, 0], data[:, 1]] = sym
        g[dmrs[:, 0], dmrs[:, 1]] = [dm[l][m] for l, _k, m in dmrs]
    return g


#: Timing slopes searched in a candidate's DM-RS, rad per subcarrier (+/- 0.3 is +/- 3 us at 15 kHz).
_PD_SLOPES = np.linspace(-0.3, 0.3, 241)


def pdcch_llrs(grid, pci, slot, first_symbol, n_rb, n_symb, level, cce0):
    """The 108 x ``level`` descrambled soft bits of one candidate, and its SNR per RE in dB.

    ``grid`` is the CORESET's N_symb x 12 N_rb REs (symbol ``first_symbol`` first). A common
    delay is fitted to the candidate's DM-RS (a matched search over the phase slope), then each
    REG bundle and symbol gets its own gain, since precoding may change between bundles."""
    grid = np.asarray(grid)
    rbs_of = cce_rbs(n_rb, n_symb, pci)
    bundles = [rbs_of[j] for j in range(cce0, cce0 + level)]
    rbs = [rb for b in bundles for rb in b]
    data, dmrs = _candidate_res(rbs, n_symb)
    dm = [pdcch_dmrs(pci, slot, first_symbol + l, n_rb) for l in range(n_symb)]
    z = grid[dmrs[:, 0], dmrs[:, 1]] * np.conj([dm[l][m] for l, _k, m in dmrs])
    k_dm = dmrs[:, 1].astype(np.float64)
    group = np.array([(l, rb) for l, k, _m in dmrs for rb in [k // 12]])
    bundle_of = {rb: i for i, b in enumerate(bundles) for rb in b}
    gid = np.array([l * len(bundles) + bundle_of[rb] for l, rb in group])
    n_g = n_symb * len(bundles)
    turn = np.exp(-1j * np.outer(_PD_SLOPES, k_dm))
    sums = np.zeros((len(_PD_SLOPES), n_g), dtype=np.complex128)
    for g in range(n_g):
        sums[:, g] = turn[:, gid == g] @ z[gid == g]
    score = np.sum(np.abs(sums) ** 2, axis=1)
    i = int(np.argmax(score))
    slope = float(_PD_SLOPES[i])
    if 0 < i < len(score) - 1:
        a, b, c = score[i - 1], score[i], score[i + 1]
        den = a - 2 * b + c
        if den < 0:
            slope += 0.5 * (a - c) / den * float(_PD_SLOPES[1] - _PD_SLOPES[0])
    zf = z * np.exp(-1j * slope * k_dm)
    h_g = np.array([np.mean(zf[gid == g]) for g in range(n_g)])
    resid = zf - h_g[gid]
    per = len(zf) / n_g
    noise = max(float(np.mean(np.abs(resid) ** 2)) * per / max(per - 1, 1), 1e-12)
    l_d, k_d = data[:, 0], data[:, 1]
    gid_d = np.array([l * len(bundles) + bundle_of[k // 12] for l, k in data])
    h = h_g[gid_d] * np.exp(1j * slope * k_d)
    zz = grid[l_d, k_d] * np.conj(h)
    scale = 2.0 * np.sqrt(2.0) / noise
    llr = np.empty(2 * len(zz))
    llr[0::2] = scale * zz.real
    llr[1::2] = scale * zz.imag
    llr *= 1.0 - 2.0 * _gold(int(pci), len(llr))
    snr = 10 * np.log10(max(float(np.mean(np.abs(h_g) ** 2)) / noise, 1e-12))
    return llr, snr, slope


def riv_to_rbs(riv, n):
    """(first RB, number of RBs) of a type 1 resource indication value in ``n`` RBs (38.214
    5.1.2.2.2), or None when it names none."""
    a, b = divmod(int(riv), int(n))
    if a + b < n:
        start, length = b, a + 1
    else:
        start, length = n - 1 - b, n - a + 1
    if length < 1 or start < 0 or start + length > n:
        return None
    return start, length


def parse_dci_1_0_si(a, n_rb):
    """The fields of a DCI format 1_0 payload with CRC scrambled by SI-RNTI (38.212 7.3.1.2.1)."""
    w = (n_rb * (n_rb + 1) // 2 - 1).bit_length()
    widths = (('fda', w), ('tdra', 4), ('vrb_to_prb', 1), ('mcs', 5), ('rv', 2), ('sii', 1), ('reserved', 15))
    out, at = {}, 0
    for name, width in widths:
        v = 0
        for bit in a[at:at + width]:
            v = (v << 1) | int(bit)
        out[name] = v
        at += width
    rbs = riv_to_rbs(out['fda'], n_rb)
    out['rb_start'], out['n_rbs'] = rbs if rbs else (None, None)
    out['vrb_to_prb'] = 'interleaved' if out['vrb_to_prb'] else 'non-interleaved'
    return out


def decode_pdcch(grid, pci, slot, first_symbol, n_rb, n_symb, rnti=SI_RNTI, payload_bits=None,
                 list_size=LIST, counts=TYPE0_CANDIDATES):
    """Every candidate (by default the Type0-PDCCH's; ``counts`` as ``type0_candidates`` takes)
    in one CORESET 0 occasion whose CRC checks with ``rnti``:
    a list of dicts ``level``, ``cce``, ``bits`` (the payload, a string of 0 and 1), the DCI
    format 1_0 SI-RNTI fields, ``snr_db``, ``metric``. A candidate inside a larger one that
    decoded is not tried."""
    a_len = dci_1_0_si_bits(n_rb) if payload_bits is None else int(payload_bits)
    k = a_len + L_CRC

    def check(c):
        return np.array_equal(dci_crc(c[:a_len], rnti), c[a_len:])
    found = []
    taken = set()
    for level, cce0 in sorted(type0_candidates(n_rb * n_symb // 6, counts), key=lambda t: -t[0]):
        cces = set(range(cce0, cce0 + level))
        if cces & taken:
            continue
        llr, snr, slope = pdcch_llrs(grid, pci, slot, first_symbol, n_rb, n_symb, level, cce0)
        c, metric = polar_decode(llr, k, polar_n(k, len(llr), N_MAX_DCI), list_size, check)
        if c is None:
            continue
        taken |= cces
        a = c[:a_len]
        out = {'level': level, 'cce': cce0, 'bits': ''.join(str(int(b)) for b in a)}
        if payload_bits is None:
            out.update(parse_dci_1_0_si(a, n_rb))
        out.update(snr_db=float(snr), slope=slope, metric=metric)
        found.append(out)
    return found


# ----- SIB1's PDSCH (38.211 7.3.1, 7.4.1.1; 38.214 5.1.2, 5.1.3, 5.1.6.2; 38.212 7.2) ----

#: 38.214 table 5.1.2.1.1-2 (default A, normal CP), rows 1..16: (mapping type, S, L) with
#: dmrs-TypeA-Position 2, then 3; K0 is 0 in every row.
PDSCH_TDRA_A = ((('A', 2, 12), ('A', 3, 11)), (('A', 2, 10), ('A', 3, 9)), (('A', 2, 9), ('A', 3, 8)),
                (('A', 2, 7), ('A', 3, 6)), (('A', 2, 5), ('A', 3, 4)), (('B', 9, 4), ('B', 10, 4)),
                (('B', 4, 4), ('B', 6, 4)), (('B', 5, 7), ('B', 5, 7)), (('B', 5, 2), ('B', 5, 2)),
                (('B', 9, 2), ('B', 9, 2)), (('B', 12, 2), ('B', 12, 2)), (('A', 1, 13), ('A', 1, 13)),
                (('A', 1, 6), ('A', 1, 6)), (('A', 2, 4), ('A', 2, 4)), (('B', 4, 7), ('B', 4, 7)),
                (('B', 8, 4), ('B', 8, 4)))
#: 38.214 table 5.1.3.1-1 (MCS index table 1): (Qm, R x 1024) for MCS 0..28.
MCS_TABLE_1 = ((2, 120), (2, 157), (2, 193), (2, 251), (2, 308), (2, 379), (2, 449), (2, 526), (2, 602),
               (2, 679), (4, 340), (4, 378), (4, 434), (4, 490), (4, 553), (4, 616), (4, 658), (6, 438),
               (6, 466), (6, 517), (6, 567), (6, 616), (6, 666), (6, 719), (6, 772), (6, 822), (6, 873),
               (6, 910), (6, 948))
#: 38.214 table 5.1.3.2-1: TBS for N_info <= 3824.
TBS_TABLE = (24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128, 136, 144, 152, 160, 168, 176,
             184, 192, 208, 224, 240, 256, 272, 288, 304, 320, 336, 352, 368, 384, 408, 432, 456, 480,
             504, 528, 552, 576, 608, 640, 672, 704, 736, 768, 808, 848, 888, 928, 984, 1032, 1064,
             1128, 1160, 1192, 1224, 1256, 1288, 1320, 1352, 1416, 1480, 1544, 1608, 1672, 1736, 1800,
             1864, 1928, 2024, 2088, 2152, 2216, 2280, 2408, 2472, 2536, 2600, 2664, 2728, 2792, 2856,
             2976, 3104, 3240, 3368, 3496, 3624, 3752, 3824)
#: 38.211 table 7.4.1.1.2-3, single-symbol DM-RS, dmrs-AdditionalPosition pos2 (what a UE
#: assumes for DCI format 1_0, 38.214 5.1.6.2): l_d -> positions; 0 stands for l0.
_DMRS_A_POS2 = {3: (0,), 4: (0,), 5: (0,), 6: (0,), 7: (0,), 8: (0, 7), 9: (0, 7), 10: (0, 6, 9),
                11: (0, 6, 9), 12: (0, 6, 9), 13: (0, 7, 11), 14: (0, 7, 11)}
_DMRS_B_POS2 = {2: (0,), 3: (0,), 4: (0,), 5: (0, 4), 6: (0, 4), 7: (0, 4), 8: (0, 3, 6), 9: (0, 4, 7),
                10: (0, 4, 7), 11: (0, 4, 8), 12: (0, 5, 9), 13: (0, 5, 9)}
#: PDSCH-to-DM-RS amplitude with two DM-RS CDM groups without data (38.214 table 4.1-1: -3 dB).
_DATA_GAIN = 1.0 / np.sqrt(2.0)


def tbs_of(n_re, rate, qm, layers=1):
    """The transport block size (38.214 5.1.3.2) of ``n_re`` REs at code rate ``rate`` (0..1)."""
    n_info = n_re * rate * qm * layers
    if n_info <= 0:
        return 0
    if n_info <= 3824:
        n = max(3, int(np.floor(np.log2(n_info))) - 6)
        q = max(24, (2 ** n) * int(np.floor(n_info / 2 ** n)))
        return next(t for t in TBS_TABLE if t >= q)
    n = int(np.floor(np.log2(n_info - 24))) - 5
    q = max(3840, (2 ** n) * int(round((n_info - 24) / 2 ** n)))
    if rate <= 0.25:
        c = -(-(q + 24) // 3816)
        return 8 * c * -(-(q + 24) // (8 * c)) - 24
    if q > 8424:
        c = -(-(q + 24) // 8424)
        return 8 * c * -(-(q + 24) // (8 * c)) - 24
    return 8 * -(-(q + 24) // 8) - 24


def vrb_to_prb(vrbs, n_rb, interleaved):
    """The PRBs (from CORESET 0's lowest) of ``vrbs`` for DCI format 1_0 with SI-RNTI in the
    Type0 CSS (38.211 7.3.1.6): VRB n is PRB n, or with interleaving bundles of 2 RBs over
    CORESET 0, bundle j = c R + r going to r C + c (R 2, C = floor(N_bundle / 2)), the last
    bundle staying put."""
    vrbs = list(vrbs)
    if not interleaved:
        return vrbs
    n_bundle = -(-n_rb // 2)
    c_cols = n_bundle // 2
    out = []
    for v in vrbs:
        j, within = divmod(v, 2)
        f = n_bundle - 1 if j == n_bundle - 1 else (j % 2) * c_cols + j // 2
        out.append(2 * f + within)
    return out


def sliv_to_sl(sliv):
    """(S, L) of a start and length indicator (38.214 5.1.2.1), or None."""
    a, b = divmod(int(sliv), 14)
    for s, l in ((b, a + 1), (13 - b, 15 - a)):
        if 0 <= s < 14 and 1 <= l <= 14 - s and int(sliv) == (14 * (l - 1) + s if l - 1 <= 7 else 14 * (14 - l + 1) + (13 - s)):
            return s, l
    return None


def pdsch_alloc(dci, dmrs_typea_position, n_rb, tdra=None):
    """Where a DCI 1_0 (SI-RNTI) puts its PDSCH: a dict ``mapping``, ``S``, ``L``, ``dmrs``
    (DM-RS symbols of the slot), ``data_symbols``, ``vrbs``, ``prbs``, ``qm``, ``rate``,
    ``n_re``, ``tbs``; None for a reserved MCS, no RBs, a K0 other than 0 or a row ``tdra``
    lacks. ``tdra`` is SIB1's pdsch-TimeDomainAllocationList as (K0, mapping type 'A' or 'B',
    S, L) rows, for other SI (38.214 table 5.1.2.1.1-1); None is default table A."""
    mcs = int(dci['mcs'])
    if mcs > 28 or not dci.get('n_rbs'):
        return None
    if tdra is None:
        kind, s, l = PDSCH_TDRA_A[int(dci['tdra']) & 15][0 if int(dmrs_typea_position) == 2 else 1]
    else:
        if int(dci['tdra']) >= len(tdra) or int(tdra[int(dci['tdra'])][0]) != 0:
            return None
        _k0, kind, s, l = tdra[int(dci['tdra'])]
    if kind == 'A':
        pos = _DMRS_A_POS2.get(s + l)
        dmrs = tuple(int(dmrs_typea_position) if p == 0 else p for p in pos) if pos else ()
    else:
        pos = _DMRS_B_POS2.get(l)
        dmrs = tuple(s + p for p in pos) if pos else ()
    if not dmrs:
        return None
    shared = kind == 'B' and l == 2                    # data in the DM-RS symbol: one CDM group
    data = tuple(x for x in range(s, s + l) if shared or x not in dmrs)
    qm, r = MCS_TABLE_1[mcs]
    vrbs = list(range(int(dci['rb_start']), int(dci['rb_start']) + int(dci['n_rbs'])))
    prbs = vrb_to_prb(vrbs, n_rb, dci.get('vrb_to_prb') == 'interleaved')
    n_dmrs = (6 if shared else 12) * len(dmrs)
    n_re = min(156, 12 * l - n_dmrs) * len(vrbs)
    return {'mapping': kind, 'S': s, 'L': l, 'dmrs': dmrs, 'data_symbols': data, 'shared_dmrs': shared,
            'vrbs': vrbs, 'prbs': prbs, 'qm': qm, 'rate': r / 1024.0, 'n_re': n_re,
            'tbs': tbs_of(n_re, r / 1024.0, qm)}


def pdsch_dmrs(pci, slot, symbol, n_sc):
    """PDSCH DM-RS r(m), m = 0..n_sc/2 - 1, of one symbol (38.211 7.4.1.1.1, N_ID = PCI, n_SCID
    0): type 1, port 1000, on even subcarriers k = 2m counted from the reference point."""
    return pdcch_dmrs(pci, slot, symbol, n_sc // 6)


def _qam_llr(z, gain2, noise, qm):
    """Max-log soft bits of equalised symbols ``z`` (= y conj(h)), |h|^2 ``gain2``, noise
    variance ``noise`` (38.211 5.1.3-5.1.5 Gray maps; positive means 0)."""
    if qm == 2:
        scale = 2.0 * np.sqrt(2.0) / noise
        out = np.empty((len(z), 2))
        out[:, 0] = scale * z.real
        out[:, 1] = scale * z.imag
        return out.ravel()
    m = qm // 2
    norm = {4: np.sqrt(10.0), 6: np.sqrt(42.0)}[qm]
    bits = np.array([[(v >> (m - 1 - i)) & 1 for i in range(m)] for v in range(2 ** m)])
    level = np.ones(2 ** m)
    for i in range(m - 1, -1, -1):          # x = (1 - 2b0)(2^(m-1) - (1 - 2b1)(2^(m-2) - ...)))
        level = (1 - 2 * bits[:, i]) * (2 ** (m - 1 - i) - level) if i < m - 1 else (1 - 2 * bits[:, i])
    level = level / norm
    out = np.empty((len(z), qm))
    for axis, part in ((0, z.real), (1, z.imag)):
        d = (part[:, None] - gain2[:, None] * level[None, :]) ** 2 / (gain2[:, None] * noise)
        for i in range(m):
            zero = np.min(np.where(bits[None, :, i] == 0, d, np.inf), axis=1)
            one = np.min(np.where(bits[None, :, i] == 1, d, np.inf), axis=1)
            out[:, axis + 2 * i] = one - zero
    return out.ravel()


def pdsch_llrs(grid, pci, slot, alloc, rnti=SI_RNTI, k_ref=0, slope=None):
    """The G descrambled soft bits of one PDSCH, its SNR per RE in dB and the delay slope.

    ``grid`` is the slot's 14 symbols x CORESET 0's subcarriers; ``k_ref`` is CORESET 0's
    subcarrier 0 counted from the DM-RS reference point: 0 for SIB1 (the reference is CORESET
    0), 12 x its lowest common RB for other SI (the reference is common RB 0). ``slope`` (rad
    per subcarrier, the scheduling PDCCH's delay fit) is used when given: one or two PRBs
    cannot tell a delay from their own gains. The channel is estimated per PRB and DM-RS symbol (the
    precoding may change between PRBs), after a common delay is fitted, and is linear in time
    between DM-RS symbols (held beyond them)."""
    grid = np.asarray(grid)
    prbs = alloc['prbs']
    dm_k = np.array([12 * p + 2 * j for p in prbs for j in range(6)])
    dm_l = list(alloc['dmrs'])
    zs = []
    for l in dm_l:
        r = pdsch_dmrs(pci, slot, l, 12 * (max(prbs) + 1) + int(k_ref))
        zs.append(grid[l, dm_k] * np.conj(r[(dm_k + int(k_ref)) // 2]))
    z = np.concatenate(zs)
    k_all = np.tile(dm_k, len(dm_l)).astype(np.float64)
    n_g = len(prbs) * len(dm_l)
    gid = np.repeat(np.arange(n_g), 6)
    if slope is None:
        turn = np.exp(-1j * np.outer(_PD_SLOPES, k_all))
        sums = np.zeros((len(_PD_SLOPES), n_g), dtype=np.complex128)
        np.add.at(sums.T, gid, (turn * z[None, :]).T)
        score = np.sum(np.abs(sums) ** 2, axis=1)
        i = int(np.argmax(score))
        slope = float(_PD_SLOPES[i])
        if 0 < i < len(score) - 1:
            a, b, c = score[i - 1], score[i], score[i + 1]
            den = a - 2 * b + c
            if den < 0:
                slope += 0.5 * (a - c) / den * float(_PD_SLOPES[1] - _PD_SLOPES[0])
    slope = float(slope)
    zf = z * np.exp(-1j * slope * k_all)
    h_g = np.array([zf[gid == g].mean() for g in range(n_g)]).reshape(len(dm_l), len(prbs))
    resid = zf - h_g.ravel()[gid]
    noise = max(float(np.mean(np.abs(resid) ** 2)) * 6 / 5, 1e-12) * _DATA_GAIN ** 2
    soft = []
    k_sc = np.arange(12)
    for l in alloc['data_symbols']:
        if len(dm_l) == 1:
            h_l = h_g[0]
        else:
            h_l = np.array([np.interp(l, dm_l, h_g[:, p].real) + 1j * np.interp(l, dm_l, h_g[:, p].imag)
                            for p in range(len(prbs))])
        sc = (12 * np.array(prbs)[:, None] + k_sc[None, :])                # VRB order, k first
        h = (h_l[:, None] * np.exp(1j * slope * sc)).ravel() * _DATA_GAIN
        sc = sc.ravel()
        if alloc.get('shared_dmrs') and l in dm_l:
            keep = sc % 2 == 1
            sc, h = sc[keep], h[keep]
        y = grid[l, sc]
        soft.append(_qam_llr(y * np.conj(h), np.abs(h) ** 2, noise, alloc['qm']))
    llr = np.concatenate(soft)
    c_init = (int(rnti) << 15) + int(pci)
    llr *= 1.0 - 2.0 * _gold(c_init, len(llr))
    snr = 10 * np.log10(max(float(np.mean(np.abs(h_g) ** 2)) * _DATA_GAIN ** 2 / noise, 1e-12))
    return llr, snr, slope


def dlsch_setup(tbs, rate):
    """The coding of a transport block of ``tbs`` bits (38.212 7.2): ``bg``, ``crc`` ('16' or
    '24A') and the segmentation (``iqnr_ldpc.segment``)."""
    from . import iqnr_ldpc as L
    bg = L.base_graph(tbs, rate)
    kind = '24A' if tbs > 3824 else '16'
    seg = L.segment(tbs + int(kind[:2]), bg)
    return dict(seg, bg=bg, crc=kind, tbs=int(tbs))


def _block_lengths(g, qm, n_blocks):
    """E_r of each code block (38.212 5.4.2.1) for G bits."""
    sym = g // qm
    out = []
    for r in range(n_blocks):
        if r <= n_blocks - (sym % n_blocks) - 1:
            out.append(qm * (sym // n_blocks))
        else:
            out.append(qm * -(-sym // n_blocks))
    return out


def dlsch_soft(llr, setup, rv, qm, buffers=None):
    """Each code block's soft circular buffer from one transmission (summed into ``buffers``)."""
    from . import iqnr_ldpc as L
    out, at = [], 0
    for r, e_len in enumerate(_block_lengths(len(llr), qm, setup['C'])):
        prev = None if buffers is None else buffers[r]
        out.append(L.rate_recover(llr[at:at + e_len], rv, qm, setup['bg'], setup['Zc'], setup['k_prime'],
                                  setup['K'], prev))
        at += e_len
    return out


def dlsch_decode(buffers, setup):
    """The transport block's bytes from its code blocks' buffers, or None when a CRC fails."""
    from . import iqnr_ldpc as L
    a = setup['tbs']
    n_crc = int(setup['crc'][:2])
    bits = []
    for buf in buffers:
        if setup['C'] > 1:
            def check(c):
                return np.array_equal(L.crc(c[:-24], '24B'), c[-24:])
        else:
            def check(c):
                return np.array_equal(L.crc(c[:a], setup['crc']), c[a:a + n_crc])
        c, _it = L.decode(buf, setup['bg'], setup['Zc'], setup['i_ls'], setup['k_prime'], setup['K'],
                          check=check)
        if c is None:
            return None
        bits.append(c[:len(c) - setup['L']])
    b = np.concatenate(bits)
    if not np.array_equal(L.crc(b[:a], setup['crc']), b[a:a + n_crc]) or not b[:a].any():
        return None                      # all zero bits pass the CRC, and are no block
    return bytes(np.packbits(b[:a]))


def encode_dlsch(data, setup, rv, qm, g):
    """The ``g`` bits sent of transport block ``data`` (bytes) at redundancy version ``rv``, for tests."""
    from . import iqnr_ldpc as L
    a = setup['tbs']
    tb = np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))[:a]
    b = np.concatenate((tb, L.crc(tb, setup['crc'])))
    per = setup['k_prime'] - setup['L']
    out = []
    for r, e_len in enumerate(_block_lengths(g, qm, setup['C'])):
        blk = b[r * per:(r + 1) * per]
        if setup['C'] > 1:
            blk = np.concatenate((blk, L.crc(blk, '24B')))
        c = np.zeros(setup['K'], dtype=np.uint8)
        c[:len(blk)] = blk
        d = L.encode(c, setup['bg'], setup['Zc'], setup['i_ls'])
        out.append(L.rate_match(d, e_len, rv, qm, setup['bg'], setup['Zc'], setup['k_prime'], setup['K']))
    return np.concatenate(out)


def _qam_map(bits, qm):
    """38.211 5.1.3-5.1.5: Gray-mapped QPSK, 16QAM or 64QAM symbols of ``bits``."""
    b = np.asarray(bits, dtype=np.float64).reshape(-1, qm)
    s = 1 - 2 * b
    if qm == 2:
        return (s[:, 0] + 1j * s[:, 1]) / np.sqrt(2.0)
    if qm == 4:
        return (s[:, 0] * (2 - s[:, 2]) + 1j * s[:, 1] * (2 - s[:, 3])) / np.sqrt(10.0)
    return (s[:, 0] * (4 - s[:, 2] * (2 - s[:, 4])) + 1j * s[:, 1] * (4 - s[:, 3] * (2 - s[:, 5]))) / np.sqrt(42.0)


def pdsch_grid(data, setup, alloc, pci, slot, rv, n_rb, rnti=SI_RNTI, k_ref=0):
    """The 14 x 12 ``n_rb`` slot grid of one PDSCH carrying ``data`` (bytes), with its DM-RS,
    for tests: data REs at -3 dB to the DM-RS, in VRB order, k first, then l."""
    n_data = sum(12 * len(alloc['prbs']) if not (alloc.get('shared_dmrs') and l in alloc['dmrs'])
                 else 6 * len(alloc['prbs']) for l in alloc['data_symbols'])
    g_len = n_data * alloc['qm']
    bits = encode_dlsch(data, setup, rv, alloc['qm'], g_len)
    bits = bits ^ _gold((int(rnti) << 15) + int(pci), g_len)
    sym = _qam_map(bits, alloc['qm']) * _DATA_GAIN
    grid = np.zeros((14, 12 * n_rb), dtype=np.complex128)
    at = 0
    for l in alloc['data_symbols']:
        sc = (12 * np.array(alloc['prbs'])[:, None] + np.arange(12)[None, :]).ravel()
        if alloc.get('shared_dmrs') and l in alloc['dmrs']:
            sc = sc[sc % 2 == 1]
        grid[l, sc] = sym[at:at + len(sc)]
        at += len(sc)
    dm_k = np.array([12 * p + 2 * j for p in alloc['prbs'] for j in range(6)])
    for l in alloc['dmrs']:
        r = pdsch_dmrs(pci, slot, l, 12 * n_rb + int(k_ref))
        grid[l, dm_k] = r[(dm_k + int(k_ref)) // 2]
    return grid
