You are the CONSENSUS reviewer on the FM receiver. Read-only: do not edit, create or delete files, do not run git
commands that change state, and do not list or read anything outside the working directory. You may run the tests
and short probes.

Independent reviewers from different models reviewed the phase m1 acceptance test tools/tests/test_iq_measure.py (written before the
implementation, which does not exist, so the test failing today is expected). Reviewers sometimes misread the
test or the contract, so VERIFY every claim against the test file and CONTRACTS-measure.md before keeping it.
Quote the file:line you checked. Reject claims that are false, that ask to pin details the spec leaves open, or
that are out of scope. Keep severity honest. The spec is in CONTRACTS-measure.md and the SPEC below.

REVIEW 1:
I'll review the phase m1 measurement tests against the contract and spec, starting with the contract and the diff.verdict: changes
issues:
- [high] tools/tests/test_iq_measure.py:check_classes Contract rules 2–3 (OOK if the low envelope level is under 10% of the high one, ASK if it is at least 10%) have no signal. Add an ASK fixture that must class as `ask` and an OOK just under that cut that must class as `ook`.
- [medium] tools/tests/test_iq_measure.py:check_classes Rule order and the numeric cuts (`snr_db < 6`, envelope CV 0.1, frequency clusters 3 within-level stds apart, FM std ≥ 500 Hz, “frequency steady”) are only sampled on easy cases. Add an OOK with SNR under 6 dB that stays `noise`, plus one signal on each side of the other cuts.
- [medium] tools/tests/test_iq_measure.py:check_shape `evidence` may be `[]`. Require one short sentence for the rule that fired.
- [medium] tools/tests/test_iq_measure.py:check_bandwidth `bandwidth_20db_hz`, `noise_floor_dbfs`, `snr_db`, `envelope.depth`, `frequency.levels_hz` / `deviation_hz`, and burst `count`, `duty`, and `gaps_s` are never checked against known values. Assert the analytic ones (AM depth 0.8, FM deviation 75 kHz, OOK count 12, duty `12*0.04/1.3`, 20 dB width).
- [medium] tools/tests/test_iq_measure.py:waveform_reference OOK and FSK “99% bandwidth” is pinned to a Hann periodogram’s 0.5%–99.5% span, which the contract does not define. Use a window-independent width or a tolerance wide enough for another valid 99% estimator.
- [medium] tools/tests/test_iq_measure.py:694 The default span of a 31 s file must be exactly 30 s ± 1 µs. The contract says at most the first 30 s, and the spec requires chunked reads. Accept any `t1` in `(30 − one chunk, 30]`.
- [medium] tools/tests/test_iq_measure.py:reading_writes_nothing `open_iq` / `measure_file` are called with `rate=1e6` on `sig.cfile`, whose meta says 2 MS/s. The contract does not define a mismatch. Copy the meta file and do not pass `rate=`.
- [medium] tools/tests/test_iq_measure.py:347 “At least 3 bins apart” and “> 15 dB above the floor” are not enforced (the gap floor is `rate/2**20`, and nine strong tones may return any count from 1 to 8). Add a pair closer than 3 bins and a tone at 14 dB and assert both are omitted.
- [medium] tools/tests/test_iq_measure.py:133 “Within 2 bins” is hardcoded as `2 * rate / 1024`, so a coarser correct FFT fails. Compare to the implementation’s own bin or to a fixed Hz tolerance.
- [medium] tools/tests/test_iq_measure.py:631 A `.cdif` centre is never required to be `RF_FREQ` (the tone check uses whatever centre was reported) and `power_dbm` is not required to be `None`. Assert `center_hz == 100e6` and `power_dbm is None`.
- [medium] tools/tests/test_iq_measure.py:521 `band` only checks which tones remain. Brick-wall restriction and decimation to at least twice the band width are untested. Assert occupied bandwidth (and the tone grid) after the band `(centre+5 kHz, centre+200 kHz)`.
- [low] tools/tests/test_iq_measure.py:check_symbols_and_bursts `symbol_rate_baud` is not required to be `None` for CW, FM, AM, and noise. Assert `None`.
- [low] tools/tests/test_iq_measure.py:check_readers `source.path`, `IQSource.seconds` on `.sdvr` and `.cdif`, and `read()` at a middle offset are never checked. Assert them.
- [low] tools/tests/test_iq_measure.py:check_readers Sweep `.sdvr`, `.fft`, missing, and short files are refused on `open_iq` only. Refuse them from `measure_file` too.
- [low] tools/tests/test_iq_measure.py:809 The import probe only calls `measure` on an array, so a banned import inside `open_iq` still passes. Open a temporary `.cfile` in that subprocess before listing `sys.modules`.
REVIEW 2:
verdict: changes
issues:
- [high] tools/tests/test_iq_measure.py:469 no ASK signal or class test, contract ook/ask threshold untested; add 2-level envelope ASK vector expecting ask
- [high] tools/tests/test_iq_measure.py:567 t0=2.0 on 1s array pins unspecified out-of-range behavior with zero samples; use in-range t0 or define clipping
- [high] tools/tests/test_iq_measure.py:828 G5 checks top-level modules only so fm_receiver.library/radios/dsp slip through; match substrings/full names
- [medium] tools/tests/test_iq_measure.py:530 envelope levels/depth/keyed and frequency levels_hz/deviation_hz/keyed never asserted functionally; assert OOK keyed, AM depth 0.8, FSK +-5kHz, FM 75kHz
- [medium] tools/tests/test_iq_measure.py:477 bandwidth_20db_hz never asserted numerically; add CW/AM/FM 20dB-width checks
- [medium] tools/tests/test_iq_measure.py:545 snr_db/noise_floor_dbfs/power_dbfs absolute accuracy never checked; assert ~30dB and ~0dB cases
- [medium] tools/tests/test_iq_measure.py:535 bursts count/duty/gaps/durations-truncation never checked; assert count 12, gap 60ms, duty ~0.4
- [medium] tools/tests/test_iq_measure.py:531 symbol_rate_baud None cases for CW/FM/AM/noise never checked; assert None
- [medium] tools/tests/test_iq_measure.py:326 evidence content per fired rule never checked; assert one sentence per rule
- [medium] tools/tests/test_iq_measure.py:347 tone 3-bin separation uses fixed RATE/2^20, pinning NFFT the contract leaves open; compare against implementation bin or drop
- [medium] tools/tests/test_iq_measure.py:354 requires repeat_interval set whenever count>=3, contract only requires None under 3; allow None for aperiodic bursts
- [medium] tools/tests/test_iq_measure.py:738 write_card non-card-overwrite tests swallow exception and only check bytes; require IQSourceError
- [medium] tools/tests/test_iq_measure.py:642 short-file refusal tested only for open_iq, not measure_file; add measure_file on <1024-sample file
- [medium] tools/tests/test_iq_measure.py:611 bare .cfile accepted check ignores rate/centre values and default centre; assert rate and centre read back
- [low] tools/tests/test_iq_measure.py:517 nine-tone check passes with 1 tone listed; require 8 with correct freqs
- [low] tools/tests/test_iq_measure.py:701 card_path None never tested for unknown extension; assert None for .bin
- [low] tools/tests/test_iq_measure.py:521 band decimation to >=2x width and measure_file band param never tested; assert span/decimation behavior

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
