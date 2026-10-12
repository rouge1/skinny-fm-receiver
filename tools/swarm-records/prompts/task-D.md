Task: document Phase 3 (spectrum recordings played without sound) and wire its two tests into run_all.py.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CLAUDE.md (the Knowledge paragraph: capabilities.md has status marks, usage.md is the user guide, roadmap.md
holds planned work in phases to tick), CONTRACTS.md, tools/swarm-records/specs/p3.md, tools/fm_receiver/spectra.py,
the Phase 3 changes in tools/fm_receiver/library.py and app.py (`git log --oneline -8`, `git diff 8d4cb5c -- tools/fm_receiver`),
and tools/tests/run_all.py.

Edit only:
- tools/tests/run_all.py: add test_spectra.py and test_sceptre_spectra.py to TESTS (after test_sceptre_library.py)
  and update the docstring's count and time estimate ("the twelve" is already stale: count the list).
- knowledge/usage.md: how to play a sweep DVR or a Sceptre spectrum .fft from the Recordings tab (listed under
  "Sceptre spectrum", no sound, no radio, dBm, Loop, seek).
- knowledge/capabilities.md: add the capability with its status mark. It is implemented and checked by the
  synthetic tests test_spectra.py and test_sceptre_spectra.py; the real-file timing (a 1 GB ring) is NOT yet
  measured, so do not mark it verified on a real file.
- knowledge/roadmap.md: tick the Phase 3 items that shipped (read the phase's list first; leave the unshipped ones).
- .agents/skills/fm-receiver/SKILL.md: only where it lists modules or tests (add spectra.py and the two tests, one
  line each).
Match the surrounding tone and format. Keep it short; no new files. Do not edit code under tools/fm_receiver.

Done when: `/home/user/miniconda3/envs/gnu/bin/python -c "import ast,sys;ast.parse(open('tools/tests/run_all.py').read())"`
passes, and the run_all.py list names two existing files. End with a short summary.
