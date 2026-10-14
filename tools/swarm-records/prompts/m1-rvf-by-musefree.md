You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python.

Another model added fmctl commands to tools/fm_receiver/control.py in this worktree. See it with: git diff main --
tools/fm_receiver/control.py. The spec is CONTRACTS-measure.md section "fmctl additions" (the measure row is phase
m2: not expected) and the tests tools/tests/test_control_more.py and test_control.py (both pass).

Review for:
1. Contract compliance: each command's behaviour and each listed refusal, the replies' JSON shape, and the status
   keys (sweep {start_mhz, stop_mhz}, file, level_unit).
2. Bugs the tests miss: Qt work off the GUI thread, a refusal that still changes state, a delete that can remove
   a file that is not the app's own recording (probe with temporary files only), recordings-folder not going
   through config.update_config, exceptions that escape as a crash instead of the error shape, bad arguments
   (missing, non-numeric, negative, extra).
3. Anything a user driving the open window with tools/fmctl would hit.
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
