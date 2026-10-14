You are a REVIEWER on the FM receiver. Do not edit, create or delete any file. Do not run git commands that change state. Do not list or read anything outside the working directory. You may run short probes and the tests: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_narrow_files.py` (the marker group f is expected to fail: a later task does it).

Another model fixed narrow IQ recordings (a 54.7 kS/s BLUE file) in tools/fm_receiver/radios.py, app.py, widgets.py. See the diff: `git diff main -- tools/fm_receiver`. Read CLAUDE.md first. Spec of the change:
- A file of rate R has a tuner range of LO +/- r, r = max(0, R/2*usable - margin), margin = min(150 kHz, R/8). For R >= 1.2 MS/s nothing changes: FM files and every live radio (BB60D, HackRF, RTL-SDR, others) keep their numbers. tuner_mhz is never outside the file's own band.
- Drawn channel band and status.channel_filter_khz are min(filter, band) for a narrow file; the saved filter value is left alone.
- RF view of a recording of band B Hz: span knob range [max(500 Hz, B/200), B]; 50 kHz stays the minimum for live sweeps and for bands over 10 MHz.
- Qt paint overrides guarded by try/except; colours from theme.TOKENS; layout changes animated.
Review for: correctness bugs, regressions for wide files and live radios (a changed max_offset used by other callers: grep every use), edge cases (rate 0, tiny rates, band exactly 10 MHz, switching tracks from narrow to wide and back: is the knob range and floor restored?), unguarded paint code, copied colours, dead code.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
