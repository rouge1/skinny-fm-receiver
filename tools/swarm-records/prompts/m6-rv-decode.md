Task: REVIEW ONLY (do not edit anything). Review the new tool tools/iq-decode in this worktree. Do not list or read
anything outside the working directory. Read AGENTS.md, CONTRACTS-measure.md (last section "Decoding with rtl_433"),
tools/swarm-records/specs/m6.md, tools/iq-decode, `extract` in tools/fm_receiver/iqmeasure.py, and
tools/tests/test_iq_decode.py. Check against the contract: scaling to the 99.9th percentile 0.5 and gain_db,
cf32 output and rtl_433 command line (-r cf32:FILE -s RATE -f CENTER -F json -M level [-R]), stdout to a FILE not a
pipe, stderr handling and the version line, timeout, temp dir cleanup on every path (including exceptions and
timeouts), exit codes 0/1/2, no stdout noise on errors, resample to --out-rate (integer decimation correctness,
anti-aliasing, a rate that is not a multiple, a signal at the band edge), memory (no whole-file array above the output
size), numpy-only, Linux and macOS portability, and the investigation paragraph in the docstring: is it honest
(measured, or "not reproduced")? Probe with small scripts in a temp folder using
/home/user/miniconda3/envs/gnu/bin/python and PYTHONPATH=tools; run the test with
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py.
List findings as [high] (wrong result or crash on a legitimate input), [med], [low], each with file:line, a
reproduction and a fix. One-line verdict.
