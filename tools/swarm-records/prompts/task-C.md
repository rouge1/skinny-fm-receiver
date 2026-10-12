Task: play a Sceptre spectrum recording (a sweep DVR 'dvr-sweep' or a .fft 'fft' track) in the Recordings tab with
no sound, no engine and no radio. This is part 2 (`gui`) of tools/tests/test_sceptre_spectra.py.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS.md (section "tools/fm_receiver/app.py (and control.py)", and the library.py and spectra.py sections),
CLAUDE.md, tools/fm_receiver/app.py (how a Recordings track is chosen, drawn (waterfall from the file, mini map,
rf_view, level_unit) and played: _play, _playhead, _seek_to, the Play/Pause button, Loop, the end-of-file handling,
how IQ playback is started and stopped), tools/fm_receiver/library.py (merged: Track.is_spectrum, render, extent;
do not edit it), tools/fm_receiver/spectra.py (merged; do not edit), tools/fm_receiver/control.py (status and
playback reports), and the acceptance test tools/tests/test_sceptre_spectra.py (`gui`; part 1 already passes). The
tests are the spec: do not edit any test.

Edit only:
- tools/fm_receiver/app.py: choosing and playing a spectrum track
- tools/fm_receiver/control.py: only if `fmctl status` / `playback` needs a change to report it like the others
- tools/fm_receiver/widgets.py: only where a view needs it

Behaviour (the contract): choosing a spectrum track draws it like any other. Play on one opens NO engine and NO
radio (engine.running stays False, engine.radio None). A timer (about 25 Hz) moves the playhead in real time from
where it is; _playhead() reads it; rows of the waterfall and the trace come from the file at the playhead, in dBm
(rf_view.level_unit 'dBm'); the audio box does nothing. Pause holds. _seek_to jumps, paused or playing. At the end
it stops (Play button unchecked and reading 'Play', playhead on the end) unless Loop is on, when it starts again
from 0. Leaving the tab or choosing another recording stops it like any playback. fmctl status/playback reports
recording, file, playing, position and duration like the other tracks. Read the spectrum from the file with
library.render / spectra (only what is drawn: never the whole file); the file is never written.

Constraints: the IQ files (own WAV/IQ, .sdvr IQ, .cdif) must play exactly as before. Guard every Qt paint override;
colours from theme.TOKENS; no UI state jumps (fade/glide as the surrounding code does for things that appear).
Match the surrounding code's style. No new dependencies.

Done when all of these pass (GUI tests take minutes: run each by itself, with a generous timeout):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_spectra.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_library.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_recordings.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_control.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_gui.py
End with a short summary: files changed, what you did, the test results, anything you could not do.
