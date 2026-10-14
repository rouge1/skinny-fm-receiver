Task: REVIEW ONLY (do not edit anything). Review `IQSlice` and `extract` added to tools/fm_receiver/iqmeasure.py and
the new checks appended to tools/tests/test_iq_measure.py in this worktree (git diff main shows them if you can run
git; otherwise read the end of both files).

Do not list or read anything outside the working directory. Read AGENTS.md, CONTRACTS-measure.md (last section
"Decoding with rtl_433"), tools/swarm-records/specs/m6.md. Check against the contract: band semantics (absolute Hz,
None -> whole file or +/-250 kHz), the output rate between 2x band and max_rate, center_hz, chunked reads (<= 2**22
per read, no whole-span arrays at input rate, a low-rate file too), the 50 M output sample limit raising IQSourceError
before reading, t0/t1 handling and the returned t0/t1, edge cases (t1 beyond the file, t0 >= t1, band outside the
file, band wider than max_rate allows, a band narrower than a few bins, dec computed so the output rate is
>= 2 x band), dtype complex64, and that existing code is unchanged. Run the test file with
QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
and probe edge cases with small scripts in a temp folder. List findings as [high] (wrong result or crash on a
legitimate input), [med], [low], each with file:line, a reproduction and a concrete fix. End with a one-line verdict.
