"""The FM receiver window - it opens straight onto the radio, no launcher.

Three tabs. The first two are the radio's two modes, sharing it and the big
spectrum view:

- **Sweep (FFT)** hops the radio across a span wider than it can see at
  once and stitches the FFTs (``sweep.py``). No demodulation, so it is
  cheap. The stations it finds are listed; double-click one, or the
  spectrum, to listen.
- **Receive (IQ)** runs the radio at a narrow IQ bandwidth and demodulates
  one station: stereo audio, RDS, the multiplex spectrum (``dsp.py``).

The third, **Recordings**, lists what Record made (``library.py``) and plays
it back. The radio is closed meanwhile - free for other programs - and
opens again on the way out. An IQ recording plays as a radio would, through
Receive's chain, with a band recording's other stations a click away; a WAV
plays as its sound and the sound's spectrum.

Everything else - gain, the view's dials, volume and mute, recording -
works on the running flowgraph without rebuilding it. The window's
settings are saved when it closes (``config.py``).
"""

import argparse
import concurrent.futures
import math
import os
import signal
import sys
import time

import numpy as np  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from . import library, theme
from .config import default_recording_dir, load_config, update_config
from . import bb60_source, bb60_sweep
from .bb60_sweep import RBW_LADDER, RT_MAX_SPAN_HZ
from .dsp import AUDIO_RATE, CHANNEL_MAX_BW, MPX_RATE, wav_source
from .engine import Engine
from .radios import (RADIO_NAMES, IQFile, RadioError, detect_radios,
                     make_radio, plugged_in, rate_label)
from .rds_core import clock_text
from .recording import (NAME_STEADY_S, IqRecording, RecordingInfo, WavWriter,
                        session_base)
from .style import apply_window_theme
from .sweep import SweepPlan, find_stations, to_db
from .widgets import (Card, DigitEntry, Form, PageTabs, Knob, LevelMeter, SpectrumView, StepRoller,
                      ThemeDisc, TimelineStrip, on_raster)

#: (name, start MHz, stop MHz); 'full' is the whole of the radio's sweep
#: range, whichever radio it is (9 kHz-6 GHz on the BB60D).
SWEEP_PRESETS = [('Full range of the radio', 'full', None),
                 ('FM broadcast 87.5-108', 87.5, 108.0),
                 ('FM Japan 76-95', 76.0, 95.0),
                 ('FM OIRT 65.8-74', 65.8, 74.0),
                 ('VHF 30-300', 30.0, 300.0),
                 ('Custom', None, None)]
#: The sweep view stops here when dragged or zoomed out: below 0 Hz there is
#: nothing, and no radio here sweeps above 6 GHz.
SWEEP_VIEW_HZ = (0.0, 6e9)
FFT_SIZES = (1024, 2048, 4096, 8192, 16384)
#: The sweep's bounds are kept at least this far apart: Signal Hound's
#: suggested minimum span for the BB60D's own sweep, and ample for the rest.
MIN_SWEEP_SPAN_HZ = 200e3
#: Stations are listed only where FM broadcasting is - 65.8 MHz (OIRT) to
#: 108 - however much more the sweep covers.
FM_BROADCAST_HZ = (65.8e6, 108e6)
STEPS_KHZ = (10, 50, 100, 200)
#: The channel filter's range: below 60 kHz it cuts into the audio itself;
#: 400 kHz takes in an HD Radio station's sidebands (see dsp.py).
CHANNEL_MIN_HZ, CHANNEL_MAX_HZ = 60e3, CHANNEL_MAX_BW
#: How much a wheel notch over the channel band on the spectrum widens it.
BAND_WHEEL_HZ, BAND_WHEEL_FINE_HZ = 5e3, 1e3
#: What the channel filter's arrows move it by.
CHANNEL_STEP_HZ = 5e3
#: The share of IQ samples at full scale past which a radio with no overload
#: flag (a HackRF) is overloaded. On the bench a HackRF at 47% gain clipped
#: 77% of them, and at 54% all, with RDS lost; at 40% a strong station
#: clipped 0-0.4% with RDS at 99% - harmless, so no warning.
CLIP_WARN = 1e-2
#: Under this share the clipped readout stays in the good colour: ble-scanner
#: saw 0.1-0.6% at its best gain, and 2-8% where packets were lost.
CLIP_NOTE = 3e-3
#: The clipped readout is smoothed over about this long, as ble-scanner's is
#: (0.95 old + 0.05 new per 16 ms).
CLIP_TAU_S = 0.3
#: The clipped share is not read for this long after a radio opens: an
#: RTL-SDR just plugged in clipped 1.5% for its first 0.4 s (its tuner
#: settling), then nothing, which flashed "Input overloaded" for 3 s.
OPEN_CLIP_GRACE_S = 1.0
#: No samples from the radio for this long and the status line says it is
#: lost: longer than any mode switch or retune takes. A radio's own sweep
#: may take longer than this for one pass, so it gets three of those.
STALL_S = 3.0
#: A lost radio's USB is looked at this often (``radios.plugged_in``: the
#: listing costs about 20 ms). Seen gone and then back, it is opened again
#: after BACK_SETTLE_S - once back on the bus a radio still loads its
#: firmware - and tried REOPEN_TRIES times. None of the radios recovers by
#: itself: unplugged and plugged back in (2026-09-23), each stayed silent
#: until it was opened again.
LOST_POLL_S = 1.0
BACK_SETTLE_S = 2.0
REOPEN_TRIES = 3
#: Below any reference level AGC would pick: from here it always rises.
REF_FLOOR_DB = -200.0
#: Over this share of samples clipped, an overload is heavy and AGC takes
#: its whole step down; under it, half: a HackRF at 45% on 99.1 MHz clipped
#: 2.55% once after a clean minute (2026-09-24), where 60% clipped 100%.
CLIP_HEAVY = 0.1
#: While the BB60D reported an overload this recently, its silence is the
#: overload, not a lost radio: overloaded it sends no samples at all (every
#: read empty, each with an ADC overflow line - 98.7 MHz at 60%, 2026-09-24).
OVERLOAD_NOT_LOST_S = 3.0
RADIO_ORDER = ('hackrf', 'usrp', 'bb60', 'rtlsdr', 'file')
#: The tabs, in order.
TAB_MODES = ('sweep', 'receive', 'recordings')
#: The saved view (dials, span) of the RF spectrum in each mode that has one.
VIEW_KEYS = {'receive': 'view_receive', 'playback': 'view_playback'}

DEFAULTS = {
    'radio': None, 'usrp_address': '', 'iq_file': '', 'mode': 'receive',
    'frequency_mhz': 98.7, 'center_mhz': None, 'step_khz': 100, 'gain': {}, 'gain_auto': {},
    'receive_rate': {},
    # Per radio: the most gain AGC on the IQ stream may use - where the
    # slider was last put by hand; the slider itself follows AGC.
    'agc_ceiling': {},
    'sweep_rate': {}, 'settle_ms': {}, 'channel_bw_khz': 200, 'region': 'RBDS',
    'stereo': True, 'volume': 60, 'muted': False, 'sweep_band': 'full',
    'sweep_start_mhz': 87.5, 'sweep_stop_mhz': 108.0, 'sweep_rbw_khz': 0,
    'sweep_realtime': False,
    # The cards folded to their title (the Radio card to its Center).
    'folded': {},
    'sweep_fft': 4096, 'sweep_frames': 16,
    'min_snr_db': 15, 'snap': True, 'record_audio': True,
    'record_iq_channel': False, 'record_iq_band': False, 'recording_dir': '',
    'view_receive': {'span_hz': 1.2e6, 'ref_db': -10, 'range_db': 110, 'avg': 4},
    # A span wider than any sweep: the Span dial clamps it to the whole sweep.
    'view_sweep': {'span_hz': 1e12, 'ref_db': -10, 'range_db': 110, 'avg': 1},
    'view_mpx': {'ref_db': -10, 'range_db': 100, 'avg': 6},
    # A recording's band, all of it; and a WAV's sound, to 16 kHz.
    'view_playback': {'span_hz': 1e12, 'ref_db': -10, 'range_db': 110, 'avg': 4},
    'view_audio': {'span_hz': 16e3, 'ref_db': -10, 'range_db': 90, 'avg': 2},
    'play_loop': False,
    'theme': 'slate', 'geometry': None, 'splitters': {},
}


class IqAgc:
    """AGC for the IQ stream (Receive), where the radio has none of
    its own: the window moves the RF gain from what each health poll says.

    Down ``STEP_DOWN`` % (``STEP_UP`` for a light one: not ``heavy``) once
    overloads show in two polls within ``HOT_S`` -
    not necessarily running: a stalled BB60D's reports arrive unevenly, and
    a poll between them came up empty. An overload is the BB60D's report,
    or over ``CLIP_WARN`` of a HackRF's or RTL-SDR's samples clipped. Back
    up ``STEP_UP`` % after ``RISE_S`` calm - no overload, and under
    ``CLIP_NOTE`` clipped - never over ``ceiling`` (the slider); between
    the two it holds. A rise that brings the overload back is undone (down
    ``STEP_UP``, to where it was calm) and doubles the wait before the
    next, to ``RISE_MAX_S``. It should settle, not hunt: a BB60D's gain
    change leaves a gap of about 0.1 s in the samples (106-110 ms at
    2.5 MS/s); a HackRF's none (measured 2026-09-24). What arrives within
    ``SETTLE_S`` of a change is not judged: the gap, and samples from before.
    """

    STEP_DOWN = 10.0
    STEP_UP = 5.0
    HOT_S = 1.5
    SETTLE_S = 1.0
    RISE_S = 60.0
    RISE_MAX_S = 960.0

    def __init__(self, ceiling, gain):
        self.ceiling = float(ceiling)
        self.gain = min(float(gain), self.ceiling)
        self.changed_at = None
        self.quiet_since = None
        self.risen_at = None
        self.rise_wait = self.RISE_S
        self.hot = []                            # when polls saw an overload

    def update(self, now, overloads, calm=True, heavy=True):
        """The gain to set now, or None; ``overloads`` since the last poll,
        whether it was ``calm`` enough to count towards a rise, and whether
        an overload was ``heavy`` (the BB60D's always are: it says no more)."""
        if self.changed_at is not None and now - self.changed_at < self.SETTLE_S:
            return None
        if self.quiet_since is None:
            self.quiet_since = now
        self.hot = [t for t in self.hot if now - t < self.HOT_S]
        if overloads:
            self.quiet_since = now
            self.hot.append(now)
            if len(self.hot) < 2 or self.gain <= 0:
                return None
            step = self.STEP_DOWN if heavy else self.STEP_UP
            if self.risen_at is not None and now - self.risen_at < self.rise_wait:
                # The rise brought it: back to where it was calm, and wait
                # longer before the next (HackRF, 99.1 MHz: 40% calm, 45%
                # clipped 11%, and a whole step down went on to 35%).
                self.rise_wait = min(2 * self.rise_wait, self.RISE_MAX_S)
                step = self.STEP_UP
            self.risen_at = None
            return self._set(max(0.0, self.gain - step), now)
        if not calm:
            self.quiet_since = now
            return None
        if self.gain < self.ceiling and now - self.quiet_since >= self.rise_wait:
            self.risen_at = self.quiet_since = now
            return self._set(min(self.ceiling, self.gain + self.STEP_UP), now)
        return None

    def _set(self, gain, now):
        self.gain, self.changed_at, self.hot = gain, now, []
        return gain


def _merged(saved):
    config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key, value in saved.items():
        if isinstance(config.get(key), dict) and isinstance(value, dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def _hline():
    line = Qt.QFrame()
    line.setFrameShape(Qt.QFrame.HLine)
    line.setStyleSheet(f"color: {theme.TOKENS['rule_soft']};")
    return line


def _wrapping(label):
    """A label that wraps in whatever width the column gives it, rather than
    widening the column: the left column only scrolls up and down, so a long
    file name or message would otherwise push its controls off the edge."""
    label.setWordWrap(True)
    label.setSizePolicy(Qt.QSizePolicy.Ignored, Qt.QSizePolicy.Preferred)
    label.setMinimumWidth(1)
    return label


def _mono_font(pixels=None):
    """A fixed-pitch face, for text where a gap or a stray character must
    show. Linux has the "Monospace" alias; a Mac has none, and Qt picked
    American Typewriter for it, so Menlo there, at 16 px unless told - the
    size Linux's default 12 pt comes out at."""
    mac = sys.platform == 'darwin'
    font = Qt.QFont('Menlo' if mac else 'Monospace')
    font.setStyleHint(Qt.QFont.TypeWriter)
    if pixels or mac:
        font.setPixelSize(pixels or 16)
    return font


def _coloured(text, token):
    return f"<span style='color:{theme.TOKENS[token]}'>{text}</span>"


class Playback:
    """What the Recordings tab has loaded: an IQ track on its file radio,
    or a WAV track on its source block."""

    def __init__(self, recording, track, radio=None, source=None):
        self.recording = recording
        self.track = track
        self.radio = radio                  # radios.IQFile, for IQ
        self.source = source                # dsp.wav_source, for a WAV
        self.paused = False

    @property
    def is_iq(self):
        return self.radio is not None

    @property
    def name(self):
        return os.path.basename(self.track.path)


def _freq_text(hz):
    """9 kHz, 87.5 MHz, 6 GHz - no more digits than it has."""
    for scale, unit in ((1e9, 'GHz'), (1e6, 'MHz'), (1e3, 'kHz')):
        if hz >= scale:
            return f"{hz / scale:.6g} {unit}"
    return f"{hz:.0f} Hz"


def _share_text(share):
    """A clipped share as a percentage: two decimals below 10%."""
    pct = share * 100
    return f"{pct:.2f}%" if pct < 10 else f"{pct:.1f}%"


class MainWindow(Qt.QWidget):

    def __init__(self, args, config):
        super().__init__()
        self.args = args
        self.cfg = _merged(config)
        if args.theme:
            self.cfg['theme'] = args.theme
        apply_window_theme(self, self.cfg['theme'])
        # Tooltips while another window - the terminal - has the focus: Qt
        # shows them only in the active window otherwise.
        self.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)
        self.setWindowTitle("FM Receiver")
        self.engine = Engine(want_audio=not args.no_sound_card)
        #: Muted by --no-audio, and not yet unmuted: the saved setting is
        #: left as it was, so the flag lasts only for this run.
        self._flag_muted = bool(args.no_audio)
        self.radio = None
        self._mode = None
        self._wav = None
        self._iq_recs = []
        self._rec_t0 = None
        self._last_serial = -1
        self._list_serial = -1
        self._sweep_avg = None
        self._sweep_db = None
        self._wf_hold = None
        #: The sweep's bounds before Real time took them, and the window it
        #: put there - so letting the button out can give the span back.
        self._before_rt = None
        self._rt_span = None
        self._last_wf = 0.0
        self._last_overload = 0
        self._overload_until = 0.0
        self._overload_at = None
        #: AGC on the IQ stream (IqAgc), made when it is first needed, and
        #: the most gain it may use: where the slider was last put by hand.
        self._iq_agc = None
        self._agc_ceiling = None
        #: A lost radio being watched for its return (:meth:`_watch_lost`).
        self._lost = None
        self._clipped = 0.0
        self._clipped_at = None
        self._clip_smooth = None
        self._clip_t = time.monotonic()
        self._opened_at = None
        self._agc_levels = []
        self._names = {}
        self._rx_sig = None
        self._starting = False
        # Recording: its description (RDS and all), for the Recordings tab.
        self._rec_info = None
        # The Recordings tab: the live radio to go back to, what is listed,
        # chosen and playing, and the overview being worked out.
        self._live = None
        self._recordings = []
        self._rec_sel = None
        self._track = None
        self._play = None
        self._start_at = 0.0
        self._heard = ('', 0.0)
        self._overview_job = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        self._edge_timer = Qt.QTimer(self)
        self._edge_timer.setSingleShot(True)
        self._edge_timer.timeout.connect(self._show_range)
        self._build()
        self._restore_geometry()
        self.fast_timer = Qt.QTimer(self)
        self.fast_timer.timeout.connect(self._tick_fast)
        self.fast_timer.start(66)
        self.slow_timer = Qt.QTimer(self)
        self.slow_timer.timeout.connect(self._tick_slow)
        self.slow_timer.start(400)
        self._shortcuts()

    # ================================================================ build
    def _build(self):
        outer = Qt.QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        outer.addLayout(self._build_header())
        self.main_split = Qt.QSplitter(QtCore.Qt.Horizontal)
        self.main_split.addWidget(self._build_left())
        self.main_split.addWidget(self._build_right())
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([self._left_width, 1560 - self._left_width])
        outer.addWidget(self.main_split, 1)

    def _build_header(self):
        row = Qt.QHBoxLayout()
        title = Qt.QLabel("FM Receiver")
        font = Qt.QFont(theme.TOKENS['f_num'])
        font.setPixelSize(theme.TOKENS['s_xl'])
        font.setBold(True)
        title.setFont(font)
        row.addWidget(title)
        row.addSpacing(16)
        row.addWidget(Qt.QLabel("Radio:"))
        self.radio_combo = Qt.QComboBox()
        for kind in RADIO_ORDER:
            self.radio_combo.addItem(RADIO_NAMES[kind], kind)
        self.radio_combo.activated.connect(self._radio_chosen)
        row.addWidget(self.radio_combo)
        self.usrp_edit = Qt.QLineEdit(self.cfg['usrp_address'])
        self.usrp_edit.setPlaceholderText("IP address (blank: first found)")
        self.usrp_edit.setMaximumWidth(220)
        self.usrp_edit.editingFinished.connect(self._usrp_address_changed)
        row.addWidget(self.usrp_edit)
        # An RTL-SDR on another computer is for those who ask for it: the
        # box shows only when the app is started with --rtl-address.
        # Without it the RTL-SDR is this computer's. Not saved: the flag
        # says where, each run.
        self._rtl_network = self.args.rtl_address is not None
        self.rtl_edit = Qt.QLineEdit(self.args.rtl_address or '')
        self.rtl_edit.setPlaceholderText("ssh host[:port] (blank: this computer)")
        self.rtl_edit.setToolTip(
            "Where the RTL-SDR is plugged in. Blank: this computer. Otherwise an "
            "ssh host (as in ~/.ssh/config): rtl_tcp is started there over ssh "
            "and the samples come over the network. If rtl_tcp is already "
            "listening on the port (1234 unless given), it is used as it is.")
        self.rtl_edit.setMaximumWidth(260)
        self.rtl_edit.editingFinished.connect(self._rtl_address_changed)
        self.rtl_edit.setVisible(False)
        row.addWidget(self.rtl_edit)
        self.file_btn = Qt.QPushButton("Open IQ file...")
        self.file_btn.clicked.connect(self._choose_file)
        row.addWidget(self.file_btn)
        self.run_btn = Qt.QPushButton("Stop")
        self.run_btn.setObjectName('run')
        self.run_btn.setToolTip("Stop streaming and let go of the radio, or start again.")
        self.run_btn.clicked.connect(self._run_clicked)
        row.addWidget(self.run_btn)
        row.addSpacing(12)
        self.status = Qt.QLabel("")
        self.status.setTextFormat(QtCore.Qt.RichText)
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        # The theme picker, as the RF bench toolkit's launcher has it: the
        # word, then the disc, held close as one control.
        picker = Qt.QHBoxLayout()
        picker.setSpacing(4)
        self.theme_word = Qt.QLabel("Themes")
        picker.addWidget(self.theme_word, 0, QtCore.Qt.AlignVCenter)
        self.theme_disc = ThemeDisc()
        self.theme_disc.clicked.connect(self._next_theme)
        picker.addWidget(self.theme_disc)
        self.theme_word.setToolTip(self.theme_disc.toolTip())
        row.addLayout(picker)
        return row

    # ---------------------------------------------------------------- left
    def _build_left(self):
        panel = Qt.QWidget()
        box = Qt.QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 6, 0)
        box.setSpacing(8)
        self.tabs = PageTabs()
        self.tabs.addTab(self._build_sweep_tab(), "Sweep (FFT)")
        self.tabs.addTab(self._build_receive_tab(), "Receive (IQ)")
        self.tabs.addTab(self._build_recordings_tab(), "Recordings")
        self.tabs.setCurrentIndex(TAB_MODES.index(self.cfg['mode'])
                                  if self.cfg['mode'] in TAB_MODES else 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        box.addWidget(self.tabs)
        self.gain_box = self._build_gain()
        self.audio_box = self._build_audio()
        self.record_box = self._build_record()
        box.addWidget(self.gain_box)
        box.addWidget(self.audio_box)
        box.addWidget(self.record_box)
        box.addStretch(1)
        self._left_box = box
        self._place_side_boxes()
        scroll = Qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(Qt.QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.left_panel, self.left_scroll = panel, scroll
        self._fit_left()
        return scroll

    def _fit_left(self):
        """Make the left column wide enough for everything in it: it only
        scrolls up and down, so anything wider is cut off. Measured again
        once the window is shown, when the stylesheet has sized the controls."""
        # Measured open: a card folded now may be opened later.
        folded = [c for c in getattr(self, '_cards', {}).values() if c.is_folded()]
        for card in folded:
            card.set_folded(False)
        self.left_panel.adjustSize()
        bar = self.left_scroll.verticalScrollBar().sizeHint().width()
        self._left_width = self.left_panel.sizeHint().width() + bar + 4
        for card in folded:
            card.set_folded(True)
        self.left_scroll.setMinimumWidth(self._left_width)

    def _refit_left(self):
        """The theme changed, and its faces with it: size the left column to
        its controls again - Walnut's wider type left them cut off under the
        spectrum. A column dragged wider than it needs stays as it is."""
        old = self._left_width
        self._fit_left()
        left, right = self.main_split.sizes()
        if left < self._left_width or left == old:
            self.main_split.setSizes([self._left_width, left + right - self._left_width])

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_fitted', False):
            self._fitted = True
            self._fit_left()
            if not self.cfg['splitters'].get('main'):
                self.main_split.setSizes([self._left_width,
                                          max(600, self.width() - self._left_width)])

    def _build_sweep_tab(self):
        """Two boxes, as in Receive: the sweep, and the tuner on it."""
        page = Qt.QWidget()
        outer = Qt.QVBoxLayout(page)
        outer.setContentsMargins(6, 8, 6, 6)
        outer.setSpacing(10)
        sweep_card = Card("Sweep")
        form = self._form(sweep_card)
        self.sweep_form = form
        self.preset_combo = Qt.QComboBox()
        for name, _, _ in SWEEP_PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.activated.connect(self._preset_chosen)
        form.addRow("Band:", self.preset_combo)
        # The bounds, as the Tuner's digits, to the kHz: 9 kHz is 0000.009.
        self.sweep_start = DigitEntry('MHz', 1e6, 4, 3, minimum_hz=1e3,
                                      value_hz=self.cfg['sweep_start_mhz'] * 1e6,
                                      pixel_size=18, bold=False, default_place=3,
                                      caption='Sweep start')
        self.sweep_stop = DigitEntry('MHz', 1e6, 4, 3, minimum_hz=1e3,
                                     value_hz=self.cfg['sweep_stop_mhz'] * 1e6,
                                     pixel_size=18, bold=False, default_place=3,
                                     caption='Sweep stop')
        for entry, which in ((self.sweep_start, 'lowest'), (self.sweep_stop, 'highest')):
            entry.setToolTip(f"The {which} frequency swept, within the radio's range.\n"
                             "Hover a digit and roll the wheel, or type a frequency.")
            entry.valueChanged.connect(self._sweep_bounds_edited)
        form.addRow("Start:", self.sweep_start)
        form.addRow("Stop:", self.sweep_stop)
        # A turn of the wheel re-plans once the digits rest, not every notch.
        self._bounds_timer = Qt.QTimer(self)
        self._bounds_timer.setSingleShot(True)
        self._bounds_timer.setInterval(150)
        self._bounds_timer.timeout.connect(self._update_sweep_plan)
        self.rbw_combo = Qt.QComboBox()
        self.rbw_combo.addItem("Auto", 0.0)
        for rbw in RBW_LADDER:
            self.rbw_combo.addItem(f"{rbw / 1e3:g} kHz" if rbw < 1e6 else "1 MHz", float(rbw))
        saved = float(self.cfg['sweep_rbw_khz']) * 1e3
        self.rbw_combo.setCurrentIndex(max(0, self.rbw_combo.findData(saved)))
        self.rbw_combo.setToolTip(
            "Resolution bandwidth of the radio's own sweep. Auto keeps a sweep\n"
            "near 80,000 points; narrower shows more detail and a lower noise\n"
            "floor. Too narrow for the span is raised, to 1.5 million points.")
        self.rbw_combo.activated.connect(lambda _: self._update_sweep_plan())
        form.addRow("RBW:", self.rbw_combo)
        # The tuner, here as well as in Receive: the marker on the spectrum,
        # what Listen tunes to, and what Real time watches around. Its own
        # box, built here and placed under the sweep's.
        tuner_card = Card("Tuner")
        tuner_form = self._form(tuner_card)
        tune = Qt.QHBoxLayout()
        tune.setSpacing(6)
        self.sweep_tuner = DigitEntry('MHz', 1e6, 4, 3,
                                      value_hz=self.cfg['frequency_mhz'] * 1e6,
                                      pixel_size=20, default_place=2, caption='Tuner')
        self.sweep_tuner.setToolTip(
            "Where the receiver will tune - the marker on the sweep, and the\n"
            "same tuner Receive shows. Hover a digit and roll the wheel, or\n"
            "type a frequency; a click on the spectrum moves it too. Real time\n"
            "watches the band around it.")
        self.sweep_tuner.valueChanged.connect(self.tune)
        self.sweep_roller = StepRoller(height=self.sweep_tuner.sizeHint().height())
        self.sweep_roller.setToolTip("Move the tuner down or up by the Step: click "
                                     "(hold to repeat), or roll the wheel over it.")
        self.sweep_roller.stepped.connect(self._step)
        tune.addWidget(self.sweep_tuner, 0, QtCore.Qt.AlignVCenter)
        tune.addWidget(self.sweep_roller, 0, QtCore.Qt.AlignVCenter)
        tuner_form.addRow("Tuner:", tune)
        self.rt_btn = Qt.QPushButton("Real time")
        self.rt_btn.setCheckable(True)
        self.rt_btn.setEnabled(bb60_sweep.REALTIME_OK)
        self.rt_btn.setToolTip(
            f"Watch {RT_MAX_SPAN_HZ / 1e6:g} MHz around the tuner in real time instead of\n"
            "sweeping: every sample is FFT'd, so nothing is missed - a burst of\n"
            "307 us or more at 10 kHz RBW - and a density map behind the trace\n"
            "shows how often each level was hit. 30 frames a second; the FM band\n"
            "fits. Its RBW runs from 2.47 to 631 kHz (Auto: 10 kHz). Pressing it\n"
            "drops the sweep to that window; letting it out gives back the span\n"
            "that was there. No listening meanwhile: the radio does one thing at\n"
            "a time." if bb60_sweep.REALTIME_OK else
            "Not on a Mac: Signal Hound's library for it sweeps and streams IQ,\n"
            "but has no real time.")
        # Hidden unless asked for (--realtime): Receive at the BB60D's widest
        # IQ bandwidth shows as much, as often, and plays the station too.
        self.rt_btn.setChecked(bool(self.cfg['sweep_realtime']) and bb60_sweep.REALTIME_OK
                               and self.args.realtime)
        # Connected after the saved state is set: pressing it moves the
        # sweep's bounds, which the window is not built enough for yet.
        self.rt_btn.toggled.connect(self._realtime_toggled)
        form.addRow(self.rt_btn)
        self.rt_btn.setVisible(False)            # until a radio that has it
        self.sweep_rate_combo = Qt.QComboBox()
        self.sweep_rate_combo.setToolTip(
            "The radio's sample rate while sweeping: how much spectrum each "
            "step sees. Wider means fewer steps.")
        self.sweep_rate_combo.activated.connect(lambda _: self._restart_sweep())
        form.addRow("Step bandwidth:", self.sweep_rate_combo)
        self.fft_combo = Qt.QComboBox()
        for n in FFT_SIZES:
            self.fft_combo.addItem(f"{n} bins", n)
        self.fft_combo.setCurrentIndex(max(0, FFT_SIZES.index(self.cfg['sweep_fft'])
                                           if self.cfg['sweep_fft'] in FFT_SIZES else 2))
        self.fft_combo.setToolTip("FFT size: sets the resolution bandwidth (RBW).")
        self.fft_combo.activated.connect(lambda _: self._update_sweep_plan())
        form.addRow("FFT:", self.fft_combo)
        self.frames_spin = Qt.QSpinBox()
        self.frames_spin.setRange(1, 128)
        self.frames_spin.setValue(int(self.cfg['sweep_frames']))
        self.frames_spin.setToolTip("FFT frames averaged at each step.")
        self.frames_spin.valueChanged.connect(lambda _: self._update_sweep_plan())
        form.addRow("Frames per step:", self.frames_spin)
        self.settle_spin = Qt.QDoubleSpinBox()
        self.settle_spin.setRange(0, 500)
        self.settle_spin.setDecimals(1)
        self.settle_spin.setSuffix(" ms")
        self.settle_spin.setToolTip(
            "Samples skipped after each retune, on top of what is still queued.\n"
            "Too little shows ghosts of the previous step's signals.")
        self.settle_spin.valueChanged.connect(self._settle_changed)
        form.addRow("Settle:", self.settle_spin)
        # Rows only one kind of sweep has: the radio's own, or LO hopping.
        self._native_rows = (self.rbw_combo,)
        self._hop_rows = (self.sweep_rate_combo, self.fft_combo, self.frames_spin,
                          self.settle_spin)
        self.pause_btn = Qt.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._pause_toggled)
        form.addRow(self.pause_btn)
        self.listen_btn = Qt.QPushButton("Listen")
        self.listen_btn.setToolTip("Receive the selected station (or the marker).")
        self.listen_btn.clicked.connect(self._listen_selected)
        tune.addWidget(self.listen_btn, 1, QtCore.Qt.AlignVCenter)   # beside it
        # What the sweep is doing: at the foot of the tab, under RF gain.
        self.sweep_info = _wrapping(Qt.QLabel(""))
        self.sweep_info.setContentsMargins(4, 0, 4, 0)
        self.snr_spin = Qt.QSpinBox()
        self.snr_spin.setRange(3, 60)
        self.snr_spin.setSuffix(" dB")
        self.snr_spin.setValue(int(self.cfg['min_snr_db']))
        self.snr_spin.setToolTip("How far above the noise floor a channel must "
                                 "stand to be listed as a station.")
        self.snr_spin.valueChanged.connect(lambda _: self._force_station_list())
        form.addRow("Station threshold:", self.snr_spin)
        # With no radio yet, only the saved band says whether it is the full
        # range; the radio's own range comes with it (_load_radio_settings).
        if self.cfg['sweep_band'] == 'full':
            self.preset_combo.setCurrentIndex(0)
        else:
            self._select_preset(remember=False)
        # The stations found, under the sweep that found them.
        stations = Qt.QGroupBox("Stations found (double-click to listen)")
        sbox = Qt.QVBoxLayout(stations)
        self.station_list = Qt.QListWidget()
        self.station_list.setFont(_mono_font())
        self.station_list.setMinimumHeight(160)
        self.station_list.itemDoubleClicked.connect(self._station_activated)
        self.station_list.currentItemChanged.connect(self._station_selected)
        sbox.addWidget(self.station_list)
        outer.addWidget(sweep_card)
        outer.addWidget(stations)
        outer.addWidget(tuner_card)
        outer.addWidget(self.sweep_info)
        outer.addStretch(1)
        self._sweep_outer = outer       # RF gain joins it in Sweep
        return page

    def _build_receive_tab(self):
        """Three boxes, top to bottom: the radio itself, the tuner inside its
        band, and the station as decoded."""
        page = Qt.QWidget()
        box = Qt.QVBoxLayout(page)
        box.setContentsMargins(6, 8, 6, 6)
        box.setSpacing(10)
        box.addWidget(self._build_radio_card())
        box.addWidget(self._build_tuner_card())
        box.addWidget(self._build_rds_card())
        box.addStretch(1)
        return page

    def _foldable(self, card, form, name, keep=(), also=(), center=False):
        """A Receive card with a chevron, folded as it was left."""
        self._cards = getattr(self, '_cards', {})
        self._cards[name] = card
        card.foldable(form, keep, also, folded=bool(self.cfg['folded'].get(name)),
                      center=center)
        card.folded.connect(lambda folded: self.cfg['folded'].__setitem__(name, folded))
        return card

    @staticmethod
    def _form(box):
        form = Form(box)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        return form

    def _build_radio_card(self):
        """The radio itself: where it is tuned and how much it takes in. The
        tuner moves inside that band."""
        box = Card("Radio")
        form = self._form(box)
        row = Qt.QHBoxLayout()
        row.setSpacing(6)
        centre = self.cfg.get('center_mhz') or (self.cfg['frequency_mhz'] - 0.3)
        self.center_entry = DigitEntry('MHz', 1e6, 4, 3, value_hz=centre * 1e6,
                                       pixel_size=20, default_place=3, caption='Center')
        self.center_entry.setToolTip(
            "The radio's own centre frequency (its LO) - the dashed line.\n"
            "Moving it moves the band the tuner can reach. Hover a digit and roll\n"
            "the wheel, or type a frequency.")
        self.center_entry.valueChanged.connect(self._center_edited)
        row.addWidget(self.center_entry)
        self.recenter_btn = Qt.QPushButton("Center on tuner")
        self.recenter_btn.setObjectName('small')
        self.recenter_btn.setToolTip("Put the radio's centre just below the tuner, so "
                                     "there is room to tune either way.")
        self.recenter_btn.clicked.connect(self._center_on_tuner)
        row.addWidget(self.recenter_btn)
        row.addStretch(1)
        form.addRow("Center:", row)
        self.rx_rate_combo = Qt.QComboBox()
        self.rx_rate_combo.setToolTip(
            "The radio's IQ bandwidth (sample rate) while receiving: how much of "
            "the band\nthe spectrum shows, and the tuner can reach, around the "
            "Center.")
        self.rx_rate_combo.activated.connect(lambda _: (self._update_folder_tip(),
                                                        self._restart_receive()))
        form.addRow("IQ bandwidth:", self.rx_rate_combo)
        # RF gain's row comes here in Receive (:meth:`_place_side_boxes`).
        self.rx_gain_slot = Qt.QWidget()
        slot = Qt.QHBoxLayout(self.rx_gain_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        form.addRow("RF gain:", self.rx_gain_slot)
        return self._foldable(box, form, 'radio', keep=(self.center_entry,), center=True)

    def _build_tuner_card(self):
        """The station you hear, its step, and its channel filter."""
        box = Card("Tuner")
        form = self._form(box)
        # The Step knob makes the tuner's row tall: its label sits mid-row.
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        tune = Qt.QHBoxLayout()
        tune.setSpacing(6)
        self.tuner = DigitEntry('MHz', 1e6, 4, 3, value_hz=self.cfg['frequency_mhz'] * 1e6,
                                pixel_size=30, default_place=2, caption='Tuner')
        self.tuner.setToolTip(
            "The station you hear. Hover a digit and roll the wheel to change\n"
            "it (Up/Down do the same), or type a frequency. It stays inside\n"
            "the band around the radio's Center.")
        self.tuner.valueChanged.connect(self._tuner_edited)
        self.roller = StepRoller(height=self.tuner.sizeHint().height())
        self.roller.setToolTip("Step down or up by the Step: click (hold to repeat), "
                               "or roll the wheel over it.\nCtrl+Left / Ctrl+Right too.")
        self.roller.stepped.connect(self._step)
        saved = self.cfg['step_khz'] if self.cfg['step_khz'] in STEPS_KHZ else 100
        self.step_knob = Knob('Step', 0, len(STEPS_KHZ) - 1, STEPS_KHZ.index(saved),
                              lambda i: f"{STEPS_KHZ[int(round(i))]} kHz", step=1, wheel=1,
                              tooltip="What the roller and Ctrl+Left/Right move by, and "
                              "what Snap rounds to:\n10, 50, 100 or 200 kHz. 200 kHz is "
                              "the Americas' FM raster, on the odd tenths\n(88.1, 88.3 "
                              "... 107.9); 100 kHz is Europe's.")
        self.step_knob.valueChanged.connect(lambda _: self._snap_toggled(
            self.snap_check.isChecked()))
        # Right on top of the tuner, its range, as wide as the digits and
        # centred over them; as much room under them keeps the tuner level
        # with its label, its roller and the knob.
        self.range_label = _wrapping(Qt.QLabel("-"))
        self.range_label.setTextFormat(QtCore.Qt.RichText)
        self.range_label.setAlignment(QtCore.Qt.AlignHCenter)
        column = Qt.QVBoxLayout()
        column.setSpacing(2)
        column.addStretch(1)
        column.addWidget(self.range_label)
        column.addWidget(self.tuner)
        column.addSpacing(self.range_label.sizeHint().height() + column.spacing())
        column.addStretch(1)
        tune.addLayout(column)
        tune.addWidget(self.roller, 0, QtCore.Qt.AlignVCenter)
        tune.addWidget(self.step_knob)
        tune.addStretch(1)
        form.addRow("Tuner:", tune)
        chan = Qt.QHBoxLayout()
        chan.setSpacing(6)
        self.chan_entry = DigitEntry('kHz', 1e3, 3, 0, minimum_hz=CHANNEL_MIN_HZ,
                                     maximum_hz=CHANNEL_MAX_HZ,
                                     value_hz=float(self.cfg['channel_bw_khz']) * 1e3,
                                     pixel_size=18, bold=False, default_place=0,
                                     caption='Channel filter')
        self.chan_entry.setToolTip(
            "Channel filter bandwidth, 60-400 kHz. Narrower rejects a strong\n"
            "neighbour; below ~180 kHz stereo and RDS start to suffer, and past\n"
            "~250 kHz the audio takes in any neighbour that close (400 kHz holds\n"
            "an HD Radio station's digital sidebands, for the channel recording).\n"
            "Hover a digit and roll the wheel, use the arrows, or roll the wheel\n"
            "over the orange band on the spectrum.")
        self.chan_entry.valueChanged.connect(self._chan_bw_changed)
        self.chan_roller = StepRoller(height=self.chan_entry.sizeHint().height())
        self.chan_roller.setToolTip(f"Channel filter {CHANNEL_STEP_HZ / 1e3:.0f} kHz "
                                    "wider or narrower: click, hold, or roll the wheel.")
        self.chan_roller.stepped.connect(lambda steps: self.chan_entry.setValue(
            self.chan_entry.value() + steps * CHANNEL_STEP_HZ, emit=True))
        chan.addWidget(self.chan_entry)
        chan.addWidget(self.chan_roller)
        chan.addStretch(1)
        form.addRow("Channel filter:", chan)
        return self._foldable(box, form, 'tuner', keep=(self.tuner,),
                              also=(self.step_knob,), center=True)

    def _build_rds_card(self):
        """The station as decoded: its name, how it is decoded, how well it
        is received, and the RDS in full."""
        box = Card("RDS")
        form = self._form(box)
        big = Qt.QFont()
        big.setPixelSize(19)
        big.setBold(True)
        mono = _mono_font(15)
        self.lbl = {}

        def value(key, font=None):
            label = _wrapping(Qt.QLabel("-"))
            if font is not None:
                label.setFont(font)
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.lbl[key] = label
            return label

        form.addRow("Station:", value('station_name', big))
        self.region_combo = Qt.QComboBox()
        self.region_combo.addItem("RBDS / 75 us (Americas)", 'RBDS')
        self.region_combo.addItem("RDS / 50 us (Europe, rest)", 'RDS')
        self.region_combo.setCurrentIndex(1 if self.cfg['region'] == 'RDS' else 0)
        self.region_combo.activated.connect(self._region_changed)
        form.addRow("Standard:", self.region_combo)
        opts = Qt.QHBoxLayout()
        self.stereo_check = Qt.QCheckBox("Stereo")
        self.stereo_check.setChecked(bool(self.cfg['stereo']))
        self.stereo_check.toggled.connect(self._stereo_toggled)
        opts.addWidget(self.stereo_check)
        self.snap_check = Qt.QCheckBox("Snap to step")
        self.snap_check.setToolTip("Clicks and middle-drags on the spectrum tune to "
                                   "the nearest multiple of the Step.")
        self.snap_check.setChecked(bool(self.cfg['snap']))
        self.snap_check.toggled.connect(self._snap_toggled)
        opts.addWidget(self.snap_check)
        form.addRow(opts)
        self.clear_btn = Qt.QPushButton("Clear RDS")
        self.clear_btn.clicked.connect(self._clear_rds)
        form.addRow(self.clear_btn)
        form.addRow(_hline())
        self.sig_label = _wrapping(Qt.QLabel("-"))
        self.sig_label.setTextFormat(QtCore.Qt.RichText)
        form.addRow("Signal:", self.sig_label)
        self.stereo_label = _wrapping(Qt.QLabel("-"))
        self.stereo_label.setTextFormat(QtCore.Qt.RichText)
        form.addRow("Audio:", self.stereo_label)
        form.addRow(_hline())
        for key, caption, font in (('pi', "Station ID (PI):", None),
                                   ('pty', "Program type:", None),
                                   ('ps', "Now showing (PS):", mono),
                                   ('nowplaying', "Now playing:", big),
                                   ('radiotext', "RadioText:", mono),
                                   ('flags', "Flags:", None),
                                   ('clock', "Station clock:", None),
                                   ('quality', "Decode quality:", None)):
            form.addRow(caption, value(key, font))
        return self._foldable(box, form, 'rds',
                              keep=(self.lbl['nowplaying'], self.lbl['radiotext']))

    def _build_recordings_tab(self):
        """The recordings in the folder, newest first, and the player."""
        page = Qt.QWidget()
        box = Qt.QVBoxLayout(page)
        box.setContentsMargins(6, 8, 6, 6)
        box.setSpacing(8)
        self.rec_list = Qt.QListWidget()
        self.rec_list.setMinimumHeight(180)
        self.rec_list.setWordWrap(True)
        self.rec_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.rec_list.setToolTip("One line for each press of Record, newest first. "
                                 "Double-click to play.")
        self.rec_list.currentItemChanged.connect(self._recording_selected)
        self.rec_list.itemDoubleClicked.connect(lambda _item: self.play_btn.setChecked(True))
        box.addWidget(self.rec_list, 1)
        self.lib_note = _wrapping(Qt.QLabel(""))
        self.lib_note.setTextFormat(QtCore.Qt.RichText)
        box.addWidget(self.lib_note)

        player = Qt.QGroupBox("Player")
        form = self._form(player)
        self.track_combo = Qt.QComboBox()
        self.track_combo.setToolTip(
            "Which of the recording's files to play. The IQ is the radio signal: "
            "the spectrum,\nwaterfall and RDS come from it, and the sound is made "
            "from it again. The WAV\nis the sound as it was heard.")
        self.track_combo.activated.connect(self._track_chosen)
        form.addRow("Play:", self.track_combo)
        row = Qt.QHBoxLayout()
        self.play_btn = Qt.QPushButton("Play")
        self.play_btn.setCheckable(True)
        self.play_btn.setEnabled(False)
        self.play_btn.toggled.connect(self._play_toggled)
        row.addWidget(self.play_btn)
        self.loop_check = Qt.QCheckBox("Loop")
        self.loop_check.setToolTip("At the end, start again from the beginning "
                                   "rather than stop.")
        self.loop_check.setChecked(bool(self.cfg['play_loop']))
        self.loop_check.toggled.connect(self._loop_toggled)
        row.addWidget(self.loop_check)
        row.addStretch(1)
        self.time_label = Qt.QLabel("0:00 / 0:00")
        self.time_label.setFont(_mono_font())
        row.addWidget(self.time_label)
        form.addRow(row)
        self.timeline = TimelineStrip()
        self.timeline.seekRequested.connect(self._seek_to)
        self.timeline.setToolTip(
            "The whole recording: time left to right, frequency upwards, in\n"
            "the colours of the spectrum's waterfall: its Ref level and Range\n"
            "set them, as they do the waterfall's. Click or drag to jump.")
        form.addRow(self.timeline)
        big = Qt.QFont()
        big.setPixelSize(17)
        big.setBold(True)
        self.play_station = _wrapping(Qt.QLabel("-"))
        self.play_station.setFont(big)
        form.addRow("Station:", self.play_station)
        self.play_text = _wrapping(Qt.QLabel("-"))
        self.play_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow("RDS:", self.play_text)
        box.addWidget(player)

        files = Qt.QHBoxLayout()
        self.lib_folder_btn = Qt.QPushButton("Folder...")
        self.lib_folder_btn.setToolTip("Choose the recordings folder (Record saves there too).")
        self.lib_folder_btn.clicked.connect(self._choose_folder)
        self.reveal_btn = Qt.QPushButton("Show in folder")
        self.reveal_btn.clicked.connect(self._reveal_folder)
        self.delete_btn = Qt.QPushButton("Delete...")
        self.delete_btn.setToolTip("Delete every file of the chosen recording.")
        self.delete_btn.clicked.connect(self._delete_clicked)
        for button in (self.lib_folder_btn, self.reveal_btn, self.delete_btn):
            files.addWidget(button)
        files.addStretch(1)
        box.addLayout(files)

        # Record, or a file manager, changes the folder: list it again.
        self._lib_timer = Qt.QTimer(self)
        self._lib_timer.setSingleShot(True)
        self._lib_timer.setInterval(400)
        self._lib_timer.timeout.connect(
            lambda: self._live is not None and self._refresh_library())
        self._lib_watch = Qt.QFileSystemWatcher(self)
        self._lib_watch.directoryChanged.connect(lambda _path: self._lib_timer.start())
        return page

    def _build_gain(self):
        """The RF gain row, in its own box: the box sits in Sweep's tab or
        under the tabs, and in Receive the row leaves it for a row of the
        Radio card (:meth:`_place_side_boxes`)."""
        box = Qt.QGroupBox("RF gain")
        self._gain_box_layout = Qt.QVBoxLayout(box)
        self.gain_row = Qt.QWidget()
        row = Qt.QHBoxLayout(self.gain_row)
        row.setContentsMargins(0, 0, 0, 0)
        self._gain_box_layout.addWidget(self.gain_row)
        # FM receiver: AGC - the BB60D's own in its sweep, and the window's
        # on the IQ stream of any radio that says when it overloads.
        self.agc_box = Qt.QCheckBox("AGC")
        self.agc_box.toggled.connect(self._agc_toggled)
        self.agc_box.setVisible(False)
        row.addWidget(self.agc_box)
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.valueChanged.connect(self._gain_changed)
        row.addWidget(self.gain_slider, 1)
        self.gain_label = Qt.QLabel("")
        self.gain_label.setMinimumWidth(40)
        row.addWidget(self.gain_label)
        return box

    def _build_audio(self):
        box = Qt.QGroupBox("Audio")
        grid = Qt.QGridLayout(box)
        self.mute_btn = Qt.QPushButton("Mute")
        self.mute_btn.setObjectName('mute')
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Mute the speaker (Ctrl+M). Recording carries on.")
        self.mute_btn.toggled.connect(self._mute_toggled)
        self.mute_btn.setChecked(bool(self.cfg['muted']) or self._flag_muted)
        grid.addWidget(self.mute_btn, 0, 0, 2, 1)
        self.volume_knob = Knob('Volume', 0, 100, float(self.cfg['volume']),
                                lambda v: f"{v:.0f}%", step=1, wheel=2,
                                tooltip='Speaker volume (Ctrl+Up / Ctrl+Down).')
        self.volume_knob.valueChanged.connect(self._volume_changed)
        grid.addWidget(self.volume_knob, 0, 1, 2, 1)
        self.meter = LevelMeter()
        grid.addWidget(self.meter, 0, 2)
        self.audio_note = _wrapping(Qt.QLabel(""))
        grid.addWidget(self.audio_note, 1, 2)
        grid.setColumnStretch(2, 1)
        return box

    def _build_record(self):
        box = Qt.QGroupBox("Record")
        grid = Qt.QGridLayout(box)
        self.rec_audio = Qt.QCheckBox("Audio (WAV, 48 kHz stereo)")
        self.rec_audio.setChecked(bool(self.cfg['record_audio']))
        self.rec_channel = Qt.QCheckBox("IQ - channel (500 kS/s, 4 MB/s)")
        self.rec_channel.setChecked(bool(self.cfg['record_iq_channel']))
        self.rec_band = Qt.QCheckBox("IQ - whole band")
        self.rec_band.setChecked(bool(self.cfg['record_iq_band']))
        self.rec_band.setToolTip("Everything the radio receives at its IQ bandwidth. "
                                 "Large: 8 bytes per sample.")
        grid.addWidget(self.rec_audio, 0, 0, 1, 2)
        grid.addWidget(self.rec_channel, 1, 0, 1, 2)
        grid.addWidget(self.rec_band, 2, 0, 1, 2)
        self.rec_btn = Qt.QPushButton("Record")
        self.rec_btn.setCheckable(True)
        self.rec_btn.setToolTip("Start or stop recording what is ticked (Ctrl+R).")
        self.rec_btn.toggled.connect(self._record_toggled)
        grid.addWidget(self.rec_btn, 3, 0)
        self.folder_btn = Qt.QPushButton("Folder...")
        self.folder_btn.clicked.connect(self._choose_folder)
        grid.addWidget(self.folder_btn, 3, 1)
        self.rec_label = _wrapping(Qt.QLabel(""))
        self.rec_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        grid.addWidget(self.rec_label, 4, 0, 1, 2)
        self._update_folder_tip()
        return box

    # --------------------------------------------------------------- right
    def _build_right(self):
        self.right_split = Qt.QSplitter(QtCore.Qt.Vertical)
        # On top the RF spectrum, or a WAV's sound as it plays.
        self.top_stack = Qt.QStackedWidget()
        self.rf_view = SpectrumView("RF spectrum", unit='MHz', waterfall=True,
                                    min_span_hz=50e3, tuner_menu=True,
                                    snap_hz=self._step_hz() if self.cfg['snap'] else None)
        self.rf_view.clicked.connect(self._rf_clicked)
        self.rf_view.tunerRequested.connect(self._rf_clicked)
        self.rf_view.activated.connect(self._rf_activated)
        self.rf_view.bandWheel.connect(self._band_wheel)
        self.rf_view.bandDragged.connect(self._band_dragged)
        self.rf_view.bandDragFinished.connect(self._band_drag_finished)
        self.rf_view.averageChanged.connect(self._rf_average_changed)
        # In real time the density map spans the view's scale: the device
        # makes it from the Ref level down the Range.
        for knob in (self.rf_view.ref_knob, self.rf_view.range_knob):
            knob.valueChanged.connect(self._view_scale_changed)
        self.top_stack.addWidget(self.rf_view)
        self.audio_view = SpectrumView("Audio - left and right together", unit='kHz',
                                       waterfall=True, min_span_hz=1e3)
        self.audio_view.averageChanged.connect(self._audio_average_changed)
        self.audio_view.load_state(self.cfg['view_audio'])
        self.top_stack.addWidget(self.audio_view)
        # The Recordings strip is coloured by the view that shows its track.
        for view in (self.rf_view, self.audio_view):
            for knob in (view.ref_knob, view.range_knob):
                knob.valueChanged.connect(lambda _v: self._timeline_levels())
        self.right_split.addWidget(self.top_stack)

        # Under the RF spectrum in Receive: the multiplex (RDS is in the
        # Receive tab). Sweeping, it is hidden and the spectrum has the
        # height: the stations found are in the Sweep tab.
        self.bottom = Qt.QTabWidget()
        self.bottom.setTabBarAutoHide(True)
        self.bottom.setDocumentMode(True)
        self.mpx_view = SpectrumView(
            "FM multiplex - mono, pilot 19k, stereo 38k, RDS 57k", unit='kHz',
            waterfall=False, min_span_hz=5e3)
        self.mpx_view.averageChanged.connect(self._mpx_average_changed)
        self.rx_page = self.mpx_view
        self.bottom.addTab(self.mpx_view, "Multiplex")
        self.right_split.addWidget(self.bottom)
        self.right_split.setStretchFactor(0, 3)
        self.right_split.setStretchFactor(1, 2)
        self.rf_view.load_state(self.cfg['view_receive'])
        self.mpx_view.load_state(self.cfg['view_mpx'])
        self.mpx_view.set_extent(0, MPX_RATE / 2, keep_span=False)
        return self.right_split

    def _shortcuts(self):
        def add(keys, action):
            sc = Qt.QShortcut(Qt.QKeySequence(keys), self)
            sc.setContext(QtCore.Qt.WindowShortcut)
            sc.activated.connect(action)
        add("Ctrl+M", self.mute_btn.toggle)
        add("Ctrl+R", self.rec_btn.toggle)
        add("Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        add("Ctrl+2", lambda: self.tabs.setCurrentIndex(1))
        add("Ctrl+3", lambda: self.tabs.setCurrentIndex(2))
        add("Ctrl+Left", lambda: self._step(-1))
        add("Ctrl+Right", lambda: self._step(1))
        add("Ctrl+Up", lambda: self.volume_knob.setValue(self.volume_knob.value() + 5))
        add("Ctrl+Down", lambda: self.volume_knob.setValue(self.volume_knob.value() - 5))
        # Plain A: typed into a digit entry or a text box it is a letter
        # there instead - Qt gives a key a text field takes to the field.
        add("A", self._auto_scale)

    def _auto_scale(self):
        """Fit the Ref level and Range to the spectrum on show - the RF
        spectrum, or a WAV's sound. Under AGC the Ref level is the radio's
        reference, so only the Range moves."""
        view = self.top_stack.currentWidget()
        if not view.auto_scale(keep_ref=view is self.rf_view and self._agc_on()):
            self._set_status("Auto scale: no spectrum on show yet.")

    # ============================================================ startup
    def start_initial(self):
        """Open the radio and start in the saved mode - run once, just after
        the window is first shown, so it appears before the radio opens."""
        kind = self.args.radio or self.cfg.get('radio')
        if self.args.file:
            kind = 'file'
            self.cfg['iq_file'] = self.args.file
        if not kind:
            found = detect_radios()
            kind = found[0] if found else 'hackrf'
        if self.args.freq:
            self._set_tuner(self.args.freq * 1e6)
        if self.args.sweep:
            self._set_sweep_span(*(mhz * 1e6 for mhz in self.args.sweep))
        mode = self.args.mode or ('receive' if self.args.file else None)
        if mode:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(TAB_MODES.index(mode))
            self.tabs.blockSignals(False)
            self._place_side_boxes()
        if self._tab_mode() == 'recordings':
            # The radio waits, closed, for Sweep or Receive.
            self.radio_combo.setCurrentIndex(max(0, self.radio_combo.findData(kind)))
            self.usrp_edit.setVisible(kind == 'usrp')
            self.rtl_edit.setVisible(kind == 'rtlsdr' and self._rtl_network)
            self.file_btn.setVisible(kind == 'file')
            self._enter_recordings()
            return
        self._use_radio(kind)

    # ============================================================== radio
    def _set_status(self, text, token=None):
        self.status.setText(_coloured(text, token) if token else text)

    def _use_radio(self, kind):
        """Stop, open the radio of ``kind`` and start in the current mode."""
        self._lost = None
        self._iq_agc = None
        self._stop_recording("the radio changed")
        index = self.radio_combo.findData(kind)
        self.radio_combo.setCurrentIndex(max(0, index))
        self.usrp_edit.setVisible(kind == 'usrp')
        self.rtl_edit.setVisible(kind == 'rtlsdr' and self._rtl_network)
        self.file_btn.setVisible(kind == 'file')
        self._remember_radio_settings()
        radio = make_radio(kind, self.usrp_edit.text(), self.cfg.get('iq_file', ''),
                           self.rtl_edit.text() if self._rtl_network else '')
        self._set_status(f"Opening {radio.describe()}...")
        Qt.QApplication.processEvents()
        try:
            self.engine.use_radio(radio)
        except RadioError as exc:
            self.radio = None
            self._show_error(str(exc))
            return
        except Exception as exc:
            self.radio = None
            self._show_error(f"Could not open {radio.describe()}: {exc}")
            return
        self.radio = radio
        self.cfg['radio'] = kind
        self._load_radio_settings()
        self._start_mode(self._tab_mode())
        self._opened_at = time.monotonic()

    def _show_error(self, text):
        first = text.split('\n')[0]
        self._set_status(first, 'bad')
        self.status.setToolTip(text)
        self.rf_view.clear()
        self.rf_view.set_message(first)
        self.run_btn.setText("Start")

    def _remember_radio_settings(self):
        """Keep the outgoing radio's gain and rates for next time."""
        if self.radio is None:
            return
        kind = self.radio.kind
        self.cfg['gain'][kind] = self.gain_slider.value()
        if self._agc_available():
            self.cfg['gain_auto'][kind] = self.agc_box.isChecked()
            if self._agc_ceiling is not None:
                self.cfg['agc_ceiling'][kind] = self._agc_ceiling
        if self.rx_rate_combo.count():
            self.cfg['receive_rate'][kind] = self.rx_rate_combo.currentData()
        if self.sweep_rate_combo.count():
            self.cfg['sweep_rate'][kind] = self.sweep_rate_combo.currentData()
        self.cfg['settle_ms'][kind] = self.settle_spin.value()

    def _load_radio_settings(self):
        radio = self.radio
        kind = radio.kind

        def fill(combo, rates, saved, default, unavailable=None):
            # Rates this computer can't use are listed, greyed out, with why.
            unavailable = unavailable or {}
            combo.blockSignals(True)
            combo.clear()
            for rate in rates:
                combo.addItem(rate_label(rate), float(rate))
                why = unavailable.get(rate)
                if why:
                    combo.model().item(combo.count() - 1).setEnabled(False)
                    combo.setItemData(combo.count() - 1, why, QtCore.Qt.ToolTipRole)
            usable = [r for r in rates if r not in unavailable]
            pick = saved if saved in usable else default
            if pick in usable:
                combo.setCurrentIndex(list(rates).index(pick))
            combo.blockSignals(False)

        fill(self.rx_rate_combo, radio.receive_rates,
             self.cfg['receive_rate'].get(kind), radio.default_receive_rate,
             radio.unavailable_rates)
        fill(self.sweep_rate_combo, radio.sweep_rates,
             self.cfg['sweep_rate'].get(kind), radio.default_sweep_rate)
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(self.cfg['gain'].get(kind, radio.default_gain)))
        self.gain_slider.blockSignals(False)
        radio.gain_percent = float(self.gain_slider.value())
        # Saved apart from the gain: AGC may have left the slider under it.
        self._agc_ceiling = float(self.cfg['agc_ceiling'].get(kind, self.gain_slider.value()))
        self.agc_box.blockSignals(True)
        self.agc_box.setChecked(bool(self._agc_available()
                                     and self.cfg['gain_auto'].get(kind, False)))
        self.agc_box.blockSignals(False)
        self._show_gain()
        self.settle_spin.blockSignals(True)
        self.settle_spin.setValue(float(self.cfg['settle_ms'].get(kind, radio.settle_ms)))
        self.settle_spin.blockSignals(False)
        low, high = radio.freq_range_hz
        self._tuner_range(low, high)
        self.center_entry.set_range(low, high)
        self._show_sweep_rows(radio.native_sweep)
        self.rt_btn.setVisible(radio.native_sweep and radio.has_realtime
                               and self.args.realtime)
        if self.cfg['sweep_band'] == 'full':
            self._set_sweep_span(*radio.sweep_range_hz, replan=False)
        else:
            self._limit_sweep_bounds()
            self._select_preset()
        if kind == 'file' and radio.meta and radio.meta.get('station_hz') \
                and radio.path != getattr(self, '_opened_file', None):
            # A new recording opens on the station it was made of; the same
            # one again (Stop, Start) stays where it was tuned.
            self._set_tuner(radio.meta['station_hz'])
        if kind == 'file':
            self._opened_file = radio.path
            self.center_entry.set_range(radio.center_hz, radio.center_hz)
        # A recording's centre is where it was made: nothing to move.
        self.center_entry.setEnabled(kind != 'file')
        self.recenter_btn.setEnabled(kind != 'file')
        self._update_folder_tip()
        self.tabs.setTabEnabled(0, radio.can_sweep)
        self.tabs.setTabToolTip(0, "" if radio.can_sweep else
                                "An IQ recording cannot sweep: its band is fixed.")
        if not radio.can_sweep and self.tabs.currentIndex() == 0:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(1)
            self.tabs.blockSignals(False)
            self._place_side_boxes()

    def _radio_chosen(self, index):
        kind = self.radio_combo.itemData(index)
        if kind == 'file' and not self.cfg.get('iq_file'):
            if not self._choose_file(start=False):
                if self.radio is not None:
                    self.radio_combo.setCurrentIndex(self.radio_combo.findData(self.radio.kind))
                return
        self._use_radio(kind)

    def _usrp_address_changed(self):
        if self.cfg.get('usrp_address') != self.usrp_edit.text().strip():
            self.cfg['usrp_address'] = self.usrp_edit.text().strip()
            if self.radio_combo.currentData() == 'usrp':
                self._use_radio('usrp')

    def _rtl_address_changed(self):
        if self.radio_combo.currentData() != 'rtlsdr':
            return
        radio = self.radio
        if (radio is None or radio.kind != 'rtlsdr'
                or radio.address != self.rtl_edit.text().strip()):
            self._use_radio('rtlsdr')

    def _choose_file(self, start=True):
        folder = (os.path.dirname(self.cfg.get('iq_file') or '')
                  or self._recording_dir())
        path, _ = Qt.QFileDialog.getOpenFileName(
            self, "Play an IQ recording", folder,
            "IQ recordings (*.sigmf-meta *.sigmf-data *.cfile *.json);;All files (*)")
        if not path:
            return False
        self.cfg['iq_file'] = path
        if start is not False:
            self._use_radio('file')
        return True

    def _run_clicked(self):
        if self.radio is not None:
            # Closed, not just stopped: a stopped HackRF source still holds
            # the device, and Stop is how another program gets it.
            self._lost = None
            self._stop_recording("streaming stopped")
            self._remember_radio_settings()
            self._save_view()
            self.engine.close()
            self.radio = None
            self._mode = None
            self.run_btn.setText("Start")
            self._set_status("Stopped - the radio is free for other programs.", 'warn')
            return
        self._use_radio(self.radio_combo.currentData())

    # =============================================================== modes
    def _tab_mode(self):
        return TAB_MODES[max(0, self.tabs.currentIndex())]

    def _place_side_boxes(self):
        """In Receive the RF gain is a row of the Radio card. In Sweep its box goes into the tab, under its boxes, and
        Audio and Record are hidden: there is nothing to hear or record
        while the radio sweeps. Elsewhere all three sit under the tabs."""
        mode = self._tab_mode()
        receive = mode == 'receive'
        if receive:
            self.rx_gain_slot.layout().addWidget(self.gain_row)
        else:
            self._gain_box_layout.addWidget(self.gain_row)
        self.gain_box.setVisible(not receive)
        if mode == 'sweep':
            self._sweep_outer.insertWidget(self._sweep_outer.indexOf(self.sweep_info),
                                           self.gain_box)
        else:
            self._left_box.insertWidget(1, self.gain_box)
        quiet = mode == 'sweep'                  # nothing to hear or record
        self.audio_box.setVisible(not quiet)
        self.record_box.setVisible(not quiet)

    def _tab_changed(self, _index):
        self._place_side_boxes()
        mode = self._tab_mode()
        if mode == 'recordings':
            self._enter_recordings()
        elif self._live is not None:
            self._leave_recordings()
        else:
            self._start_mode(mode)

    def _start_mode(self, mode):
        if self.radio is None or self._starting:
            return
        self._starting = True
        self._stop_recording(f"switched to {mode.title()}")
        try:
            if mode == 'sweep' and self.radio.can_sweep:
                self._start_sweep()
            else:
                self._start_receive()
            self.run_btn.setText("Stop")
            self.status.setToolTip('')
            self._show_gain()
        except Exception as exc:
            self._show_error(f"{self.radio.describe()} would not start: {exc}")
        finally:
            self._starting = False

    def _save_view(self):
        if self._mode == 'receive':
            self.cfg['view_receive'] = self.rf_view.state()
        elif self._mode == 'sweep':
            self.cfg['view_sweep'] = self.rf_view.state()
        elif self._mode == 'playback' and self._play is not None:
            if self._play.is_iq:
                self.cfg['view_playback'] = self.rf_view.state()
            else:
                self.cfg['view_audio'] = self.audio_view.state()

    # ---- receive
    def _receive_rate(self):
        data = self.rx_rate_combo.currentData()
        return float(data) if data else self.radio.default_receive_rate

    def _start_receive(self):
        self._run_receive('receive', self.tuner.value(), self._receive_rate(),
                          self.center_entry.value())
        self.rec_btn.setEnabled(True)
        self._set_status(self._running_text(), 'good')

    def _run_receive(self, mode, station_hz, rate, center_hz):
        """Build the receive chain and show it: for the radio (``mode``
        'receive'), or for an IQ recording ('playback')."""
        self._save_view()
        self._mode = mode
        self.rf_view.load_state(self.cfg[VIEW_KEYS[mode]])
        self.rf_view.set_level_unit('dBFS')
        self.rf_view.clear_density()
        # Receiving, the view is the band the radio streams: no limits.
        self.rf_view.set_pan_limits(None, None)
        self.engine.start_receive(
            station_hz, rate, center_hz=center_hz,
            channel_bw=self.chan_entry.value(),
            region=self.region_combo.currentData(),
            stereo=self.stereo_check.isChecked(),
            volume=self.volume_knob.value() / 100.0,
            muted=self.mute_btn.isChecked())
        rx = self.engine.rx
        # The tuner's marker shows while tuning; the orange band marks the
        # station the rest of the time.
        self.rf_view.set_marker_auto(True)
        rx.rf_probe.set_alpha(1.0 / max(1, self.rf_view.avg_knob.value()))
        rx.mpx_probe.set_alpha(1.0 / max(1, self.mpx_view.avg_knob.value()))
        self._place_receive_view()
        self.bottom.setCurrentWidget(self.rx_page)
        self.bottom.setVisible(True)
        self._clear_rds_labels()
        self._show_audio_note()

    def _place_receive_view(self, recentre=True):
        """Draw the band, the centre, the tuner and its channel where the
        engine has them. ``recentre`` puts a zoomed view back on the
        station - not wanted when the pointer is what moved it."""
        e = self.engine
        self.rf_view.set_extent(e.lo_hz - e.rate / 2, e.lo_hz + e.rate / 2,
                                center_hz=e.station_hz if recentre else None)
        self.rf_view.set_marker(e.station_hz)
        bw = self.chan_entry.value()
        self.rf_view.set_band(e.station_hz - bw / 2, e.station_hz + bw / 2)
        self.rf_view.set_center_line(e.lo_hz)
        self.rf_view.set_tuner_range(*e.tuner_range())
        self.rf_view.set_message('')
        self.center_entry.setValue(e.lo_hz)
        self._set_tuner(e.station_hz)
        self._show_range()

    def _show_range(self, at_edge=False):
        e = self.engine
        if self._mode != 'receive' or e.rx is None:
            self.range_label.setText('-')
            return
        low, high = e.tuner_range()
        text = f"\u2194 {low / 1e6:.3f} - {high / 1e6:.3f} MHz"
        tip = ("Tuner range: the lowest and highest the tuner can go around the "
               "radio's Center.")
        if self.radio is not None and self.radio.min_offset_hz:
            tip += (f"\nIt also keeps {self.radio.min_offset_hz / 1e3:.0f} kHz clear of "
                    "the Center, where the radio's DC spike is.")
        if at_edge:
            text = _coloured("\u2194 Band edge: move Center", 'warn')
            tip = "The tuner stops at the edge of the band: move the Center to go further."
            self._edge_timer.start(3000)
        self.range_label.setText(text)
        self.range_label.setToolTip(tip)

    def _restart_receive(self):
        if self._mode == 'receive' and self.radio is not None:
            self._start_mode('receive')

    def _running_text(self):
        e = self.engine
        if self._mode == 'playback' and self._play is not None:
            return f"{'Paused' if self._play.paused else 'Playing'} {self._play.name}"
        if e.mode == 'sweep' and getattr(e.sweeper, 'native', False):
            plan = e.sweeper.plan
            what = ("Watching {} to {} in real time" if getattr(plan, 'realtime', False)
                    else "Sweeping {} to {} in the radio")
            return (f"{self.radio.describe()} - "
                    + what.format(_freq_text(plan.start_hz), _freq_text(plan.stop_hz)))
        what = 'Sweeping' if e.mode == 'sweep' else 'Receiving'
        return f"{self.radio.describe()} - {what} at {rate_label(e.rate)}"

    def _show_audio_note(self):
        if self.args.no_sound_card:
            self.audio_note.setText("No sound output (started with "
                                    "--no-sound-card); recording still works.")
        elif self.engine.audio_error:
            self.audio_note.setText(_coloured(
                f"No sound output: {self.engine.audio_error}", 'warn'))
        elif self._flag_muted:
            self.audio_note.setText("Muted at start (--no-audio): press Mute to hear it.")
        else:
            self.audio_note.setText("")

    # ---- sweep
    def _sweep_plan(self):
        start, stop = self.sweep_start.value(), self.sweep_stop.value()
        if self.radio.native_sweep:
            view = self.rf_view
            radio = self.radio
            return radio.native_plan(start, stop, self.rbw_combo.currentData() or None,
                                     realtime=self.rt_btn.isChecked() and radio.has_realtime,
                                     ref_db=view.ref_knob.value(),
                                     scale_db=view.range_knob.value(),
                                     auto_gain=self.agc_box.isChecked() and radio.has_agc)
        rate = float(self.sweep_rate_combo.currentData() or self.radio.default_sweep_rate)
        return SweepPlan(start, stop, rate, int(self.fft_combo.currentData()),
                         self.radio.usable_fraction(rate), self.radio.dc_notch_hz)

    def _show_sweep_rows(self, native):
        """The RBW for a radio that sweeps itself; step bandwidth, FFT,
        frames and settling for one whose LO is hopped."""
        for rows, shown in ((self._native_rows, native), (self._hop_rows, not native)):
            for field in rows:
                field.setVisible(shown)
                label = self.sweep_form.labelForField(field)
                if label is not None:
                    label.setVisible(shown)

    def _realtime_span(self):
        """Real time's window - 27 MHz at most - on the tuner, slid inside
        the radio's range rather than cut short at its edges."""
        half = RT_MAX_SPAN_HZ / 2
        centre = self.tuner.value()
        start, stop = centre - half, centre + half
        if self.radio is not None:
            low, high = self.radio.sweep_range_hz
            if start < low:
                start, stop = low, min(high, low + RT_MAX_SPAN_HZ)
            elif stop > high:
                start, stop = max(low, high - RT_MAX_SPAN_HZ), high
        return start, stop

    def _put_realtime_window(self):
        """Put that window where the tuner is, and remember where it landed:
        letting the button out tells an untouched window from one the bounds
        have been edited under."""
        self._set_sweep_span(*self._realtime_span())
        self._rt_span = (self.sweep_start.value(), self.sweep_stop.value())

    def _realtime_toggled(self, on):
        """Pressed, real time drops the sweep to its window on the tuner;
        let out, it gives back the span that was there."""
        if on:
            self._before_rt = (self.sweep_start.value(), self.sweep_stop.value())
            self._put_realtime_window()
            return
        before, self._before_rt = self._before_rt, None
        here = (self.sweep_start.value(), self.sweep_stop.value())
        if before is not None and self._rt_span == here:
            self._set_sweep_span(*before)
        else:
            self._update_sweep_plan()

    def _follow_realtime(self, hz):
        """The tuner put outside the window real time is watching moves the
        window, not the tuner: it is the tuner that says where to watch."""
        if not (self._mode == 'sweep' and self.rt_btn.isChecked()
                and getattr(self.radio, 'has_realtime', False)):
            return
        if self.sweep_start.value() <= hz <= self.sweep_stop.value():
            return
        self._put_realtime_window()

    def _limit_sweep_bounds(self):
        """Each bound inside the radio's range, and short of the other."""
        if self.radio is None:
            return
        low, high = self.radio.sweep_range_hz
        self.sweep_start.set_range(low, high - MIN_SWEEP_SPAN_HZ)
        self.sweep_stop.set_range(low + MIN_SWEEP_SPAN_HZ, high)
        self.sweep_start.set_range(low, self.sweep_stop.value() - MIN_SWEEP_SPAN_HZ)
        self.sweep_stop.set_range(self.sweep_start.value() + MIN_SWEEP_SPAN_HZ, high)

    def _start_sweep(self):
        self._save_view()
        self._mode = 'sweep'
        self.rf_view.load_state(self.cfg['view_sweep'])
        plan = self._sweep_plan()
        self.engine.start_sweep(plan, frames=self.frames_spin.value(),
                                settle_ms=self.settle_spin.value())
        self._reset_sweep_display(plan, full_span=False)
        self.bottom.setVisible(False)          # the RF spectrum has the height
        self.rec_btn.setEnabled(False)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("Pause")
        self._set_status(self._running_text(), 'good')

    def _reset_sweep_display(self, plan, full_span=True):
        self._sweep_avg = None
        self._sweep_db = None
        self._wf_hold = None
        self._last_serial = -1
        self._list_serial = -1
        self.rf_view.set_pan_limits(*SWEEP_VIEW_HZ)
        self.rf_view.set_extent(plan.start_hz, plan.stop_hz, keep_span=not full_span)
        self._show_sweep_band()
        self.rf_view.set_center_line(None)
        self.rf_view.set_tuner_range(None, None)
        self.rf_view.set_marker_auto(False)           # sweeping, the marker is the pick
        self.range_label.setText('-')
        self.rf_view.set_marker(self.tuner.value())
        self.rf_view.set_message('')
        self.rf_view.clear_peak()
        self.rf_view.set_level_unit(getattr(plan, 'unit', 'dBFS'))
        self.rf_view.clear_density()
        self.sweep_info.setText(plan.describe())

    def _show_sweep_band(self):
        """The channel the receiver would take, drawn on the sweep: the
        orange bar says where Listen, the Receive tab and Real time will
        start from, and middle-dragging it moves them. Nothing is retuned
        meanwhile - the radio is sweeping."""
        bw = self.chan_entry.value()
        hz = self.tuner.value()
        self.rf_view.set_band(hz - bw / 2, hz + bw / 2)

    def _restart_sweep(self):
        if self._mode == 'sweep' and self.radio is not None:
            self._start_mode('sweep')

    def _update_sweep_plan(self):
        """Span, FFT or frames changed: re-plan on the running sweep - only a
        new step bandwidth needs the radio restarted."""
        if self._mode != 'sweep' or self.engine.sweeper is None:
            return
        try:
            plan = self._sweep_plan()
        except ValueError as exc:
            self.sweep_info.setText(_coloured(str(exc), 'warn'))
            return
        self.engine.sweeper.set_plan(plan, frames=self.frames_spin.value())
        self._reset_sweep_display(plan)

    def _sweep_bounds_edited(self, _hz):
        self._limit_sweep_bounds()
        self._select_preset()
        self._bounds_timer.start()

    def _set_sweep_span(self, start_hz, stop_hz, replan=True):
        if self.radio is not None:
            low, high = self.radio.sweep_range_hz
            start_hz, stop_hz = max(start_hz, low), min(stop_hz, high)
        # Widest first, so neither bound is held back by the other's old value.
        for entry in (self.sweep_start, self.sweep_stop):
            entry.set_range(1e3, 6000e6)
        self.sweep_start.setValue(start_hz)
        self.sweep_stop.setValue(stop_hz)
        self._limit_sweep_bounds()
        self._select_preset()
        if replan:
            self._update_sweep_plan()

    def _preset_chosen(self, index):
        _, start, stop = SWEEP_PRESETS[index]
        if start == 'full':
            if self.radio is not None:
                self._set_sweep_span(*self.radio.sweep_range_hz)
        elif start is not None:
            self._set_sweep_span(start * 1e6, stop * 1e6)

    def _select_preset(self, remember=True):
        """The preset the bounds are, or Custom; remembered as the band."""
        start, stop = self.sweep_start.value(), self.sweep_stop.value()
        full = self.radio.sweep_range_hz if self.radio is not None else None
        for i, (_, a, b) in enumerate(SWEEP_PRESETS):
            if a == 'full':
                hit = full is not None and abs(full[0] - start) < 1 and abs(full[1] - stop) < 1
            else:
                hit = a is not None and abs(a * 1e6 - start) < 1 and abs(b * 1e6 - stop) < 1
            if hit:
                self.preset_combo.setCurrentIndex(i)
                if remember:
                    self.cfg['sweep_band'] = 'full' if a == 'full' else 'preset'
                return
        self.preset_combo.setCurrentIndex(len(SWEEP_PRESETS) - 1)
        if remember:
            self.cfg['sweep_band'] = 'custom'

    def _view_scale_changed(self, _value):
        plan = getattr(self.engine.sweeper, 'plan', None)
        # The device uses the Ref level in real time (the density map's
        # top) and under AGC (the gain it picks).
        if self._mode == 'sweep' and (getattr(plan, 'realtime', False)
                                      or getattr(plan, 'auto_gain', False)):
            self._bounds_timer.start()

    def _settle_changed(self, ms):
        if self.engine.sweeper is not None:
            self.engine.sweeper.set_settle_ms(ms)

    def _pause_toggled(self, paused):
        self.pause_btn.setText("Resume" if paused else "Pause")
        if self.engine.sweeper is not None:
            self.engine.sweeper.set_paused(paused)

    # ============================================================= tuning
    def _set_tuner(self, hz, emit=False):
        """One frequency, both faces of the tuner: Receive's digits and
        Sweep's. Only the one the window asks for emits."""
        changed = self.tuner.setValue(hz, emit=emit)
        self.sweep_tuner.setValue(hz)
        return changed

    def _tuner_range(self, low, high):
        for entry in (self.tuner, self.sweep_tuner):
            entry.set_range(low, high)

    def _step_hz(self):
        return float(STEPS_KHZ[int(round(self.step_knob.value()))]) * 1e3

    def _step(self, steps):
        step = self._step_hz()
        self.tune(on_raster(self.tuner.value() + steps * step, step))

    def _tuner_edited(self, hz):
        self.tune(hz)

    def tune(self, hz, follow=False, recentre=True, final=True):
        """Retune the receiver (or, sweeping, move the marker).

        In Receive the tuner stops at the edge of the band around the
        Center; with ``follow`` a station outside it moves the Center
        instead. ``recentre`` brings a zoomed view back to the station, and
        ``final`` False (mid-drag) leaves the recordings' parts until the
        drag ends."""
        # To the tuner's last digit, 1 kHz: a drag or a click lands anywhere.
        hz = round(float(hz) / self.tuner.resolution) * self.tuner.resolution
        hz = min(max(hz, self.tuner.minimum()), self.tuner.maximum())
        if self._mode in ('receive', 'playback') and self.engine.rx is not None:
            moved = self.engine.tune(hz, follow=follow)
            self._after_retune(moved, hz, recentre, final)
            self.rf_view.tuner_moving()
        else:
            self.engine.tune(hz)
            self._set_tuner(hz)
            self.rf_view.set_marker(hz)
            self._show_sweep_band()
            if final:                 # mid-drag, the window waits for the end
                self._follow_realtime(hz)

    def _center_edited(self, hz):
        if self._mode == 'receive' and self.engine.rx is not None:
            # The centre line steps aside while it moves, so the spectrum
            # under it can be seen; it comes back once the centre settles.
            self.rf_view.center_moving()
            station = self.engine.station_hz
            moved = self.engine.set_center(hz)
            self._after_retune(moved, station, recentre=False, final=True)

    def _center_on_tuner(self):
        lo_offset = self.radio.lo_offset_hz if self.radio is not None else 300e3
        self.center_entry.setValue(self.tuner.value() - lo_offset, emit=True)

    def _after_retune(self, moved, wanted_hz, recentre, final):
        e = self.engine
        self._place_receive_view(recentre=recentre)
        # Peak hold of the old channel's MPX, or of the band before the LO
        # moved, is not this signal: start both again.
        self.mpx_view.clear_peak()
        if moved:
            self.rf_view.clear_peak()
        self._clear_rds_labels()
        if abs(e.station_hz - wanted_hz) > 500:
            self._show_range(at_edge=True)
        if final:
            self._recordings_retuned()

    def _recordings_retuned(self):
        e = self.engine
        if self._rec_info is not None:
            self._rec_info.note_tune(e.station_hz)
        for rec in self._iq_recs:
            centre = e.station_hz if rec.kind == 'channel' else e.lo_hz
            try:
                rec.retuned(centre, e.station_hz)
            except Exception as exc:
                self._stop_recording(f"could not continue recording: {exc}")
                break

    def _rf_clicked(self, hz):
        self.tune(hz, recentre=False)       # sweeping, this only moves the marker

    def _rf_activated(self, hz):
        self.tune(hz, recentre=False)
        if self._mode == 'sweep':
            self.tabs.setCurrentIndex(1)

    def _band_wheel(self, steps, fine):
        step = BAND_WHEEL_FINE_HZ if fine else BAND_WHEEL_HZ
        self.chan_entry.setValue(self.chan_entry.value() + steps * step, emit=True)

    def _band_dragged(self, hz):
        if self.snap_check.isChecked():
            hz = on_raster(hz, self._step_hz())
        if abs(hz - self.tuner.value()) >= 500:
            self.tune(hz, recentre=False, final=False)

    def _band_drag_finished(self):
        self._recordings_retuned()
        self._follow_realtime(self.tuner.value())

    def _listen_selected(self):
        item = self.station_list.currentItem()
        if item is not None:
            self.tune(item.data(QtCore.Qt.UserRole))
        self.tabs.setCurrentIndex(1)

    def _station_selected(self, item, _previous=None):
        if item is not None:
            self.tune(item.data(QtCore.Qt.UserRole))

    def _station_activated(self, item):
        self.tune(item.data(QtCore.Qt.UserRole))
        self.tabs.setCurrentIndex(1)

    # ========================================================= live controls
    def _agc_on(self):
        """AGC in force: ticked, on a radio sweeping itself, in Sweep."""
        return (self.radio is not None and self.radio.has_agc
                and self.agc_box.isChecked() and self._mode == 'sweep')

    def _agc_available(self):
        """A radio the window can steer the gain of: one that reports its
        overloads (the BB60D) or has its clipping counted (``clip_warn``:
        a HackRF, an RTL-SDR, a USRP). Not a recording."""
        radio = self.radio
        return (radio is not None and radio.kind != 'file'
                and bool(radio.has_agc or radio.clip_warn))

    def _iq_agc_on(self):
        """The window's AGC on the IQ stream in force: ticked, in Receive
        (``IqAgc``)."""
        return (self._agc_available() and self.agc_box.isChecked()
                and self._mode == 'receive')

    def _agc_tooltip(self):
        radio = self.radio
        bb60 = radio is not None and radio.has_agc
        if bb60:
            sweep = ("In Sweep: the Ref level knob moves to 5 dB over the strongest\n"
                     "signal, and the radio sets its gain and attenuation for it, band\n"
                     "by band.\n\n")
            down = ("the gain goes down 10% when the radio reports an overload\n"
                    "(an overloaded BB60D sends nothing at all)")
            cost = ("Each change leaves a gap of about 0.1 s in the samples: a click\n"
                    "in the audio.")
        else:
            sweep = "Not in Sweep on this radio: the slider sets the gain there.\n\n"
            down = (f"the gain goes down 10% when over {CLIP_HEAVY:.0%} of the samples\n"
                    f"clip, 5% when over {CLIP_WARN:.0%}")
            cost = ("A HackRF's gain changes leave no gap, but each try at more gain\n"
                    "can clip for a moment." if radio is not None and radio.kind == 'hackrf'
                    else "Each change may leave a gap in the samples, and each try at\n"
                    "more gain can clip for a moment.")
        return ("Automatic gain.\n\n" + sweep + f"In Receive: {down},\n"
                "and back up 5% after a minute without, never above the slider.\n"
                + cost + "\nOnce the gain has settled, turn AGC off to keep it there.")

    def _follow_iq_agc(self, overloads, now, calm, heavy=True):
        if not self._iq_agc_on() or not self.engine.running:
            self._iq_agc = None
            return
        if self._iq_agc is None:
            ceiling = self._agc_ceiling if self._agc_ceiling is not None \
                else self.gain_slider.value()
            self._iq_agc = IqAgc(ceiling, self.radio.gain_percent)
        gain = self._iq_agc.update(now, overloads, calm, heavy)
        if gain is not None:
            try:
                self.radio.apply_gain(gain)
            except Exception as exc:
                self._set_status(f"AGC could not set the gain: {exc}", 'warn')
            # The slider follows, quietly: a move by hand is a new ceiling.
            self.gain_slider.blockSignals(True)
            self.gain_slider.setValue(int(round(self.radio.gain_percent)))
            self.gain_slider.blockSignals(False)
            self._show_gain()

    def _show_gain(self):
        """The slider and its label, for AGC or not."""
        radio = self.radio
        available = self._agc_available()
        self.agc_box.setVisible(available)
        # A HackRF's or RTL-SDR's AGC is the window's, on the IQ stream: none
        # in Sweep. The box keeps its tick for Receive.
        self.agc_box.setEnabled(available and (radio.has_agc or self._mode != 'sweep'))
        self.agc_box.setToolTip(self._agc_tooltip() if available else "")
        agc = self._agc_on()
        self.gain_slider.setEnabled(radio is not None and radio.kind != 'file' and not agc)
        if self._iq_agc_on():
            ceiling = self._agc_ceiling if self._agc_ceiling is not None \
                else self.gain_slider.value()
            self.gain_label.setText(f"AGC {radio.gain_percent:.0f}%")
            self.gain_slider.setToolTip(
                f"With AGC the slider follows the gain AGC sets, up to {ceiling:.0f}%:\n"
                "where you last put it. Moving it sets a new limit, and the gain.\n"
                "Once the gain has settled, turn AGC off to keep it there.")
        else:
            self.gain_label.setText("AGC" if agc else f"{self.gain_slider.value()}%")
            self.gain_slider.setToolTip("")

    def _agc_toggled(self, on):
        self._agc_levels = []
        if on:
            # From here: AGC may use up to where the slider is now. Turned off,
            # the gain stays where AGC put it - the slider is already there.
            self._agc_ceiling = float(self.gain_slider.value())
        self._iq_agc = None
        if self.radio is not None:
            self.cfg['gain_auto'][self.radio.kind] = self.agc_box.isChecked()
        self._show_gain()
        sweeper = self.engine.sweeper
        if self._mode == 'sweep' and getattr(sweeper, 'native', False):
            done = sweeper.snapshot()[2]
            if on and done is not None and sweeper.plan.bin_hz:
                # The Ref level from the last sweep first: the knob may be
                # anywhere, and the device's gain follows it at once.
                level = bb60_sweep.strongest_input(done, sweeper.plan.bin_hz,
                                                   sweeper.plan.rbw)
                self.rf_view.ref_knob.setValue(bb60_sweep.agc_ref(level, REF_FLOOR_DB))
            self._update_sweep_plan()

    def _follow_agc(self, done):
        """AGC: the Ref level knob to 5 dB over the strongest input (the
        most power in 200 kHz, over the last few seconds' sweeps), when that
        has risen past it or fallen well below; turning the knob re-plans
        the sweep at the new reference level."""
        plan = self.engine.sweeper.plan
        if not plan.bin_hz:
            return
        knob = self.rf_view.ref_knob
        now = time.monotonic()
        self._agc_levels = [(t, v) for t, v in self._agc_levels
                            if now - t < bb60_sweep.AGC_WINDOW_S]
        self._agc_levels.append((now, bb60_sweep.strongest_input(done, plan.bin_hz, plan.rbw)))
        ref = bb60_sweep.agc_ref(max(v for _, v in self._agc_levels), knob.value())
        if ref is not None:
            knob.setValue(ref)

    def _gain_changed(self, value):
        # Moved by hand (AGC moves it quietly): with AGC, a new ceiling.
        self._agc_ceiling = float(value)
        self._iq_agc = None
        self.gain_label.setText("AGC" if self._agc_on() else f"{value}%")
        if self.radio is not None:
            self.radio.gain_percent = float(value)
            if self.engine.running:
                try:
                    self.radio.apply_gain(value)
                except Exception as exc:
                    self._set_status(f"Could not set gain: {exc}", 'warn')

    def _chan_bw_changed(self, hz):
        if self._mode == 'sweep':
            self._show_sweep_band()
        elif self.engine.rx is not None:
            self.engine.rx.set_channel_bw(hz)
            e = self.engine
            self.rf_view.set_band(e.station_hz - hz / 2, e.station_hz + hz / 2)

    def _region_changed(self, _index):
        if self.engine.rx is not None:
            self.engine.rx.set_region(self.region_combo.currentData())
            self._clear_rds_labels()

    def _stereo_toggled(self, on):
        if self.engine.rx is not None:
            self.engine.rx.set_stereo_enabled(on)

    def _snap_toggled(self, on):
        self.rf_view.snap_hz = self._step_hz() if on else None

    def _mute_toggled(self, muted):
        if not muted:
            self._flag_muted = False
            self._show_audio_note()
        self.mute_btn.setText("Muted" if muted else "Mute")
        for chain in (self.engine.rx, self.engine.player):
            if chain is not None:
                chain.set_muted(muted)

    def _volume_changed(self, value):
        for chain in (self.engine.rx, self.engine.player):
            if chain is not None:
                chain.set_volume(value / 100.0)

    def _rf_average_changed(self, n):
        if self._mode in ('receive', 'playback') and self.engine.rx is not None:
            self.engine.rx.rf_probe.set_alpha(1.0 / max(1, n))
        self._sweep_avg = None

    def _audio_average_changed(self, n):
        if self.engine.player is not None:
            self.engine.player.probe.set_alpha(1.0 / max(1, n))

    def _mpx_average_changed(self, n):
        if self.engine.rx is not None:
            self.engine.rx.mpx_probe.set_alpha(1.0 / max(1, n))

    def _clear_rds(self):
        if self.engine.rx is not None:
            self.engine.rx.reset_decoders()
        self._clear_rds_labels()

    def _clear_rds_labels(self):
        for label in self.lbl.values():
            label.setText('-')

    def _next_theme(self):
        self.set_theme(theme.after(theme.current()))

    def set_theme(self, name):
        self.cfg['theme'] = theme.valid(name)
        apply_window_theme(self, self.cfg['theme'])
        for view in (self.rf_view, self.mpx_view, self.audio_view):
            view.restyle()
        for entry in (self.tuner, self.sweep_tuner, self.center_entry,
                      self.chan_entry):
            entry.restyle()
        self.timeline.update()
        self.theme_disc.describe()
        self.theme_word.setToolTip(self.theme_disc.toolTip())
        self._refit_left()
        self.update()

    # ============================================================ recording
    def _recording_dir(self):
        return self.cfg.get('recording_dir') or default_recording_dir()

    def _update_folder_tip(self):
        self.folder_btn.setToolTip(f"Recordings go to {self._recording_dir()}")
        self.rec_band.setText("IQ - whole band" + (
            f" ({self._receive_rate() * 8 / 1e6:.0f} MB/s)"
            if self.radio is not None and self.rx_rate_combo.count() else ""))

    def _choose_folder(self):
        folder = Qt.QFileDialog.getExistingDirectory(self, "Recordings folder",
                                                     self._recording_dir())
        if folder:
            self.cfg['recording_dir'] = folder
            self._update_folder_tip()
            if self._live is not None:
                self._rec_sel = None
                self._refresh_library()

    def _record_toggled(self, on):
        if on:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        rx = self.engine.rx
        if self._mode != 'receive' or rx is None or not self.engine.running:
            self.rec_btn.blockSignals(True)
            self.rec_btn.setChecked(False)
            self.rec_btn.blockSignals(False)
            self.rec_label.setText(_coloured("Recording works in Receive.", 'warn'))
            return
        if not (self.rec_audio.isChecked() or self.rec_channel.isChecked()
                or self.rec_band.isChecked()):
            self.rec_btn.blockSignals(True)
            self.rec_btn.setChecked(False)
            self.rec_btn.blockSignals(False)
            self.rec_label.setText(_coloured("Tick something to record.", 'warn'))
            return
        folder = self._recording_dir()
        e = self.engine
        name = self.radio.describe()
        kinds = [kind for kind, box in (('audio', self.rec_audio),
                                        ('iq-channel', self.rec_channel),
                                        ('iq-band', self.rec_band)) if box.isChecked()]
        try:
            os.makedirs(folder, exist_ok=True)
            # Every file of this recording is named from one base.
            session = session_base(folder, e.station_hz)
            self._rec_info = RecordingInfo(session, e.station_hz, name, kinds)
            if self.rec_audio.isChecked():
                self._wav = WavWriter(session + '-audio.wav')
                rx.tap.set_writer(self._wav)
            if self.rec_channel.isChecked():
                rec = IqRecording(rx.channel_sink, 'channel', folder, rx.channel_rate,
                                  e.station_hz, e.station_hz, name,
                                  base=session + '-iq-channel')
                rec.start()
                self._iq_recs.append(rec)
            if self.rec_band.isChecked():
                rec = IqRecording(rx.band_sink, 'band', folder, e.rate, e.lo_hz,
                                  e.station_hz, name, base=session + '-iq-band')
                rec.start()
                self._iq_recs.append(rec)
        except Exception as exc:
            self._stop_recording()
            self.rec_label.setText(_coloured(f"Could not record: {exc}", 'bad'))
            return
        self._rec_t0 = time.monotonic()
        self.rec_btn.setText("Stop recording")
        for box in (self.rec_audio, self.rec_channel, self.rec_band):
            box.setEnabled(False)

    def _stop_recording(self, reason=''):
        if self._wav is None and not self._iq_recs and self._rec_info is None:
            return
        saved, files = [], []
        if self._wav is not None:
            if self.engine.rx is not None:
                self.engine.rx.tap.set_writer(None)
            path = self._wav.close()
            note = f" ({self._wav.error})" if self._wav.error else ''
            saved.append(os.path.basename(path) + note)
            files.append(path)
            self._wav = None
        for rec in self._iq_recs:
            try:
                paths = rec.stop()
                saved.extend(os.path.basename(p) for p in paths)
                files.extend(paths)
            except Exception as exc:
                saved.append(f"IQ error: {exc}")
        self._iq_recs = []
        if self._rec_info is not None:
            try:
                self._rec_info.finish(files)
            except Exception as exc:
                saved.append(f"Description not saved: {exc}")
            self._rec_info = None
        self._rec_t0 = None
        self.rec_btn.blockSignals(True)
        self.rec_btn.setChecked(False)
        self.rec_btn.blockSignals(False)
        self.rec_btn.setText("Record")
        for box in (self.rec_audio, self.rec_channel, self.rec_band):
            box.setEnabled(True)
        why = f"Stopped ({reason}). " if reason else ""
        text = f"{why}Saved in {self._recording_dir()}:\n" + "\n".join(saved)
        self.rec_label.setText(text)
        self.rec_label.setToolTip(text)

    def _recording_progress(self):
        if self._rec_t0 is None:
            return
        seconds = time.monotonic() - self._rec_t0
        size = sum(r.bytes_written() for r in self._iq_recs)
        if self._wav is not None:
            size += self._wav.frames * 4
        text = f"Recording {int(seconds // 60):02d}:{int(seconds % 60):02d} - {size / 1e6:.1f} MB"
        if self._wav is not None and self._wav.dropped:
            text += f" - {self._wav.dropped} audio samples dropped (disk too slow)"
        self.rec_label.setText(_coloured(text, 'live'))

    # ========================================================== recordings
    def _enter_recordings(self):
        """Close the radio - free for other programs while you listen back -
        and list the recordings."""
        self._stop_recording("switched to Recordings")
        self._live = {'kind': self.radio_combo.currentData(),
                      'tuner': self.tuner.value(), 'center': self.center_entry.value()}
        if self.radio is not None:
            self._remember_radio_settings()
            self._save_view()
            self.engine.close()
            self.radio = None
        self._mode = None
        for widget in (self.radio_combo, self.usrp_edit, self.rtl_edit, self.file_btn,
                       self.run_btn, self.gain_slider, self.agc_box, self.rec_btn):
            widget.setEnabled(False)
        self._idle_views()
        held = self._live['kind'] == 'bb60' and bb60_source.KEEP_OPEN
        self._set_status("Recordings - the radio is stopped until you go back to "
                         "Sweep or Receive" + (" (on a Mac a BB60D stays open)." if held
                                               else ", and free for other programs."))
        self.status.setToolTip('')
        self._refresh_library()

    def _leave_recordings(self):
        """Back to Sweep or Receive: the radio opens again, tuned where it was."""
        self._stop_playback()
        live, self._live = self._live, None
        self._show_audio_view(False)
        for widget in (self.radio_combo, self.usrp_edit, self.rtl_edit, self.file_btn, self.run_btn):
            widget.setEnabled(True)
        self._tuner_range(1e3, 6000e6)
        self._set_tuner(live['tuner'])
        self.center_entry.set_range(1e3, 6000e6)
        self.center_entry.setValue(live['center'])
        self._use_radio(live['kind'] or self.radio_combo.currentData())

    def _show_audio_view(self, audio):
        """A WAV's sound on top, and nothing under it; else the RF spectrum
        and the multiplex."""
        self.top_stack.setCurrentWidget(self.audio_view if audio else self.rf_view)
        self.bottom.setVisible(not audio)

    def _idle_views(self):
        self._show_audio_view(False)
        view = self.rf_view
        view.clear()
        view.set_band(None, None)
        view.set_center_line(None)
        view.set_tuner_range(None, None)
        view.set_marker(None)
        view.clear_density()
        view.set_message("Choose a recording and press Play")
        self.mpx_view.clear()
        self.bottom.setCurrentWidget(self.rx_page)
        self.meter.set_levels((0.0, 0.0), (0.0, 0.0))

    def _watch_folder(self, folder):
        watched = self._lib_watch.directories()
        if watched != [folder]:
            if watched:
                self._lib_watch.removePaths(watched)
            if os.path.isdir(folder):
                self._lib_watch.addPath(folder)

    def _refresh_library(self):
        """Read the folder again, keeping the one chosen."""
        folder = self._recording_dir()
        self._watch_folder(folder)
        keep = self._rec_sel.key if self._rec_sel is not None else None
        try:
            self._recordings = library.scan(folder)
        except Exception:
            self._recordings = []
            self._report('recordings')
        self.rec_list.blockSignals(True)
        self.rec_list.clear()
        chosen = None
        for rec in self._recordings:
            item = Qt.QListWidgetItem(self._recording_text(rec))
            item.setData(QtCore.Qt.UserRole, rec.key)
            item.setToolTip("\n".join(os.path.basename(p) for p in rec.files()))
            self.rec_list.addItem(item)
            if rec.key == keep:
                chosen = item
        self.rec_list.blockSignals(False)
        count = len(self._recordings)
        if count:
            self.lib_note.setText(f"{count} recording{'s' if count > 1 else ''} in {folder}")
        else:
            self.lib_note.setText(f"No recordings in {folder} yet. Record in the "
                                  "Receive tab, then come back here.")
        if self._play is not None and not any(r.key == self._play.recording.key
                                              for r in self._recordings):
            self._stop_playback()                   # deleted from outside
        if chosen is None and self.rec_list.count():
            chosen = self.rec_list.item(0)
        if chosen is not None:
            self.rec_list.blockSignals(True)
            self.rec_list.setCurrentItem(chosen)
            self.rec_list.blockSignals(False)
        self._recording_selected(chosen)

    @staticmethod
    def _recording_text(rec):
        head = f"{rec.station_hz / 1e6:.2f} MHz"
        if rec.name:
            head += f"   {rec.name.strip()}"
        if rec.callsign:
            head += f"   {rec.callsign}"
        return head + "\n" + " · ".join((rec.started.strftime('%b %d %H:%M'),
                                         library.clock(rec.seconds), rec.kinds_text(),
                                         library.size_text(rec.bytes)))

    def _recording_for(self, item):
        key = item.data(QtCore.Qt.UserRole) if item is not None else None
        return next((r for r in self._recordings if r.key == key), None)

    def _recording_selected(self, item, _previous=None):
        rec = self._recording_for(item)
        if rec is not None and self._rec_sel is not None and rec.key == self._rec_sel.key:
            self._rec_sel = rec                     # read again: same recording
            if self._play is not None:
                self._play.recording = rec
            return
        self._stop_playback()
        self._rec_sel = rec
        self.track_combo.clear()
        if rec is None:
            self._load_track(None)
            return
        for track in rec.tracks:
            self.track_combo.addItem(track.label(), track.path)
        best = rec.best_track()
        self.track_combo.setCurrentIndex(rec.tracks.index(best))
        self._load_track(best)

    def _track_chosen(self, index):
        if self._rec_sel is None or not 0 <= index < len(self._rec_sel.tracks):
            return
        track = self._rec_sel.tracks[index]
        if self._track is not None and track.path == self._track.path:
            return
        playing = self._play is not None and not self._play.paused
        self._stop_playback()
        self._load_track(track)
        if playing:
            self.play_btn.setChecked(True)

    def _load_track(self, track):
        """Show ``track`` ready to play from its start, and have its overview
        worked out."""
        self._track = track
        self._start_at = 0.0
        self._heard = ('', 0.0)
        self.timeline.set_overview(None)
        self._timeline_levels()
        self.timeline.set_duration(track.seconds if track else 0.0)
        self.timeline.set_position(0.0)
        self.timeline.set_note('')
        self._show_time(0.0)
        self.play_btn.setEnabled(track is not None and not track.error)
        self.delete_btn.setEnabled(self._rec_sel is not None)
        rec = self._rec_sel
        if rec is None:
            self.play_station.setText('-')
            self.play_text.setText('-')
            return
        self._show_station(rec.station_hz, rec.name)
        self._show_rds_text(*rec.rds_at(0.0))
        if track.error:
            self.timeline.set_note(f"Cannot play: {track.error}")
            return
        self.timeline.set_note("Working out the overview...")
        self._overview_job = (track.path, self._pool.submit(library.overview, track))

    def _timeline_levels(self):
        """The strip's colours from the view the chosen track plays in: the
        RF spectrum for IQ, the sound's spectrum for a WAV."""
        track = self._track
        view = self.audio_view if track is not None and track.kind == 'audio' else self.rf_view
        self.timeline.set_levels(view.ref_knob.value(), view.range_knob.value())

    def _collect_overview(self):
        job = self._overview_job
        if job is None or not job[1].done():
            return
        self._overview_job = None
        path, future = job
        if self._track is None or self._track.path != path:
            return
        try:
            img, _ = future.result()
            self.timeline.set_overview(img)
            self.timeline.set_note('')
        except Exception as exc:
            self.timeline.set_note(f"No overview: {exc}")

    def _show_station(self, hz, name):
        name = (name or '').strip()
        self.play_station.setText(f"{hz / 1e6:.2f} MHz" + (f"   {name}" if name else ""))

    def _show_rds_text(self, radiotext, playing):
        lines = [x.strip() for x in (playing, radiotext) if x and x.strip()]
        self.play_text.setText("\n".join(lines) or '-')

    def _show_time(self, seconds):
        total = self._track.seconds if self._track is not None else 0.0
        self.time_label.setText(f"{library.clock(seconds)} / {library.clock(total)}")
        self.timeline.set_position(seconds)

    # ---- the player
    def _play_toggled(self, on):
        try:
            if on:
                self._play_start()
            else:
                self._play_pause()
        except Exception as exc:
            name = os.path.basename(self._track.path) if self._track else 'it'
            self._stop_playback()
            self.play_btn.blockSignals(True)
            self.play_btn.setChecked(False)
            self.play_btn.blockSignals(False)
            self.play_btn.setText("Play")
            self._set_status(f"Could not play {name}: {str(exc).splitlines()[0]}", 'bad')
            self.status.setToolTip(str(exc))

    def _play_start(self):
        track = self._track
        if track is None:
            self.play_btn.setChecked(False)
            return
        p = self._play
        if p is not None and p.track.path == track.path:
            self._resume()
        else:
            self._stop_playback()
            if track.is_iq:
                self._open_iq(track)
            else:
                self._open_wav(track)
        self.play_btn.setText("Pause")
        self._set_status(self._running_text(), 'good')

    def _open_iq(self, track):
        """Play an IQ recording as a radio: the file source, through the
        receive chain, on the station it was made of."""
        radio = IQFile(track.path, repeat=True, throttle=True)
        self.engine.use_radio(radio)
        self._play = Playback(self._rec_sel, track, radio=radio)
        radio.place(int(self._start_at * radio.rate))
        low, high = radio.freq_range_hz
        self._tuner_range(low, high)
        self.center_entry.set_range(radio.center_hz, radio.center_hz)
        self._show_audio_view(False)
        self._run_receive('playback', track.station_hz or radio.center_hz,
                          radio.rate, radio.center_hz)
        radio.counting(True)

    def _open_wav(self, track):
        if track.rate != AUDIO_RATE:
            raise ValueError(f"it is sampled at {track.rate / 1e3:g} kHz; "
                             f"only {AUDIO_RATE / 1e3:g} kHz plays")
        source = wav_source(library.wav_frames(track.path),
                            loop=self.loop_check.isChecked())
        source.seek(int(self._start_at * AUDIO_RATE))
        self.engine.close()
        self._play = Playback(self._rec_sel, track, source=source)
        self._mode = 'playback'
        self._show_audio_view(True)
        view = self.audio_view
        view.clear()
        view.load_state(self.cfg['view_audio'])
        view.set_extent(0.0, AUDIO_RATE / 2, center_hz=0.0)
        view.set_level_unit('dBFS')
        self.engine.start_wav(source, volume=self.volume_knob.value() / 100.0,
                              muted=self.mute_btn.isChecked())
        self.engine.player.probe.set_alpha(1.0 / max(1, view.avg_knob.value()))
        self._show_audio_note()

    def _play_pause(self):
        p = self._play
        if p is None or p.paused:
            return
        if p.is_iq:
            p.radio.counting(False)
            self.engine.pause()
        else:
            p.source.set_paused(True)
        p.paused = True
        self.play_btn.setText("Play")
        self._set_status(self._running_text())

    def _resume(self):
        p = self._play
        if p.is_iq:
            p.radio.place(p.radio.position())    # exactly where it stopped
            self.engine.resume()
            p.radio.counting(True)
        else:
            p.source.set_paused(False)
        p.paused = False

    def _stop_playback(self):
        """Unload whatever is playing; the radio stays closed."""
        if self._play is None:
            return
        self._save_view()
        self._play = None
        self.engine.close()
        self._mode = None
        self.audio_view.clear()
        self._idle_views()
        self._clear_rds_labels()
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(False)
        self.play_btn.blockSignals(False)
        self.play_btn.setText("Play")
        self._start_at = 0.0
        self._show_time(0.0)

    def _loop_toggled(self, on):
        self.cfg['play_loop'] = bool(on)
        if self._play is not None and not self._play.is_iq:
            self._play.source.loop = bool(on)

    def _seek_to(self, seconds):
        """Jump to ``seconds`` into the track - before it plays, too."""
        p = self._play
        if p is None:
            self._start_at = seconds
        elif p.is_iq:
            p.radio.seek(int(seconds * p.radio.rate))
            rx = self.engine.rx
            if rx is not None:
                rx.reset_decoders()
                if self.engine.running:
                    rx.discard_stale(p.radio.block, True, 0.0)
            self.rf_view.clear_peak()
            self.mpx_view.clear_peak()
            self._clear_rds_labels()
            self._heard = ('', 0.0)
        else:
            p.source.seek(int(seconds * AUDIO_RATE))
            if self.engine.player is not None:
                self.engine.player.seeked()
            self.audio_view.clear_peak()
        self._show_time(seconds)

    def _follow_playback(self):
        """The playhead and the time; at the end, stop, or go round."""
        p = self._play
        if p.paused:
            return
        if p.is_iq:
            if not self.engine.running:
                return
            played, total = p.radio.played(), p.radio.total
            if played >= total and not self.loop_check.isChecked():
                self._play_ended()
                return
            seconds = (played % total) / p.radio.rate
        else:
            if p.source.finished:
                self._play_ended()
                return
            seconds = p.source.position / AUDIO_RATE
        self._show_time(seconds)

    def _play_ended(self):
        name = self._play.name
        self.play_btn.setChecked(False)          # pauses
        self._seek_to(0.0)
        self._set_status(f"Finished {name}")

    def _draw_wav(self):
        player = self.engine.player
        spectrum = player.probe.snapshot()
        if spectrum is not None:
            n = len(spectrum)
            half = n // 2 + 1
            self.audio_view.set_data(np.arange(half) * AUDIO_RATE / n,
                                     to_db(spectrum[:half]))
        peak, rms = player.tap.levels()
        self.meter.set_levels(peak, rms)

    def _refresh_wav(self):
        """A WAV has no RDS: show what the recording logged at this time."""
        p = self._play
        rec = p.recording
        self._show_station(rec.station_hz, rec.name)
        self._show_rds_text(*rec.rds_at(p.source.position / AUDIO_RATE))

    def _show_playing_rds(self, name, snap):
        """An IQ recording's RDS, decoded as it plays. What is heard on the
        station it was made of goes into the list, and is kept: the PI and
        call sign at once, a name once it has held for ``NAME_STEADY_S``
        (a station scrolling words through its PS names each in turn)."""
        p = self._play
        if not p.is_iq:
            return
        station = self.engine.station_hz
        self._show_station(station, name)
        title, artist = snap['title'], snap['artist']
        self._show_rds_text(snap['radiotext'], ' - '.join(x for x in (artist, title) if x))
        rec = p.recording
        if abs(station - rec.station_hz) >= 50e3:
            return
        now = time.monotonic()
        if name != self._heard[0]:
            self._heard = (name, now)
        steady = name if now - self._heard[1] >= NAME_STEADY_S else ''
        heard = {'station_name': steady, 'pi': snap['pi_hex'],
                 'callsign': snap['callsign_confirmed']}
        new = {k: v for k, v in heard.items() if v and rec.info.get(k) != v}
        if not new:
            return
        try:
            library.learn(rec, **new)
        except OSError as exc:
            self._report(f'keeping what was heard: {exc}')
            return
        if 'station_name' in new or 'callsign' in new:
            for i in range(self.rec_list.count()):
                item = self.rec_list.item(i)
                if item.data(QtCore.Qt.UserRole) == rec.key:
                    item.setText(self._recording_text(rec))

    def _reveal_folder(self):
        folder = self._recording_dir()
        if not os.path.isdir(folder):
            self.lib_note.setText(_coloured(f"{folder} does not exist yet.", 'warn'))
            return
        Qt.QDesktopServices.openUrl(Qt.QUrl.fromLocalFile(folder))

    def _delete_clicked(self):
        rec = self._rec_sel
        if rec is None:
            return
        files = rec.files()
        answer = Qt.QMessageBox.question(
            self, "Delete recording",
            f"Delete the recording of {rec.station_hz / 1e6:.2f} MHz from "
            f"{rec.started.strftime('%b %d %H:%M')}?\n\n{len(files)} files, "
            f"{library.size_text(rec.bytes)}, in {os.path.dirname(rec.key)}.\n"
            "This cannot be undone.",
            Qt.QMessageBox.Yes | Qt.QMessageBox.Cancel, Qt.QMessageBox.Cancel)
        if answer == Qt.QMessageBox.Yes:
            self._delete_recording(rec)

    def _delete_recording(self, rec):
        if self._play is not None and self._play.recording.key == rec.key:
            self._stop_playback()
        failed = library.delete(rec)
        self._rec_sel = None
        self._refresh_library()
        if failed:
            self.lib_note.setText(_coloured("Could not delete " + "; ".join(failed), 'bad'))

    # ============================================================== timers
    def _tick_fast(self):
        try:
            running = self.engine.running
            if running and self._mode in ('receive', 'playback') and self.engine.rx:
                self._draw_receive()
            elif running and self._mode == 'playback' and self.engine.player:
                if not self._play.paused:
                    self._draw_wav()
            elif running and self._mode == 'sweep' and self.engine.sweeper:
                self._draw_sweep()
            if self._play is not None:
                self._follow_playback()
        except Exception:
            self._report('display')

    def _draw_receive(self):
        rx = self.engine.rx
        self._draw_rf(rx.rf_probe)
        mpx = rx.mpx_probe.snapshot()
        if mpx is not None:
            n = len(mpx)
            half = n // 2 + 1
            self.mpx_view.set_data(np.arange(half) * MPX_RATE / n, to_db(mpx[:half]),
                                   waterfall_row=False)
        peak, rms = rx.tap.levels()
        self.meter.set_levels(peak, rms)

    def _draw_rf(self, probe):
        """The band the radio streams, from a chain's spectrum tap."""
        e = self.engine
        rf = probe.snapshot()
        if rf is not None:
            n = len(rf)
            freqs = e.lo_hz + (np.arange(n) - n / 2) * e.rate / n
            db = to_db(rf)
            self._rx_sig = (freqs, db)
            self.rf_view.set_data(freqs, db)

    def _draw_sweep(self):
        sweeper = self.engine.sweeper
        freqs, live, done, serial = sweeper.snapshot()
        if len(live) != len(freqs) or (done is not None and len(done) != len(freqs)):
            return                                  # caught mid re-plan
        avg = max(1, int(self.rf_view.avg_knob.value()))
        new = done is not None and serial != self._last_serial
        # A radio's own sweep can outrun the screen (real time: 30 frames a
        # second, drawn 15): the most each point reached in all of them, so
        # a burst in a frame between draws still reaches the trace, peak
        # hold and waterfall.
        take = getattr(sweeper, 'take_held', None)
        held = take() if take is not None else None
        if held is not None and len(held) != len(freqs):
            held = None
        if new:
            self._last_serial = serial
            lin = 10 ** (done / 10)
            if self._sweep_avg is None or len(self._sweep_avg) != len(lin):
                self._sweep_avg = lin
            else:
                self._sweep_avg += (lin - self._sweep_avg) / avg
            self._sweep_db = to_db(self._sweep_avg)
            if getattr(sweeper.plan, 'auto_gain', False):
                self._follow_agc(done)
            # Every sweep reaches the waterfall: a row is the most of them
            # since the last row.
            got = held if held is not None else done
            if self._wf_hold is None or len(self._wf_hold) != len(got):
                self._wf_hold = got.copy()
            else:
                np.maximum(self._wf_hold, got, out=self._wf_hold)
        now = time.monotonic()
        row = None
        if new and now - self._last_wf > 0.1 and self._wf_hold is not None:
            self._last_wf = now
            row, self._wf_hold = self._wf_hold, None
        if avg <= 1:
            self.rf_view.set_data(freqs, held if held is not None else live,
                                  waterfall_row=False)
            if row is not None:
                self.rf_view.add_waterfall_row(freqs, row)
        elif self._sweep_db is not None and new:
            self.rf_view.set_data(freqs, self._sweep_db, waterfall_row=False)
            if row is not None:
                self.rf_view.add_waterfall_row(freqs, self._sweep_db)
        density = getattr(sweeper, 'density', None)
        if new and density is not None:
            frame = density()
            if frame is not None:
                self.rf_view.set_density(*frame)

    def _tick_slow(self):
        try:
            if self._rec_t0 is not None:
                self._recording_progress()
            self._collect_overview()
            self._watch_lost()                   # also once a reopen has failed
            if not self.engine.running:
                return
            self._check_health()
            if self._mode in ('receive', 'playback') and self.engine.rx is not None:
                self._refresh_receive()
            elif self._mode == 'playback' and self.engine.player is not None:
                self._refresh_wav()
            elif self._mode == 'sweep' and self.engine.sweeper is not None:
                self._refresh_sweep()
        except Exception:
            self._report('refresh')

    def _report(self, where):
        """Print a timer's exception once, with its traceback: a timer that
        raised every tick would bury the terminal."""
        import traceback
        text = traceback.format_exc()
        seen = self.__dict__.setdefault('_reported', set())
        if text not in seen:
            seen.add(text)
            print(f"FM receiver ({where}):\n{text}", file=sys.stderr)

    def _clip_counts(self):
        """On a radio with no overload flag: ('receive', full scale,
        samples) since the last call, of the IQ stream in Receive;
        ('sweep', worst step's share, its centre in Hz) for the
        last complete sweep; or None."""
        if self.radio is None or not self.radio.clip_warn:
            return None
        e = self.engine
        stream = e.rx if self._mode == 'receive' else None
        if self._opened_at is not None \
                and time.monotonic() - self._opened_at < OPEN_CLIP_GRACE_S:
            if stream is not None:
                stream.clip.take()                   # dropped, not shown
            return None
        if stream is not None:
            return ('receive',) + stream.clip.take()
        report = getattr(self.engine.sweeper, 'clip_report', None)
        if self._mode == 'sweep' and report is not None:
            got = report()
            return None if got is None else ('sweep',) + got
        return None

    def _check_health(self):
        health = self.radio.health() if self.radio else {}
        overload = health.get('overload', 0)
        fresh = max(0, overload - self._last_overload)
        if fresh:
            self._overload_until = time.monotonic() + 3.0
            self._overload_at = time.monotonic()
            self._clipped = 0.0
        self._last_overload = overload
        counts = self._clip_counts()
        now = time.monotonic()
        note = None                                  # (share, where)
        if counts is None:
            self._clip_smooth = None
        elif counts[0] == 'sweep':
            # Steady for a whole sweep: its worst step, not smoothed.
            _, share, where = counts
            self._clip_smooth = None
            note = (share, where)
            if share > CLIP_WARN:
                self._overload_until = now + 3.0
                self._clipped, self._clipped_at = share, where
        elif counts[2]:
            _, hit, n = counts
            clipped = hit / n
            if clipped > CLIP_WARN:
                self._overload_until = now + 3.0
                self._clipped, self._clipped_at = clipped, None
            k = 1.0 - math.exp(-(now - self._clip_t) / CLIP_TAU_S)
            self._clip_smooth = clipped if self._clip_smooth is None \
                else self._clip_smooth + k * (clipped - self._clip_smooth)
        if self._clip_smooth is not None:
            note = (self._clip_smooth, None)
        self._clip_t = now
        # AGC: an overload report, or this poll's clipped share; none at all
        # (just opened, no samples yet) is neither hot nor calm.
        if self.radio is not None and self.radio.has_agc:
            self._follow_iq_agc(fresh, now, calm=not fresh)
        elif counts is not None and counts[0] == 'receive' and counts[2]:
            share = counts[1] / counts[2]
            self._follow_iq_agc(int(share > CLIP_WARN), now, calm=share < CLIP_NOTE,
                                heavy=share > CLIP_HEAVY)
        else:
            self._follow_iq_agc(0, now, calm=False)
        text, token = self._running_text(), 'good'
        if note is not None:
            share, where = note
            text += f" - clipped {_share_text(share)}"
            if where is not None and share > 0:
                text += f" at worst, in the step centred on {_freq_text(where)}"
            if share >= CLIP_NOTE:
                token = 'warn'
        if now < self._overload_until:
            text, token = "Input overloaded - turn the RF gain down", 'bad'
            if self._iq_agc is not None and self._iq_agc.gain > 0:
                text = ("Input overloaded - AGC is turning the gain down "
                        f"({self.radio.gain_percent:.0f}%)")
            if self._clipped:
                text += f" ({_share_text(self._clipped)} of samples clipped"
                if self._clipped_at is not None:
                    text += f" in the step centred on {_freq_text(self._clipped_at)}"
                text += ")"
        elif health.get('usb_v', bb60_sweep.MIN_USB_VOLTAGE) < bb60_sweep.MIN_USB_VOLTAGE:
            text += (f" - USB voltage low ({health['usb_v']:.2f} V): "
                     "measurements may be off")
            token = 'warn'
        elif health.get('dropped'):
            text += f" - {health['dropped']} buffers dropped"
            token = 'warn'
        lost = self._lost_text()
        if lost is None:
            self._lost = None                    # it came back by itself
        else:
            if self._lost is None:
                self._lost = self._new_lost_watch()
            text, token = self._lost_status(lost), 'bad'
        self._set_status(text, token)
        # Only a radio that reports its health sets the tooltip: an error's
        # tooltip stays, and starting a mode clears it.
        if 'usb_v' in health:
            tip = (f"{self.radio.name}: {health['temp_c']:.1f} °C, "
                   f"USB {health['usb_v']:.2f} V, {health['usb_a']:.2f} A")
            if self.status.toolTip() != tip:
                self.status.setToolTip(tip)

    def _lost_text(self):
        """The status line for a radio that has stopped sending, or None.
        A radio that can tell why (``Engine.lost``) says so at once; any
        other is noticed when its samples stop for a while."""
        if self._mode not in ('sweep', 'receive') or self.radio is None:
            return None
        e = self.engine
        age = e.data_age()
        if age is None:
            return None
        if self._overload_at is not None \
                and time.monotonic() - self._overload_at < OVERLOAD_NOT_LOST_S:
            return None                          # overloaded, not gone: see the constant
        again = "press Stop, then Start, to open it again"
        lost = e.lost()
        if lost:
            return f"{self.radio.name} lost: {lost} - {again}"
        limit = STALL_S
        if e.mode == 'sweep' and getattr(e.sweeper, 'native', False):
            limit = max(limit, 3 * (e.sweeper.sweep_seconds or 0))
        if age < limit:
            return None
        text = (f"No samples from the {self.radio.name} for {age:.0f} s - "
                f"is it still connected? Then {again}")
        reason = e.lost_reason()
        return text + (f" ({reason})" if reason else "")

    def _new_lost_watch(self):
        return {'kind': self.radio_combo.currentData(), 'name': self.radio.name,
                'address': self.rtl_edit.text() if self._rtl_network else '',
                'checked': 0.0, 'gone': False, 'back': None, 'tries': 0}

    def _lost_status(self, text):
        """What to say about a lost radio: once it is seen gone from the
        USB, that it will open again by itself when it is back."""
        w = self._lost
        if w is not None and w['back'] is not None:
            return f"{w['name']} is back - opening it again..."
        if w is not None and w['gone']:
            return f"{w['name']} unplugged - plug it back in and it will open again by itself"
        return text

    def _watch_lost(self):
        """Open a lost radio again once it is back on the USB. Only one
        that was seen gone first: a radio that stops sending while still
        plugged in would stop again, so that is left to Stop and Start.
        A network radio can't be seen (``plugged_in`` is None)."""
        w = self._lost
        now = time.monotonic()
        if w is None or now - w['checked'] < LOST_POLL_S:
            return
        w['checked'] = now
        present = plugged_in(w['kind'], w['address'])
        if present is None:
            return
        if not present:
            w['gone'], w['back'] = True, None
            return
        if not w['gone']:
            return
        if w['back'] is None:
            w['back'] = now
            self._set_status(self._lost_status(''), 'warn')
            return
        if now - w['back'] < BACK_SETTLE_S:
            return
        w['tries'] += 1
        self._use_radio(w['kind'])               # forgets the watch
        if self.radio is None and w['tries'] < REOPEN_TRIES:
            w['back'] = now                      # not ready yet: again, later
            self._lost = w

    def _refresh_sweep(self):
        s = self.engine.sweeper
        plan = s.plan
        error = getattr(s, 'error', None)
        if error:
            self.sweep_info.setText(_coloured(f"{plan.describe()}<br>{error}", 'warn'))
        elif s.sweep_seconds and getattr(plan, 'realtime', False):
            self.sweep_info.setText(f"{plan.describe()}<br>"
                                    f"{1.0 / s.sweep_seconds:.0f} frames a second")
        elif s.sweep_seconds:
            rate = 1.0 / s.sweep_seconds if s.sweep_seconds > 0 else 0
            speed = (plan.stop_hz - plan.start_hz) / s.sweep_seconds
            speed = (f"{speed / 1e9:.1f} GHz/s" if speed >= 1e9 else f"{speed / 1e6:.0f} MHz/s")
            self.sweep_info.setText(
                f"{plan.describe()}<br>{s.sweep_seconds * 1e3:.0f} ms per sweep "
                f"({rate:.1f}/s, {speed})")
        if self._sweep_db is not None and self._last_serial != self._list_serial:
            self._list_serial = self._last_serial
            self._update_station_list(plan.freqs(), self._sweep_db)

    def _force_station_list(self):
        self._list_serial = -1

    def _update_station_list(self, freqs, db):
        # Only where FM broadcasting is: the floor is measured there too, not
        # over the gigahertz of a full-range sweep.
        freqs, db = np.asarray(freqs), np.asarray(db)
        if len(freqs) != len(db):
            return                                  # a sweep of the last plan
        fm = (freqs >= FM_BROADCAST_HZ[0]) & (freqs <= FM_BROADCAST_HZ[1])
        found = find_stations(freqs[fm], db[fm], min_snr_db=self.snr_spin.value()) \
            if fm.any() else []
        found.sort(key=lambda s: s[0])
        unit = self.rf_view.level_unit
        current = self.station_list.currentItem()
        keep = current.data(QtCore.Qt.UserRole) if current else None
        self.station_list.blockSignals(True)
        self.station_list.clear()
        for f, snr, level in found:
            name = self._names.get(round(f / 1e5))
            text = f"{f / 1e6:7.1f} MHz  {snr:5.1f} dB  {level:6.1f} {unit}"
            if name:
                text += f"  {name}"
            item = Qt.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, float(f))
            self.station_list.addItem(item)
            if keep is not None and abs(keep - f) < 1:
                self.station_list.setCurrentItem(item)
        self.station_list.blockSignals(False)

    def _refresh_receive(self):
        rx = self.engine.rx
        stereo, phase, _ = rx.update_stereo()
        if not rx.pilot_locked():
            audio = _coloured("Mono - no stereo pilot", 'ink_2')
        elif not self.stereo_check.isChecked():
            audio = "Mono (stereo switched off)"
        else:
            kind = ('standard phase' if abs(math.degrees(phase) - 90) < 45
                    else 'cosine phase')
            audio = _coloured(f"Stereo - pilot locked ({kind})", 'good')
        if self.mute_btn.isChecked():
            audio += " - " + _coloured("muted", 'bad')
        self.stereo_label.setText(audio)
        self.sig_label.setText(self._signal_text(rx))
        self._refresh_rds(rx.rds.snapshot())

    def _signal_text(self, rx):
        power = rx.channel_power_db()
        text = f"{power:.1f} dBFS in channel"
        snr = self._snr_db()
        if snr is not None:
            token = 'good' if snr > 30 else 'warn' if snr > 15 else 'bad'
            text += f", {_coloured(f'{snr:.0f} dB', token)} above the floor"
        return text

    def _snr_db(self):
        """The channel's mean level over the spectrum's median, or None."""
        if self._rx_sig is None or self.engine.station_hz is None:
            return None
        freqs, db = self._rx_sig
        inside = np.abs(freqs - self.engine.station_hz) < self.chan_entry.value() / 2
        if not inside.any():
            return None
        return float(10 * np.log10(np.mean(10 ** (db[inside] / 10))) - np.median(db))

    def _refresh_rds(self, snap):
        name = snap['station_short'] or snap['station_name'] or ''
        self.lbl['station_name'].setText(name or '(waiting)')
        if name:
            self._names[round(self.engine.station_hz / 1e5)] = name
        title, artist = snap['title'], snap['artist']
        self.lbl['nowplaying'].setText(
            ' - '.join(x for x in (artist, title) if x) if (title or artist) else '-')
        pi_text = snap['pi_hex'] or '-'
        if snap['callsign_confirmed']:
            pi_text += f"  ({snap['callsign_confirmed']})"
        elif snap['callsign']:
            pi_text += f"  (maybe {snap['callsign']})"
        self.lbl['pi'].setText(pi_text)
        self.lbl['pty'].setText(snap['pty'] or '-')
        self.lbl['ps'].setText(snap['ps'] or '-')
        self.lbl['radiotext'].setText(snap['radiotext'] or '-')
        flags = []
        if snap['tp']:
            flags.append("Traffic program")
        if snap['ta']:
            flags.append("Traffic announcement")
        if snap.get('has_tmc'):
            flags.append("TMC")
        self.lbl['flags'].setText(", ".join(flags) or '-')
        clock = snap['clock']
        if clock:
            ago = clock['synced_ago_s']
            ago = f"{ago:.0f} s" if ago < 120 else f"{ago / 60:.0f} min"
            self.lbl['clock'].setText(f"{clock_text(clock)}   synced {ago} ago")
        else:
            self.lbl['clock'].setText('-')
        if snap['blocks_seen']:
            self.lbl['quality'].setText(
                f"{snap['groups']} groups, "
                f"{100 * (1 - (snap['block_error_rate'] or 0)):.0f}% blocks good")
        else:
            self.lbl['quality'].setText('searching...')
        if self._rec_info is not None:
            self._rec_info.note_rds(snap)
        if self._mode == 'playback' and self._play is not None:
            self._show_playing_rds(name, snap)

    # ============================================================ closing
    def _restore_geometry(self):
        geometry = self.cfg.get('geometry')
        restored = False
        if geometry:
            try:
                restored = self.restoreGeometry(QtCore.QByteArray.fromHex(geometry.encode()))
            except Exception:
                restored = False
        if not restored:
            self.resize(1560, 980)
        for name, split in (('main', self.main_split), ('right', self.right_split),
                            ('rf', getattr(self.rf_view, 'splitter', None))):
            state = self.cfg['splitters'].get(name)
            if split is not None and state:
                try:
                    split.restoreState(QtCore.QByteArray.fromHex(state.encode()))
                except Exception:
                    pass

    def save_settings(self):
        self._remember_radio_settings()
        self._save_view()
        # In Recordings the tuner shows the recording: keep the radio's own.
        live = self._live or {}
        self.cfg.update({
            'mode': self._tab_mode(),
            'frequency_mhz': live.get('tuner', self.tuner.value()) / 1e6,
            'center_mhz': live.get('center', self.center_entry.value()) / 1e6,
            'step_khz': STEPS_KHZ[int(round(self.step_knob.value()))],
            'usrp_address': self.usrp_edit.text().strip(),
            'channel_bw_khz': int(round(self.chan_entry.value() / 1e3)),
            'region': self.region_combo.currentData(),
            'stereo': self.stereo_check.isChecked(),
            'volume': self.volume_knob.value(),
            'muted': (bool(self.cfg['muted']) if self._flag_muted
                      else self.mute_btn.isChecked()),
            'sweep_start_mhz': self.sweep_start.value() / 1e6,
            'sweep_stop_mhz': self.sweep_stop.value() / 1e6,
            'sweep_rbw_khz': (self.rbw_combo.currentData() or 0.0) / 1e3,
            # Kept for --realtime while the button is hidden.
            'sweep_realtime': (self.rt_btn.isChecked() if self.args.realtime
                               else self.cfg['sweep_realtime']),
            'sweep_fft': self.fft_combo.currentData(),
            'sweep_frames': self.frames_spin.value(),
            'min_snr_db': self.snr_spin.value(), 'snap': self.snap_check.isChecked(),
            'record_audio': self.rec_audio.isChecked(),
            'record_iq_channel': self.rec_channel.isChecked(),
            'record_iq_band': self.rec_band.isChecked(),
            'view_mpx': self.mpx_view.state(),
            'play_loop': self.loop_check.isChecked(),
            'geometry': bytes(self.saveGeometry().toHex()).decode(),
            'splitters': {name: bytes(split.saveState().toHex()).decode()
                          for name, split in (('main', self.main_split),
                                              ('right', self.right_split),
                                              ('rf', self.rf_view.splitter))},
        })
        try:
            update_config(self.cfg)
        except Exception as exc:
            print(f"FM receiver: could not save settings: {exc}", file=sys.stderr)

    def closeEvent(self, event):
        self.fast_timer.stop()
        self.slow_timer.stop()
        self._stop_recording("closing")
        if not self.args.no_save:
            self.save_settings()
        try:
            self.engine.close()
        except Exception as exc:
            print(f"FM receiver: closing the radio: {exc}", file=sys.stderr)
        self._pool.shutdown(wait=False)
        event.accept()


# ================================================================== main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog='fm-receiver',
        description="FM receiver: wideband FFT sweep, and FM stereo + RDS "
                    "reception with recording.")
    ap.add_argument('--radio', choices=RADIO_ORDER,
                    help="radio to open (default: the last one used, else "
                         "whichever is plugged in)")
    ap.add_argument('--usrp-address', help="a USRP's IP address")
    ap.add_argument('--rtl-address', metavar='HOST[:PORT]',
                    help="use an RTL-SDR on another computer: an ssh host to "
                         "start rtl_tcp on ('' for this computer). Also shows "
                         "the address box next to the Radio list; without "
                         "this flag the RTL-SDR is always this computer's")
    ap.add_argument('--file', help="play an IQ recording (implies --radio file)")
    ap.add_argument('--freq', type=float, metavar='MHZ', help="station to tune")
    ap.add_argument('--mode', choices=TAB_MODES, help="tab to start in")
    ap.add_argument('--sweep', type=float, nargs=2, metavar=('START', 'STOP'),
                    help="sweep span in MHz")
    ap.add_argument('--theme', choices=list(theme.NAMES))
    ap.add_argument('--realtime', action='store_true',
                    help="show the Sweep tab's Real time button (a BB60D, not "
                         "on a Mac): 27 MHz with nothing missed, and a density "
                         "map - Receive at 40 MS/s is otherwise much the same")
    ap.add_argument('--no-audio', '--mute', dest='no_audio', action='store_true',
                    help="start muted; press Mute (Ctrl+M) to hear it. Not "
                         "saved: the next run starts as the settings say")
    # (testing) No sound card at all: the tests, and a computer without one.
    ap.add_argument('--no-sound-card', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--no-save', action='store_true',
                    help="do not save settings on exit")
    ap.add_argument('--no-control', action='store_true',
                    help="no control socket: fmctl can't reach this window")
    ap.add_argument('--screenshot', metavar='PNG',
                    help="(testing) save a picture of the window and quit, "
                         "after --quit-after seconds")
    ap.add_argument('--quit-after', type=float, metavar='S',
                    help="(testing) close by itself after S seconds")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = load_config()
    if args.usrp_address is not None:
        config['usrp_address'] = args.usrp_address
    Qt.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
    app.setApplicationName('fm-receiver')
    window = MainWindow(args, config)
    window.show()
    Qt.QTimer.singleShot(50, window.start_initial)
    control = None
    if not args.no_control:
        from .control import ControlServer
        control = ControlServer(window)
        control.start()

    # A Python signal handler only runs when Python does, and Qt's event
    # loop idles in C++: keep a timer ticking so Ctrl+C and SIGTERM close
    # the window - and the radio - properly. (An RF bench toolkit rule.)
    def stop(*_):
        window.close()
        app.quit()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    ticker = Qt.QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(200)

    if args.quit_after:
        def finish():
            if args.screenshot:
                window.grab().save(args.screenshot)
            stop()
        Qt.QTimer.singleShot(int(args.quit_after * 1000), finish)
    try:
        code = app.exec_()
    finally:
        ticker.stop()
        if control is not None:
            control.close()
        # However the loop ended, stop the flowgraph before Python tears
        # down: collecting a Python block under a running flowgraph aborts.
        try:
            window.engine.close()
        except Exception:
            pass
    return code


if __name__ == '__main__':
    sys.exit(main())
