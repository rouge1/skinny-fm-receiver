Task: fix a regression in `tools/fm_receiver/iqmeasure.py`: a pure CW tone at low SNR in a wide recording is no longer classed `carrier`.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (the class rules, "Where the envelope and frequency are measured", last section "Cost of a measurement (m5)"), CLAUDE.md, tools/fm_receiver/iqmeasure.py (`_banded`, `_decimated`, `_analyse`, `measure_file`, `measure`) and tools/tests/test_iq_measure.py.
Edit only: tools/fm_receiver/iqmeasure.py and tools/tests/test_iq_measure.py (APPEND new checks and add them to GROUPS; change nothing existing). Nothing else. Do not commit.

The failure (measured on a real file after m5): 28 MS/s, 8.6 s, one CW tone at +100000 Hz, 19 dB SNR over the 28 MHz (white noise). m5 code gives `frequency.levels_hz` [100030, 100329], `keyed: true`, symbol rate 1140 baud, class `fsk`, with the envelope constant and one tone. The pre-m5 code (`git show 644289d:tools/fm_receiver/iqmeasure.py`) gave levels [100037], deviation 78 Hz, carrier. The m5 tests used 40 dB SNR and missed it. Suspects, in order: the overlap-save/phasor-sum band-limit in `_banded` leaving a phase or gain step at block edges (the instantaneous frequency turns a step into a second cluster); phase of the mixer across blocks; decimation aliasing noise; the block size (block edges every `step` samples, about 1 per 10 ms at 28 MS/s). Do not assume: find it by measurement. Compare `_banded` output of the new code to the old per-chunk code on a synthetic 19 dB file, look at the instantaneous frequency around block edges.

STEP 1, tests first. Append checks that FAIL on the current code (run them and confirm, report the numbers):
1. CW at +100003.3 Hz, 28 MS/s, 3 s (write the .cfile in blocks in the test's temp folder, complex64 interleaved; pass rate= and center_hz=), white complex noise so SNR over the full 28 MHz is 19 dB: class `carrier`, `frequency.keyed` False, `frequency.deviation_hz` < 100, one tone. If 3 s is too slow for the suite use the shortest length that still reproduces the failure, and say what you chose.
2. The same signal at 10 MS/s and at 1 MS/s (SNR 19 dB over the band): the same assertions.
3. The same CW at 40 dB SNR, 28 MS/s still `carrier`.
4. Block-size independence: measure the same file with the module's block size constant(s) (e.g. FILTER_BLOCK, MAX_BLOCK, via monkeypatch of the module attribute) doubled and halved: `frequency.levels_hz` agree within 20 Hz.
Existing checks must still pass (OOK, FSK 9.6 kbaud +/-5 kHz, FM, AM, noise, two-tone, the m5 cost checks: file read at most twice, no whole-span array, 28 MS/s 1 s CW under 4 s, 30 s at 1 MS/s under 5 s).

STEP 2, the fix in iqmeasure.py, minimal. Then all checks must pass:
`QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py`
Also run tools/tests/test_iq_tools.py and tools/tests/test_control_more.py the same way.

Report: the root cause in 2-3 sentences with the measured numbers (before and after: levels_hz, deviation, class), the check names that failed before the fix, the run times of the new checks, and the timing of the 28 MS/s 1 s CW and the 30 s 1 MS/s checks.
