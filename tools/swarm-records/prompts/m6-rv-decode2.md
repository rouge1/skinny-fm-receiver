Task: REVIEW ONLY (do not edit). Second round on tools/iq-decode. Do not list or read anything outside the working
directory. Read tools/iq-decode and CONTRACTS-measure.md (last section). The fix round removed the boxcar stage
(extract now gets max_rate=--out-rate), made the scale chunked, replaced non-finite samples with 0, refused --keep
onto the input, and reported rtl_433's exit code/timeouts. Verify each by a probe in a temp folder
(/home/user/miniconda3/envs/gnu/bin/python, PYTHONPATH=tools): a 2 MS/s file with a tone at 100 kHz and one at
375 kHz, default band: the 375 kHz tone must not appear; --out-rate 250000 with a 300 kS/s file; a NaN sample; --keep
onto the input; the run time of 5 s at 2 MS/s; the test file passes. Findings [high]/[med]/[low] with reproductions.
One-line verdict.
