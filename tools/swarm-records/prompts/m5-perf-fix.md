Task: STEP 2: make the m5 checks you wrote pass by fixing `tools/fm_receiver/iqmeasure.py`.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (last section "Cost of a measurement (m5)"), tools/fm_receiver/iqmeasure.py and
tools/tests/test_iq_measure.py. Do not weaken or edit any test.

Edit only tools/fm_receiver/iqmeasure.py (and, only if a check you wrote was wrong, append-fix your own new
checks; say so).

First find WHICH step holds whole-span arrays or does whole-span-rate FFTs (profile with the synthetic 28 MS/s
file: time and ru_maxrss per step; the second pass `_banded` does a full-rate FFT and inverse FFT per chunk at 28
MS/s, and the mixer/exp is computed at full rate). Then fix: mix to the band centre and decimate in chunks without
full-rate transforms, e.g. FFT overlap-save where only the band's bins are kept and a SMALL inverse FFT is taken
(frequency-domain decimation), or a windowed-FIR / polyphase decimator; numpy only, no scipy; reads of at most
2**22 samples; the file read at most twice; no array of the span's length. Avoid float64 temporaries per chunk
(use complex64 where precision allows). The results must not change: every existing check stays the same class
and within tolerance.

Done when:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
all exit 0. Report the cause you found and the measured 28 MS/s 1 s time and memory, and the 30 s 1 MS/s time.
