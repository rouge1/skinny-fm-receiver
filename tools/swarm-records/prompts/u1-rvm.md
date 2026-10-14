You are a REVIEWER on the FM receiver. Do not edit, create or delete any file. Do not run git commands that change state. Do not list or read anything outside the working directory. You may run short probes and the tests: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_narrow_files.py`.

Another model added a user-placed marker for spectrum recordings (.fft, sweep .sdvr in the Recordings tab). See the diff: `git diff beb8c50 -- tools/fm_receiver` (app.py, widgets.py, control.py). Read CLAUDE.md first. Spec:
- For a spectrum track the RF view's right-click menu reads "Marker to <f> MHz" (IQ tracks keep "Tuner to <f> MHz"); choosing it places the marker; a left click also places it; dragging the marker moves it; the readout shows frequency and level at that bin in the view's unit (dBm if calibrated). `fmctl marker FREQ` moves it in a spectrum recording and replies the level; `status` gains `marker_mhz` (None when not placed).
- Everything else behaves as before: live sweeps and IQ tracks keep their click, double-click, snap and tuner behaviour.
- Qt paint and mouse overrides are guarded with try/except (an exception there aborts the process); colours from theme.TOKENS, never copied at import; layout changes are animated (fade/glide).
Review for: correctness bugs, regressions in live sweep / IQ playback clicks (marker_mode left on after switching tracks or modes? cleared when the recording changes or the view is reset?), the marker readout when the level is NaN or the bin is outside the data, drag outside the plot, the marker surviving a zoom (it should stay at its frequency), the old tuner marker line colliding with the new one, unguarded handlers, copied colours, dead code.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
