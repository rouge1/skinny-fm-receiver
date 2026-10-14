Review feedback on your fmctl. Independent reviewers from other models reviewed it, and a consensus run verified each
claim against your code. Apply the fix list below. Edit only tools/fm_receiver/control.py. Do not list or read anything outside the working
directory. Tests are off limits.

FIX LIST:
- [medium] tools/fm_receiver/control.py:956-957 `delete` replies ok after `library.delete` reports failures (`app.py:3906-3917` drops them). Call `library.delete` here, collect the failures, and raise CommandError if any remain. (haiku; musefree)
- [medium] tools/fm_receiver/control.py:941-957 `delete` has no recording check. Call `_recording_now(w)` first, as `start`/`stop`/`open` do. (haiku)
- [medium] tools/fm_receiver/control.py:963-967 and 476-478 an empty path makes `abspath('')` the cwd, which passes `isdir` and is set as the recordings folder or added to `folders`. Refuse an empty path before `abspath`. Do not change `open` (its `isfile` check at 923 already refuses the cwd). (haiku)
- [medium] tools/fm_receiver/control.py:883-884 `radio file` with no `iq_file` reaches `app.py:1937-1938`, which opens a modal QFileDialog and blocks the socket. Refuse it before `setCurrentIndex`, with a message to use `open PATH`. (haiku)
- [medium] tools/fm_receiver/control.py:884 and 931 `radio` and `open` run from Recordings, where `app.py:2909-2925` has disabled the widgets and `_live` is set, so the radio opens under Recordings. Refuse when `w._tab_mode() == 'recordings'`. (musefree)
- [medium] tools/fm_receiver/control.py:901-902 `start` in Recordings clicks the disabled `run_btn` (`app.py:2923`), so it does nothing and returns ok with running false. Raise CommandError when the button is not enabled. (musefree)
- [low] tools/fm_receiver/control.py:931-934 `open` sets `cfg['iq_file']` before `_use_radio`, and it stays changed if the radio fails to open. Restore the old value on failure. Severity lowered from high: the path was already validated by `read_iq_metadata`. (haiku; musefree)
- [low] tools/fm_receiver/control.py:901-903 `start` returns ok with running false when the radio does not open. Raise CommandError with the status text, as `radio` does at 886-887. (haiku)
- [low] tools/fm_receiver/control.py:854 `filter nan` passes NaN to `Knob.setValue` (`widgets.py:311`). The error is generic, but the reply is still ok:false. Reject non-finite values with CommandError. (musefree)

Done when all of these pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py
