You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and short
probes with QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python.

Another model changed tools/fmctl, the function `_delete_recordings` of tools/fm_receiver/app.py, and appended checks to
tools/tests/test_control_more.py (see git diff main -- tools/fmctl tools/fm_receiver/app.py, and the appended tests).
The task: (1) fmctl makes a relative path absolute against its own cwd and keeps a quoted path with spaces whole for
`open`, `recordings-folder`, `folders add/remove`, `screenshot` (and a later `measure`); (2) `_delete_recordings`
returns what it deleted, keeping the Recordings tab's behaviour; (3) a test (only) of whether cmd_mode's second
setCurrentIndex retry can open the radio twice. Read tools/fm_receiver/control.py (read-only: how it parses a command
line) and CONTRACTS-measure.md. Run tools/tests/test_control_more.py and test_control.py.
Review for:
1. Regressions of fmctl's old behaviour: `fmctl 'tune 99.1; wait 3; status'` (one argument as a script), several
   arguments as one command, commands with quotes or a backslash or `;` in a path or a string argument (for example
   `screenshot`, `rds`, any command taking free text), an apostrophe in a path, an empty argument, a Windows-like path,
   `~`. Does the line fmctl sends reach control.py's parser as the same words (including a path with a space, quotes,
   or a `;`)? Probe it with the parser's own function if there is one.
2. The `_delete_recordings` change: callers that used it before, the return value for Sceptre/external files that
   cannot be deleted, exceptions.
3. Test quality: the appended tests prove G3b (relative path and a path with a space, from another working
   directory), not just that something ran; a test that cannot fail.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
