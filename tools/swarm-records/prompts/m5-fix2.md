Task: fix one review finding in `_banded` of tools/fm_receiver/iqmeasure.py.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Edit only tools/fm_receiver/iqmeasure.py and (append only) tools/tests/test_iq_measure.py.

Finding (two reviewers, medium): when `dec` is large (a band narrower than about rate/2**15, e.g. a 10 Hz band at
28 MS/s), `pad = dec * ceil(PAD/dec)` and `step` both become `dec`, so a block is 3*dec samples (millions): a read
and an FFT above 2**22 samples and possibly a span-length array. Fix: keep the padding PAD (not rounded up to a
multiple of dec) and only force `step` to a multiple of dec, aligning the output grid by arithmetic on sample
indices; and when dec itself exceeds what a block of at most 2**22 samples can hold, still never read more than
2**22 samples at once nor take a transform longer than 2**22 (for example by accumulating the decimated output
from sub-blocks, or a fixed-length filter). Results unchanged for all existing checks. Append a test: a 1 MS/s,
10 s file with a band of 5 Hz around a CW tone (use measure_file with band=(f-2.5, f+2.5) or whatever the API
gives) must measure without any read larger than 2**22 samples (counting source) and match the unbanded class.

Done when these exit 0:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
