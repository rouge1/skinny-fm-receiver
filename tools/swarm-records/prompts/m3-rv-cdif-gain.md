You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python.

Another model fixed tools/fm_receiver/iqmeasure.py so a Sceptre .cdif with the DATA_GAIN keyword is read in dBm:
samples scaled by 10**(-DATA_GAIN/20), full_scale_dbm 0.0, as radios.BlueReader does (class BlueReader in
tools/fm_receiver/radios.py); without the keyword nothing changes (power_dbm None). See: git diff main. Tests were
appended to tools/tests/test_iq_measure.py and test_iq_tools.py (they pass). Spec: gate G0 = a synthetic .cdif with
DATA_GAIN 60 and a tone of raw amplitude 1000 measures power_dbm 0.0 +/- 0.1, power_dbfs equal, full_scale_dbm 0.0;
same file without the keyword: power_dbm None; iq-picture of the first has its tone at 0 dBm +/- 1 dB in
peak_panel's data and labels the axis dBm.

Review for: correctness of the scaling (dtype, no mutation of the file array, t0/t1/band paths, measure_file and
IQSource.read all scaled once, not twice), keyword edge cases (missing, string, NaN, inf, negative, zero, 2-D file),
that iq-picture and fmctl measure (through open_iq) really show dBm, that the new tests actually prove G0 (not
vacuous), and that imports stay numpy-only. Probe with synthetic files in the system temp dir only.
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
