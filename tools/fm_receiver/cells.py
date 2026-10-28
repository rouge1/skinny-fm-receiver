"""The cells in an IQ recording, as the Recordings tab's cells pane shows them.

No Qt here: what the pane draws is a *model* (a dict), built from

- the quick check (``quick``: ``iqmeasure.cells_file``, 50 ms, a few seconds), which knows each
  cell's PCI and level but nothing of its broadcast, and
- a read (``tools/iq-nr FILE --pdsch`` or ``tools/iq-lte FILE --own``, ``read_argv``), the
  tools' JSON as they print it, whole or a cell at a time (``--progress``).

A model: ``kind`` ('nr' or 'lte'), ``title``, ``rows`` ([(key, label)], MIB, SIB1, then the SIBs
any cell schedules), ``sectors`` (strongest first: ``pci``, ``rel_db``, ``state`` 'waiting',
'reading', 'read' or 'not read', ``identity`` {operator, area, cell}, ``how``, ``marks`` {row key:
{mark, text, detail}}) and ``note``. A mark is 'ok' (decoded; the text says how many copies),
'match' (not decoded, but its signal fits a stronger cell's bytes: ``iqnr_decode``'s
``sibs_matched``), 'missing' (looked for, not found), 'unsure' (not found where a stronger cell
was on top of it) or 'span' (its window is not in the recording). ``detail`` is [(label, value)]
for the pane to open under the grid.

A read is kept beside a ``.cfile`` as ``<base>.cells.json`` (``save``, ``load``), the tools' JSON
as it came, so the pane shows it again without reading; the list line's words come from it.
"""

import datetime
import json
import os
import sys

#: Beside ``<base>.cfile``.
SUFFIX = '.cells.json'
SCHEMA = 1
#: Recordings sampled slower than this hold no whole cell (``iqmeasure.CELLS_MIN_RATE``).
MIN_RATE = 1.92e6
#: The cell tools, beside this package.
TOOLS = {kind: os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f'iq-{kind}')
         for kind in ('lte', 'nr')}
#: Frames of two LTE cells starting within this of each other (seconds): one site.
SAME_SITE_S = 3e-6
#: The SIB whose message carries the network time.
TIME_SIB = {'nr': 'sib9', 'lte': 'sib16'}
TECH = {'nr': '5G NR', 'lte': 'LTE'}
_WORDS = ('no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine')


def _n(count, word):
    """'three sectors', 'one cell'."""
    number = _WORDS[count] if 0 <= count < len(_WORDS) else str(count)
    return f"{number} {word}{'' if count == 1 else 's'}"


def _sib_number(key):
    try:
        return int(str(key).lower().replace('sib', '').split('-')[0])
    except ValueError:
        return 999


def row_label(kind, key):
    if key == 'mib':
        return 'MIB'
    return str(key).upper() + (', network time' if key == TIME_SIB.get(kind) else '')


def _rows(kind, sibs):
    keys = sorted({str(s).lower() for s in sibs if str(s).lower() not in ('mib', 'sib1')}, key=_sib_number)
    return [(k, row_label(kind, k)) for k in ['mib', 'sib1'] + keys]


def _hex(value, width=0):
    try:
        return f"0x{int(value):0{width}X}"
    except (TypeError, ValueError):
        return None


def _operators(plmns):
    out = []
    for p in plmns or ():
        if isinstance(p, dict) and p.get('mcc') is not None:
            out.append(f"{p['mcc']}-{p.get('mnc', '')}")
        elif isinstance(p, str):
            out.append(p)
    return ', '.join(out) or None


def _value_text(value):
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(', ', ': '))
        return text if len(text) <= 160 else text[:157] + '...'
    return str(value)


def _summary_detail(summary):
    """A SIB's fields as detail lines, its top level only."""
    if not isinstance(summary, dict):
        return []
    return [(str(k), _value_text(v)) for k, v in summary.items() if v not in (None, '', [], {})]


def _mark(mark, text, detail=()):
    return {'mark': mark, 'text': text, 'detail': list(detail)}


def _sector(pci, rel_db, state='waiting'):
    return {'pci': pci, 'rel_db': None if rel_db is None else float(rel_db), 'state': state,
            'identity': {'operator': None, 'area': None, 'cell': None}, 'how': None, 'marks': {},
            'reason': None}


def _title(kind, sectors, sites):
    n = len(sectors)
    if n == 0:
        return f"{TECH[kind]}: no cell found"
    if kind == 'lte' and sites == 1 and n > 1:
        return f"LTE: one site, {_n(n, 'sector')}"
    if kind == 'lte' and sites and sites > 1:
        return f"LTE: {_n(n, 'cell')}, {_n(sites, 'site')}"
    if kind == 'nr' and n > 1:
        return f"5G NR: {_n(n, 'cell')} on one carrier"
    return f"{TECH[kind]}: {_n(n, 'cell')}"


def _lte_sites(offsets):
    """How many sites cells whose frames start at ``offsets`` (seconds) make."""
    offsets = sorted(x for x in offsets if x is not None)
    if not offsets:
        return None
    sites, last = 1, offsets[0]
    for x in offsets[1:]:
        if x - last > SAME_SITE_S:
            sites += 1
        last = x
    return sites


# ----------------------------------------------------------------- the quick check

def quick(path):
    """``{'lte': model or None, 'nr': model or None}`` for the first 50 ms of ``path``: the cells
    found and their levels, nothing read yet. Raises ``iqmeasure.IQSourceError`` (or OSError) for
    a file that cannot be read."""
    from . import iqmeasure
    lte, nr = iqmeasure.cells_file(path)
    out = {'lte': None, 'nr': None}
    if lte.get('searched') and lte.get('cells'):
        cells = sorted(lte['cells'], key=lambda c: -(c.get('relative_power_db') or 0.0))
        sectors = [_sector(c.get('pci'), c.get('relative_power_db')) for c in cells]
        sites = _lte_sites([c.get('frame_offset_s') for c in cells])
        out['lte'] = _model('lte', sectors, _rows('lte', ()), sites)
    if nr.get('searched') and nr.get('ssbs'):
        ssbs = sorted(nr['ssbs'], key=lambda e: -(e.get('relative_power_db') or 0.0))
        out['nr'] = _model('nr', [_sector(e.get('pci'), e.get('relative_power_db')) for e in ssbs],
                           _rows('nr', ()))
    return out


def _model(kind, sectors, rows, sites=None, note=None):
    return {'kind': kind, 'title': _title(kind, sectors, sites), 'rows': rows, 'sectors': sectors,
            'note': note}


# ----------------------------------------------------------------- NR

def _nr_sector(e, stronger):
    """A sector from one ``iq-nr --pdsch`` entry; ``stronger``: the entries before it on its SSB
    frequency, whose transmissions were taken out of its slots."""
    s = _sector(e.get('pci'), e.get('relative_power_db'), 'read')
    mib = e.get('mib')
    bursts = e.get('mib_bursts')
    if mib:
        got = bursts[0] if isinstance(bursts, list) and bursts else None
        detail = [(k.replace('_', ' '), _value_text(v)) for k, v in mib.items()
                  if k not in ('pci', 'metric', 'ssb_s')]
        if isinstance(bursts, list) and len(bursts) == 2:
            detail.insert(0, ('bursts decoded', f"{bursts[0]} of {bursts[1]}"))
        s['marks']['mib'] = _mark('ok', str(got) if got is not None else 'decoded', detail)
    else:
        s['marks']['mib'] = _mark('missing', 'not decoded')
    own = e.get('own')
    if not isinstance(own, dict):
        s['state'] = 'not read'
        s['reason'] = 'no MIB' if not mib else 'not read'
        return s, ()
    removed = [x.get('pci') for x in stronger if isinstance(x.get('own'), dict) and x['own'].get('sib1_hex')]
    s['how'] = f"with {_and(removed)} removed" if removed and any(own.get('cancelled') or ()) else 'alone'
    sib1 = own.get('sib1') or {}
    s['identity'] = {'operator': _operators(sib1.get('plmns')), 'area': _hex(sib1.get('tac')),
                     'cell': _hex(sib1.get('cell_identity'), 9)}
    blocks = own.get('blocks') or {}
    s1 = blocks.get('sib1') or [0, 0]
    if own.get('sib1_hex'):
        detail = [('copies decoded', f"{s1[0]} of {s1[1]} DCIs found")]
        if (blocks.get('combined') or [0, 0])[0]:
            detail.append(('blocks that took copies summed', str(blocks['combined'][0])))
        if own.get('summed'):
            detail.append(('DCI found by summing occasions', 'yes'))
        detail += [('barred', _value_text(sib1.get('cell_barred')))] if 'cell_barred' in sib1 else []
        carrier = sib1.get('carrier') or {}
        if carrier.get('bands'):
            detail.append(('bands', _value_text(carrier['bands'])))
        detail.append(('bytes', own['sib1_hex']))
        s['marks']['sib1'] = _mark('ok', str(s1[0]), detail)
    else:
        s['marks']['sib1'] = _mark('missing', 'not decoded', [('reason', own.get('reason') or 'no SIB1')])
    si = own.get('si') or {}
    heard = si.get('sibs_heard') or {}
    matched = si.get('sibs_matched') or {}
    summaries = si.get('sibs') or {}
    schedule = sib1.get('si_schedule') or []
    # A matched message's SIBs, and the PCI whose bytes it fits.
    match_of = {}
    for m in ((own.get('shared') or {}).get('messages') or []):
        if m.get('matched') and isinstance(m.get('si'), int) and 0 < m['si'] <= len(schedule):
            for sib in schedule[m['si'] - 1].get('sibs') or ():
                match_of[str(sib).lower()] = m
    unsure = bool(si.get('missing_unsure'))
    for sib, count in heard.items():
        detail = [('copies decoded', str(count))] + _summary_detail(summaries.get(sib))
        if sib == TIME_SIB['nr'] and own.get('time'):
            detail.insert(1, ('network time (UTC)', _value_text(own['time'].get('utc'))))
        s['marks'][sib] = _mark('ok', str(count), detail)
    for sib, copies in matched.items():
        if sib in s['marks']:
            continue
        m = match_of.get(sib, {})
        detail = [('copies tested', str(m.get('copies', copies)))]
        if 'z' in m:
            detail.append(('score', f"z {m['z']:g}; chance matches stay under {m['null_z']:g}"))
        detail.append(('what it means', f"not decoded, but its signal fits the bytes PCI {m.get('pci')} "
                                        "was decoded sending" if m.get('pci') is not None else
                                        "not decoded, but its signal fits a stronger cell's bytes"))
        if m.get('hex'):
            detail.append(('bytes', m['hex']))
        s['marks'][sib] = _mark('match', f"same as {m['pci']}" if m.get('pci') is not None else 'matched',
                                detail)
    for sib in si.get('missing') or ():
        if sib in s['marks']:
            continue
        if unsure and removed:
            s['marks'][sib] = _mark('unsure', 'not found, unsure',
                                    [('why unsure', f"PCI {_and(removed)} sends in the same slots, above it")])
        else:
            s['marks'][sib] = _mark('missing', 'not found')
    keys = list(heard) + list(matched) + list(si.get('missing') or ()) + list(si.get('scheduled') or ())
    for sib in si.get('scheduled') or ():
        s['marks'].setdefault(sib, _mark('missing', 'not found'))
    return s, keys


def _and(items):
    items = [str(x) for x in items]
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1] if items else ''


def nr_model(result, waiting=()):
    """The model of an ``iq-nr --pdsch`` reply (``ssbs``, or the entries read so far); ``waiting``:
    sectors of the quick check not read yet, shown after them."""
    entries = [e for e in (result.get('ssbs') if isinstance(result, dict) else result) or ()
               if isinstance(e, dict)]
    sectors, sibs = [], set()
    for i, e in enumerate(entries):
        stronger = [x for x in entries[:i] if abs(float(x.get('ssb_hz') or 0) - float(e.get('ssb_hz') or 0)) < 1e3]
        s, keys = _nr_sector(e, stronger)
        sectors.append(s)
        sibs.update(keys)
    seen = {s['pci'] for s in sectors}
    sectors += [dict(w) for w in waiting if w.get('pci') not in seen]
    return _model('nr', sectors, _rows('nr', sibs))


# ----------------------------------------------------------------- LTE

def _lte_sector(c):
    s = _sector(c.get('pci'), c.get('relative_power_db'), 'read')
    if c.get('duplex', 'FDD') != 'FDD':
        s['state'], s['reason'] = 'not read', 'TDD: not read'
        return s, ()
    mib = c.get('own_mib') or {}
    if mib.get('mib'):
        m = mib['mib']
        detail = [('frames decoded', f"{mib.get('decoded')} of {mib.get('frames')}"),
                  ('antenna ports', _value_text(mib.get('ports')))]
        detail += [(k.replace('_', ' '), _value_text(v)) for k, v in m.items() if k not in ('spare',)]
        s['marks']['mib'] = _mark('ok', str(mib.get('decoded')), detail)
    else:
        s['marks']['mib'] = _mark('missing', 'not decoded', [('reason', mib.get('reason') or 'no MIB')])
    known = c.get('own_known') or []
    s['how'] = f"with {_and(known)} removed" if known else 'alone'
    s1 = c.get('own_sib1') or {}
    own = c.get('own') or {}
    fields = s1.get('fields') or own.get('sib1') or {}
    s['identity'] = {'operator': _operators(fields.get('plmns')), 'area': _hex(fields.get('tac')),
                     'cell': _hex(fields.get('cell_identity'), 7)}
    if s1.get('decoded') or s1.get('combined'):
        sums = len(s1.get('combined') or ())
        detail = [('copies decoded', f"{s1.get('decoded', 0)} of {s1.get('tried', 0)}")]
        if sums:
            detail.append(('blocks from copies summed', str(sums)))
        for k in ('enb_id', 'cell_id', 'freq_band', 'cell_barred'):
            if fields.get(k) is not None:
                detail.append((k.replace('_', ' ').replace('enb', 'eNB').replace('freq ', ''), _value_text(fields[k])))
        if s1.get('sib1'):
            detail.append(('bytes', s1['sib1']))
        s['marks']['sib1'] = _mark('ok', str(s1.get('decoded', 0) + sums), detail)
    elif mib.get('mib'):
        s['marks']['sib1'] = _mark('missing', 'not decoded', [('copies tried', str(s1.get('tried', 0)))])
    si = own.get('si') or {}
    keys = []
    summaries = si.get('sibs') or {}
    for entry in si.get('schedule') or ():
        for sib in entry.get('sibs') or ():
            sib = str(sib).lower()
            keys.append(sib)
            if sib in s['marks']:
                continue
            info = summaries.get(sib) or {}
            if sib in (entry.get('heard') or ()) or info.get('count'):
                count = info.get('count')
                detail = [('copies decoded', _value_text(count))] if count is not None else []
                detail.append(('SI message', f"{entry.get('si')}, every {entry.get('period_frames')} frames"))
                detail += _summary_detail(info.get('summary'))
                if sib == TIME_SIB['lte'] and si.get('time'):
                    detail.insert(1, ('network time', _value_text(si['time'])))
                s['marks'][sib] = _mark('ok', _value_text(count) if count is not None else 'decoded', detail)
            elif entry.get('covered') is False:
                s['marks'][sib] = _mark('span', 'not in this recording',
                                        [('why', f"its window (every {entry.get('period_frames')} frames) "
                                                 "did not come in the span read")])
            elif known:
                s['marks'][sib] = _mark('unsure', 'not found, unsure',
                                        [('why unsure', f"PCI {_and(known)} was taken out of its subframes")])
            else:
                s['marks'][sib] = _mark('missing', 'not found')
    if not s['marks'].get('sib1') and not mib.get('mib'):
        s['reason'] = mib.get('reason') or 'nothing decoded'
    return s, keys


def lte_model(result, waiting=()):
    """The model of an ``iq-lte --own`` reply (its ``cells``, or the cells read so far)."""
    cells = [c for c in (result.get('cells') if isinstance(result, dict) else result) or ()
             if isinstance(c, dict)]
    cells = sorted(cells, key=lambda c: -(c.get('relative_power_db') or 0.0))
    sectors, sibs = [], set()
    for c in cells:
        s, keys = _lte_sector(c)
        sectors.append(s)
        sibs.update(keys)
    seen = {s['pci'] for s in sectors}
    sectors += [dict(w) for w in waiting if w.get('pci') not in seen]
    rate = result.get('rate_sps') if isinstance(result, dict) else None
    sites = _lte_sites([c['frame_offset_samples'] / rate for c in cells
                        if rate and c.get('frame_offset_samples') is not None])
    return _model('lte', sectors, _rows('lte', sibs), sites)


def model(kind, result, waiting=()):
    return (nr_model if kind == 'nr' else lte_model)(result, waiting)


def reading(quick_model, entries):
    """The model while a read runs: the cells done so far (``entries``, as ``--progress`` gives
    them), then the quick check's others, waiting. The title stays the quick check's."""
    kind = quick_model['kind']
    waiting = [dict(s) for s in quick_model['sectors']]
    out = model(kind, list(entries), waiting)
    out['title'] = quick_model['title']
    if not entries:
        out['rows'] = quick_model['rows']
    return out


# ----------------------------------------------------------------- reading

def read_argv(kind, path, python=None):
    """The command that reads ``path``'s cells of ``kind``, reporting as it goes. A recording of
    this app tuned to a station 300 kHz off its centre has the LTE carrier there."""
    tools = os.environ.get('FMRX_CELLS_TOOLS')
    tool = os.path.join(tools, f'iq-{kind}') if tools else TOOLS[kind]
    argv = [python or sys.executable, tool, str(path)]
    if kind == 'nr':
        argv += ['--pdsch', '--progress']
    else:
        argv += ['--own', '--progress']
        station = _station_hz(path)
        if station is not None:
            argv += ['--block-center', repr(station)]
    return argv


def _station_hz(path):
    from .radios import RadioError, read_iq_metadata
    try:
        meta = read_iq_metadata(path)
    except (RadioError, OSError, ValueError, KeyError):
        return None
    station = meta.get('station_hz')
    if not station or abs(station - (meta.get('center_hz') or 0.0)) < 1.0:
        return None
    return float(station)


def progress_line(line):
    """The ``progress`` dict of one stderr line of a read, or None for any other line."""
    line = line.strip()
    if not line.startswith('{"progress"'):
        return None
    try:
        got = json.loads(line).get('progress')
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def progress_text(p):
    """'PCI 124: other SI, 40%'."""
    stage = p.get('stage') or ''
    pct = int(100 * p['done'] / p['total']) if p.get('total') else None
    head = f"PCI {p.get('pci')}: {stage}" if p.get('pci') is not None else stage
    return head + (f", {pct}%" if pct is not None and p.get('total', 1) > 1 else '')


def progress_fraction(p):
    """The whole read's share done, from one progress dict."""
    cells = max(1, int(p.get('cells') or 1))
    within = (p['done'] / p['total']) if p.get('total') else 0.0
    if p.get('stage') == 'done':
        within = 1.0
    return min(1.0, (int(p.get('cell') or 0) + within) / cells)


# ----------------------------------------------------------------- kept beside the recording

def path_for(recording):
    """``<base>.cells.json`` beside a ``.cfile``; None for any other file (the app does not
    write beside those)."""
    base, ext = os.path.splitext(str(recording))
    return base + SUFFIX if ext.lower() == '.cfile' else None


def load(recording):
    """``{'lte': reply or None, 'nr': reply or None, 'read': when, 'seconds': how long,
    'summary': the list line's words}`` kept beside ``recording``, or None."""
    target = path_for(recording)
    if target is None or not os.path.isfile(target):
        return None
    try:
        with open(target) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get('schema') == SCHEMA else None


def save(recording, replies, seconds):
    """Keep ``replies`` ({'lte': ..., 'nr': ...}) beside ``recording``. A file there is replaced
    only when it is one of these. Returns the path, or None when nothing is written."""
    target = path_for(recording)
    if target is None:
        return None
    if os.path.exists(target) and load(recording) is None:
        return None
    doc = {'schema': SCHEMA, 'read': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
           'seconds': round(float(seconds), 1), 'lte': replies.get('lte'), 'nr': replies.get('nr')}
    doc['summary'] = summary(models_of(doc))
    tmp = target + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, target)
    return target


def models_of(doc):
    """The models of a kept read: [model], NR first."""
    out = []
    for kind in ('nr', 'lte'):
        if isinstance(doc.get(kind), dict):
            out.append(model(kind, doc[kind]))
    return out


def summary(models):
    """The list line's words: '5G NR, 3 cells', 'LTE, 2 cells'; '' when there are none."""
    parts = []
    for m in models or ():
        if m and m.get('sectors'):
            n = len(m['sectors'])
            parts.append(f"{TECH[m['kind']]}, {n} cell{'' if n == 1 else 's'}")
    return '; '.join(parts)
