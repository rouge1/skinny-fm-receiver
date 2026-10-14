Task: a marker for spectrum recordings in the Recordings tab. Phase u1, task marker.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CLAUDE.md, tools/tests/test_narrow_files.py (acceptance tests, group f is yours; do not edit it), tools/fm_receiver/app.py and widgets.py (the RF spectrum view: `set_marker`, the right-click menu text "Tuner to", the left-click handler, the marker line and readout; the status command and `_mode == 'playback'`), tools/fm_receiver/control.py (`marker` command and `status`).
Edit only: tools/fm_receiver/app.py, tools/fm_receiver/widgets.py, tools/fm_receiver/control.py. Do not commit.

Problem: in a spectrum track (.fft or a sweep .sdvr in Recordings) there is no receiver, so `tune()` has nothing to move and the menu's "Tuner to" does nothing visible. The user wants the marker: a vertical line they can drop at a frequency and drag, with the frequency and the level read out. `rf_view.set_marker` and `fmctl marker FREQ` already exist.
Wanted:
- For a spectrum track the RF view's right-click menu reads "Marker to <f> MHz" (for IQ tracks it keeps "Tuner to <f> MHz"); choosing it places the marker there.
- A left click in a spectrum track places the marker; dragging the marker moves it; the readout shows the frequency and the level at that bin in the view's unit (dBm for a calibrated file).
- `fmctl marker FREQ` also moves it in a spectrum recording and replies the level (as now).
- `status` gains `marker_mhz` (None when not placed; present in every mode, including Receive and IQ playback, where it is None or the marker the user placed, as the tests say).
Rules: colours from `theme.TOKENS`; a Qt paint/mouse override is guarded with try/except (an exception there aborts the process, CLAUDE.md); any layout change (readout appearing) is animated (fade/glide, no sudden jumps); keep it small and reuse the existing marker code.
Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_narrow_files.py`: every case must pass. Then, one after another: test_recordings.py, test_sceptre_spectra.py, test_sceptre_library.py, test_gui.py, test_control.py, test_control_more.py, test_tuning.py, all exit 0.
SCREENSHOT (required): write a short script (tools/tests/_u1_shot2.py, delete it afterwards) that opens a synthetic `.fft` or sweep `.sdvr` as test_sceptre_spectra.py does, places the marker with the menu action or a click, drags it, and saves a PNG into `shots/u1-marker.png` in the working directory (offscreen, as test_gui.py does). Do not commit the PNG. Report: files changed with one line each, test results, the PNG path.
