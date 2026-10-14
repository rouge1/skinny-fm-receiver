You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run probes with
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python; build synthetic files only in a
temp folder from tempfile.

Another model wrote the new tool tools/iq-picture (see git diff main -- tools/iq-picture). Spec: CONTRACTS-measure.md
section "Tools" and tools/tests/test_iq_tools.py (its iq-measure checks fail only because that tool is another task).
Its task prompt also required: module-level peak_panel(path, t0=None, t1=None, band=None, rate=None, center_hz=None,
nfft=None) -> (freq_hz, peak_db); chunked reading through iqmeasure.open_iq(...).read (a 30 s, 1 MS/s file under 10 s
and under 1 GB extra memory); matplotlib Agg selected before pyplot; no gnuradio, PyQt5, library or radios import.
Review for:
1. Contract compliance: options, dBm when full_scale_dbm else dBFS, title naming file/centre/rate/span, writes only
   the PNG, exit 1 + stderr message (no PNG) on IQSourceError.
2. Bugs: --band with edges outside the file's span or inverted, --t0/--t1 odd values (nan, inf, t0 >= t1), --nfft not a
   power of two or larger than the span, a file just over 1024 samples, a band narrower than one bin, exceptions other
   than IQSourceError escaping as tracebacks, the span at the file end (partial last chunk), the frequency axis not
   absolute, mean computed over different counts per chunk (a weighting bug), waterfall time axis wrong.
3. Memory: any place that holds the whole span (arrays proportional to the file length).
Probe the edge cases with short scripts and report only what you reproduced or can quote from the code.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
