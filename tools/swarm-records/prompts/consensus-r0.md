You are the CONSENSUS reviewer on the FM receiver measurement-tools plan (phase r0). Read-only: do not edit, create or delete files, do not run git commands that change state, and do not list or read anything outside the working directory. You may run short probes.

Do not list or read anything outside the working directory.

The plan under review is in the file prompts-copy below (pasted in full). Three independent reviewers from different models reviewed it with different lenses: A completeness (grokw), B control surface / fmctl (musefree), C architecture and reuse (haiku). Reviewers sometimes misread code, so VERIFY every claim against the code in the working directory (and the workflow text in the plan) before keeping it. Quote the file:line you checked. Reject claims that are false, that are out of scope, or that the code contradicts. Merge duplicates. Keep severity honest.

THE PLAN:
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

REVIEW A (grokw, completeness):
I'll review the plan against the workflow by reading the repo docs and the recording/control code first.verdict: plan-changes
missing_tools:
- iq-load: one reader for `.cfile`+`.sigmf-meta`/`.json`, IQ and sweep `.sdvr`, `.cdif`, `.fft`, and WAV, returning rate, centre, a time/frequency slice, and a stated level scale. Steps 3–4 cannot name a level in dBm until this exists. Reuse `sceptre_dvr.scan`/`Reader`/`Sweeps.read(dbm=True)`, `sceptre_blue.Blue`, `radios.BlueReader` (`dBm = 10*log10(|x|**2) - DATA_GAIN`, scale `10**(-DATA_GAIN/20)`), `dvr_iq.Channel.full_scale_dbm` (`20*log10(scale*FULL_SCALE)`), `library.render`/`wav_frames`.
- iq-card: write one JSON signal card per measured signal (occupied bandwidth, envelope levels and timing, instantaneous-frequency levels, tones, symbol rate, burst edges, repeat interval, level and scale name). Workflow steps 3–4 are comparable only if the card is kept. Put SigMF `annotations` on this app’s own `.cfile` (`recording.write_iq_metadata` currently writes `annotations: []`). Never write beside a Sceptre file.
- iq-compare: diff two cards (bandwidth, symbol rate, burst interval, dBm). Needed once more than one signal is named.
- known-signal fixture inside the repo: numpy pulse trains (OOK, PPM, PWM, Manchester, both edges) plus the existing FM+RDS path, written only under a temp dir. `tools/tests/signals.py` already builds MPX at 250 kHz, 75 kHz deviation, and RDS; it has no ISM keying. Tests must not import `/data/python/SDR`.
change_to_plan:
- [high] Ship order: `iq-load` + in-repo fixtures, then `iq-measure` (must emit burst `t0/t1` and a card, and a folder batch), then `iq-picture`, then `iq-compare`. `fmctl measure` only wraps the last card. Demod and frames come after a card names a class (Round 22).
- [high] Drop `iq-frames` from this build. One tool that tries Morse, UART 8N1, both Manchester conventions, NRZ-after-sync, POCSAG, and `rtl_433 -r` is a decoder per protocol. `rtl_433` is not numpy-only. Split later, one framing per named class.
- [high] `iq-measure` must report the scale, not a bare dBm number. Verified: sweep tiles and `.fft` are dBm; `.cdif` is dBm only after `DATA_GAIN`; IQ `.sdvr` has `full_scale_dbm` but `library.render` draws IQ as dBFS (`scale = 1.0`); app `.cfile` is dBFS unless `fmrx:full_scale_dbm` was copied by `dvr_iq.write_iq`; WAV is int16/32768 dBFS. Roadmap still has “Show dBm in the app for a DVR” open.
- [medium] `iq-picture` should call `library.render(..., band=)` and `dvr-sweep.picture` (matplotlib, 1600-column peak pool). A third spectrogram will disagree with the Recordings waterfall. `fmctl view rf window|span|center` plus `seek` already sets the mini-map box (window 2–300 s); add an export of that box, not a new time/band command.
- [medium] Keep symbol-rate run-length clustering in `iq-measure` only. Narrow `iq-demod` to shift, filter, decimate, envelope or discriminator, and only `ook|fsk|fm|am` after the card says so. PPM/PWM/PSK/NTSC stay measurements until a class is worth a decoder.
- [low] Park `fmctl iq-out` on Round 21 (ZMQ / rtl_tcp / pipe). It does not advance steps 3–7. `open PATH`, `delete`, `radio`/`start`/`stop`, `filter`, `step`, and `marker FREQ` are real control gaps; `marker` waits on the same dBm scale as `iq-load`. `folders` already exists.
risks:
- A card written next to `.sdvr`/`.cdif`/`.fft` breaks the rule that nothing is written beside a Sceptre file.
- Using `library.render`’s dBFS for a DVR or `.cdif` will publish a wrong dBm and make cards incomparable.
- `iq-demod` and `iq-frames` built before burst edges exist will be tuned by eye and will not be a repeatable measurement.
- `dvr-sweep` already imports matplotlib for PNG, so “numpy only” is false for pictures; follow that split rather than adding a new image library.

REVIEW B (musefree, control surface):
verdict: plan-changes
missing_tools:
- marker FREQ: dBm/dBFS at an arbitrary frequency from the live trace (`widgets.SpectrumView._x/_db/_peak`, readout at `widgets.py:3314`); `peaks` is thresholded top-20 only, so a measurement session cannot name a weak tone's level. Read-only probe, reuses `find_peaks` floor in `control.py:680`.
- snap on|off + stereo on|off + clear-rds: `snap_check`, `stereo_check` (`app.py:992-1000`), `clear_btn` (`app.py:1024`); all change what tune/hear/decode report, none in COMMANDS. Cheap `setChecked` like `mute`.
- sweep-extra: `rbw_combo`, `sweep_rate_combo`/`fft_combo`, `preset_combo`, `rt_btn`, `pause_btn`, `listen_btn`, `recenter_btn` (`app.py:663-786,857`); `sweep START STOP` sets bounds only. Needed to repeat a survey at same RBW/preset/pause state.
- open PATH + radio KIND + start/stop: `file_btn/_choose_file`, `radio_combo/_radio_chosen`, `run_btn` (`app.py:528-559`); no way to load an arbitrary file or (re)open a radio without the mouse.
- delete N + recordings-folder set: `rec_list/RevealList.deleteRequested→_delete_keys`, `delete_btn/_delete_clicked`, `config.recording_dir` (`app.py:1471-1536`); `folders` handles only library folders, never the recordings dir.
- box TIME BAND: mini-map box edges/sides (`widgets.MiniMap.window_hz/position/span`, `control.py:217-223` reads it); `seek`+`view window` set time only, band side-drag has no command, so a script cannot set the exact waterfall export box.
- rtl433 controls: Receive RTL433 Band preset/Width/Options/Log/devices tab (Round 18, `app.py:663+`); VERIFIED no `rtl*` key in `COMMANDS`. INFER: needed only if measurement uses live ISM decode; file tools + `rtl_433 -r` cover offline.
change_to_plan:
- [high] Add read-only `marker FREQ` and `view`-mirroring of map box band before `measure`; otherwise step 3-4 pictures/numbers are not repeatable from a script.
- [high] Scope `delete` narrowly: own recordings only, explicit index, refuse Sceptre library folders (their tooltip at `app.py:1531` already promises no delete), confirm flag; decide trash vs unlink per Linux/macOS rule. INFER: unlink with confirmation is enough.
- [medium] Merge `open PATH` with radio-stopped + not-recording guards (copy `scan`'s guards at `control.py:791-794`); validate path, refuse while `rec_btn` checked. Cheap but needs the design decision the plan skips.
- [medium] Keep `filter`/`step` (VERIFIED cheap: `chan_knob` 60-400kHz, `step_knob` STEPS_KHZ 10/50/100/200kHz at `app.py:902-944`), drop or defer `iq-out` server work from this round: Round 21's ZMQ/rtl_tcp/pipe (~2-3h) is not on workflow steps 3-4; `capture` already feeds file tools.
- [low] Add `status` fields first (sweep bounds/RBW, pause/realtime, radio list, stereo/snap, track/file path, sample-rate/center, level unit, view shown spans); `measure` on last capture depends on it.
risks:
- `status` cannot tell a script what it looks at: no sweep RBW/bounds, no file/track path, no rate/center of playback, no level unit (dBFS vs dBm/`full_scale_dbm`), no marker value.
- File tools need nothing from the window except paths+meta (`capture`/`record stop` reply gives center/rate/files); marker and view box are display-only (`view ref/range` display-only per `control.py:613`) but export must reuse `library.render` or colours diverge from `theme.TOKENS`.
- `radio`/`open` mid-record retunes mid-file: enforce stop-first or recordings corrupt; second window has no socket (`control.py:1000-1017`).

REVIEW C (haiku, architecture and reuse):
verdict: plan-changes
missing_tools:
- iqmeasure (new library module, `tools/fm_receiver/iqmeasure.py`, numpy only): the shared measurement core that `iq-measure`, `iq-picture`, `iq-demod`, `iq-frames` and `fmctl measure` all call. Steps 3-4 need it, and nothing in the repo measures occupied bandwidth, keying, FSK levels, symbol rate or burst timing (verified by grep). Reuse the chunked `.cfile` read from `radios.iq_reader`/`read_iq_metadata`, the Blackman-Harris window and `_pool` from `library.py`, and the shift/filter/decimate pattern in `rds_core.fm_demodulate` and `dvr_iq.write_iq`.
- iq-picture (`tools/iq-picture` wrapper): the spectrogram. Reuse `library._render_spectrum`'s row/slot logic, but only after the numpy-only part is split out of `library.py` (see below).
- iq-frames (`tools/iq-frames`): Morse, UART, Manchester, NRZ and POCSAG decoders, with a hand-off to `rtl_433 -r`. Reuse `rds_core.syndrome`/`correct_block` for the CRC/BCH check pattern. Build last, after classification exists.
- fmctl `open`, `radio`, `start`, `stop`, `filter`, `step`, `marker`, `iq-out`, `delete`, `measure`: most map to existing `control.py` commands, so they are thin. `delete` should call `library.delete` (line 540); `measure` should run in a subprocess, not on the Qt thread.
change_to_plan:
- [high] Fix the "numpy only" claim. Verified: `library.py` imports `radios`, which imports `gnuradio` at module level (radios.py:38), so `library.render` cannot run without GNU Radio. `dvr_iq.py` and `rds_core.py` import scipy (environment.yml pins scipy=1.15, so the gnu env has it). Either the new numpy core must not import `library`, `radios` or `dsp`, or the plan states that the tools need the gnu env. Move `blackman_harris`, `_pool` and the render core into the new numpy-only module, and have `library.render` import them.
- [high] Module layout: `tools/fm_receiver/iqmeasure.py` (numpy only: `read_chunks(path, meta)` via memmap, `occupied_bandwidth`, `energy_centroid`, `envelope_levels`, `keying_runs`, `inst_freq_levels`, `tones`, `symbol_rate`, `bursts`, `level_dbm`), plus a `tools/fm_receiver/iqdemod.py` for shift/filter/decimate. Wrappers in `tools/` are thin `sys.path.insert` scripts, as `tools/dvr-sweep` and `tools/dvr-to-iq` are. Keep `iqmeasure.py` out of `dsp.py`, which needs GNU Radio.
- [high] Reuse, don't duplicate: `beacon_hunt.py` already does a constant-envelope check and a 2-FSK preamble search in numpy. Its logic should move into `iqmeasure` and `beacon_hunt` should import it, or the plan should say it is left alone on purpose. `rds_core.fm_demodulate` is the discriminator to reuse for FSK, not a second copy.
- [high] dBm: `sceptre_dvr` gives `full_scale_dbm` for DVR runs; `.cfile` recordings from this app need `recording.write_iq_metadata`'s fields. Without a full-scale value, `level_dbm` must say "dBFS" and never print dBm.
- [high] Test plan (no radio, no captured data): a new `tools/tests/test_iq_measure.py` (added to `TESTS` in `run_all.py`; its docstring says "fifteen", update it). Its synthetic generators (OOK at known symbol rate, 2-FSK at known deviation, a Manchester burst, a tone pair, a noise floor at known dB) write `.cfile` + `.sigmf-meta` into a temp folder. Asserts: occupied bandwidth within 5% of truth, symbol rate exact to one sample, burst count and repeat interval exact, level within 0.5 dB. Don't read `/data/python/SDR` at test time; copy the few generator formulas into the test. Editing `tests/signals.py` needs explicit permission.
- [medium] Parallel plan. Sequential first: W1 writes `iqmeasure.py` and its test. Then in parallel: W2 `iq-measure` wrapper and JSON output; W3 `iq-picture` (needs the render core moved out of `library.py`, which is owned by one worker); W4 `iq-demod` (owns `iqdemod.py`); W5 `control.py` + `tools/fmctl` additions (the `measure` command waits for W2). `iq-frames` waits for W4. `knowledge/` and `run_all.py` are edited by the manager only.
- [medium] File ownership: W1 `iqmeasure.py`, `tests/test_iq_measure.py`; W2 `tools/iq-measure`; W3 `tools/iq-picture`, the `library.py` split (one owner: `library.py` must not be touched by two workers); W4 `iqdemod.py`, `tools/iq-demod`; W5 `control.py`, `tools/fmctl`, `tests/test_control.py`; W6 `tools/iq-frames`. Manager: `run_all.py`, `knowledge/*`, `CONTRACTS.md`.
- [medium] The roadmap's "tools/iq-measure" stub should say which inputs it takes. Right now the plan lists `--t0 --t1 --f0 --f1` but not how it reads `.sdvr`, `.cdif` or `.cfile`. Reuse `radios.iq_reader` for all of them.
- [low] Put `tools/iq-measure`'s JSON keys in `CONTRACTS.md` before the workers start, so `fmctl measure` and the tests agree.
risks:
- Verified: `library.py`→`radios.py`→`gnuradio` import chain; any tool importing `library` is not numpy only.
- Inferred: scipy is needed for decimation (`resample_poly`); numpy-only tools would need a pure-numpy fallback or must state scipy.
- Verified: `environment.yml` is the same on Linux and macOS. Inferred: rtl_433 is GPL-2, so hand-off by subprocess only; never vendor it.
- Inferred: `fmctl measure` on a 1 GB file blocks the Qt thread if run in-process; use a subprocess and a reply timeout.
- Inferred: the Signal Hound licence rule is not endangered by these tools, since they read files only, but `sceptre_dvr` must stay the only reader of `.sdvr`.
- Unverified: whether the spectrum view has a marker widget for the `marker FREQ` command.
- Inferred: the classifier can't name a signal from one burst; require repeats before a label is written to JSON.

Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
tools:
- <name> | owner file: <path> | depends_on: <names or none> | parallel_group: <number> | reuse: <existing module or "none">
drop:
- <tool or item> — <why> (source A/B/C)
reorder:
- <one line: what moves where and why> (source)
fmctl_additions:
- <command> | <what it does> | design decision for the user: <the decision, or "none"> (source)
open_questions:
- <one line each, for the user>
rejected:
- <claim and why it is wrong> (source)
