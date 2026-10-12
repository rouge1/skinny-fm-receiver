Task: implement the spectrum reader, the new module tools/fm_receiver/spectra.py.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS.md (section "tools/fm_receiver/spectra.py"), CLAUDE.md, tools/fm_receiver/sceptre_dvr.py (its scan,
scan_sweeps and Sweeps class: read it to learn how a sweep DVR's tiles are located, how Sweeps.read(dbm=True)
decodes them, and how the newest unbroken stretch is found), tools/fm_receiver/sceptre_blue.py (the BLUE header
reader), and the acceptance test tools/tests/test_spectra.py. The test is the spec: do not edit any test.

Edit only:
- tools/fm_receiver/spectra.py: new file (SpectraError, SpectrumFile, open_spectra).

Constraints: numpy only (no Qt). Use sceptre_dvr's own scanning and Sweeps.read for a DVR (do not re-parse tiles
yourself) and sceptre_blue for a .fft; read through a memory map or slices, never the whole file; a read touches
only the tiles (a DVR) or rows (a .fft) the requested stretch needs; the file is only ever opened for reading. Rows
by time t0 <= k*row_seconds < t1; band from the bin f_lo falls in to the one f_hi falls in; thinning rows evenly
(first row kept) and columns by max over the bins each covers. Anything that is not a spectrum file raises
SpectraError (never another exception type from open_spectra). Vectorise with numpy; no Python loops over bins.
Module docstring says what and why. Match the surrounding code's style.

Done when this passes:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_spectra.py

Also keep `tools/tests/test_sceptre_dvr.py` passing (you do not edit sceptre_dvr.py).
End with a short summary: files changed, what you did, the test result, anything you could not do.
