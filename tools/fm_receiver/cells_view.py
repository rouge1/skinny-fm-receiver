"""The Recordings tab's cells pane: what a recording's LTE and 5G NR cells were heard sending.

It takes the multiplex's place under the waterfall (``app_cells``) for an IQ recording holding
cells. One layout in every state, so nothing jumps: a heading with the button on its right
(Read cells, Stop while reading, Read again), then per technology a grid whose columns are the
cells, strongest first - their level against the strongest, their identity (operator, tracking
area, cell), a rule, a row per message (MIB, SIB1, the SIBs scheduled) and how each was read.
Marks fill in as each cell is read. A mark opens what is behind it under the grid. A sync signal
that is not counted as a cell (``cells``' ``unconfirmed``) is a quiet line under the grid, not a
column; it opens the same way (row ``sync``).

The look of it only: the models come from ``cells``. Colours are the stylesheet's (``style``,
the ``cells*`` object names) or read from ``theme.TOKENS`` when painted; ``restyle`` sets the
faces again when the theme changes.
"""

from PyQt5 import Qt, QtCore, QtGui  # type: ignore
from PyQt5.QtCore import pyqtSignal  # type: ignore

from . import theme
from .widgets import fade

#: How long the detail glides open, and the grid's old picture fades over the new.
GLIDE_MS = 240
#: The level bar's scale: a cell this far under the strongest has an empty bar.
BAR_DB = 20.0
KEY = (('ok', "decoded, with how many copies"),
       ('match', "not decoded; its signal fits a stronger cell's bytes"),
       ('missing', "not found"))


def _font(widget, face='f_ui', size='s_md', weight=QtGui.QFont.Normal):
    """Set ``widget``'s font from the theme, and remember how, for ``restyle``."""
    widget.setProperty('cells_font', (face, size, int(weight)))
    _apply_font(widget)
    return widget


def _apply_font(widget):
    face, size, weight = widget.property('cells_font')
    f = QtGui.QFont(theme.TOKENS[face])
    f.setPixelSize(int(theme.TOKENS[size]))
    f.setWeight(int(weight))
    widget.setFont(f)


def _label(text, name='cellsValue', face='f_ui', size='s_md', weight=QtGui.QFont.Normal):
    lb = Qt.QLabel(text)
    lb.setObjectName(name)
    return _font(lb, face, size, weight)


def _rule(name='cellsRule'):
    line = Qt.QFrame()
    line.setObjectName(name)
    line.setFixedHeight(1)
    return line


def _paint_glyph(p, kind, rect):
    """A filled dot (decoded), a ring (matched), a dashed ring (not found)."""
    r = QtCore.QRectF(rect)
    if kind == 'ok':
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(theme.TOKENS['live']))
    elif kind in ('match', 'missing', 'unsure'):
        pen = QtGui.QPen(QtGui.QColor(theme.TOKENS['ink' if kind == 'match' else 'ink_3']), 1.6)
        if kind != 'match':
            pen.setDashPattern([1.5, 1.8])
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
    else:
        return
    p.drawEllipse(r)


class Glyph(Qt.QWidget):
    """A mark on its own, for the key."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setFixedSize(14, 14)

    def paintEvent(self, _event):
        try:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            _paint_glyph(p, self.kind, QtCore.QRectF(2, 2, 10, 10))
            p.end()
        except Exception:                                   # never out of a paint override
            pass


class MarkCell(Qt.QAbstractButton):
    """One message of one cell: its mark and its words. A click (or Space) opens its detail;
    the open one sits on a tile."""

    def __init__(self, mark, parent=None):
        super().__init__(parent)
        self.mark = mark or {}
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor if self.mark.get('detail') else QtCore.Qt.ArrowCursor)
        self.setEnabled(bool(self.mark.get('detail')))         # nothing to open: not a button
        self.setFocusPolicy(QtCore.Qt.StrongFocus if self.mark.get('detail') else QtCore.Qt.NoFocus)
        self.setAccessibleName(self.mark.get('text') or '')
        _font(self, 'f_num' if self.mark.get('mark') == 'ok' else 'f_ui')
        self.setSizePolicy(Qt.QSizePolicy.Preferred, Qt.QSizePolicy.Fixed)

    def _text(self):
        return self.mark.get('text') or ('–' if not self.mark else '')

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self.font())
        return QtCore.QSize(26 + fm.horizontalAdvance(self._text()) + 8, max(22, fm.height() + 6))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _event):
        try:
            t = theme.TOKENS
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            if self.isChecked() or (self.underMouse() and self.mark.get('detail')):
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(t['panel_2']))
                p.drawRoundedRect(r, 3, 3)
            if self.hasFocus():
                p.setPen(QtGui.QPen(QtGui.QColor(t['ink_2']), 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 3, 3)
            mid = self.height() / 2.0
            _paint_glyph(p, self.mark.get('mark'), QtCore.QRectF(6, mid - 5, 10, 10))
            kind = self.mark.get('mark')
            p.setPen(QtGui.QColor(t['ink'] if kind == 'ok' else t['ink_3'] if kind in (None, 'span')
                                  else t['ink_2']))
            p.setFont(self.font())
            x = 26 if kind in ('ok', 'match', 'missing', 'unsure') else 6
            p.drawText(QtCore.QRectF(x, 0, self.width() - x, self.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self._text())
            p.end()
        except Exception:
            pass

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class Strength(Qt.QWidget):
    """A cell's level against the strongest: a bar on a ``BAR_DB`` scale, and the number."""

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setProperty('cells_font', ('f_num', 's_sm', QtGui.QFont.Normal))
        _apply_font(self)
        self.setFixedHeight(20)
        self.setMinimumWidth(150)

    def paintEvent(self, _event):
        try:
            t = theme.TOKENS
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            w = 96.0
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(t['rule_soft']))
            p.drawRoundedRect(QtCore.QRectF(0, 7, w, 6), 2, 2)
            if self.db is not None:
                share = max(0.0, min(1.0, 1.0 + self.db / BAR_DB))
                p.setBrush(QtGui.QColor(t['trace']))
                p.drawRoundedRect(QtCore.QRectF(0, 7, max(2.0, w * share), 6), 2, 2)
                p.setPen(QtGui.QColor(t['ink_2']))
                p.setFont(self.font())
                text = '0 dB' if abs(self.db) < 0.05 else f"−{abs(self.db):.0f} dB"
                p.drawText(QtCore.QRectF(w + 10, 0, 80, 20), QtCore.Qt.AlignVCenter, text)
            p.end()
        except Exception:
            pass


class CellsPane(Qt.QWidget):
    """The pane. ``show_*`` set its state; ``readRequested`` and ``stopRequested`` are its
    button; ``state()`` says what it shows, for ``fmctl cells-pane``."""

    readRequested = pyqtSignal()
    stopRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('cellsPane')
        self._models = []
        self._state = 'empty'
        self._status = ''
        self._open = None                         # (model index, pci, row key) whose detail shows
        self._cells = {}                          # the same key -> its MarkCell
        box = Qt.QVBoxLayout(self)
        box.setContentsMargins(56, 10, 24, 6)
        box.setSpacing(6)

        head = Qt.QHBoxLayout()
        head.setSpacing(12)
        self.title = _label('', 'cellsTitle', weight=QtGui.QFont.DemiBold)
        self.status = _label('', 'cellsQuiet')
        self.bar = Qt.QProgressBar()
        self.bar.setObjectName('cellsBar')
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(180, 6)
        self.button = Qt.QPushButton('Read cells')
        self.button.setObjectName('cellsAction')
        self.button.clicked.connect(self._button)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.status)
        head.addWidget(self.bar)
        head.addWidget(self.button)
        box.addLayout(head)

        self.scroll = Qt.QScrollArea()
        self.scroll.setObjectName('cellsScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(Qt.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        box.addWidget(self.scroll, 1)
        self.body = None
        self.bar.hide()
        self.button.hide()
        self._build()

    # ------------------------------------------------------------- states
    def show_found(self, models, seconds=None):
        how_long = (f"Takes about {max(1, round(seconds / 60))} minute{'s' if round(seconds / 60) > 1 else ''}"
                    if seconds else 'Takes a minute or two')
        self._set('found', models, how_long + '; the app stays usable', 'Read cells')

    def show_reading(self, models, text, fraction):
        self._set('reading', models, text, 'Stop')
        self.bar.setValue(int(round(1000 * max(0.0, min(1.0, fraction)))))

    def show_read(self, models, text):
        self._set('read', models, text, 'Read again')

    def show_failed(self, models, text):
        self._set('failed', models, text, 'Read cells')

    def state(self):
        """What the pane shows, as JSON."""
        return {'state': self._state, 'title': self.title.text(), 'status': self._status,
                'button': self.button.text() if not self.button.isHidden() else None,
                'progress': self.bar.value() / 1000.0 if not self.bar.isHidden() else None,
                'open': None if self._open is None else {'model': self._open[0], 'pci': self._open[1],
                                                        'row': self._open[2]},
                'models': self._models}

    def open_detail(self, model_index, pci, row):
        """Open (or, if open, close) a mark's detail, as a click does. False when there is none."""
        cell = self._cells.get((model_index, pci, row))
        if cell is None or not cell.mark.get('detail'):
            return False
        cell.click()
        return True

    def restyle(self):
        for w in [self] + self.findChildren(Qt.QWidget):
            if w.property('cells_font') is not None:
                _apply_font(w)
                w.updateGeometry()
        self.update()

    # ------------------------------------------------------------- inside
    def _button(self):
        (self.stopRequested if self._state == 'reading' else self.readRequested).emit()

    def _set(self, state, models, status, button):
        changed = models != self._models
        self._state, self._status = state, status
        self.status.setText(status)
        self.status.setObjectName('cellsWarn' if state == 'failed' else 'cellsQuiet')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.bar.setVisible(state == 'reading')
        if button:
            self.button.setText(button)
        self.button.setVisible(button is not None)
        if models:
            titles = [m['title'] for m in models]
            self.title.setText(titles[0] if len(titles) == 1 else ' and '.join(
                m['title'].split(':')[0] for m in models))
        else:
            self.title.setText('Cells')
        if changed:
            self._models = [dict(m) for m in models]
            self._rebuild()

    def _rebuild(self):
        """A new grid; the old one's picture fades over it, so a change is followed, not jumped."""
        old = None
        if self.isVisible() and self.body is not None:
            old = Qt.QLabel(self.scroll.viewport())
            old.setPixmap(self.scroll.viewport().grab())
            old.setGeometry(self.scroll.viewport().rect())
            old.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        at = self.scroll.verticalScrollBar().value()
        self._build()
        self.scroll.verticalScrollBar().setValue(at)
        if old is not None:
            old.show()
            old.raise_()
            fade(old, False, GLIDE_MS, done=old.deleteLater)

    def _build(self):
        body = Qt.QWidget()
        body.setObjectName('cellsBody')
        col = Qt.QVBoxLayout(body)
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(10)
        self._cells = {}
        many = len(self._models) > 1
        for mi, m in enumerate(self._models):
            if many:
                col.addWidget(_label(m['title'], 'cellsTitle', weight=QtGui.QFont.DemiBold))
            col.addLayout(self._grid(mi, m))
        self.detail = Qt.QFrame()
        self.detail.setObjectName('cellsDetail')
        self.detail_box = Qt.QVBoxLayout(self.detail)
        self.detail_box.setContentsMargins(14, 10, 14, 12)
        self.detail_box.setSpacing(4)
        self.detail.hide()
        col.addWidget(self.detail)
        if self._models:
            key = Qt.QHBoxLayout()
            key.setSpacing(6)
            for kind, text in KEY:
                key.addWidget(Glyph(kind))
                key.addWidget(_label(text, 'cellsQuiet', size='s_sm'))
                key.addSpacing(16)
            key.addStretch(1)
            col.addLayout(key)
        col.addStretch(1)
        self.body = body
        self.scroll.setWidget(body)
        if self._open is not None and self._open in self._cells and self._cells[self._open].mark.get('detail'):
            cell = self._cells[self._open]
            cell.setChecked(True)
            self._fill_detail(self._open, cell.mark)
            self.detail.show()
        else:
            self._open = None

    def _grid(self, mi, m):
        grid = Qt.QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        grid.setColumnMinimumWidth(0, 160)
        sectors = m['sectors']
        for c, s in enumerate(sectors, start=1):
            head = _label(f"PCI {s['pci']}", 'cellsValue', 'f_num', weight=QtGui.QFont.DemiBold)
            grid.addWidget(head, 0, c)
            grid.addWidget(Strength(s.get('rel_db')), 1, c)
            grid.setColumnMinimumWidth(c, 200)
        grid.setColumnStretch(len(sectors) + 1, 1)
        r = 2
        for key, name in (('operator', 'Operator'), ('area', 'Tracking area'), ('cell', 'Cell identity')):
            grid.addWidget(_label(name, 'cellsName'), r, 0)
            for c, s in enumerate(sectors, start=1):
                value = (s.get('identity') or {}).get(key)
                lb = _label(value or '–', 'cellsValue' if value else 'cellsQuiet', 'f_num')
                lb.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                grid.addWidget(lb, r, c)
            r += 1
        grid.addWidget(_rule(), r, 0, 1, len(sectors) + 2)
        r += 1
        for key, name in m['rows']:
            grid.addWidget(_label(name, 'cellsName'), r, 0)
            for c, s in enumerate(sectors, start=1):
                mark = (s.get('marks') or {}).get(key)
                cell = MarkCell(mark)
                where = (mi, s['pci'], key)
                cell.clicked.connect(lambda _on=False, w=where: self._clicked(w))
                self._cells[where] = cell
                grid.addWidget(cell, r, c, QtCore.Qt.AlignLeft)
            r += 1
        grid.addWidget(_rule('cellsRuleSoft'), r, 0, 1, len(sectors) + 2)
        r += 1
        grid.addWidget(_label('Read', 'cellsName'), r, 0)
        for c, s in enumerate(sectors, start=1):
            how = s.get('how') or {'waiting': 'waiting', 'reading': 'reading'}.get(s.get('state')) \
                or s.get('reason') or '–'
            if s.get('state') == 'not read' and s.get('reason'):
                how = f"not read: {s['reason']}"
            grid.addWidget(_label(how, 'cellsName' if s.get('state') == 'read' else 'cellsQuiet'), r, c)
        r += 1
        for i, u in enumerate(m.get('unconfirmed') or ()):
            if i == 0:                                        # a soft rule above them
                grid.addWidget(_rule('cellsRuleSoft'), r, 0, 1, len(sectors) + 2)
                r += 1
            line = MarkCell(u['mark'])
            where = (mi, u['pci'], 'sync')
            line.clicked.connect(lambda _on=False, w=where: self._clicked(w))
            self._cells[where] = line
            grid.addWidget(line, r, 0, 1, len(sectors) + 2, QtCore.Qt.AlignLeft)
            r += 1
        return grid

    def _clicked(self, where):
        cell = self._cells.get(where)
        if cell is None:
            return
        if self._open == where:                     # the open one: close it
            self._open = None
            cell.setChecked(False)
            self._glide_detail(False)
            return
        if self._open in self._cells:
            self._cells[self._open].setChecked(False)
        self._open = where
        cell.setChecked(True)
        self._fill_detail(where, cell.mark)
        self._glide_detail(True)

    def _fill_detail(self, where, mark):
        while self.detail_box.count():
            item = self.detail_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                _clear(item.layout())
        mi, pci, row = where
        model = self._models[mi]
        if row == 'sync':
            heading = f"Sync signal at PCI {pci}: not counted as a cell"
        else:
            heading = f"{dict(model['rows']).get(row, row)} of PCI {pci}: {mark.get('text', '')}"
        self.detail_box.addWidget(_label(heading, 'cellsTitle', weight=QtGui.QFont.DemiBold))
        form = Qt.QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(2)
        form.setColumnMinimumWidth(0, 160)
        for i, (label, value) in enumerate(mark.get('detail') or ()):
            form.addWidget(_label(str(label), 'cellsName'), i, 0, QtCore.Qt.AlignTop)
            v = _label(str(value), 'cellsValue', 'f_num' if label == 'bytes' else 'f_ui')
            v.setWordWrap(True)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            if label == 'bytes':
                v.setText(_breakable(str(value)))
            form.addWidget(v, i, 1)
        form.setColumnStretch(1, 1)
        self.detail_box.addLayout(form)

    def _glide_detail(self, opened):
        """The detail glides open under the grid (its height from nothing), or shut."""
        d = self.detail
        anim = getattr(self, '_detail_anim', None)
        if anim is not None:
            anim.stop()
        if opened:
            d.setMaximumHeight(0 if d.isHidden() else d.height())
            d.show()
            end = d.sizeHint().height()
        else:
            end = 0
        anim = QtCore.QPropertyAnimation(d, b'maximumHeight', self)
        anim.setDuration(GLIDE_MS)
        anim.setStartValue(d.maximumHeight() if d.maximumHeight() < 16777215 else d.height())
        anim.setEndValue(end)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        def finished():
            if opened:
                d.setMaximumHeight(16777215)
                self.scroll.ensureWidgetVisible(d, 0, 12)
            else:
                d.hide()
        anim.finished.connect(finished)
        self._detail_anim = anim
        anim.start()


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _breakable(text):
    """Long hex in groups of 16 characters (8 bytes), so it wraps and reads as a dump."""
    return ' '.join(text[i:i + 16] for i in range(0, len(text), 16))
