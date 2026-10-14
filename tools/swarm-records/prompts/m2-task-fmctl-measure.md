Task: fmctl-measure (phase m2): add the `measure [SECONDS] [card]` command to the control socket. Tests first, then code.

Read AGENTS.md first and follow it (rule 2 is lifted for tools/tests/test_control_more.py: APPEND new checks only, do
not change existing ones). Do not list or read anything outside the working directory. Read CONTRACTS-measure.md (the
fmctl table's `measure` row and "Tools"), CLAUDE.md, tools/fm_receiver/control.py (the existing commands, reply and
error shapes, how `capture` records and replies its path, how long-running commands avoid freezing the window),
tools/iq-measure (the tool to run), tools/fm_receiver/iqmeasure.py (read-only) and tools/tests/test_control_more.py
and tools/tests/test_control.py (the harness; how a test makes a capture without a radio, or writes a .cfile and
tells the window it is the last capture).

Edit only:
- tools/fm_receiver/control.py
- tools/tests/test_control_more.py (append only)

Behaviour: `measure [SECONDS] [card]` runs tools/iq-measure (located relative to control.py's own folder: tools/) on
the LAST capture, the path `capture` replied with (remember it in the control state when capture finishes), as a
subprocess with sys.executable and a 60 s limit, with `--t0 0 --t1 SECONDS` when SECONDS is given, and `--card` when
the word `card` is given (either order). It replies the card (the tool's JSON, as the reply's data, in the existing
reply shape; add `card_file` with the written path when card was asked and the file exists). Refuses (existing error
shape): no capture yet; the capture's file is gone; a bad SECONDS (not a positive number); the tool exits non-zero
(its stderr message in the error); the 60 s limit (kill the subprocess). `card` on a capture that is not a .cfile
is the tool's refusal, passed through. It must not freeze the GUI thread for the duration (follow what `capture` or
`wait` do). Add it to the help text. A `.cfile` capture written by this app has its .sigmf-meta: no --rate needed.

Tests (append): after `capture 2 iq-band` (or the harness equivalent without a radio; if no radio is possible, write
a synthetic .cfile + .sigmf-meta in a temp folder and register it as the last capture the way the existing tests do),
`measure` replies a card with schema 1 and a class; `measure 1`'s source t1_s is about 1; `measure card` leaves
<base>.card.json beside the .cfile equal to the reply's card; no capture yet is refused; a missing file is refused;
bad SECONDS refused. Do not run anything outside the temp folder.

Done when both pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py
End with a short summary: files changed, what you did, test results, anything you could not do.
