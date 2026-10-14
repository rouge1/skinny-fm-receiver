Task: fmctl-paths (phase m2). Three items from m1's review that live outside control.py. Tests first, then code.

Read AGENTS.md first and follow it (rule 2 is lifted for tools/tests/test_control_more.py: APPEND tests only, do not
change existing ones). Do not list or read anything outside the working directory. Read CONTRACTS-measure.md
("fmctl additions"), CLAUDE.md, tools/fmctl, tools/fm_receiver/control.py (read-only) and tools/fm_receiver/app.py
(read `_delete_recordings`, and `_tab_changed`, which is read-only), tools/tests/test_control_more.py and
tools/tests/test_control.py (copy their harness).

Edit only:
- tools/fmctl
- tools/fm_receiver/app.py (only the function `_delete_recordings`)
- tools/tests/test_control_more.py (append new checks only)

Do NOT edit tools/fm_receiver/control.py (if a reply needs something there, say so in the summary: the manager hands
it to the next task). Items:
1. tools/fmctl: for the commands `open`, `recordings-folder`, `folders add/remove` (and `measure`, which takes a path
   later), make a relative path argument absolute against fmctl's OWN working directory before it is sent, as it
   already does for `screenshot`, and keep a path with spaces whole (quote it the way the control socket's command
   parser expects; read control.py's parser to see how it splits; `fmctl open "/tmp/a b.cfile"` must reach the window
   as one path). Add a way to test this without a window if one exists; otherwise start the test window the way
   test_control.py does and call fmctl as a subprocess from another cwd. Required tests (gate G3b): `fmctl open` of a
   relative path and of a path containing a space works from another working directory.
2. app.py `_delete_recordings` must return what it deleted (a list of the paths or names it removed, empty if none)
   so a later change to control.py can reply it for `delete N confirm`; keep the current behaviour otherwise (the
   Recordings tab's own delete). Add a check that calls it through the existing test harness if feasible.
3. cmd_mode's second setCurrentIndex retry in control.py: only add a TEST that tries to prove the radio can be opened
   twice (count opens through the harness); if the test passes, nothing is wrong, keep it as a regression check; if
   it fails, do not fix it (control.py is not yours): describe it in the summary.

Done when both pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py
(an item-3 failure the summary explains is acceptable: say so). End with a short summary: files changed, what you did,
test results, anything you could not do.
