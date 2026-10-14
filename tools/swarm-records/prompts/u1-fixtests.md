Two reviewers read tools/tests/test_narrow_files.py. Their findings are below. Verify each against the spec you were given and against the code; apply those that are supported, reject (and say why in your report) any that pin more than the spec pins or cannot pass on a correct implementation. Edit only tools/tests/test_narrow_files.py. Do not commit. Do not list or read anything outside the working directory.
Points I (the manager) judge worth doing: the tuner range must be about LO +/- max(0, R/2*usable - min(150 kHz, R/8)), i.e. a real width of about 2 r, not merely non-empty (use a tolerance of about 1 kHz); the shown channel filter and the drawn band must equal min(filter, band) and be centred on the tuner / inside the file; after `view rf full` shown equals the band; the readout level unit is dBm for the calibrated spectrum tracks and the fmctl marker reply is compared with the level at that bin; a recording wider than 10 MHz keeps a 50 kHz minimum span (cheap synthetic spectrum or IQ file only if one is quick to write; otherwise state it); the IQ-track menu choice still moves the tuner and leaves marker_mhz None; the narrow-rate formula is asserted on the IQFile class only (other radios unchanged); exclude grid points adjacent to a range edge from the placed-within-tolerance check; "tuner placed anywhere" via a typed `tune` and via a drag of the channel band on the chart if a drag helper is reachable.
Do NOT require the tuner to land within a "tuner step" of the request beyond the spec's wording (distinct values, inside the band).
Run the suite as before; it must still run to the end, with the new cases failing on the current code for the spec's reasons. Report which items you applied, rejected, and the pass/fail list.

REVIEW 1 (by Grok):
I'll review the phase u1 tests against the spec and the current receiver code, without changing any files.verdict: changes
issues:
- [medium] tools/tests/test_narrow_files.py:check_a_width The window range is only required to be non-empty, so any span wider than about 1 kHz still yields three distinct tunes. Assert `tuner_range_mhz` is the LO ± `max_offset` (about ±18 kHz here), within 1 kHz.
- [medium] tools/tests/test_narrow_files.py:check_c_drawn Only the drawn width is capped, so a legal-width band parked on the left edge passes. Assert the band lies inside the file and on the tuner.
- [medium] tools/tests/test_narrow_files.py:part1_narrow The tuner is never dragged on the chart. Middle-drag the channel band across the file and assert `tuner_mhz` follows and stays inside the band.
- [medium] tools/tests/test_narrow_files.py:check_d_range Bands over 10 MHz and live sweeps, which must keep a 50 kHz minimum span, are untested. Add one recording wider than 10 MHz whose Span minimum stays 50 kHz.
- [medium] tools/tests/test_narrow_files.py:check_fmctl_reply Any numeric `level_db` with a matching unit passes, and dBm is never required. Compare the reply to that bin, and require `dBm` on the calibrated sweep.
- [low] tools/tests/test_narrow_files.py:check_readout The level is checked against the bin of the readout’s own frequency, and the frequency slack grows as 1/viewport width. Compare both fields to the marker’s bin within one bin and 0.1 dB.
- [low] tools/tests/test_narrow_files.py:check_d_span5 “Around the tuner” only requires the tuner to lie inside the 5 kHz window. Also require the window centre within a small fraction of 5 kHz of the tuner.
- [low] tools/tests/test_narrow_files.py:check_c_shown The “never wider than the band” rule is sampled once. Re-read `channel_filter_khz` and the drawn band after a tune and after `view rf span full`.
REVIEW 2 (by Muse):
verdict: changes
issues:
- [high] tools/tests/test_narrow_files.py:check_a_width only asserts width>0 so the reported [0,6000] bug passes; assert width is ~2r and inside the file band
- [high] tools/tests/test_narrow_files.py:check_a_distinct asserts distinct+inside but not placed; assert each tune lands within a tuner step of the request
- [high] tools/tests/test_narrow_files.py:check_c_shown asserts only <=band so any small value passes; assert ==min(200kHz,band)
- [high] tools/tests/test_narrow_files.py:check_c_drawn asserts only <=band; assert width==min(filter,band) and centred on the tuner
- [medium] tools/tests/test_narrow_files.py:check_b_band passes when pinned at LO; pair with a movement assertion or fold into check_b_placed
- [medium] tools/tests/test_narrow_files.py:check_d_range no live-sweep or >10MHz case; add the spec's 50kHz-minimum case
- [medium] tools/tests/test_narrow_files.py:check_d_span5 only asserts containment; assert the 5kHz window is centred on the tuner
- [medium] tools/tests/test_narrow_files.py:check_d_full asserts span only; assert shown==band as the spec promises
- [medium] tools/tests/test_narrow_files.py:check_readout never asserts dBm; assert level_unit is dBm for these spectrum tracks
- [medium] tools/tests/test_narrow_files.py:iq_menu asserts menu text only; assert choosing it moves the tuner and leaves marker_mhz alone
- [medium] tools/tests/test_narrow_files.py:check_no_marker only covers spectrum windows; assert status.marker_mhz exists in receive/IQ playback too
- [medium] tools/tests/test_narrow_files.py:check_margin pins HackRF at 54/250kS/s it never offers; restrict narrow-rate formula to IQFile and cover BB60/USRP/RTLSDR unchanging
- [medium] tools/tests/test_narrow_files.py:sweep_grid tuner drag and digit typing are never exercised; spec promises placed, dragged and typed coverage
- [low] tools/tests/test_narrow_files.py:check_menu 0.0006MHz tolerance against 1kHz menu rounding plus per-pixel mapping is flaky on wide bands; widen or assert rounding explicitly
- [low] tools/tests/test_narrow_files.py:check_b_placed 501Hz tolerance at a range edge not on the 1kHz raster can fail a correct clamp; exclude edge-adjacent grid points
- [low] tools/tests/test_narrow_files.py:check_readout tolerance grows with px so a narrow offscreen viewport makes it vacuous; require a minimum viewport width
