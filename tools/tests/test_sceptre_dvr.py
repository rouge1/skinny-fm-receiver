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
- read the spectrum tiles (``Sweeps``): a sweep DVR's, in time order, by band,
  with what an earlier recording left out, and refuse an IQ-only file;
- and cope with a file Sceptre is still writing: trust its chunks, not the
  window it left on page 1, leave out the newest chunk, and say when a chunk
  was overwritten after it was scanned;
- and, played through the real receive chain, decode the station's PI and
  name, so what a DVR holds is a working recording.

Run:  python tools/tests/test_sceptre_dvr.py [--keep]     (about 25 s)
"""

import os
import shutil
import struct
import subprocess
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
    lay = sceptre_dvr.scan(holed, live=False)      # edited just now, but paused
    assert len(lay['runs']) == 8 and lay['gaps'] == 1, lay
    print(f"a lost run: {len(lay['runs'])} runs, {lay['gaps']} gap counted")


def sweep_checks(folder):
    bins, sweeps, tiles = 4096, 16, 8
    path = os.path.join(folder, 'sweep.sdvr')
    signals.write_sweep_sdvr(path, tiles=tiles, bins=bins)
    lay = sceptre_dvr.scan_sweeps(path)
    print(f"sweeps: {len(lay['tiles'])} tiles of {lay['sweeps']} x {lay['bins']} bins, "
          f"{lay['gaps']} gaps, {lay['end'] - lay['start']:.1f} s")
    # The stale tile of the same kind and the IQ-mode one are left out; so is
    # a window left from IQ.
    assert len(lay['tiles']) == tiles and lay['gaps'] == 0 and not lay['live'], lay
    assert lay['bins'] == bins and lay['sweeps'] == sweeps, lay
    assert lay['bin_hz'] == 4882.8125 and lay['start_hz'] == 4882.8125, lay
    assert [w for _, w in lay['tiles']] == sorted(w for _, w in lay['tiles'])
    sw = sceptre_dvr.Sweeps(path)
    times, freqs, db = sw.read()
    assert db.shape == (tiles * sweeps, bins) and db.dtype == np.int8, db.shape
    want = np.concatenate([signals.sweep_tile_bytes(k, bins) for k in range(tiles)])
    assert np.array_equal(db, want), 'bytes differ (bin-major, time order?)'
    assert np.allclose(np.diff(times), 0.2325), np.diff(times)[:3]
    assert freqs[0] == 4882.8125 and abs(freqs[1] - freqs[0] - 4882.8125) < 1e-6
    # A band: bins whose frequencies lie in it, and only them.
    lo, hi = 3.0e6, 3.1e6
    t2, f2, d2 = sw.read(lo, hi, first=2, stop=4)
    assert f2[0] <= lo and f2[-1] >= hi and f2[1] > lo and f2[-2] < hi, (f2[0], f2[-1])
    i0 = int(np.searchsorted(freqs, f2[0]))
    assert np.array_equal(d2, want[2 * sweeps:4 * sweeps, i0:i0 + len(f2)])
    assert len(t2) == 2 * sweeps
    try:
        sw.bin_range(9e9, 9.1e9)
        raise AssertionError('a band outside the DVR should be refused')
    except sceptre_dvr.SdvrError as exc:
        assert 'outside' in str(exc)
    print("Sweeps: bytes, times and a band are right; the leftovers are left out")

    # The tool: the report, and a picture of a band.
    tool = os.path.join(os.path.dirname(HERE), 'dvr-sweep')
    png = os.path.join(folder, 'sweep.png')
    out = subprocess.run([sys.executable, tool, path, '--band', '0.5', '10',
                          '--png', png], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert '8 tiles of 16 sweeps' in out.stdout and 'wrote' in out.stdout, out.stdout
    assert os.path.getsize(png) > 5000, 'no picture'
    with open(os.path.join(folder, 'junk.sdvr'), 'wb') as fh:
        fh.write(bytes(40960))
    bad = subprocess.run([sys.executable, tool, os.path.join(folder, 'junk.sdvr')],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and 'dvr-sweep:' in bad.stderr, bad

    # An IQ DVR's tiles read the same way, over its band.
    iq = np.zeros(RUN * 4, dtype=np.complex64)
    both = os.path.join(folder, 'iqtiles.sdvr')
    signals.write_sdvr(both, iq, RATE, CENTRE, run=RUN)
    assert sceptre_dvr.scan_sweeps(both)['bins'] == 64
    # ... and an IQ DVR with no tiles is refused, saying what it holds.
    plain = os.path.join(folder, 'notiles.sdvr')
    signals.write_sdvr(plain, iq, RATE, CENTRE, run=RUN, tiles=False)
    try:
        sceptre_dvr.scan_sweeps(plain)
        raise AssertionError('no tiles should be refused')
    except sceptre_dvr.SdvrError as exc:
        assert 'no spectrum tiles' in str(exc), exc


def live_checks(folder):
    """A file Sceptre is writing: new to the clock, its window from before."""
    rng = np.random.default_rng(5)
    iq = (0.3 * (rng.standard_normal(RUN * 9) + 1j * rng.standard_normal(RUN * 9))
          ).astype(np.complex64)
    start = 1.7907e9 + 0.25
    path = os.path.join(folder, 'live.sdvr')
    want = signals.write_sdvr(path, iq, RATE, CENTRE, run=RUN, start=start,
                              window=(start - 500, start - 490), age=None)
    lay = sceptre_dvr.scan(path)
    assert lay['live'], 'a file just written should count as live'
    # The window (from before) would drop every run; the chunks are trusted,
    # the leftover run is dropped, and the newest run (perhaps half written)
    # is left out.
    assert len(lay['runs']) == 8 and lay['samples'] == RUN * 8, lay
    assert lay['gaps'] == 0 and lay['start'] == start, lay
    got = sceptre_dvr.Reader(path, lay).read(0, RUN * 8)
    assert np.array_equal(got, want[:RUN * 8]), 'a live file read wrong'
    print(f"live: window from before ignored, newest run left out, "
          f"{len(lay['runs'])} runs exact")
    # The same file, paused (old): nothing is dropped, and its window, which
    # holds none of the runs, is not believed either.
    old = time.time() - 60
    os.utime(path, (old, old))
    paused = sceptre_dvr.scan(path)
    assert not paused['live'] and paused['samples'] == RUN * 9, paused
    assert sceptre_dvr.scan(path, live=True)['samples'] == RUN * 8

    # Sceptre overwrites a run after the scan: counted, and only then.
    reader = sceptre_dvr.Reader(path, lay)
    reader.read(0, RUN * 8)
    assert reader.overwritten == 0, reader.overwritten
    with open(path, 'r+b') as fh:
        fh.seek(lay['runs'][2][0] - 4096 + 0x14)
        fh.write(struct.pack('<dd', 1.7907e9 + 99, 0.5))
    reader.read(RUN * 2, RUN * 3)
    assert reader.overwritten == 1, reader.overwritten
    reader.read(RUN * 5, RUN * 6)
    assert reader.overwritten == 1, 'an untouched run was counted'
    print("live: a run overwritten after the scan is counted")

    # A sweep DVR being written: the newest tile is left out; an overwritten
    # one is left out of a read, and counted.
    sweep = os.path.join(folder, 'livesweep.sdvr')
    signals.write_sweep_sdvr(sweep, tiles=6, bins=512, age=None)
    slay = sceptre_dvr.scan_sweeps(sweep)
    assert slay['live'] and len(slay['tiles']) == 5, slay
    sw = sceptre_dvr.Sweeps(sweep, slay)
    assert len(sw.read()[0]) == 5 * 16 and sw.overwritten == 0
    with open(sweep, 'r+b') as fh:
        fh.seek(slay['tiles'][1][0] - 4096 + 0x14)
        fh.write(struct.pack('<dd', 1.7907e9 + 999, 0.0))
    times, _, db = sw.read()
    assert len(times) == 4 * 16 and sw.overwritten == 1, (len(times), sw.overwritten)
    print("live: newest tile left out; an overwritten tile is dropped and counted")


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
    lay = sceptre_dvr.scan(cut, live=False)
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
        sweep_checks(folder)
        live_checks(folder)
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
