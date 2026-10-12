You are the CONSENSUS reviewer on FM Receiver (PyQt5 + numpy app). Read-only: do not edit, create or delete files,
do not run git commands that change state, and do not list or read anything outside the working directory. You may run
short probes (python: /home/user/miniconda3/envs/gnu/bin/python, PYTHONPATH=tools, QT_QPA_PLATFORM=offscreen);
do not run the whole GUI suites.

Two models implemented playing spectrum recordings (app.py) in a bake-off. Entries are on branches
p3/playback-grokw and p3/playback-musefree; inspect each with `git diff main p3/playback-<m> -- tools/fm_receiver`.
Both pass tools/tests/test_sceptre_spectra.py and the manager has run the other suites. Spec: CONTRACTS.md (app.py
section) and tools/swarm-records/specs/p3.md. VERIFY every claim against the code, quote file:line.
Score and pick the winner ONLY by the spec's pre-registered rubric and winner rule:
- Bake-off scoring rubric (0 to 100): correctness against the spec and the tests, and bugs a reviewer finds that
  the tests miss, 50; spec compliance including the only-what-it-needs reading and no writes, 30; clarity and fit
  with the surrounding code, 20.
- Winner rule: the highest score; a tie goes to the entry with fewer failing review items, then the shorter diff.
Do not add your own criteria.

REVIEWS (heading: entry reviewed "by" reviewer):
=== C-grokw-by-haiku
verdict: changes
issues:
- [high] tools/fm_receiver/app.py:3587 `_spectrum_line` calls `library.render` on the UI thread every time the sweep changes, which is up to 25 times a second. For a sweep DVR that reads the whole ~19 MB tile each time (spectra.py `_read_dvr`), so the timer blocks the UI. Render the sweep on `self._pool`, or keep the decoded tile in memory and reuse it.
- [medium] tools/fm_receiver/app.py:3582 The cache key `int(seconds / rs)` is the floor, but `render` picks the nearest sweep (`round`). A bucket keeps whichever sweep was drawn first, so the trace and waterfall can show a sweep up to half a row off. Use `k = int(seconds / rs + 0.5)`.
- [medium] tools/fm_receiver/app.py:3560 `_tick_spectrum` swallows a read error (a file that became unreadable) and keeps ticking at 25 Hz with no message, so the playhead runs on with nothing drawn. On error, stop `_spec_timer`, clear `_play`, and set a status.
- [medium] tools/fm_receiver/app.py:4264 `closeEvent` does not stop `self._spec_timer`, so it keeps ticking after close. Add `self._spec_timer.stop()` next to the other timer stops.
- [low] tools/fm_receiver/app.py:3568 `_draw_spectrum` returns early while paused, so a seek while paused (`_seek_to`, line 3485) updates the waterfall but not the trace, which stays on the old sweep until Play. Draw the sweep at the new position from `_seek_to` when paused.
- [low] tools/fm_receiver/app.py:3503 Play at the end (no Loop) resumes, then stops on the same tick, so pressing Play does nothing. Restart from 0 when `p.at >= track.seconds` on resume.
- [low] tools/fm_receiver/app.py:3549 `_tick_spectrum` and `_tick_fast` (line 3816) both call `_follow_playback` on the playhead. This is harmless because it is idempotent, but it is a second driver for the same playhead; keep one.
=== C-grokw-by-musefree
verdict: changes
issues:
- [medium] tools/fm_receiver/app.py:3336 _open_spectrum does no I/O so a deleted/corrupt file or error track starts "Playing" with an advancing clock and no data; probe one row (library.render) at open so _play_toggled reports "Could not play" like IQ does.
- [medium] tools/fm_receiver/app.py:3402 _resume from end (p.at>=total, loop off) instantly re-stops so pressing Play at the end only flashes Pause; seek to 0 first as _play_ended does for IQ/WAV.
- [low] tools/fm_receiver/app.py:4263 closeEvent stops fast/slow timers but not _spec_timer; stop it there.
- [low] tools/fm_receiver/app.py:3447 _playhead returns _shown_at when paused while _play_pause advanced p.at up to a tick ahead, causing a forward jump on resume, and it is unclamped past total; return p.at when paused for spectrum and clamp to [0,total].
- [low] tools/fm_receiver/app.py:3576 _spectrum_line issues a 1-row library.render per sweep step which decodes the whole DVR tile each time (hundreds of redundant tile decodes, on the UI thread); cache the tile block and slice rows from it.

=== C-musefree-by-haiku
verdict: changes
issues:
- [medium] tools/fm_receiver/app.py:3538-3542 `_play_ended` calls `_seek_to(0.0)`, so a spectrum track's playhead jumps back to 0 at the end instead of staying on the end as CONTRACTS.md requires. Fix: for a spectrum track, keep the playhead at `track.seconds` and only return to 0 when Play is pressed again at the end.
- [medium] tools/fm_receiver/app.py:3574 `_draw_spectrum` reads with `max_cols=view.WF_COLS` (4096), but `library.render` (window stretch) pools only above `library.SPECTRUM_MAX_COLS` (16384). For sweeps of 4097 to 16384 bins the frequency extents differ, so `_same_extent` fails on the first live row and `_add_row` resets `_hist`. The stretch just drawn on Play, and after each seek while playing, is wiped. I confirmed the extent mismatch with a synthetic 8000-bin file. Fix: read with `max_cols=library.SPECTRUM_MAX_COLS`.
- [low] tools/fm_receiver/app.py:3370-3387 `_open_spectrum` does not call `view.set_pan_limits(None, None)` as `_run_receive` does. The Sweep tab's 0–6 GHz pan limits (app.py:2271) can then clamp a spectrum file outside that band. Fix: add the call.
- [low] tools/fm_receiver/app.py:3193-3198 `_prepare_view` requests the first stretch, but `_open_spectrum`→`_follow_clock` (app.py:3394) overrides it with a stretch at 0, so the waterfall is blank at Play despite the comment saying the first stretch is drawn.

Rules: merge duplicates. Reject false claims, claims that pin details the contract leaves open, or out of scope.
Keep severity honest.

Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
scores:
- <model>: <0-100> - <one line why>
winner: <model>
fix list per entry (for each author):
<model>:
- [high|medium|low] <file:line> <what to change> (source: reviewer names)
rejected:
- <one line: the claim and why it's wrong> (source)
