Task: fix two classifier bugs in iq-measure so a single CW tone classes `carrier`.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (the `tones` field, "Where the envelope and frequency are measured", and the class rules), tools/fm_receiver/iqmeasure.py and tools/tests/test_iq_measure.py (reuse its synthetic-signal helpers).

Edit only:
- tools/tests/test_iq_measure.py: APPEND new checks only; do not change existing ones
- tools/fm_receiver/iqmeasure.py: the fix

Bugs (found on real files):
1. A CW tone in a 28 MS/s, 40 dB SNR recording classed `fm`: the frequency spread was measured over the whole 28 MHz of noise (818 Hz std; rule 5 is 500 Hz). Fix as the contract says: shift to the occupied centre and band-limit (FFT-domain) to max(3 x bandwidth_hz, 20 kHz) before the envelope, instantaneous frequency, bursts and symbol-rate steps, then decimate. `occupied`, `level`, `tones` unchanged except the tones rule.
2. A CW tone at 108 dB SNR classed `unknown`: the tones list held the Hann window's sidelobes (57 dB above the floor but 71 dB below the tone), so rule 7 "one tone" failed. Fix: drop any tone more than 60 dB below the strongest.

Procedure: FIRST append tests: (a) CW tone at +50 kHz, 1 MS/s, 100 dB SNR: class carrier and exactly 1 tone; (b) CW tone at 28 MS/s over a 28 MHz band, 40 dB SNR white noise: class carrier (keep the signal short, e.g. 0.1-0.2 s, to stay fast). Run the tests and confirm (a) and (b) fail on the unfixed code. THEN fix iqmeasure.py. The whole existing suite (114 checks) must still pass unchanged. Keep the 30 s, 1 MS/s measure under 5 s and 1 GB extra: do not read the span whole.

Done when this passes (run from the worktree root):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
Do not commit. In your final answer say which new checks failed before the fix.
