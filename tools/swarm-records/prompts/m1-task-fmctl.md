Task: add the fmctl commands filter, step, radio, start, stop, open, delete, recordings-folder, marker, and the new
status keys, to the control socket in tools/fm_receiver/control.py.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (section "fmctl additions": the table is the spec; the `measure` row is phase m2, do
NOT implement it), CLAUDE.md, tools/fm_receiver/control.py (the existing commands, reply and error shapes),
tools/fm_receiver/app.py (the knobs, the Run button, the recordings list; read only), tools/fm_receiver/library.py
(delete), tools/fm_receiver/config.py, and the acceptance tests tools/tests/test_control_more.py and
tools/tests/test_control.py. The tests are the spec: do not edit any test.

Edit only:
- tools/fm_receiver/control.py

Constraints: do not edit app.py; if it needs a helper, say so in your final summary instead. Every command runs
its Qt work the way the existing commands do (on the GUI thread, as control.py already arranges). Refusals use the
existing error shape and, where the contract says the error lists values, list them. Settings are written only
through config.update_config. Update the help text so the new commands are listed. Match the surrounding style.

Done when both pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py

End with a short summary: files changed, what you did, the test results, anything you could not do.
