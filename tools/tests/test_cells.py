"""The Recordings tab's cells pane (``cells``, ``cells_view``, ``app_cells``) and the tools'
``--progress``.

Made-up cells only (PCIs 123-125 and 321-323, operator 001-01, TAC 0x1234), never a reply from
the air. What the checks pin:

- part 1, the model (no Qt): an NR reply of three cells on one carrier gives the title, the
  rows (MIB, SIB1, SIB2-5, SIB9 with its network time) and per cell how it was read and each
  mark: decoded with its copies, a message matched to the stronger cell's bytes (with its PCI and
  score), not found but unsure under a stronger cell; an LTE reply of two sites gives a SIB whose
  window was not in the span ('span'), one unsure under a removed cell, a TDD cell not read, and
  missing fields do not break it; the model while reading (cells done, the others waiting);
  progress lines, their words and their share, which only grows (NR cell by cell, LTE's two
  passes); an SI message not broadcast is 'sent only on request'; the file kept beside a
  ``.cfile`` (only there, never over another file), and the list line's words;
- part 2, the tools: a read's stderr read while the tool writes it (20000 lines, none lost or
  torn: the file is shared, so it is read at an offset); ``iq-lte FILE --mib --progress`` gives the same stdout as without, and on
  stderr a JSON line per stage and a 'done' line carrying the cell; ``iqnr_decode.own_decode_cells``
  calls ``progress`` for each cell's stages and once done with its result, and ``iq-nr``'s
  ``Progress`` writes one line per whole per cent;
- part 3, the window: an IQ band recording whose quick check finds cells puts the pane in the
  multiplex's place (the split glides to half), MIB and SIB1 rows waiting; Read cells runs the
  tool (a stub here), its progress shows and each cell fills in, the read is kept beside the
  recording and the list line says what it found; a mark's detail opens and closes; the theme
  recolours the pane; a recording sampled too slowly has the multiplex back; the kept read shows
  again with no tool run; another recording chosen during a read gets its own pane, and the read
  goes on; Read again on a kept read keeps the grid (the cell being read marked) and after Stop;
  Stop ends a read and its process; deleting the recording mid-read stops the read and takes the
  kept read with it; leaving Recordings puts the multiplex back at once, the split as it was.

Run:  PYTHONPATH=tools python tools/tests/test_cells.py   (under a minute)
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
FOLDER = tempfile.mkdtemp(prefix='fmrx-cells-')
os.environ['FMRX_CONFIG'] = os.path.join(FOLDER, 'config.json')
os.environ['FMRX_CELLS_NO_CAP'] = '1'

import numpy as np  # noqa: E402

from fm_receiver import cells  # noqa: E402

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append(ok)
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f'\n         {detail}'))


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:                                  # a check that crashes fails
        import traceback
        ok, detail = False, traceback.format_exc()
    report(name, bool(ok), detail)


# ----------------------------------------------------------------- made-up replies

SCHEDULE = [{'periodicity_rf': 16, 'broadcast_status': 'broadcasting', 'sibs': [s]}
            for s in ('sib2', 'sib3', 'sib4', 'sib5', 'sib9')]


def nr_entry(pci, rel, own_extra, mib=True):
    sib1 = {'plmns': ['001-01'], 'tac': 0x1234, 'cell_identity': 0x12345600 + pci % 10,
            'cell_barred': False, 'si_schedule': SCHEDULE, 'carrier': {'bands': [99]}}
    own = {'ran': True, 'decoder': 'ours', 'sib1': sib1, 'sib1_hex': '00ff00ff', 'time': None,
           'blocks': {'sib1': [14, 14], 'other': [10, 10], 'combined': [0, 0]}, 'cancelled': [0, 0],
           'summed': None, 'reason': None}
    own.update(own_extra)
    return {'ssb_hz': 600.15e6, 'pci': pci, 'relative_power_db': rel, 'first_ssb_s': 0.004,
            'mib': {'sfn': 10, 'ssb_index': 0, 'scs_common_khz': 15} if mib else None,
            'mib_bursts': [5, 5], 'own': own}


def nr_reply():
    every = ['sib2', 'sib3', 'sib4', 'sib5', 'sib9']
    first = nr_entry(123, 0.0, {'si': {'sibs_heard': {s: 2 for s in every},
                                       'sibs': {'sib2': {'q_rx_lev_min': -60}}, 'scheduled': every,
                                       'missing': [], 'unscheduled': []},
                                'time': {'utc': '2026-01-01T00:00:00.000Z'}})
    second = nr_entry(124, -7.0, {'cancelled': [10, 5],
                                  'si': {'sibs_heard': {'sib2': 1, 'sib3': 3, 'sib4': 3, 'sib5': 1},
                                         'sibs_matched': {'sib9': 2}, 'sibs': {}, 'scheduled': every,
                                         'missing': [], 'unscheduled': []},
                                  'shared': {'tested': 10, 'on_their_dci': [0, 0], 'messages': [
                                      {'si': 5, 'pci': 123, 'copies': 2, 'z': 30.0, 'null_z': 3.0,
                                       'matched': True}]}})
    third = nr_entry(125, -12.0, {'cancelled': [10, 5], 'blocks': {'sib1': [1, 10], 'other': [0, 0],
                                                                  'combined': [1, 0]},
                                  'si': {'sibs_heard': {}, 'sibs_matched': {'sib4': 2, 'sib5': 2, 'sib9': 2},
                                         'sibs': {}, 'scheduled': every, 'missing': ['sib2', 'sib3'],
                                         'missing_unsure': True, 'unscheduled': []},
                                  'shared': {'tested': 10, 'on_their_dci': [0, 0], 'messages': [
                                      {'si': 1, 'pci': 123, 'copies': 2, 'z': 4.0, 'null_z': 4.0, 'matched': False},
                                      {'si': 3, 'pci': 123, 'copies': 2, 'z': 28.0, 'null_z': 4.0, 'matched': True},
                                      {'si': 4, 'pci': 123, 'copies': 2, 'z': 20.0, 'null_z': 4.0, 'matched': True},
                                      {'si': 5, 'pci': 123, 'copies': 2, 'z': 15.0, 'null_z': 4.0, 'matched': True}]}})
    return {'file': 'synthetic', 'rate': 23.04e6, 'verdict': 'nr', 'ssbs': [first, second, third]}


def lte_cell(pci, rel, offset, known=(), schedule=None, duplex='FDD'):
    return {'pci': pci, 'relative_power_db': rel, 'frame_offset_samples': offset, 'duplex': duplex,
            'own_known': list(known),
            'own_mib': {'mib': {'n_rb': 50, 'sfn': 1}, 'decoded': 10, 'frames': 10, 'ports': 2},
            'own_sib1': {'decoded': 5, 'tried': 5, 'combined': [], 'sib1': '00',
                         'fields': {'plmns': [{'mcc': '001', 'mnc': '01'}], 'tac': 0x1234,
                                    'cell_identity': 0x1234500 + pci % 10, 'enb_id': 0x12345,
                                    'cell_id': pci % 10, 'freq_band': 13}},
            'own': {'ran': True, 'si': {'schedule': schedule or [], 'sibs': {'sib2': {'count': 3, 'summary': {'x': 1}}}}}}


def lte_reply():
    sched = [{'si': 1, 'period_frames': 16, 'sibs': ['sib2'], 'covered': True, 'heard': ['sib2']},
             {'si': 2, 'period_frames': 64, 'sibs': ['sib3'], 'covered': False, 'heard': []}]
    weak = [{'si': 1, 'period_frames': 16, 'sibs': ['sib2'], 'covered': True, 'heard': []},
            {'si': 2, 'period_frames': 64, 'sibs': ['sib3'], 'covered': False, 'heard': []}]
    tdd = lte_cell(323, -9.0, 5000, duplex='TDD')
    tdd['own_sib1'] = tdd['own_mib'] = tdd['own'] = None
    second = lte_cell(322, -3.0, 101, known=[321], schedule=weak)
    second['own']['si']['sibs'] = {}
    return {'rate_sps': 1.92e6, 'cells': [lte_cell(321, 0.0, 100, schedule=sched), second, tdd]}


# ----------------------------------------------------------------- part 1

def part1():
    print('part 1: the model')

    def nr():
        m = cells.model('nr', nr_reply())
        rows = [label for _k, label in m['rows']]
        a, b, c = m['sectors']
        ok = (m['title'] == '5G NR: three cells on one carrier'
              and rows == ['MIB', 'SIB1', 'SIB2', 'SIB3', 'SIB4', 'SIB5', 'SIB9, network time']
              and (a['how'], b['how'], c['how']) == ('alone', 'with 123 removed', 'with 123 and 124 removed')
              and a['identity'] == {'operator': '001-01', 'area': '0x1234', 'cell': '0x012345603'}
              and a['marks']['sib1']['text'] == '14' and a['marks']['sib9']['mark'] == 'ok'
              and ('network time (UTC)', '2026-01-01T00:00:00.000Z') in a['marks']['sib9']['detail']
              and b['marks']['sib9']['mark'] == 'match' and b['marks']['sib9']['text'] == 'same as 123'
              and any('z 30' in v for _l, v in b['marks']['sib9']['detail'])
              and b['marks']['sib2'] == {'mark': 'ok', 'text': '1', 'detail': b['marks']['sib2']['detail']}
              and [c['marks'][s]['mark'] for s in ('sib2', 'sib3', 'sib4', 'sib5', 'sib9')]
              == ['unsure', 'unsure', 'match', 'match', 'match'])
        return ok, (m['title'], rows, [(s['how'], {k: v['mark'] for k, v in s['marks'].items()}) for s in m['sectors']])
    expect("NR: three cells on one carrier: rows, how each was read, decoded / matched / unsure", nr)

    def lte():
        m = cells.model('lte', lte_reply())
        a, b, c = m['sectors']
        ok = (m['title'] == 'LTE: three cells, two sites'
              and [k for k, _l in m['rows']] == ['mib', 'sib1', 'sib2', 'sib3']
              and a['identity']['operator'] == '001-01' and a['marks']['sib2']['text'] == '3'
              and a['marks']['sib3']['mark'] == 'span' and b['marks']['sib2']['mark'] == 'unsure'
              and b['how'] == 'with 321 removed' and c['state'] == 'not read' and 'TDD' in c['reason'])
        return ok, (m['title'], m['rows'], [(s['state'], {k: v['mark'] for k, v in s['marks'].items()}) for s in m['sectors']])
    expect("LTE: two sites; a window not in the span, a SIB unsure under a removed cell, TDD not read", lte)

    def lte_one_site():
        r = lte_reply()
        r['cells'] = r['cells'][:2]
        return cells.model('lte', r)['title'] == 'LTE: one site, two sectors', cells.model('lte', r)['title']
    expect("LTE: frames within a few microseconds are one site's sectors", lte_one_site)

    def sparse():
        m1 = cells.model('nr', {'ssbs': [{'pci': 123, 'relative_power_db': 0, 'mib': None, 'own': None}]})
        m2 = cells.model('lte', {'cells': [{'pci': 321, 'own_mib': None, 'own_sib1': None, 'own': None}]})
        return (m1['sectors'][0]['state'] == 'not read' and m2['sectors'][0]['marks']['mib']['mark'] == 'missing'), (m1, m2)
    expect("a cell with no MIB, or nothing read, does not break the model", sparse)

    def while_reading():
        quick = {'kind': 'nr', 'title': '5G NR: three cells on one carrier', 'rows': [('mib', 'MIB'), ('sib1', 'SIB1')],
                 'sectors': [cells._sector(p, r) for p, r in ((123, 0), (124, -7), (125, -12))], 'note': None}
        m0 = cells.reading(quick, [])
        m1 = cells.reading(quick, nr_reply()['ssbs'][:1])
        return ([s['state'] for s in m0['sectors']] == ['waiting'] * 3 and len(m0['rows']) == 2
                and [s['state'] for s in m1['sectors']] == ['read', 'waiting', 'waiting']
                and len(m1['rows']) == 7 and m1['title'] == quick['title']), (m0, m1['rows'])
    expect("while reading: the cells done, then the others waiting; rows once SIB1 is known", while_reading)

    def progress():
        p = cells.progress_line('{"progress": {"cell": 1, "cells": 3, "pci": 124, "stage": "other SI", '
                                '"done": 40, "total": 80}}')
        other = cells.progress_line('iq-nr: something else')
        return (p is not None and other is None and cells.progress_text(p) == 'PCI 124: other SI, 50%'
                and abs(cells.progress_fraction(p, 'nr') - 1.75 / 3) < 1e-9), (p, cells.progress_text(p) if p else None)
    expect("a progress line: read, worded ('PCI 124: other SI, 50%') and its share of the read", progress)

    def forwards():
        def run(kind, stages):
            out = []
            for stage, cells_order in stages:
                for cell in cells_order:
                    for d in range(0, 11):
                        out.append(cells.progress_fraction({'cell': cell, 'cells': 3, 'stage': stage,
                                                            'done': d, 'total': 10}, kind))
                    if stage in ('other SI',):
                        out.append(cells.progress_fraction({'cell': cell, 'cells': 3, 'stage': 'done',
                                                            'done': 1, 'total': 1}, kind))
            return out
        nr = []
        for cell in range(3):
            nr += run('nr', [('SIB1', [cell]), ('other SI', [cell])])
        lte = run('lte', [('MIB and SIB1', [0, 1, 2]), ('other SI', [0, 1, 2])])
        ok = all(b >= a - 1e-12 for a, b in zip(nr, nr[1:])) and all(b >= a - 1e-12 for a, b in zip(lte, lte[1:]))
        return ok and abs(nr[-1] - 1) < 1e-9 and abs(lte[-1] - 1) < 1e-9, (nr[-3:], lte[-3:])
    expect("the read's share only grows: NR cell by cell, LTE's MIB and SIB1 pass then its SI pass", forwards)

    def on_request():
        r = nr_reply()
        e = r['ssbs'][0]
        e['own']['sib1']['si_schedule'] = [dict(x) for x in SCHEDULE]
        e['own']['sib1']['si_schedule'][4]['broadcast_status'] = 'notBroadcasting'
        e['own']['si']['sibs_heard'].pop('sib9')
        mark = cells.model('nr', r)['sectors'][0]['marks']['sib9']
        return mark['mark'] == 'span' and mark['text'] == 'sent only on request', mark
    expect("an SI message SIB1 lists as not broadcast: 'sent only on request', not 'not found'", on_request)

    def kept():
        folder = tempfile.mkdtemp(prefix='cells-kept-')
        try:
            rec = os.path.join(folder, 'fm-600.00MHz-20260101-120000-iq-band.cfile')
            open(rec, 'wb').close()
            target = cells.save(rec, {'nr': nr_reply()}, 125.0)
            doc = cells.load(rec)
            other = os.path.join(folder, 'x.sdvr')
            not_cfile = cells.save(other, {'nr': nr_reply()}, 1.0)
            busy = os.path.join(folder, 'fm-600.00MHz-20260101-130000-iq-band')
            open(busy + '.cfile', 'wb').close()
            open(busy + cells.SUFFIX, 'w').write('{"someone": "else"}')
            kept_out = cells.save(busy + '.cfile', {'nr': nr_reply()}, 1.0)
            return (target == rec[:-6] + '.cells.json' and doc['summary'] == '5G NR, 3 cells'
                    and doc['seconds'] == 125.0 and cells.models_of(doc)[0]['sectors'][0]['pci'] == 123
                    and not_cfile is None and kept_out is None
                    and json.load(open(busy + cells.SUFFIX)) == {'someone': 'else'}), (target, doc and doc.get('summary'))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("a read is kept beside a .cfile only, never over another file; its list words", kept)

    def kept_away():
        folder = tempfile.mkdtemp(prefix='cells-away-')
        try:
            rec = os.path.join(folder, 'sceptre.sdvr')
            open(rec, 'wb').write(b'\0' * 64)
            target = cells.save(rec, {'nr': nr_reply()}, 30.0)
            beside = os.listdir(folder) == ['sceptre.sdvr']
            doc = cells.load(rec)
            words = cells.kept_summary(rec)
            gone = os.path.join(folder, 'deleted.cdif')
            open(gone, 'wb').write(b'\0' * 8)
            orphan = cells.save(gone, {'nr': nr_reply()}, 1.0)
            os.remove(gone)
            cells.save(rec, {'nr': nr_reply()}, 31.0)      # the next save tidies the orphan away
            tidied = not os.path.exists(orphan) and os.path.exists(target)
            open(rec, 'wb').write(b'\0' * 128)               # Sceptre writes it again
            stale = cells.load(rec)
            return (target.startswith(cells.kept_dir()) and beside and doc is not None
                    and doc['seconds'] == 30.0 and words == '5G NR, 3 cells' and tidied
                    and stale is None and cells.kept_summary(rec) == ''), (target, words, tidied, stale)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("a Sceptre recording's read is kept in the app's folder, dropped when the file is "
           "written again or gone", kept_away)


# ----------------------------------------------------------------- part 2

def _tool(name):
    path = os.path.join(os.path.dirname(HERE), name)
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def part2():
    print('part 2: the tools report as they go')

    def shared_file():
        from fm_receiver.app_cells import _Read
        script = os.path.join(FOLDER, 'writer.py')
        with open(script, 'w') as fh:
            fh.write("import json, sys\n"
                     "for i in range(20000):\n"
                     "    sys.stderr.write(json.dumps({'progress': {'cell': i, 'x': 'y' * (i % 300)}}) + '\\n')\n")
        r = _Read('x', 'nr', [sys.executable, script], False)
        got = []
        while r.proc.poll() is None:
            got += r.lines()
        got += r.lines()
        r.close()
        parsed = [cells.progress_line(x) for x in got]
        return (len(got) == 20000 and all(parsed) and [p['cell'] for p in parsed] == list(range(20000))), len(got)
    expect("a read's stderr, read as the tool writes it: every line whole, none lost", shared_file)

    def lte_cli():
        from tests.test_iqlte_phy import PCI, cell_iq
        iq, fs = cell_iq(2, snr_db=6.0)
        folder = tempfile.mkdtemp(prefix='cells-lte-')
        try:
            base = os.path.join(folder, 'lte')
            iq.astype(np.complex64).tofile(base + '.cfile')
            with open(base + '.sigmf-meta', 'w') as fh:
                json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': fs},
                           'captures': [{'core:sample_start': 0, 'core:frequency': 700e6}]}, fh)
            argv = [sys.executable, os.path.join(os.path.dirname(HERE), 'iq-lte'), base + '.cfile', '--mib', '--t1', '0.1']
            plain = subprocess.run(argv, capture_output=True, text=True, timeout=300)
            told = subprocess.run(argv + ['--progress'], capture_output=True, text=True, timeout=300)
            lines = [cells.progress_line(x) for x in told.stderr.splitlines()]
            lines = [x for x in lines if x]
            done = [x for x in lines if x['stage'] == 'done']
            return (plain.returncode == told.returncode == 0 and plain.stdout == told.stdout
                    and any(x['stage'] == 'MIB and SIB1' for x in lines)
                    and any(d['entry'].get('pci') == PCI and d['entry'].get('own_mib') for d in done)), \
                (told.stderr[-400:], plain.stdout == told.stdout)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    expect("iq-lte --mib --progress: the same stdout, a line per stage, the cell when done", lte_cli)

    def nr_cells():
        from fm_receiver import iqnr_decode
        seen = []
        real = iqnr_decode.own_decode

        def fake(iq, fs, centre, cell, tools=None, known=None, progress=None):
            for stage in ('SIB1', 'other SI'):
                for i in range(3):
                    progress(stage, i, 3)
            return {'pci': cell['pci']}
        iqnr_decode.own_decode = fake
        try:
            cells_in = [{'pci': 123, 'ssb_hz': 1e9, 'mib': {'a': 1}}, {'pci': 124, 'ssb_hz': 1e9, 'mib': None},
                        {'pci': 125, 'ssb_hz': 1e9, 'mib': {'a': 1}}]
            out = iqnr_decode.own_decode_cells(None, 1.0, 0.0, cells_in, tools='/none',
                                               progress=lambda *a, **k: seen.append((a, k.get('own'))))
        finally:
            iqnr_decode.own_decode = real
        stages = [(a[0], a[1]) for a, _o in seen]
        dones = [(a[0], o['pci']) for a, o in seen if a[1] == 'done']
        return (out[1] is None and (0, 'SIB1') in stages and (2, 'other SI') in stages
                and dones == [(0, 123), (2, 125)]), (stages, dones)
    expect("own_decode_cells: each cell's stages, then 'done' with its result; none for a cell without a MIB", nr_cells)

    def nr_writer():
        tool = _tool('iq-nr')
        out = io.StringIO()
        tell = tool.Progress(out)
        for done in range(0, 101):
            tell(0, 2, 123, 'other SI', done, 200)
        tell(0, 2, 123, 'done', 1, 1, entry={'pci': 123, 'x': np.float64(1.5)})
        lines = [cells.progress_line(x) for x in out.getvalue().splitlines()]
        return (all(lines) and len(lines) == 51 + 1 and lines[-1]['entry'] == {'pci': 123, 'x': 1.5}), len(lines)
    expect("iq-nr's Progress: one line a whole per cent, the entry JSON-safe", nr_writer)


# ----------------------------------------------------------------- part 3

STUB = '''#!/usr/bin/env python3
import json, os, sys, time
reply = json.load(open(%(reply)r))
slow = os.path.exists(%(slow)r)
for i, e in enumerate(reply['ssbs']):
    for stage in ('SIB1', 'other SI'):
        for d in range(0, 4):
            sys.stderr.write(json.dumps({'progress': {'cell': i, 'cells': 3, 'pci': e['pci'], 'stage': stage,
                                                      'done': d, 'total': 4}}) + '\\n')
            sys.stderr.flush()
            time.sleep(5.0 if slow else 0.05)
    sys.stderr.write(json.dumps({'progress': {'cell': i, 'cells': 3, 'pci': e['pci'], 'stage': 'done',
                                              'done': 1, 'total': 1, 'entry': e}}) + '\\n')
    sys.stderr.flush()
open(%(ran)r, 'a').write('ran\\n')
print(json.dumps(reply))
'''


def pump(app, seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def part3():
    print('part 3: the window')
    from PyQt5 import Qt, QtGui
    from fm_receiver import app as fmapp
    from fm_receiver import control, theme
    qapp = Qt.QApplication.instance() or Qt.QApplication(sys.argv[:1])
    rec_dir = os.path.join(FOLDER, 'recordings')
    tools = os.path.join(FOLDER, 'tools')
    os.makedirs(rec_dir)
    os.makedirs(tools)
    reply_path = os.path.join(FOLDER, 'reply.json')
    json.dump(nr_reply(), open(reply_path, 'w'))
    slow, ran = os.path.join(FOLDER, 'slow'), os.path.join(FOLDER, 'ran')
    with open(os.path.join(tools, 'iq-nr'), 'w') as fh:
        fh.write(STUB % {'reply': reply_path, 'slow': slow, 'ran': ran})
    os.environ['FMRX_CELLS_TOOLS'] = tools

    def recording(mhz, stamp, rate):
        base = os.path.join(rec_dir, f'fm-{mhz:.2f}MHz-{stamp}-iq-band')
        np.zeros(int(rate * 0.05), dtype=np.complex64).tofile(base + '.cfile')
        json.dump({'rate': rate, 'offset_hz': 0, 'station_hz': mhz * 1e6, 'center_hz': mhz * 1e6},
                  open(base + '.json', 'w'))
        return base + '.cfile'
    cell_rec = recording(600.0, '20260101-120000', 2e6)
    slow_rec = recording(98.1, '20260101-110000', 250e3)

    quick = {'kind': 'nr', 'title': '5G NR: three cells on one carrier', 'rows': [('mib', 'MIB'), ('sib1', 'SIB1')],
             'sectors': [cells._sector(p, r) for p, r in ((123, 0.0), (124, -7.0), (125, -12.0))], 'note': None}
    asked = []

    def fake_quick(path):
        asked.append(path)
        return {'nr': quick if path != slow_rec else None, 'lte': None}
    cells.quick = fake_quick

    args = fmapp.parse_args(['--no-sound-card', '--no-control', '--mode', 'recordings'])
    w = fmapp.MainWindow(args, {'recording_dir': rec_dir})
    w.resize(1500, 950)
    w.show()
    pump(qapp, 0.2)
    w.start_initial()
    pane = w.cells_pane
    cmd = control.COMMANDS

    def pick(path):
        for i, rec in enumerate(w._recordings):
            if any(t.path == path for t in rec.tracks):
                cmd['recording'](w, [str(i)])
                return True
        return False

    def state():
        return cmd['cells-pane'](w, [])

    try:
        def found():
            assert pump(qapp, 3, lambda: len(w._recordings) == 2), len(w._recordings)
            split0 = w.right_split.sizes()
            pick(cell_rec)
            shown = pump(qapp, 10, lambda: state()['shown'] and state()['state'] == 'found')
            pump(qapp, 0.6)
            st = state()
            sizes = w.right_split.sizes()
            share = sizes[1] / float(sum(sizes))
            return (shown and st['title'] == quick['title'] and st['button'] == 'Read cells'
                    and [k for k, _l in st['models'][0]['rows']] == ['mib', 'sib1']
                    and abs(share - 0.5) < 0.05 and w.bottom.currentWidget() is pane), (st['state'], sizes, split0)
        expect("cells found: the pane takes the multiplex's place, the split at half, MIB and SIB1 waiting", found)

        def read():
            cmd['cells-pane'](w, ['read'])
            seen = {'reading': False, 'partial': False}

            def watch():
                st = state()
                if st['state'] == 'reading':
                    seen['reading'] = True
                    states = [s['state'] for s in st['models'][0]['sectors']]
                    if states[0] == 'read' and states[1] in ('waiting', 'reading'):
                        seen['partial'] = True
                return st['state'] == 'read'
            done = pump(qapp, 20, watch)
            st = state()
            line = w.rec_list.currentItem().text()
            kept = cells.load(cell_rec)
            marks = {s['pci']: {k: v['mark'] for k, v in s['marks'].items()} for s in st['models'][0]['sectors']}
            return (done and seen['reading'] and seen['partial'] and st['button'] == 'Read again'
                    and marks[124]['sib9'] == 'match' and marks[125]['sib2'] == 'unsure'
                    and kept is not None and '5G NR, 3 cells' in line), (seen, st['status'], line)
        expect("Read cells: progress shows, each cell fills in, the read is kept and the list says so", read)

        def detail():
            cmd['cells-pane'](w, ['open', '124', 'sib9'])
            pump(qapp, 0.6)
            opened = state()['open'] == {'model': 0, 'pci': 124, 'row': 'sib9'} and not pane.detail.isHidden()
            cmd['cells-pane'](w, ['open', '124', 'sib9'])
            pump(qapp, 0.6)
            return opened and state()['open'] is None and pane.detail.isHidden(), state()['open']
        expect("a mark's detail glides open under the grid, and shut again", detail)

        def recolour():
            name = pane.findChild(Qt.QLabel, 'cellsName')
            before = name.palette().color(QtGui.QPalette.WindowText).name()
            w.set_theme(theme.after(theme.current()))
            pump(qapp, 0.2)
            after = name.palette().color(QtGui.QPalette.WindowText).name()
            return after == theme.TOKENS['ink_2'].lower() and after != before, (before, after)
        expect("a theme change recolours the pane", recolour)

        def multiplex_back():
            pick(slow_rec)
            back = pump(qapp, 3, lambda: w.bottom.currentWidget() is w.rx_page)
            return back and slow_rec not in asked, asked
        expect("a recording sampled too slowly for a cell: the multiplex back, no check run", multiplex_back)

        def kept_again():
            os.remove(ran)
            pick(cell_rec)
            shown = pump(qapp, 3, lambda: state()['shown'] and state()['state'] == 'read')
            return shown and not os.path.exists(ran), (state()['state'], state()['shown'], os.path.exists(ran))
        expect("the kept read shows again at once, with no tool run", kept_again)

        def rewritten():
            kept_file = cells.path_for(cell_rec)
            saved = open(kept_file).read()
            pick(slow_rec)
            pump(qapp, 1, lambda: w.bottom.currentWidget() is w.rx_page)
            os.remove(kept_file)                             # as a Sceptre file's, kept away from it
            np.zeros(int(2e6 * 0.06), dtype=np.complex64).tofile(cell_rec)   # written again
            pick(cell_rec)
            found = pump(qapp, 3, lambda: state()['shown'] and state()['state'] == 'found')
            open(kept_file, 'w').write(saved)
            np.zeros(int(2e6 * 0.05), dtype=np.complex64).tofile(cell_rec)
            return found, state()['state']
        expect("a recording written again over the old one: this session's read of it is not shown", rewritten)

        def other_while_reading():
            other = recording(601.0, '20260101-130000', 2e6)
            w._refresh_library()
            pump(qapp, 0.5)
            open(slow, 'w').close()
            pick(cell_rec)
            pump(qapp, 1, lambda: state()['shown'])
            cmd['cells-pane'](w, ['read'])
            started = pump(qapp, 3, lambda: state()['state'] == 'reading')
            pick(other)
            shown = pump(qapp, 10, lambda: state()['shown'] and state()['state'] == 'found')
            back = pick(cell_rec) and pump(qapp, 3, lambda: state()['state'] == 'reading')
            st = state()
            sectors = st['models'][0]['sectors'] if st['models'] else []
            cmd['cells-pane'](w, ['stop'])
            os.remove(slow)
            return started and shown and back and len(sectors) == 3, (started, shown, back, st['state'])
        expect("another recording chosen during a read gets its pane; back on the first, the read goes on", other_while_reading)

        def again_keeps_grid():
            pick(cell_rec)
            pump(qapp, 2, lambda: state()['state'] in ('read', 'found', 'failed'))
            w._cells_quick.pop(cell_rec, None)          # as after a restart: only the kept read
            w._cells_done.clear()
            open(slow, 'w').close()
            cmd['cells-pane'](w, ['read'])
            pump(qapp, 1.0)
            st = state()
            grid = bool(st['models']) and len(st['models'][0]['sectors']) == 3
            reading = [s['state'] for s in st['models'][0]['sectors']] if grid else []
            cmd['cells-pane'](w, ['stop'])
            after = state()
            os.remove(slow)
            return (grid and 'reading' in reading and after['state'] == 'failed'
                    and len(after['models'][0]['sectors']) == 3), (reading, after['state'])
        expect("Read again on a kept read: the grid stays while reading, the cell read marked, and after Stop", again_keeps_grid)

        def stop():
            open(slow, 'w').close()
            cmd['cells-pane'](w, ['read'])
            started = pump(qapp, 3, lambda: w._cells_job is not None and w._cells_job['read'] is not None)
            proc = w._cells_job['read'].proc
            cmd['cells-pane'](w, ['stop'])
            gone = proc.poll() is not None
            st = state()
            return started and gone and st['state'] == 'failed' and 'Stopped' in st['status'], st['status']
        expect("Stop ends the read and its process", stop)

        def delete():
            rec = next(r for r in w._recordings if any(t.path == cell_rec for t in r.tracks))
            target = cells.path_for(cell_rec)
            pick(cell_rec)
            pump(qapp, 1, lambda: state()['shown'])
            cmd['cells-pane'](w, ['read'])                  # still slow: deleted mid-read
            pump(qapp, 1, lambda: w._cells_job is not None and w._cells_job['read'] is not None)
            proc = w._cells_job['read'].proc
            w._delete_recording(rec)
            pump(qapp, 0.5)
            return (not os.path.exists(target) and not os.path.exists(cell_rec) and w._cells_job is None
                    and proc.poll() is not None), os.listdir(rec_dir)
        expect("deleting the recording mid-read stops the read and takes its kept read", delete)

        def leave():
            other = next(p for r in w._recordings for p in [t.path for t in r.tracks] if '601.00MHz' in p)
            pick(other)
            pump(qapp, 3, lambda: state()['shown'])
            was = w.bottom.currentWidget() is pane
            w._cells_leave()
            return was and w.bottom.currentWidget() is w.rx_page and w._split_before is None, \
                (was, type(w.bottom.currentWidget()).__name__)
        expect("leaving Recordings: the multiplex back at once, the split as it was", leave)
    finally:
        w.close()
        pump(qapp, 0.2)


def main():
    try:
        part1()
        part2()
        part3()
    finally:
        shutil.rmtree(FOLDER, ignore_errors=True)
    bad = RESULTS.count(False)
    print(f"{len(RESULTS) - bad} ok, {bad} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
