"""The 5G NR sync finder (phase n1): ``fm_receiver/iqnr.py`` against
tools/swarm-records/specs/n1.md, the module half of the contract.

Signals: ``tests/nr_synth.py`` (an NR downlink with SSBs, seeded) and the
non-NR signals of ``tests/lte_negatives.py`` and ``tests/lte_synth.py``. Every
search case is built and searched in a worker process (a spawn pool, as
test_iqlte does); the timing checks (G9, I) run afterwards in this process,
alone, so the pool does not inflate them. The GSCN and raster arithmetic is
written out here from the spec's table, not taken from the module, so the
expected answers do not depend on the code under test.

Groups, one ok/FAIL line per check (the gate in brackets; each counted line
prints the measured count and the threshold):
  A  G1  10 MHz, 15 kHz case A, L_max 4, ssb_hz 600.15 MHz, PCIs 0 1 2 3 123 200
         217 321 500 1007, 20 and 0 dB, seeds 0-1 (40 cases): strongest entry's
         PCI right; the 20 dB cases counted on their own (20 cases)
  B  G2  20 MHz, 30 kHz case C, same PCIs, seed 0: L_max 4 at 600.15 MHz and
         L_max 8 at 3721.44 MHz (20 cases): strongest entry's PCI right
  C  G3  raster search (no ssb_hz), centre 600.0 MHz, 20 MS/s, 20 dB, 20 MHz
         carrier: 10 cases at 15 kHz and 10 at 30 kHz, each SSB on a seeded
         raster point: strongest entry's GSCN, SCS and PCI right (18 of 20);
         no entry at a wrong GSCN in 19 of 20 cases
  D  G4  two carriers, 40 MS/s, centre 600.0 MHz, PCIs 200 and 217 at -10 and
         +8 MHz, seeds 0-4 (5 cases): both PCIs listed at their own GSCNs (4 of 5)
  E  G5  DM-RS and timing, 15 kHz case A, ssb_sent (1,) and (2,), n_hf 0 and 1,
         PCIs 123 and 321, seeds 0-2 (24 cases): ssb_index and half_frame right
         (22 of 24); periods 20 and 10 ms on 200 ms, PCI 123, seeds 0-4 (10
         cases): period_ms right (10 of 10)
  F  G6  noise, tone, wifi_like, atsc_like, uplink_like and synth_lte (10 MHz,
         PCI 123), seeds 0-4, centre 600.0 MHz, 20 MS/s (30 cases): [] (30 of 30);
         a 1 ms and a 1 MS/s NR capture: [] and no exception (2 of 2)
  G  G7  raster table: the spec's 9 examples both ways, exact to 1 Hz (9 of 9);
         hz_to_gscn(600.10 MHz) is None; gscn_to_hz(1) and (22256) raise ValueError
  H  G8  CFO: 15 kHz case A PCI 321 at +5 and -5 kHz; 30 kHz case C PCI 217 at
         +10 and -10 kHz (4 of 4): strongest entry's PCI right
  I  G9  timing, serial: raster ssb_search on 100 ms at 20 MS/s under 30 s; with
         ssb_hz under 5 s. (The command's peak RSS is in test_iq_nr.py.)
  never  over every case above, the two "never" sentences of the spec only: a PCI
         is never guessed (no listed entry with SSS chance above 1e-3, the
         chance rule of ssb_search) and at most one entry per (gscn, pci). A case
         that did not run is a failure of the sweep. Wrong PCIs and GSCNs are
         counted by the gates (G1-G4), not swept.
  L8     one L_max 8 case (3721.44 MHz): the strongest entry's half_frame is None
  interface  every listed entry has the spec's fields; raster_in_band(600 MHz,
         20 MS/s, 15 kHz) equals the list computed here
  G10    iqnr.py imports only numpy, the standard library and fm_receiver (ast)

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iqnr.py
(about 155 searches and synthesised captures, most of them 100 ms; the pool
uses at most six workers)
"""

import ast
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests import lte_negatives, lte_synth, nr_synth  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE = os.path.join(ROOT, 'tools', 'fm_receiver', 'iqnr.py')

PCIS = (0, 1, 2, 3, 123, 200, 217, 321, 500, 1007)
A_SSB = 600.15e6          # GSCN 1500, below 3 GHz (L_max 4): A, E, H
B_SSB_L8 = 3721.44e6      # GSCN 8000, above 3 GHz (L_max 8)
C_CENTRE = 600.0e6
C_FS = 20e6
F_CENTRE = 600.0e6
F_FS = 20e6
D_CENTRE = 600.0e6
D_FS = 40e6
CHANCE_MAX = 1e-3
WORKERS = max(1, min(6, os.cpu_count() or 1))
NEGATIVES = ('synth_noise', 'synth_tone', 'synth_wifi_like', 'synth_atsc_like', 'synth_uplink_like')
BRIEF_KEYS = ('gscn', 'scs_khz', 'pci', 'chance', 'ssb_index', 'half_frame', 'period_ms', 'ssb_hz')
FIELDS = ('ssb_hz', 'gscn', 'scs_khz', 'pci', 'nid1', 'nid2', 'ssb_index', 'half_frame', 'l_max',
          'period_ms', 'bursts', 'first_ssb_s', 'cfo_hz', 'pss_score', 'sss_score', 'sss_next',
          'chance', 'dmrs_score', 'dmrs_chance', 'relative_power_db', 'detail')
RESULTS = []


# ---- harness ----------------------------------------------------------------------

def report(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    line = f"  {'ok  ' if ok else 'FAIL'} {name}"
    if not ok and detail:
        line += f"  ({detail})"
    print(line, flush=True)


def expect(name, test):
    """``test()`` returns ``(ok, detail)``; an exception is a failed check."""
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f'{type(exc).__name__}: {exc}'
    report(name, ok, detail)


def counted(name, rows, need):
    """One line for a group: the count, the threshold, and why the rest failed."""
    n_ok = sum(1 for _, ok, _ in rows if ok)
    bad = [f'{label}: {why}' for label, ok, why in rows if not ok]
    more = f'; and {len(bad) - 2} more' if len(bad) > 2 else ''
    report(f'{name}: {n_ok} of {len(rows)} (need {need})',
           len(rows) > 0 and n_ok >= need, '; '.join(bad[:2]) + more)


# ---- independent arithmetic (38.104 table 5.4.3.1-1, written from the spec) ------------

def ref_gscn(hz):
    """GSCN of a sync raster frequency. Raises ValueError when hz is not a raster point."""
    f = int(round(hz))
    if f < 3000000000:
        n, rem = divmod(f, 1200000)
        if rem not in (50000, 150000, 250000):
            raise ValueError(f'{hz} is not a raster point below 3 GHz')
        return 3 * n + (rem // 50000 - 3) // 2
    d, rem = divmod(f - 3000000000, 1440000)
    if rem:
        raise ValueError(f'{hz} is not a raster point above 3 GHz')
    return 7499 + d


def raster_points(centre_hz, fs, scs_khz):
    """(gscn, SS_REF Hz) of every raster point whose whole SSB lies inside centre +/- 0.45 fs,
    ascending. Integer Hz throughout."""
    half = int(round(120 * scs_khz * 1e3))
    lo = int(round(centre_hz - 0.45 * fs))
    hi = int(round(centre_hz + 0.45 * fs))
    out = []
    for n in range(lo // 1200000 - 1, hi // 1200000 + 2):
        for m in (1, 3, 5):
            f = n * 1200000 + m * 50000
            if lo + half <= f <= hi - half:
                out.append((ref_gscn(f), f))
    return sorted(out, key=lambda item: item[1])


# ---- the jobs: one synthesised capture and one search each ---------------------------

def job(group, label, make, centre, search=None, pairs=(), head=None, **extra):
    """``pairs``: the right (pci, gscn) answers of this capture; a listed entry that is not
    one of them is wrong. The search runs in a worker (see ``_job``)."""
    out = dict(group=group, label=label, make=make, centre=float(centre),
               search=dict(search or {}), pairs=list(pairs), head=head)
    out.update(extra)
    return out


def jobs_a():
    out = []
    for pci in PCIS:
        for snr in (20.0, 0.0):
            for seed in (0, 1):
                kw = dict(pci=pci, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20,
                          n_hf=0, bw_mhz=10, ssb_offset_hz=0.0, dur_ms=100, snr_db=snr, seed=seed)
                out.append(job('A', f'pci {pci} {snr:g} dB seed {seed}', ('nr', kw), A_SSB,
                               search=dict(ssb_hz=A_SSB), pairs=[(pci, GSCN_A)], snr=snr))
    return out


GSCN_A = ref_gscn(A_SSB)


def jobs_b():
    out = []
    for pci in PCIS:
        for l_max, ssb in ((4, A_SSB), (8, B_SSB_L8)):
            kw = dict(pci=pci, scs_khz=30, case='C', l_max=l_max, ssb_sent=(0,), period_ms=20,
                      n_hf=0, bw_mhz=20, ssb_offset_hz=0.0, dur_ms=100, snr_db=20.0, seed=0)
            out.append(job('B', f'pci {pci} L_max {l_max}', ('nr', kw), ssb,
                           search=dict(ssb_hz=ssb), pairs=[(pci, ref_gscn(ssb))]))
    return out


def jobs_l8():
    """One L_max 8 capture: half_frame is None (the interface's rule for L_max 8)."""
    kw = dict(pci=123, scs_khz=30, case='C', l_max=8, ssb_sent=(0,), period_ms=20, n_hf=0,
              bw_mhz=20, ssb_offset_hz=0.0, dur_ms=100, snr_db=20.0, seed=0)
    return [job('L8', 'pci 123 L_max 8 at 3721.44 MHz', ('nr', kw), B_SSB_L8,
                search=dict(ssb_hz=B_SSB_L8), pairs=[(123, ref_gscn(B_SSB_L8))], want_half=None)]


def jobs_c():
    out = []
    for scs, case in ((15, 'A'), (30, 'C')):
        points = raster_points(C_CENTRE, C_FS, scs)
        for k, pci in enumerate(PCIS):
            rng = np.random.default_rng(1000 + 100 * scs + k)
            gscn, ref = points[int(rng.integers(len(points)))]
            kw = dict(pci=pci, scs_khz=scs, case=case, l_max=4, ssb_sent=(0,), period_ms=20,
                      n_hf=0, bw_mhz=20, ssb_offset_hz=float(ref - C_CENTRE), dur_ms=100,
                      snr_db=20.0, fs=C_FS, seed=k)
            out.append(job('C', f'{scs} kHz pci {pci} gscn {gscn}', ('nr', kw), C_CENTRE,
                           pairs=[(pci, gscn)], scs=scs))
    return out


def jobs_d():
    out = []
    for seed in range(5):
        cells = [dict(pci=200, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                      ssb_offset_hz=0.45e6, power_db=0.0, carrier_offset_hz=-10e6),
                 dict(pci=217, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                      ssb_offset_hz=0.45e6, power_db=-6.0, carrier_offset_hz=8e6)]
        g1 = ref_gscn(D_CENTRE - 10e6 + 0.45e6)
        g2 = ref_gscn(D_CENTRE + 8e6 + 0.45e6)
        kw = dict(cells=cells, bw_mhz=10, dur_ms=100, snr_db=20.0, fs=D_FS, seed=seed)
        out.append(job('D', f'seed {seed}', ('multi', kw), D_CENTRE,
                       pairs=[(200, g1), (217, g2)]))
    return out


def jobs_e():
    out = []
    for ssb_sent in ((1,), (2,)):
        for n_hf in (0, 1):
            for pci in (123, 321):
                for seed in (0, 1, 2):
                    kw = dict(pci=pci, scs_khz=15, case='A', l_max=4, ssb_sent=ssb_sent,
                              period_ms=20, n_hf=n_hf, bw_mhz=10, ssb_offset_hz=0.0, dur_ms=100,
                              seed=seed)
                    out.append(job('E', f'pci {pci} ssb {ssb_sent[0]} hf {n_hf} seed {seed}',
                                   ('nr', kw), A_SSB, search=dict(ssb_hz=A_SSB),
                                   pairs=[(pci, GSCN_A)], want_index=ssb_sent[0], want_half=n_hf))
    for period in (20, 10):
        for seed in range(5):
            kw = dict(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=period,
                      n_hf=0, bw_mhz=10, ssb_offset_hz=0.0, dur_ms=200, seed=seed)
            out.append(job('P', f'period {period} seed {seed}', ('nr', kw), A_SSB,
                           search=dict(ssb_hz=A_SSB), pairs=[(123, GSCN_A)], want_period=period))
    return out


def jobs_f():
    out = []
    for name in NEGATIVES:
        for seed in range(5):
            out.append(job('F', f'{name} seed {seed}',
                           (name, dict(seed=seed, fs=F_FS, dur=0.1)), F_CENTRE))
    for seed in range(5):
        kw = dict(bw_mhz=10, n_frames=10, pci=123, seed=seed, fs=F_FS)
        out.append(job('F', f'synth_lte seed {seed}', ('lte', kw), F_CENTRE))
    short = dict(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                 bw_mhz=10, ssb_offset_hz=0.15e6, dur_ms=100, seed=0)
    out.append(job('S', '1 ms of NR at 20 MS/s', ('nr', dict(short, fs=F_FS)), F_CENTRE,
                   head=int(round(1e-3 * F_FS))))
    out.append(job('S', 'NR at 1 MS/s', ('nr', dict(short, fs=1e6)), F_CENTRE))
    return out


def jobs_h():
    out = []
    for cfo in (5e3, -5e3):
        kw = dict(pci=321, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                  bw_mhz=10, ssb_offset_hz=0.0, dur_ms=100, cfo_hz=cfo, seed=0)
        out.append(job('H', f'15 kHz pci 321 cfo {cfo:+.0f}', ('nr', kw), A_SSB,
                       search=dict(ssb_hz=A_SSB), pairs=[(321, GSCN_A)]))
    for cfo in (10e3, -10e3):
        kw = dict(pci=217, scs_khz=30, case='C', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                  bw_mhz=20, ssb_offset_hz=0.0, dur_ms=100, cfo_hz=cfo, seed=0)
        out.append(job('H', f'30 kHz pci 217 cfo {cfo:+.0f}', ('nr', kw), A_SSB,
                       search=dict(ssb_hz=A_SSB), pairs=[(217, GSCN_A)]))
    return out


def all_jobs():
    return jobs_a() + jobs_b() + jobs_c() + jobs_d() + jobs_e() + jobs_f() + jobs_h() + jobs_l8()


# ---- the worker: synthesise, then search; every failure comes back as text -----------

BRIEF = BRIEF_KEYS
FULL = FIELDS


def _make(kind, kw):
    if kind == 'nr':
        iq, fs, _ = nr_synth.synth_nr(**kw)
    elif kind == 'multi':
        iq, fs, _ = nr_synth.synth_nr_multi(**kw)
    elif kind == 'lte':
        iq, fs = lte_synth.synth_lte(**kw)
    else:
        iq, fs = getattr(lte_negatives, kind)(**kw)
    return iq, fs


def _job(j):
    """Run one job. Returns a small plain dict: ``error`` (None, or 'stage: exception'),
    ``entries`` (the brief fields of each listed entry, strongest first), ``missing``
    (spec fields absent from an entry), ``seconds`` (the search only)."""
    out = dict(error=None, entries=[], missing=[], seconds=0.0)
    stage = 'synth'
    try:
        iq, fs = _make(j['make'][0], j['make'][1])
        if j['head']:
            iq = iq[:j['head']]
        stage = 'import'
        from fm_receiver import iqnr
        stage = 'search'
        t0 = time.perf_counter()
        res = iqnr.ssb_search(iq, fs, j['centre'], **j['search'])
        out['seconds'] = time.perf_counter() - t0
        out['entries'] = [{k: e[k] for k in BRIEF} for e in res]
        out['missing'] = [[k for k in FULL if k not in e] for e in res]
    except Exception as exc:
        out['error'] = f'{stage}: {type(exc).__name__}: {exc}'
    return out


def run_pool(jobs):
    ctx = multiprocessing.get_context('spawn')
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx) as ex:
        return list(ex.map(_job, jobs, chunksize=1))


# ---- grading ---------------------------------------------------------------------

def rows_of(pairs, group, fn, where=None):
    """(label, ok, why) for each job of ``group`` (and ``where(job)`` when given); a job that
    errored fails with its error."""
    out = []
    for j, o in pairs:
        if j['group'] != group or (where is not None and not where(j)):
            continue
        if o['error']:
            out.append((j['label'], False, o['error']))
            continue
        ok, why = fn(j, o)
        out.append((j['label'], ok, why))
    return out


def _top(o):
    return o['entries'][0] if o['entries'] else None


def fn_pci(j, o):
    top = _top(o)
    want = j['pairs'][0][0]
    if top is None:
        return False, 'nothing listed'
    return top['pci'] == want, f"strongest PCI {top['pci']}, want {want}"


def fn_c(j, o):
    top = _top(o)
    want = (j['pairs'][0][0], j['pairs'][0][1], j['scs'])
    if top is None:
        return False, 'nothing listed'
    got = (top['pci'], top['gscn'], top['scs_khz'])
    return got == want, f'strongest (pci, gscn, scs) {got}, want {want}'


def fn_c_gscn(j, o):
    want = j['pairs'][0][1]
    wrong = [e['gscn'] for e in o['entries'] if e['gscn'] != want]
    return not wrong, f'entries at GSCN {wrong}, want only {want}'


def fn_d(j, o):
    found = []
    for pci, gscn in j['pairs']:
        found.append(any(e['pci'] == pci and e['gscn'] == gscn for e in o['entries']))
    want = ', '.join(f'pci {p} at gscn {g}' for p, g in j['pairs'])
    return all(found), f'found {found}, want {want}; listed {[(e["pci"], e["gscn"]) for e in o["entries"]]}'


def fn_index(j, o):
    top = _top(o)
    if top is None:
        return False, 'nothing listed'
    ok = top['ssb_index'] == j['want_index'] and top['half_frame'] == j['want_half']
    return ok, (f"ssb_index {top['ssb_index']} half_frame {top['half_frame']}, "
                f"want {j['want_index']} and {j['want_half']}")


def fn_period(j, o):
    top = _top(o)
    if top is None:
        return False, 'nothing listed'
    return top['period_ms'] == j['want_period'], f"period_ms {top['period_ms']}, want {j['want_period']}"


def fn_empty(j, o):
    return not o['entries'], f"{len(o['entries'])} entries listed"


def fn_pci_listed(j, o):
    top = _top(o)
    if top is None:
        return False, 'nothing listed'
    return top['pci'] == j['pairs'][0][0], f"strongest PCI {top['pci']}"


def sweep(pairs):
    """Every case of the grid: wrong PCIs, wrong GSCNs, chances above the limit, repeats."""
    ran = 0
    entries = 0
    bad = {'wrong PCI': [], 'wrong GSCN': [], 'chance above 1e-3': [], 'repeated (gscn, pci)': []}
    for j, o in pairs:
        if o['error']:
            continue
        ran += 1
        pcis = {p for p, _ in j['pairs']}
        gscns = {g for _, g in j['pairs']}
        seen = set()
        for e in o['entries']:
            entries += 1
            if e['pci'] not in pcis:
                bad['wrong PCI'].append(j['label'])
            if e['gscn'] not in gscns:
                bad['wrong GSCN'].append(j['label'])
            if not (e['chance'] <= CHANCE_MAX):
                bad['chance above 1e-3'].append(j['label'])
            key = (e['gscn'], e['pci'])
            if key in seen:
                bad['repeated (gscn, pci)'].append(j['label'])
            seen.add(key)
    return ran, entries, bad


# ---- the groups -------------------------------------------------------------------

def group_a(outs):
    counted('A G1 right PCI, all 40 cases', rows_of(outs, 'A', fn_pci), 38)
    counted('A G1 right PCI at 20 dB', rows_of(outs, 'A', fn_pci, where=lambda j: j['snr'] == 20.0), 20)


def group_b(outs):
    counted('B G2 right PCI, L_max 4 and 8 (30 kHz case C, 20 MHz)', rows_of(outs, 'B', fn_pci), 19)


def group_c(outs):
    counted('C G3 right GSCN, SCS and PCI', rows_of(outs, 'C', fn_c), 18)
    counted('C G3 no entry at a wrong GSCN', rows_of(outs, 'C', fn_c_gscn), 19)


def group_d(outs):
    counted('D G4 both PCIs at their own GSCNs, per capture', rows_of(outs, 'D', fn_d), 4)


def group_e(outs):
    counted('E G5 ssb_index and half_frame right', rows_of(outs, 'E', fn_index), 22)
    counted('E G5 period_ms right (periods 20 and 10 ms, 200 ms)', rows_of(outs, 'P', fn_period), 10)


def group_f(outs):
    counted('F G6 [] on the negatives and synth_lte', rows_of(outs, 'F', fn_empty), 30)
    counted('F G6 [] on the 1 ms and 1 MS/s NR captures (no exception)', rows_of(outs, 'S', fn_empty), 2)


def fn_l8(j, o):
    top = _top(o)
    if top is None:
        return False, 'nothing listed'
    return top['half_frame'] is None, f"half_frame {top['half_frame']!r}, want None"


def group_l8(outs):
    counted('L8 half_frame is None at L_max 8', rows_of(outs, 'L8', fn_l8), 1)


def group_h(outs):
    counted('H G8 right PCI with CFO', rows_of(outs, 'H', fn_pci), 4)


def never(outs):
    """The two spec "never" sentences over every case (zero tolerance). A case that did
    not run fails the sweep too. The wrong-PCI and wrong-GSCN counts are gates, not sweeps."""
    ran, entries, bad = sweep(outs)
    total = len(outs)
    for kind in ('chance above 1e-3', 'repeated (gscn, pci)'):
        items = bad[kind]
        ok = ran == total and not items
        detail = f'{len(items)} bad, first {items[:3]}' if items else ''
        if ran != total:
            detail = f'{total - ran} cases did not run'
        report(f'never: no {kind} in a listed entry: {len(items)} of {entries} entries, '
               f'{ran} of {total} cases ran (need 0)', ok, detail)


def _search_timed(make_kw, search, centre):
    """Synthesise one capture (not timed), then time ``ssb_search`` alone."""
    iq, fs, _ = nr_synth.synth_nr(**make_kw)
    from fm_receiver import iqnr as mod
    t0 = time.perf_counter()
    res = mod.ssb_search(iq, fs, centre, **search)
    return time.perf_counter() - t0, res


TIMING_KW = dict(pci=123, scs_khz=15, case='A', l_max=4, ssb_sent=(0,), period_ms=20, n_hf=0,
                 bw_mhz=20, ssb_offset_hz=0.15e6, dur_ms=100, fs=20e6, seed=0)


def group_timing():
    def raster():
        dt, res = _search_timed(TIMING_KW, {}, 600.0e6)
        top = res[0]['pci'] if res else None
        return dt < 30.0, f'{dt:.2f} s, limit 30 s, strongest PCI {top}'
    expect('I G9 raster ssb_search, 100 ms at 20 MS/s: under 30 s', raster)

    def with_hz():
        dt, res = _search_timed(TIMING_KW, dict(ssb_hz=600.15e6), 600.0e6)
        top = res[0]['pci'] if res else None
        return dt < 5.0, f'{dt:.2f} s, limit 5 s, strongest PCI {top}'
    expect('I G9 ssb_search with ssb_hz, 100 ms at 20 MS/s: under 5 s', with_hz)


def group_table():
    def mod():
        from fm_receiver import iqnr
        return iqnr

    examples = ((2, 1.25e6), (3, 1.35e6), (4, 1.45e6), (1499, 600.05e6), (1500, 600.15e6),
                (1501, 600.25e6), (7499, 3000.00e6), (8000, 3721.44e6), (22255, 24248.64e6))
    rows = []
    for g, hz in examples:
        try:
            m = mod()
            got_hz, got_g = m.gscn_to_hz(g), m.hz_to_gscn(hz)
            ok = abs(got_hz - hz) <= 1.0 and got_g == g
            why = f'gscn_to_hz {got_hz!r}, hz_to_gscn {got_g!r}'
        except Exception as exc:
            ok, why = False, f'{type(exc).__name__}: {exc}'
        rows.append((f'GSCN {g}', ok, why))
    counted('G G7 spec examples both ways, exact to 1 Hz', rows, 9)
    expect('G G7 hz_to_gscn(600.10 MHz) is None',
           lambda: (mod().hz_to_gscn(600.10e6) is None, repr(mod().hz_to_gscn(600.10e6))))
    for g in (1, 22256):
        def raises(g=g):
            try:
                mod().gscn_to_hz(g)
            except ValueError:
                return True, ''
            return False, 'no ValueError'
        expect(f'G G7 gscn_to_hz({g}) raises ValueError', raises)


def group_interface(outs):
    def fields():
        bad = sum(len(m) for j, o in outs if not o['error'] for m in o['missing'])
        n = sum(len(o['entries']) for j, o in outs if not o['error'])
        return n > 0 and bad == 0, f'{bad} missing fields over {n} entries (need entries to check)'
    expect('interface: every listed entry has the spec fields', fields)

    def band():
        from fm_receiver import iqnr as mod
        got = [(g, float(hz)) for g, hz in mod.raster_in_band(C_CENTRE, C_FS, 15)]
        want = raster_points(C_CENTRE, C_FS, 15)
        same = len(got) == len(want) and all(
            g1 == g2 and abs(h1 - h2) <= 1.0 for (g1, h1), (g2, h2) in zip(got, want))
        return same, f'{len(got)} points, want {len(want)}'
    expect('interface: raster_in_band(600.0 MHz, 20 MS/s, 15 kHz) equals the list computed here', band)


def group_imports():
    try:
        with open(MODULE) as fh:
            tree = ast.parse(fh.read(), MODULE)
    except OSError as exc:
        report('G10 iqnr.py imports only numpy, the standard library and fm_receiver', False,
               f'{type(exc).__name__}: {exc}')
        return
    stdlib = set(sys.stdlib_module_names)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(('fm_receiver' if node.level else node.module.split('.')[0]))
    bad = sorted(m for m in mods if m not in stdlib | {'numpy', 'fm_receiver'})
    report('G10 iqnr.py imports only numpy, the standard library and fm_receiver', not bad,
           f'imports {bad}')


def main():
    t0 = time.time()
    jobs = all_jobs()
    print(f'  note  {len(jobs)} captures, {WORKERS} workers', flush=True)
    try:
        outs = list(zip(jobs, run_pool(jobs)))
    except Exception as exc:
        report('pool ran every capture', False, f'{type(exc).__name__}: {exc}')
        outs = []
    if outs:
        for name, fn in (('A', group_a), ('B', group_b), ('C', group_c), ('D', group_d),
                         ('E', group_e), ('F', group_f), ('H', group_h), ('L8', group_l8)):
            try:
                fn(outs)
            except Exception as exc:
                report(f'{name} ran', False, f'{type(exc).__name__}: {exc}')
        try:
            never(outs)
        except Exception as exc:
            report('never ran', False, f'{type(exc).__name__}: {exc}')
        try:
            group_interface(outs)
        except Exception as exc:
            report('interface ran', False, f'{type(exc).__name__}: {exc}')
    try:
        group_timing()
    except Exception as exc:
        report('I G9 timing ran', False, f'{type(exc).__name__}: {exc}')
    for fn in (group_table, group_imports):
        try:
            fn()
        except Exception as exc:
            report(f'{fn.__name__} ran', False, f'{type(exc).__name__}: {exc}')
    failed = [name for name, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed in {time.time() - t0:.0f} s')
    if failed:
        print('failed:')
        for name in failed:
            print(f'  {name}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
