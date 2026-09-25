"""The Recordings tab: what is listed, and playing it back - no radio.

Part 1 checks the pieces on their own: recordings grouped from their file
names (and an older recording whose kinds were named a second apart), the
WAV reader (a header cut short too), the overview, the WAV source's seek,
loop and end, and the description Record writes - a station name kept only
once it has held still.

Part 2 records the synthetic station from an IQ file in the Receive tab -
WAV, channel and band, with a retune - then opens the Recordings tab and
plays it back: the radio closes, the recording is listed with its RDS
name, the strip coloured by the RF spectrum's Ref level and Range and in
its dBFS, the band plays with RDS, seeks (by the overview strip too), pauses
and resumes with RDS kept, stops at its end or loops, the WAV plays with
its spectrum and the RadioText logged at record time, a recording is
deleted, and going back to Receive opens the radio where it was.

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

    # The overview: the band's station stands out in its own rows, and a
    # WAV's tones in the rows of their frequencies.
    img, (low, high) = library.overview(new.tracks[0], columns=60, rows=64)
    assert img.shape == (64, 60) and abs(low - 97.15e6) < 1 and abs(high - 99.65e6) < 1
    row = int((98.7e6 - low) / (high - low) * 64)
    profile = img.mean(axis=1)
    assert profile[row - 1:row + 2].max() > np.median(profile) + 20, profile
    img, (low, high) = library.overview(new.tracks[3], columns=30, rows=64)
    edges = np.geomspace(low, high, 65)
    loud = set(np.argsort(img.mean(axis=1))[-2:])
    want = {int(np.searchsorted(edges, 1000.0)) - 1, int(np.searchsorted(edges, 2500.0)) - 1}
    assert loud == want, (loud, want)

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


# ============================================================ part 2

WINDOWS = []


def make_window(argv, config):
    args = fmapp.parse_args(argv + ['--no-audio'])
    window = fmapp.MainWindow(args, config)
    WINDOWS.append(window)
    window.show()
    pump(0.2)
    window.start_initial()
    return window


def click_strip(strip, fraction):
    pos = QtCore.QPoint(int(strip.width() * fraction), strip.height() // 2)
    QtTest.QTest.mousePress(strip, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos)
    QtTest.QTest.mouseRelease(strip, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos)
    QAPP.processEvents()


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
    assert pump(5, lambda: w.timeline._index is not None), 'no overview'
    # The strip is coloured by the RF spectrum's Ref level and Range, and
    # follows them.
    view = w.rf_view
    assert w.timeline._levels == (view.ref_knob.value() - view.range_knob.value(),
                                  view.ref_knob.value()), w.timeline._levels
    before = w.timeline._index.copy()
    view.ref_knob.setValue(view.ref_knob.value() - 30)
    view.range_knob.setValue(60)
    assert w.timeline._levels == (view.ref_knob.value() - 60, view.ref_knob.value())
    assert not np.array_equal(before, w.timeline._index), 'the colours did not move'

    # The band plays with RDS; the time goes on.
    w.play_btn.setChecked(True)
    assert e.running and e.rx is not None and w._mode == 'playback', w.status.text()
    assert w.top_stack.currentWidget() is w.rf_view and w.bottom.isVisible()
    # The strip's dB are the spectrum's: its loudest level, the view's.
    assert pump(3, lambda: w._rx_sig is not None)
    pump(1)
    shown, strip = float(np.max(w._rx_sig[1])), float(np.max(w.timeline._db))
    assert abs(shown - strip) < 3, (shown, strip)
    # Playing, the view's own saved scale, and the strip follows it.
    assert w.timeline._levels == (view.ref_knob.value() - view.range_knob.value(),
                                  view.ref_knob.value())
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

    # A click on the strip jumps there.
    click_strip(w.timeline, 0.75)
    assert abs(w._play.radio.position() / w._play.radio.rate - 0.75 * total) < 0.5, \
        w._play.radio.position()
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
    click_strip(w.timeline, 0.9)
    w.play_btn.setChecked(True)
    assert pump(total * 0.1 + 3, lambda: w._play.radio.played() > w._play.radio.total)
    assert w.play_btn.isChecked() and e.running
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
    # A WAV's strip: the sound's spectrum sets its colours, and its dB are
    # that view's.
    av = w.audio_view
    assert w.timeline._levels == (av.ref_knob.value() - av.range_knob.value(),
                                  av.ref_knob.value()), w.timeline._levels
    assert pump(5, lambda: w.timeline._db is not None), 'no WAV overview'
    # Where it is playing: this WAV was recorded across a retune, so its
    # loudest moment is not now.
    cols = w.timeline._db.shape[1]
    at = int(w._play.source.position / 48000 / w.timeline.duration * cols)
    shown = float(np.max(db))
    strip = float(np.max(w.timeline._db[:, max(0, at - 4):at + 2]))
    assert abs(shown - strip) < 3, (shown, strip, float(np.max(w.timeline._db)))
    av.range_knob.setValue(av.range_knob.value() - 20)
    assert w.timeline._levels[0] == av.ref_knob.value() - av.range_knob.value()
    assert 'Hello from the synthetic station' in w.play_text.text(), w.play_text.text()
    w.play_btn.setChecked(False)
    assert e.running and w._play.source.paused, 'a WAV pauses on silence'
    w._seek_to(1.0)
    assert w._play.source.position == 48000

    # Delete it: every file goes, and so does the list's line.
    before = len(os.listdir(folder))
    assert before >= 9, os.listdir(folder)
    w._delete_recording(rec)
    assert w._play is None and w.rec_list.count() == 0, w.rec_list.count()
    assert not os.listdir(folder), os.listdir(folder)
    assert 'No recordings' in w.lib_note.text()

    # Back to Receive: the radio opens again, tuned where it was.
    w.tabs.setCurrentIndex(1)
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
