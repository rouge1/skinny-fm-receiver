Review feedback on your tests-fmctl. Independent reviewers from other models reviewed it, and a consensus run verified each
claim against your test. Apply the fix list below. Edit only tools/tests/test_control_more.py. Do not list or read anything outside the working
directory. No implementation file exists; the test is still expected to fail against the missing implementation.
Keep to what the spec and CONTRACTS-measure.md pin; where the fix list says to accept alternatives, accept them.

FIX LIST:
- [high] tools/tests/test_control_more.py:398-400 (t_marker_live) - `level_keys` matches `level_unit`, so `float('dBFS')` raises; check only numeric values and skip the unit key (source: 2)
- [medium] tools/tests/test_control_more.py:390-402 and 407-412 (t_marker_live, t_marker_peak_hold) - compare the reply level with the `rf_view` live bins and `_peak` bins at the nearest frequency, in the view's unit; currently only finiteness or unit is checked (source: 1+2)
- [medium] tools/tests/test_control_more.py:393-397 (t_marker_live) - the reply shape is open in the contract; accept the frequency in MHz or Hz and do not require a dict or a `unit` key (source: 1)
- [medium] tools/tests/test_control_more.py:439 (t_recordings_folder_moves) - drop `w._refresh_library()` and wait with `pump(until=...)` for the new file to appear in the list (source: 1+2)
- [medium] tools/tests/test_control_more.py:367-375 (t_open_refused_unreadable) - the spec does not say how `open` treats a bare zero-filled `.cfile`; use a garbage `.sdvr` header or a directory named `*.cfile` instead (source: 2)
- [medium] tools/tests/test_control_more.py:362-363, 421-422, 455, 486-487 - the word lists match almost any error text; require the specific cause, or at least that the reply is not a generic error (source: 2)
- [medium] tools/tests/test_control_more.py:450-456 (t_recordings_folder_refused) - add a case where the path is an existing file, not a directory (source: 2)
- [medium] tools/tests/test_control_more.py:468-488 (t_delete_refused_external) - add a non-Sceptre external file (e.g. a wav in an added folder) and check it is refused (source: 2)
- [low] tools/tests/test_control_more.py:259-267 (t_filter_refused_outside_receive) - also refuse `filter` in Sweep mode (source: 1)
- [low] tools/tests/test_control_more.py:240-242 (t_filter_sets) - accept the knob label string such as "200 kHz" as the value set; `numbers_in` ignores strings (source: 1)
- [low] tools/tests/test_control_more.py:223 (t_status_file_none) - the spec says `file` is "the file loaded or None"; while hackrf is selected, accept None or the opened path rather than only None (source: 1)
- [low] tools/tests/test_control_more.py:249, 353, 434, 503 - replace `assert r is not None` with checks of the reply content (clamped kHz, opened file, folder, deleted item) (source: 1+2)
- [low] tools/tests/test_control_more.py:271-283 (t_step_sets, t_step_refused_unknown) - loop over `fmapp.STEPS_KHZ` and check each sets `_step_hz()` (source: 2)
- [low] tools/tests/test_control_more.py:316 (t_stop_starts) - drop the `'Start'` label check; check `w.engine.running` only (source: 2)
- [low] tools/tests/test_control_more.py:263, 329, 339, 383 - drop or make optional the 'receive'/'recording' wording, which the contract does not fix (source: 2)
- [low] tools/tests/test_control_more.py:481-483 (t_delete_refused_external) - select the Sceptre row by its path or file name, not by the display text in `kinds` (source: 2)
- [low] tools/tests/test_control_more.py:174, 178, 218, 230, 298, 302, 347, 355 - replace fixed `pump(seconds)` sleeps with `pump(until=...)` on `w.engine.running` or `w.radio.kind`, and check state after each refusal (source: 2)

Done when the file still runs end to end (QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py), failing only for the missing implementation, with no error in the test code itself.
