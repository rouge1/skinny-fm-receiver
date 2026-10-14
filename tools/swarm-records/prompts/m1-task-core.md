Task: implement the measurement core, the new module tools/fm_receiver/iqmeasure.py (IQSourceError, IQSource,
open_iq, measure, measure_file, card_path, write_card).

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (section "tools/fm_receiver/iqmeasure.py": the interfaces, the card and the class rules
are the spec), CLAUDE.md, tools/fm_receiver/sceptre_dvr.py (scan, Reader), tools/fm_receiver/dvr_iq.py (how
full_scale_dbm is found), tools/fm_receiver/sceptre_blue.py, tools/tests/signals.py (write_sdvr, write_blue), and
the acceptance test tools/tests/test_iq_measure.py. The test is the spec: do not edit any test.

Edit only:
- tools/fm_receiver/iqmeasure.py: new file.

Constraints: numpy only. It must import none of gnuradio, PyQt5, scipy, library, radios, dsp (even indirectly: check
what sceptre_dvr, dvr_iq and sceptre_blue import before you use them). Read-only on every input. Files are read
in chunks (memory-map or slices), never whole; measuring 30 s of 1 MS/s IQ must take under 5 s and under 1 GB
extra memory. Vectorise with numpy; no Python loops over samples. Deterministic (no randomness). Classify exactly
by the contract's rules in the contract's order and put one short evidence sentence per rule that fired. Round
floats to 6 significant digits; JSON-safe card (no numpy scalars, None where not measurable). Module docstring
says what and why. Match the surrounding code's style.

Done when this passes:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py

End with a short summary: files changed, what you did, the test result, anything you could not do.

SPEC (behaviour):
## Behaviour (exact; the tests assert it)
- The interfaces, the card, the class rules and the refusals are exactly as in `CONTRACTS-measure.md`.
- Synthetic signals the tests build (all complex64, 1 MS/s unless stated; SNR as stated):
  CW tone at +50 kHz, 30 dB SNR; OOK 4,000 baud keyed on a 20 kHz carrier, 12 bursts of 40 ms every 100 ms;
  FSK at 9,600 baud, +/-5 kHz, constant envelope; FM, 75 kHz deviation, 1 kHz tone; AM, 80 % depth, 1 kHz tone;
  white noise only; a two-tone signal (+20 kHz, -60 kHz).
- Measured against known values: class equal to the signal's; `bandwidth_hz` within 15 % of the analytic
  occupied bandwidth (CW: under 5 kHz); `symbol_rate_baud` within 5 %; burst `median_duration_s` within 5 % and
  `repeat_interval_s` within 2 %; `tones` find both tones within 2 bins; `power_dbm` equals `power_dbfs` plus
  `full_scale_dbm` within 0.1 dB.
- The file readers: a `.cfile` with its `.sigmf-meta` (rate, centre read back), a bare `.cfile` (refused without
  `rate=`, accepted with), an IQ `.sdvr` written by `tests/signals.write_sdvr`, a `.cdif` by `write_blue`, a sweep
  `.sdvr` and a `.fft` (both refused), a missing file (refused).
- `write_card` writes `<base>.card.json` beside a `.cfile` and refuses the other three; the file read back
  equals the card; an existing non-card file is never overwritten.
- fmctl: each command of the table in the contract behaves and refuses as listed; `status` has the new keys.
  The old `test_control.py` still passes unchanged.
