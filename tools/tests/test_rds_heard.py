"""RadioText that takes turns: a station alternating a typed message with the
song, under one A/B flag, shows one at a time; ``radiotext_heard`` must keep
both. Protocol level, no radio, no DSP."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_receiver.rds_core import RdsProtocol            # noqa: E402
from tests.signals import rds_block, rds_groups        # noqa: E402


def raw_bits(groups):
    bits = []
    for pi, b, c, d in groups:
        for info, name in ((pi, 'A'), (b, 'B'), (c, 'C'), (d, 'D')):
            block = rds_block(info, name)
            bits.extend((block >> i) & 1 for i in range(25, -1, -1))
    return np.array(bits, dtype=np.uint8)


def main():
    typed = 'SYNTHETIC NIGHT MIX - TYPED MESSAGE'
    song = 'An Artist - A Title'
    proto = RdsProtocol()
    for _ in range(4):
        for text in (typed, song):
            for _ in range(3):
                proto.feed(raw_bits(rds_groups(0x1234, 'TEST FM', text)))
    snap = proto.snapshot()
    heard = snap['radiotext_heard']
    print('radiotext', repr(snap['radiotext']), 'heard', heard)
    assert typed in heard and song in heard, heard
    assert len(heard) == 2, heard              # no splices of the two
    print('ok')


if __name__ == '__main__':
    main()
