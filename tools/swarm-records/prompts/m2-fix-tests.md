Review feedback on your test file tools/tests/test_iq_tools.py. Reviewers from other models reviewed it and a
consensus run verified each claim. Apply the fix list below. Edit only tools/tests/test_iq_tools.py. Do not list or
read anything outside the working directory.

Manager's overrides on the list: (a) do NOT add assertions on the PNG's title text, unit label, or mean/peak/waterfall
structure: they cannot be read from pixels without OCR and are judged by the manager looking at the picture; skip
those three items. (b) For the ASK signal copy the m1 builder from tools/tests/test_iq_measure.py. (c) Two-tone
expects class unknown as m1 pins it at test_iq_measure.py:487; read how that test pins it and mirror it.

I'll verify both reviews against the m2 spec, the Tools contract, and the acceptance tests.consensus: 12 confirmed, 5 rejected
fix list (for the author):
- [high] tools/tests/test_iq_tools.py:53 Add the m1 ASK signal (low level at least 10% of the high level) and expect class "ask". (grokw, musefree)
- [medium] tools/tests/test_iq_tools.py:53 Expect class "unknown" for the two-tone file; m1 pins that at tools/tests/test_iq_measure.py:487. (musefree)
- [medium] tools/tests/test_iq_tools.py:702 Assert the PNG title names the file, centre, rate and span. (grokw, musefree)
- [medium] tools/tests/test_iq_tools.py:702 Assert dBm for a calibrated .sdvr and dBFS for a .cfile. (grokw, musefree)
- [medium] tools/tests/test_iq_tools.py:702 Assert a mean and a peak spectrum over a waterfall, drawn with matplotlib Agg. (grokw, musefree)
- [medium] tools/tests/test_iq_tools.py:732 Snapshot the refusal folder (inputs and any extra files), as refuse_measure does, not only pic.png. (grokw, musefree)
- [low] tools/tests/test_iq_tools.py:556 Use a --t0/--t1 span whose class differs from the whole file, not only the reported source fields. (grokw)
- [low] tools/tests/test_iq_tools.py:626 Refuse a file shorter than 1024 samples and a path that is not .cfile/.sdvr/.cdif (exit 1, stderr, no write). (grokw, musefree)
- [low] tools/tests/test_iq_tools.py:626 Refuse an inverted or out-of-range --band and a --t0/--t1 span under 1024 samples. (musefree)
- [low] tools/tests/test_iq_tools.py:671 Refuse overwriting non-JSON garbage, and allow --card to replace an existing card. (musefree)
- [low] tools/tests/test_iq_tools.py:820 Add dsp to the iq-measure import ban (numpy only; iqmeasure bans dsp). (musefree)
- [low] tools/tests/test_iq_tools.py:578 Cover --rate alone, --center alone without --rate, and --card on a bare .cfile without --rate. (musefree)
rejected:
- peak_panel must be called with all seven arguments or a one-argument call raises TypeError: the task signature makes every argument but path optional. (grokw, musefree)
- A blank PNG must be tied to peak_panel samples in the pixels: the spec says the test finds both tones from peak_panel data, not by eye. (musefree)
- --rate/--center must be shown overriding a .sigmf-meta: the contract takes rate and centre from the sidecar and uses those flags for a bare .cfile. (grokw)
- Drop the 15 dB, local-maximum and bin-width tone check: the spec tells this test to find the tones, and that detector is a valid way to do it. (grokw, musefree)
- Require the default 30 s cap and empty stderr on success: m1 already pins the 30 s span, and the contract does not require silent stderr. (musefree)

Done when the file still runs to completion (its checks fail only because the tools do not exist):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
End with a short summary.
