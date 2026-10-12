Review feedback on your spectra reader. Independent reviewers from other models reviewed it, and a consensus run
verified each claim against your code. Apply the fix list below. Edit only tools/fm_receiver/spectra.py. Do not list
or read anything outside the working directory. Tests and contracts are off limits.

FIX LIST:
- [medium] tools/fm_receiver/spectra.py:95 a DVR band with hi<=lo (a frequency span that misses the file, or
  f_lo > f_hi) returns (0, 0); keep the selected row numbers ks and return an (n, 0) float32 block, exactly as the
  .fft path does (same shape and times for both kinds).
- [medium] tools/fm_receiver/spectra.py:63 max_cols=0 is falsy, so the cap is skipped; test `is not None`, treat 0
  as a real limit and return an (n, 0) block with 0 freqs without calling _pool (arange(0) // 0). Same for
  max_rows=0: 0 rows.
- [low] tools/fm_receiver/spectra.py:28-29 the 1e-9 slack on the upper bound can drop a row up to 1e-9 interval
  inside t1; make the slack on a time boundary much smaller (about 1e-12 of a row) while still treating an exact
  k*row_seconds as on the row.

Done when this passes:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_spectra.py
End with a short summary.
