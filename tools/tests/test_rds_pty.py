"""PTY from a repaired block B: one wrongly corrected group must not overwrite
the programme type every clean group agrees on. Protocol level, no radio, no
DSP."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_receiver.rds_core import RdsProtocol            # noqa: E402


def group_b(gtype, ver_b, pty, rest=0):
    return (gtype << 12) | (ver_b << 11) | (pty << 5) | rest


def main():
    proto = RdsProtocol()
    pi = 0x1234
    for k in range(20):
        proto._decode_group({'A': pi, 'B': group_b(0, 0, 5, k & 3), 'C': 0, 'D': 0x2020})
    assert proto.pty == 5, proto.pty

    # A false 0B with B and C repaired, carrying PTY 4: kept out.
    proto._decode_group({'B': group_b(0, 1, 4, 2), 'C': 0x0DEB}, corrected=frozenset('BC'))
    assert proto.pty == 5, proto.pty
    proto._decode_group({'A': pi, 'B': group_b(2, 0, 5, 1), 'C': 0x4142, 'D': 0x4344})
    assert proto.pty == 5, proto.pty

    # The same wrong repair over and over (as off the bench) still counts for
    # nothing; an intact group with a new PTY is taken at once.
    for _ in range(30):
        proto._decode_group({'B': group_b(0, 1, 4, 2), 'C': 0x0DEB}, corrected=frozenset('BC'))
    assert proto.pty == 5, proto.pty
    proto._decode_group({'A': pi, 'B': group_b(0, 0, 9, 0), 'D': 0x2020})
    assert proto.pty == 9, proto.pty
    print('ok')


if __name__ == '__main__':
    main()
