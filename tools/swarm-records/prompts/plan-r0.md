THE PLAN UNDER REVIEW (the FM receiver app, repo root = your working directory; read CLAUDE.md, AGENTS.md,
knowledge/workflow.md, knowledge/roadmap.md (Rounds 20-23), tools/fm_receiver/control.py first).

The user's workflow (knowledge/workflow.md): 1 find the signal, 2 record IQ, 3 waterfall pictures of a whole
transmission and of single bursts, 4 detect the modulation, 5 demodulate, 6 detect the encoding, 7 recover the
data. Steps 3-4 must be repeatable measurements that NAME a signal before any decoder is written (roadmap Round 22:
classify, don't decode a decoder per modulation).

What fmctl covers: steps 1-2 (mode, sweep, peakhold, peaks, scan, view, tune, center, rate, gain, agc, record,
capture, status) and the Recordings tab (recordings, recording, play, loop, seek, folders). Not covered: steps 3-7,
and these gaps: open an arbitrary file by path; delete a recording; multi-pick; set the recordings folder; choose the
mini map's box by time and band; export what the waterfall shows; radio/start/stop; filter (channel filter knob);
step; marker (dBm at a frequency); iq-out (Round 21, unbuilt).

Proposed build (file tools that run without the window, numpy only, like tools/dvr-sweep and tools/dvr-to-iq):
 1. tools/iq-measure FILE [--t0 --t1 --f0 --f1]: JSON per signal: occupied bandwidth and where the energy sits,
    envelope levels and timing (keying), instantaneous-frequency levels (FSK), tones, symbol rate, burst length and
    repeat interval, level in dBm.
 2. tools/iq-picture FILE --t0 --t1 --f0 --f1 OUT.png: a spectrogram of a whole transmission or one burst.
 3. tools/iq-demod FILE --mode am|fm|fsk|ook: shift to 0 Hz, filter, decimate; envelope or discriminator out as a
    float file; symbol rate by run-length clustering.
 4. tools/iq-frames: try Morse, UART 8N1, Manchester (both conventions), NRZ after a sync word, POCSAG; agreement
    across repeats, CRC/BCH; hand off to rtl_433 -r for known protocols.
 5. fmctl additions: open PATH, radio, start/stop, filter, step, marker FREQ, iq-out, delete, measure (calls tool 1
    on the last capture).
Test bench: the RF bench toolkit /data/python/SDR makes signals of known parameters (apps/ism_frame.py, ntsc_encode.py,
rds_encode.py, fm_video_core.py): do NOT read outside your working directory; the planner has its own copy of what
matters (see the text below if given).

Rules of this repo that any plan must respect: public repo, no captured data committed; Signal Hound's library is
never committed and never reverse engineered; Linux and macOS from one checkout; tests need no radio
(tools/tests/run_all.py); colours from theme.TOKENS; settings through config.update_config; animate layout changes;
commit trailers. Tools in tools/ are plain Python files without a .py suffix, numpy only (see tools/dvr-sweep).
