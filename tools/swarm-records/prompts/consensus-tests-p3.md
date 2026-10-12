You are the CONSENSUS reviewer on FM Receiver (PyQt5 + numpy app). Read-only: do not edit, create or delete files,
do not run git commands that change state, and do not list or read anything outside the working directory. You may
run short probes (python: /home/user/miniconda3/envs/gnu/bin/python, PYTHONPATH=tools, QT_QPA_PLATFORM=offscreen).

Two independent reviewers (A: musefree, B: haiku) reviewed the Phase 3 acceptance tests tools/tests/test_spectra.py
and tools/tests/test_sceptre_spectra.py against CONTRACTS.md and tools/swarm-records/specs/p3.md. The
implementation does not exist yet (the tests fail today: not an issue). Reviewers sometimes misread, so VERIFY every
claim against the code (tools/tests/signals.py, tools/fm_receiver/*.py, the tests). Quote the file:line you checked.
In particular, for the render "nearest sweep" tie claim: compute, from the contract's rule (row r is the sweep
nearest the middle of slot r of [t0,t1); sweep k is at k*row_seconds), whether the test's render calls with
rows == number of sweeps are ties, and what choice the test's allclose asserts (for the .fft, row r = -100 + r).
Also state which tie-break the test requires (earlier or later sweep).

REVIEWS:
=== A (musefree)
verdict: changes
issues:
- [high] tools/tests/test_sceptre_spectra.py:107 rows==sweeps ties middle (r+0.5) so nearest is ambiguous; fix test to avoid ties or pin floor in contract
- [high] tools/tests/test_sceptre_spectra.py:pieces no dual-track DVR (IQ plus wide tiles in one Recording per 1.5x rule); add such file and assert group Sceptre IQ DVR
- [high] tools/tests/test_sceptre_spectra.py:gui no fmctl status/playback check required by CONTRACTS.md app; add fmctl assertions for spectrum
- [medium] tools/tests/test_sceptre_spectra.py:97 started tolerance 1yr lets mtime fallback pass; tighten to hours and add bad-header fallback case
- [medium] tools/tests/test_spectre_spectra.py:pieces never checks rate None, center_hz middle, layout object required by contract; assert them
- [medium] tools/tests/test_spectre_spectra.py:101 extent only for dvr-sweep, never fft; assert extent for fft equals start/stop_hz
- [medium] tools/tests/test_spectra.py:107 spy only checks width stop-first<=2 not position; assert touched tile indices
- [medium] CONTRACTS.md:render pooling by max contradicts app map job pool mean; clarify spectrum always-max or honor pool arg
- [low] tools/tests/test_spectra.py:151 refused misses .fft with CF or 1-D SF and junk .sdvr; add wrong-format-with-right-extension cases
- [low] tools/tests/test_sceptre_spectra.py:gui never checks audio box idle, playhead on end after Loop-off stop, or leaving-tab stop; assert them
=== B (haiku)
verdict: changes
issues:
- [high] tools/tests/test_sceptre_spectra.py:107-109 and :118-119 — `render` with rows equal to the sweep count puts each slot's middle exactly between two sweeps, so "nearest" is a tie and the exact `allclose` pins one tie-break. It also fails at tile boundaries (rows 15/16 have different gains). Fix: render half as many rows (slot middle = sweep 2r+1 exactly) and compare to `whole[1::2]` and `data[1::2]`, or accept either neighbour.
- [medium] tools/tests/test_sceptre_spectra.py:97 — the sweep start tolerance is one year (3600*24*365), so it checks nothing. The header time is exact (1790700000.25), so use under 1 s.
- [medium] tools/tests/test_sceptre_spectra.py:212-215 — "choosing another recording stops it" is never exercised: playback is paused before `pick(w, 'fft')`. Add a pick while playing and assert `_play` is None and the engine is closed.
- [medium] tools/tests/test_sceptre_spectra.py:gui (no check) — spec's `fmctl status` / `playback` reporting for a spectrum track is untested. Add a control status check while playing.
- [medium] tools/tests/test_spectra.py:164-179 — a BLUE `.fft` that is not 2-D float32 (`fmt='CF'`, or SF with subsize 0) must raise `SpectraError`, per CONTRACTS. Only `.cdif` by extension is tested, so this is uncovered.
- [medium] tools/tests/test_sceptre_spectra.py:86-93 — no test that an IQ DVR whose tiles span more than 1.5× the IQ rate gives both tracks in one Recording under 'Sceptre IQ DVR'. Also no test of the bad-header fallback to mtime. Both are spec rules.
- [low] tools/tests/test_sceptre_spectra.py:184-185 — the 0.6 lower bound over a 1 s wall-clock pump under offscreen load could flake; widen it to about 0.5.
- [low] tools/tests/test_sceptre_spectra.py:218 — `_playhead() > 0.3` after picking the .fft passes if a stale position carried over; assert the playhead is near 0 before Play.
- [low] tools/tests/test_sceptre_spectra.py:29 — `FOLDER` is never removed (about 40 MB station plus the rest), so each run leaves files in /tmp. Clean up at the end.
- [low] tools/tests/test_spectra.py:18 — `import signals` differs from `from tests import signals` used by the other suites; use the same form.
- [low] tools/tests/test_sceptre_spectra.py:pieces — "listing reads headers only" (CONTRACTS) has no check. Add a spy that no `Sweeps.read` or IQ read happens during `scan_external`.
Rules: merge duplicates. Reject false claims, claims that pin details the contract leaves open, or out of scope.
Keep severity honest. Separate "the test is wrong or impossible" from "a rule has no test" (coverage gap).

Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
tie-break required by the tests: earlier | later | either
fix list (for the test author):
- [high|medium|low] <file:line> <what to change> (source: A, B or A+B)
rejected:
- <one line: the claim and why it's wrong> (source)
