"""Sceptre's IQ DVR (``.sdvr``) and BLUE IQ (``.cdif``) in the Recordings tab - no radio.

Files built to the layout the code reads (``signals.write_sdvr``,
``signals.write_blue``). It must:

- list them from the folders added, as one recording each: its centre as the
  station, its length from the samples, the time it began, the file's name for
  a name, the kind - and pass over a stray file and a folder that is not there
  (sweep DVRs and spectrum files are test_sceptre_spectra.py's);
- read only their headers, and the same header once while the file is as it
  was;
- draw them (``library.render``) exactly as the same samples in this app's own
  ``.cfile`` - the tone where it should be - a BLUE file's in dBm (its
  ``DATA_GAIN`` taken off);
- never write beside them or delete them;
- in the window: show them in the list beside this app's own recordings, keep
  the folders in the settings (``fmctl folders``), and play both - the radio
  that plays them is the IQ file one, its position moving.

Run:  python tools/tests/test_sceptre_library.py [--keep]     (about 30 s)
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt, QtCore  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-sceptre-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import control, library  # noqa: E402
from fm_receiver.widgets import HEADER_ROLE  # noqa: E402
from fm_receiver.radios import read_iq_metadata  # noqa: E402
from tests import signals  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
WINDOWS = []
RATE = 2.5e6
CENTRE = 98.4e6
TONE_HZ = 300e3
SECONDS = 1.0
GAIN_DB = 20.0              # a BLUE file's DATA_GAIN: dBm = 10 log10 |x|^2 - GAIN_DB


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def tone(seconds, rate, hz, level=0.3):
    t = np.arange(int(seconds * rate)) / rate
    rng = np.random.default_rng(3)
    noise = 0.01 * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
    return (level * np.exp(2j * np.pi * hz * t) + noise).astype(np.complex64)


def build(folder):
    signals.ensure_dir(folder)
    iq = tone(SECONDS, RATE, TONE_HZ)
    sdvr = os.path.join(folder, 'dvr.sdvr')
    got = signals.write_sdvr(sdvr, iq, RATE, CENTRE, run=1 << 15, age=60.0)
    blue_iq = tone(SECONDS, 1e6, -100e3)
    cdif = signals.write_blue(os.path.join(folder, '2026-09-30_10-44-21_99269khz.cdif'),
                              blue_iq, 1e6, keywords={'RF_FREQ': 99.269e6, 'SAMPLE_RATE': 1e6,
                                                      'DATA_GAIN': GAIN_DB,
                                                      'TIME_EPOCH': '2026-09-30T10:44:21Z'})
    with open(os.path.join(folder, 'notes.txt'), 'w') as fh:
        fh.write('not a recording')
    return sdvr, cdif, got, blue_iq


def pieces(folder):
    sdvr, cdif, got, blue_iq = build(folder)
    gone = os.path.join(FOLDER, 'not-there')
    before = sorted(os.listdir(folder))
    recs = library.scan_external([folder, gone, folder])
    assert len(recs) == 2, [r.key for r in recs]          # notes passed over
    by_kind = {r.tracks[0].kind: r for r in recs}
    d, b = by_kind['dvr-iq'], by_kind['cdif']
    assert d.external and b.external
    assert d.station_hz == CENTRE and abs(b.station_hz - 99.269e6) < 1
    assert abs(d.seconds - len(got) // (1 << 15) * (1 << 15) / RATE) < 1e-6, d.seconds
    assert abs(b.seconds - SECONDS) < 1e-6, b.seconds
    assert d.name == 'dvr.sdvr' and d.kinds_text() == 'Sceptre IQ DVR'
    assert b.kinds_text() == 'Sceptre BLUE IQ'
    assert d.tracks[0].error is None and b.tracks[0].error is None
    assert d.tracks[0].rate == RATE and b.tracks[0].rate == 1e6
    assert abs(d.started.timestamp() - (1.7907e9 + 0.25)) < 1.0, d.started
    assert abs(b.started.timestamp() - 1790765061.8554673 + 0.0) < 3600 * 24, b.started
    assert d.files() == [sdvr] and d.tracks[0].sidecars() == []
    # the same header, once: a second look is the first's answer
    again = library.scan_external([folder])
    assert library._external_meta(sdvr, 'dvr-iq') is library._external_meta(sdvr, 'dvr-iq')
    assert len(again) == 2

    # a garbage time in a header does not empty the list; an upper-case
    # extension is listed and plays; a sweep DVR is not scanned twice
    odd = signals.ensure_dir(os.path.join(FOLDER, 'odd'))
    bad = os.path.join(odd, 'bad-time.cdif')
    shutil.copy(cdif, bad)
    with open(bad, 'r+b') as fh:
        fh.seek(256)
        fh.write(struct.pack('<d', 1e300))
    shout = os.path.join(odd, 'LOUD.SDVR')
    shutil.copy(sdvr, shout)
    got_odd = library.scan_external([odd])
    assert sorted(r.name for r in got_odd) == ['LOUD.SDVR', 'bad-time.cdif'], \
        [r.name for r in got_odd]
    assert all(r.tracks[0].error is None for r in got_odd)
    assert read_iq_metadata(shout)['layout']['samples'] > 0

    # drawn exactly as this app's own file of the same samples
    own = os.path.join(FOLDER, 'own')
    got.tofile(own + '.cfile')
    with open(own + '.json', 'w') as fh:
        json.dump({'rate': RATE, 'center_hz': CENTRE, 'station_hz': CENTRE,
                   'offset_hz': 0.0}, fh)
    ref = library.Track(own + '.cfile', 'iq-band', 1)
    assert ref.error is None
    seconds = d.tracks[0].seconds
    a, fa = library.render(d.tracks[0], 0, seconds, 24, 256, 2, 'mean')
    r, fr = library.render(ref, 0, seconds, 24, 256, 2, 'mean')
    assert np.allclose(a, r, atol=1e-4), np.nanmax(np.abs(a - r))
    assert np.allclose(fa, fr)
    peak = fa[int(np.nanargmax(np.nanmean(a, axis=0)))]
    assert abs(peak - (CENTRE + TONE_HZ)) < RATE / 256 * 1.5, peak
    # the BLUE file's tone, 100 kHz below its centre
    c, fc = library.render(b.tracks[0], 0, b.seconds, 16, 256, 2, 'mean')
    peak = fc[int(np.nanargmax(np.nanmean(c, axis=0)))]
    assert abs(peak - (b.station_hz - 100e3)) < 1e6 / 256 * 1.5, peak
    assert np.isfinite(c).all()
    # in dBm: the tone of magnitude 0.3 is 0.03 with the file's gain taken off
    n, _ = library.render(b.tracks[0], 0, b.seconds, 4, None, 2, 'max')
    level = float(np.nanmax(n))
    assert abs(level - (20 * np.log10(0.3) - GAIN_DB)) < 1.5, level

    # nothing is deleted, nothing written beside them
    failed = library.delete(d)
    assert failed and os.path.exists(sdvr), failed
    assert sorted(os.listdir(folder)) == before
    print("part 1 passed")


def window(folder, own):
    station = signals.write_station(os.path.join(FOLDER, 'station'), seconds=2.0)
    args = fmapp.parse_args(['--file', station, '--no-sound-card'])
    w = fmapp.MainWindow(args, {'recording_dir': own, 'library_folders': [folder]})
    WINDOWS.append(w)
    w.show()
    pump(0.2)
    w.start_initial()
    return w


def play_row(w, kind):
    row = next(i for i, r in enumerate(w._recordings) if r.tracks[0].kind == kind)
    w.rec_list.clearSelection()
    w.rec_list.setCurrentItem(w._rec_items[row])
    w._rec_items[row].setSelected(True)
    assert pump(5, lambda: w._track is not None and w._track.kind == kind)
    assert pump(8, lambda: w.play_btn.isEnabled())
    w.play_btn.setChecked(True)
    assert pump(15, lambda: w._play is not None and w.engine.running
                and w.engine.radio is not None and w.engine.radio.kind == 'file'), \
        'no playback'
    radio = w.engine.radio
    layout = radio.meta['layout']
    assert pump(15, lambda: w._play is not None and w._play.position_s() > 0.05) \
        if hasattr(w._play, 'position_s') else pump(10, lambda: radio.position() > 0)
    w.play_btn.setChecked(False)
    pump(1.0)
    return layout


def gui(folder):
    own = signals.ensure_dir(os.path.join(FOLDER, 'own-recordings'))
    with wave.open(os.path.join(own, 'fm-98.70MHz-20260920-100000-audio.wav'), 'wb') as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(48000)
        fh.writeframes(bytes(48000 * 4))
    w = window(folder, own)
    w.tabs.setCurrentIndex(2)
    lst = w.rec_list
    assert pump(10, lambda: len(w._recordings) == 3), len(w._recordings)
    kinds = [r.tracks[0].kind for r in w._recordings]
    assert kinds == ['audio', 'dvr-iq', 'cdif'], kinds       # this app's, then each Sceptre kind
    # grouped under titles that are not rows: three of them, and not chosen
    heads = [i for i in range(lst.count()) if lst.item(i).data(HEADER_ROLE)]
    assert len(heads) == 3 and lst.count() == 6, (heads, lst.count())
    assert [lst.item(i).text() for i in heads] == [
        'FM recordings', 'Sceptre IQ DVR', 'Sceptre BLUE IQ']
    assert [lst.item(i).data(HEADER_ROLE) for i in heads] == [1, 1, 1]      # the counts
    assert all(not lst.item(i).flags() & QtCore.Qt.ItemIsSelectable for i in heads)
    assert [it.data(QtCore.Qt.UserRole) for it in w._rec_items] == \
        [r.key for r in w._recordings]
    assert w.lib_note.text() == '' and not w.lib_note.isVisible(), w.lib_note.text()
    text = ' '.join(it.text() for it in w._rec_items)
    assert 'Sceptre IQ DVR' in text and 'Sceptre BLUE IQ' in text, text
    assert 'dvr.sdvr' not in text and '.cdif' not in text, text    # file names are in the tooltips
    assert 'dvr.sdvr' in w._rec_items[1].toolTip()
    # one file to a recording: nothing to choose, so no Play: list
    assert pump(5, lambda: w.track_combo.isHidden()), 'the Play: list is shown for one file'

    # the folders: through fmctl, kept in the settings
    reply = control.cmd_folders(w, [])
    assert reply['folders'] == [folder], reply
    other = signals.ensure_dir(os.path.join(FOLDER, 'other'))
    try:
        control.cmd_folders(w, ['add', os.path.join(FOLDER, 'nowhere')])
        raise AssertionError('a missing folder was added')
    except control.CommandError:
        pass
    reply = control.cmd_folders(w, ['add', other])
    assert reply['folders'] == [folder, other], reply
    assert json.load(open(os.environ['FMRX_CONFIG']))['library_folders'] == [folder, other]
    reply = control.cmd_folders(w, ['remove', other])
    assert reply['folders'] == [folder]
    assert pump(5, lambda: len(w._recordings) == 3)

    # playing: the IQ file radio, from the DVR's runs and the BLUE file's samples
    layout = play_row(w, 'dvr-iq')
    assert layout.get('runs') and layout['samples'] > 0, layout
    layout = play_row(w, 'cdif')
    assert layout.get('blue') and layout['samples'] == int(SECONDS * 1e6), layout

    # a Sceptre file is not deleted from the list, and nothing is written beside it
    keys = [r.key for r in w._recordings if r.external]
    w._delete_keys(keys)
    assert all(os.path.exists(k) for k in keys)
    assert 'not deleted' in w.lib_note.text(), w.lib_note.text()
    assert sorted(os.listdir(folder)) == sorted(['dvr.sdvr', 'notes.txt',
                                                 os.path.basename(keys[0]) if keys[0].endswith('.cdif')
                                                 else os.path.basename(keys[1])]), os.listdir(folder)
    print("part 2 passed")


def main():
    keep = '--keep' in sys.argv
    try:
        folder = os.path.join(FOLDER, 'sceptre')
        pieces(folder)
        gui(folder)
    finally:
        for w in WINDOWS:
            w.close()
        if keep:
            print("files kept in", FOLDER)
        else:
            shutil.rmtree(FOLDER, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
