You are a REVIEWER on the FM receiver. Do not edit, create or delete any file. Do not run git commands that change
state. Do not list or read anything outside the working directory. You may run the tests and short probes with
`PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python` (QT_QPA_PLATFORM=offscreen).

Another model made `measure_file` cheap for long, wide files (phase m5). See the change with:
git diff 644289d HEAD -- tools/fm_receiver/iqmeasure.py. Spec: CONTRACTS-measure.md last section "Cost of a
measurement (m5)"; tests: tools/tests/test_iq_measure.py (the checks about 28 MS/s and 1 MS/s, 30 s).
The change replaces the per-chunk full-rate FFT/IFFT band filter in `_banded` by block overlap-save, with either
a phasor sum (narrow band) or a small inverse transform.

Review for:
1. Correctness: block edges (first and last block, n not a multiple of the step, short files, n_1 near 64),
   PAD vs. filter length, phase continuity of the mixer across blocks (fc * s / rate), decimation grid alignment
   when dec does not divide the step, bands wide enough to take the IFFT branch, band edges, odd lengths.
   Probe with short scripts: compare the old `_banded` (git show 644289d:tools/fm_receiver/iqmeasure.py) to the new
   one on random signals, several dec, lo/hi, n; report max difference.
2. Performance and memory: 28 MS/s, the phasor matrix size (phasors array bytes at the largest case: wide
   keep x grid), whole-span arrays, reads of at most 2**22 samples, the file read at most twice.
3. Anything the tests miss that a user would hit (CHUNK was lowered to 1 << 16: any effect on other callers?).
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
