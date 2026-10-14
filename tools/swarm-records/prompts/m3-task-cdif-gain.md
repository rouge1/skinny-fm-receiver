Task: make iqmeasure read a Sceptre .cdif in dBm when the file says how.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read tools/fm_receiver/iqmeasure.py (_open_cdif, IQSource) and, for the right reading, class BlueReader in
tools/fm_receiver/radios.py (it only reads; do not import radios into iqmeasure: numpy-only imports stay).

Bug: iqmeasure.open_iq on a real FM .cdif hands back raw counts, so iq-measure reports power_dbfs +47.9 and
power_dbm None. Fix as BlueReader does: when the BLUE keyword DATA_GAIN is present (blue.keywords.get('DATA_GAIN'),
float-convertible), scale every sample read by 10 ** (-DATA_GAIN / 20) (as float32, do not mutate the file's array)
and open the source with full_scale_dbm = 0.0, so |x|**2 is mW and power_dbm == power_dbfs. Without the keyword
(or an unparsable value) nothing changes: raw samples, full_scale_dbm None, power_dbm None. iq-picture and
fmctl measure go through open_iq, so they follow; do not edit them.

Edit only:
- tools/fm_receiver/iqmeasure.py
- tools/tests/test_iq_measure.py (append tests only)
- tools/tests/test_iq_tools.py (append tests only)

Tests to append (synthetic only; signals.write_blue in tools/tests/signals.py takes keywords={'DATA_GAIN': 60.0};
write files to a temp dir):
1. A .cdif with DATA_GAIN 60 holding a complex tone of raw amplitude 1000 (|x|**2 is 1 mW after scaling) plus a
   little noise: measure_file gives power_dbm 0.0 +/- 0.1, power_dbfs equal to power_dbm, full_scale_dbm 0.0.
2. The same samples written without the keyword: power_dbm is None.
3. In test_iq_tools.py: iq-picture of the first file has its tone at 0 dBm +/- 1 dB in peak_panel's data (see how
   the existing tests there reach it) and the axis is labelled dBm.
Follow the style of the neighbouring tests and the runner each file uses.

Done when all of these pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
End with a one-paragraph summary. Do not commit.
