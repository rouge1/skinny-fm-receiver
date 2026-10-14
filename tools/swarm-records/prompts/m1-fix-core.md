Review feedback on your core. Independent reviewers from other models reviewed it, and a consensus run verified each
claim against your code. Apply the fix list below. Edit only tools/fm_receiver/iqmeasure.py. Do not list or read anything outside the working
directory. Tests are off limits.

FIX LIST:
- [high] tools/fm_receiver/iqmeasure.py:485 Decimation leaves at least half the bins empty, so the 25th-percentile floor collapses and in-band noise is reported at a huge SNR with edge tones. Take the floor from the in-band bins only. (source: haiku-by-musefree)
- [medium] tools/fm_receiver/iqmeasure.py:274 All-zero power still divides in `_occupied`, so `bandwidth_hz` is negative. Return 0 when the total is not positive. (source: haiku-by-musefree)
- [medium] tools/fm_receiver/iqmeasure.py:444 A band narrower than `rate/(2*CHUNK)` makes `dec > CHUNK`, `step` 0, and `range` raises ValueError. Keep every step a positive multiple of `dec`. (source: haiku-by-grokw)
- [medium] tools/fm_receiver/iqmeasure.py:432 An inverted band, a band outside the recording, or fewer than 64 samples after decimation raises ValueError from `measure_file` (also line 475). Raise `IQSourceError`. (source: haiku-by-grokw, haiku-by-musefree)

Done when all of these pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
Keep the 30 s measure at about 330 MB and about 1 s: do not read the span whole.
