You are a REVIEWER on FM Receiver (PyQt5 + numpy app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with /home/user/miniconda3/envs/gnu/bin/python (PYTHONPATH=tools, QT_QPA_PLATFORM=offscreen).

Another model implemented the spectrum reader tools/fm_receiver/spectra.py on this branch (new file; see it with
`git show HEAD --stat` and read the file). Spec and tests: CONTRACTS.md (section spectra.py),
tools/swarm-records/specs/p3.md, tools/tests/test_spectra.py. It uses tools/fm_receiver/sceptre_dvr.py and
sceptre_blue.py (do not suggest editing those).

Review for:
1. Spec compliance, every rule including the refused cases: dBm from each tile's gain/offset; rows t0 <= k*row_seconds
   < t1; band inclusive of the bins f_lo and f_hi fall in; even thinning with the first row kept; column pooling by
   max with freqs the mean frequency of each column; empty (not an error) outside the file; SpectraError for
   everything that is not a spectrum file (including a corrupt or truncated file, and a .fft of the wrong format).
2. Correctness bugs: off-by-one at boundaries, float rounding in the row range (k*row_seconds vs t0/t1), a band
   outside the file, max_cols/max_rows of 0 or 1, a DVR whose newest unbroken stretch is not the whole file,
   mutation of inputs, resource leaks (open files/memmaps), anything that writes to the file.
3. Performance: only the tiles/rows a stretch touches are read (a 1 GB ring has hundreds of tiles; no whole-file
   read, no Python loop over bins, listing cost in open_spectra).
4. Interface drift from CONTRACTS.md (names, attributes, return types and dtypes).
5. Anything the tests miss that a user would hit.
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
