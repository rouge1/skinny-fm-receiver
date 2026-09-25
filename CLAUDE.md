# CLAUDE.md

A standalone FM receiver: a wideband FFT sweep plus one-station FM
stereo/RDS reception with recording. It is a PyQt5 + GNU Radio 3.10 app
with no launcher.

- Run: `./fm-receiver` (activates the `gnu` conda env). Headless:
  `QT_QPA_PLATFORM=offscreen ./fm-receiver --no-audio --quit-after 10 --screenshot out.png`
- Test: `python tools/tests/run_all.py` (no radio); add `--hw` when a BB60D
  is attached, `--hackrf` for a HackRF, `--rtl` for an RTL-SDR (with this
  app closed: the check starts and stops its own rtl_tcp). Other sessions on this machine (for
  example ble-scanner) use the HackRF too: check whether it is in use
  (`pgrep -af "blescan|hackrf_"`, and `hackrf_info` opens it) rather than
  assuming, and make sure nothing of ours holds it afterwards.
- Project skill: `.agents/skills/fm-receiver/SKILL.md`. Claude Code reads
  it through a local link in `.claude/skills/` (git-ignored); make the
  link in each checkout (command in `.gitignore`).
- Platforms: Linux (x86-64) and macOS (Apple Silicon), from the same
  checkout, in the environment `environment.yml` makes. Anything
  platform-specific (library names, paths, `/proc`) goes behind
  `sys.platform`, with Linux's behaviour unchanged. Signal Hound's
  library is never committed: the repo is public and their licence forbids
  copies reaching people without the hardware. The same licence forbids
  reverse engineering, decompiling or disassembling it: debug it by what it
  does (return codes, crash reports, the samples), never by its code.
- Code: `tools/fm_receiver/`. `app.py` is the window, `engine.py` the
  flowgraph, `sweep.py` the FFT sweep, `dsp.py` the receive chain,
  `radios.py` the radios, `recording.py` IQ/WAV output, `library.py` the
  Recordings tab's list and overview, `widgets.py` the knobs, digit
  entries, theme disc, spectrum view, timeline strip and the foldable boxes
  (`Card` on a grid `Form`: Qt 5's `QFormLayout` leaves gaps for hidden
  rows), `bb60_sweep.py` the
  BB60D's own sweep (Signal Hound's API through ctypes, on the device the
  SoapySDR module opened), `hackrf_sweep.py` the HackRF's (its firmware's
  sweep mode through libhackrf, on the device the SoapySDR module lets go),
  `rtl_tcp.py` the RTL-SDR (an rtl_tcp client; rtl_tcp started here, or on
  another computer over ssh), `rtl433.py` the rtl_433 chain, for Receive's
  rtl_433 card (the radio's samples piped into rtl_433 processes, their
  JSON read back; rtl_433 is a system package, `rtl-433` on Linux, not in
  conda).
- Knowledge: `knowledge/capabilities.md` is the living capability list.
  Update it (and its status marks) when a feature is added or verified.
  `knowledge/usage.md` is the user guide. `knowledge/roadmap.md` holds
  planned work in phases: tick items as they ship.
- `rds_core.py`, `bb60_source.py` and `theme.py` are copied from the RF
  bench toolkit (`/data/python/SDR`); its `devnotes/rds.md` and
  `devnotes/radios.md` explain them. Changes made for this app are marked
  "FM receiver".

Rules carried over from the toolkit, because they crash or silently break
things:

- Keep a Python reference to every Python block while a flowgraph might be
  running. Stop the flowgraph before the process exits.
- HackRF gain is applied after `start()`.
- Colours come from `theme.TOKENS` when something is drawn, never copied at
  import.
- Guard every Qt paint override: an exception there aborts the process.
- Settings are written only through `config.update_config` (merge, then an
  atomic replace). Tests set `FMRX_CONFIG`, so they never write the user's
  file.
