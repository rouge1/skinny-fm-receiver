"""The window's own controls: knobs, the spectrum view, the level meters.

**An exception in a Qt override aborts the whole program** (an RF bench
toolkit rule, learnt from a paintEvent): every paint here is guarded, and a
size or value that can be zero is checked before it divides anything.
"""

import math
import time

import numpy as np  # type: ignore
import pyqtgraph as pg  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore
from PyQt5.QtCore import pyqtSignal  # type: ignore

from . import theme
from .style import colour

pg.setConfigOptions(antialias=False, imageAxisOrder='row-major')


# -------------------------------------------------------------------- knob

def _mix(a, b, f):
    """QColor ``f`` of the way from ``a`` to ``b``."""
    a, b = Qt.QColor(a), Qt.QColor(b)
    return Qt.QColor.fromRgbF(*(x + (y - x) * f for x, y in zip(a.getRgbF(), b.getRgbF())))


class _Dial(Qt.QDial):
    """A QDial drawn as a knob in the theme, turned by dragging up and down.

    Qt's own dial follows the pointer round its centre, which on a small
    knob is fiddly; vertical drag is what knobs on instruments do. Double
    click puts it back to its default.

    Under the pointer - where the wheel turns it - the knob lights: its rim
    and pointer turn the on-air orange, and it glows orange round its edge
    on the dark themes (Slate, Walnut), as the RF bench toolkit's tiles
    are lit, or lifts on a shadow on the light one (Reading Room), where a
    glow barely shows on the paper. It fades in and out over ``GLOW_MS``,
    and stays while the knob is dragged. Clicked focus does not light it -
    a clicked knob kept its light long after, and then showed no change
    under the pointer; a ring marks focus that came by Tab.
    """

    reset = pyqtSignal()
    #: Whole wheel notches over the knob, and whether Shift was held.
    wheeled = pyqtSignal(int, bool)

    #: The toolkit's tiles lift in 150 ms.
    GLOW_MS = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setNotchesVisible(False)
        self.setWrapping(False)
        self.setFixedSize(46, 46)
        self._press = None
        self._wheel = 0
        self._tab_focus = False
        # The window's click-to-move guard, which holds the wheel back from
        # an unclicked control, leaves a knob be.
        self.setProperty('wheel_on_hover', True)
        self.glow = 0.0
        self._glow_to = 0.0
        self._effect = Qt.QGraphicsDropShadowEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._glow_anim = QtCore.QVariantAnimation(self)
        self._glow_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._glow_anim.valueChanged.connect(self._set_glow)

    # -- the light under the pointer
    def _aim_glow(self):
        on = self.isEnabled() and (self.underMouse() or self._press is not None)
        target = 1.0 if on else 0.0
        if target == self._glow_to:
            return
        self._glow_to = target
        self._glow_anim.stop()
        self._glow_anim.setStartValue(self.glow)
        self._glow_anim.setEndValue(target)
        self._glow_anim.setDuration(max(1, int(self.GLOW_MS * abs(target - self.glow))))
        self._glow_anim.start()

    def _set_glow(self, level):
        self.glow = min(1.0, max(0.0, float(level)))
        effect = self._effect
        if self.glow <= 0.0:
            effect.setEnabled(False)
        else:
            t = theme.TOKENS
            if t['scheme'] == 'light':
                tint = Qt.QColor(t['shade'])
                tint.setAlphaF(0.7 * self.glow)
                effect.setOffset(0, 1 + 2 * self.glow)
                effect.setBlurRadius(4 + 12 * self.glow)
            else:
                tint = Qt.QColor(t['live'])
                tint.setAlphaF(self.glow)
                effect.setOffset(0, 0)
                effect.setBlurRadius(6 + 16 * self.glow)
            effect.setColor(tint)
            effect.setEnabled(True)
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._aim_glow()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._aim_glow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.EnabledChange:
            self._aim_glow()

    def focusInEvent(self, event):
        self._tab_focus = event.reason() in (QtCore.Qt.TabFocusReason,
                                             QtCore.Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._tab_focus = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        # QDial's own wheel moves a few of its thousand steps a notch - too
        # little to see. The knob decides how far a notch goes. Always
        # taken, so the column it sits in does not scroll instead.
        event.accept()
        if self.isEnabled():
            steps = _wheel_steps(event, '_wheel', self)
            if steps:
                self.wheeled.emit(steps, bool(event.modifiers() & QtCore.Qt.ShiftModifier))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.setFocus(QtCore.Qt.MouseFocusReason)
            self._press = (event.pos().y(), self.value())
            self.setSliderDown(True)
            self._aim_glow()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & QtCore.Qt.LeftButton:
            y0, v0 = self._press
            span = self.maximum() - self.minimum()
            fine = 4.0 if event.modifiers() & QtCore.Qt.ShiftModifier else 1.0
            self.setValue(int(round(v0 + (y0 - event.pos().y()) * span / (160.0 * fine))))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._press is not None:
            self._press = None
            self.setSliderDown(False)
            self._aim_glow()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.reset.emit()
        event.accept()

    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:                       # never abort the app
            print(f"knob paint: {exc}")

    def _paint(self):
        side = min(self.width(), self.height()) - 6
        if side <= 4:
            return
        p = Qt.QPainter(self)
        p.setRenderHint(Qt.QPainter.Antialiasing)
        rect = QtCore.QRectF((self.width() - side) / 2, (self.height() - side) / 2,
                             side, side)
        span = max(1, self.maximum() - self.minimum())
        frac = (self.value() - self.minimum()) / span
        glow = self.glow if self.isEnabled() else 0.0
        pen = Qt.QPen(colour('rule'), 4)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, -270 * 16)
        pen.setColor(_mix(colour('trace' if self.isEnabled() else 'ink_3'),
                          colour('live'), glow))
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, int(-270 * 16 * frac))
        inner = rect.adjusted(8, 8, -8, -8)
        rim = _mix(colour('ink_2' if self._tab_focus and self.hasFocus() else 'ink_3'),
                   colour('live'), glow)
        p.setPen(Qt.QPen(rim, 1 + glow))
        p.setBrush(colour('panel_2'))
        p.drawEllipse(inner)
        angle = math.radians(225 - 270 * frac)
        c = inner.center()
        r = inner.width() / 2 - 3
        pen = Qt.QPen(_mix(colour('ink'), colour('live'), glow), 2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QtCore.QPointF(c.x() + 0.35 * r * math.cos(angle),
                                  c.y() - 0.35 * r * math.sin(angle)),
                   QtCore.QPointF(c.x() + r * math.cos(angle),
                                  c.y() - r * math.sin(angle)))
        p.end()


class Knob(Qt.QWidget):
    """A dial with its name above and its value below, in real units.

    ``log`` spaces the steps logarithmically (for spans that go from
    kilohertz to tens of megahertz). ``fmt`` turns the value into text.
    The mouse wheel over it turns it by ``wheel`` a notch - an amount, or
    on a log knob a factor - and a fifth of that with Shift (never less
    than ``step``).
    """

    valueChanged = pyqtSignal(float)

    STEPS = 1000

    def __init__(self, caption, minimum, maximum, value, fmt=None, log=False,
                 step=None, tooltip='', wheel=None, parent=None):
        super().__init__(parent)
        self._min, self._max = float(minimum), float(maximum)
        self._log = bool(log)
        self._fmt = fmt or (lambda v: f"{v:g}")
        self._default = float(value)
        self._step = step
        if wheel is None:
            wheel = 2 ** 0.25 if log else (step or (self._max - self._min) / 100)
        self._wheel_by = float(wheel)
        self.dial = _Dial()
        self.dial.setRange(0, self.STEPS)
        self.caption = Qt.QLabel(caption)
        self.caption.setAlignment(QtCore.Qt.AlignHCenter)
        self.text = Qt.QLabel()
        self.text.setAlignment(QtCore.Qt.AlignHCenter)
        self.text.setMinimumWidth(62)
        box = Qt.QVBoxLayout(self)
        box.setContentsMargins(2, 0, 2, 0)
        box.setSpacing(1)
        box.addWidget(self.caption)
        box.addWidget(self.dial, 0, QtCore.Qt.AlignHCenter)
        box.addWidget(self.text)
        if tooltip:
            self.setToolTip(tooltip + "\nRoll the wheel over it or drag up/down "
                            "(Shift: fine); double-click to reset.")
        self._quiet = False
        self._exact = None
        self._value = float(value)
        self.dial.valueChanged.connect(self._changed)
        self.dial.reset.connect(lambda: self.setValue(self._default))
        self.dial.wheeled.connect(self._wheeled)
        self.setValue(value)

    def _wheeled(self, steps, fine):
        v = self.value()
        if self._log and self._min > 0:
            factor = self._wheel_by ** (0.2 if fine else 1.0)
            v *= factor ** steps
        else:
            by = self._wheel_by / 5 if fine else self._wheel_by
            if self._step:
                by = max(self._step, round(by / self._step) * self._step)
            v += steps * by
        if self._step and not self._log:
            v = round(v / self._step) * self._step
        self.setValue(v)

    def set_range(self, minimum, maximum):
        value = self.value()
        self._min, self._max = float(minimum), float(maximum)
        self.setValue(min(max(value, self._min), self._max), emit=False)

    def _to_pos(self, v):
        v = min(max(float(v), self._min), self._max)
        if self._max <= self._min:
            return 0
        if self._log and self._min > 0:
            f = math.log(v / self._min) / math.log(self._max / self._min)
        else:
            f = (v - self._min) / (self._max - self._min)
        return int(round(f * self.STEPS))

    def _from_pos(self, pos):
        f = pos / self.STEPS
        if self._log and self._min > 0:
            v = self._min * (self._max / self._min) ** f
        else:
            v = self._min + f * (self._max - self._min)
        if self._step:
            v = round(v / self._step) * self._step
        return min(max(v, self._min), self._max)

    def _changed(self, pos):
        v = self._exact if self._exact is not None else self._from_pos(pos)
        self._value = v
        self.text.setText(self._fmt(v))
        if not self._quiet:
            self.valueChanged.emit(v)

    def value(self):
        return self._value

    def setValue(self, v, emit=True):
        """Set the value exactly (not rounded to a dial position)."""
        v = min(max(float(v), self._min), self._max)
        self._quiet = not emit
        self._exact = v
        try:
            pos = self._to_pos(v)
            if pos == self.dial.value():
                self._changed(pos)
            else:
                self.dial.setValue(pos)
        finally:
            self._quiet = False
            self._exact = None


# ------------------------------------------------------------ digit entry

def _wheel_steps(event, store, owner):
    """Whole wheel notches from ``event``, keeping the remainder on
    ``owner.<store>`` - a smooth-scrolling wheel or touchpad sends
    fractions of a notch. A vertical wheel with Shift held arrives as
    horizontal on some systems, so either axis counts."""
    angle = event.angleDelta() if hasattr(event, 'angleDelta') else None
    if angle is not None:
        delta = angle.y() or angle.x()
    else:                                           # a QGraphicsSceneWheelEvent
        delta = event.delta()
    total = getattr(owner, store) + delta
    steps = int(total / 120)                        # towards zero
    setattr(owner, store, total - steps * 120)
    return steps


class _Editor(Qt.QLineEdit):
    """The line a :class:`DigitEntry` opens for typing; Escape abandons it."""
    cancelled = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            # QLineEdit passes Enter on (for a dialog's default button); the
            # entry under it would take it as "open the editor" again.
            event.accept()


#: The face the digit entries are drawn in: the theme's number face, except
#: Walnut's. Its Limelight is a nameplate's Art Deco, 29% wider than Slate's
#: digits at the Tuner's 30 px, and it pushed the Receive tab's controls
#: under the spectrum; Walnut's reading face, Libre Caslon Text, is 15%.
DIGIT_FACE = {'walnut': 'f_ui'}


class DigitEntry(Qt.QWidget):
    """A number drawn as digits, each one its own wheel.

    Hover over a digit and it lights up; the mouse wheel then adds or takes
    away that digit's place value, carrying as arithmetic does - the tens
    digit of 90 MHz rolled up gives 100 MHz, and the ones digit of 99 MHz
    gives 100 as well. Up and Down do the same to the digit under the
    pointer, or else the one last clicked (Left and Right move that one);
    PageUp and PageDown ten at a time. Typing a digit, Enter or a double
    click opens the value to be typed. Leading zeros are dimmed.

    The value is held as a whole number of the last digit's unit, so it
    never drifts; :attr:`valueChanged` gives it in hertz. It is clamped to
    :meth:`set_range`.
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, unit='MHz', scale_hz=1e6, int_digits=4, frac_digits=3,
                 minimum_hz=0.0, maximum_hz=6e9, value_hz=0.0, pixel_size=30,
                 bold=True, default_place=None, caption='', parent=None):
        super().__init__(parent)
        self.unit = unit
        self.scale = float(scale_hz)
        self.int_digits = int(int_digits)
        self.frac_digits = int(frac_digits)
        self.places = self.int_digits + self.frac_digits
        self.resolution = self.scale / 10 ** self.frac_digits
        self.pixel_size = int(pixel_size)
        self.bold = bool(bold)
        self._min = 0
        self._max = 10 ** self.places - 1
        self._units = 0
        self._hover = None
        self._sel = self.frac_digits if default_place is None else int(default_place)
        self._wheel = 0
        self._editor = None
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setSizePolicy(Qt.QSizePolicy.Fixed, Qt.QSizePolicy.Fixed)
        if caption:
            self.setAccessibleName(caption)
        self.set_range(minimum_hz, maximum_hz)
        self.setValue(value_hz)

    # -- value
    def value(self):
        return self._units * self.resolution

    def setValue(self, hz, emit=False):
        """Set the value (rounded to the last digit, clamped); emit only if
        asked, and only if it changed."""
        units = min(max(int(round(float(hz) / self.resolution)), self._min), self._max)
        changed = units != self._units
        self._units = units
        self.update()
        if emit and changed:
            self.valueChanged.emit(self.value())
        return changed

    def set_range(self, minimum_hz, maximum_hz):
        top = 10 ** self.places - 1
        self._min = min(max(int(math.ceil(minimum_hz / self.resolution - 1e-9)), 0), top)
        self._max = max(min(int(math.floor(maximum_hz / self.resolution + 1e-9)), top), self._min)
        self._units = min(max(self._units, self._min), self._max)
        self.update()

    def minimum(self):
        return self._min * self.resolution

    def maximum(self):
        return self._max * self.resolution

    def bump(self, place, steps):
        """Add ``steps`` of digit ``place`` (0 is the last digit)."""
        if place is None or not steps:
            return False
        return self.setValue((self._units + steps * 10 ** place) * self.resolution, emit=True)

    def text(self):
        return f"{self.value() / self.scale:.{self.frac_digits}f} {self.unit}"

    # -- geometry
    def _font(self, unit=False):
        font = Qt.QFont(theme.TOKENS[DIGIT_FACE.get(theme.current(), 'f_num')])
        if unit:
            font.setPixelSize(max(11, int(self.pixel_size * 0.5)))
        else:
            font.setPixelSize(self.pixel_size)
            font.setBold(self.bold)
        return font

    PAD = 6

    def _layout(self):
        """[(place or None for the point, QRectF)], the unit's QRectF, and
        the whole height."""
        fm = Qt.QFontMetricsF(self._font())
        cell = max(fm.horizontalAdvance(d) for d in '0123456789') + 2
        dot = fm.horizontalAdvance('.') + 2
        h = fm.height() + 6
        x = self.PAD
        cells = []
        for place in range(self.places - 1, -1, -1):
            cells.append((place, QtCore.QRectF(x, 0, cell, h)))
            x += cell
            if place == self.frac_digits and self.frac_digits:
                cells.append((None, QtCore.QRectF(x, 0, dot, h)))
                x += dot
        ufm = Qt.QFontMetricsF(self._font(unit=True))
        unit = QtCore.QRectF(x + 5, 0, ufm.horizontalAdvance(self.unit) + 2, h)
        return cells, unit, h

    def sizeHint(self):
        cells, unit, h = self._layout()
        return QtCore.QSize(int(math.ceil(unit.right() + self.PAD)), int(math.ceil(h)))

    def minimumSizeHint(self):
        return self.sizeHint()

    def restyle(self):
        """The theme changed: its number face may have too."""
        self.updateGeometry()
        self.update()

    def _place_at(self, x):
        for place, rect in self._layout()[0]:
            if place is not None and rect.left() <= x < rect.right():
                return place
        return None

    def _digit(self, place):
        return (self._units // 10 ** place) % 10

    # -- paint
    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:                       # never abort the app
            print(f"digit entry paint: {exc}")

    def _paint(self):
        cells, unit, h = self._layout()
        p = Qt.QPainter(self)
        p.setRenderHint(Qt.QPainter.Antialiasing)
        frame = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(Qt.QPen(colour('rule_soft'), 1))
        p.setBrush(colour('well'))
        p.drawRoundedRect(frame, 4, 4)
        top = max((pl for pl in range(self.places) if self._digit(pl)), default=0)
        enabled = self.isEnabled()
        p.setFont(self._font())
        for place, rect in cells:
            if place is None:
                p.setPen(colour('ink_2' if enabled else 'ink_3'))
                p.drawText(rect, QtCore.Qt.AlignCenter, '.')
                continue
            lit = enabled and place == self._hover
            if lit:
                p.setPen(QtCore.Qt.NoPen)
                glow = colour('live')
                glow.setAlpha(60)
                p.setBrush(glow)
                p.drawRoundedRect(rect.adjusted(0, 3, 0, -3), 3, 3)
            leading = place > top and place > self.frac_digits
            token = ('ink_3' if not enabled or leading else
                     'ink_0' if lit else 'ink')
            p.setPen(colour(token))
            p.drawText(rect, QtCore.Qt.AlignCenter, str(self._digit(place)))
            if enabled and self.hasFocus() and place == self._sel:
                p.fillRect(QtCore.QRectF(rect.left() + 2, rect.bottom() - 4,
                                         rect.width() - 4, 2), colour('live'))
        p.setFont(self._font(unit=True))
        p.setPen(colour('ink_2'))
        p.drawText(unit, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.unit)
        p.end()

    # -- mouse and keys
    def mouseMoveEvent(self, event):
        place = self._place_at(event.pos().x())
        if place != self._hover:
            self._hover = place
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        # Always taken, even between digits: passed on, it would scroll the
        # column the entry sits in.
        event.accept()
        if not self.isEnabled():
            return
        steps = _wheel_steps(event, '_wheel', self)
        place = self._hover if self._hover is not None else self._sel
        self.bump(place, steps)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.setFocus(QtCore.Qt.MouseFocusReason)
            place = self._place_at(event.pos().x())
            if place is not None:
                self._sel = place
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.edit()
            event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        place = self._hover if self._hover is not None else self._sel
        if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            self.bump(place, 1 if key == QtCore.Qt.Key_Up else -1)
        elif key in (QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown):
            self.bump(place, 10 if key == QtCore.Qt.Key_PageUp else -10)
        elif key == QtCore.Qt.Key_Left:
            self._hover = None
            self._sel = min(self._sel + 1, self.places - 1)
            self.update()
        elif key == QtCore.Qt.Key_Right:
            self._hover = None
            self._sel = max(self._sel - 1, 0)
            self.update()
        elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_F2):
            self.edit()
        elif event.text() and event.text() in '0123456789.':
            self.edit(event.text())
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    # -- typing
    def edit(self, initial=None):
        """Open the value for typing, in the entry's own unit."""
        if self._editor is not None or not self.isEnabled():
            return
        editor = _Editor(self)
        editor.setFont(self._font())
        editor.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        validator = Qt.QDoubleValidator(self.minimum() / self.scale,
                                        self.maximum() / self.scale,
                                        self.frac_digits, editor)
        validator.setNotation(Qt.QDoubleValidator.StandardNotation)
        validator.setLocale(QtCore.QLocale.c())
        editor.setValidator(validator)
        editor.setGeometry(self.rect())
        if initial is None:
            editor.setText(f"{self.value() / self.scale:.{self.frac_digits}f}")
            editor.selectAll()
        else:
            editor.setText(initial)
        editor.returnPressed.connect(lambda: self._finish_edit(True))
        editor.editingFinished.connect(lambda: self._finish_edit(True))
        editor.cancelled.connect(lambda: self._finish_edit(False))
        self._editor = editor
        editor.show()
        editor.setFocus(QtCore.Qt.OtherFocusReason)

    def _finish_edit(self, commit):
        editor = self._editor
        if editor is None:
            return
        self._editor = None
        text = editor.text().strip()
        editor.hide()
        editor.deleteLater()
        self.setFocus(QtCore.Qt.OtherFocusReason)
        if commit and text:
            try:
                self.setValue(float(text) * self.scale, emit=True)
            except ValueError:
                pass


class StepRoller(Qt.QWidget):
    """Up and down by one step, as a ▲/▼ pair: click either half (held, it
    repeats), or roll the mouse wheel over it. :attr:`stepped` gives the
    number of steps, signed."""

    stepped = pyqtSignal(int)

    def __init__(self, height=44, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, int(height))
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._hover = 0
        self._held = 0
        self._wheel = 0
        self._repeat = Qt.QTimer(self)
        self._repeat.timeout.connect(self._repeat_step)

    def _half(self, y):
        return 1 if y < self.height() / 2 else -1

    def wheelEvent(self, event):
        event.accept()
        if self.isEnabled():
            steps = _wheel_steps(event, '_wheel', self)
            if steps:
                self.stepped.emit(steps)

    def mouseMoveEvent(self, event):
        half = self._half(event.pos().y())
        if half != self._hover:
            self._hover = half
            self.update()

    def leaveEvent(self, event):
        self._hover = 0
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.isEnabled():
            self._held = self._half(event.pos().y())
            self.stepped.emit(self._held)
            self._repeat.start(400)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._held = 0
        self._repeat.stop()
        self.update()

    def _repeat_step(self):
        if self._held:
            self._repeat.setInterval(70)
            self.stepped.emit(self._held)

    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:
            print(f"step roller paint: {exc}")

    def _paint(self):
        w, h = self.width(), self.height()
        if w < 8 or h < 12:
            return
        p = Qt.QPainter(self)
        p.setRenderHint(Qt.QPainter.Antialiasing)
        frame = QtCore.QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(Qt.QPen(colour('rule'), 1))
        p.setBrush(colour('panel_2'))
        p.drawRoundedRect(frame, 4, 4)
        for half, top in ((1, 0.0), (-1, h / 2)):
            rect = QtCore.QRectF(1, top + 1, w - 2, h / 2 - 2)
            if half in (self._hover, self._held) and self.isEnabled():
                glow = colour('live')
                glow.setAlpha(110 if half == self._held else 50)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(glow)
                p.drawRoundedRect(rect, 3, 3)
            c = rect.center()
            s = min(w, h / 2) * 0.22
            tip = -s if half == 1 else s
            tri = Qt.QPolygonF([QtCore.QPointF(c.x() - s * 1.2, c.y() - tip * 0.6),
                                QtCore.QPointF(c.x() + s * 1.2, c.y() - tip * 0.6),
                                QtCore.QPointF(c.x(), c.y() + tip * 0.9)])
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(colour('ink' if self.isEnabled() else 'ink_3'))
            p.drawPolygon(tri)
        p.setPen(Qt.QPen(colour('rule_soft'), 1))
        p.drawLine(QtCore.QPointF(4, h / 2), QtCore.QPointF(w - 4, h / 2))
        p.end()


# -------------------------------------------------------------- theme disc

class ThemeDisc(Qt.QAbstractButton):
    """The theme in force, as a disc, and a click moves on to the next.

    From the RF bench toolkit's launcher (``RFbenchToolkit.py``), FM
    receiver: the disc *is* the theme - its ground, ringed in its rule, with
    the colour its plots draw a signal in at the centre. The name is in the
    tooltip; the accessible name also says where a click goes.
    """

    DIAMETER = 22
    DOT = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        # Focus from the keyboard only, with the ring only when Tab brought it.
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self._ring = False
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.describe()

    def focusInEvent(self, event):
        self._ring = event.reason() in (QtCore.Qt.TabFocusReason,
                                        QtCore.Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._ring = False
        super().focusOutEvent(event)

    def describe(self):
        """Say which theme this is and which comes next; repaint."""
        now = theme.current()
        name = theme.NAMES[now]
        self.setToolTip(f"Theme: {name}")
        self.setAccessibleName(f"Theme: {name}. Activate for "
                               f"{theme.NAMES[theme.after(now)]}.")
        self.update()

    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:                       # never abort the app
            print(f"theme disc paint: {exc}")

    def _paint(self):
        p = Qt.QPainter(self)
        p.setRenderHint(Qt.QPainter.Antialiasing)
        centre = QtCore.QPointF(self.width() / 2, self.height() / 2)
        grow = 1.12 if self.underMouse() else 1.0
        radius = self.DIAMETER * grow / 2
        p.setPen(Qt.QPen(colour('rule'), 1))
        p.setBrush(colour('ground'))
        p.drawEllipse(centre, radius - 0.5, radius - 0.5)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(colour('trace'))
        p.drawEllipse(centre, self.DOT * grow / 2, self.DOT * grow / 2)
        if self.hasFocus() and self._ring:
            p.setPen(Qt.QPen(colour('ink'), 1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(centre, radius + 3, radius + 3)
        p.end()


# ------------------------------------------------------------- level meter

class Form(Qt.QGridLayout):
    """A form - labels right of a column, fields beside them - on a grid.
    ``QFormLayout`` in Qt 5 keeps the spacing of a hidden row, so a form
    with rows hidden (a radio's, or a folded :class:`Card`'s) opened gaps;
    a grid closes them. The calls used of a form: :meth:`addRow`,
    :meth:`setLabelAlignment`, :meth:`labelForField`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnStretch(1, 1)
        self._align = QtCore.Qt.AlignRight
        self._rows = []                       # (label or None, field)

    def setLabelAlignment(self, align):
        self._align = align
        for label, field in self._rows:
            if label is not None:
                self.setAlignment(label, self._label_align(field))

    def _label_align(self, field):
        """As a form puts it: level with a one-line field, and with the
        first line of text that wraps - unless told otherwise."""
        if self._align & QtCore.Qt.AlignVertical_Mask:
            return self._align
        wraps = isinstance(field, Qt.QLabel) and field.wordWrap()
        return self._align | (QtCore.Qt.AlignTop if wraps else QtCore.Qt.AlignVCenter)

    def addRow(self, *args):
        row = len(self._rows)
        if len(args) == 1:
            label, field = None, args[0]
            self._put(field, row, 0, 2)
        else:
            label, field = args
            if isinstance(label, str):
                label = Qt.QLabel(label)
            self.addWidget(label, row, 0, self._label_align(field))
            self._put(field, row, 1, 1)
        self._rows.append((label, field))

    def _put(self, field, row, column, span):
        if isinstance(field, Qt.QLayout):
            self.addLayout(field, row, column, 1, span)
        elif field.sizePolicy().horizontalPolicy() & Qt.QSizePolicy.GrowFlag:
            self.addWidget(field, row, column, 1, span)
        else:                                 # as a form keeps it: at the left
            self.addWidget(field, row, column, 1, span, QtCore.Qt.AlignLeft)

    def labelForField(self, field):
        for label, f in self._rows:
            if f is field:
                return label
        return None

    def row_widgets(self):
        """Each row's widgets, the label's and the field's (within its
        layout, if it is one)."""
        return [([label] if label is not None else []) + _widgets_in(field)
                for label, field in self._rows]


def _widgets_in(thing):
    if isinstance(thing, Qt.QWidget):
        return [thing]
    found = []
    for i in range(thing.count()):
        item = thing.itemAt(i)
        if item.widget() is not None:
            found.append(item.widget())
        elif item.layout() is not None:
            found += _widgets_in(item.layout())
    return found


class PageTabs(Qt.QTabWidget):
    """Tabs as tall as the page on show. Qt 5's are as tall as the tallest
    page, whatever the others' size policies say, so Receive's folded boxes
    sat over a gap the height of Sweep's page. As wide as the widest, still:
    the left column is sized to fit every page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Not Expanding (a tab widget's default): the room under the page on
        # show goes to the boxes below it, not to empty tab.
        self.setSizePolicy(Qt.QSizePolicy.Preferred, Qt.QSizePolicy.Preferred)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def _fit(self, size, page_size):
        page = self.currentWidget()
        pages = [self.widget(i) for i in range(self.count())]
        if page is None or not pages:
            return size
        tallest = max(page_size(p).height() for p in pages)
        size.setHeight(size.height() - tallest + page_size(page).height())
        return size

    def sizeHint(self):
        return self._fit(super().sizeHint(), lambda p: p.sizeHint())

    def hasHeightForWidth(self):
        page = self.currentWidget()
        return page is not None and page.hasHeightForWidth()

    def heightForWidth(self, width):
        """The page on show's, plus the tab bar and frame. (The left column
        is laid out by height for width - it has text that wraps - and Qt's
        answer here is the tallest page's too.)"""
        page = self.currentWidget()
        if page is None:
            return super().heightForWidth(width)
        pages = [self.widget(i) for i in range(self.count())]
        base = super().sizeHint()
        chrome_w = base.width() - max(p.sizeHint().width() for p in pages)
        chrome_h = base.height() - max(p.sizeHint().height() for p in pages)
        height = page.heightForWidth(width - chrome_w)
        return (height if height >= 0 else page.sizeHint().height()) + chrome_h

    def minimumSizeHint(self):
        return self._fit(super().minimumSizeHint(), lambda p: p.minimumSizeHint())


class _Chevron(Qt.QAbstractButton):
    """The fold on a :class:`Card`'s title: pointing down while the card is
    open, right while it is folded, turning between the two."""

    SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._turn = 0.0                      # 0 pointing down, 1 right
        self.anim = QtCore.QVariantAnimation(self)
        self.anim.valueChanged.connect(self._turned)

    def _turned(self, value):
        self._turn = float(value)
        self.update()

    def turn_to(self, folded, ms):
        target = 1.0 if folded else 0.0
        self.anim.stop()
        if ms <= 0:
            self._turned(target)
            return
        self.anim.setStartValue(self._turn)
        self.anim.setEndValue(target)
        self.anim.setDuration(max(1, int(ms * abs(target - self._turn))))
        self.anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.anim.start()

    def paintEvent(self, event):
        try:
            p = Qt.QPainter(self)
            p.setRenderHint(Qt.QPainter.Antialiasing)
            ink = theme.TOKENS['ink' if self.underMouse() else 'ink_2']
            p.setPen(Qt.QPen(Qt.QColor(ink), 1.6, QtCore.Qt.SolidLine,
                             QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            c, r = self.SIZE / 2, self.SIZE * 0.22
            # A "v" about the centre, turned a quarter towards ">" as it folds.
            p.translate(c, c)
            p.rotate(-90.0 * self._turn)
            points = [(-r * 1.4, -r * 0.7), (0.0, r * 0.7), (r * 1.4, -r * 0.7)]
            p.drawPolyline(Qt.QPolygonF([QtCore.QPointF(x, y) for x, y in points]))
            p.end()
        except Exception as exc:                  # never abort the app
            print(f"chevron: {exc}")


class Card(Qt.QGroupBox):
    """A group box that can fold: :meth:`foldable` puts a chevron at the
    right of its title, and it or a click on the title hides the rows of
    its form - all but the ``keep`` rows, which stay in sight folded, less
    any ``also`` widgets in them, which fade. With ``center`` the row kept
    glides to the middle of the card once it has folded, and back before
    it opens. A click does all this over :attr:`FOLD_MS` a step;
    :meth:`set_folded` at once. :attr:`folded` carries True when it folds,
    False when it opens."""

    folded = pyqtSignal(bool)
    FOLD_MS = 200
    _NO_LIMIT = 16777215                      # QWIDGETSIZE_MAX

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self._form = None
        self._keep = self._also = ()
        self._center = False
        self.chevron = None
        # The height itself is slid, minimum and maximum: with only the
        # maximum, the left column (sized to its minimums) squeezed the
        # boxes above while one opened.
        self._slide = QtCore.QVariantAnimation(self)
        self._slide.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._slide.valueChanged.connect(self._slide_to)
        self._slide.finished.connect(self._slid)
        # The kept row's glide: the label column's least width, so the
        # label (right-aligned in it) and its field move together.
        self._glide = QtCore.QVariantAnimation(self)
        self._glide.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._glide.valueChanged.connect(
            lambda x: self._form.setColumnMinimumWidth(0, int(x)))
        self._glide.finished.connect(self._glided)
        # The ``also`` widgets' fade.
        self._fade = QtCore.QVariantAnimation(self)
        self._fade.valueChanged.connect(self._set_opacity)
        self._fade.finished.connect(self._faded)

    def foldable(self, form, keep=(), also=(), folded=False, center=False):
        """``form`` is the card's :class:`Form`; ``keep``, widgets whose
        rows stay shown when it is folded; ``also``, widgets in those rows
        that fade away all the same (the Tuner's Step knob); ``center``,
        the kept row to the card's middle when folded."""
        self._form, self._keep, self._also = form, tuple(keep), tuple(also)
        self._center = bool(center)
        self.chevron = _Chevron(self)
        self.chevron.setToolTip(f"Fold or open {self.title()}.")
        self.chevron.clicked.connect(lambda opened: self._fold(not opened, self.FOLD_MS))
        self.set_folded(folded)
        return self

    def is_folded(self):
        return self.chevron is not None and not self.chevron.isChecked()

    def set_folded(self, folded):
        """Fold or open it now, with no slide."""
        if self.chevron is None:
            return
        changed = self.chevron.isChecked() == bool(folded)
        self.chevron.setChecked(not folded)
        self._fold(folded, 0, emit=changed)

    def _fold(self, folded, ms, emit=True):
        for anim in (self._slide, self._glide, self._fade):
            anim.stop()
        self.layout().setEnabled(True)
        self.chevron.turn_to(folded, ms)
        if ms <= 0 or not self.isVisible():
            self._show_rows(not folded)
            self._set_opacity(1.0)
            self._faded()
            self._let_height_go()
            self._form.setColumnMinimumWidth(
                0, self._center_x() if folded and self._center else 0)
        elif folded:
            self._slide_rows(True, ms)
        elif self._center and self._form.columnMinimumWidth(0) > 0:
            self._glide_to(self._open_label_width(), ms)    # then _glided opens it
        else:
            self._slide_rows(False, ms)
        if emit:
            self.folded.emit(bool(folded))

    def _slide_rows(self, folded, ms):
        """Slide shut or open, the ``also`` widgets fading with it."""
        start = self.height()
        # The height it will end at, measured with the rows as they will
        # be. Then the rows are laid out open and the layout held still, so
        # the card's edge slides over them - squeezed by the layout
        # instead, they piled on top of each other. Closing, they are
        # hidden once the slide is done.
        self._show_rows(not folded)
        end = self._natural_height()
        if not folded:
            self._set_opacity(0.0)
        self._show_rows(True)
        self.resize(self.width(), max(start, end))
        self.layout().activate()
        self.layout().setEnabled(False)
        self.setFixedHeight(start)
        self._slide.setDuration(ms)
        self._slide.setStartValue(start)
        self._slide.setEndValue(end)
        self._slide.start()
        if self._also:
            self._fade.setDuration(ms)
            self._fade.setStartValue(1.0 if folded else 0.0)
            self._fade.setEndValue(0.0 if folded else 1.0)
            self._fade.start()

    def _glide_to(self, x, ms):
        self._glide.setDuration(ms)
        self._glide.setStartValue(float(self._label_column()))
        self._glide.setEndValue(float(x))
        self._glide.start()

    def _glided(self):
        if not self.is_folded():
            self._slide_rows(False, self.FOLD_MS)

    def _label_column(self):
        """The label column's width now."""
        least = self._form.columnMinimumWidth(0)
        for label, _ in self._form._rows:
            if label is not None and not label.isHidden():
                least = max(least, label.width())
        return least

    def _open_label_width(self):
        return max([label.sizeHint().width() for label, _ in self._form._rows
                    if label is not None] or [0])

    def _center_x(self):
        """The label column's width that puts the kept row, label and
        field, in the middle of the card."""
        form = self._form
        for label, field in form._rows:
            if label is None or not any(w in self._keep for w in _widgets_in(field)):
                continue
            field_w = (field.sizeHint().width() if isinstance(field, Qt.QLayout)
                       else field.sizeHint().width())
            margins = form.contentsMargins()
            room = self.contentsRect().width() - margins.left() - margins.right()
            label_w = label.sizeHint().width()
            row_w = label_w + form.horizontalSpacing() + field_w
            return label_w + max(0, (room - row_w) // 2)
        return 0

    def _set_opacity(self, value):
        for w in self._also:
            effect = w.graphicsEffect()
            if not isinstance(effect, Qt.QGraphicsOpacityEffect):
                effect = Qt.QGraphicsOpacityEffect(w)
                w.setGraphicsEffect(effect)
            effect.setOpacity(float(value))

    def _faded(self):
        """Fully shown again, a widget drops its fade: it paints as before,
        glow and all."""
        if not self.is_folded():
            for w in self._also:
                if isinstance(w.graphicsEffect(), Qt.QGraphicsOpacityEffect):
                    w.setGraphicsEffect(None)

    def _let_height_go(self):
        self.layout().setEnabled(True)
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._NO_LIMIT)
        self.layout().activate()

    def _slide_to(self, height):
        """One step of the slide. Qt passes a size change up to the
        scroll area one event at a time, through the tab widget, and lagged
        the slide by a frame or more - squeezing the boxes above as one
        opened - so the column is refitted here, at once."""
        try:
            self.setFixedHeight(int(height))
            parent = self.parentWidget()
            while parent is not None and not isinstance(parent, Qt.QScrollArea):
                if parent.layout() is not None:
                    parent.layout().invalidate()
                parent.updateGeometry()
                parent = parent.parentWidget()
            if parent is not None and parent.widgetResizable():
                panel = parent.widget()
                want = (panel.heightForWidth(panel.width()) if panel.hasHeightForWidth()
                        else panel.sizeHint().height())
                panel.resize(panel.width(), max(want, parent.viewport().height()))
                panel.layout().activate()
        except Exception as exc:                  # never abort the app
            print(f"card: {exc}")

    def _natural_height(self):
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._NO_LIMIT)
        self.layout().activate()
        if self.hasHeightForWidth():
            return max(self.heightForWidth(self.width()), self.minimumSizeHint().height())
        return self.sizeHint().height()

    def _slid(self):
        folded = self.is_folded()
        if folded and self._center:
            # Held where it is while the other rows go - the label column
            # would snap narrower - and then glided to the middle.
            self._form.setColumnMinimumWidth(0, self._label_column())
        if folded:
            self._show_rows(False)
        self._let_height_go()
        if folded and self._center:
            self._glide_to(self._center_x(), self.FOLD_MS)
        elif not folded:
            self._form.setColumnMinimumWidth(0, 0)

    def _show_rows(self, shown):
        for widgets in self._form.row_widgets():
            kept = any(w in self._keep for w in widgets)
            for w in widgets:
                if not kept or w in self._also:
                    w.setVisible(shown)

    def _title_rect(self):
        option = Qt.QStyleOptionGroupBox()
        self.initStyleOption(option)
        return self.style().subControlRect(Qt.QStyle.CC_GroupBox, option,
                                           Qt.QStyle.SC_GroupBoxLabel, self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            if (self.chevron is not None and self._center and self.is_folded()
                    and not self._slide.state() and not self._glide.state()):
                self._form.setColumnMinimumWidth(0, self._center_x())
            if self.chevron is not None:
                title = self._title_rect()
                y = title.center().y() - self.chevron.height() // 2
                self.chevron.move(self.width() - self.chevron.width() - 2, max(0, y))
        except Exception as exc:                  # never abort the app
            print(f"card: {exc}")

    def mousePressEvent(self, event):
        try:
            if (self.chevron is not None and event.button() == QtCore.Qt.LeftButton
                    and event.pos().y() <= self._title_rect().bottom()):
                self.chevron.click()
                return
        except Exception as exc:                  # never abort the app
            print(f"card: {exc}")
        super().mousePressEvent(event)


class LevelMeter(Qt.QWidget):
    """Left and right audio level, peak and RMS, -50 to 0 dBFS."""

    FLOOR_DB = -50.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(30)
        self.setMinimumWidth(120)
        self._rms = [self.FLOOR_DB, self.FLOOR_DB]
        self._peak = [self.FLOOR_DB, self.FLOOR_DB]
        self._hold = [self.FLOOR_DB, self.FLOOR_DB]

    def set_levels(self, peak, rms):
        for i in range(2):
            p = 20 * math.log10(peak[i]) if peak[i] > 1e-6 else self.FLOOR_DB
            r = 20 * math.log10(rms[i]) if rms[i] > 1e-6 else self.FLOOR_DB
            # Rise at once, fall back gently.
            self._rms[i] = max(r, self._rms[i] - 3.0)
            self._peak[i] = max(p, self._peak[i] - 2.0)
            self._hold[i] = max(p, self._hold[i] - 0.5)
        self.update()

    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:
            print(f"meter paint: {exc}")

    def _paint(self):
        w, h = self.width(), self.height()
        if w < 20 or h < 8:
            return
        p = Qt.QPainter(self)
        label_w = 14
        bar_h = max(3, (h - 6) // 2)
        span = -self.FLOOR_DB
        for i, name in enumerate('LR'):
            y = 2 + i * (bar_h + 2)
            p.setPen(colour('ink_2'))
            p.drawText(QtCore.QRectF(0, y, label_w, bar_h),
                       QtCore.Qt.AlignCenter, name)
            x0, bw = label_w + 2, w - label_w - 4
            p.fillRect(QtCore.QRectF(x0, y, bw, bar_h), colour('well'))
            peak = min(max((self._peak[i] - self.FLOOR_DB) / span, 0), 1)
            rms = min(max((self._rms[i] - self.FLOOR_DB) / span, 0), 1)
            token = ('bad' if self._peak[i] > -1 else
                     'warn' if self._peak[i] > -6 else 'good')
            dim = colour(token)
            dim.setAlpha(110)
            p.fillRect(QtCore.QRectF(x0, y, bw * peak, bar_h), dim)
            p.fillRect(QtCore.QRectF(x0, y, bw * rms, bar_h), colour(token))
            hold = min(max((self._hold[i] - self.FLOOR_DB) / span, 0), 1)
            p.fillRect(QtCore.QRectF(x0 + bw * hold - 1, y, 2, bar_h), colour('ink'))
        p.end()


# ----------------------------------------------------------- spectrum view

#: Each theme's waterfall, built from its own colours: (position 0-1, a
#: token name or '#rrggbb'). The floor is the plot's own ``well``, so the
#: noise sinks into the background and only signals show.
WATERFALL = {
    # Ice and ember: slate blue rising to the trace's pale ice, then the
    # tuner's orange for the strongest (option B, chosen 2026-09-22).
    'slate': ((0.0, 'well'), (0.4, '#1d3a50'), (0.75, 'trace'), (1.0, 'live')),
    # Ink on paper: signals come out dark, through the pulse's ultramarine
    # to the iron-gall ink.
    'reading-room': ((0.0, 'well'), (0.3, 'rule_soft'), (0.65, 'trace'),
                     (1.0, 'ink')),
    # Dial glow: the wood, through the tan of the dial to its cream.
    'walnut': ((0.0, 'well'), (0.45, '#33241a'), (0.65, 'rule'),
               (0.85, 'trace'), (1.0, 'ink')),
}


def _oklab(rgb):
    """sRGB 0-1 (n, 3) to Oklab: blends through it stay even in lightness."""
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    lms = c @ np.array([[0.4122214708, 0.2119034982, 0.0883024619],
                        [0.5363325363, 0.6806995451, 0.2817188376],
                        [0.0514459929, 0.1073969566, 0.6299787005]])
    return np.cbrt(lms) @ np.array([[0.2104542553, 1.9779984951, 0.0259040371],
                                    [0.7936177850, -2.4285922050, 0.7827717662],
                                    [-0.0040720468, 0.4505937099, -0.8086757660]])


def _srgb(lab):
    lms = lab @ np.array([[1.0, 1.0, 1.0],
                          [0.3963377774, -0.1055613458, -0.0894841775],
                          [0.2158037573, -0.0638541728, -1.2914855480]])
    c = (lms ** 3) @ np.array([[4.0767416621, -1.2684380046, -0.0041960863],
                               [-3.3077115913, 2.6097574011, -0.7034186147],
                               [0.2309699292, -0.3413193965, 1.7076147010]])
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def waterfall_lut(stops, n=256):
    """An (n, 3) uint8 lookup table through ``stops``, blended in Oklab.
    Token names are read from the theme in force now."""
    pos = np.array([p for p, _ in stops], dtype=np.float64)
    hexes = [theme.TOKENS.get(c, c) if not c.startswith('#') else c for _, c in stops]
    rgb = np.array([[int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)] for h in hexes])
    lab = _oklab(rgb)
    x = np.linspace(0.0, 1.0, n)
    out = np.stack([np.interp(x, pos, lab[:, k]) for k in range(3)], axis=1)
    return np.round(_srgb(out) * 255).astype(np.uint8)


#: The two lines on the spectrum: the tuner's (where you listen) and the
#: radio's centre. Tokens, read when drawn, or '#rrggbb'. Walnut's warn is
#: an amber too near its valve orange and its tan trace to tell apart, so
#: its centre line is verdigris - the patina on a dial's brass - chosen over
#: its sage ``good`` and a lemon yellow, side by side on a real FM band.
MARKERS = {
    # The Center is a reference, not an alert: the quietest ink, dashed.
    # Orange means one thing - where you are tuned.
    'slate': {'tuner': 'live', 'center': 'ink_3'},
    'reading-room': {'tuner': 'live', 'center': 'ink_3'},
    'walnut': {'tuner': 'live', 'center': 'ink_3'},
}

#: The trace's colour where it is not the theme's ``trace``: Walnut's tan
#: is its chrome too, so the signal is drawn in the cream and leads.
TRACE = {'walnut': 'ink'}


def trace_colour():
    token = TRACE.get(theme.current(), 'trace')
    return Qt.QColor(theme.TOKENS[token])


def density_lut():
    """RGBA for a density map: the theme's waterfall colours from a third of
    the way up (the rest is the plot's own background), clear at zero and
    more opaque the more often a level was hit."""
    base = waterfall_lut(WATERFALL.get(theme.current(), WATERFALL['slate']))
    t = np.linspace(0.0, 1.0, 256)
    rgb = base[np.round((0.35 + 0.65 * t) * (len(base) - 1)).astype(int)]
    alpha = np.where(t > 0, 80 + 175 * t, 0.0)
    return np.column_stack([rgb, alpha]).astype(np.uint8)


def _marker_colour(which):
    token = MARKERS.get(theme.current(), MARKERS['slate'])[which]
    return Qt.QColor(theme.TOKENS.get(token, token))


class _Fade(QtCore.QObject):
    """Fades graphics items in and out together."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = [item for item in items if item is not None]
        self.target = 1.0
        self.anim = QtCore.QVariantAnimation(self)
        self.anim.valueChanged.connect(self._apply)

    def _apply(self, value):
        for item in self.items:
            item.setOpacity(float(value))

    def level(self):
        return self.items[0].opacity() if self.items else 0.0

    def to(self, target, ms):
        """Fade to ``target`` (0-1), taking ``ms`` for the whole way."""
        self.target = float(target)
        now = self.level()
        self.anim.stop()
        if abs(now - self.target) < 1e-3:
            self._apply(self.target)
            return
        self.anim.setStartValue(float(now))
        self.anim.setEndValue(self.target)
        self.anim.setDuration(max(1, int(ms * abs(now - self.target))))
        self.anim.start()

    def set(self, value):
        self.anim.stop()
        self.target = float(value)
        self._apply(self.target)


def on_raster(hz, step_hz):
    """``hz`` rounded to the channel raster of ``step_hz``. A 200 kHz raster
    is the Americas' FM one, whose channels are the odd tenths (88.1, 88.3,
    ... 107.9 MHz), so it is offset by 100 kHz; the others start at zero."""
    anchor = 100e3 if abs(step_hz - 200e3) < 1 else 0.0
    return anchor + round((hz - anchor) / step_hz) * step_hz


def _mhz(hz):
    return f"{hz / 1e6:.3f} MHz"


def _span_text(hz):
    if hz >= 1e9:
        return f"{hz / 1e9:.3g} GHz"
    if hz >= 1e6:
        return f"{hz / 1e6:.3g} MHz"
    return f"{hz / 1e3:.3g} kHz"


class ChannelBand(pg.LinearRegionItem):
    """The channel filter drawn on the spectrum, and a handle on it: the
    wheel over it asks for a wider or narrower filter, and a drag with the
    middle button asks to move it - :class:`SpectrumView` passes both on as
    signals, and the window decides. Every other button and the wheel
    elsewhere still pan and zoom the view."""

    def __init__(self, view):
        super().__init__(movable=False)
        self._view = view
        self._drag = None
        self._wheel = 0
        self.hovered = False
        self.setToolTip("The channel the receiver takes. Wheel: wider or narrower\n"
                        "(Shift: fine). Middle-drag: move the tuner - within the\n"
                        "band around the Center in Receive; anywhere on a sweep,\n"
                        "where it says what Listen and Real time will start from.")

    def hoverEvent(self, ev):
        # Claiming the middle button on hover is how pyqtgraph gives this
        # item the drag rather than the view under it, which takes every
        # button to pan.
        over = not ev.isExit() and ev.acceptDrags(QtCore.Qt.MouseButton.MiddleButton)
        if over != self.hovered:
            self.hovered = over
            self._view._paint_band()

    def wheelEvent(self, ev):
        ev.accept()
        steps = _wheel_steps(ev, '_wheel', self)
        if steps:
            fine = bool(ev.modifiers() & QtCore.Qt.ShiftModifier)
            self._view.bandWheel.emit(steps, fine)

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.MouseButton.MiddleButton:
            ev.ignore()
            return
        ev.accept()
        if ev.isStart():
            low, high = self.getRegion()
            self._drag = ((low + high) / 2, ev.buttonDownPos().x())
        if self._drag is None:
            return
        centre, x0 = self._drag
        self._view.bandDragged.emit((centre + ev.pos().x() - x0) * self._view.scale)
        if ev.isFinish():
            self._drag = None
            self._view.bandDragFinished.emit()


class _RowRing:
    """Waterfall rows with the time each came, oldest overwritten. With
    ``merge_s``, rows within one such slot are kept as their maximum - the
    coarse copy a long stretch of time is drawn from."""

    def __init__(self, rows, cols, merge_s=None):
        self.rows = np.empty((rows, cols), dtype=np.float16)
        self.t = np.zeros(rows)
        self.n = self.head = 0
        self.merge_s = merge_s
        self._slot = None

    def add(self, row, t):
        slot = None if self.merge_s is None else int(t // self.merge_s)
        if slot is not None and slot == self._slot and self.n:
            last = (self.head - 1) % len(self.t)
            np.maximum(self.rows[last], row, out=self.rows[last])
            self.t[last] = t
            return
        self._slot = slot
        self.rows[self.head] = row
        self.t[self.head] = t
        self.head = (self.head + 1) % len(self.t)
        self.n = min(self.n + 1, len(self.t))

    def newest_first(self):
        """The ring's indices and times, newest first."""
        order = (self.head - 1 - np.arange(self.n)) % len(self.t)
        return order, self.t[order]


def _ago(seconds):
    """A waterfall's time scale: how long ago, from "now" at the top."""
    if seconds < 0.05:
        return "now"
    if seconds < 90:
        return f"{seconds:g} s"
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class TimeAxis(pg.AxisItem):
    """The waterfall's left axis: seconds ago, "now" at the top, at steps
    that read as time (1, 2, 5, 10, 15, 30 s, then minutes)."""

    STEPS = (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300)

    def tickSpacing(self, minVal, maxVal, size):
        span = abs(maxVal - minVal)
        for step in self.STEPS:
            if span / step <= 6:
                return [(step, 0)]
        return [(self.STEPS[-1], 0)]

    def tickStrings(self, values, scale, spacing):
        return [_ago(v * scale) for v in values]


class SpectrumView(Qt.QWidget):
    """A spectrum, optionally with a waterfall, and the dials that set the
    view: **Span** (zoom around the centre), **Ref** (the top of the scale),
    **Range** (dB from top to bottom - the amplitude scale) and **Avg**
    (frames or sweeps averaged).

    The tuner's marker can fade (:meth:`set_marker_auto`): hidden, it shows
    while the tuner is moved or held, then goes. The centre line does the
    opposite - it fades while the centre is moved (:meth:`center_moving`),
    so the spectrum under it can be seen, and comes back once it settles.

    Frequencies come in hertz and are shown in ``unit`` (MHz or kHz). Left
    click emits :attr:`clicked`, double click :attr:`activated`, both in
    hertz. The mouse wheel zooms and dragging pans, along frequency only;
    the Span dial follows. Over the channel band the wheel emits
    :attr:`bandWheel` (notches, fine) and a middle-button drag
    :attr:`bandDragged` (hertz) then :attr:`bandDragFinished`. With
    ``tuner_menu``, a right click offers to put the tuner where the pointer
    is, and :attr:`tunerRequested` carries it (hertz).

    The level axis is a handle too: under the pointer it lights, the wheel
    over it zooms the amplitude scale about the level under the pointer
    (Range and Ref level together, as the wheel zooms the span), and a
    middle-button drag up or down moves the Ref level. The knobs follow.

    The waterfall keeps the last :attr:`WF_HISTORY_S` seconds and shows
    :attr:`wf_span_s` of them, "now" at the top, with a time scale down its
    left side; the wheel over that scale shows more or less of the past.
    Where a row on screen covers several rows of data it shows their
    maximum, so a short burst is not lost however far out it is zoomed.
    """

    clicked = pyqtSignal(float)
    activated = pyqtSignal(float)
    averageChanged = pyqtSignal(int)
    bandWheel = pyqtSignal(int, bool)
    bandDragged = pyqtSignal(float)
    bandDragFinished = pyqtSignal()
    tunerRequested = pyqtSignal(float)

    #: Rows drawn on screen, whatever the time shown; columns kept.
    WF_ROWS = 220
    WF_COLS = 4096
    #: How long the waterfall remembers, and the rows that allows at the
    #: fastest rate one comes (Receive, 15 a second), kept as float16.
    WF_HISTORY_S = 300.0
    WF_HISTORY_ROWS = 4800
    #: The coarse copy: a row a quarter of a second, drawn from once a row
    #: on screen covers that much. From every row, five minutes on screen
    #: took 90 ms a draw.
    WF_COARSE_S = 0.25
    #: How much of it is shown: at least, by default, at most.
    WF_SPAN_S = (2.0, 20.0, WF_HISTORY_S)
    #: A wheel notch over the time scale shows this much more or less.
    TIME_ZOOM = 1.25
    #: The channel band is a handle as well as a picture: it is drawn at
    #: least this many pixels wide, so a narrow channel on a wide sweep can
    #: still be grabbed.
    BAND_MIN_PX = 7
    #: A wheel notch over the level axis zooms the scale by this (a fifth
    #: of it with Shift), as the Span knob's wheel does.
    AXIS_ZOOM = 1.25
    #: The fades, in ms: out and in, and how long after the last move each
    #: line decides the tuner or centre has settled.
    FADE_FAST_MS = 120
    CENTER_BACK_MS = 350
    CENTER_SETTLE_MS = 600
    MARKER_AWAY_MS = 500
    TUNER_SETTLE_MS = 900

    def __init__(self, title, unit='MHz', waterfall=True, min_span_hz=20e3,
                 ref_db=-20.0, range_db=100.0, avg=4, snap_hz=None,
                 tuner_menu=False, parent=None):
        super().__init__(parent)
        self.unit = unit
        self.scale = 1e6 if unit == 'MHz' else 1e3
        self.min_span_hz = float(min_span_hz)
        self.full = (0.0, 1.0)
        self.center_hz = 0.5
        self._peak = None
        self._x = None
        self._wf = None                   # the rows on screen, newest first
        self._wf_extent = None
        self._hist = None                 # the history: every row, and
        self._coarse = None               # the most of each quarter second
        self._wf_drawn = 0.0              # the newest row's time when drawn
        self.wf_span_s = self.WF_SPAN_S[1]
        self._time_hot = False
        self._wf_later = Qt.QTimer(self)
        self._wf_later.setSingleShot(True)
        self._wf_later.timeout.connect(self._draw_wf)
        self._syncing = False
        self._wanted_span = None
        self.snap_hz = snap_hz
        # One menu for the view's life, its text rewritten at each click:
        # a new one per click would pile up under the view, which owns it.
        self._menu = self._tune_action = self._menu_hz = None
        if tuner_menu:
            self._menu = Qt.QMenu(self)
            self._tune_action = self._menu.addAction('')
            self._tune_action.triggered.connect(self._tuner_chosen)
        self.title = title

        self.plot = pg.PlotWidget()
        self.plot.setObjectName('spectrum')
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setLabel('bottom', unit)
        self.level_unit = 'dBFS'
        self.plot.setLabel('left', self.level_unit)
        self.plot.getPlotItem().setTitle(title)
        self.curve = self.plot.plot([], [])
        self.curve.setDownsampling(auto=True, method='peak')
        self.curve.setClipToView(True)
        self.peak_curve = self.plot.plot([], [])
        self.peak_curve.setDownsampling(auto=True, method='peak')
        self.peak_curve.setClipToView(True)
        self.marker = pg.InfiniteLine(angle=90, movable=False)
        self.marker.setVisible(False)
        self.plot.addItem(self.marker, ignoreBounds=True)
        # A real-time density map (the BB60D's), behind the trace.
        self.density_item = pg.ImageItem()
        self._density_args = None
        self.density_item.setZValue(-5)
        self.density_item.setVisible(False)
        self.plot.addItem(self.density_item, ignoreBounds=True)
        self.band = ChannelBand(self)
        self.band.setVisible(False)
        self.band.setZValue(-10)
        self.plot.addItem(self.band, ignoreBounds=True)
        #: Where the band really is, before it is widened to stay grabbable.
        self._band_hz = None
        # The radio's centre frequency, and the parts of the band the tuner
        # cannot reach, shaded.
        self.center_line = pg.InfiniteLine(angle=90, movable=False)
        self.center_line.setVisible(False)
        self.plot.addItem(self.center_line, ignoreBounds=True)
        self.outside = []
        for _ in range(2):
            shade = pg.LinearRegionItem(movable=False)
            shade.setVisible(False)
            shade.setZValue(5)             # over the trace: it dims it
            self.plot.addItem(shade, ignoreBounds=True)
            self.outside.append(shade)
        self.message = pg.TextItem('', anchor=(0.5, 0.5))
        self.plot.addItem(self.message, ignoreBounds=True)
        self.plot.sigXRangeChanged.connect(self._range_changed)
        self.plot.scene().sigMouseClicked.connect(self._scene_clicked)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self.plot.scene().sigMouseMoved.connect(self._plot_mouse_moved)

        self.wf_plot = None
        if waterfall:
            self.wf_plot = pg.PlotWidget(axisItems={'left': TimeAxis('left')})
            self.wf_plot.setObjectName('waterfall')
            self.wf_plot.setMenuEnabled(False)
            self.wf_plot.hideButtons()
            self.wf_plot.setMouseEnabled(x=True, y=False)
            self.wf_plot.hideAxis('bottom')
            self.wf_plot.getPlotItem().invertY(True)
            self.wf_plot.setXLink(self.plot)
            self.wf_plot.setLabel('left', 'time')
            # A label at either end would be cut in half: the top is now.
            self.wf_plot.getAxis('left').setStyle(hideOverlappingLabels=True)
            self.wf_plot.setYRange(0, self.wf_span_s, padding=0)
            # Linked views line up by where they are on screen, so the
            # waterfall's plot must start where the spectrum's does: its
            # axis is kept as wide as the spectrum's.
            self.plot.getAxis('left').geometryChanged.connect(self._match_axes)
            self.wf_image = pg.ImageItem()
            self.wf_plot.addItem(self.wf_image)
            self.wf_plot.scene().sigMouseClicked.connect(self._scene_clicked)
            self.wf_plot.scene().sigMouseMoved.connect(self._mouse_moved)
            self.wf_plot.scene().sigMouseMoved.connect(self._wf_mouse_moved)
            self.wf_plot.scene().installEventFilter(self)
            self.wf_plot.viewport().installEventFilter(self)

        # The dials.
        self.span_knob = Knob('Span', self.min_span_hz, 6e9, 6e9, _span_text,
                              log=True, wheel=1.25,
                              tooltip='How much frequency the view shows, '
                              'centred on the station (or on the view).')
        self.ref_knob = Knob('Ref level', -160, 20, ref_db, lambda v: f"{v:.0f} dB",
                             step=1, wheel=2, tooltip='The level at the top of the scale.\n'
                             'A fits the scale to the trace (auto scale).')
        self.range_knob = Knob('Range', 10, 180, range_db, lambda v: f"{v:.0f} dB",
                               step=1, wheel=5, tooltip='dB from the top of the scale to the '
                               'bottom: the amplitude scale. Also the waterfall colours.')
        self.avg_knob = Knob('Average', 1, 50, avg, lambda v: f"{v:.0f}x",
                             step=1, wheel=1,
                             tooltip='How many frames (or sweeps) are averaged.')
        self.span_knob.valueChanged.connect(self._span_changed)
        self.ref_knob.valueChanged.connect(lambda _: self._apply_levels())
        self.range_knob.valueChanged.connect(lambda _: self._apply_levels())
        self.avg_knob.valueChanged.connect(lambda v: self.averageChanged.emit(int(v)))
        self.peak_check = Qt.QCheckBox('Peak hold')
        self.peak_check.toggled.connect(self._peak_toggled)
        self.wf_check = Qt.QCheckBox('Waterfall')
        self.wf_check.setChecked(True)
        self.wf_check.setVisible(waterfall)
        self.wf_check.toggled.connect(self._wf_toggled)
        self.full_btn = Qt.QPushButton('Full span')
        self.full_btn.setToolTip('Show everything there is.')
        self.full_btn.clicked.connect(lambda: self.span_knob.setValue(self.span_knob._max))
        # The pointer's frequency and level, in the plot's bottom-left
        # corner; clicks go through it to the plot.
        self.readout = Qt.QLabel('', self.plot)
        self.readout.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.readout.hide()
        self.plot.getPlotItem().getViewBox().sigResized.connect(self._place_readout)

        # The lines are on the spectrum only: the waterfall is left clear.
        self._marker_fade = _Fade([self.marker], self)
        self._center_fade = _Fade([self.center_line], self)
        self.marker_auto = False
        self._grabbed = False
        self._center_back = Qt.QTimer(self)
        self._center_back.setSingleShot(True)
        self._center_back.timeout.connect(
            lambda: self._center_fade.to(1.0, self.CENTER_BACK_MS))
        self._marker_away = Qt.QTimer(self)
        self._marker_away.setSingleShot(True)
        self._marker_away.timeout.connect(self._marker_settled)
        # The middle button going down on the channel band is "grabbing the
        # tuner", and its marker shows before any drag begins. The scene is
        # watched for it: the band itself is not sent the press.
        self.plot.scene().installEventFilter(self)
        # Leaving the plot must put the level axis's light out.
        self.plot.viewport().installEventFilter(self)
        self._axis_hot = False
        self._axis_drag = None            # (pointer y, Ref level) at the press

        # Under the plot: the dials and switches, at the right-hand end.
        controls = Qt.QHBoxLayout()
        controls.setSpacing(6)
        controls.addStretch(1)
        for knob in (self.span_knob, self.ref_knob, self.range_knob, self.avg_knob):
            controls.addWidget(knob)
        checks = Qt.QVBoxLayout()
        checks.addWidget(self.peak_check)
        checks.addWidget(self.wf_check)
        checks.addWidget(self.full_btn)
        controls.addLayout(checks)

        layout = Qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if waterfall:
            self.splitter = Qt.QSplitter(QtCore.Qt.Vertical)
            self.splitter.addWidget(self.plot)
            self.splitter.addWidget(self.wf_plot)
            self.splitter.setStretchFactor(0, 3)
            self.splitter.setStretchFactor(1, 2)
            layout.addWidget(self.splitter, 1)
        else:
            layout.addWidget(self.plot, 1)
        layout.addLayout(controls)
        self.restyle()
        self._apply_levels()

    # -- theme
    def restyle(self):
        t = theme.TOKENS
        for plot in (self.plot, self.wf_plot):
            if plot is None:
                continue
            plot.setBackground(t['well'])
            item = plot.getPlotItem()
            for name in ('left', 'bottom'):
                axis = item.getAxis(name)
                axis.setPen(pg.mkPen(t['rule']))
                axis.setTextPen(pg.mkPen(t['ink_2']))
        # In pixels: 10 pt is 13 px on Linux but 10 on a Mac (72 dpi).
        self.plot.getPlotItem().setTitle(self.title, color=t['ink'], size='13px')
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        ink = trace_colour()
        self.curve.setPen(pg.mkPen(ink, width=1))
        # A faint fill under the trace, down past the bottom of any scale:
        # it gives the spectrum weight, and a weak station a shape.
        wash = Qt.QColor(ink)
        wash.setAlpha(26)
        self.curve.setFillLevel(-400.0)
        self.curve.setBrush(pg.mkBrush(wash))
        for plot in (self.plot, self.wf_plot):
            if plot is not None:
                plot.getPlotItem().getViewBox().setBorder(pg.mkPen(t['rule']))
        self.peak_curve.setPen(pg.mkPen(t['ink_3'], width=1,
                                        style=QtCore.Qt.DashLine))
        tuner = pg.mkPen(_marker_colour('tuner'), width=1.5)
        self.marker.setPen(tuner)
        self._paint_band()
        centre = pg.mkPen(_marker_colour('center'), width=1.5, style=QtCore.Qt.DashLine)
        self.center_line.setPen(centre)
        # Out of the tuner's reach: veiled, lighter on a dark theme and
        # darker on a light one (a black shade vanishes on a near-black
        # plot), with a dotted line where the reach ends.
        # Out of reach is dimmed: the plot's own well laid over it, trace
        # and grid alike, so it reads as unavailable. (Lighter, as it was
        # on the dark themes, it read as selected.)
        veil = Qt.QColor(t['well'])
        veil.setAlpha(170)
        edge = pg.mkPen(Qt.QColor(t['ink_3']), width=1, style=QtCore.Qt.DotLine)
        for region in self.outside:
            region.setBrush(pg.mkBrush(veil))
            for line in region.lines:
                line.setPen(edge)
        self.message.setColor(t['warn'])
        # A chip of the panel behind the readout, nearly opaque, so a busy
        # trace or density map under it can't wash the numbers out.
        chip = Qt.QColor(t['panel'])
        self.readout.setStyleSheet(
            f"background: rgba({chip.red()}, {chip.green()}, {chip.blue()}, 224);"
            f" color: {t['ink']}; border: 1px solid {t['rule']};"
            " border-radius: 2px; padding: 3px 8px;")
        if self._density_args is not None:
            self.set_density(*self._density_args)    # in the new theme's colours
        if self.wf_plot is not None:
            self.wf_image.setLookupTable(waterfall_lut(
                WATERFALL.get(theme.current(), WATERFALL['slate'])))

    def _match_axes(self):
        try:
            self.wf_plot.getAxis('left').setWidth(self.plot.getAxis('left').width())
        except Exception as exc:                  # never abort the app
            print(f"spectrum view: {exc}")

    def _paint_band(self):
        """The channel band, brighter with the pointer over it - it is a
        handle as well as a picture."""
        t = theme.TOKENS
        fill = Qt.QColor(t['live'])
        fill.setAlpha(36 if self.band.hovered else 14)
        self.band.setBrush(pg.mkBrush(fill))
        self.band.update()
        for line in self.band.lines:
            edge = Qt.QColor(t['live'])
            edge.setAlpha(255 if self.band.hovered else 200)
            line.setPen(pg.mkPen(edge))

    # -- data
    def set_extent(self, low_hz, high_hz, center_hz=None, keep_span=True):
        """The frequencies there is data for: the most the Span dial shows."""
        if high_hz <= low_hz:
            return
        changed = (abs(low_hz - self.full[0]) > 1 or abs(high_hz - self.full[1]) > 1)
        self.full = (float(low_hz), float(high_hz))
        if center_hz is not None:
            self.center_hz = float(center_hz)
        elif not (low_hz <= self.center_hz <= high_hz):
            self.center_hz = (low_hz + high_hz) / 2
        if keep_span:
            # A span just loaded (load_state) was clamped to the last extent's
            # dial; what was asked for is kept until the extent is known - a
            # sweep opened at 1 GHz of 6, the dial's first limit, before.
            span = self._wanted_span or self.span_knob.value()
        else:
            span = high_hz - low_hz
        self._wanted_span = None
        self.span_knob.set_range(min(self.min_span_hz, high_hz - low_hz), high_hz - low_hz)
        if changed:
            self._peak = None
            self._wf = None
            self._hist = None
        self.span_knob.setValue(min(span, high_hz - low_hz), emit=False)
        self._apply_span()

    def set_center(self, hz):
        self.center_hz = float(hz)
        self._apply_span()

    def set_level_unit(self, unit):
        """What the levels are in: dBFS from an IQ stream, dBm from a radio
        that measures them itself (the BB60D's own sweep)."""
        if unit != self.level_unit:
            self.level_unit = unit
            self.plot.setLabel('left', unit)

    def set_data(self, freqs_hz, db, waterfall_row=True):
        freqs_hz = np.asarray(freqs_hz, dtype=np.float64)
        db = np.asarray(db, dtype=np.float64)
        if not len(db):
            return
        x = freqs_hz / self.scale
        self._x = x
        self._db = db
        self.curve.setData(x, db)
        if self.peak_check.isChecked():
            if self._peak is None or len(self._peak) != len(db):
                self._peak = db.copy()
            else:
                np.maximum(self._peak, db, out=self._peak)
            self.peak_curve.setData(x, self._peak)
        if self.wf_plot is not None and waterfall_row and self.wf_check.isChecked():
            self._add_row(x, db)

    def clear(self):
        self.curve.setData([], [])
        self.peak_curve.setData([], [])
        self._peak = None
        self._wf = None
        self._hist = None
        self._x = None
        if self.wf_plot is not None:
            self.wf_image.clear()

    #: Auto scale: the scale reaches this far past the trace, top and
    #: bottom, and is never narrower than the least range.
    AUTO_MARGIN_DB = 10.0
    AUTO_MIN_RANGE_DB = 30.0

    def auto_scale(self, keep_ref=False):
        """Fit the scale to the trace on screen and centre it there: from
        its noise floor (the 5th percentile) to its peak, with a margin
        either side. ``keep_ref`` (AGC holds the Ref level) sets the Range
        only, as near centred as that allows. The span is left alone.
        True if there was a trace to fit."""
        if self._x is None or not len(self._x):
            return False
        (x0, x1), _ = self.plot.getPlotItem().getViewBox().viewRange()
        shown = self._db[(self._x >= x0) & (self._x <= x1)]
        shown = shown[np.isfinite(shown)]
        if not len(shown):
            return False
        floor, peak = float(np.percentile(shown, 5)), float(np.max(shown))
        middle = (floor + peak) / 2
        if keep_ref:
            span = 2 * (self.ref_knob.value() - middle)
        else:
            span = peak - floor + 2 * self.AUTO_MARGIN_DB
        span = 5 * math.ceil(max(span, self.AUTO_MIN_RANGE_DB) / 5)
        self.range_knob.setValue(min(max(span, self.range_knob._min), self.range_knob._max))
        if not keep_ref:
            top = round(middle + self.range_knob.value() / 2)
            self.ref_knob.setValue(min(max(top, self.ref_knob._min), self.ref_knob._max))
        return True

    def clear_peak(self):
        self._peak = None
        self.peak_curve.setData([], [])

    #: The density map's colours run, on a log scale, from one hit in ten
    #: thousand to a quarter of a column's: the noise spreads a column's
    #: hits over dozens of levels, and a steady carrier takes an eighth of
    #: a column (525 columns over 4,200 points at 10 kHz RBW).
    DENSITY_LOG_RANGE = (-4.0, -0.6)

    def set_density(self, frame, extent, alpha=None):
        """Draw a real-time density map behind the trace: ``frame`` rows
        from bottom to top, over ``extent`` = (low Hz, high Hz, bottom dB,
        top dB). Where nothing was hit stays clear. ``alpha``, the API's
        persistence (1 just hit, fading to 0), scales each pixel's
        opacity, so a burst fades out where it was."""
        self._density_args = (frame, extent, alpha)
        low_hz, high_hz, bottom, top = extent
        lo, hi = self.DENSITY_LOG_RANGE
        with np.errstate(divide='ignore'):
            level = (np.log10(frame) - lo) / (hi - lo)
        index = np.where(frame > 0, np.clip(np.round(level * 255), 1, 255), 0).astype(np.intp)
        rgba = density_lut()[index]              # the theme's, read now
        if alpha is not None:
            rgba[..., 3] = (rgba[..., 3] * np.clip(alpha, 0.0, 1.0)).astype(np.uint8)
        self.density_item.setImage(rgba, autoLevels=False,
                                   rect=QtCore.QRectF(low_hz / self.scale, bottom,
                                                      (high_hz - low_hz) / self.scale,
                                                      top - bottom))
        self.density_item.setVisible(True)

    def clear_density(self):
        self._density_args = None
        if self.density_item.isVisible():
            self.density_item.setVisible(False)
            self.density_item.clear()

    def add_waterfall_row(self, freqs_hz, db):
        if self.wf_plot is not None and self.wf_check.isChecked():
            self._add_row(np.asarray(freqs_hz) / self.scale, np.asarray(db))

    def _add_row(self, x, db, now=None):
        n = len(db)
        if n > self.WF_COLS:
            # Max-pool k bins to a column: a narrow carrier must not vanish.
            k = int(math.ceil(n / self.WF_COLS))
            cols = int(math.ceil(n / k))
            padded = np.concatenate([db, np.full(k * cols - n, db[-1])])
            row = padded.reshape(cols, k).max(axis=1)
        else:
            row = db
        extent = (x[0], x[-1] + (x[-1] - x[0]) / max(1, n - 1))
        if (self._hist is None or self._hist.rows.shape[1] != len(row)
                or self._wf_extent != extent):
            # A new band or resolution: the history starts again.
            self._hist = _RowRing(self.WF_HISTORY_ROWS, len(row))
            self._coarse = _RowRing(int(self.WF_HISTORY_S / self.WF_COARSE_S) + 2, len(row),
                                    merge_s=self.WF_COARSE_S)
            self._wf_drawn = 0.0
            self._wf_extent = extent
        t = time.monotonic() if now is None else float(now)
        self._hist.add(row, t)
        self._coarse.add(row, t)
        # Every row is drawn while a draw is cheap (the full history, a
        # minute or less on screen); showing more, at most once a row on
        # screen - and a timer sees the newest row drawn even if no more
        # come (Pause).
        wait = self.wf_span_s / self.WF_ROWS
        if wait < self.WF_COARSE_S or t - self._wf_drawn >= wait * 0.999 or self._wf is None:
            self._wf_later.stop()
            self._draw_wf()
        elif not self._wf_later.isActive():
            self._wf_later.start(int(1000 * wait))

    def _draw_wf(self):
        """The rows on screen from the history: row ``b`` covers the ages
        ``b`` to ``b + 1`` screen rows back from the newest, and shows the
        most of every row of data it covers - or, zoomed in past the data's
        own rate, the row that was current then."""
        if self._hist is None or self._hist.n == 0 or self.wf_plot is None:
            return
        span = self.wf_span_s
        # The coarse copy once a quarter second is two rows on screen or less.
        ring = self._coarse if span / self.WF_ROWS >= self.WF_COARSE_S / 2 else self._hist
        order, times = ring.newest_first()
        n = len(order)
        ages = times[0] - times
        keep = min(n, int(np.searchsorted(ages, span, 'left')) + 1)
        order, ages = order[:keep], ages[:keep]
        step = span / self.WF_ROWS
        lows = np.arange(self.WF_ROWS) * step
        starts = np.clip(np.searchsorted(ages, lows, 'right') - 1, 0, keep - 1)
        img = np.maximum.reduceat(ring.rows[order], starts, axis=0).astype(np.float32)
        # Past the oldest row's own stretch there is nothing yet.
        gap = float(np.median(np.diff(ages))) if keep > 1 else step
        img[lows > ages[-1] + max(gap, step)] = np.nan
        self._wf = img
        self._wf_drawn = times[0]
        top = self.ref_knob.value()
        bottom = top - self.range_knob.value()
        extent = self._wf_extent
        # The rectangle goes with every image: set before the first one, it
        # is lost, and the picture is drawn one column per MHz from zero.
        self.wf_image.setImage(np.nan_to_num(img, nan=bottom), autoLevels=False,
                               levels=(bottom, top),
                               rect=QtCore.QRectF(extent[0], 0, extent[1] - extent[0], span))

    # -- markers
    def set_marker(self, hz):
        if hz is None:
            self.marker.setVisible(False)
            return
        self.marker.setValue(hz / self.scale)
        self.marker.setVisible(True)

    def set_band(self, low_hz, high_hz):
        """The channel band, in hertz; None hides it."""
        self._band_hz = None if low_hz is None else (float(low_hz), float(high_hz))
        self._place_band()

    def _place_band(self):
        """Draw the band where it is - but never thinner than
        :attr:`BAND_MIN_PX`, since it is a handle as well as a picture: a
        200 kHz channel on a 6 GHz sweep is a hundredth of a pixel, and
        nothing the pointer could find."""
        if self._band_hz is None:
            self.band.setVisible(False)
            return
        low, high = self._band_hz
        least = self.BAND_MIN_PX * self._hz_per_pixel()
        if high - low < least:
            middle = (low + high) / 2
            low, high = middle - least / 2, middle + least / 2
        self.band.setRegion((low / self.scale, high / self.scale))
        self.band.setVisible(True)

    def _hz_per_pixel(self):
        vb = self.plot.getPlotItem().getViewBox()
        (x0, x1), _ = vb.viewRange()
        return (x1 - x0) * self.scale / max(1.0, vb.width())

    def eventFilter(self, obj, event):
        try:
            kind = event.type()
            if obj is self.plot.viewport():
                if kind == QtCore.QEvent.Leave and self._axis_drag is None:
                    self._light_axis(False)
                return False
            if self.wf_plot is not None:
                if obj is self.wf_plot.viewport():
                    if kind == QtCore.QEvent.Leave:
                        self._light_time_axis(False)
                    return False
                if obj is self.wf_plot.scene():
                    if kind == QtCore.QEvent.GraphicsSceneWheel \
                            and self._over_time_axis(event.scenePos()):
                        fine = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                        self.zoom_time(event.delta() / 120.0, fine)
                        event.accept()
                        return True
                    return False
            if self._axis_event(kind, event):
                return True
            if kind in (QtCore.QEvent.GraphicsSceneMousePress,
                        QtCore.QEvent.GraphicsSceneMouseRelease) \
                    and event.button() == QtCore.Qt.MiddleButton:
                if kind == QtCore.QEvent.GraphicsSceneMousePress:
                    if self._over_band(event.scenePos()):
                        self.tuner_grabbed(True)
                elif self._grabbed:
                    self.tuner_grabbed(False)
        except Exception as exc:                  # never abort the app
            print(f"spectrum view: {exc}")
        return False

    # -- the level axis as a handle
    def _axis_rect(self):
        """The level axis's own strip, in scene coordinates. (Its bounding
        rectangle spans the plot too: the grid is drawn by the axis.)"""
        axis = self.plot.getAxis('left')
        parent = axis.parentItem()
        return parent.mapRectToScene(axis.geometry()) if parent is not None \
            else axis.sceneBoundingRect()

    def _over_axis(self, scene_pos):
        return self._axis_rect().contains(scene_pos)

    def _light_axis(self, on):
        """The level axis's numbers and unit lit (the on-air colour) while
        the pointer is on it, with an up-and-down cursor: it can be wheeled
        and dragged."""
        if on == self._axis_hot:
            return
        self._axis_hot = on
        self._paint_lit(self.plot, self.level_unit, on)

    @staticmethod
    def _paint_lit(plot, label, on):
        t = theme.TOKENS
        axis = plot.getAxis('left')
        ink = _marker_colour('tuner') if on else Qt.QColor(t['ink_2'])
        # The numbers and the name only: the axis's pen draws the grid too.
        axis.setTextPen(pg.mkPen(ink))
        if on:
            axis.setLabel(label, color=ink.name())
            plot.viewport().setCursor(QtCore.Qt.SizeVerCursor)
        else:
            axis.setLabel(label)                # its ordinary colour
            plot.viewport().unsetCursor()

    # -- the waterfall's time scale as a handle
    def _time_axis_rect(self):
        axis = self.wf_plot.getAxis('left')
        parent = axis.parentItem()
        return parent.mapRectToScene(axis.geometry()) if parent is not None \
            else axis.sceneBoundingRect()

    def _over_time_axis(self, scene_pos):
        return self.wf_plot is not None and self._time_axis_rect().contains(scene_pos)

    def _light_time_axis(self, on):
        if on == self._time_hot:
            return
        self._time_hot = on
        self._paint_lit(self.wf_plot, 'time', on)

    def _wf_mouse_moved(self, scene_pos):
        self._light_time_axis(self._over_time_axis(scene_pos))

    def zoom_time(self, notches, fine=False):
        """Show less of the past (``notches`` up) or more (down), "now"
        staying at the top."""
        step = 1 + (self.TIME_ZOOM - 1) / (5 if fine else 1)
        self.set_wf_span(self.wf_span_s * step ** -notches)

    def set_wf_span(self, seconds):
        low, _, high = self.WF_SPAN_S
        self.wf_span_s = float(min(max(seconds, low), high))
        if self.wf_plot is not None:
            self.wf_plot.setYRange(0, self.wf_span_s, padding=0)
            self._draw_wf()

    def _axis_event(self, kind, event):
        """The wheel and the middle button on the level axis; True if the
        event was the axis's."""
        if kind == QtCore.QEvent.GraphicsSceneWheel and self._over_axis(event.scenePos()):
            fine = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
            self.zoom_levels(event.delta() / 120.0, event.scenePos().y(), fine)
            event.accept()
            return True
        middle = QtCore.Qt.MiddleButton
        if kind == QtCore.QEvent.GraphicsSceneMousePress and event.button() == middle \
                and self._over_axis(event.scenePos()):
            self._axis_drag = (event.scenePos().y(), self.ref_knob.value())
            event.accept()
            return True
        if kind == QtCore.QEvent.GraphicsSceneMouseMove and self._axis_drag is not None:
            y0, ref0 = self._axis_drag
            self.drag_levels(ref0, event.scenePos().y() - y0)
            return True
        if kind == QtCore.QEvent.GraphicsSceneMouseRelease and event.button() == middle \
                and self._axis_drag is not None:
            self._axis_drag = None
            self._light_axis(self._over_axis(event.scenePos()))
            return True
        return False

    def zoom_levels(self, notches, scene_y, fine=False):
        """Zoom the amplitude scale ``notches`` wheel notches (up: in)
        about the level at ``scene_y``, which stays where it is on screen."""
        vb = self.plot.getPlotItem().getViewBox()
        rect = vb.sceneBoundingRect()
        level = vb.mapSceneToView(QtCore.QPointF(rect.center().x(), scene_y)).y()
        step = 1 + (self.AXIS_ZOOM - 1) / (5 if fine else 1)
        top, span = self.ref_knob.value(), self.range_knob.value()
        new_span = min(max(span * step ** -notches, self.range_knob._min), self.range_knob._max)
        new_top = level + (top - level) * new_span / span
        self.range_knob.setValue(round(new_span, 1))
        self.ref_knob.setValue(min(max(round(new_top, 1), self.ref_knob._min),
                                   self.ref_knob._max))

    def drag_levels(self, ref0, pixels):
        """The scale dragged ``pixels`` down (negative: up) from where it
        was, at Ref level ``ref0``: the trace goes with the pointer."""
        vb = self.plot.getPlotItem().getViewBox()
        per_pixel = self.range_knob.value() / max(1.0, vb.height())
        ref = round((ref0 + pixels * per_pixel) * 2) / 2
        self.ref_knob.setValue(min(max(ref, self.ref_knob._min), self.ref_knob._max))

    def _over_band(self, scene_pos):
        if not self.band.isVisible():
            return False
        vb = self.plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return False
        low, high = self.band.getRegion()
        return low <= vb.mapSceneToView(scene_pos).x() <= high

    # -- the fades
    def set_marker_auto(self, auto):
        """True (Receive): the tuner's marker hides, and shows only while the
        tuner moves or is held. False (Sweep): always shown."""
        self.marker_auto = bool(auto)
        self._marker_away.stop()
        self._grabbed = False
        self._marker_fade.set(0.0 if auto else 1.0)

    def tuner_moving(self):
        """The tuner moved: show its marker, and hide it again once it has
        been still a while - unless it is being held."""
        if not self.marker_auto:
            return
        self._marker_fade.to(1.0, self.FADE_FAST_MS)
        if not self._grabbed:
            self._marker_away.start(self.TUNER_SETTLE_MS)

    def tuner_grabbed(self, held):
        """The middle button went down on the channel band, or came up."""
        self._grabbed = bool(held)
        if not self.marker_auto:
            return
        if held:
            self._marker_away.stop()
            self._marker_fade.to(1.0, self.FADE_FAST_MS)
        else:
            self._marker_away.start(self.TUNER_SETTLE_MS)

    def _marker_settled(self):
        if self.marker_auto and not self._grabbed:
            self._marker_fade.to(0.0, self.MARKER_AWAY_MS)

    def center_moving(self):
        """The radio's centre is being moved: fade its line out of the way,
        and bring it back once the centre has been still a while."""
        self._center_fade.to(0.0, self.FADE_FAST_MS)
        self._center_back.start(self.CENTER_SETTLE_MS)

    def set_center_line(self, hz):
        """The radio's centre frequency, as a dashed line; None hides it."""
        if hz is not None:
            self.center_line.setValue(hz / self.scale)
        self.center_line.setVisible(hz is not None)

    def set_tuner_range(self, low_hz, high_hz):
        """Shade what lies outside ``low_hz``-``high_hz``, where the tuner
        cannot go; None clears it."""
        if low_hz is None:
            for region in self.outside:
                region.setVisible(False)
            return
        far = 1e12 / self.scale
        self.outside[0].setRegion((-far, low_hz / self.scale))
        self.outside[1].setRegion((high_hz / self.scale, far))
        for region in self.outside:
            region.setVisible(True)

    def set_pan_limits(self, low_hz, high_hz):
        """How far the mouse may drag or zoom the view out: to ``low_hz`` and
        ``high_hz``; None lets it go anywhere. The waterfall gets the same
        limits - it is linked to the spectrum, and dragging it pans both."""
        low = None if low_hz is None else low_hz / self.scale
        high = None if high_hz is None else high_hz / self.scale
        for plot in (self.plot, self.wf_plot):
            if plot is not None:
                plot.getPlotItem().getViewBox().setLimits(xMin=low, xMax=high)

    def set_message(self, text):
        self.message.setText(text)
        vb = self.plot.getPlotItem().getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        self.message.setPos((x0 + x1) / 2, (y0 + y1) / 2)

    # -- view state
    def state(self):
        return {'span_hz': self.span_knob.value(), 'ref_db': self.ref_knob.value(),
                'range_db': self.range_knob.value(), 'avg': int(self.avg_knob.value()),
                'peak_hold': self.peak_check.isChecked(),
                'waterfall': self.wf_check.isChecked(), 'wf_span_s': self.wf_span_s}

    def load_state(self, state):
        if not isinstance(state, dict):
            return
        try:
            if 'ref_db' in state:
                self.ref_knob.setValue(float(state['ref_db']))
            if 'range_db' in state:
                self.range_knob.setValue(float(state['range_db']))
            if 'avg' in state:
                self.avg_knob.setValue(float(state['avg']))
            if 'span_hz' in state:
                self._wanted_span = float(state['span_hz'])
                self.span_knob.setValue(self._wanted_span, emit=False)
            self.peak_check.setChecked(bool(state.get('peak_hold', False)))
            self.wf_check.setChecked(bool(state.get('waterfall', True)))
            if 'wf_span_s' in state:
                self.set_wf_span(float(state['wf_span_s']))
        except Exception as exc:
            print(f"spectrum view: ignoring saved state: {exc}")

    # -- internals
    def _apply_levels(self):
        top = self.ref_knob.value()
        bottom = top - self.range_knob.value()
        self.plot.setYRange(bottom, top, padding=0)
        if self.wf_plot is not None and self._wf is not None:
            self.wf_image.setLevels((bottom, top))

    def _span_changed(self, _value):
        self._wanted_span = None                    # turned since: that stands
        self._apply_span()

    def _apply_span(self):
        low, high = self.full
        span = min(self.span_knob.value(), high - low)
        centre = min(max(self.center_hz, low + span / 2), high - span / 2)
        self._syncing = True
        try:
            self.plot.setXRange((centre - span / 2) / self.scale,
                                (centre + span / 2) / self.scale, padding=0)
        finally:
            self._syncing = False

    def _range_changed(self, _vb, rng):
        # Zoomed out, the band may now be thinner than it is drawn.
        self._place_band()
        if self._syncing:
            return
        x0, x1 = rng
        span = (x1 - x0) * self.scale
        if span <= 0:
            return
        # The mouse zoomed or panned: the dial follows, and so does the centre.
        self.center_hz = (x0 + x1) / 2 * self.scale
        self.span_knob.setValue(min(max(span, self.span_knob._min), self.span_knob._max),
                                emit=False)

    def _to_hz(self, scene_pos):
        for plot in (self.plot, self.wf_plot):
            if plot is None:
                continue
            vb = plot.getPlotItem().getViewBox()
            if vb.sceneBoundingRect().contains(scene_pos):
                return vb.mapSceneToView(scene_pos).x() * self.scale
        return None

    def _scene_clicked(self, event):
        if event.button() not in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            return
        hz = self._to_hz(event.scenePos())
        if hz is None:
            return
        if self.snap_hz:
            hz = on_raster(hz, self.snap_hz)
        if event.button() == QtCore.Qt.RightButton:
            self._offer_tuner(hz, event.screenPos())
            return
        if event.double():
            self.activated.emit(hz)
        else:
            self.clicked.emit(hz)

    def _offer_tuner(self, hz, screen_pos):
        """Right click: offer to put the tuner where the pointer is. The
        frequency is on the item, so it is plain where it would land - the
        one Snap would round to, if Snap is on."""
        if self._menu is None:
            return
        self._menu_hz = hz
        self._tune_action.setText(f"Tuner to {hz / self.scale:.3f} {self.unit}")
        self._menu.popup(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))

    def _tuner_chosen(self):
        # Nothing to go on if the item is somehow reached without the menu
        # having been offered over the plot.
        if self._menu_hz is not None:
            self.tunerRequested.emit(self._menu_hz)

    def _plot_mouse_moved(self, scene_pos):
        # The spectrum's own scene: the waterfall's moves come to
        # _mouse_moved too, where its time scale sits as the level axis does.
        if self._axis_drag is None:
            self._light_axis(self._over_axis(scene_pos))

    def _mouse_moved(self, scene_pos):
        hz = self._to_hz(scene_pos)
        if hz is None or self._x is None or not len(self._x):
            self._set_readout('')
            return
        i = int(np.clip(np.searchsorted(self._x, hz / self.scale), 0, len(self._x) - 1))
        value = f"{hz / self.scale:.3f} {self.unit}"
        self._set_readout(f"{value}   {self._db[i]:.1f} {self.level_unit}")

    def _set_readout(self, text):
        self.readout.setText(text)
        self.readout.setVisible(bool(text))      # no empty chip
        self.readout.adjustSize()
        self._place_readout()

    def _place_readout(self, *_):
        """Keep the readout in the bottom-left corner of the plot's box."""
        try:
            vb = self.plot.getPlotItem().getViewBox()
            corner = self.plot.mapFromScene(vb.sceneBoundingRect().bottomLeft())
            self.readout.move(corner.x() + 6, corner.y() - self.readout.height() - 4)
            self.readout.raise_()
        except Exception as exc:                  # never abort the app
            print(f"spectrum view: {exc}")

    def _peak_toggled(self, on):
        if not on:
            self.clear_peak()

    def _wf_toggled(self, on):
        if self.wf_plot is not None:
            self.wf_plot.setVisible(on)


# ---------------------------------------------------------- timeline strip

class TimelineStrip(Qt.QWidget):
    """A recording from end to end, as a waterfall lying on its side - time
    left to right, frequency up the strip, in the theme's waterfall colours
    - with the playhead across it. It is the seek bar: click or drag, and
    :attr:`seekRequested` (seconds) goes when the button comes up; the
    playhead follows the pointer until then.

    With no overview yet (it is worked out in the background) it is a plain
    track that seeks all the same."""

    seekRequested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setMinimumWidth(160)
        self.setSizePolicy(Qt.QSizePolicy.Expanding, Qt.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("The whole recording: time left to right, frequency "
                        "upwards.\nClick or drag to jump.")
        self.duration = 0.0
        self.position = 0.0
        self._index = None                  # (rows, cols) uint8, top row first
        self._image = None
        self._image_theme = None
        self._drag_x = None
        self.note = ''

    def set_overview(self, img):
        """``img``: (rows, columns) in dB, row 0 the lowest frequency. The
        colours span the 5th to the 99.7th percentile, so the floor sinks
        into the background and the strongest signals are the brightest."""
        if img is None:
            self._index = None
        else:
            img = np.asarray(img, dtype=np.float64)
            low, high = np.percentile(img, 5), np.percentile(img, 99.7)
            level = (img - low) / max(high - low, 1e-6)
            self._index = np.ascontiguousarray(
                np.round(np.clip(level, 0, 1) * 255).astype(np.uint8)[::-1])
        self._image = None
        self.update()

    def set_duration(self, seconds):
        self.duration = max(0.0, float(seconds))
        self.update()

    def set_position(self, seconds):
        seconds = float(seconds)
        if self.duration and self.width() and \
                abs(seconds - self.position) * self.width() / self.duration < 0.5:
            self.position = seconds                # under a pixel: no repaint
            return
        self.position = seconds
        self.update()

    def set_note(self, text):
        self.note = text
        self.update()

    def _frame(self):
        return QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _time_at(self, x):
        frame = self._frame()
        if frame.width() <= 0 or not self.duration:
            return 0.0
        return min(max((x - frame.left()) / frame.width(), 0.0), 1.0) * self.duration

    # -- mouse
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.duration:
            self._drag_x = event.pos().x()
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag_x is not None:
            self._drag_x = event.pos().x()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_x is not None and event.button() == QtCore.Qt.LeftButton:
            seconds = self._time_at(event.pos().x())
            self._drag_x = None
            self.position = seconds
            self.update()
            self.seekRequested.emit(seconds)

    # -- paint
    def paintEvent(self, event):
        try:
            self._paint()
        except Exception as exc:
            print(f"timeline paint: {exc}")

    def _colours(self):
        """The overview in the theme's waterfall colours, made again only
        when the theme changes."""
        name = theme.current()
        if self._image is None or self._image_theme != name:
            lut = waterfall_lut(WATERFALL.get(name, WATERFALL['slate']))
            rgb = np.ascontiguousarray(lut[self._index])
            h, w = self._index.shape
            self._image = Qt.QImage(rgb.data, w, h, 3 * w, Qt.QImage.Format_RGB888).copy()
            self._image_theme = name
        return self._image

    def _paint(self):
        frame = self._frame()
        if frame.width() < 4 or frame.height() < 4:
            return
        t = theme.TOKENS
        p = Qt.QPainter(self)
        p.setRenderHint(Qt.QPainter.SmoothPixmapTransform, True)
        p.fillRect(frame, colour('well'))
        if self._index is not None and self._index.size:
            p.drawImage(frame, self._colours())
        elif self.note:
            p.setPen(colour('ink_3'))
            p.drawText(frame, QtCore.Qt.AlignCenter, self.note)
        if self.duration:
            x = (self._drag_x if self._drag_x is not None else
                 frame.left() + frame.width() * min(self.position / self.duration, 1.0))
            x = min(max(x, frame.left() + 1), frame.right() - 1)
            # What has played is dimmed; the playhead is in the ink, apart
            # from any colour the waterfall can reach.
            played = Qt.QColor(t['ground'])
            played.setAlpha(120)
            p.fillRect(QtCore.QRectF(frame.left(), frame.top(), x - frame.left(),
                                     frame.height()), played)
            p.setPen(Qt.QPen(Qt.QColor(t['ink']), 2))
            p.drawLine(QtCore.QPointF(x, frame.top()), QtCore.QPointF(x, frame.bottom()))
        p.setPen(Qt.QPen(Qt.QColor(t['rule']), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(frame)
        p.end()
