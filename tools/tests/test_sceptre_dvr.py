"""Playing a Sceptre DVR file (``.sdvr``) as an IQ recording.

Built from synthetic files that follow the layout ``sceptre_dvr.py`` reads
(``signals.write_sdvr``): runs of IQ with spectrum tiles between them, the
ring wrapped so the newest chunks come first, and an old run left over from
before. It must:

- find every run, in time order, skip the tiles and the leftover run, and
  report the rate, centre, band and the full-scale level in dBm (the scale a
  run's header carries);
- refuse a file that is not a DVR, holds no IQ (sweep tiles only), or is cut
  short - with a message that says what to do;
- play the samples exactly (int16 to float32, full scale 1.0), from a seek
  and around the end when it repeats, through ``IQFile``;
- and, played through the real receive chain, decode the station's PI and
  name, so what a DVR holds is a working recording.

Run:  python tools/tests/test_sceptre_dvr.py [--keep]     (about 25 s)
"""

import os
import shutil
import struct
import sys
import tempfile
import time

import numpy as np
from gnuradio import blocks, gr  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import sceptre_dvr  # noqa: E402
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import IQFile, RadioError, read_iq_metadata  # noqa: E402
from tests import signals  # noqa: E402

RATE = 2.5e6
CENTRE = 98.4e6
RUN = 1 << 15


def play(radio, count):
    """The first ``count`` samples the radio's block gives, as fast as it can."""
    tb = gr.top_block()
    head = blocks.head(gr.sizeof_gr_complex, count)
    sink = blocks.vector_sink_c()
    tb.connect(radio.block, head, sink)
    tb.run()
    got = np.array(sink.data(), dtype=np.complex64)
    tb.disconnect_all()
    return got


def layout_checks(folder):
    rng = np.random.default_rng(3)
    iq = (0.4 * (rng.standard_normal(RUN * 9) + 1j * rng.standard_normal(RUN * 9))
          ).astype(np.complex64)
    path = os.path.join(folder, 'dvr.sdvr')
    want = signals.write_sdvr(path, iq, RATE, CENTRE, run=RUN)
    lay = sceptre_dvr.scan(path)
    print(f"layout: {len(lay['runs'])} runs, {lay['samples']} samples, "
          f"{lay['rate']:.0f} S/s, centre {lay['center_hz'] / 1e6:.1f} MHz, "
          f"{lay['tiles']} tiles, {lay['gaps']} gaps")
    assert len(lay['runs']) == 9 and lay['samples'] == RUN * 9, lay
    assert abs(lay['rate'] - RATE) < 1e-3 and lay['center_hz'] == CENTRE, lay
    assert lay['band_hz'] == (CENTRE - RATE / 2, CENTRE + RATE / 2), lay['band_hz']
    assert lay['gaps'] == 0, lay
    assert abs(lay['full_scale_dbm'] - (-20.0 + 10)) < 1e-3, lay['full_scale_dbm']
    assert lay['tiles'] >= 9, lay['tiles']            # they were there to skip
    assert abs((lay['end'] - lay['start']) - lay['samples'] / RATE) < 1e-6, lay

    # The radio: in time order, exactly, from a seek and around the end.
    radio = IQFile(path, repeat=True, throttle=False)
    radio.open()
    assert radio.rate == RATE and radio.center_hz == CENTRE, (radio.rate, radio.center_hz)
    assert radio.total == RUN * 9, radio.total
    got = play(radio, RUN * 9 + 1000)
    assert np.array_equal(got[:RUN * 9], want), 'samples differ from what was written'
    assert np.array_equal(got[RUN * 9:], want[:1000]), 'the repeat did not start over'
    radio.block.file.seek(RUN * 4 + 17, 0)
    got = play(radio, 5000)
    assert np.array_equal(got, want[RUN * 4 + 17:RUN * 4 + 17 + 5000]), 'seek is off'
    once = IQFile(path, repeat=False, throttle=False)
    once.open()
    assert len(play(once, RUN * 20)) == RUN * 9, 'a single play should end'
    print("IQFile: samples exact, in time order, seek and repeat right")

    # A gap - one run missing from the ring - is counted, not hidden.
    holed = os.path.join(folder, 'holed.sdvr')
    signals.write_sdvr(holed, iq, RATE, CENTRE, run=RUN, rotate=False, tiles=False)
    with open(holed, 'r+b') as fh:                 # break run 4's header tag
        fh.seek((4 + 4 * (1 + RUN * 4 // 4096)) * 4096 + 0x10)
        fh.write(b'XX')
    lay = sceptre_dvr.scan(holed)
    assert len(lay['runs']) == 8 and lay['gaps'] == 1, lay
    print(f"a lost run: {len(lay['runs'])} runs, {lay['gaps']} gap counted")


def refusal_checks(folder):
    def refused(path, *words):
        try:
            read_iq_metadata(path)
        except RadioError as exc:
            for w in words:
                assert w in str(exc), (w, str(exc))
            return
        raise AssertionError(f"{os.path.basename(path)} should have been refused")

    junk = os.path.join(folder, 'junk.sdvr')
    with open(junk, 'wb') as fh:
        fh.write(bytes(40960))
    refused(junk, 'not a Sceptre DVR')

    tiny = os.path.join(folder, 'tiny.sdvr')
    with open(tiny, 'wb') as fh:
        fh.write(b'SDVR' + bytes(100))
    refused(tiny, 'too short')

    sweeps = os.path.join(folder, 'sweeps.sdvr')
    with open(sweeps, 'wb') as fh:
        fh.write(b'SDVR' + bytes(4092) + bytes(4096 * 3))
        for k in range(3):
            fh.write(signals._sdvr_header(b'SB', 1.79e9 + k, 0.2325, 16, bins=64)
                     + signals._pad(bytes(16 * 64)))
    refused(sweeps, 'holds no IQ', 'IQ tab')

    cut = os.path.join(folder, 'cut.sdvr')
    iq = np.full(RUN * 2, 0.25 + 0.1j, dtype=np.complex64)
    signals.write_sdvr(cut, iq, RATE, CENTRE, run=RUN, rotate=False, tiles=False,
                       stale=False)
    with open(cut, 'r+b') as fh:                   # lose the end of the last run
        fh.truncate(os.path.getsize(cut) - 8192)
    lay = sceptre_dvr.scan(cut)
    assert len(lay['runs']) == 1, lay              # the whole run before it only
    print("refused: not a DVR, too short, sweeps only; a cut-short run is left out")


def end_to_end(folder):
    """A synthetic station in a DVR, through the engine, RDS and all."""
    seconds = 9.0
    left, right = signals.tones(seconds)
    mpx = signals.multiplex(seconds, left, right)
    iq = signals.fm_iq(mpx, RATE, 300e3) * 0.9
    path = os.path.join(folder, 'station.sdvr')
    signals.write_sdvr(path, iq, RATE, CENTRE, run=1 << 18)
    radio = IQFile(path, repeat=False, throttle=True)
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    tb.start_receive(98.7e6, radio.rate, region='RBDS', stereo=True, volume=0.5)
    assert abs(tb.offset_hz - 300e3) < 1, tb.offset_hz
    t0 = time.time()
    tb.wait()
    snap = tb.rx.rds.snapshot()
    print(f"end to end: PI {snap['pi_hex']}  PS {snap['ps']!r}  "
          f"blocks {snap['blocks_ok']}/{snap['blocks_seen']}  ({time.time() - t0:.0f} s)")
    tb.close()
    assert snap['pi_hex'] == '0x1234', snap['pi_hex']
    assert snap['station_name'].strip() == 'TEST FM', snap['station_name']


def main():
    keep = '--keep' in sys.argv
    folder = tempfile.mkdtemp(prefix='fmrx-sdvr-')
    try:
        layout_checks(folder)
        refusal_checks(folder)
        end_to_end(folder)
    finally:
        if keep:
            print('kept', folder)
        else:
            shutil.rmtree(folder, ignore_errors=True)
    print('OK')


if __name__ == '__main__':
    main()
