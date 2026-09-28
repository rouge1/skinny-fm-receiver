"""The running window's control socket: another program - ``tools/fmctl``,
or Claude through it - works the window as a click would.

While the window is open it listens on a local socket
(``config.control_path``: a Unix socket in the session's runtime folder,
the user's only, never the network). A client sends one command a line and
gets one JSON line back::

    tune 99.1                     plain words, or
    {"cmd": "tune", "args": [99.1]}

    {"ok": true, "result": {...}, "status": "Receiving 99.100 MHz ..."}
    {"ok": false, "error": "...", "status": "..."}

Every command runs on the Qt main thread and **works the widgets a click
does** - the tuner's digits, the gain slider, the tabs - so the window moves
in front of the user, the settings are saved as usual, and the window and
the API never disagree. A command the window would refuse (gain with AGC
on, the Center while sweeping) is refused the same way, as an error. The
status line comes back with every reply, so a radio's complaint is seen.

Commands run one at a time, from all clients in turn: opening a radio pumps
Qt's events, and a second command must not run inside the first. ``wait``
holds the queue for its seconds without blocking the window.
"""

import json
import os
import shlex
import sys

import numpy as np  # type: ignore
from PyQt5 import Qt, QtCore, QtNetwork  # type: ignore

from .config import control_path

#: A command line longer than this is refused, and the client dropped.
MAX_LINE = 64 * 1024
#: The longest path a Unix socket takes: 104 bytes on macOS, 108 on Linux,
#: less the terminating nul.
MAX_PATH = 103
#: The longest ``wait``.
MAX_WAIT_S = 120.0


class CommandError(Exception):
    """A command the window would refuse: said as an error, not a crash."""


class Later:
    """A reply that comes after ``seconds``, from ``then()``."""

    def __init__(self, seconds, then):
        self.seconds = seconds
        self.then = then


def _plain(html_text):
    doc = Qt.QTextDocument()
    doc.setHtml(html_text)
    return doc.toPlainText().strip()


def _mhz(hz):
    return None if hz is None else round(hz / 1e6, 6)


def _number(word, what):
    try:
        return float(word)
    except (TypeError, ValueError):
        raise CommandError(f"{what}: expected a number, got {word!r}") from None


def _on_off(word, what):
    word = str(word).lower()
    if word in ('on', '1', 'true', 'yes'):
        return True
    if word in ('off', '0', 'false', 'no'):
        return False
    raise CommandError(f"{what}: expected on or off, got {word!r}")


def _args(args, low, high, usage):
    if not low <= len(args) <= high:
        raise CommandError(f"usage: {usage}")
    return args


# ============================================================== commands
# Each takes the window and the command's words, and returns what the reply
# carries as its result. HELP holds each one's usage and what it does.

HELP = {}
COMMANDS = {}


def command(name, usage, text):
    def register(fn):
        COMMANDS[name] = fn
        HELP[name] = (usage, text)
        return fn
    return register


@command('help', 'help', "These commands.")
def cmd_help(w, args):
    return {name: f"{usage} - {text}" for name, (usage, text) in HELP.items()}


def _tuner_range(w):
    """Where the tuner can go: in Receive, the band around the Center
    (the engine's; the digits span the whole radio), else the digits'."""
    if w._mode in ('receive', 'playback') and w.engine.rx is not None:
        return w.engine.tuner_range()
    return w.tuner.minimum(), w.tuner.maximum()


def _agc_offered(w):
    # Not isVisible(): a folded Radio box hides the gain row, AGC and all.
    return bool(w._agc_available() and w.agc_box.isEnabled())


@command('status', 'status', "What the window shows: radio, tab, frequencies, gain, "
         "audio, stereo, RDS, HD Radio, signal, clipping, recording.")
def cmd_status(w, args):
    e = w.engine
    radio = w.radio
    out = {
        'radio': radio.kind if radio is not None else None,
        'radio_name': radio.describe() if radio is not None else None,
        'running': bool(e.running),
        'tab': w._tab_mode(),
        'mode': w._mode,
        'tuner_mhz': _mhz(w.tuner.value()),
        'tuner_range_mhz': [_mhz(hz) for hz in _tuner_range(w)],
        'center_mhz': _mhz(e.lo_hz if w._mode == 'receive' else w.center_entry.value()),
        'station_mhz': _mhz(e.station_hz) if w._mode in ('receive', 'playback') else None,
        'rate_msps': round(e.rate / 1e6, 6) if e.rate else None,
        'gain_percent': w.gain_slider.value(),
        'agc': bool(w._agc_available() and w.agc_box.isChecked()),
        'agc_available': _agc_offered(w),
        'volume': round(w.volume_knob.value()),
        'muted': w.mute_btn.isChecked(),
        'channel_filter_khz': round(w.chan_entry.value() / 1e3, 3),
        'step_khz': w._step_hz() / 1e3,
        'clipped_percent': (round(100 * w._clip_smooth, 3)
                            if w._clip_smooth is not None else None),
        'recording': w.rec_btn.isChecked(),
        'recording_text': w.rec_label.text() or None,
    }
    rx = e.rx
    if rx is not None and w._mode in ('receive', 'playback'):
        snap = rx.rds.snapshot()
        snr = w._snr_db()
        out['signal'] = {
            'channel_dbfs': round(float(rx.channel_power_db()), 1),
            'snr_db': round(snr, 1) if snr is not None else None,
        }
        out['stereo'] = {'enabled': w.stereo_check.isChecked(),
                         'pilot_locked': bool(rx.pilot_locked())}
        good = (None if not snap['blocks_seen']
                else round(100 * (1 - (snap['block_error_rate'] or 0)), 1))
        out['rds'] = {
            'pi': snap['pi_hex'] or None,
            'callsign': snap['callsign_confirmed'] or snap['callsign'] or None,
            'ps': snap['ps'] or None,
            'station': snap['station_short'] or snap['station_name'] or None,
            'radiotext': snap['radiotext'] or None,
            'pty': snap['pty'] or None,
            'groups': snap['groups'],
            'blocks_good_percent': good,
        }
        out['hd'] = _hd_state(w)
    return out


def _hd_state(w):
    s = w.engine.hd.status()
    rx = w.engine.rx
    return {
        'available': s['available'],
        'enabled': s['enabled'],
        'program': s['program'] + 1,
        'heard': rx.hd_heard if rx is not None else None,
        'synced': s['synced'],
        'playing': s['playing'],
        'station': s['station'] or None,
        'slogan': s['slogan'] or None,
        'mode': s['mode'],
        'ber': s['ber'],
        'kbps': s['kbps'],
        'decode_errors': s['decode_errors'],
        'crc_errors': s['crc_errors'],
        'damaged_percent': round(100 * s['damaged_share'], 1),
        'underruns': s['underruns'],
        'iq_dropped_s': s['iq_dropped_s'],
        'buffer_s': s['buffer_s'],
        'programs': {f"HD{n + 1}": {'name': s['names'].get(n),
                                    'type': s['types'].get(n),
                                    'audio': n in s['audio']}
                     for n in sorted(set(s['names']) | set(s['types']) | set(s['audio']))},
        'title': s['title'] or None,
        'artist': s['artist'] or None,
        'status': _plain(w.hd_lbl['status'].text()),
    }


@command('tune', 'tune MHZ', "The tuner, as its digits do. Outside the band around "
         "the Center, the Center moves first, as Center on tuner does.")
def cmd_tune(w, args):
    mhz = _number(_args(args, 1, 1, 'tune MHZ')[0], 'tune')
    hz = mhz * 1e6
    moved = False
    radio = w.radio

    def inside():
        low, high = _tuner_range(w)
        return low <= hz <= high

    # An IQ file's band is where it was recorded: its Center can't move.
    if (not inside() and w._tab_mode() == 'receive' and w._mode == 'receive'
            and radio is not None and radio.kind != 'file'
            and radio.freq_range_hz[0] <= hz <= radio.freq_range_hz[1]):
        w.center_entry.setValue(hz - radio.lo_offset_hz, emit=True)
        moved = True
    # Checked first: the digits would clamp it to the edge, and tune there.
    if not inside():
        low, high = _tuner_range(w)
        raise CommandError(f"{mhz:g} MHz is outside the tuner's range, "
                           f"{low / 1e6:.3f}-{high / 1e6:.3f} MHz")
    w.tuner.setValue(hz, emit=True)
    return {'tuner_mhz': _mhz(w.tuner.value()), 'center_moved': moved,
            'center_mhz': _mhz(w.engine.lo_hz) if w._mode == 'receive' else None}


@command('center', 'center MHZ', "The Radio box's Center (Receive only).")
def cmd_center(w, args):
    mhz = _number(_args(args, 1, 1, 'center MHZ')[0], 'center')
    if w._tab_mode() != 'receive' or w._mode != 'receive' or w.engine.rx is None:
        raise CommandError("the Center is Receive's: switch with 'mode receive' first")
    w.center_entry.setValue(mhz * 1e6, emit=True)
    return {'center_mhz': _mhz(w.engine.lo_hz), 'tuner_mhz': _mhz(w.tuner.value())}


@command('gain', 'gain PERCENT', "The RF gain slider, 0-100.")
def cmd_gain(w, args):
    value = _number(_args(args, 1, 1, 'gain PERCENT')[0], 'gain')
    slider = w.gain_slider
    if not slider.isEnabled():
        raise CommandError("the gain slider is greyed out: "
                           + ("AGC is on ('agc off' first)" if w._agc_on()
                              else "this radio has no gain"))
    slider.setValue(int(round(min(max(value, 0), 100))))
    return {'gain_percent': slider.value()}


@command('agc', 'agc on|off', "The AGC box beside the gain.")
def cmd_agc(w, args):
    on = _on_off(_args(args, 1, 1, 'agc on|off')[0], 'agc')
    box = w.agc_box
    if not _agc_offered(w):
        raise CommandError("AGC isn't offered here"
                           + (f": {box.toolTip()}" if box.toolTip() else
                              " (not for this radio or this tab)"))
    box.setChecked(on)
    return {'agc': box.isChecked()}


@command('mode', 'mode sweep|receive|recordings', "The tabs.")
def cmd_mode(w, args):
    from .app import TAB_MODES
    mode = _args(args, 1, 1, 'mode sweep|receive|recordings')[0].lower()
    if mode not in TAB_MODES:
        raise CommandError(f"mode: one of {', '.join(TAB_MODES)}")
    w.tabs.setCurrentIndex(TAB_MODES.index(mode))
    return {'tab': w._tab_mode(), 'mode': w._mode}


@command('volume', 'volume PERCENT', "The Volume knob, 0-100.")
def cmd_volume(w, args):
    value = _number(_args(args, 1, 1, 'volume PERCENT')[0], 'volume')
    w.volume_knob.setValue(min(max(value, 0), 100))
    return {'volume': round(w.volume_knob.value())}


@command('mute', 'mute on|off', "The Mute button.")
def cmd_mute(w, args):
    w.mute_btn.setChecked(_on_off(_args(args, 1, 1, 'mute on|off')[0], 'mute'))
    return {'muted': w.mute_btn.isChecked()}


@command('hd', 'hd on|off|1|2|3|4', "The HD Radio box: the digital audio on or off, "
         "or the program to play (HD1-HD4; this switches it on). Needs nrsc5 installed.")
def cmd_hd(w, args):
    word = _args(args, 1, 1, 'hd on|off|1|2|3|4')[0].lower()
    if not w.engine.hd.available:
        raise CommandError("hd: nrsc5 is not installed")
    number = word[2:] if word.startswith('hd') else word
    if number in ('1', '2', '3', '4'):
        w.hd_check.setChecked(True)
        w.hd_buttons[int(number) - 1].setChecked(True)
        w._hd_program_picked(int(number) - 1)
    else:
        w.hd_check.setChecked(_on_off(word, 'hd'))
    w._refresh_hd()
    return _hd_state(w)


@command('screenshot', 'screenshot PATH.png', "The window, grabbed, as the user sees it.")
def cmd_screenshot(w, args):
    path = _args(args, 1, 1, 'screenshot PATH.png')[0]
    if not os.path.isabs(path):
        raise CommandError("screenshot: give an absolute path (fmctl does)")
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise CommandError(f"screenshot: no folder {folder}")
    if not w.grab().save(path):
        raise CommandError(f"screenshot: could not write {path}")
    return {'path': path}


@command('wait', 'wait SECONDS', "Reply after this long, the window running meanwhile "
         f"(at most {MAX_WAIT_S:.0f} s). Other commands wait too.")
def cmd_wait(w, args):
    seconds = _number(_args(args, 1, 1, 'wait SECONDS')[0], 'wait')
    if not 0 <= seconds <= MAX_WAIT_S:
        raise CommandError(f"wait: 0 to {MAX_WAIT_S:.0f} seconds")
    return Later(seconds, lambda: {'waited_s': seconds})


@command('sweep', 'sweep START STOP', "The Sweep tab over START-STOP MHz, as its "
         "Start and Stop digits set it (switching to Sweep first).")
def cmd_sweep(w, args):
    start, stop = (_number(a, 'sweep') for a in _args(args, 2, 2, 'sweep START STOP'))
    if w.radio is None:
        raise CommandError("no radio is open")
    if not w.radio.can_sweep:
        raise CommandError(f"{w.radio.describe()} can't sweep")
    if stop <= start:
        raise CommandError("sweep: STOP must be above START")
    if w._tab_mode() != 'sweep':
        cmd_mode(w, ['sweep'])
    w._set_sweep_span(start * 1e6, stop * 1e6)
    return {'start_mhz': _mhz(w.sweep_start.value()), 'stop_mhz': _mhz(w.sweep_stop.value())}


@command('peakhold', 'peakhold on|off|clear', "The RF spectrum's Peak hold box; "
         "clear starts the held trace again.")
def cmd_peakhold(w, args):
    word = _args(args, 1, 1, 'peakhold on|off|clear')[0].lower()
    view = w.rf_view
    if word == 'clear':
        view.clear_peak()
    else:
        view.peak_check.setChecked(_on_off(word, 'peakhold'))
    return {'peak_hold': view.peak_check.isChecked()}


#: The spectrum views ``view`` sets, by name: the attribute on the window.
VIEWS = {'rf': 'rf_view', 'mpx': 'mpx_view', 'audio': 'audio_view'}
VIEW_USAGE = ('view [rf|mpx|audio] [span X|full] [center X] [ref DB] [range DB] '
              '[avg N]')


def _view_state(view):
    """A view's dials, frequencies in its own unit (MHz or kHz)."""
    (x0, x1), _ = view.plot.getPlotItem().getViewBox().viewRange()
    return {'unit': view.unit, 'span': round(view.span_knob.value() / view.scale, 6),
            'center': round((x0 + x1) / 2, 6),
            'shown': [round(x0, 6), round(x1, 6)],
            'full': [round(hz / view.scale, 6) for hz in view.full],
            'ref_db': round(view.ref_knob.value(), 1),
            'range_db': round(view.range_knob.value(), 1),
            'avg': int(view.avg_knob.value()), 'level_unit': view.level_unit}


@command('view', VIEW_USAGE, "A spectrum view's dials: rf (the RF spectrum and its "
         "waterfall), mpx (the multiplex), audio (a recording playing). Span and center "
         "in the view's unit (MHz for rf, kHz for the others), full for the whole band; "
         "ref and range set the level scale (display only, the waterfall's colours "
         "too); avg is Average, which smooths the trace peaks reads. With no settings, "
         "reports them; with no view, all three.")
def cmd_view(w, args):
    if not args:
        return {name: _view_state(getattr(w, attr)) for name, attr in VIEWS.items()}
    name, words = args[0].lower(), args[1:]
    if name not in VIEWS:
        raise CommandError(f"view: expected rf, mpx or audio, got {args[0]!r}")
    if len(words) % 2:
        raise CommandError(f"usage: {VIEW_USAGE}")
    view = getattr(w, VIEWS[name])
    pairs = [(words[i].lower(), words[i + 1]) for i in range(0, len(words), 2)]
    for key, _ in pairs:
        if key not in ('span', 'center', 'ref', 'range', 'avg'):
            raise CommandError(f"view: unknown setting {key!r} ({VIEW_USAGE})")
    # All checked first, so a bad one leaves the view as it was.
    values = {key: (word if key == 'span' and word.lower() == 'full'
                    else _number(word, f"view {key}")) for key, word in pairs}
    if 'avg' in values and values['avg'] < 1:
        raise CommandError("view avg: at least 1")
    if 'range' in values and values['range'] <= 0:
        raise CommandError("view range: more than 0 dB")
    if isinstance(values.get('span'), float) and values['span'] <= 0:
        raise CommandError("view span: more than 0")
    if 'center' in values:
        low, high = (hz / view.scale for hz in view.full)
        if not low <= values['center'] <= high:
            raise CommandError(f"view center: {values['center']:g} {view.unit} is off "
                               f"the view ({low:g}-{high:g} {view.unit})")
    # The dials themselves, as a hand turns them: clamped to theirs.
    if 'ref' in values:
        view.ref_knob.setValue(values['ref'])
    if 'range' in values:
        view.range_knob.setValue(values['range'])
    if 'avg' in values:
        view.avg_knob.setValue(round(values['avg']))
    if 'center' in values:
        view.center_hz = values['center'] * view.scale
    if 'span' in values:
        span = values['span']
        view.span_knob.setValue(view.span_knob._max if span == 'full'
                                else span * view.scale)
    if 'center' in values:
        view._apply_span()
    return _view_state(view)


#: Runs of bins over the threshold closer than this many bins (or
#: PEAK_MERGE_HZ) are one signal.
PEAK_MERGE_BINS = 3
PEAK_MERGE_HZ = 10e3


def find_peaks(freqs, db, threshold_db=10.0, limit=20):
    """Signals standing ``threshold_db`` over the floor (the median): each
    run of bins above it, runs a few bins apart merged, as ``(floor,
    [{freq_mhz, level_db, above_floor_db, width_khz}, ...])``, strongest
    first."""
    freqs = np.asarray(freqs, dtype=np.float64)
    db = np.asarray(db, dtype=np.float64)
    if len(db) < 8:
        return float('nan'), []
    floor = float(np.median(db))
    bin_hz = (freqs[-1] - freqs[0]) / max(1, len(freqs) - 1)
    gap = max(PEAK_MERGE_BINS, int(np.ceil(PEAK_MERGE_HZ / max(bin_hz, 1.0))))
    hot = np.flatnonzero(db > floor + threshold_db)
    if not len(hot):
        return floor, []
    peaks = []
    for run in np.split(hot, np.flatnonzero(np.diff(hot) > gap) + 1):
        top = run[np.argmax(db[run])]
        peaks.append({'freq_mhz': round(float(freqs[top]) / 1e6, 6),
                      'level_db': round(float(db[top]), 1),
                      'above_floor_db': round(float(db[top]) - floor, 1),
                      'width_khz': round(float(run[-1] - run[0] + 1) * bin_hz / 1e3, 1)})
    peaks.sort(key=lambda p: -p['level_db'])
    return floor, peaks[:limit]


@command('peaks', 'peaks [THRESHOLD_DB [START STOP]]', "The signals on the RF spectrum "
         "(the held trace while Peak hold is on) THRESHOLD_DB (default 10) over the "
         "floor, within START-STOP MHz if given; the strongest 20.")
def cmd_peaks(w, args):
    usage = 'peaks [THRESHOLD_DB [START STOP]]'
    if len(_args(args, 0, 3, usage)) == 2:
        raise CommandError(f"usage: {usage}")
    threshold = _number(args[0], 'peaks') if args else 10.0
    view = w.rf_view
    if view._x is None or not len(view._x):
        raise CommandError("nothing on the spectrum yet ('wait' a moment)")
    held = (view.peak_check.isChecked() and view._peak is not None
            and len(view._peak) == len(view._x))
    freqs = view._x * view.scale
    db = view._peak if held else view._db
    if len(args) == 3:
        low, high = (_number(a, 'peaks') * 1e6 for a in args[1:])
        inside = (freqs >= low) & (freqs <= high)
        if inside.sum() < 8:
            raise CommandError(f"peaks: {args[1]}-{args[2]} MHz is not on the spectrum "
                               f"({freqs[0] / 1e6:.3f}-{freqs[-1] / 1e6:.3f} MHz)")
        freqs, db = freqs[inside], db[inside]
    floor, peaks = find_peaks(freqs, db, threshold)
    return {'trace': 'peak hold' if held else 'live', 'unit': view.level_unit,
            'floor_db': round(floor, 1),
            'bin_khz': round((freqs[-1] - freqs[0]) / max(1, len(freqs) - 1) / 1e3, 3),
            'span_mhz': [_mhz(freqs[0]), _mhz(freqs[-1])], 'peaks': peaks}


@command('rate', 'rate MSPS', "The Radio box's IQ bandwidth (Receive).")
def cmd_rate(w, args):
    msps = _number(_args(args, 1, 1, 'rate MSPS')[0], 'rate')
    combo = w.rx_rate_combo
    offered = []
    for i in range(combo.count()):
        rate = float(combo.itemData(i))
        enabled = bool(combo.model().item(i).isEnabled())
        offered.append(f"{rate / 1e6:g}" + ("" if enabled else " (greyed)"))
        if abs(rate - msps * 1e6) < 1:
            if not enabled:
                raise CommandError(f"{msps:g} MS/s is greyed out here: "
                                   f"{combo.itemData(i, QtCore.Qt.ToolTipRole)}")
            combo.setCurrentIndex(i)
            combo.activated.emit(i)             # as a pick from the list does
            e = w.engine
            return {'rate_msps': round(e.rate / 1e6, 6) if e.rate else msps}
    raise CommandError(f"rate: this radio offers {', '.join(offered)} MS/s")


RECORD_KINDS = {'audio': 'rec_audio', 'iq-channel': 'rec_channel', 'iq-band': 'rec_band'}


def _record_reply(w):
    paths = []
    for p in w._last_saved:
        paths.append(p)
        # The descriptions beside the IQ: its rate and centre, for other tools.
        if p.endswith('.cfile'):
            paths.extend(q for q in (p[:-6] + '.sigmf-meta', p[:-6] + '.json')
                         if os.path.exists(q))
    files = [{'path': p, 'bytes': os.path.getsize(p) if os.path.exists(p) else None}
             for p in paths]
    return {'recording': w.rec_btn.isChecked(), 'files': files}


@command('record', 'record start|stop, record audio|iq-channel|iq-band on|off',
         "The Record box: what to record, and the Record button. Stop replies "
         "with the files.")
def cmd_record(w, args):
    usage = 'record start|stop, record audio|iq-channel|iq-band on|off'
    word = _args(args, 1, 2, usage)[0].lower()
    if len(args) == 2:
        if word not in RECORD_KINDS:
            raise CommandError(f"record: one of {', '.join(RECORD_KINDS)}")
        box = getattr(w, RECORD_KINDS[word])
        if not box.isEnabled():
            raise CommandError("recording: 'record stop' first to change what is recorded")
        box.setChecked(_on_off(args[1], 'record'))
        return {kind: getattr(w, name).isChecked() for kind, name in RECORD_KINDS.items()}
    if word == 'start':
        if w.rec_btn.isChecked():
            raise CommandError("already recording")
        w.rec_btn.setChecked(True)
        if not w.rec_btn.isChecked():
            raise CommandError(_plain(w.rec_label.text()) or "could not record")
        return {'recording': True}
    if word == 'stop':
        if not w.rec_btn.isChecked():
            raise CommandError("not recording")
        w.rec_btn.setChecked(False)
        return _record_reply(w)
    raise CommandError(f"usage: {usage}")


#: A capture larger than this is refused: pick a lower IQ bandwidth.
MAX_CAPTURE_BYTES = 4e9
#: Bytes a second each kind writes (IQ band: 8 a sample, at the IQ rate).
CAPTURE_RATES = {'iq-channel': 4e6, 'audio': 192e3}


@command('capture', 'capture SECONDS [iq-band|iq-channel|audio]', "Record only that "
         "(default iq-band) for SECONDS, then reply with the files. The Record box's "
         f"ticks are put back after. At most {MAX_CAPTURE_BYTES / 1e9:g} GB.")
def cmd_capture(w, args):
    _args(args, 1, 2, 'capture SECONDS [iq-band|iq-channel|audio]')
    seconds = _number(args[0], 'capture')
    kind = args[1].lower() if len(args) == 2 else 'iq-band'
    if kind not in RECORD_KINDS:
        raise CommandError(f"capture: one of {', '.join(RECORD_KINDS)}")
    if not 0 < seconds <= MAX_WAIT_S:
        raise CommandError(f"capture: 0 to {MAX_WAIT_S:.0f} seconds")
    if w.rec_btn.isChecked():
        raise CommandError("already recording ('record stop' first)")
    rate = w.engine.rate or 0.0
    size = CAPTURE_RATES.get(kind, 8 * rate) * seconds
    if size > MAX_CAPTURE_BYTES:
        raise CommandError(f"capture: {size / 1e9:.1f} GB at {rate / 1e6:g} MS/s; "
                           "pick a lower 'rate', or fewer seconds")
    ticks = {name: getattr(w, name).isChecked() for name in RECORD_KINDS.values()}

    def put_back():
        for name, on in ticks.items():
            getattr(w, name).setChecked(on)

    for k, name in RECORD_KINDS.items():
        getattr(w, name).setChecked(k == kind)
    w.rec_btn.setChecked(True)
    if not w.rec_btn.isChecked():
        put_back()
        raise CommandError(_plain(w.rec_label.text()) or "could not record")
    lo, station = w.engine.lo_hz, w.engine.station_hz

    def finish():
        try:
            if w.rec_btn.isChecked():
                w.rec_btn.setChecked(False)
            reply = _record_reply(w)
            reply.update(kind=kind, seconds=seconds, rate_msps=rate / 1e6,
                         center_mhz=_mhz(lo), station_mhz=_mhz(station))
            return reply
        finally:
            put_back()
    return Later(seconds, finish)


def parse(line):
    """(name, args) from a line: JSON ``{"cmd", "args"}`` or plain words."""
    line = line.strip()
    if line.startswith('{'):
        try:
            msg = json.loads(line)
        except ValueError as exc:
            raise CommandError(f"bad JSON: {exc}") from None
        if not isinstance(msg, dict) or not isinstance(msg.get('cmd'), str):
            raise CommandError('JSON commands are {"cmd": NAME, "args": [...]}')
        args = msg.get('args', [])
        if not isinstance(args, list):
            args = [args]
        return msg['cmd'].lower(), [str(a) for a in args]
    try:
        words = shlex.split(line)
    except ValueError as exc:
        raise CommandError(f"bad quoting: {exc}") from None
    if not words:
        raise CommandError("empty command")
    return words[0].lower(), words[1:]


# ================================================================ server

class ControlServer(QtCore.QObject):
    """The socket, its clients, and the one queue their commands run in."""

    def __init__(self, window, path=None):
        super().__init__(window)
        self.window = window
        self.path = path or control_path()
        self.server = None
        self._buffers = {}                      # socket -> bytes not yet a line
        self._queue = []                        # (socket, line)
        self._busy = False

    def start(self):
        """Listen; False, and said on stderr, if another window already is."""
        if len(os.fsencode(self.path)) > MAX_PATH:
            # Qt says only "Name error".
            print(f"FM receiver: no control socket: {self.path} is longer than a "
                  f"Unix socket's {MAX_PATH} bytes (FMRX_CONTROL names a shorter one)",
                  file=sys.stderr)
            return False
        probe = QtNetwork.QLocalSocket()
        probe.connectToServer(self.path)
        if probe.waitForConnected(200):
            probe.disconnectFromServer()
            print(f"FM receiver: another window has the control socket {self.path}; "
                  "this one has none", file=sys.stderr)
            return False
        # Left behind by a window that died: nobody answers on it.
        QtNetwork.QLocalServer.removeServer(self.path)
        server = QtNetwork.QLocalServer(self)
        server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
        if not server.listen(self.path):
            print(f"FM receiver: no control socket ({self.path}): "
                  f"{server.errorString()}", file=sys.stderr)
            return False
        server.newConnection.connect(self._accept)
        self.server = server
        return True

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None
        for sock in list(self._buffers):
            sock.abort()
        self._buffers.clear()
        self._queue.clear()

    def _accept(self):
        while self.server is not None and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b''
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._gone(s))

    def _gone(self, sock):
        self._buffers.pop(sock, None)
        self._queue = [(s, line) for s, line in self._queue if s is not sock]
        sock.deleteLater()

    def _read(self, sock):
        if sock not in self._buffers:
            return
        data = self._buffers[sock] + bytes(sock.readAll())
        *lines, rest = data.split(b'\n')
        if len(rest) > MAX_LINE:
            self._send(sock, {'ok': False, 'error': 'line too long'})
            sock.disconnectFromServer()
            return
        self._buffers[sock] = rest
        for line in lines:
            if line.strip():
                self._queue.append((sock, line.decode('utf-8', 'replace')))
        self._pump()

    def _pump(self):
        while self._queue and not self._busy:
            sock, line = self._queue.pop(0)
            self._busy = True
            reply = self._run(line)
            if isinstance(reply, Later):
                later = reply
                QtCore.QTimer.singleShot(int(later.seconds * 1000),
                                         lambda s=sock, l=later: self._finish(s, l))
                return
            self._busy = False
            self._send(sock, reply)

    def _finish(self, sock, later):
        try:
            reply = self._reply(later.then())
        except Exception as exc:
            reply = self._error(exc)
        self._busy = False
        self._send(sock, reply)
        self._pump()

    def _run(self, line):
        try:
            name, args = parse(line)
            fn = COMMANDS.get(name)
            if fn is None:
                raise CommandError(f"unknown command {name!r} ('help' lists them)")
            result = fn(self.window, args)
            return result if isinstance(result, Later) else self._reply(result)
        except Exception as exc:
            return self._error(exc)

    def _status(self):
        return _plain(self.window.status.text())

    def _reply(self, result):
        return {'ok': True, 'result': result, 'status': self._status()}

    def _error(self, exc):
        text = str(exc) if isinstance(exc, CommandError) else f"{type(exc).__name__}: {exc}"
        return {'ok': False, 'error': text, 'status': self._status()}

    def _send(self, sock, reply):
        if sock not in self._buffers or sock.state() != QtNetwork.QLocalSocket.ConnectedState:
            return
        sock.write((json.dumps(reply, default=str) + '\n').encode())
        sock.flush()
