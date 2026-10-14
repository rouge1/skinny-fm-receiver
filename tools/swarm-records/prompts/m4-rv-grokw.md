Review the branch checked out here (diff against main: `git diff main...HEAD`; the commit changes tools/fm_receiver/iqmeasure.py and appends tests to tools/tests/test_iq_measure.py). Read AGENTS.md, CONTRACTS-measure.md (tones; "Where the envelope and frequency are measured"; class rules). Do not list or read anything outside the working directory. Do not edit anything.

Goal: a CW tone classes `carrier` in a 28 MS/s 40 dB SNR recording and at 108 dB SNR with window sidelobes; rules 1-6 thresholds unchanged; the old suite unchanged.
Check: correctness of the second band-limited pass (centre/offset arithmetic, `base_s` in frequency levels, bursts/symbol rate on the decimated rate, edge cases: band None, occupied centre near the span edge, tiny bandwidth, n_s<64), the tones rule, and whether the new tests would fail on main's iqmeasure.py. Also memory/time: a 30 s 1 MS/s file is now read twice; flag if reading is not chunked.
Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
Answer with a list of findings, each tagged [high] (wrong result), [medium], [low], with file:line and a one-line fix, or "no findings".
