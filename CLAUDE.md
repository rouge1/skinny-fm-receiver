# CLAUDE.md

A standalone FM receiver: a wideband FFT sweep plus one-station FM
stereo/RDS reception with recording. It is a PyQt5 + GNU Radio 3.10 app
with no launcher.

- Run: `./fm-receiver` (activates the `gnu` conda env). Headless:
  `QT_QPA_PLATFORM=offscreen ./fm-receiver --no-audio --quit-after 10 --screenshot out.png`
- Test: `python tools/tests/run_all.py` (no radio); add `--hw` when a BB60D
  is attached, `--hackrf` for a HackRF. Other sessions on this machine (for
  example ble-scanner) use the HackRF too: agree who has it first, and make
  sure nothing of ours holds it afterwards (`hackrf_info` must open it).
- Code: `tools/fm_receiver/`. `app.py` is the window, `engine.py` the
  flowgraph, `sweep.py` the FFT sweep, `dsp.py` the receive chain,
  `radios.py` the radios, `recording.py` IQ/WAV output, `widgets.py` the
  knobs, digit entries, theme disc and spectrum view.
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
