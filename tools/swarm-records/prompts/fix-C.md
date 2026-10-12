Review feedback on your spectrum playback change. Independent reviewers from other models reviewed it, and a
consensus run verified each claim. Apply the fix list below. Edit only tools/fm_receiver/app.py (and control.py or
widgets.py only if a fix truly needs it). Do not list or read anything outside the working directory. Tests and
contracts are off limits.

FIX LIST:
- [medium] app.py:3538-3542 `_play_ended` calls `_seek_to(0.0)`, so the playhead jumps to 0 at the end. For a
  spectrum track it must stay on the end (`track.seconds`, the contract says "Play, playhead on the end"); Play
  pressed again at the end restarts from 0.
- [medium] app.py:3574 `_draw_spectrum` reads with max_cols=view.WF_COLS (4096) but library.render's stretch pools
  only above library.SPECTRUM_MAX_COLS (16384): for 4097 to 16384-bin sweeps the extents differ and the stretch just
  drawn is wiped on the first live row. Read with max_cols=library.SPECTRUM_MAX_COLS (or make both agree).
- [low] app.py:3370-3387 `_open_spectrum` must call `view.set_pan_limits(None, None)` as `_run_receive` does, so the
  Sweep tab's 0-6 GHz pan limits do not clamp a spectrum file outside that band.
- [low] app.py:3193-3198 `_prepare_view` requests the first stretch but `_open_spectrum` -> `_follow_clock` overrides
  it with a stretch at 0: fix the stretch or the comment so what is drawn at Play is what the comment says.

Done when these pass, each by itself:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_spectra.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_recordings.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_library.py
End with a short summary.
