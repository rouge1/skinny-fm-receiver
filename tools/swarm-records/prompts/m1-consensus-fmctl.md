You are the CONSENSUS reviewer on the FM receiver. Read-only: do not edit, create or delete files, do not run git
commands that change state, and do not list or read anything outside the working directory. You may run the tests
and short probes.

Two reviewers from different models reviewed the fmctl additions in tools/fm_receiver/control.py (spec:
CONTRACTS-measure.md section "fmctl additions"; the measure row is phase m2 and not expected; tests
tools/tests/test_control_more.py and test_control.py pass). Reviewers sometimes misread code, so VERIFY every claim
against the code before keeping it (see the change with git diff main -- tools/fm_receiver/control.py). Quote the
file:line you checked. Reject claims that are false, that ask beyond what the contract table says, or that are
out of scope (for example changes to tools/fmctl or app.py: list those as "out of files" instead of the fix list,
since the author may edit only control.py). Keep severity honest.

REVIEWS:
--- m1-rvf-by-haiku
verdict: changes
issues:
- [medium] tools/fm_receiver/control.py:956-957 `delete` replies `deleted` even when `library.delete` failed (`_delete_recordings` only writes to `lib_note` and returns nothing). Make `_delete_recordings` return `failed`, and raise a CommandError with that text when it is non-empty.
- [medium] tools/fm_receiver/control.py:941-956 `delete` has no recording check, so it can unlink the file being written by Record (the live recording is in the list). Refuse with `_recording_now(w)` first, as `open`, `start` and `stop` do.
- [medium] tools/fm_receiver/control.py:965 `recordings-folder ""` makes `os.path.abspath('')` the window's cwd and sets it as the recordings folder. Refuse an empty path before `abspath`, and do the same in `open` (line 922) and `folders`.
- [medium] tools/fmctl:23-28 only makes `screenshot` paths absolute, and `main` joins argv with spaces. So `open`, `recordings-folder` and `folders` take a relative path against the window's cwd, and a path with spaces breaks the command. Quote and absolutise the path argument for those commands, as `screenshot` does.
- [low] tools/fm_receiver/control.py:884-890 `radio file` with no file set opens a modal QFileDialog inside the command, so the queue waits on a human. Refuse it with a message that says to use `open PATH`, or pass `start=False` without the dialog.
- [low] tools/fm_receiver/control.py:931-932 `open` sets `w.cfg['iq_file']` before `_use_radio`. If the radio fails to open, the previous radio is already closed and the bad path stays in the settings. Check the file opens before changing the setting, or restore it on failure.
- [low] tools/fm_receiver/control.py:897-903 `start` returns `ok: true` with `running: false` when the radio fails to open, which hides the failure. Raise CommandError with the status text, as `radio` does at line 886-887.
- [low] tools/fm_receiver/control.py:389-391 the second `setCurrentIndex` in `cmd_mode` can reopen the radio twice. Find the cause in `_tab_changed` and fix it there, rather than retrying the tab switch.
--- m1-rvf-by-musefree
verdict: changes
issues:
- [high] tools/fm_receiver/control.py:931 open sets cfg iq_file before _use_radio then raises, so a refused open leaves iq_file changed; assign only after success
- [high] tools/fm_receiver/control.py:884 radio (and open/start) run from Recordings while widgets are disabled, opening a radio with _live set and corrupting enter/leave bookkeeping; refuse outside Receive/Sweep
- [medium] tools/fm_receiver/control.py:901 start clicks disabled Run button in Recordings and returns ok with running False without starting; refuse when not startable
- [medium] tools/fm_receiver/control.py:390 mode forces Sweep tab for file radio that cannot sweep, leaving tab sweep with mode receive; refuse sweep for file
- [medium] tools/fm_receiver/control.py:956 delete returns ok even when library.delete reports unremoved files and still drops selection; return the failures as an error
- [medium] tools/fm_receiver/control.py:858 filter passes NaN into Knob.setValue raising ValueError (generic error, not CommandError); reject non-finite numbers


Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
fix list (for the author):
- [high|medium|low] <file:line> <one line: what to change> (source)
out of files:
- <claim that needs tools/fmctl or app.py>
rejected:
- <one line: the claim and why it's wrong> (source)
