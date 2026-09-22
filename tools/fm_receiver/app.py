"""The FM receiver window - it opens straight onto the radio, no launcher.

Two modes, one per tab, sharing the radio and the big spectrum view:

- **Sweep (FFT)** hops the radio across a span wider than it can see at
  once and stitches the FFTs (``sweep.py``). No demodulation, so it is
  cheap. The stations it finds are listed; double-click one, or the
  spectrum, to listen.
- **Receive (IQ)** runs the radio at a narrow IQ bandwidth and demodulates
  one station: stereo audio, RDS, the multiplex spectrum (``dsp.py``).

Everything else - gain, the view's dials, volume and mute, recording -
works on the running flowgraph without rebuilding it. The window's
settings are saved when it closes (``config.py``).
"""

import argparse
import math
import os
import signal
import sys
import time

import numpy as np  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from . import __version__, theme
from .config import default_recording_dir, load_config, update_config
from . import bb60_sweep
from .bb60_sweep import RBW_LADDER, RT_MAX_SPAN_HZ, NativeSweepPlan
from .dsp import CHANNEL_MAX_BW, MPX_RATE
from .engine import Engine
from .radios import (RADIO_NAMES, RadioError, detect_radios, make_radio,
                     rate_label)
from .rds_core import clock_text
from .recording import IqRecording, WavWriter, recording_base
from .style import apply_window_theme
from .sweep import SweepPlan, find_stations, to_db
from .widgets import (DigitEntry, Knob, LevelMeter, SpectrumView, StepRoller,
                      ThemeDisc, on_raster)

#: (name, start MHz, stop MHz); 'full' is the whole of the radio's sweep
#: range, whichever radio it is (9 kHz-6 GHz on the BB60D).
SWEEP_PRESETS = [('Full range of the radio', 'full', None),
                 ('FM broadcast 87.5-108', 87.5, 108.0),
                 ('FM Japan 76-95', 76.0, 95.0),
                 ('FM OIRT 65.8-74', 65.8, 74.0),
                 ('VHF 30-300', 30.0, 300.0),
                 ('Custom', None, None)]
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
RADIO_ORDER = ('hackrf', 'usrp', 'bb60', 'file')

DEFAULTS = {
    'radio': None, 'usrp_address': '', 'iq_file': '', 'mode': 'receive',
    'frequency_mhz': 98.7, 'center_mhz': None, 'step_khz': 100, 'gain': {}, 'receive_rate': {},
    'sweep_rate': {}, 'settle_ms': {}, 'channel_bw_khz': 200, 'region': 'RBDS',
    'stereo': True, 'volume': 60, 'muted': False, 'sweep_band': 'full',
    'sweep_start_mhz': 87.5, 'sweep_stop_mhz': 108.0, 'sweep_rbw_khz': 0,
    'sweep_realtime': False,
    'sweep_fft': 4096, 'sweep_frames': 16,
    'min_snr_db': 15, 'snap': True, 'record_audio': True,
    'record_iq_channel': False, 'record_iq_band': False, 'recording_dir': '',
    'view_receive': {'span_hz': 1.2e6, 'ref_db': -10, 'range_db': 110, 'avg': 4},
    # A span wider than any sweep: the Span dial clamps it to the whole sweep.
    'view_sweep': {'span_hz': 1e12, 'ref_db': -10, 'range_db': 110, 'avg': 1},
    'view_mpx': {'ref_db': -10, 'range_db': 100, 'avg': 6},
    'theme': 'slate', 'geometry': None, 'splitters': {},
}


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


def _coloured(text, token):
    return f"<span style='color:{theme.TOKENS[token]}'>{text}</span>"


def _freq_text(hz):
    """9 kHz, 87.5 MHz, 6 GHz - no more digits than it has."""
    for scale, unit in ((1e9, 'GHz'), (1e6, 'MHz'), (1e3, 'kHz')):
        if hz >= scale:
            return f"{hz / scale:.6g} {unit}"
    return f"{hz:.0f} Hz"


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
        self.setWindowTitle(f"FM Receiver {__version__}")
        self.engine = Engine(want_audio=not args.no_audio)
        self.radio = None
        self._mode = None
        self._wav = None
        self._iq_recs = []
        self._rec_t0 = None
        self._last_serial = -1
        self._list_serial = -1
        self._sweep_avg = None
        self._sweep_db = None
        self._last_wf = 0.0
        self._last_overload = 0
        self._overload_until = 0.0
        self._clipped = 0.0
        self._names = {}
        self._rx_sig = None
        self._starting = False

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
        self.file_btn = Qt.QPushButton("Open IQ file...")
        self.file_btn.clicked.connect(self._choose_file)
        row.addWidget(self.file_btn)
        self.run_btn = Qt.QPushButton("Stop")
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
        self.tabs = Qt.QTabWidget()
        self.tabs.addTab(self._build_sweep_tab(), "Sweep (FFT)")
        self.tabs.addTab(self._build_receive_tab(), "Receive (IQ)")
        self.tabs.setCurrentIndex(0 if self.cfg['mode'] == 'sweep' else 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        box.addWidget(self.tabs)
        box.addWidget(self._build_gain())
        box.addWidget(self._build_audio())
        box.addWidget(self._build_record())
        box.addStretch(1)
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
        self.left_panel.adjustSize()
        bar = self.left_scroll.verticalScrollBar().sizeHint().width()
        self._left_width = self.left_panel.sizeHint().width() + bar + 4
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
        page = Qt.QWidget()
        form = Qt.QFormLayout(page)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
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
        self.rt_check = Qt.QCheckBox("Real time (27 MHz or less)")
        self.rt_check.setChecked(bool(self.cfg['sweep_realtime']) and bb60_sweep.REALTIME_OK)
        self.rt_check.setToolTip(
            "Watch the span in real time instead of sweeping it: every sample is\n"
            "FFT'd, so nothing is missed - a burst of 307 us or more at 10 kHz RBW\n"
            "- and a density map behind the trace shows how often each level\n"
            "was hit. 30 frames a second; the FM band fits. Its RBW runs from\n"
            "2.47 to 631 kHz (Auto: 10 kHz). No listening meanwhile: the radio\n"
            "does one thing at a time." if bb60_sweep.REALTIME_OK else
            "Not on a Mac: Signal Hound's library for it sweeps and streams IQ,\n"
            "but has no real time.")
        self.rt_check.toggled.connect(lambda _: self._update_sweep_plan())
        form.addRow(self.rt_check)
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
        self._native_rows = (self.rbw_combo, self.rt_check)
        self._hop_rows = (self.sweep_rate_combo, self.fft_combo, self.frames_spin,
                          self.settle_spin)
        buttons = Qt.QHBoxLayout()
        self.pause_btn = Qt.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._pause_toggled)
        buttons.addWidget(self.pause_btn)
        self.listen_btn = Qt.QPushButton("Listen")
        self.listen_btn.setToolTip("Receive the selected station (or the marker).")
        self.listen_btn.clicked.connect(self._listen_selected)
        buttons.addWidget(self.listen_btn)
        form.addRow(buttons)
        self.sweep_info = _wrapping(Qt.QLabel(""))
        form.addRow(self.sweep_info)
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

    @staticmethod
    def _form(box):
        form = Qt.QFormLayout(box)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        return form

    def _build_radio_card(self):
        """The radio itself: where it is tuned and how much it takes in. The
        tuner moves inside that band."""
        box = Qt.QGroupBox("Radio")
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
        self.range_label = _wrapping(Qt.QLabel("-"))
        self.range_label.setTextFormat(QtCore.Qt.RichText)
        form.addRow("Tuner range:", self.range_label)
        self.rx_rate_combo = Qt.QComboBox()
        self.rx_rate_combo.setToolTip(
            "The radio's IQ bandwidth (sample rate) while receiving: how much of "
            "the band\nthe spectrum shows, and the tuner can reach, around the "
            "Center.")
        self.rx_rate_combo.activated.connect(lambda _: (self._update_folder_tip(),
                                                        self._restart_receive()))
        form.addRow("IQ bandwidth:", self.rx_rate_combo)
        return box

    def _build_tuner_card(self):
        """The station you hear, its step, and its channel filter."""
        box = Qt.QGroupBox("Tuner")
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
        tune.addWidget(self.tuner, 0, QtCore.Qt.AlignVCenter)
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
        return box

    def _build_rds_card(self):
        """The station as decoded: its name, how it is decoded, how well it
        is received, and the RDS in full."""
        box = Qt.QGroupBox("RDS")
        form = self._form(box)
        big = Qt.QFont()
        big.setPixelSize(19)
        big.setBold(True)
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)
        mono.setPixelSize(15)
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
        return box

    def _build_gain(self):
        box = Qt.QGroupBox("RF gain")
        row = Qt.QHBoxLayout(box)
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
        self.mute_btn.setChecked(bool(self.cfg['muted']))
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
        self.rf_view = SpectrumView("RF spectrum", unit='MHz', waterfall=True,
                                    min_span_hz=50e3,
                                    snap_hz=self._step_hz() if self.cfg['snap'] else None)
        self.rf_view.clicked.connect(self._rf_clicked)
        self.rf_view.activated.connect(self._rf_activated)
        self.rf_view.bandWheel.connect(self._band_wheel)
        self.rf_view.bandDragged.connect(self._band_dragged)
        self.rf_view.bandDragFinished.connect(self._band_drag_finished)
        self.rf_view.averageChanged.connect(self._rf_average_changed)
        # In real time the density map spans the view's scale: the device
        # makes it from the Ref level down the Range.
        for knob in (self.rf_view.ref_knob, self.rf_view.range_knob):
            knob.valueChanged.connect(self._view_scale_changed)
        self.right_split.addWidget(self.rf_view)

        self.bottom = Qt.QStackedWidget()
        # Sweep page: the stations found.
        stations = Qt.QGroupBox("Stations found (double-click to listen)")
        sbox = Qt.QVBoxLayout(stations)
        self.station_list = Qt.QListWidget()
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)
        self.station_list.setFont(mono)
        self.station_list.itemDoubleClicked.connect(self._station_activated)
        self.station_list.currentItemChanged.connect(self._station_selected)
        sbox.addWidget(self.station_list)
        self.bottom.addWidget(stations)
        # Receive page: the multiplex (RDS is in the Receive tab).
        self.mpx_view = SpectrumView(
            "FM multiplex - mono, pilot 19k, stereo 38k, RDS 57k", unit='kHz',
            waterfall=False, min_span_hz=5e3)
        self.mpx_view.averageChanged.connect(self._mpx_average_changed)
        self.rx_page = self.mpx_view
        self.bottom.addWidget(self.mpx_view)
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
        add("Ctrl+Left", lambda: self._step(-1))
        add("Ctrl+Right", lambda: self._step(1))
        add("Ctrl+Up", lambda: self.volume_knob.setValue(self.volume_knob.value() + 5))
        add("Ctrl+Down", lambda: self.volume_knob.setValue(self.volume_knob.value() - 5))

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
            self.tuner.setValue(self.args.freq * 1e6)
        if self.args.sweep:
            self._set_sweep_span(*self.args.sweep)
        if self.args.mode:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(0 if self.args.mode == 'sweep' else 1)
            self.tabs.blockSignals(False)
        self._use_radio(kind)

    # ============================================================== radio
    def _set_status(self, text, token=None):
        self.status.setText(_coloured(text, token) if token else text)

    def _use_radio(self, kind):
        """Stop, open the radio of ``kind`` and start in the current mode."""
        self._stop_recording("the radio changed")
        index = self.radio_combo.findData(kind)
        self.radio_combo.setCurrentIndex(max(0, index))
        self.usrp_edit.setVisible(kind == 'usrp')
        self.file_btn.setVisible(kind == 'file')
        self._remember_radio_settings()
        radio = make_radio(kind, self.usrp_edit.text(), self.cfg.get('iq_file', ''))
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
        if self.rx_rate_combo.count():
            self.cfg['receive_rate'][kind] = self.rx_rate_combo.currentData()
        if self.sweep_rate_combo.count():
            self.cfg['sweep_rate'][kind] = self.sweep_rate_combo.currentData()
        self.cfg['settle_ms'][kind] = self.settle_spin.value()

    def _load_radio_settings(self):
        radio = self.radio
        kind = radio.kind

        def fill(combo, rates, saved, default):
            combo.blockSignals(True)
            combo.clear()
            for rate in rates:
                combo.addItem(rate_label(rate), float(rate))
            pick = saved if saved in rates else default
            if pick in rates:
                combo.setCurrentIndex(list(rates).index(pick))
            combo.blockSignals(False)

        fill(self.rx_rate_combo, radio.receive_rates,
             self.cfg['receive_rate'].get(kind), radio.default_receive_rate)
        fill(self.sweep_rate_combo, radio.sweep_rates,
             self.cfg['sweep_rate'].get(kind), radio.default_sweep_rate)
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(self.cfg['gain'].get(kind, radio.default_gain)))
        self.gain_slider.blockSignals(False)
        self.gain_label.setText(f"{self.gain_slider.value()}%")
        radio.gain_percent = float(self.gain_slider.value())
        self.gain_slider.setEnabled(kind != 'file')
        self.settle_spin.blockSignals(True)
        self.settle_spin.setValue(float(self.cfg['settle_ms'].get(kind, radio.settle_ms)))
        self.settle_spin.blockSignals(False)
        low, high = radio.freq_range_hz
        self.tuner.set_range(low, high)
        self.center_entry.set_range(low, high)
        self._show_sweep_rows(radio.native_sweep)
        if self.cfg['sweep_band'] == 'full':
            self._set_sweep_span(*radio.sweep_range_hz, replan=False)
        else:
            self._limit_sweep_bounds()
            self._select_preset()
        if kind == 'file' and radio.meta and radio.meta.get('station_hz') \
                and radio.path != getattr(self, '_opened_file', None):
            # A new recording opens on the station it was made of; the same
            # one again (Stop, Start) stays where it was tuned.
            self.tuner.setValue(radio.meta['station_hz'])
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
        return 'sweep' if self.tabs.currentIndex() == 0 else 'receive'

    def _tab_changed(self, _index):
        self._start_mode(self._tab_mode())

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
        except Exception as exc:
            self._show_error(f"{self.radio.describe()} would not start: {exc}")
        finally:
            self._starting = False

    def _save_view(self):
        if self._mode == 'receive':
            self.cfg['view_receive'] = self.rf_view.state()
        elif self._mode == 'sweep':
            self.cfg['view_sweep'] = self.rf_view.state()

    # ---- receive
    def _receive_rate(self):
        data = self.rx_rate_combo.currentData()
        return float(data) if data else self.radio.default_receive_rate

    def _start_receive(self):
        self._save_view()
        self._mode = 'receive'
        self.rf_view.load_state(self.cfg['view_receive'])
        self.rf_view.set_level_unit('dBFS')
        self.rf_view.clear_density()
        self.engine.start_receive(
            self.tuner.value(), self._receive_rate(),
            center_hz=self.center_entry.value(),
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
        self.rec_btn.setEnabled(True)
        self._clear_rds_labels()
        self._set_status(self._running_text(), 'good')
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
        self.tuner.setValue(e.station_hz)
        self._show_range()

    def _show_range(self, at_edge=False):
        e = self.engine
        if self._mode != 'receive' or e.rx is None:
            self.range_label.setText('-')
            return
        low, high = e.tuner_range()
        text = f"{low / 1e6:.3f} - {high / 1e6:.3f} MHz"
        if self.radio is not None and self.radio.min_offset_hz:
            text += f", clear of the centre by {self.radio.min_offset_hz / 1e3:.0f} kHz"
        if at_edge:
            text = _coloured("The tuner stops at the edge of the band: move the "
                             "Center to go further.", 'warn')
            self._edge_timer.start(3000)
        self.range_label.setText(text)

    def _restart_receive(self):
        if self._mode == 'receive' and self.radio is not None:
            self._start_mode('receive')

    def _running_text(self):
        e = self.engine
        if e.mode == 'sweep' and getattr(e.sweeper, 'native', False):
            plan = e.sweeper.plan
            what = ("Watching {} to {} in real time" if getattr(plan, 'realtime', False)
                    else "Sweeping {} to {} in the radio")
            return (f"{self.radio.describe()} - "
                    + what.format(_freq_text(plan.start_hz), _freq_text(plan.stop_hz)))
        what = 'Sweeping' if e.mode == 'sweep' else 'Receiving'
        return f"{self.radio.describe()} - {what} at {rate_label(e.rate)}"

    def _show_audio_note(self):
        if self.args.no_audio:
            self.audio_note.setText("Sound output is off (started with the "
                                    "no-audio option); recording still works.")
        elif self.engine.audio_error:
            self.audio_note.setText(_coloured(
                f"No sound output: {self.engine.audio_error}", 'warn'))
        else:
            self.audio_note.setText("")

    # ---- sweep
    def _sweep_plan(self):
        start, stop = self.sweep_start.value(), self.sweep_stop.value()
        if self.radio.native_sweep:
            view = self.rf_view
            return NativeSweepPlan(start, stop, self.rbw_combo.currentData() or None,
                                   realtime=self.rt_check.isChecked(),
                                   ref_db=view.ref_knob.value(),
                                   scale_db=view.range_knob.value())
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

    def _enable_realtime(self, span):
        """Real time is for spans the API takes, where its library has it."""
        self.rt_check.setEnabled(bb60_sweep.REALTIME_OK and span <= RT_MAX_SPAN_HZ + 1)

    def _limit_sweep_bounds(self):
        """Each bound inside the radio's range, and short of the other."""
        span = self.sweep_stop.value() - self.sweep_start.value()
        self._enable_realtime(span)
        if self.radio is None:
            return
        low, high = self.radio.sweep_range_hz
        self.sweep_start.set_range(low, high - MIN_SWEEP_SPAN_HZ)
        self.sweep_stop.set_range(low + MIN_SWEEP_SPAN_HZ, high)
        self.sweep_start.set_range(low, self.sweep_stop.value() - MIN_SWEEP_SPAN_HZ)
        self.sweep_stop.set_range(self.sweep_start.value() + MIN_SWEEP_SPAN_HZ, high)
        span = self.sweep_stop.value() - self.sweep_start.value()
        self._enable_realtime(span)

    def _start_sweep(self):
        self._save_view()
        self._mode = 'sweep'
        self.rf_view.load_state(self.cfg['view_sweep'])
        plan = self._sweep_plan()
        self.engine.start_sweep(plan, frames=self.frames_spin.value(),
                                settle_ms=self.settle_spin.value())
        self._reset_sweep_display(plan, full_span=False)
        self.bottom.setCurrentIndex(0)
        self.rec_btn.setEnabled(False)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("Pause")
        self._set_status(self._running_text(), 'good')

    def _reset_sweep_display(self, plan, full_span=True):
        self._sweep_avg = None
        self._sweep_db = None
        self._last_serial = -1
        self._list_serial = -1
        self.rf_view.set_extent(plan.start_hz, plan.stop_hz, keep_span=not full_span)
        self.rf_view.set_band(None, None)
        self.rf_view.set_center_line(None)
        self.rf_view.set_tuner_range(None, None)
        self.rf_view.set_marker_auto(False)           # sweeping, the marker is the pick
        self.range_label.setText('-')
        self.rf_view.set_marker(self.tuner.value())
        self.rf_view.set_message('')
        self.rf_view.clear_peak()
        self.rf_view.set_level_unit('dBm' if getattr(plan, 'native', False) else 'dBFS')
        self.rf_view.clear_density()
        self.sweep_info.setText(plan.describe())

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
        if self._mode == 'sweep' and getattr(plan, 'realtime', False):
            self._bounds_timer.start()

    def _settle_changed(self, ms):
        if self.engine.sweeper is not None:
            self.engine.sweeper.set_settle_ms(ms)

    def _pause_toggled(self, paused):
        self.pause_btn.setText("Resume" if paused else "Pause")
        if self.engine.sweeper is not None:
            self.engine.sweeper.set_paused(paused)

    # ============================================================= tuning
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
        if self._mode == 'receive' and self.engine.rx is not None:
            moved = self.engine.tune(hz, follow=follow)
            self._after_retune(moved, hz, recentre, final)
            self.rf_view.tuner_moving()
        else:
            self.engine.tune(hz)
            self.tuner.setValue(hz)
            self.rf_view.set_marker(hz)

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
    def _gain_changed(self, value):
        self.gain_label.setText(f"{value}%")
        if self.radio is not None:
            self.radio.gain_percent = float(value)
            if self.engine.running:
                try:
                    self.radio.apply_gain(value)
                except Exception as exc:
                    self._set_status(f"Could not set gain: {exc}", 'warn')

    def _chan_bw_changed(self, hz):
        if self.engine.rx is not None:
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
        self.mute_btn.setText("Muted" if muted else "Mute")
        if self.engine.rx is not None:
            self.engine.rx.set_muted(muted)

    def _volume_changed(self, value):
        if self.engine.rx is not None:
            self.engine.rx.set_volume(value / 100.0)

    def _rf_average_changed(self, n):
        if self._mode == 'receive' and self.engine.rx is not None:
            self.engine.rx.rf_probe.set_alpha(1.0 / max(1, n))
        self._sweep_avg = None

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
        for view in (self.rf_view, self.mpx_view):
            view.restyle()
        for entry in (self.tuner, self.center_entry, self.chan_entry):
            entry.restyle()
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
        try:
            os.makedirs(folder, exist_ok=True)
            if self.rec_audio.isChecked():
                path = recording_base(folder, e.station_hz, 'audio') + '.wav'
                self._wav = WavWriter(path)
                rx.tap.set_writer(self._wav)
            if self.rec_channel.isChecked():
                rec = IqRecording(rx.channel_sink, 'channel', folder, rx.channel_rate,
                                  e.station_hz, e.station_hz, name)
                rec.start()
                self._iq_recs.append(rec)
            if self.rec_band.isChecked():
                rec = IqRecording(rx.band_sink, 'band', folder, e.rate, e.lo_hz,
                                  e.station_hz, name)
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
        if self._wav is None and not self._iq_recs:
            return
        saved = []
        if self._wav is not None:
            if self.engine.rx is not None:
                self.engine.rx.tap.set_writer(None)
            path = self._wav.close()
            note = f" ({self._wav.error})" if self._wav.error else ''
            saved.append(os.path.basename(path) + note)
            self._wav = None
        for rec in self._iq_recs:
            try:
                paths = rec.stop()
                saved.extend(os.path.basename(p) for p in paths)
            except Exception as exc:
                saved.append(f"IQ error: {exc}")
        self._iq_recs = []
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

    # ============================================================== timers
    def _tick_fast(self):
        try:
            if self.engine.running and self._mode == 'receive' and self.engine.rx:
                self._draw_receive()
            elif self.engine.running and self._mode == 'sweep' and self.engine.sweeper:
                self._draw_sweep()
        except Exception:
            self._report('display')

    def _draw_receive(self):
        e, rx = self.engine, self.engine.rx
        rf = rx.rf_probe.snapshot()
        if rf is not None:
            n = len(rf)
            freqs = e.lo_hz + (np.arange(n) - n / 2) * e.rate / n
            db = to_db(rf)
            self._rx_sig = (freqs, db)
            self.rf_view.set_data(freqs, db)
        mpx = rx.mpx_probe.snapshot()
        if mpx is not None:
            n = len(mpx)
            half = n // 2 + 1
            self.mpx_view.set_data(np.arange(half) * MPX_RATE / n, to_db(mpx[:half]),
                                   waterfall_row=False)
        peak, rms = rx.tap.levels()
        self.meter.set_levels(peak, rms)

    def _draw_sweep(self):
        freqs, live, done, serial = self.engine.sweeper.snapshot()
        if len(live) != len(freqs) or (done is not None and len(done) != len(freqs)):
            return                                  # caught mid re-plan
        avg = max(1, int(self.rf_view.avg_knob.value()))
        new = done is not None and serial != self._last_serial
        if new:
            self._last_serial = serial
            lin = 10 ** (done / 10)
            if self._sweep_avg is None or len(self._sweep_avg) != len(lin):
                self._sweep_avg = lin
            else:
                self._sweep_avg += (lin - self._sweep_avg) / avg
            self._sweep_db = to_db(self._sweep_avg)
        now = time.monotonic()
        row = new and now - self._last_wf > 0.1
        if row:
            self._last_wf = now
        if avg <= 1:
            self.rf_view.set_data(freqs, live, waterfall_row=False)
            if row:
                self.rf_view.add_waterfall_row(freqs, done)
        elif self._sweep_db is not None and new:
            self.rf_view.set_data(freqs, self._sweep_db, waterfall_row=row)
        density = getattr(self.engine.sweeper, 'density', None)
        if new and density is not None:
            frame = density()
            if frame is not None:
                self.rf_view.set_density(*frame)

    def _tick_slow(self):
        try:
            if self._rec_t0 is not None:
                self._recording_progress()
            if not self.engine.running:
                return
            self._check_health()
            if self._mode == 'receive' and self.engine.rx is not None:
                self._refresh_receive()
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

    def _check_health(self):
        health = self.radio.health() if self.radio else {}
        overload = health.get('overload', 0)
        if overload > self._last_overload:
            self._overload_until = time.monotonic() + 3.0
            self._clipped = 0.0
        self._last_overload = overload
        if self.radio is not None and self.radio.clip_warn \
                and self._mode == 'receive' and self.engine.rx is not None:
            clipped = self.engine.rx.clip.take()
            if clipped > CLIP_WARN:
                self._overload_until = time.monotonic() + 3.0
                self._clipped = clipped
        text, token = self._running_text(), 'good'
        if time.monotonic() < self._overload_until:
            text, token = "Input overloaded - turn the RF gain down", 'bad'
            if self._clipped:
                text += f" ({self._clipped * 100:.1f}% of samples clipped)"
        elif health.get('dropped'):
            text += f" - {health['dropped']} buffers dropped"
            token = 'warn'
        self._set_status(text, token)

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
        if self._rx_sig is not None:
            freqs, db = self._rx_sig
            e = self.engine
            bw = self.chan_entry.value()
            inside = np.abs(freqs - e.station_hz) < bw / 2
            if inside.any():
                snr = 10 * np.log10(np.mean(10 ** (db[inside] / 10))) - np.median(db)
                token = 'good' if snr > 30 else 'warn' if snr > 15 else 'bad'
                text += f", {_coloured(f'{snr:.0f} dB', token)} above the floor"
        return text

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
        self.cfg.update({
            'mode': self._tab_mode(), 'frequency_mhz': self.tuner.value() / 1e6,
            'center_mhz': self.center_entry.value() / 1e6,
            'step_khz': STEPS_KHZ[int(round(self.step_knob.value()))],
            'usrp_address': self.usrp_edit.text().strip(),
            'channel_bw_khz': int(round(self.chan_entry.value() / 1e3)),
            'region': self.region_combo.currentData(),
            'stereo': self.stereo_check.isChecked(),
            'volume': self.volume_knob.value(), 'muted': self.mute_btn.isChecked(),
            'sweep_start_mhz': self.sweep_start.value() / 1e6,
            'sweep_stop_mhz': self.sweep_stop.value() / 1e6,
            'sweep_rbw_khz': (self.rbw_combo.currentData() or 0.0) / 1e3,
            'sweep_realtime': self.rt_check.isChecked(),
            'sweep_fft': self.fft_combo.currentData(),
            'sweep_frames': self.frames_spin.value(),
            'min_snr_db': self.snr_spin.value(), 'snap': self.snap_check.isChecked(),
            'record_audio': self.rec_audio.isChecked(),
            'record_iq_channel': self.rec_channel.isChecked(),
            'record_iq_band': self.rec_band.isChecked(),
            'view_mpx': self.mpx_view.state(),
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
    ap.add_argument('--file', help="play an IQ recording (implies --radio file)")
    ap.add_argument('--freq', type=float, metavar='MHZ', help="station to tune")
    ap.add_argument('--mode', choices=('sweep', 'receive'), help="mode to start in")
    ap.add_argument('--sweep', type=float, nargs=2, metavar=('START', 'STOP'),
                    help="sweep span in MHz")
    ap.add_argument('--theme', choices=list(theme.NAMES))
    ap.add_argument('--no-audio', action='store_true',
                    help="no sound output (recording still works)")
    ap.add_argument('--no-save', action='store_true',
                    help="do not save settings on exit")
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
        # However the loop ended, stop the flowgraph before Python tears
        # down: collecting a Python block under a running flowgraph aborts.
        try:
            window.engine.close()
        except Exception:
            pass
    return code


if __name__ == '__main__':
    sys.exit(main())
