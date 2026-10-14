Task: write the acceptance tests for the new tool `tools/iq-decode` (phase m6). STEP 1 only: failing tests, then STOP.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (last section "Decoding with rtl_433"), tools/tests/test_iq_tools.py (the pattern for
running a tool in a subprocess, reporting checks, SKIP, temp folders and writing a .cfile with a .sigmf-meta), and
tools/iq-measure (the tool pattern). Also read tools/swarm-records/specs/m6.md, section "Behaviour": it holds the
exact numbers.

Edit only the NEW file tools/tests/test_iq_decode.py. Same check/report style as test_iq_tools.py. It must print
SKIP and exit 0 when `rtl_433` is not on PATH (shutil.which). Synthetic data only, built in the test (no import of
anything outside tools/ and numpy). tools/iq-decode does not exist yet: the checks must fail until it does (a missing
tool is a failure of the check, not a crash of the whole file).

Fixture frame, built by a helper in the test: Nexus-TH, one row of 36 bits `101101011000000010111110111101000111`;
each bit a 496 us mark then a gap of 1944 us for a 1 and 964 us for a 0; then a trailing 496 us mark; the row is
repeated 12 times with a 4000 us gap after each row's trailing mark (25000 us after the last); 5 ms of silence
before the first; mark edges ramped over 16 us; amplitude 0.4 at 250 kS/s; complex noise 0.003 per component
(seeded rng). A mark is the carrier on (constant phase), a gap is silence.

Checks (run `sys.executable tools/iq-decode FILE ...` with PYTHONPATH tools, parse its stdout JSON):
1. Frame as .cfile + .sigmf-meta (250 kS/s, centre 433.92 MHz): packets >= 1; summary contains model Nexus-TH
   with id 181; decodes[0].temperature_C == 19.0; JSON keys file, center_hz, rate_sps, gain_db, rtl_433,
   protocols, packets, decodes, summary are all present; exit 0.
2. The frame scaled by 0.005 and mixed up by 30 kHz: decoded (packets >= 1, Nexus-TH id 181); gain_db between
   +35 and +45. NEGATIVE CONTROL, must not be skipped: run `rtl_433 -r cf32:FILE -s 250000 -f 433920000 -F json`
   directly on that same scaled file (write the stdout to a FILE, not a pipe) and assert it decodes 0 lines with a
   "model" key. If this control decodes something on this machine, report the check as a failure that says the
   control is invalid.
3. The frame inside a 2 MS/s .cfile (meta: 2e6, centre 433.92e6 - 0 ... choose the frame placed 200 kHz above the
   centre, i.e. at 434.12 MHz), `--band 434.07e6 434.17e6`: decoded; reported center_hz within 1 kHz of 434.12e6.
   The file is 0.5 s long with the frame (about 0.36 s) inside it: build it by resampling by zero-order repeat
   (np.repeat by 8) the 250 kS/s frame, then mixing; noise 0.003 per component added after.
4. Noise-only file: exit 0, packets == 0, decodes == [].
5. Missing file: exit 1 and a message on stderr, nothing on stdout. A Sceptre sweep .sdvr (write a file with that
   extension holding garbage bytes): exit 1, message on stderr.
6. rtl_433 absent: run with env PATH set to an empty temp folder (and the python by absolute path): exit 2, the
   stderr message mentions rtl_433 and how to install it.
7. `--keep PATH` keeps the cf32 file (exists, size > 0); without it no cf32 file is left behind in the temp
   dir (run with TMPDIR set to a fresh folder and assert it is empty afterwards).
8. Source check on the tool: the text of tools/iq-decode contains a stdout redirected to a file for rtl_433 (assert
   `subprocess.PIPE` is not used for rtl_433's stdout: grep the source for `stdout=subprocess.PIPE` and fail if
   found), and the check 1 result is reproducible with 12 packets repeated (packets >= 1 is the bar, do not
   assert 12).
9. Speed: 5 s of the fixture at 2 MS/s (the frame repeated to fill 5 s) decodes in under 10 s wall, packets >= 1.
10. Unit tests of `iqmeasure.extract` (import from fm_receiver) are NOT in this file; another task owns them.

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py
Expected now: every check fails because tools/iq-decode is missing. Also verify the fixture is right: with rtl_433
present, run rtl_433 directly (on an UNscaled 250 kS/s copy, stdout to a file) once in a scratch check and confirm it
decodes Nexus-TH id 181 temperature 19.0, so the positive checks can pass; say in your summary what it printed.
End with a short summary.
