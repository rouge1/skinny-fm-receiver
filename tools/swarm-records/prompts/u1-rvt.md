You are a REVIEWER on the FM receiver. Do not edit, create or delete any file. Do not run git commands that change state. Do not list or read anything outside the working directory. You may run the test suite and short probes: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_narrow_files.py`.

Another model wrote the phase u1 acceptance tests, in tools/tests/test_narrow_files.py (new file), from the spec below. The implementation does NOT exist yet, so the new tests fail today (14 of 38 pass). Do not report that. Judge the TESTS. Read CLAUDE.md, tools/tests/test_narrow_files.py, and tools/fm_receiver/radios.py, app.py, widgets.py, control.py where needed to see whether each assertion is reachable and consistent.

Review for:
1. Spec coverage: every spec rule with no test.
2. Wrong or over-specified tests: asserts that contradict the spec or pin details the spec leaves open (for example tuner_range_mhz ordering, exact widths).
3. Weak tests: "must never happen" rules checked at a few points; timing-dependent checks; float equality; tests that would pass on a wrong implementation (for example a check that passes if the status key is merely present).
4. Passability: could a correct implementation pass them all? Any impossible or self-contradictory.
5. Do the tests restore state, or leak a config to the user's file (FMRX_CONFIG must be set)?

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:test or file:line> <one line: what is wrong and the fix>

SPEC:
# Phase u1 spec: narrow IQ files and spectrum recordings in the Recordings tab

Goal: the user's four bug reports from using the Recordings tab, reproduced by the planner on a real 54.7 kS/s
Sceptre BLUE IQ file (a 100.1015 MHz CW tone, 4.4 s) in the offscreen window:
1. "On Sceptre BLUE IQ the tuner is trapped on the left side of the RF spectrum chart." `fmctl status` shows
   `tuner_mhz` 5944.3 (!) with `tuner_range_mhz` [0, 6000], and the orange channel band at the chart's left.
   Cause found: `radios.Radio.max_offset(rate)` is `rate * usable_fraction / 2 - EDGE_MARGIN_HZ` (150 kHz): for
   any file under about 300 kS/s that is negative, clamped to 0, so the tuner's range has no width.
2. "I can't change the span" and 3. "it goes from 50 kHz to 57 kHz": the RF view is built with
   `min_span_hz=50e3` (`app.py`, the RF spectrum), and `span_knob.set_range(min(min_span, band), band)` leaves
   [50 kHz, 54.7 kHz] for a 54.7 kHz file: no real zoom.
4. "On Sceptre spectrum (the `.fft` and sweep DVR) I can't right-click and drop a tuner or move the tuner": for a
   spectrum track there is no receiver, so `tune()` has nothing to move; the menu's "Tuner to" does nothing
   visible. What the user wants is the marker: a vertical line they can drop at a frequency and drag, with the
   frequency and the level read out. (`rf_view.set_marker` and fmctl `marker FREQ` already exist.)
Read `CLAUDE.md`, `AGENTS.md` and `tools/tests/test_recordings.py`, `test_sceptre_spectra.py` for how the
window is driven offscreen.

First command of the manager: `swarm.py phase u1 active`. Register every task WITH its model
(`swarm.py task u1 <task> queued --model <m>`); never without `--model`.

## Tasks and file ownership
| task | model(s) | edits only | notes |
|---|---|---|---|
| tests | haiku; reviewers grokw, musefree | `tools/tests/test_narrow_files.py` (new) | tests first; synthetic files from `tests/signals.py` (`write_blue` at 54,687.5 S/s and at 250 kS/s, `write_sdvr`, spectrum helpers from `test_sceptre_spectra.py`) |
| narrow | single author: grokw; reviewers haiku, musefree | `tools/fm_receiver/radios.py`, `tools/fm_receiver/app.py`, `tools/fm_receiver/widgets.py` | bugs 1 to 3; after tests are reviewed |
| marker | single author: haiku; reviewers grokw, musefree | `tools/fm_receiver/app.py`, `tools/fm_receiver/widgets.py`, `tools/fm_receiver/control.py` | bug 4; after `narrow` is merged (same files) |
| wiring+docs | haiku; reviewer grokw | `tools/tests/run_all.py`, `knowledge/usage.md`, `knowledge/capabilities.md` | the new suite; the marker and the span of a narrow file in the user guide |

## Behaviour (what the tests assert)
- A file of rate R has a tuner range of the LO +/- r with r = max(0, R/2 * usable - margin) and
  margin = min(150 kHz, R / 8): for R >= 1.2 MS/s nothing changes (FM files and every live radio keep
  their numbers: the existing suites prove it); a 54.7 kS/s file has r about 20 kHz, so the tuner is placed,
  dragged and typed anywhere in it and is never pinned: `tune 100.1015`, `tune 100.09`, `tune 100.11` give
  distinct `tuner_mhz` values inside the file's band; `tuner_mhz` is never outside the file's own band.
- The channel band drawn and `status.channel_filter_khz` never exceed the file's band: the filter is shown
  at min(filter, band) for a narrow file. (The app's saved filter value is left alone.)
- The RF view of a recording of band B (Hz): span knob range [max(500 Hz, B / 200), B] (50 kHz stays the
  minimum for live sweeps and for bands over 10 MHz). `view rf span 5` (kHz) on the 54.7 kHz file gives
  `span` 0.005 MHz and `shown` a 5 kHz window around the tuner; `view rf full` returns 54.7 kHz.
- The marker: for a spectrum track (`.fft`, sweep `.sdvr`) the RF view's right-click menu reads
  "Marker to <f> MHz" (for IQ tracks it keeps "Tuner to <f>"), and choosing it places the marker there;
  a left click also places it; dragging the marker moves it; the readout shows the frequency and the level
  at that bin in the view's unit (dBm for a calibrated file). `fmctl marker FREQ` also moves it in a spectrum
  recording and replies the level (as now); `status` gains `marker_mhz` (None when not placed).
- Everything else in the Recordings tab behaves as before: the existing suites pass unchanged.

## Acceptance targets
- `test_narrow_files.py` exits 0; the existing `test_recordings.py`, `test_sceptre_spectra.py`,
  `test_sceptre_library.py`, `test_gui.py`, `test_control.py`, `test_control_more.py`, `test_tuning.py` exit 0.
- Planner, with the real files (not delegated): the 54.7 kS/s `.cdif`: tuner inside the chart and movable,
  span 0.5 to 54.7 kHz, a screenshot read by eye; a real `.fft`: right-click menu text and a marker placed and
  dragged, screenshot read by eye.

## Gates (pre-registered)
- G1 `test_narrow_files.py` exits 0 with every case present and none skipped; the new cases fail on the
  pre-fix code (the manager shows both runs).
- G2 the seven suites above exit 0; G3 `run_all.py` passes.
- G4 the planner's real-file check, above.
- One author per task, two reviewers; any [high] is probe-verified before merge. A Qt paint override is
  guarded (CLAUDE.md); a layout change is animated (the user's standing rule); colours come from
  `theme.TOKENS`.

## Amendments (dated, post-hoc, with direction)
(none)

