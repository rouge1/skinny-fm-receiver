Fix round on tools/iq-decode (edit only that file). Do not list or read anything outside the working directory.
Two reviewers found, confirmed:
1. [high] The second resampling stage (a boxcar block average to --out-rate) does not anti-alias: a tone at 200 or
   375 kHz folds into the band at about -13 dB, and the edge droops. FIX BY REMOVING IT: call
   `iqmeasure.extract(..., max_rate=args.out_rate)` so the extractor's brick-wall band-limit and decimation do the
   work (it now narrows the band to 0.9 x output rate when no integer decimation fits). The output rate is then at
   most --out-rate; report the actual rate in rate_sps. Delete the boxcar code. Update the docstring: --out-rate is
   a ceiling, and a band wider than it is narrowed about its centre.
2. [med] Scale on the final stream (follows from 1) and without a full-length float temp: take the 99.9th
   percentile in chunks (for example np.partition of each chunk's top 0.2 % merged, or a histogram of |x| with 4096
   log-spaced bins), accurate to better than 0.1 dB is not needed: 0.5 dB is fine; scale the array in place in
   blocks.
3. [med] A NaN or inf in the data: treat non-finite samples as 0 before scaling (and say so on stderr as a one-line
   warning); never an empty silent result.
4. [med] `--keep PATH` must not overwrite the input recording or any existing file that is not a previous cf32 of
   ours: refuse (exit 1, message) when PATH resolves to the input file, or its sidecar, or exists and is not
   a regular file; overwriting an existing regular file given to --keep that is not the input is allowed.
5. [med] rtl_433's own exit code: a non-zero exit with zero JSON lines is reported on stderr (its stderr tail); a
   timeout is exit 1 with a message. The stdout JSON document keeps exactly the contract's keys; exit stays 0 for a
   completed run whatever the count.
Keep every existing behaviour the tests check. Add nothing to tests (not yours). Run
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py
and test_iq_measure.py. End with a short summary including the 5 s at 2 MS/s wall time.
