Review feedback on your cdif-gain work. Reviewers from two other models found one real gap. Edit only
tools/tests/test_iq_tools.py (append or amend your own new check only). Do not list or read anything outside the working
directory. iqmeasure.py and tools/iq-picture are off limits.

FIX LIST:
- [medium] tools/tests/test_iq_tools.py check_picture_dbm only checks the unit string and PNG size, so it never
  proves the level in peak_panel's data. Note: peak_panel's levels are per bin (power divided by sum(window**2)), so
  an on-bin unit tone reads about +34 dB there, NOT 0 dBm; do not assert 0 dBm. Instead assert that peak_panel of
  the DATA_GAIN 60 file (tone of raw amplitude 1000) has its maximum 60 dB (+/- 0.5) below the maximum of
  peak_panel of the identical samples written without the DATA_GAIN keyword (the keyword is what scales it), and
  keep the dBm axis-label check. Add a one-line comment saying why the absolute value is not asserted.

Done when both pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
Do not commit. End with a one-paragraph summary.
