You are the CONSENSUS reviewer on the FM receiver. Read-only: do not edit, create or delete files, do not run git
commands that change state, and do not list or read anything outside the working directory. You may run the tests
and short probes.

Independent reviewers from different models reviewed the phase m1 acceptance test tools/tests/test_control_more.py (written before the
implementation, which does not exist, so the test failing today is expected). Reviewers sometimes misread the
test or the contract, so VERIFY every claim against the test file and CONTRACTS-measure.md before keeping it.
Quote the file:line you checked. Reject claims that are false, that ask to pin details the spec leaves open, or
that are out of scope. Keep severity honest. The spec is in CONTRACTS-measure.md and the SPEC below.

REVIEW 1:
I'll review the new fmctl tests against the contract and the phase m1 spec, starting with the diff and the contract.verdict: changes
issues:
- [high] tools/tests/test_control_more.py:t_delete_needs_confirm The recordings folder has no `fm-…` recording when this runs, so `n > 0` cannot pass. Write an app wav into the recordings folder before `delete 0`.
- [medium] tools/tests/test_control_more.py:t_status_file_none The contract only says `file` is the loaded file or None. After `radio hackrf`, `cfg['iq_file']` is still set. Treat None as “no path chosen”, and allow the opened path while another radio is selected.
- [medium] tools/tests/test_control_more.py:t_marker_live The reply shape is open, but the test requires a dict, a `unit` key, and every `freq*` number within 0.2 of MHz, so a `freq_hz` value in Hz fails. Accept Hz or MHz explicitly, and a numeric level in the view’s unit.
- [medium] tools/tests/test_control_more.py:t_marker_live The level is never compared to the live RF trace, and `t_marker_peak_hold` never checks the peak-hold trace, so a constant level passes. Assert the reply against `rf_view`’s live bins and its peak-hold bins.
- [medium] tools/tests/test_control_more.py:t_recordings_folder_moves The test calls `w._refresh_library()` itself, so “the list is re-read” is not enforced. Drop that call and read the new folder through `recordings`.
- [medium] tools/tests/test_control_more.py:measure The contract row `measure` (reply the card, `card` writes it, refuse when there is no capture) has no test. Add the no-capture refusal here.
- [low] tools/tests/test_control_more.py:t_filter_refused_outside_receive “Outside Receive” is only checked on the Recordings tab. Also refuse `filter` in Sweep.
- [low] tools/tests/test_control_more.py:t_filter_sets `numbers_in` rejects a reply whose value is the knob label `"200 kHz"` (same for `step`). Accept that string as the value set.
- [low] tools/tests/test_control_more.py:t_filter_clamps_high The clamp cases never check that the reply is the clamped value (`r is not None` only). Assert the reply is the knob’s clamped kHz.
REVIEW 2:
verdict: changes
issues:
- [high] tools/tests/test_control_more.py:367-375 (t_open_refused_unreadable) - A zero-filled bare .cfile with no .sigmf-meta is not "unreadable" by the spec, and the spec does not say how `open` treats a bare .cfile, so the test pins a refusal the spec does not require. Fix: use content the reader must reject (a garbage .sdvr header, or a directory named *.cfile) and drop the zero-data case.
- [high] tools/tests/test_control_more.py:398-400 (t_marker_live) - `level_keys` matches any key containing "level", so a reply with `level_unit` (the name `status` already uses) makes `float('dBFS')` raise, and a correct implementation fails. Fix: check only numeric values, excluding the unit key.
- [high] tools/tests/test_control_more.py:362-363, 421-422, 455, 486-487 (open missing, marker off band, recordings-folder, delete Sceptre) - The word lists ("no ", "not", "could", "delet", "left") match almost any error text, so these refusals are effectively unchecked. Fix: require the specific cause (the path's name, "recording", "confirm", "Sceptre"), or check that the reply is not a generic error.
- [medium] tools/tests/test_control_more.py:439-441 (t_recordings_folder_moves) - The test calls `w._refresh_library()` itself before checking the list, so it passes even if `recordings-folder` never re-reads the list, which the spec requires. Fix: drop the manual refresh and wait with `pump(until=...)` for the new file to appear.
- [medium] tools/tests/test_control_more.py:390-402 and 407-412 (t_marker_live, t_marker_peak_hold) - The level is only checked to be finite; nothing ties it to the trace. Fix: compare it with the RF view's dB at the nearest bin (live or `_peak`) within a few dB, in the view's unit.
- [medium] tools/tests/test_control_more.py:496-507 (t_delete_removes_own and t_delete_refused_external) - Spec gaps: no test for `delete` with an out-of-range N, or for a non-Sceptre external file (only a Sceptre one is tested). Fix: add both.
- [medium] tools/tests/test_control_more.py:362-375 and 450-456 (open, recordings-folder) - Spec gap: no test that `recordings-folder` refuses a path that is a file rather than a directory (only a nonexistent path is tested). Fix: add this case.
- [medium] tools/tests/test_control_more.py:271-283 (t_step_sets, t_step_refused_unknown) - Spec gap: only 50 and 100 are set, so 10 and 200 are never checked. Fix: loop over `fmapp.STEPS_KHZ` and check each sets `_step_hz()`.
- [low] tools/tests/test_control_more.py:249, 353, 434, 503 (`assert r is not None`) - These check nothing about the reply the spec describes (the filter value set, the file opened, the folder). Fix: assert the reply's content.
- [low] tools/tests/test_control_more.py:316 (t_stop_starts) - Asserts the Run button's label is 'Start', which the spec does not fix. Fix: check `w.engine.running` only.
- [low] tools/tests/test_control_more.py:263, 329, 339, 383 - Pins that the error text contains 'receive' or 'recording', which the contract does not fix. Fix: drop the wording requirement or make it optional.
- [low] tools/tests/test_control_more.py:482 (t_delete_refused_external) - Matches the listed kind text ('sceptre' or 'dvr'), which is display wording. Fix: match on the listed path or the file name instead.
- [low] tools/tests/test_control_more.py:117-150, 233-236, 259-267, 357-365 - Fixed `pump(1.0)`/`pump(0.5)` sleeps and single-point "never" checks (refused filter, open, delete each tested once, in one state) are timing-sensitive and sparse. Fix: wait with `pump(until=...)` on `w.engine.running` or `w.radio.kind`, and check the state after each refusal.
Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
fix list (for the author):
- [high|medium|low] <file:line> <one line: what to change> (source: 1, 2 or 1+2)
rejected:
- <one line: the claim and why it's wrong> (source)

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
