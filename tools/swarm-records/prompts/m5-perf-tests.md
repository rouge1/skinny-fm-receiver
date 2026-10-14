Task: make `measure_file` cheap for long, wide recordings. STEP 1 only: write failing tests, then STOP.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (last section "Cost of a measurement (m5)", and "Where the envelope and frequency are
measured"), tools/fm_receiver/iqmeasure.py (especially `_banded`, `_decimated`, `_analyse`, `measure_file`, `CHUNK`,
`PAD`), and tools/tests/test_iq_measure.py.

Edit only tools/tests/test_iq_measure.py (APPEND new check functions and add them to GROUPS; change nothing
existing). Do not edit iqmeasure.py in this step.

Background: commit 96109b5 added a band-limit that made a real 28 MS/s, 8.6 s IQ file (240 M samples) take 61 s and
1.05 GB, up from 12 s. Targets: 28 MS/s CW through `measure_file` from a `.cfile` in under 4 s per second of
signal, peak extra memory under 400 MB, class `carrier`.

New checks (each must FAIL on the current code, and be fast enough to run in the suite; use 1 s = 28 M samples
written in blocks to a `.cfile` in the test's temp FOLDER, complex64 interleaved, no sidecar needed if `rate=` and
`center_hz=` are passed to measure_file):
1. Synthetic 28 MS/s, 1 s CW (+100 kHz, 40 dB SNR): run `measure_file` in a SUBPROCESS (sys.executable, PYTHONPATH
   tools) that prints wall time and `resource.getrusage(RUSAGE_SELF).ru_maxrss` growth over a baseline taken after
   importing numpy and iqmeasure; assert class 'carrier', time < 4 s, memory growth < 400 MB. Print the measured
   numbers in the check's detail.
2. A counting source: monkeypatch/wrap `iqmeasure.open_iq` (or the source's read) so every `read` request size is
   recorded; assert the largest request is <= 2**22 samples and the total samples requested <= 2 x the span (the
   file is read at most twice). Use a smaller wide file (e.g. 28 MS/s, 0.3 s) if that keeps the test quick.
3. No array of the span's full length is allocated: with tracemalloc on a 28 MS/s, 0.3 s file the peak traced
   memory stays below 0.25 x the span's size in bytes as complex64 times... choose a bound that a chunked
   implementation meets easily and a whole-span one (>= 8 bytes x n) fails.
4. `measure` of an array and `measure_file` of the same samples give the same class and agree within the existing
   tolerances (power, tone frequency) for a small signal.
5. A 30 s, 1 MS/s file: under 5 s, under 1 GB extra memory (subprocess, as check 1).

Done when: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py`
runs, the existing checks still pass, and checks 1-3 are reported failing. Report which check fails and the
measured numbers.
