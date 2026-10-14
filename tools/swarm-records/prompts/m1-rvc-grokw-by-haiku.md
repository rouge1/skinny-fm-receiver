You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python.

Another model implemented the phase m1 measurement core, the new file tools/fm_receiver/iqmeasure.py, in this
worktree. See it with: git diff main -- tools/fm_receiver/iqmeasure.py. The spec is CONTRACTS-measure.md (section
"tools/fm_receiver/iqmeasure.py") and the acceptance test tools/tests/test_iq_measure.py (it passes).

Review for:
1. Spec and contract compliance: every rule, including the class rules in their order, the refusals, "never
   writes", "never overwrites a file that is not a card", the 30 s default span, band restriction, t0/t1.
2. Correctness bugs the tests miss: probe edge cases with short scripts (synthetic signals only: noise, CW, OOK,
   ASK, FSK, FM, AM, two tones; short files near 1024 samples; a band that contains nothing; an odd-length file;
   a complex64 array vs a list; NaN/zeros input). Look for exceptions that are not IQSourceError, numpy scalars in
   the card (it must be JSON-serialisable with json.dumps), and mutation of the input.
3. Performance and memory: measure 30 s of 1 MS/s synthetic complex64 (write it to a .cfile in a temp folder
   OUTSIDE the repo is not allowed, so use the system temp dir via tempfile) with measure_file: must be under 5 s
   and under 1 GB extra memory, read in chunks.
4. Imports: numpy only, none of gnuradio, PyQt5, scipy, library, radios, dsp (also indirectly).
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
