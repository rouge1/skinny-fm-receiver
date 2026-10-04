"""The Recordings tab: what is listed, and playing it back - no radio.

Part 1 checks the pieces on their own: recordings grouped from their file
names (and an older recording whose kinds were named a second apart), the
WAV reader (a header cut short too), ``library.render`` (any stretch of a
track as a waterfall: its columns, its slots of time, NaN outside the file,
a WAV on a linear scale), the WAV source's seek, loop and end, and the
description Record writes - a station name kept only once it has held still.

Part 1b drives the mini map alone: the box on it, pressing outside it and
inside it, dragging in time and frequency, the wheel, and what it never
does without a recording.

Part 2 records the synthetic station from an IQ file in the Receive tab -
WAV, channel and band, with a retune - then opens the Recordings tab and
plays it back: the radio closes, the recording is listed with its RDS
name, the mini map beside the waterfall coloured by the RF spectrum's Ref
level and Range and in its dBFS, the band plays with RDS, the waterfall
drawn from the file (before play, after a seek, carried on by the radio's
rows, zoomed), seeks by dragging the map's box (which pans the spectrum
too), pauses and resumes with RDS kept, stops at its end or loops, the WAV
plays with its spectrum and the RadioText logged at record time, a
recording is deleted, and going back to Receive closes the map and opens the
radio where it was.

Run:  python tools/tests/test_recordings.py     (about a minute)
"""

import json
import os
import shutil
import sys
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np  # noqa: E402
from PyQt5 import Qt, QtCore, QtTest  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix='fmrx-rec-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')

from fm_receiver import app as fmapp  # noqa: E402
from fm_receiver import library, recording  # noqa: E402
from fm_receiver.dsp import wav_source  # noqa: E402
from tests import signals  # noqa: E402

QAPP = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def mouse_move(widget, pos):
    """A move with the left button down. (QTest.mouseMove sets the cursor,
    which the offscreen platform does not turn into an event.)"""
    Qt.QApplication.sendEvent(widget, Qt.QMouseEvent(
        QtCore.QEvent.MouseMove, QtCore.QPointF(pos), QtCore.Qt.NoButton,
        QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))


def write_wav(path, seconds, rate=48000, left_hz=1000.0, right_hz=2500.0):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.stack([0.5 * np.sin(2 * np.pi * left_hz * t),
                     0.5 * np.sin(2 * np.pi * right_hz * t)], axis=1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return path


# ============================================================ part 1

def part1_pieces():
    folder = signals.ensure_dir(os.path.join(FOLDER, 'library'))

    # One press of Record: a WAV, a channel in two parts, the band, and
    # the description - all named from one base.
    base = recording.session_base(folder, 98.7e6, stamp='20260922-101500')
    write_wav(base + '-audio.wav', 3.0)
    signals.write_station(base + '-iq-channel', seconds=2.0, rate=500e3,
                          center_hz=98.7e6, station_hz=98.7e6)
    signals.write_station(base + '-iq-channel-part2', seconds=1.0, rate=500e3,
                          center_hz=98.9e6, station_hz=98.9e6)
    signals.write_station(base + '-iq-band', seconds=3.0, rate=2.5e6,
                          center_hz=98.4e6, station_hz=98.7e6)
    # Its clock starts now, not before the files above: writing them took
    # 1.2 s on a busy machine, and the log's first entry landed past 1 s.
    info = recording.RecordingInfo(base, 98.7e6, 'Test radio', ['audio', 'iq-channel', 'iq-band'])
    # RadioText lands in the log once it has held for STEADY_S; a name only
    # once it has held for NAME_STEADY_S.
    snap = {'station_name': 'TEST FM ', 'pi_hex': '0x1234', 'callsign': 'KXYZ',
            'callsign_confirmed': None, 'pty': 'Rock', 'radiotext': 'First text',
            'title': None, 'artist': None, 'station_short': None}
    info.note_rds(snap)
    assert not info.doc['station_name'] and not info.doc['timeline'], info.doc
    info._t0 -= 3.0                               # three seconds on
    info.note_rds(snap)
    [entry] = info.doc['timeline']
    assert entry['radiotext'] == 'First text' and entry['t'] < 1.5, entry
    assert info.doc['pi'] == '0x1234' and not info.doc['station_name']
    info._t0 -= 6.0                               # nine seconds on
    info.note_rds(dict(snap, radiotext='Second text'))
    assert info.doc['station_name'] == 'TEST FM ', info.doc
    info._t0 -= 3.0
    info.note_rds(dict(snap, radiotext='Second text'))
    assert [e['radiotext'] for e in info.doc['timeline']] == ['First text', 'Second text']
    info.finish([base + '-audio.wav', base + '-iq-channel.cfile'])
    saved = json.load(open(base + recording.RecordingInfo.SUFFIX))
    assert saved['stopped'] and saved['seconds'] >= 12 and len(saved['files']) == 2, saved

    # A second recording in the same second takes the next name.
    again = recording.session_base(folder, 98.7e6, stamp='20260922-101500')
    assert again == base + '-2', again

    # An older recording: WAV and band IQ named a second apart, no description.
    write_wav(os.path.join(folder, 'fm-101.10MHz-20260921-120000-audio.wav'), 1.0)
    signals.write_station(os.path.join(folder, 'fm-101.10MHz-20260921-120001-iq-band'),
                          seconds=1.0, rate=2.5e6, center_hz=100.8e6, station_hz=101.1e6)
    # A WAV whose app was killed: its header still says no audio.
    cut = write_wav(os.path.join(folder, 'fm-88.10MHz-20260920-080000-audio.wav'), 2.0)
    with open(cut, 'r+b') as fh:
        fh.seek(40)
        fh.write(b'\0\0\0\0')
    assert abs(library.wav_layout(cut)[1] - 96000) < 2

    recs = library.scan(folder)
    assert [os.path.basename(r.key) for r in recs] == [
        'fm-98.70MHz-20260922-101500', 'fm-101.10MHz-20260921-120000',
        'fm-88.10MHz-20260920-080000'], [r.key for r in recs]
    new, old, killed = recs
    assert [(t.kind, t.part) for t in new.tracks] == [
        ('iq-band', 1), ('iq-channel', 1), ('iq-channel', 2), ('audio', 1)]
    assert new.name == 'TEST FM ' and new.best_track().kind == 'iq-band'
    assert abs(new.seconds - 3.0) < 0.01, new.seconds          # the longest kind
    assert new.kinds_text() == 'WAV + IQ channel (2 parts) + IQ band', new.kinds_text()
    assert new.rds_at(1.0) == ('First text', '') and new.rds_at(20.0)[0] == 'Second text'
    assert 'at 98.90 MHz' in new.tracks[2].label(), new.tracks[2].label()
    assert [t.kind for t in old.tracks] == ['iq-band', 'audio'], 'named a second apart: one'
    assert abs(killed.seconds - 2.0) < 0.01

    # render: the band's station stands out in its own columns.
    band = new.tracks[0]
    img, freqs = library.render(band, 0.0, 3.0, 24, cols=60)
    assert img.shape == (24, 60) and abs(freqs[0] - 97.17e6) < 50e3, (img.shape, freqs[0])
    assert library.extent(band) == (97.15e6, 99.65e6), library.extent(band)
    profile = np.nanmean(img, axis=0)
    assert abs(freqs[np.argmax(profile)] - 98.7e6) < 50e3, freqs[np.argmax(profile)]
    # Squeezed by the mean, the columns keep the noise where a view draws it:
    # never above the most of their bins, a few dB under it over noise, and
    # a flat spectrum stays what it is whatever the group sizes.
    mean, _ = library.render(band, 0.0, 3.0, 24, cols=60, pool='mean')
    assert mean.shape == img.shape and np.all(mean <= img + 1e-3)
    assert np.median(img - mean) > 2, np.median(img - mean)
    flat = library._pool(np.full((2, 100), 5.0), 7, 'mean')
    assert flat.shape == (2, 7) and np.allclose(flat, 5.0), flat
    # Natively its columns are the spectrum views' own bins, on their scale.
    native, nf = library.render(band, 0.5, 2.5, 10)
    assert native.shape == (10, library.RF_FFT) and np.array_equal(nf, library.bin_freqs(band))
    assert abs(nf[0] - 97.15e6) < 1 and abs(nf[-1] - (99.65e6 - 2.5e6 / library.RF_FFT)) < 1
    # A slot outside the file is NaN: rows before its start, and after its end.
    out, _ = library.render(band, -1.0, 1.0, 4, cols=16)
    assert np.isnan(out[:2]).all() and np.isfinite(out[2:]).all(), np.isnan(out).all(axis=1)
    out, _ = library.render(band, 2.0, 5.0, 6, cols=16)
    assert np.isfinite(out[:2]).all() and np.isnan(out[2:]).all(), np.isnan(out).all(axis=1)
    # Each row is its own slot of time: a tone that lasts a second (1 to 2 s
    # of 3) is in the rows of that second, and no others.
    rate = 500e3
    t = np.arange(int(3 * rate)) / rate
    burst = (0.3 * np.exp(2j * np.pi * 100e3 * t) * ((t >= 1) & (t < 2))).astype(np.complex64)
    burst.tofile(os.path.join(folder, 'burst.cfile'))
    with open(os.path.join(folder, 'burst.json'), 'w') as fh:
        json.dump({'rate': rate, 'offset_hz': 0, 'station_hz': 98.4e6, 'center_hz': 98.4e6}, fh)
    track = library.Track(os.path.join(folder, 'burst.cfile'), 'iq-channel', 1)
    img, freqs = library.render(track, 0.0, 3.0, 6, cols=64)
    level = img[:, int(np.argmin(np.abs(freqs - 98.5e6)))]
    assert level[2:4].min() > level[[0, 1, 4, 5]].max() + 30, level
    os.remove(os.path.join(folder, 'burst.cfile'))
    os.remove(os.path.join(folder, 'burst.json'))
    # A recording of nothing is a blank picture, not an error.
    open(os.path.join(folder, 'nothing.cfile'), 'wb').close()
    with open(os.path.join(folder, 'nothing.json'), 'w') as fh:
        json.dump({'rate': rate, 'offset_hz': 0, 'station_hz': 98.4e6, 'center_hz': 98.4e6}, fh)
    track = library.Track(os.path.join(folder, 'nothing.cfile'), 'iq-channel', 1)
    img, freqs = library.render(track, 0.0, 0.0, 4, cols=16)
    assert track.seconds == 0.0 and img.shape == (4, 16) and np.isnan(img).all()
    os.remove(os.path.join(folder, 'nothing.cfile'))
    os.remove(os.path.join(folder, 'nothing.json'))
    # A WAV is linear, 0 to half its rate, as the audio spectrum is: its
    # tones are in the columns of their own frequencies.
    img, freqs = library.render(new.tracks[3], 0.0, 3.0, 12)
    assert img.shape == (12, 1025) and freqs[0] == 0 and abs(freqs[-1] - 24000) < 1
    assert library.extent(new.tracks[3]) == (0.0, 24000.0)
    profile = np.nanmean(img, axis=0)
    for hz in (1000.0, 2500.0):
        near = np.abs(freqs - hz) < 60
        assert profile[near].max() > np.median(profile) + 40, (hz, profile[near].max())
        assert abs(freqs[near][np.argmax(profile[near])] - hz) < 25, hz

    # The WAV source: seek, the loop point, the end.
    frames = library.wav_frames(new.tracks[3].path)
    src = wav_source(frames, loop=False)
    left, right = np.zeros(1000, np.float32), np.zeros(1000, np.float32)
    src.seek(len(frames) - 400)
    assert src.work([], [left, right]) == 1000 and src.finished
    assert np.all(left[400:] == 0) and np.any(left[:400] != 0), 'silence after the end'
    assert src.work([], [left, right]) == 1000 and not np.any(left), 'finished: silence'
    src.loop = True
    src.seek(len(frames) - 400)
    assert src.work([], [left, right]) == 400 and src.position == 0 and not src.finished
    src.set_paused(True)
    assert src.work([], [left, right]) == 1000 and src.position == 0 and not np.any(left)

    # Deleting takes every file, the descriptions with them.
    before = set(os.listdir(folder))
    assert not library.delete(old)
    gone = before - set(os.listdir(folder))
    assert gone == {'fm-101.10MHz-20260921-120000-audio.wav',
                    'fm-101.10MHz-20260921-120001-iq-band.cfile',
                    'fm-101.10MHz-20260921-120001-iq-band.json'}, gone
    print("part 1 passed")


# ============================================================ part 1b

def part1b_minimap():
    """The mini map alone: 100 px by 200, 100 s, 97-99 MHz."""
    from fm_receiver.widgets import MiniMap
    mini = MiniMap()
    mini.resize(100, 200)
    mini.show()
    pump(0.1)
    sent = {'scrub': [], 'seek': [], 'pan': [], 'zoom': []}
    mini.scrubbed.connect(sent['scrub'].append)
    mini.seekRequested.connect(sent['seek'].append)
    mini.panRequested.connect(sent['pan'].append)
    mini.zoomRequested.connect(lambda n, fine: sent['zoom'].append((n, fine)))

    def point(seconds, hz):
        return QtCore.QPoint(int(mini.x_of(hz)), int(mini.y_of(seconds)))

    def press(pos):
        QtTest.QTest.mousePress(mini, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos)

    def move(pos):
        mouse_move(mini, pos)

    def release(pos):
        QtTest.QTest.mouseRelease(mini, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos)

    # Without a recording it is a frame, and does nothing.
    press(QtCore.QPoint(50, 100))
    release(QtCore.QPoint(50, 100))
    assert not any(sent.values()) and mini.window_rect() is None

    mini.set_duration(100.0)
    mini.set_extent((97e6, 99e6))
    mini.set_window(20.0, 97.5e6, 98.0e6)
    mini.set_position(50.0)
    # Later at the top: the playhead is the box's top edge, and the box runs
    # down the time it shows; its width is the band the spectrum shows.
    box = mini.window_rect()
    frame = mini._frame()
    assert abs(box.top() - mini.y_of(50.0)) < 1 and abs(box.bottom() - mini.y_of(30.0)) < 1, box
    assert abs(box.height() - frame.height() * 0.2) < 1, box
    assert abs(box.left() - mini.x_of(97.5e6)) < 1 and abs(box.right() - mini.x_of(98.0e6)) < 1
    assert mini.y_of(100.0) <= frame.top() + 1 and mini.y_of(0.0) >= frame.bottom() - 1, \
        'the end is at the top and the start at the bottom'
    assert abs(mini.time_at(mini.y_of(37.0)) - 37.0) < 1e-6
    assert abs(mini.hz_at(mini.x_of(98.4e6)) - 98.4e6) < 1

    # Pressing outside the box puts its middle on the pointer: the playhead
    # is the top, so half the span above where it was pressed.
    press(point(80.0, 98.6e6))
    assert abs(sent['scrub'][-1] - 90.0) < 0.6, sent['scrub']
    assert abs(sent['pan'][-1] - 98.6e6) < 25e3, sent['pan']
    assert not sent['seek'], 'nothing is played until the button comes up'
    move(point(40.0, 98.6e6))
    assert abs(sent['scrub'][-1] - 50.0) < 0.6, sent['scrub'][-1]
    release(point(40.0, 98.6e6))
    assert len(sent['seek']) == 1 and abs(sent['seek'][0] - 50.0) < 0.6, sent['seek']
    assert abs(mini.position - 50.0) < 0.6

    # Pressing inside carries it from where it was taken: nothing jumps.
    for key in sent:
        sent[key].clear()
    mini.set_position(50.0)
    mini.set_window(20.0, 97.5e6, 98.0e6)
    inside = point(45.0, 97.75e6)
    assert mini.window_rect().contains(QtCore.QPointF(inside))
    press(inside)
    assert abs(sent['scrub'][-1] - 50.0) < 0.6, 'pressed inside: it stays'
    assert abs(sent['pan'][-1] - 97.75e6) < 25e3, sent['pan'][-1]
    move(point(25.0, 98.25e6))                    # 20 s earlier, 0.5 MHz up
    assert abs(sent['scrub'][-1] - 30.0) < 0.6, sent['scrub'][-1]
    assert abs(sent['pan'][-1] - 98.25e6) < 25e3, sent['pan'][-1]
    release(point(25.0, 98.25e6))
    assert abs(sent['seek'][-1] - 30.0) < 0.6, sent['seek']

    # Dragged past either end it stops there.
    press(point(30.0, 97.75e6))
    move(QtCore.QPoint(50, -40))
    assert abs(sent['scrub'][-1] - 100.0) < 1e-6, sent['scrub'][-1]
    move(QtCore.QPoint(50, 400))
    assert sent['scrub'][-1] == 0.0, sent['scrub'][-1]
    release(QtCore.QPoint(50, 400))
    assert sent['seek'][-1] == 0.0

    # At the very start the box rests on the bottom edge, as small as it may
    # be, and the map is not dimmed all over.
    mini.set_position(0.0)
    box = mini.window_rect()
    assert box is not None and box.height() >= mini.WINDOW_MIN_PX - 1e-6 \
        and abs(box.bottom() - frame.bottom()) < 1, box
    mini.set_position(100.0)
    assert abs(mini.window_rect().top() - frame.top()) < 1.5

    # The wheel is the waterfall's time zoom; Shift makes it fine.
    for key in sent:
        sent[key].clear()
    for mods, fine in ((QtCore.Qt.NoModifier, False), (QtCore.Qt.ShiftModifier, True)):
        mini.wheelEvent(Qt.QWheelEvent(QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
                                       QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
                                       QtCore.Qt.NoButton, mods, QtCore.Qt.NoScrollPhase, False))
        assert sent['zoom'][-1] == (1.0, fine), sent['zoom']

    # A very long recording: the box is still there to be found.
    mini.set_duration(36000.0)
    mini.set_window(20.0, 97.5e6, 98.0e6)
    mini.set_position(18000.0)
    assert mini.window_rect().height() >= mini.WINDOW_MIN_PX - 1e-6
    # A picture in dB, later at the top, and NaN (before the file) left clear.
    img = np.full((8, 4), -90.0)
    img[7, 1] = -20.0                               # the latest row
    img[0] = np.nan
    mini.set_levels(-20, 60)
    mini.set_image(img, (97e6, 99e6))
    assert mini._index is not None and mini._index.shape == (8, 4)
    assert mini._index[0, 1] == 255 and mini._index[7].max() == 0, mini._index
    mini.set_image(None, (97e6, 99e6))
    assert mini._index is None

    # A recording shorter than the box (20 s of a 12 s file): a press outside
    # it puts the playhead at the pointer, not at the end.
    for key in sent:
        sent[key].clear()
    mini.set_duration(12.0)
    mini.set_window(20.0, 97.5e6, 98.0e6)
    mini.set_position(3.0)
    outside = point(8.0, 98.5e6)
    assert not mini.window_rect().contains(QtCore.QPointF(outside))
    press(outside)
    assert abs(sent['scrub'][-1] - 8.0) < 0.3, sent['scrub']
    # Hidden mid-drag (the tab left, a recording chosen): no release is coming.
    assert mini._grab is not None
    mini.hide()
    assert mini._grab is None
    mini.show()
    # Panned wholly off the recorded band, the box rests against the edge.
    mini.set_window(20.0, 120e6, 121e6)
    box = mini.window_rect()
    assert box.width() >= mini.WINDOW_MIN_PX - 1e-6 and mini._frame().contains(box), box
    mini.set_window(20.0, 50e6, 51e6)
    box = mini.window_rect()
    assert box.width() >= mini.WINDOW_MIN_PX - 1e-6 and mini._frame().contains(box), box
    mini.close()

    # The view: unticking Waterfall takes the whole pane, the map's column
    # with it, so the spectrum has the height; and a clock that goes back
    # (a recording looping) starts the history again.
    from fm_receiver.widgets import SpectrumView
    view = SpectrumView('test', waterfall=True)
    view.resize(700, 400)
    view.show()
    view.set_extent(97e6, 99e6)
    pump(0.1)
    view.set_map(True)
    assert pump(2, lambda: view.minimap.width() == view.MAP_W)
    # The map is a column as tall as the spectrum and the waterfall together,
    # and the two plots end where it begins (so they stay lined up).
    top = view.minimap.mapTo(view, QtCore.QPoint(0, 0)).y()
    bottom = top + view.minimap.height()
    assert top <= view.plot.mapTo(view, QtCore.QPoint(0, 0)).y() + 2, top
    wf_bottom = view.wf_plot.mapTo(view, QtCore.QPoint(0, view.wf_plot.height())).y()
    assert bottom >= wf_bottom - 2, (bottom, wf_bottom)
    assert view.minimap.height() > view.plot.height() + view.wf_plot.height() - 10
    edge = view.minimap.mapTo(view, QtCore.QPoint(0, 0)).x()
    assert abs(view.plot.mapTo(view, QtCore.QPoint(view.plot.width(), 0)).x() - edge) <= \
        view.MAP_GAP + 1 and abs(view.wf_plot.mapTo(view, QtCore.QPoint(view.wf_plot.width(), 0)).x()
                                 - edge) <= view.MAP_GAP + 1, 'both plots end at the map'
    # Unticking Waterfall takes the waterfall away and the spectrum has its
    # height; the map stays, a scrubber all the same.
    view.wf_check.setChecked(False)
    assert view.wf_plot.isHidden() and not view.minimap.isHidden()
    view.wf_check.setChecked(True)
    assert not view.wf_plot.isHidden()
    x = np.linspace(97e6, 99e6, 256)
    view.wf_clock = lambda: view._test_clock
    for t in (10.0, 10.1, 10.2, 10.3):
        view._test_clock = t
        view.set_data(x, np.full(256, -50.0))
    assert view._hist.n == 4, view._hist.n
    view._test_clock = 0.1                     # round again
    view.set_data(x, np.full(256, -50.0))
    assert view._hist.n == 1, 'the history starts again when the clock goes back'
    view._test_clock = 0.2
    view.set_data(x, np.full(256, -50.0))
    order, times = view._hist.newest_first()
    assert view._hist.n == 2 and np.all(np.diff(times) < 0), times

    # The waterfall's time scale: live it is seconds ago; in a recording it
    # is the recording's own time, ticks at round times (so they move with the
    # picture), none before it began, a tenth of a second where they are close.
    axis = view.time_axis
    assert axis.origin is None
    assert axis.tickStrings([0.0, 5.0, 10.0], 1, 5) == ['now', '5 s', '10 s']

    def labels(origin, low, high):
        view.set_time_origin(origin)
        [(step, values)] = axis.tickValues(low, high, 200)
        return sorted(zip(values, axis.tickStrings(values, 1, step)), reverse=True)

    got = labels(35.3, 0.0, 20.0)
    assert [t for _, t in got] == ['0:20', '0:25', '0:30', '0:35'], got
    assert abs(got[-1][0] - 0.3) < 1e-9 and abs(got[0][0] - 15.3) < 1e-9, got
    assert [t for _, t in labels(7.0, 0.0, 20.0)] == ['0:00', '0:05'], 'none before the start'
    assert [t for _, t in labels(0.0, 0.0, 20.0)] == ['0:00']
    assert [t for _, t in labels(10.2, 0.0, 2.5)] == [
        '0:08.0', '0:08.5', '0:09.0', '0:09.5', '0:10.0'], labels(10.2, 0.0, 2.5)
    assert [t for _, t in labels(3725.3, 0.0, 20.0)] == [
        '1:01:50', '1:01:55', '1:02:00', '1:02:05'], labels(3725.3, 0.0, 20.0)
    view.set_time_origin(None)
    assert axis.tickStrings([0.0, 5.0], 1, 5) == ['now', '5 s']
    view.close()
    print("part 1b passed")


# ============================================================ part 2

WINDOWS = []


def make_window(argv, config):
    args = fmapp.parse_args(argv + ['--no-sound-card'])
    window = fmapp.MainWindow(args, config)
    WINDOWS.append(window)
    window.show()
    pump(0.2)
    window.start_initial()
    return window


def drag_map(mini, to_seconds, to_hz=None, release=True):
    """Take the map's box by its middle and carry it so the playhead is at
    ``to_seconds`` (and, with ``to_hz``, its band centred there)."""
    box = mini.window_rect()
    x0, y0 = int(box.center().x()), int(box.center().y())
    x1 = x0 if to_hz is None else int(mini.x_of(to_hz) - (mini.x_of(mini._window_centre_hz())
                                                           - box.center().x()))
    y1 = y0 + int(mini.y_of(to_seconds) - mini.y_of(mini.position))
    QtTest.QTest.mousePress(mini, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                            QtCore.QPoint(x0, y0))
    mouse_move(mini, QtCore.QPoint(x1, y1))
    end = QtCore.QPoint(x1, y1)
    if release:
        QtTest.QTest.mouseRelease(mini, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    QAPP.processEvents()
    return end


def seconds_shown(w):
    shown = w.time_label.text().split('/')[0].strip()
    m, s = shown.split(':')
    return int(m) * 60 + int(s)


def part2_window():
    folder = signals.ensure_dir(os.path.join(FOLDER, 'recordings'))
    station = signals.write_station(os.path.join(FOLDER, 'synth'), seconds=12.0)
    w = make_window(['--file', station], {'recording_dir': folder})
    e = w.engine
    assert e.running and w._mode == 'receive', w.status.text()
    assert pump(15, lambda: w.lbl['station_name'].text() == 'TEST FM'), 'no RDS'

    # Record everything; the name has to hold 8 s to be kept, then retune.
    for box in (w.rec_audio, w.rec_channel, w.rec_band):
        box.setChecked(True)
    w.rec_btn.click()
    assert w._rec_info is not None
    pump(recording.NAME_STEADY_S + 2.5)
    w._step(1)                                   # 98.8: the channel's part 2
    pump(1.5)
    w.rec_btn.click()
    assert w._rec_info is None
    live_tuner = w.tuner.value()

    # Recordings: the radio closes, the recording is listed with its name.
    w.tabs.setCurrentIndex(2)
    assert w.radio is None and e.radio is None and not e.running, 'the radio stays open'
    assert not w.radio_combo.isEnabled() and not w.rec_btn.isEnabled()
    assert w.rec_list.count() == 1, w.rec_list.count()
    first = w.rec_list.item(0).text()
    assert first.startswith('98.70 MHz   TEST FM'), first
    assert 'WAV + IQ channel (2 parts) + IQ band' in first, first
    rec = w._rec_sel
    assert w.track_combo.count() == 4 and w._track.kind == 'iq-band'
    view = w.rf_view
    mini = view.minimap
    # There is nothing to record in Recordings, and no radio to set the
    # gain of: no Record box, no RF gain; Audio stays.
    assert w.record_box.isHidden() and w.gain_box.isHidden() and not w.audio_box.isHidden()
    # The mini map slides open beside the waterfall and is drawn from the
    # file; the audio view's stays shut.
    assert w.top_stack.currentWidget() is view
    assert pump(5, lambda: mini._index is not None), 'no map'
    assert pump(3, lambda: not mini.isHidden() and mini.width() == view.MAP_W), mini.width()
    assert w.audio_view.minimap.isHidden() and mini.duration == w._track.seconds
    assert mini.extent_hz == library.extent(w._track), mini.extent_hz
    # Coloured by the RF spectrum's Ref level and Range, and follows them.
    assert mini._levels == (view.ref_knob.value() - view.range_knob.value(),
                            view.ref_knob.value()), mini._levels
    before = mini._index.copy()
    view.ref_knob.setValue(view.ref_knob.value() - 30)
    view.range_knob.setValue(60)
    assert mini._levels == (view.ref_knob.value() - 60, view.ref_knob.value())
    assert not np.array_equal(before, mini._index), 'the colours did not move'
    # Its box is what the waterfall shows: that much time, that band.
    vb = view.plot.getPlotItem().getViewBox()
    assert mini.span == view.wf_span_s, (mini.span, view.wf_span_s)
    assert abs(mini.window_hz[0] - vb.viewRange()[0][0] * 1e6) < 1, mini.window_hz

    def newest(v):
        ring = v._hist
        return ring.t[(ring.head - 1) % len(ring.t)]

    # Before it plays, a seek draws the waterfall's stretch from the file,
    # ending where the box's top edge is.
    w._seek_to(8.0)
    assert pump(3, lambda: view._hist is not None and view._hist.n > 0
                and not view.wf_frozen), 'no waterfall drawn from the file'
    assert abs(newest(view) - 8.0) < view.wf_span_s / view.WF_ROWS + 0.05, newest(view)
    assert view._wf is not None and view._x is not None and not w.play_btn.isChecked()
    assert abs(mini.position - 8.0) < 0.1 and seconds_shown(w) == 8
    assert abs(view.time_axis.origin - 8.0) < 0.01, 'the time scale is the recording\'s own'
    # The map looks like the waterfall: the same noise at the same level (the
    # most of 32 bins reads some 4 dB brighter at the median).
    order, _ = view._hist.newest_first()
    drawn = float(np.median(view._hist.rows[order].astype(float)))
    mapped = float(np.nanmedian(mini._db))
    assert abs(drawn - mapped) < 2.5, ('map vs waterfall, dB', mapped, drawn)
    # Played on from there, the radio's rows carry on from the file's, on
    # the track's own time, at the same level.
    # What was done to the view before Play (zoomed, the box carried sideways,
    # the waterfall's time set) is still so after it.
    view.set_wf_span(10.0)
    view.span_knob.setValue(1e6)
    view.pan_to(98.2e6)
    centre = view.center_hz
    w.play_btn.setChecked(True)
    assert pump(8, lambda: newest(view) > 8.5 and not view.wf_frozen), newest(view)
    assert view.wf_clock is not None
    assert view.wf_span_s == 10.0 and abs(view.span_knob.value() - 1e6) < 1, \
        (view.wf_span_s, view.span_knob.value())
    assert abs(view.center_hz - centre) < 1e3 and abs(centre - 98.2e6) < 1e5, (view.center_hz, centre)
    order, times = view._hist.newest_first()
    peaks = view._hist.rows[order].astype(float).max(axis=1)
    filed, live = peaks[times < 7.9], peaks[times > 8.3]
    assert len(filed) > 20 and len(live) > 3, (len(filed), len(live))
    assert np.all(np.diff(times) <= 1e-9), 'the history is in time order'
    assert abs(np.median(filed) - np.median(live)) < 3, (np.median(filed), np.median(live))
    w.play_btn.setChecked(False)
    view.full_btn.click()
    view.set_wf_span(20.0)
    w._seek_to(0.0)

    # The band plays with RDS; the time goes on.
    w.play_btn.setChecked(True)
    assert e.running and e.rx is not None and w._mode == 'playback', w.status.text()
    assert w.top_stack.currentWidget() is w.rf_view and w.bottom.isVisible()
    # The strip's dB are the spectrum's: its loudest level, the view's.
    assert pump(3, lambda: w._rx_sig is not None)
    pump(1)
    # (The typical row's peak: the single loudest cell of hundreds is a
    # modulation extreme, which moves with the number of rows.)
    shown = float(np.max(w._rx_sig[1]))
    mapped = float(np.median(np.nanmax(mini._db, axis=1)))
    assert abs(shown - mapped) < 3, (shown, mapped)
    # Playing, the view's own saved scale, and the map follows it.
    assert mini._levels == (view.ref_knob.value() - view.range_knob.value(),
                            view.ref_knob.value())
    # The waterfall's rows are on the track's own time, and so is its scale.
    assert abs(view.time_axis.origin - w._shown_at) < 0.3, (view.time_axis.origin, w._shown_at)
    assert view.wf_clock is not None and not view.wf_frozen
    here = w._play.radio.position() / w._play.radio.rate
    assert abs(newest(view) - here) < 0.6, (newest(view), here)
    assert pump(12, lambda: 'TEST FM' in w.play_station.text()), w.play_station.text()
    assert pump(8, lambda: 'Hello from the synthetic station' in w.play_text.text()), \
        w.play_text.text()
    assert w.rf_view._x is not None and w.rf_view._wf is not None, 'no spectrum or waterfall'
    total = w._track.seconds

    # Pause: the flowgraph stops, the place holds; resume keeps the RDS.
    w.play_btn.setChecked(False)
    assert not e.running and w.play_btn.text() == 'Play'
    held = w._play.radio.position()
    pump(0.8)
    assert w._play.radio.position() == held
    w.play_btn.setChecked(True)
    assert e.running and e.rx.rds.snapshot()['station_name'].strip() == 'TEST FM'
    assert pump(2, lambda: w._play.radio.position() > held + 0.3 * w._play.radio.rate)

    # Dragging the map's box jumps there: the time follows the pointer while
    # the button is down, the sound waits for it to come up, and the
    # waterfall shows the stretch ending there, drawn from the file.
    view.set_wf_span(5.0)                         # a box smaller than the file
    assert pump(2, lambda: mini.span == 5.0) and total > 8, (mini.span, total)
    held = w._play.radio.position()
    end = drag_map(mini, 0.75 * total, release=False)
    assert abs(w._shown_at - 0.75 * total) < 0.5 and w._scrubbing, \
        (w._shown_at, 0.75 * total, w._scrubbing, mini.position)
    assert abs(seconds_shown(w) - w._shown_at) < 1.0, w.time_label.text()
    assert pump(3, lambda: abs(newest(view) - 0.75 * total) < 0.4), (newest(view), 0.75 * total)
    assert view.wf_frozen, "the radio's rows wait while the box is carried"
    assert abs(w._play.radio.position() - held) < 0.5 * w._play.radio.rate * 2, \
        'not seeked until the button comes up'
    QtTest.QTest.mouseRelease(mini, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    assert pump(3, lambda: not view.wf_frozen and not w._scrubbing), 'the rows go on after the drop'
    assert abs(w._play.radio.position() / w._play.radio.rate - 0.75 * total) < 0.8, \
        w._play.radio.position()
    # The box dragged sideways pans the spectrum - it does not retune.
    station = e.station_hz
    view.span_knob.setValue(500e3)
    assert pump(2, lambda: abs(mini.window_hz[1] - mini.window_hz[0] - 500e3) < 1e3), mini.window_hz
    drag_map(mini, mini.position, to_hz=98.1e6)
    assert abs(view.center_hz - 98.1e6) < 60e3, view.center_hz
    assert abs(mini.window_hz[0] + 250e3 - 98.1e6) < 60e3, mini.window_hz
    assert e.station_hz == station, 'panning is not tuning'
    view.full_btn.click()
    # The wheel on the map is the waterfall's time zoom.
    mini.wheelEvent(Qt.QWheelEvent(QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
                                   QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                                   QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                   QtCore.Qt.NoScrollPhase, False))
    assert view.wf_span_s > 5.0 and mini.span == view.wf_span_s, view.wf_span_s
    assert pump(3, lambda: not view.wf_frozen), 'zoomed: drawn again from the file'
    view.set_wf_span(20.0)
    # A click on the spectrum tunes within the recorded band.
    w.tune(98.6e6)
    assert abs(e.station_hz - 98.6e6) < 1 and w._mode == 'playback'
    w.tune(98.7e6)

    # No loop: it stops at the end and goes back to the start.
    w.loop_check.setChecked(False)
    assert pump(total + 3, lambda: not w.play_btn.isChecked()), 'never stopped'
    assert seconds_shown(w) == 0 and 'Finished' in w.status.text() and not e.running
    # Loop: round again, past the end.
    w.loop_check.setChecked(True)
    w._seek_to(0.9 * total)
    w.play_btn.setChecked(True)
    assert pump(total * 0.1 + 3, lambda: w._play.radio.played() > w._play.radio.total)
    assert w.play_btn.isChecked() and e.running
    assert pump(3, lambda: not view.wf_frozen), 'drawn again after the wrap'
    order, times = view._hist.newest_first()
    assert np.all(np.diff(times) <= 1e-9), 'the history is in time order after a loop'
    assert abs(newest(view) - w._play.radio.position() / w._play.radio.rate) < 1.0
    w.loop_check.setChecked(False)

    # The WAV: its sound's spectrum, and the RadioText Record logged.
    audio = [t.kind for t in rec.tracks].index('audio')
    w.track_combo.setCurrentIndex(audio)
    w._track_chosen(audio)
    assert w._track.kind == 'audio' and w.play_btn.isChecked(), 'playing went on'
    assert e.player is not None and e.rx is None and e.radio is None
    assert w.top_stack.currentWidget() is w.audio_view and not w.bottom.isVisible()
    assert pump(3, lambda: w.audio_view._x is not None)
    pump(1.0)
    x, db = w.audio_view._x, w.audio_view._db
    band = (x > 0.5) & (x < 3.5)
    peak = x[band][np.argmax(db[band])]
    assert abs(peak - 1.0) < 0.05 or abs(peak - 2.5) < 0.05, peak     # the tones, kHz
    assert max(w.meter._rms) > -30, w.meter._rms
    # A WAV's map is beside the audio view's waterfall: the sound's spectrum
    # sets its colours, its frequencies are linear and its dB the view's.
    av = w.audio_view
    amini = av.minimap
    assert pump(3, lambda: not amini.isHidden() and amini.width() == av.MAP_W), amini.width()
    assert pump(2, lambda: mini.isHidden()), 'the RF map shuts'
    assert amini._levels == (av.ref_knob.value() - av.range_knob.value(),
                             av.ref_knob.value()), amini._levels
    assert amini.extent_hz == (0.0, 24000.0), amini.extent_hz
    assert pump(5, lambda: amini._index is not None), 'no WAV map'
    # Where it is playing: this WAV was recorded across a retune, so its
    # loudest moment is not now.
    rows = amini._db.shape[0]
    at = int((amini.duration - w._play.source.position / 48000) / amini.duration * rows)
    # A tone shares its map column (8 of the view's bins, the mean of their
    # power) with silence: some 6 dB under the view's own peak.
    shown = float(np.max(db))
    mapped = float(np.nanmax(amini._db[max(0, at - 3):at + 4]))
    assert abs((shown - 6) - mapped) < 3, (shown, mapped, float(np.nanmax(amini._db)))
    assert av.wf_clock is not None and pump(3, lambda: av._hist is not None and av._hist.n > 0)
    av.range_knob.setValue(av.range_knob.value() - 20)
    assert amini._levels[0] == av.ref_knob.value() - av.range_knob.value()
    assert 'Hello from the synthetic station' in w.play_text.text(), w.play_text.text()
    w.play_btn.setChecked(False)
    assert e.running and w._play.source.paused, 'a WAV pauses on silence'
    w._seek_to(1.0)
    assert w._play.source.position == 48000
    assert pump(3, lambda: not av.wf_frozen and abs(newest(av) - 1.0) < 0.2), newest(av)

    # A drag cut short by what is under it (the recording deleted) lets go.
    drag_map(amini, 0.5 * amini.duration, release=False)
    assert w._scrubbing and amini._grab is not None

    # Delete it: every file goes, and so does the list's line.
    before = len(os.listdir(folder))
    assert before >= 9, os.listdir(folder)
    w._delete_recording(rec)
    assert w._play is None and w.rec_list.count() == 0, w.rec_list.count()
    assert not os.listdir(folder), os.listdir(folder)
    assert 'No recordings' in w.lib_note.text()
    assert not w._scrubbing and pump(2, lambda: amini._grab is None), 'the drag went on'

    # A recording that cannot be read (no description beside its samples), and
    # one with none (an empty file): chosen, they say so and do not fall over.
    bad = os.path.join(folder, 'fm-99.10MHz-20260921-120000-iq-band.cfile')
    open(bad, 'wb').write(b'\0' * 4096)
    empty = os.path.join(folder, 'fm-97.30MHz-20260921-130000-iq-band')
    open(empty + '.cfile', 'wb').close()
    json.dump({'rate': 500e3, 'offset_hz': 0, 'station_hz': 97.3e6, 'center_hz': 97.3e6},
              open(empty + '.json', 'w'))
    w._refresh_library()
    assert w.rec_list.count() == 2, w.rec_list.count()
    for row in (1, 0):                              # the unreadable one, then the empty
        w.rec_list.setCurrentRow(row)
        pump(0.5)
        assert w._rec_sel is not None and w._track is not None
        if row == 1:
            assert w._track.error and not w.play_btn.isEnabled(), w._track.error
            assert pump(2, lambda: view.minimap.isHidden()), 'no map for what cannot be read'
        else:
            assert not w._track.error and w._track.seconds == 0.0
            assert view.minimap.duration == 0.0
    for path in (bad, empty + '.cfile', empty + '.json'):
        os.remove(path)
    w._refresh_library()
    assert w.rec_list.count() == 0

    # Back to Receive: the radio opens again, tuned where it was.
    w.tabs.setCurrentIndex(1)
    assert pump(3, lambda: view.minimap.isHidden() and av.minimap.isHidden()), 'the maps stay open'
    assert not w.record_box.isHidden(), 'Receive has its Record box'
    assert w.rx_gain_slot.isAncestorOf(w.gain_row), 'Receive has its RF gain row'
    assert view.wf_clock is None and av.wf_clock is None and not view.wf_frozen
    assert view.time_axis.origin is None and av.time_axis.origin is None, 'seconds ago again'

    assert e.running and w.radio is not None and w._mode == 'receive', w.status.text()
    assert w.radio_combo.isEnabled() and w.rec_btn.isEnabled()
    assert abs(w.tuner.value() - live_tuner) < 1, (w.tuner.value(), live_tuner)

    # Closed in Recordings, it opens there next time - without the radio.
    w.tabs.setCurrentIndex(2)
    w.close()
    saved = json.load(open(os.environ['FMRX_CONFIG']))
    assert saved['mode'] == 'recordings' and abs(saved['frequency_mhz'] * 1e6 - live_tuner) < 1
    w2 = make_window([], {**saved, 'recording_dir': folder})
    assert w2._live is not None and w2.radio is None and w2.engine.radio is None
    w2.close()
    print("part 2 passed")


def main():
    keep = '--keep' in sys.argv
    try:
        part1_pieces()
        part1b_minimap()
        part2_window()
    finally:
        # A failed check must not leave a flowgraph running into interpreter
        # shutdown: collecting its Python blocks under it aborts the process.
        for window in WINDOWS:
            window.close()
        if keep:
            print("files kept in", FOLDER)
        else:
            shutil.rmtree(FOLDER, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
