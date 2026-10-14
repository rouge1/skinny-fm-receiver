You are a REVIEWER. Do not edit, create or delete any file. Do not run git commands that change state. Do not list or read anything outside the working directory.
Another model changed tools/tests/run_all.py, knowledge/usage.md, knowledge/capabilities.md (see `git diff HEAD~1`). Check every factual claim in the added text against the code and tests (tools/fm_receiver/radios.py `max_offset`, IQFile.freq_range_hz, app.py and widgets.py span floor and marker mode, control.py `marker`/`status`, tools/tests/test_narrow_files.py): numbers (the +/- kHz range at 54.7 kS/s, the span floor, the 10 MHz rule), menu texts, command names and replies, claims about "the line on the tuner", status marks that claim more than synthetic offscreen tests prove. Check run_all.py lists the new suite correctly and that the other edits are minimal (no reflow). Reply exactly:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
