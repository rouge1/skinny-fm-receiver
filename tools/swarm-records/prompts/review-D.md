You are a REVIEWER on FM Receiver. Do not edit, create or delete any file. Do not run git commands that change
state. Do not list or read anything outside the working directory. Short probes with
/home/user/miniconda3/envs/gnu/bin/python are allowed; do not run the GUI suites.

Another model documented Phase 3 (spectrum recordings played without sound) and wired its tests. See:
git diff main -- tools/tests/run_all.py knowledge .agents
Check every claim in the added text against the code (tools/fm_receiver/spectra.py, library.py, app.py, CONTRACTS.md,
tools/swarm-records/specs/p3.md): behaviour described that the code does not do, status marks that over-claim (the
real 1 GB file timings are NOT measured), roadmap boxes ticked for work that did not ship, wrong test names, the
run_all.py count and test file names. Also stale statements left behind (for example text that still says a sweep
DVR is passed over).

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
