Review feedback on your library change. Independent reviewers from other models reviewed it, and a consensus run
verified each claim. Apply the fix list below. Edit only tools/fm_receiver/library.py. Do not list or read anything
outside the working directory. Tests and contracts are off limits.

FIX LIST:
- [low] tools/fm_receiver/library.py:297 a .sdvr with both an IQ track and a sweep track lists its path twice in
  files() and counts its bytes twice: dedupe files() and the size by path.
- [low] tools/fm_receiver/library.py:635 `_render_spectrum` makes one spec.read per run of contiguous wanted rows,
  re-reading a whole tile per run: group the wanted sweeps by tile (k // sweeps per tile) so each tile is read at
  most once per render (one read covering the wanted rows of a tile is fine).

Done when these pass:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_sceptre_library.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_recordings.py
and tools/tests/test_sceptre_spectra.py prints "part 1 passed" (part 2 needs app.py: not yours).
End with a short summary.
