"""The window's cells pane, in the Recordings tab: the LTE and 5G NR cells in an IQ recording.

A mixin of ``app.MainWindow``. When an IQ recording is chosen, the quick check (``cells.quick``,
50 ms of it, in a thread of its own) looks for cells; if it finds any, the pane
(``cells_view.CellsPane``) takes the multiplex's place under the waterfall, fading in. Read cells
runs ``tools/iq-nr --pdsch`` and ``tools/iq-lte --own`` in turn (``cells.read_argv``), as their own
process group, the memory capped where systemd can, and follows their ``--progress`` lines:
each cell's column fills in as it is read. Stop, another Read, or closing the window ends it.
A read is kept beside its recording (``cells.save``) and shown again when chosen; the list line
gains its words ("5G NR, 3 cells").
"""

import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from PyQt5 import Qt, QtCore  # type: ignore

from . import cells
from .cells_view import CellsPane

#: The IQ tracks a cell may be in.
CELL_KINDS = ('iq-band', 'dvr-iq', 'cdif')
#: How often a running read is looked at, ms.
POLL_MS = 250
#: The memory a read may take (``iq-lte --own`` peaks near 2 GB): beyond it the read, not the
#: session, is stopped. Linux with a user systemd only.
READ_MEMORY = '6G'
#: Read time, seconds a cell: the measured runs took 24 to 42.
SECONDS_A_CELL = 40


class _Read:
    """One running tool: its process (a group of its own), its output files and where it is."""

    def __init__(self, path, kind, argv):
        self.path, self.kind = path, kind
        self.out = tempfile.TemporaryFile()
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=self.out, stderr=self.err,
                                     start_new_session=True)
        self.began = time.monotonic()
        self.at = 0                       # how far into stderr has been read
        self.rest = b''
        self.entries = []                 # cells done, as --progress gives them
        self.last = None                  # the last progress line
        self.notes = []                   # stderr lines that are not progress

    def lines(self):
        """The stderr lines written since last asked."""
        self.err.seek(self.at)
        got = self.err.read()
        self.at += len(got)
        data = self.rest + got
        *whole, self.rest = data.split(b'\n')
        return [w.decode('utf-8', 'replace') for w in whole]

    def stop(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.close()

    def reply(self):
        self.out.seek(0)
        text = self.out.read().decode('utf-8', 'replace')
        self.close()
        return json.loads(text)

    def close(self):
        for f in (self.out, self.err):
            try:
                f.close()
            except OSError:
                pass


def _capped(argv):
    """``argv`` under a memory cap where a user systemd runs (Linux); as it is elsewhere."""
    if not sys.platform.startswith('linux') or os.environ.get('FMRX_CELLS_NO_CAP'):
        return argv
    runner = shutil.which('systemd-run')
    if runner is None or not os.environ.get('XDG_RUNTIME_DIR'):
        return argv
    return [runner, '--user', '--scope', '-q', '-p', f'MemoryMax={READ_MEMORY}',
            '-p', 'MemorySwapMax=0', '--'] + argv


class CellsWindow:
    """The cells pane's part of the window (a mixin of ``MainWindow``)."""

    def _build_cells_pane(self):
        self.cells_pane = CellsPane()
        self.cells_pane.readRequested.connect(self.cells_read)
        self.cells_pane.stopRequested.connect(self.cells_stop)
        self.bottom.addTab(self.cells_pane, "Cells")
        self._cells_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._cells_quick = {}                  # path -> {'lte': model, 'nr': model} (None: none)
        self._cells_check = None                # (path, future) of the quick check running
        self._cells_path = None                 # the IQ file of the recording on show, if any
        self._cells_job = None                  # {'path', 'kinds', 'replies', 'read', 'began'}
        self._cells_shown = False
        self._cells_words = {}                  # path -> the list line's words, once found
        self._cells_timer = Qt.QTimer(self)
        self._cells_timer.setInterval(POLL_MS)
        self._cells_timer.timeout.connect(self._cells_poll)

    # ------------------------------------------------------------ choosing
    def _cells_track(self, track):
        """``track`` is on show in Recordings: its cells, or the multiplex back."""
        path = None
        if track is not None and track.kind in CELL_KINDS and not track.error \
                and (track.rate or 0) >= cells.MIN_RATE:
            path = track.path
        self._cells_path = path
        if path is None:
            self._cells_show(False)
            return
        job = self._cells_job
        if job is not None and job['path'] == path:
            self._cells_progress_shown()
            self._cells_show(True)
            return
        kept = cells.load(path)
        if kept is not None:
            self._cells_show_kept(kept)
            return
        if path in self._cells_quick:
            self._cells_found(path)
            return
        self._cells_show(False)
        if self._cells_check is None or self._cells_check[0] != path:
            self._cells_check = (path, self._cells_exec.submit(cells.quick, path))
        self._cells_timer.start()

    def _cells_models(self, path):
        quick = self._cells_quick.get(path) or {}
        return [quick[k] for k in ('nr', 'lte') if quick.get(k)]

    def _cells_found(self, path):
        models = self._cells_models(path)
        if not models:
            self._cells_show(False)
            return
        n = sum(len(m['sectors']) for m in models)
        self.cells_pane.show_found(models, SECONDS_A_CELL * n)
        self._cells_show(True)

    def _cells_show_kept(self, kept):
        models = cells.models_of(kept)
        if not models:
            self._cells_show(False)
            return
        when = str(kept.get('read') or '')[:16].replace('T', ' ')
        took = kept.get('seconds')
        self.cells_pane.show_read(models, f"Read {when}" + (f" in {_took(took)}" if took else ''))
        self._cells_show(True)

    def _cells_show(self, shown):
        """The pane in the multiplex's place under the waterfall, or the multiplex back
        (``_show_bottom`` does nothing when the one wanted is there)."""
        self._cells_shown = shown
        if self.bottom.currentWidget() in (self.rx_page, self.cells_pane):
            self._show_bottom(False, True)

    def _cells_wanted(self):
        """Whether the pane, not the multiplex, belongs under the waterfall now."""
        return bool(self._cells_shown and self._tab_mode() == 'recordings')

    def _cells_leave(self):
        """Out of Recordings: the multiplex is the receiver's again (a read goes on)."""
        self._cells_path = None
        self._cells_shown = False

    # ------------------------------------------------------------ reading
    def cells_read(self):
        """Read the cells of the recording on show (Read cells). False when there is nothing to
        read or it is being read."""
        path = self._cells_path
        if path is None:
            return False
        if self._cells_job is not None:
            if self._cells_job['path'] == path:
                return False
            self.cells_stop()
        kinds = [k for k in ('nr', 'lte') if (self._cells_quick.get(path) or {}).get(k)]
        if not kinds:
            kept = cells.load(path) or {}
            kinds = [k for k in ('nr', 'lte') if kept.get(k)]
        if not kinds:
            return False
        self._cells_job = {'path': path, 'kinds': kinds, 'replies': {}, 'read': None,
                           'began': time.monotonic(), 'error': None}
        self._cells_next()
        self._cells_timer.start()
        return True

    def cells_stop(self):
        job, self._cells_job = self._cells_job, None
        if job is None:
            return False
        if job['read'] is not None:
            job['read'].stop()
        if job['path'] == self._cells_path:
            self.cells_pane.show_failed(self._cells_models(job['path']), 'Stopped; nothing kept')
        return True

    def _cells_next(self):
        job = self._cells_job
        kind = next(k for k in job['kinds'] if k not in job['replies'])
        try:
            job['read'] = _Read(job['path'], kind, _capped(cells.read_argv(kind, job['path'])))
        except OSError as exc:
            job['read'] = None
            self._cells_failed(f"Could not start the read: {exc.strerror or exc}")
            return
        self._cells_progress_shown()

    def _cells_poll(self):
        check = self._cells_check
        if check is not None and check[1].done():
            self._cells_check = None
            path, future = check
            try:
                self._cells_quick[path] = future.result()
            except Exception:
                self._cells_quick[path] = {}
            if path == self._cells_path and self._cells_job is None and cells.load(path) is None:
                self._cells_found(path)
                self._cells_list_words(path)
        job = self._cells_job
        if job is not None and job['read'] is not None:
            self._cells_follow(job)
        if self._cells_check is None and self._cells_job is None:
            self._cells_timer.stop()

    def _cells_follow(self, job):
        read = job['read']
        moved = False
        for line in read.lines():
            p = cells.progress_line(line)
            if p is None:
                if line.strip():
                    read.notes.append(line.strip())
                continue
            read.last = p
            moved = True
            if p.get('entry') is not None:
                read.entries.append(p['entry'])
        code = read.proc.poll()
        if code is None:
            if moved:
                self._cells_progress_shown()
            return
        job['read'] = None
        if code != 0:
            read.close()
            why = read.notes[-1] if read.notes else f"exit {code}"
            self._cells_failed(f"The {cells.TECH[read.kind]} read failed: {why}")
            return
        try:
            job['replies'][read.kind] = read.reply()
        except ValueError:
            self._cells_failed(f"The {cells.TECH[read.kind]} read printed no result")
            return
        if any(k not in job['replies'] for k in job['kinds']):
            self._cells_next()
            return
        self._cells_job = None
        seconds = time.monotonic() - job['began']
        try:
            cells.save(job['path'], job['replies'], seconds)
        except OSError:
            pass
        models = [cells.model(k, job['replies'][k]) for k in ('nr', 'lte') if k in job['replies']]
        if job['path'] == self._cells_path:
            self.cells_pane.show_read(models, f"Read in {_took(seconds)}")
        self._cells_list_words(job['path'], models)

    def _cells_progress_shown(self):
        """The running read, as far as it has got, if its recording is the one on show."""
        job = self._cells_job
        if job is None or job['path'] != self._cells_path:
            return
        quick = self._cells_quick.get(job['path']) or {}
        read = job['read']
        models = []
        for kind in ('nr', 'lte'):
            if kind not in job['kinds']:
                continue
            if kind in job['replies']:
                models.append(cells.model(kind, job['replies'][kind]))
            elif read is not None and read.kind == kind and quick.get(kind):
                models.append(cells.reading(quick[kind], read.entries))
            elif quick.get(kind):
                models.append(quick[kind])
        last = read.last if read is not None else None
        text = cells.progress_text(last) if last else f"Starting the {cells.TECH[read.kind]} read" \
            if read is not None else ''
        done = len(job['replies']) + (cells.progress_fraction(last) if last else 0.0)
        self.cells_pane.show_reading(models, text, done / max(1, len(job['kinds'])))

    def _cells_failed(self, why):
        job, self._cells_job = self._cells_job, None
        if job is not None and job['path'] == self._cells_path:
            self.cells_pane.show_failed(self._cells_models(job['path']), why)

    def _cells_list_words(self, path, models=None):
        """The recording's list line gains what was found."""
        if models is None:
            models = self._cells_models(path)
        words = cells.summary(models)
        if not words:
            return
        self._cells_words[path] = words
        for i in range(self.rec_list.count()):
            item = self.rec_list.item(i)
            rec = self._recording_for(item)
            if rec is not None and any(t.path == path for t in rec.tracks):
                item.setText(self._recording_line(rec))
                return

    def _recording_line(self, rec):
        """A recording's line in the list, with the cells found in it."""
        text = self._recording_text(rec)
        words = next((self._cells_words[t.path] for t in rec.tracks if t.path in self._cells_words), None)
        words = words or cells_words(rec)
        return text + (' \u00b7 ' + words if words else '')

    def _cells_close(self):
        self._cells_timer.stop()
        if self._cells_job is not None and self._cells_job['read'] is not None:
            self._cells_job['read'].stop()
        self._cells_job = None
        self._cells_exec.shutdown(wait=False)


def cells_words(rec):
    """What a recording's kept read found, for its list line, or ''."""
    for track in rec.tracks:
        if track.kind in CELL_KINDS:
            kept = cells.load(track.path)
            if kept is not None:
                return kept.get('summary') or cells.summary(cells.models_of(kept))
    return ''


def _took(seconds):
    seconds = float(seconds or 0)
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"
