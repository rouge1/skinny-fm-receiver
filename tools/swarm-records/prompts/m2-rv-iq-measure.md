You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run probes with
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python; build synthetic files only in a
temp folder from tempfile.

Another model wrote the new tool tools/iq-measure (see git diff main -- tools/iq-measure, a thin wrapper over
tools/fm_receiver/iqmeasure.py). Spec: CONTRACTS-measure.md section "Tools" and tools/tests/test_iq_tools.py (its
iq-picture checks fail only because that tool is another task). Review for:
1. Contract compliance: options, stdout is only the JSON, --card via write_card, exit 1 with the message on stderr and
   nothing on stdout for IQSourceError, numpy only (no matplotlib, no scipy), importable without running main.
2. Bugs: argument handling (--band LO HI order and units: absolute Hz; --t0 without --t1; --rate without --center),
   exceptions other than IQSourceError escaping as tracebacks (for example a ValueError for a bad --band, a band
   outside the file's span, t0 > t1), JSON-safety of the card (NaN/inf in the output), a broken pipe, writes without
   --card.
Probe the edge cases with short scripts and report only what you reproduced or can quote from the code.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
