Fix round on `extract` in tools/fm_receiver/iqmeasure.py and its checks in tools/tests/test_iq_measure.py (append
only to the tests). Do not list or read anything outside the working directory. Confirmed review findings:
1. [high] Output rate must lie in [2 x band, max_rate] when that is possible. With integer decimation of a file at
   a non-divisor rate (2.4, 2.048, 1.5 MS/s) and the defaults (band None -> +/-250 kHz, max_rate 1e6),
   `ceil(rate/max_rate)` gave 800 kS/s < 2 x 500 kHz. New rule: let want = rate/(2 x width). dec = the LARGEST
   integer with rate/dec >= 2 x width (that also decimates a narrow band toward 2x its width, so a long narrow
   span stays under the 50 M cap); if rate/dec still exceeds max_rate, use dec = ceil(rate/max_rate); if that makes
   rate/dec < 2 x width (no integer fits), narrow the effective band to rate/dec about the same centre (the output
   is then the band's centre part; center_hz is unchanged), documented in the docstring. A file already at or below
   max_rate with the whole-file band stays pass-through.
2. [med] The 50 M output limit uses the chosen dec, so narrowing the band (which allows a larger dec) helps; the
   message says so.
3. [med] `max_rate` must be a finite number > 0, else IQSourceError (0 crashed, negative was ignored).
4. A band partly outside the file is clipped by `_band_offsets` and center_hz is the middle of the clipped band:
   keep that, and say so in the docstring.
Tests (append, each failing before the fix): a 2.4 MS/s file, defaults: rate between 1e6 (2 x 500 kHz) when
possible - here 2.4/dec: no integer gives [1e6, 1e6], so assert the narrowed case: rate <= 1e6 and the +100 kHz tone
is at +100 kHz within 200 Hz and a +400 kHz tone is not present (>= 40 dB down); a 2.4 MS/s file with a 100 kHz band
and max_rate 1e6: rate in [200 kS/s, 1e6] and the largest such; the narrow-band long-span cap: a stub source of 60 s
at 2 MS/s with a 10 kHz band and a fetch that returns zeros must NOT raise (use a stub whose fetch returns zeros
chunk by chunk; keep it quick by a 12 s stub at 2 MS/s with band 10 kHz, max_rate 1e6 -> output about 12 s x 20 kS/s)
and the same stub with band None and max_rate 1e9 raises IQSourceError before reading; `max_rate=0` and
`max_rate=-5` raise IQSourceError. All existing checks must still pass. Run
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
End with a short summary.
