Task: list Sceptre spectrum recordings (a sweep DVR, a spectrum .fft) in the Recordings library and draw them with
library.render. This is part 1 of tools/tests/test_sceptre_spectra.py (function `pieces`).

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS.md (section "tools/fm_receiver/library.py" and the spectra.py section), CLAUDE.md,
tools/fm_receiver/spectra.py (merged and done: open_spectra, SpectrumFile.read; do not edit it),
tools/fm_receiver/library.py (how scan_external, EXTERNAL_KINDS, Track, Recording, GROUPS, kinds_text, extent,
render, delete, and the header cache work for the IQ files), tools/fm_receiver/sceptre_dvr.py (read only), and the
acceptance test tools/tests/test_sceptre_spectra.py (the `pieces` function; part 2, `gui`, belongs to a later task
and needs app.py, not you). The tests are the spec: do not edit any test.

Edit only:
- tools/fm_receiver/library.py

Decisions (an amendment to the spec, 2026-10-12): for `render` of a spectrum track, row r is the sweep nearest
the middle of slot r of [t0, t1); when the middle falls exactly between two sweeps (rows == sweeps) the EARLIER
sweep is taken (k = floor(mid / row_seconds + 1e-9)... that is: a tie goes to the earlier sweep; away from a tie
it is the nearest). A row whose sweep falls outside [0, seconds) is NaN. Reading uses spectra.SpectrumFile.read,
which already reads only the tiles/rows a stretch touches: ask it for just the rows and band needed.

Constraints: listing reads headers only (open_spectra on a big ring must stay cheap; cache like the IQ headers do,
and do not read tiles to list); the files are never written or deleted (`delete` refuses, as for the other Sceptre
files); IQ files, IQ DVRs and .cdif behave exactly as before (test_sceptre_library.py, test_recordings.py must keep
passing); an IQ DVR that has IQ and tiles of its own (tiles' span bins*bin_hz not more than 1.5 times the IQ rate)
stays one IQ track and gets no 'dvr-sweep' track. Vectorise with numpy. Match the surrounding code.

Done when these pass (each by itself; the second prints "part 1 passed" then needs app.py for part 2, so check
only that part 1 passed):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_library.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_spectra.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_recordings.py
End with a short summary: files changed, what you did, the test result, anything you could not do.
