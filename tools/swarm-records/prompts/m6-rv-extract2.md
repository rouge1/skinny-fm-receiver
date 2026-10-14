Task: REVIEW ONLY (do not edit anything). Second round on `extract` in tools/fm_receiver/iqmeasure.py. Do not list or
read anything outside the working directory. Read AGENTS.md and CONTRACTS-measure.md (last section "Decoding with
rtl_433"). The fix round changed the decimation rule: dec = largest integer with rate/dec >= 2 x width; if the rate is
then above max_rate, dec = ceil(rate/max_rate) and, if that gives rate/dec < 2 x width, the effective band is narrowed
to rate/dec about the same centre. max_rate must be finite and > 0. Verify with probes in a temp folder (python at
/home/user/miniconda3/envs/gnu/bin/python, PYTHONPATH=tools): rates 2.4e6, 2.048e6, 1e6, 250e3, 28e6 with band None and
with narrow and wide bands; the output rate is in [2 x width, max_rate] whenever an integer fits; tones land where
expected; no read over 2**22; no regression of `measure_file`. List findings [high]/[med]/[low] with a reproduction.
One-line verdict.
