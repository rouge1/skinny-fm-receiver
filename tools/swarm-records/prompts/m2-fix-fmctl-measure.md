Review feedback on your `measure` work. Reviewers from other models found these. Apply them. Edit only
tools/fm_receiver/control.py and tools/tests/test_control_more.py (append/extend your own measure checks only). Do not
list or read anything outside the working directory.

FIX LIST:
- [medium] control.py ~1138: `_last_capture` is overwritten even when `_capture_file(reply)` is None, so a failed
  capture wipes the previous good path: assign only when it is not None.
- [medium] tests ~828: assert that the card's `source.path` equals the .cfile `capture` replied with; add a
  second-capture "last wins" check (or two registered files) and a failed-capture-keeps-the-previous check.
- [low] control.py ~1209: `json.loads` of the tool's output raises a raw JSONDecodeError: wrap it as a CommandError in
  the existing error shape.
- [low] control.py ~1196: the timeout path calls kill() then communicate() without a timeout on the GUI thread, and
  abort calls it again: after kill, wait with a short timeout (kill the process group if the child was started in its
  own session), never communicate() twice, so a child that ignores the kill cannot freeze the window.
- [low] tests: add `measure 1e9` / `measure 0` / `measure card 2` refusal or success checks as the behaviour dictates,
  asserting {ok: false, error: <string>} for refusals. A 60 s timeout test is not needed unless the limit can be
  injected cheaply (a module constant monkeypatched in the harness).

Done when both pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control_more.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py
End with a short summary.
