Task: you are the TEST AUTHOR for phase m2 of the FM receiver, task `tests`. Turn the spec below and CONTRACTS-measure.md
into acceptance tests for the two command line tools tools/iq-measure and tools/iq-picture. Other models will
implement them later without seeing you, so the tests are the spec: precise, deterministic and passable.

Read AGENTS.md first (rule 2 is lifted for the one test file listed below). Do not list or read anything outside the
working directory. Read CONTRACTS-measure.md (sections "tools/fm_receiver/iqmeasure.py" and "Tools"), CLAUDE.md,
tools/dvr-sweep (the pattern), tools/fm_receiver/iqmeasure.py (read-only: the m1 core the tools wrap),
tools/tests/signals.py (write_sdvr, write_sweep_sdvr, write_blue) and tools/tests/test_iq_measure.py (copy its
harness style and its synthetic-signal builders into your file by COPYING the small functions; do not import from
another test script, do not edit signals.py).

Edit or create only:
- tools/tests/test_iq_tools.py (new)
Do NOT edit any other file. The tools do not exist yet, so the new tests are EXPECTED to fail. Run it as:
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
from the worktree root. Plain script, check helper, prints "all checks passed" and exits 0 on success, non-zero
otherwise; every check runs and is reported (one failure does not hide the rest). Keep it fast: signals of about
1 s at 1 MS/s, built once and shared. Use a temporary folder; never use production or captured data.

What to pin (run the tools as SUBPROCESSES with sys.executable, `tools/iq-measure ...`, from a temporary working
directory, with PYTHONPATH not needed):
- iq-measure on a .cfile with its .sigmf-meta of each synthetic signal (CW, OOK, FSK, FM, AM, noise) prints JSON on
  stdout whose `class` is the signal's, exit 0; stdout is only the JSON.
- Options: --t0/--t1 narrow the span (card source t0_s/t1_s reflect it), --band LO HI restricts, a bare .cfile with
  --rate (and --center) is accepted and without --rate exits 1 with a message on stderr and nothing on stdout.
- --card writes <base>.card.json beside a .cfile (file read back equals the printed card); exits 1 and writes nothing
  new for an IQ .sdvr and a .cdif (folder snapshot before/after); a sweep .sdvr, a .fft and a missing file exit 1;
  --card never overwrites a file that is not a card (put a non-card .card.json there first; assert untouched, exit 1).
- iq-measure never writes anything without --card (snapshot) and never modifies its input (hash).
- An IQ .sdvr (write_sdvr) and a .cdif (write_blue) of a CW tone give class `carrier` or the signal's class, with the
  centre frequency read back.
- iq-picture FILE OUT.png writes a valid PNG at least 800 x 500 px (read the PNG header yourself; do not import
  matplotlib or PIL in the test); exit 0; works for .cfile (with .sigmf-meta), a bare .cfile with --rate/--center,
  .sdvr, .cdif; exits 1 with a message on stderr and writes no PNG for a sweep .sdvr, a .fft, a missing file.
- Import rule: a clean subprocess that loads tools/iq-picture and tools/iq-measure as modules (importlib
  SourceFileLoader with a name other than __main__, so main does not run) lists none of gnuradio, PyQt5, library,
  radios in sys.modules (and iq-measure also not scipy or matplotlib).
- Two-tone: the module tools/iq-picture defines at module level `peak_panel(path, t0=None, t1=None, band=None,
  rate=None, center_hz=None, nfft=None)` returning `(freq_hz, peak_db)`: two equal-length numpy arrays, the absolute
  frequency axis and the peak-hold level of the spectrum panel (the data the top panel plots). Load the module as
  above and call it on a two-tone .cfile (+20 kHz and -60 kHz offsets from the centre, 30 dB SNR); assert both lines
  are local maxima at least 15 dB above the median of peak_db, within 2 bins of the expected absolute frequency
  (bin = rate / nfft; take nfft from the array length if nfft is None). This is the only place the test may call a
  function of the tool; everything else goes through the command line.
- Read-only: iq-picture writes only the PNG it is told to (snapshot).
- Keep each subprocess call under a 120 s timeout and report a timeout as a failure.

SPEC:
# Phase m2 spec: the tools, the card file and `fmctl measure`

Goal: `tools/iq-measure` and `tools/iq-picture` as in `CONTRACTS-measure.md`, cards written beside this app's own
`.cfile`, and `fmctl measure`. Needs phase m1 merged. Read `CONTRACTS-measure.md`, `CLAUDE.md`, `AGENTS.md`,
`tools/dvr-sweep` (the pattern).

First command of the manager: `swarm.py phase m2 active`.

## Tasks and file ownership
| task | model(s) | edits only | notes |
|---|---|---|---|
| tests | haiku; reviewers grokw, musefree + consensus | `tools/tests/test_iq_tools.py` (new) | runs the tools as subprocesses on synthetic files from `tests/signals.py`, `write_sdvr`, `write_blue` and a `.cfile` it writes |
| iq-measure | single author: grokw; reviewers musefree, haiku | `tools/iq-measure` (new) | thin wrapper over `iqmeasure` |
| iq-picture | bake-off: grokw, musefree | `tools/iq-picture` (new) | matplotlib `Agg`; a PNG reviewed by looking at it (`screenshot`-free: open the PNG) |
| fmctl measure | single author: haiku; reviewers grokw, musefree | `tools/fm_receiver/control.py`, `tools/tests/test_control_more.py` (append only) | after the tools exist; the last capture's path is the one `capture` replied with |
| fmctl-paths | single author: haiku; reviewers grokw, musefree | `tools/fmctl`, `tools/fm_receiver/app.py` (only `_delete_recordings`), `tools/tests/test_control_more.py` (append only) | from m1's review: `tools/fmctl` must make a relative path absolute (against fmctl's own cwd) and keep a quoted path with spaces whole, for `open`, `recordings-folder`, `folders add/remove`, `iq-measure`-style paths in `measure`; `_delete_recordings` must return what it deleted so `delete N confirm` can reply it; also check `cmd_mode`'s second `setCurrentIndex` retry cannot open the radio twice and report (fix only if proven by a test) |
| wiring | haiku | `tools/tests/run_all.py` | add `test_iq_tools.py` |

## Behaviour
- As in the contract's "Tools" section and the `measure` row of the fmctl table.
- `iq-measure` on each synthetic signal of m1 prints a card whose `class` is the signal's; `--card` writes
  `<base>.card.json` beside a `.cfile` and exits 1 (nothing written) for `.sdvr`, `.cdif`.
- `iq-picture` writes a PNG at least 800 x 500 px; a subprocess that imports it lists none of gnuradio, PyQt5,
  `library`, `radios`; the picture of a two-tone file has both tones as visible lines (the test finds them by
  the peak panel's data, not by eye).
- `fmctl measure` after `capture 2 iq-band` replies the card of that file; with `card` the card file exists.

## Acceptance targets
- `test_iq_tools.py`, `test_control_more.py`, `test_iq_measure.py`, `test_control.py`: exit 0.
- `iq-measure` on a real 8.6 s Sceptre DVR (the planner's, outside the repo): the card in under 20 s.

## Gates (pre-registered)
- G1 `tools/tests/test_iq_tools.py` exits 0. G2 `test_control_more.py` exits 0 including `measure`.
- G3 the existing suites of m1's G3 exit 0, and `test_iq_measure.py`. G3b `fmctl open` of a relative path and of a path with a space works from another working directory (a test). G4 the real-DVR timing, measured by the planner.
- G5 the PNG of the planner's real recording is looked at by the planner and judged readable (axes, units,
  title): not delegated.
- Bake-off rubric (iq-picture): correctness and the import rule 40; the picture's readability (labels, units,
  scale, no clipped text; judged from the PNG each entry writes) 40; clarity 20. Winner rule as in m1.

## Amendments (dated, post-hoc, with direction)
- 2026-10-14: added the `fmctl-paths` task (three items m1's consensus found outside its files). A new task before any m2 run; no gate changed (conservative).

## Out of scope
- Anything outside the files in "edits only"; reading `/data/python/SDR`, `/data/sceptre` or any path outside the
  worktree; captured recordings (synthetic signals only); pushing; changing a gate.

