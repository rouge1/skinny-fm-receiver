You are a REVIEWER on the FM receiver. Do not edit, create or delete any file. Do not run git commands that change state. Do not list or read anything outside the working directory. You may run the tests and short probes with `PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python` (QT_QPA_PLATFORM=offscreen).

Phase m7: after m5, a pure CW at 19 dB SNR over 28 MHz in a wide recording was classed `fsk` (levels [100030, 100329], keyed true) instead of `carrier`. Another model changed `_banded` in tools/fm_receiver/iqmeasure.py (see `git diff c8ae476 HEAD`): a raised-cosine skirt (SKIRT_BINS) outside the passband instead of a hard brick-wall mask, claiming that the hard cut rings longer than the overlap and leaves a phase step at block splices. Spec: CONTRACTS-measure.md; new tests at the end of tools/tests/test_iq_measure.py.

Review for:
1. Is the claimed root cause right? Probe: measure a CW +100003.3 Hz, white complex noise at 19 dB SNR over the full rate, at 28 MS/s for 2 to 3 s (write a .cfile in blocks, use measure_file with rate= and center_hz=), at 10 MS/s and 1 MS/s, with several seeds, on HEAD and on c8ae476 (`git show c8ae476:tools/fm_receiver/iqmeasure.py`, load as a separate module). Report class, keyed, deviation_hz, levels_hz per run. The tests use only 0.05 s: does the fix hold at 3 s and at 8 s of signal (the real failure was 8.6 s)?
2. The 1 MS/s test uses deviation < 130 where the spec says < 100. What deviation_hz does HEAD give at 1 MS/s over seeds, and what did the pre-m5 code (644289d) give? Is 130 hiding a defect?
3. Passband gain flatness and group delay: does the skirt change tone power (`power_dbm`), band edges, narrow-band (5 Hz) and wide-band (IFFT branch) cases? Does it bias OOK, FSK 9.6 kbaud, FM, AM classes? Run the whole test file.
4. Do the new tests fail without the fix and are they honest (what they assert, durations)?
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else (include the measured numbers in the issues or a "measured:" line):
verdict: pass | changes
measured: <one or two lines>
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
