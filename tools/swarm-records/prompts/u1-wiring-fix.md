A reviewer found these in your docs diff (verified by me for the first). Apply them, editing only tools/tests/run_all.py, knowledge/usage.md, knowledge/capabilities.md, minimal lines. Do not commit. Do not list or read anything outside the working directory.
I'll review the last commit's added claims against the radio, span, marker, and test code, without changing any files.verdict: changes
issues:
- [high] knowledge/usage.md:778 The ↔ readout under the tuner is filled only in Receive (`_show_range`); in Recordings it is forced to "-". Say the tuner stops at ±17 kHz from centre, and do not claim that line shows the limits on a narrow file.
- [medium] knowledge/capabilities.md:248 `marker` is not read-only: on a spectrum recording `cmd_marker` calls `place_marker` and returns the level. Drop "read-only" and say it places the marker there.
- [medium] knowledge/capabilities.md:204 Part 1 never left-clicks the spectrum. It only sends `tune` and middle-drags the channel band. Remove "clicked" from what the synthetic test proves.
- [low] tools/tests/run_all.py:1 The header still says fifteen radio-free tests; `TESTS` now has twenty, including `test_narrow_files.py`. Update the count.Also: in knowledge/capabilities.md, any older line (search "marker") that still calls `fmctl marker` read-only must be corrected the same way; mark nothing as clicked in part 1.
