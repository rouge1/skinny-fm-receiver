Task: you are the TEST AUTHOR for phase m1 of the FM receiver. Turn the spec below and CONTRACTS-measure.md into
acceptance tests. Other models will implement it later without seeing you, so the tests are the spec: precise,
deterministic and passable.

Read AGENTS.md first (rule 2 is lifted for the one test file listed below). Do not list or read anything outside
the working directory. Read CONTRACTS-measure.md, CLAUDE.md, tools/fm_receiver/control.py, tools/fm_receiver/app.py (read-only, to learn the knobs, Run button and recordings list) and tools/tests/test_control.py (copy its harness exactly: window offscreen, no radio).

Edit or create only:
- tools/tests/test_control_more.py (new)
Do NOT edit any other file, and no implementation file. The implementation does not exist yet, so the new tests
are EXPECTED to fail. Run it as: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
from the worktree root. Follow the existing suites' style: plain script, a check helper, prints "all checks
passed" and exits 0 on success, non-zero on failure (every check runs and is reported, one failure does not hide
the rest).

Rules for good tests:
- One behaviour per check, with a clear failure message. Seeds for randomness, tolerances as the spec states them.
- Build tiny fixtures yourself (temporary folder); never use production or captured data.
- For "must never happen" rules (never writes, never overwrites, refusals) sweep or assert a property.
- Pin only what the spec pins. Leave open what the spec leaves open.
- Do not touch the user's config: set FMRX_CONFIG to a temporary file as test_control.py does. The measure command is phase m2 and is NOT tested here. Test each command's success path and each listed refusal, and the new status keys.

SPEC:
# Phase m1 spec: the measurement core and the first fmctl commands

Goal: `iqmeasure.py` turns an IQ file (or an array) into the card of `CONTRACTS-measure.md` and names the signal's
class; and fmctl gains the commands that touch only `control.py`. Read `CONTRACTS-measure.md` (the interfaces and
class rules), `CLAUDE.md`, `AGENTS.md` and `knowledge/workflow.md` first.

First command of the manager: `swarm.py phase m1 active` (the board follows the phase from that).

## Tasks and file ownership
| task | model(s) | edits only | notes |
|---|---|---|---|
| tests-core | haiku (author); reviewers grokw, musefree + a consensus run | `tools/tests/test_iq_measure.py` (new) | tests first; synthetic signals built in the test with numpy (do not edit `signals.py`, do not import `/data/python/SDR`) |
| tests-fmctl | musefree (author); reviewers grokw, haiku + consensus | `tools/tests/test_control_more.py` (new) | tests first; same harness as `test_control.py` (window offscreen, no radio) |
| core | bake-off: grokw, musefree, haiku | `tools/fm_receiver/iqmeasure.py` (new) | gate: `test_iq_measure.py` |
| fmctl | single author: grokw; reviewers musefree, haiku | `tools/fm_receiver/control.py` | gate: `test_control_more.py` and the old `test_control.py`; if `app.py` needs a helper, report it instead of editing |
| wiring | haiku | `tools/tests/run_all.py` | add the two new test files after `test_control.py`; one reviewer |

Order: tests-core and tests-fmctl in parallel, each reviewed and fixed; then core and fmctl in parallel (different
files); then wiring.

## Behaviour (exact; the tests assert it)
- The interfaces, the card, the class rules and the refusals are exactly as in `CONTRACTS-measure.md`.
- Synthetic signals the tests build (all complex64, 1 MS/s unless stated; SNR as stated):
  CW tone at +50 kHz, 30 dB SNR; OOK 4,000 baud keyed on a 20 kHz carrier, 12 bursts of 40 ms every 100 ms;
  FSK at 9,600 baud, +/-5 kHz, constant envelope; FM, 75 kHz deviation, 1 kHz tone; AM, 80 % depth, 1 kHz tone;
  white noise only; a two-tone signal (+20 kHz, -60 kHz).
- Measured against known values: class equal to the signal's; `bandwidth_hz` within 15 % of the analytic
  occupied bandwidth (CW: under 5 kHz); `symbol_rate_baud` within 5 %; burst `median_duration_s` within 5 % and
  `repeat_interval_s` within 2 %; `tones` find both tones within 2 bins; `power_dbm` equals `power_dbfs` plus
  `full_scale_dbm` within 0.1 dB.
- The file readers: a `.cfile` with its `.sigmf-meta` (rate, centre read back), a bare `.cfile` (refused without
  `rate=`, accepted with), an IQ `.sdvr` written by `tests/signals.write_sdvr`, a `.cdif` by `write_blue`, a sweep
  `.sdvr` and a `.fft` (both refused), a missing file (refused).
- `write_card` writes `<base>.card.json` beside a `.cfile` and refuses the other three; the file read back
  equals the card; an existing non-card file is never overwritten.
- fmctl: each command of the table in the contract behaves and refuses as listed; `status` has the new keys.
  The old `test_control.py` still passes unchanged.

## Acceptance targets (numbers, measured by a script or test)
- `test_iq_measure.py` and `test_control_more.py`: exit 0, "all checks passed".
- The old `test_control.py`, `test_recordings.py`, `test_gui.py`, `test_sceptre_dvr.py`, `test_dvr_iq.py`,
  `test_spectra.py`, `test_sceptre_spectra.py`: exit 0.
- `iqmeasure.measure` on 30 s of 1 MS/s IQ: under 5 s; peak extra memory under 1 GB (the file is read in
  chunks, never whole).

## Gates (pre-registered: fixed before any worker runs)
- G1 `tools/tests/test_iq_measure.py` exits 0 (denominator: its checks, one per signal and per reader case).
- G2 `tools/tests/test_control_more.py` exits 0.
- G3 the seven existing suites listed above exit 0, one by one, from the merged worktree.
- G4 the timing and memory target above, measured by the planner on the merged result.
- G5 `iqmeasure.py` imports none of gnuradio, PyQt5, scipy, `library`, `radios`, `dsp`: checked by a test that
  imports it in a clean subprocess and lists `sys.modules`.
- Bake-off scoring rubric (0 to 100, core only): correctness against the contract and the tests, and bugs a
  reviewer finds that the tests miss, 50; contract and class-rule compliance, including never writing and the
  refusals, 30; clarity and fit with the surrounding code, 20.
- Winner rule: the highest score; a tie goes to the entry with fewer failing review items, then the shorter diff.
- Test review: a test is wrong when two implementations of three disagree with it: suspect the test and take it
  back to the reviewers before changing code.

## Amendments (dated, post-hoc, with direction)
(none)

## Out of scope
- Anything outside the files in "edits only"; reading `/data/python/SDR`, `/data/sceptre` or any path outside the
  worktree; captured recordings (synthetic signals only); pushing; changing a gate.
