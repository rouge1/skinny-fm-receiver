Task: REVIEW ONLY (do not edit anything). Review the new test file tools/tests/test_iq_decode.py in this worktree.

Do not list or read anything outside the working directory. Read AGENTS.md, CONTRACTS-measure.md (last section
"Decoding with rtl_433"), tools/swarm-records/specs/m6.md (section "Behaviour" and "Gates") and tools/iq-measure.
The tool tools/iq-decode does not exist yet; it will be written to the contract. Judge whether the tests (a) assert
exactly what the spec says, (b) would pass for a correct implementation (check the fixture's timings, scaling, the
gain bounds, the --band numbers, rates, the negative control being independent of the tool), (c) would fail for a
wrong one (not vacuous; no test that passes with the tool missing; no pipe for rtl_433 stdout), (d) are
deterministic and quick, leave no files behind, and skip properly when rtl_433 is missing. You may run
rtl_433 (/usr/bin/rtl_433) on files you generate in a temp folder via the python at
/home/user/miniconda3/envs/gnu/bin/python to confirm the fixture decodes. List findings as [high] (test would fail a
correct tool or pass a broken one), [med], [low], each with file:line and a concrete fix. End with a one-line verdict.
