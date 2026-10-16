"""The window's ATSC television tab, and the picture it shows.

A mixin of ``app.MainWindow``. The tab sits beside RDS and HD Radio under
the Tuner; choosing it retunes the radio to a television channel at an IQ
rate that holds six megahertz, and where the FM multiplex was, under the RF
spectrum, the picture comes up: the multiplex fades out, the splitter glides
the picture's share open. Leaving the tab does it all in reverse and puts
the FM station back.

The decoding is ``atsc.AtscRadio`` (the flowgraph and ffmpeg); this is the
look of it.
"""

import time

from PyQt5 import Qt, QtCore  # type: ignore

from . import theme
from .atsc import MIN_RATE, VIDEO_H, VIDEO_W
from .atsc_core import channel_center_mhz, channel_for_center, tv_channel_items
from .widgets import fade

#: The tab's place among the station tabs.
ATSC_TAB = 2
#: How long the multiplex and the picture trade places, and the splitter
#: takes to move.
SWAP_MS = 320
#: The picture's share of the right-hand column's height.
VIDEO_SHARE = 0.56
DEFAULT_CHANNEL = 14


def _app():
    from . import app                       # at call time: app imports this
    return app


class VideoView(Qt.QWidget):
    """The newest picture, kept in its proportions on black, with what the
    receiver is doing written over it while there is no picture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(Qt.QSizePolicy.Expanding, Qt.QSizePolicy.Expanding)
        self._image = None
        self._bytes = None
        self._text = ''
        self._detail = ''

    def set_frame(self, rgb):
        """A ``VIDEO_W`` x ``VIDEO_H`` RGB picture."""
        self._bytes = rgb                    # the image reads from this: keep it
        self._image = Qt.QImage(rgb, VIDEO_W, VIDEO_H, 3 * VIDEO_W,
                                Qt.QImage.Format_RGB888)
        self.update()

    def clear_frame(self):
        if self._image is not None:
            self._image = None
            self._bytes = None
            self.update()

    def set_message(self, text, detail=''):
        if (text, detail) != (self._text, self._detail):
            self._text, self._detail = text, detail
            self.update()

    def paintEvent(self, event):
        try:
            p = Qt.QPainter(self)
            p.fillRect(self.rect(), Qt.QColor('#000000'))
            if self._image is not None:
                w, h = self.width(), self.height()
                scale = min(w / VIDEO_W, h / VIDEO_H)
                tw, th = int(VIDEO_W * scale), int(VIDEO_H * scale)
                p.setRenderHint(Qt.QPainter.SmoothPixmapTransform, True)
                p.drawImage(QtCore.QRect((w - tw) // 2, (h - th) // 2, tw, th),
                            self._image)
            if self._text:
                p.setPen(Qt.QColor(theme.TOKENS['ink_2']))
                font = p.font()
                font.setPixelSize(16)
                p.setFont(font)
                p.drawText(self.rect().adjusted(0, 0, 0, -14), QtCore.Qt.AlignCenter,
                           self._text)
                if self._detail:
                    font.setPixelSize(12)
                    p.setFont(font)
                    p.drawText(self.rect().adjusted(0, 34, 0, 0),
                               QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                               self._detail)
            p.end()
        except Exception as exc:              # a paint that raises aborts the app
            print(f"video: {exc}")


class AtscWindow:
    """Methods of the main window. State lives on the window."""

    # ------------------------------------------------------------ build
    def _atsc_init(self):
        self._atsc_on = False                 # the tab is on show
        self._atsc_fm = None                  # the FM tuner and Center, kept meanwhile
        self._atsc_programs = ()
        self._atsc_widgets = {}
        self._swap_anim = None
        self._split_anim = None
        self._split_before = None

    def _build_atsc_page(self):
        """What the station says, as RDS and HD Radio's tabs do: the channel
        to receive, the programs it carries, what is on, how it is received."""
        app = _app()
        page = Qt.QWidget()
        form = self._form(page)
        form.setColumnStretch(1, 1)
        self._atsc_form = form
        self.atsc_lbl = {}
        big = Qt.QFont()
        big.setPixelSize(19)
        big.setBold(True)

        self.atsc_channel_combo = Qt.QComboBox()
        self.atsc_channel_combo.setToolTip(
            "The RF channel to receive: a 6 MHz block of the UHF/VHF television "
            "plan.\nThe radio is centred on it, at an IQ rate that holds all of it.")
        self._atsc_channels = tv_channel_items()
        for number, centre, caption in self._atsc_channels:
            self.atsc_channel_combo.addItem(caption, number)
        self.atsc_channel_combo.setMaxVisibleItems(16)
        self.atsc_channel_combo.activated.connect(
            lambda _i: self.atsc_select_channel(self.atsc_channel_combo.currentData()))
        form.addRow("Channel:", self.atsc_channel_combo)

        rows = (('station', "Station:"), ('signal', "Signal:"), ('programs', "Programs:"),
                ('now', "On now:"), ('next', "Next:"), ('video', "Picture:"),
                ('audio', "Sound:"))
        for key, caption in rows:
            if key == 'programs':
                holder = Qt.QWidget()
                holder.setObjectName('atscPrograms')
                holder.setStyleSheet("QWidget#atscPrograms { background: transparent; }")
                holder.setSizePolicy(Qt.QSizePolicy.Expanding, Qt.QSizePolicy.Preferred)
                self._atsc_grid = Qt.QGridLayout(holder)
                self._atsc_grid.setContentsMargins(0, 0, 0, 0)
                self._atsc_grid.setSpacing(6)
                self.atsc_lbl[key] = holder
                form.addRow(caption, holder)
                continue
            label = app._wrapping(Qt.QLabel("-"))
            label.setTextFormat(QtCore.Qt.RichText)
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            if key == 'station':
                label.setFont(big)
            self.atsc_lbl[key] = label
            form.addRow(caption, label)
        self._level_label(form, self.atsc_lbl['station'])
        self.atsc_lbl['programs'].setToolTip("Choose which program to watch and hear.")
        return page

    def _build_video_view(self):
        self.video_view = VideoView()
        self.video_timer = Qt.QTimer(self)
        self.video_timer.timeout.connect(self._tick_video)
        return self.video_view

    # ------------------------------------------------------------ tab
    def _atsc_rate(self):
        """The lowest IQ rate of the radio that holds a 6 MHz channel, or
        None when it has none."""
        radio = self.radio
        if radio is None:
            return None
        rates = [r for r in radio.usable_receive_rates()
                 if r >= MIN_RATE] if hasattr(radio, 'usable_receive_rates') else []
        if not rates and getattr(radio, 'receive_rates', None):
            rates = [r for r in radio.receive_rates if r >= MIN_RATE]
        return min(rates) if rates else None

    def _atsc_channel(self):
        number = self.cfg.get('atsc_channel')
        if number:
            return int(number)
        near = channel_for_center(self.tuner.value() / 1e6, tolerance=3.0)
        return near or DEFAULT_CHANNEL

    def _atsc_tab_changed(self, index):
        """The station tab changed: into ATSC, or out of it."""
        into = index == ATSC_TAB
        if into == self._atsc_on:
            return
        self._atsc_on = into
        if self._mode != 'receive' or self.radio is None or self._starting:
            return                             # applied when Receive starts
        if into and self._atsc_rate() is None:
            self._atsc_note()
            return
        if not into:
            self._atsc_restore_entries()
        self._atsc_animate = True
        self._start_mode('receive')
        self._atsc_animate = False

    def _atsc_note(self):
        """Why the picture cannot come up."""
        radio = self.radio
        if radio is None:
            text = "Open a radio first"
        elif self._atsc_rate() is None:
            text = (f"{radio.describe()} cannot stream 6 MHz: television needs an IQ "
                    f"rate of {MIN_RATE / 1e6:g} MS/s or more")
        else:
            return
        self.video_view.set_message("No picture", text)

    def _atsc_tune_entries(self):
        """Receive is about to start with the tab on show: Center and tuner
        onto the channel, the FM ones kept to put back."""
        if not self._atsc_on or self.radio is None or self.radio.kind == 'file':
            return
        if self._atsc_rate() is None:
            return
        if self._atsc_fm is None:
            self._atsc_fm = (self.tuner.value(), self.center_entry.value())
        centre = channel_center_mhz(self._atsc_channel()) * 1e6
        self.center_entry.setValue(centre)
        self._set_tuner(centre)

    def _atsc_restore_entries(self):
        if self._atsc_fm is not None:
            tuner, centre = self._atsc_fm
            self._atsc_fm = None
            self.center_entry.setValue(centre)
            self._set_tuner(tuner)

    def _atsc_active(self):
        """Receive runs the television chain: the tab is on show and the
        radio's rate holds a channel."""
        e = self.engine
        return bool(self._atsc_on and self.radio is not None
                    and e.rx is not None and getattr(e.rx, 'atsc', None) is not None)

    def atsc_select_channel(self, number):
        """Receive RF channel ``number``."""
        number = int(number)
        channel_center_mhz(number)             # ValueError for a non-channel
        self.cfg['atsc_channel'] = number
        index = self.atsc_channel_combo.findData(number)
        if index >= 0 and index != self.atsc_channel_combo.currentIndex():
            self.atsc_channel_combo.setCurrentIndex(index)
        self.engine.atsc.choose(None)
        if self._atsc_on and self._mode == 'receive' and self.radio is not None \
                and self.radio.kind != 'file':
            self._atsc_fm = self._atsc_fm or (self.tuner.value(), self.center_entry.value())
            self._start_mode('receive')

    def atsc_select_program(self, program):
        self.engine.atsc.choose(program)
        self._refresh_atsc()

    # ------------------------------------------------------------ swap
    def _show_bottom(self, television, animate):
        """The multiplex (or, in Receive for television, the picture) under
        the RF spectrum. Changing over, one fades into the other and the
        splitter glides, rather than jumping."""
        target = self.video_view if television else self.rx_page
        if self.bottom.currentWidget() is target:
            return
        ms = SWAP_MS if (animate and self.bottom.isVisible()) else 0
        overlay = None
        if ms:
            overlay = Qt.QLabel(self.bottom)
            overlay.setPixmap(self.bottom.grab())
            overlay.setGeometry(self.bottom.rect())
            overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            overlay.show()
            overlay.raise_()
        self.bottom.setCurrentWidget(target)
        self.bottom.setVisible(True)
        if overlay is not None:
            fade(overlay, False, ms, done=overlay.deleteLater)
        self._glide_split(television, ms)
        if television:
            self.video_timer.start(33)
        else:
            self.video_timer.stop()
            self.video_view.clear_frame()

    def _glide_split(self, television, ms):
        split = self.right_split
        total = sum(split.sizes()) or split.height()
        if television:
            if self._split_before is None:
                self._split_before = split.sizes()
            bottom = int(total * VIDEO_SHARE)
            end = [total - bottom, bottom]
        else:
            end = self._split_before or split.sizes()
            self._split_before = None
        if not ms or len(end) != 2:
            split.setSizes(end)
            return
        start = split.sizes()
        if self._split_anim is not None:
            self._split_anim.stop()
        anim = QtCore.QVariantAnimation(self)
        anim.setDuration(ms)
        anim.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def step(f):
            try:
                split.setSizes([int(a + (b - a) * f) for a, b in zip(start, end)])
            except Exception as exc:           # never abort the app
                print(f"splitter: {exc}")

        anim.valueChanged.connect(step)
        self._split_anim = anim
        anim.start()

    # ------------------------------------------------------------ refresh
    def _tick_video(self):
        try:
            video = self.engine.atsc.video
            frame = video.frame() if video is not None else None
            if frame is None:
                return
            rgb, number = frame
            if number != getattr(self, '_video_shown', None):
                self._video_shown = number
                self.video_view.set_frame(rgb)
        except Exception:
            self._report('video')

    def _refresh_atsc(self):
        """From the slow timer in Receive: poll the decoder, say what it
        found."""
        app = _app()
        coloured = app._coloured
        radio = self.engine.atsc
        lbl = self.atsc_lbl
        if not self._atsc_active():
            return
        st = radio.poll()
        number = st['channel']
        index = self.atsc_channel_combo.findData(number) if number else -1
        if index >= 0 and index != self.atsc_channel_combo.currentIndex():
            self.atsc_channel_combo.setCurrentIndex(index)

        programs = st['programs']
        chosen = next((p for p in programs if p['program'] == st['program']), None)
        shown = chosen or (programs[0] if programs else None)
        # Station
        if chosen:
            who = " ".join(x for x in (chosen['number'], chosen['name']) if x) \
                or f"Program {chosen['program']}"
            lbl['station'].setText(who)
        else:
            lbl['station'].setText('-')
        # Signal
        lock = st['lock']
        # The verdict first; what it rests on under it: MER, pilot, level.
        if lock == 'locked':
            bad = st['bad_pct']
            token = 'good' if (bad or 0) < 1 else 'warn' if bad < 10 else 'bad'
            text = (f"{coloured('Locked', 'good')} - "
                    f"{coloured(f'{bad:.1f}% bad packets', token)}")
            more = [f"MER {st['mer_db']:.1f} dB", f"pilot {st['pilot_hz']:+.0f} Hz"]
        elif lock == 'pilot only':
            text = coloured(f"Pilot found ({st['prominence_db']:.0f} dB), not decoding yet", 'warn')
            more = []
        elif lock == 'searching':
            text = coloured("Looking for the channel...", 'ink_2')
            more = []
        else:
            text = coloured("No television signal on this channel", 'ink_2')
            more = []
        if st['level_dbfs'] is not None:
            more.append(f"{st['level_dbfs']:.0f} dBFS")
        if more:
            text += "<br>" + " \u00b7 ".join(more)
        lbl['signal'].setText(text)
        self._atsc_buttons(programs, st['program'])
        # On now / next / formats, of the program on show
        import html
        lbl['now'].setText(html.escape(shown['now']) if shown and shown['now'] else '-')
        lbl['next'].setText(html.escape(shown['next']) if shown and shown['next'] else '-')
        lbl['video'].setText(html.escape(shown['format']) if shown and shown['format'] else '-')
        lbl['audio'].setText(html.escape(shown['audio']) if shown and shown['audio'] else '-')
        # Over the picture, until there is one
        if st['picture']:
            self.video_view.set_message('')
        elif lock == 'locked':
            self.video_view.set_message("Starting the picture...",
                                        "waiting for the stream's first full frame")
        elif lock == 'pilot only':
            self.video_view.set_message("Pilot found, not decoding yet",
                                        "a weak signal or a mistuned one - try the antenna")
        elif lock == 'searching':
            self.video_view.set_message("Looking for the channel...")
        else:
            self.video_view.set_message("No signal", "no television pilot on this channel")
        if radio.video is None:
            self.video_view.set_message("ffmpeg is not installed",
                                        "it turns the stream into a picture")
        if self.tabs_station_current() == ATSC_TAB:
            self.station_tabs.refit()

    def tabs_station_current(self):
        return self.station_tabs.currentIndex()

    def _atsc_buttons(self, programs, playing):
        """One button a program, as HD Radio's: the one playing lit. New
        ones fade in; a list that changed is rebuilt."""
        key = tuple((p['program'], p['number'], p['name'], p['video']) for p in programs)
        if key != self._atsc_programs:
            self._atsc_programs = key
            for button in self._atsc_widgets.values():
                self._atsc_grid.removeWidget(button)
                button.deleteLater()
            self._atsc_widgets = {}
            shown = [p for p in programs if p['video']] or programs
            for i, p in enumerate(shown):
                text = " ".join(x for x in (p['number'], p['name']) if x) \
                    or f"Program {p['program']}"
                button = Qt.QPushButton(text)
                button.setObjectName('small')
                button.setCheckable(True)
                button.setToolTip(p['streams'] or '')
                button.clicked.connect(
                    lambda _c, n=p['program']: self.atsc_select_program(n))
                self._atsc_grid.addWidget(button, i // 2, i % 2)
                self._atsc_widgets[p['program']] = button
                button.hide()
                fade(button, True)
        for number, button in self._atsc_widgets.items():
            lit = number == playing
            if button.isChecked() != lit:
                button.setChecked(lit)

    def atsc_status(self):
        """For fmctl."""
        radio = self.engine.atsc
        st = dict(radio.state)
        st['active'] = self._atsc_active()
        st['tab_on'] = self._atsc_on
        st['ffmpeg'] = bool(radio.ffmpeg)
        video = radio.video
        st['frames'] = video.frames_seen if video is not None else 0
        st['dropped_bytes'] = st.pop('dropped', 0)
        return st
