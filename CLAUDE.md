# CLAUDE.md

A standalone FM receiver: a wideband FFT sweep plus one-station FM
stereo/RDS reception with recording. It is a PyQt5 + GNU Radio 3.10 app
with no launcher.

- Run: `./fm-receiver` (activates the `gnu` conda env). Headless:
  `QT_QPA_PLATFORM=offscreen ./fm-receiver --no-sound-card --quit-after 10 --screenshot out.png`
  (`--no-sound-card` is hidden, for tests: `--no-audio` only starts muted)
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
- Code: `tools/fm_receiver/`; each module's docstring says what it is. Not
  obvious from the code:
  - `widgets.Card` sits on a grid `Form` because Qt 5's `QFormLayout`
    leaves gaps for hidden rows.
  - With the window open, drive it with `tools/fmctl` (`control.py`'s
    socket) rather than opening the radio.
  - We decode when decoding is what classifies a signal (a cell's
    broadcast, say), and only then: the aim is to say what a signal is, not
    a decoder per protocol. Today the decoders are outside ones:
    `tools/iq-decode` runs rtl_433 (on PATH); `iqlte_decode.py` hands off to
    an outside srsRAN 4G `pdsch_ue` and `iqnr_decode.py` to its NR library
    (`run_nr.sh`), both via `$FMRX_LTE_TOOLS`, SI-RNTI only, and srsRAN is
    never installed here (`knowledge/lte-verify.md`,
    `knowledge/nr-verify.md`). Our own decoders are written from the
    3GPP specs: srsRAN is AGPL and none of its code comes into this repo.
    Cellular decoding stays broadcast only (MIB, SIB1, other SI on the
    SI-RNTI): no paging, random access or user traffic, and nothing sent.
  - `sceptre_dvr`, `iqmeasure`, `iqburst`, `iqframes`, `iqcatalog`,
    `iqlte`, `iqnr` and `iqnr_phy` stay numpy only, so their tools run anywhere
    (`iq-picture` needs matplotlib; the LTE test helpers use scipy).
  - `knowledge/catalogs.md` is kept equal to `iqcatalog.py` by a test.
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
