You are a REVIEWER on FM Receiver (PyQt5 + numpy app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with /home/user/miniconda3/envs/gnu/bin/python (PYTHONPATH=tools, QT_QPA_PLATFORM=offscreen).

Another model changed tools/fm_receiver/library.py on this branch so Sceptre spectrum recordings (a sweep DVR
.sdvr of tiles, and a spectrum .fft) are listed in the Recordings library and drawn by library.render. See the
change with: git diff main -- tools/fm_receiver/library.py
Spec and tests: CONTRACTS.md (section library.py), tools/swarm-records/specs/p3.md (including its 2026-10-12
Amendment: a render tie goes to the earlier sweep), tools/tests/test_sceptre_spectra.py (part 1, `pieces`; part 2
needs app.py and fails for now: not an issue), tools/tests/test_sceptre_library.py. spectra.py is the merged reader.

Review for:
1. Spec compliance: track kinds 'dvr-sweep' and 'fft'; Track.is_spectrum/is_iq; GROUPS' fourth title; an IQ DVR with
   IQ stays an IQ track and gets a sweep track only when it has no IQ or its tiles span more than 1.5 times the IQ
   rate (both tracks in one Recording, group 'Sceptre IQ DVR'); kinds_text; extent; no file name in row text (tooltip
   only); started time from the header with the mtime fallback; delete refuses; nothing written.
2. render for a spectrum track: slot-middle nearest sweep with the tie to the earlier one, NaN outside [0, seconds),
   cols None = the file's own bins (max-thinned to at most 16384), pooling by max, returned freqs; float32 dtype.
   Probe edge cases (t0 < 0, t1 beyond the end, rows > sweeps, rows == 1, a band narrower than cols, cols > bins).
3. Performance: listing reads headers only and caches them; render reads only the tiles and bins it needs (a 1 GB
   ring with 54 tiles of 849 sweeps and 1.2 million bins: a 30 s stretch at 8 rows x 2048 cols must be fast).
4. Regressions for the IQ files: anything that changes what the IQ DVR/.cdif/own-WAV paths do.
5. Contract drift (names, fields: seconds, start, center_hz, rate None, error, layout, external).
Don't report style nits unless they hide a bug.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
