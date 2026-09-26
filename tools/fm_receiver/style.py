"""Paint the window from the shared theme tokens.

Adapted from the RF bench toolkit's ``apply_flowgraph_theme``,
``themed_icon_url`` and ``ClickToMove`` (``apps/utils.py``). Three rules from
there hold here too:

- **A colour comes from ``theme.TOKENS`` when something is drawn**, never
  copied at import: the theme can change under a running window.
- **The stylesheet sets no font on QWidget or QLabel.** The face is the
  application font, so a label that sets its own font - the monospace
  RadioText, the large station name - keeps it.
- **A control moves only once it has been clicked** (:class:`ClickToMove`):
  a stylesheet with hover rules turns on mouse tracking, and a wheel turned
  to scroll the window would otherwise move whatever slider or dial it
  passed over - RF gain, volume, frequency.
"""

import os
import tempfile

from PyQt5 import Qt  # type: ignore
from PyQt5.QtCore import QEvent, QObject, Qt as QtNs  # type: ignore

from . import ASSET_DIR, theme

ICON_DIR = os.path.join(ASSET_DIR, 'icons')


def icon_url(name):
    """An icon's path in the form a stylesheet ``url()`` wants: absolute,
    forward slashes."""
    return os.path.join(ICON_DIR, name).replace('\\', '/')


def themed_icon_url(name, colour):
    """``icons/<name>`` redrawn in ``colour`` - the spin arrows and the tick
    are pictures with their colour baked in, and must follow the theme.
    Written once to the temp directory under a name carrying the colour."""
    stem, ext = os.path.splitext(name)
    user = str(os.getuid()) if hasattr(os, 'getuid') else ''
    folder = os.path.join(tempfile.gettempdir(), 'fm-receiver-icons-' + user)
    target = os.path.join(folder, '%s-%s%s'
                          % (stem, colour.lstrip('#').lower(), ext))
    if not os.path.exists(target):
        image = Qt.QImage(os.path.join(ICON_DIR, name))
        if image.isNull():
            return icon_url(name)
        image = image.convertToFormat(Qt.QImage.Format_ARGB32_Premultiplied)
        painter = Qt.QPainter(image)
        painter.setCompositionMode(Qt.QPainter.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), Qt.QColor(colour))
        painter.end()
        try:
            os.makedirs(folder, exist_ok=True)
            partial = '%s.%d.tmp' % (target, os.getpid())
            if not image.save(partial, 'PNG'):
                return icon_url(name)
            os.replace(partial, target)
        except OSError:
            return icon_url(name)
    return target.replace('\\', '/')


def _control_pictures():
    ink, ground = theme.TOKENS['ink'], theme.TOKENS['ground']
    return (themed_icon_url('spin-up.png', ink),
            themed_icon_url('spin-down.png', ink),
            themed_icon_url('check.png', ground))


# What this window adds to the toolkit's flowgraph stylesheet: the mode
# tabs, a button that stays down (Mute, Record, Pause), the small step
# buttons and the station list.
_EXTRA_QSS = """
QTabWidget::pane { border: 1px solid %(rule)s; border-radius: 2px;
    background: %(panel)s; top: -1px; }
QTabBar::tab { background: %(ground)s; color: %(ink_2)s;
    border: 1px solid %(rule)s; border-bottom: none; padding: 7px 16px;
    margin-right: 2px; border-top-left-radius: 2px;
    border-top-right-radius: 2px; }
QTabBar::tab:selected { background: %(panel)s; color: %(ink)s; }
QTabBar::tab:hover { color: %(ink)s; }
QTabWidget > QWidget, QTabWidget QStackedWidget > QWidget {
    background: %(panel)s; }
QPushButton:checked { background: %(live)s; color: %(ground)s;
    border-color: %(live)s; }
QPushButton#mute:checked { background: %(bad)s; border-color: %(bad)s; }
QPushButton#small { min-width: 0px; padding: 6px 10px; }
QListWidget { background: %(well)s; color: %(ink)s;
    border: 1px solid %(rule)s; border-radius: 2px; }
QListWidget::item { padding: 3px 6px; }
QListWidget::item:selected { background: %(rule)s; color: %(ink)s; }
QSplitter::handle { background: %(ground)s; }
"""


#: Reading Room's Stop/Start is in ink: grey on grey, it looked disabled.
_READING_ROOM_QSS = """
QPushButton#run { background: %(ink)s; color: %(panel)s; border-color: %(ink)s; }
QPushButton#run:hover { background: %(ink_0)s; }
"""


def window_qss():
    extra = _READING_ROOM_QSS if theme.current() == 'reading-room' else ''
    return (theme.flowgraph_qss(*_control_pictures())
            + (_EXTRA_QSS + extra) % dict(theme.TOKENS))


def apply_window_theme(window, name=None):
    """Paint ``window`` in theme ``name`` (or the one in force).

    Call it first in the window's ``__init__``, before any widget exists:
    the application font has to be in place before a widget copies it.
    Calling it again later switches the theme; the plots are recoloured by
    their own ``restyle`` methods.
    """
    if name:
        theme.use(name)
    theme.load_fonts()
    app = Qt.QApplication.instance()
    if app is not None:
        font = Qt.QFont(theme.TOKENS['f_ui'])
        font.setPixelSize(theme.TOKENS['s_md'])
        app.setFont(font)
    window.setAttribute(QtNs.WA_StyledBackground, True)
    window.setStyleSheet(window_qss())
    if not getattr(window, '_click_to_move', None):
        window._click_to_move = ClickToMove(window)
        window.installEventFilter(window._click_to_move)


def colour(token):
    """A theme colour as a QColor, read now."""
    return Qt.QColor(theme.TOKENS[token])


class ClickToMove(QObject):
    """A slider, dial, spin box or combo in the window moves only after it
    has been clicked: a mouse move with no button down is dropped, and a
    wheel turn over an unfocused one goes on to whatever scrolls behind it.
    Taken from the RF bench toolkit, where passing the mouse over a power
    slider once set the power. Guards the controls the first time the
    window is shown, and :meth:`guard` takes on any made later.

    FM receiver: a control with the ``wheel_on_hover`` property is left
    alone - the knobs, which are asked to turn under the pointer, and take
    the wheel themselves so nothing behind them scrolls."""

    CONTROLS = (Qt.QAbstractSlider, Qt.QAbstractSpinBox, Qt.QComboBox)

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._guarded = False

    def guard(self, root=None):
        for control in (root or self._window).findChildren(self.CONTROLS):
            if isinstance(control, Qt.QScrollBar):
                continue
            if control.property('_fmrx_guarded') or control.property('wheel_on_hover'):
                continue
            control.setProperty('_fmrx_guarded', True)
            if control.focusPolicy() == QtNs.WheelFocus:
                control.setFocusPolicy(QtNs.StrongFocus)
            control.installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self._window:
            if kind == QEvent.Show and not self._guarded:
                self._guarded = True
                self.guard()
            return False
        if kind == QEvent.MouseMove and not int(event.buttons()) \
                and isinstance(obj, Qt.QAbstractSlider):
            return True
        if kind == QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False
