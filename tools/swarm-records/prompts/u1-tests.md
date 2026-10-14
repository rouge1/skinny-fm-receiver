Task: write the acceptance tests for phase u1 (narrow IQ files and the spectrum marker in the Recordings tab). The implementation does not exist yet, so the new tests are EXPECTED to fail on the current code.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CLAUDE.md, tools/tests/test_recordings.py and tools/tests/test_sceptre_spectra.py (how the window is driven offscreen, how fmctl commands are called, how synthetic files are written), tools/tests/signals.py (`write_blue`, `write_sdvr`), tools/fm_receiver/radios.py (`max_offset`, `EDGE_MARGIN_HZ`), tools/fm_receiver/control.py and tools/fm_receiver/app.py (search for min_span_hz, set_marker, "Tuner to", channel_filter_khz).
Edit or create only: tools/tests/test_narrow_files.py (new). Nothing else. Do not commit.

SPEC (the behaviour to assert):
Bugs seen on a real 54.7 kS/s Sceptre BLUE IQ file (a CW tone near 100.1015 MHz, 4.4 s) in the offscreen window:
1. tuner trapped at the left of the RF chart: `status` shows tuner_mhz 5944.3 with tuner_range_mhz [0, 6000]. Cause: Radio.max_offset(rate) = rate*usable_fraction/2 - 150 kHz is negative for rates under ~300 kS/s, clamped to 0.
2/3. span cannot change: RF view is built with min_span_hz=50e3, so span range is [50 kHz, 54.7 kHz] for a 54.7 kHz file.
4. In a spectrum track (.fft, sweep .sdvr) right-click "Tuner to" does nothing visible: the user wants a marker (vertical line) to drop and drag, with frequency and level read out. rf_view.set_marker and fmctl `marker FREQ` already exist.

Behaviour:
- A file of rate R has a tuner range of LO +/- r with r = max(0, R/2*usable - margin) and margin = min(150 kHz, R/8). For R >= 1.2 MS/s nothing changes. A 54.7 kS/s file has r about 20 kHz, so the tuner is placed, dragged and typed anywhere in it and is never pinned: `tune 100.1015`, `tune 100.09`, `tune 100.11` give distinct `tuner_mhz` values inside the file's band; `tuner_mhz` is never outside the file's own band (sweep a dense grid of requested frequencies, also outside the band).
- The drawn channel band and `status.channel_filter_khz` never exceed the file's band (filter shown at min(filter, band) for a narrow file; the app's saved filter value is left alone).
- RF view of a recording of band B (Hz): span knob range [max(500 Hz, B/200), B] (50 kHz stays the minimum for live sweeps and for bands over 10 MHz). `view rf span 5` (kHz) on the 54.7 kHz file gives `span` 0.005 MHz and `shown` a 5 kHz window around the tuner; `view rf full` returns 54.7 kHz.
- Marker: for a spectrum track the RF view's right-click menu reads "Marker to <f> MHz" (for IQ tracks it keeps "Tuner to <f>"); choosing it places the marker there; a left click also places it; dragging moves it; the readout shows frequency and level at that bin in the view's unit (dBm for a calibrated file). `fmctl marker FREQ` also moves it in a spectrum recording and replies the level (as now); `status` gains `marker_mhz` (None when not placed).
- Everything else behaves as before.

Cases to write (each its own named check, one behaviour per check, clear failure messages, summary at the end, exit 0 only if all pass; follow the structure of test_sceptre_spectra.py):
a. 54,687.5 S/s synthetic BLUE (write_blue) with a CW at +3 kHz: after opening it in Recordings, three `tune` values give three distinct tuner_mhz inside the band; tuner_range_mhz has width > 0.
b. Dense grid of requested tuner frequencies from LO-100 kHz to LO+100 kHz: tuner_mhz always within the file's band.
c. channel_filter_khz <= band (54.7 kHz) with the filter set wide (e.g. 200 kHz via the fmctl filter command if it exists, else the knob).
d. span range lower bound for the narrow file is <= 0.5 kHz-ish (max(500 Hz, B/200) = 500 Hz) and the upper is B; `view rf span 5` -> span 0.005 MHz; `view rf full` -> 0.0547 MHz (approx); and a 250 kS/s BLUE file has range [1.25 kHz? compute max(500, 250e3/200)=1250 Hz, 250 kHz]; a 2.4 MS/s file keeps 50 kHz minimum if it is checkable cheaply (else skip it, do not fake it).
e. unchanged: a 2 MS/s or larger file's tuner range is the same as `max(0, R/2*usable - 150e3)` (compute from radios.Radio.max_offset if the method is reachable with a rate; also assert margin = min(150 kHz, R/8) via the class's values for R=54.6875e3, 250e3, 1.2e6, 2e6 against the formula in the spec, directly on `max_offset`).
f. spectrum marker: a `.fft` (use the helpers of test_sceptre_spectra.py) and a sweep `.sdvr`: right-click menu text starts with "Marker to" and contains the frequency; IQ track menu still "Tuner to"; invoking the marker action sets `status.marker_mhz`; a simulated left click places it; a simulated drag moves it; the readout string has the frequency and a level number; `fmctl marker FREQ` replies a level and `marker_mhz` equals FREQ within one bin; `marker_mhz` is None before placing.
Find how the window exposes the menu and the click handlers in app.py/widgets.py by reading them; if a handler is not reachable from a test, call the nearest public method and say so in your report. Pin only what the spec pins; use approx tolerances for floats.

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_narrow_files.py`. It must run to the end (failures reported, no crash or hang) and each new check must fail for the reason in the spec, not an import error or typo. Report: each check name and its failure message on the current code, the run time, and any point where you could not reach a handler.
