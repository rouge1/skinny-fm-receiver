Task: wiring and docs for phase u1 (narrow IQ files and the spectrum marker). Phase u1, task wiring.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CLAUDE.md, tools/tests/run_all.py (how suites are listed), tools/tests/test_narrow_files.py (head: its docstring), knowledge/usage.md (sections "Recordings: listening back", "Tuning with the mouse on the spectrum", "Playing other IQ files", "Controlling the window from a script: fmctl"), knowledge/capabilities.md (the Recordings entries and their status marks).
Edit only: tools/tests/run_all.py, knowledge/usage.md, knowledge/capabilities.md. Nothing else. Do not commit. (Other managers edit these files on other branches: keep your edits small and local so a merge is clean; do not reformat or reflow anything.)

Do:
1. run_all.py: add `test_narrow_files.py` to the suite list next to the other Recordings suites (same entry format, in the group of test_sceptre_spectra.py).
2. usage.md: in the Recordings section: (a) a narrow IQ recording (a few tens of kS/s, for example a Sceptre BLUE file at 54.7 kS/s): the tuner sits and moves anywhere inside the file's band (typed, clicked, dragged); the channel band is drawn no wider than the file; the Span knob goes from about band/200 (at least 500 Hz) to the whole band, so you can zoom to a few kHz; (b) the marker for a spectrum recording (.fft, sweep DVR): right-click offers "Marker to <f> MHz" (IQ tracks keep "Tuner to"), a left click places it, drag moves it, the readout gives the frequency and level (dBm when calibrated); `fmctl marker FREQ` does the same and replies the level; `fmctl status` has `marker_mhz`.
3. capabilities.md: add the two features under the Recordings tab in the file's existing format, with a status mark the file's own legend allows for "tested offscreen with synthetic files (test_narrow_files.py)"; do not mark anything as verified on real hardware or a real file.
Keep the file's voice: plain, no marketing words, no em dashes if the file avoids them.
Report the diff stat and the lines you added to run_all.py.
