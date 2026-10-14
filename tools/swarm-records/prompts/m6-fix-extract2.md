Fix round 2 on `_decimation`/`extract` in tools/fm_receiver/iqmeasure.py; tests: append to
tools/tests/test_iq_measure.py. Do not list or read anything outside the working directory. A reviewer found [high]:
in the fallback (no integer decimation fits [2 x width, max_rate]) the narrowed band is rate/(2*dec) wide, half of
what a complex stream at rate/dec can hold. Use a kept width of 0.9 x rate/dec (a complex stream at rate R holds
nearly R; the 0.9 leaves room for the skirt), centred on the same centre, clipped to the file; fix the docstring
(it said "half the output rate"). Also replace the 1e-9 fudge factors by exact integer arithmetic on floor/ceil with
a plain tolerance only where float noise matters (e.g. math.floor(x + 1e-9) and math.ceil(x - 1e-9) are fine; make
sure the ceil never returns a dec that leaves rate/dec above max_rate by more than 1e-9 relative).
Tests (failing before): 2.4 MS/s and 2.048 MS/s files, band None: a tone at +230 kHz (resp. +200 kHz) is present at
its frequency (within 200 Hz) and within 6 dB of the +100 kHz tone's level; a tone at +450 kHz is >= 40 dB down for
2.4 MS/s. Existing checks must still pass (167). Run
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
End with a short summary.
