---
name: fm-receiver
description: Use when working on the FM receiver app (its code, features, tests, the BB60D, HackRF and RTL-SDR radios, planned work), when driving the user's open window with tools/fmctl, or when finding, recording and decoding an RF signal with it (a band survey, IQ capture, waterfalls, demodulation, framing, CRCs, an LTE or 5G NR cell and its broadcast information). Points to knowledge/ as the source of truth.
---

The knowledge/ folder is the source of truth for domain knowledge,
workflows, commands and constraints. Read the file that fits before acting:

- knowledge/capabilities.md: what the app can do, and what has been verified
- knowledge/usage.md: the user guide, `fmctl` included ("Controlling the
  window from a script")
- knowledge/roadmap.md: planned work, in rounds; tick items as they ship
- knowledge/workflow.md: the user's method for a signal, in seven steps:
  find, record, waterfall, modulation, demodulate, encoding, confirm
- knowledge/gridstream.md: that method carried out on a smart-meter network,
  with what failed and why (DPLL timing, v4/v5 sync, finding a CRC,
  feeding rtl_433)
- knowledge/silver-spring.md: the same method on a Silver Spring Networks
  (Itron) FHSS mesh: complemented sync, per-hop 8-bit scrambler, CRC-32,
  rates 100-300 kBd, and the wrong turns on the way
- knowledge/rtl-software.md: the SDR tools on this machine and in apt, and
  which could be fed from a recording
- knowledge/rf-ctf.md: RF CTF challenge types and the tools that decode them
- knowledge/google_bb60d.md: links to Signal Hound's BB60 API documentation
- knowledge/sceptre-dvr.md: the layout of Sceptre's DVR file (`.sdvr`), which
  the app plays as IQ, and how its sweep bytes become dBm (each tile's own gain and offset)
  (`tools/dvr-hd DVR.sdvr` decodes HD Radio from one, offline; `--info` and
  `--scan` say what is in it; `tools/dvr-sweep` reads and draws a sweep DVR's
  spectra, also while Sceptre is still recording it; `tools/dvr-to-iq` makes a channel of one a
  plain `.cfile` + SigMF; `sceptre_blue.py` reads Sceptre's `.cdif` and `.fft`;
  `spectra.py` reads a sweep DVR or a `.fft` as dBm for the Recordings tab)
- knowledge/catalogs.md: the scramblers and CRCs `iq-frames` searches (kept
  equal to `iqcatalog.py` by a test)
- knowledge/digital-radio.md: HD Radio on the local FM band, and the other
  digital radio standards
- knowledge/signal-hound-calibration.md: the BB60D's and VSG60A's calibration,
  and what it means for dBm here
- knowledge/glossary.md: the LTE, 5G NR, TV and radio acronyms
- knowledge/lte-verify.md: how an LTE carrier is verified (CP, PSS, SSS, CRS,
  several cells) and its MIB and SIB1 read; real-air results and wrong turns
- knowledge/lte-broadcast.md: the LTE SIBs after SIB1: schedule, how long to
  record, SIB16 network time, the NR carriers in SIB24
- knowledge/nr-verify.md: the 5G NR sync-block search and the broadcast decode
  (MIB, SIB1, other SI, SIB9 time), output fields and limits
- knowledge/cell-location.md: locating a tower from broadcast only, sending
  nothing (no hardware for it yet)

## The radio

- **The user's window may hold it.** Run `tools/fmctl status` first: a
  reply means the window is open, and you work it through `fmctl`, never
  by opening the radio yourself; exit 2 means no window is listening.
- **The HackRF is shared** with other sessions (ble-scanner): check before
  opening it, and leave nothing of ours holding it (CLAUDE.md has how).
- The app is for finding signals, recording IQ and waterfall pictures. It
  does not decode (rtl_433 was taken out of it); decoding happens on the
  recordings.

## fmctl

`tools/fmctl 'cmd; cmd; ...'` runs commands in turn and prints each reply as
JSON; `fmctl help` lists them. Each works the widget a click would, so the
user sees it, and is refused where the window would refuse it.

- Look: `status` (JSON: the RDS block has the flags, clock, RT+ and groups
  heard as well as the name; `hd` has the station, programs and now playing:
  read the data from it, not from a screenshot), `screenshot PATH` (to
  check what the user sees, not to read data), `peaks [DB [START STOP]]` (the
  spectrum's signals as numbers; from the held trace while `peakhold on`).
- Survey: `scan START STOP [step KHZ] [quick S] [listen S] [snr DB]` tunes each
  channel and replies with one row each (level, SNR, pilot, RDS, HD Radio);
  channels with a pilot or SNR over `snr` are heard `listen` s more. A band
  takes minutes: run it in the background and read the reply from the file.
- Move: `tune`, `center`, `gain`, `agc`, `rate`, `mode`, `sweep START STOP`,
  `volume`, `mute`, `station rds|hd|atsc` (the RDS | HD Radio | ATSC TV tab on show: put
  RDS up while reading RDS, so the user sees what you read),
  `atsc [CHANNEL|program N]` (US television, RF 14-36: the tab, its picture and
  `status`'s `atsc` block with the programs, names, now and next),
  `hd 1-8|analog` (HD Radio's program, or analog FM; the
  decoder is always on where nrsc5 is installed, with no off;
  `status`'s `hd` says what plays, what is chosen and the lamp).
- Radio: `radio KIND` (hackrf, usrp, bb60, rtlsdr or file; `file` needs `open`
  first), `start`, `stop` (both refused while recording), `filter KHZ` (Receive
  only, 60-400), `step KHZ` (10, 50, 100 or 200).
- View: `view rf|mpx|audio span X center X ref DB range DB avg N window S` (any of
  them; none reports). Before a screenshot, zoom onto the signal and set
  ref/range around it: at the default 100 dB range a weak signal is a few
  pixels, and the waterfall's colours follow ref/range too. `avg` changes
  the numbers: high (20-50) steadies levels and lifts weak steady carriers
  out of the noise for `peaks`; low (1-2) keeps bursts from being averaged
  away.
- Record: `capture SECONDS [iq-band|iq-channel|audio]` replies with the
  files and their `.sigmf-meta`; `record ...` works the Record box.
- Recordings (everything recorded plays in the Recordings tab; Sweep and
  Receive are live only; `folders add PATH` lists Sceptre's `.sdvr` IQ DVRs
  and `.cdif` exports from another folder, read-only): `mode recordings`, `recordings` (the list, newest
  first, with which lines are `picked`), `recording N [TRACK]` (picks that one line), `play on|off`, `seek SECONDS`,
  `view rf|audio window S` (the seconds the waterfall, and the mini map's
  box, show), `loop on|off`. `status`'s `playback` block says what is chosen
  and playing, where the playhead is, the box on the map and how much
  waterfall is drawn; `loading` is true, and `message` says so, while the
  file is read (a gigabyte takes seconds: the spinner is on the plot).
  The map and the waterfall draw in the background after a choice or a
  seek: `wait 1` and look at `loading` before reading or taking a
  screenshot. A sweep DVR or `.fft` (a spectrum recording) plays with no
  radio and no sound, on a clock that waits while it loads; it opens on
  its whole band, and zooming in reads that band at full detail. Not
  scriptable yet: picking several lines, and the Folders dialog itself (use
  the window; `recordings-folder` sets the folder).
- Files: `open PATH` plays an IQ file (refused while recording); `delete N confirm`
  deletes the Nth recording, 0 the newest, the app's own only (the word `confirm`
  is required); `recordings-folder PATH` sets where Record saves; `marker FREQ`
  reads the level at FREQ (MHz) on the RF trace, read-only; `measure [SECONDS]
  [card]` replies the card of the last capture (see "Measuring a recording");
  `cells lte|nr [decode] [PATH]` runs `iq-lte` or `iq-nr` on the last capture
  (or PATH) and replies with its JSON, read-only (60 s, 300 s with `decode`);
  for `lte` the carrier is taken at the station the recording was tuned to.
  A cell survey: `rate 20; center MHZ; capture 6 iq-band; cells nr decode`
  (6 s is what `decode` reads).
- `wait SECONDS` lets the window run between commands: your shell can't
  sleep in the foreground, and RDS or peak hold need seconds.

Things that bit before:

- Bursts (ISM sensors, hopping meters) don't show on the live trace or an
  averaged waterfall: use `peakhold on`, wait, then `peaks`.
- The whole band is 8 bytes a sample: 320 MB/s at the BB60D's 40 MS/s.
  `capture` refuses over 4 GB. Recordings go to `<checkout>/recordings/`
  (git-ignored).
- `tune` far away moves the Center itself; an IQ file's band is fixed.

## Measuring a recording

`tools/iq-measure FILE` prints the card of a recording as JSON: the level
(in dBm only where the file has a calibration: a `.sdvr`, or a `.cdif` with
`DATA_GAIN`), the occupied bandwidth, tones, bursts, envelope and frequency,
and a class (noise, carrier, ook, ask, fsk, fm, am or unknown) with the
evidence for it. Options: `--t0`, `--t1`, `--band LO HI` (absolute Hz),
`--rate` and `--center` for a bare `.cfile`, and `--card`, which writes
`<base>.card.json` beside a `.cfile` only. `tools/iq-picture FILE OUT.png`
draws the spectrum and waterfall (it needs matplotlib). Neither decodes. Read
the card before writing any decoder. Built and tested on synthetic signals; not
yet run on a real file (see capabilities.md).

Two more tools take it from a card to bits and frames, with no decoder per
protocol. `tools/iq-demod FILE` finds every burst of a wide capture and
demodulates each as FSK (symbol rate chosen by the longest preamble, timing
that follows a clock a few hundred ppm off; `--frames-out` writes the bit
strings). `tools/iq-frames FRAMES.json` aligns them on their common sync, says
which byte positions are constant or carry the length, and searches the
catalog of scramblers and CRCs (`knowledge/catalogs.md`) for one that fits
several frames, with the seed free per frame and a chance estimate on every
hit. `iq-decode --bursts` hands each burst to rtl_433 at the decoder's own
sample rate. Method and wrong turns: knowledge/silver-spring.md.

`tools/iq-decode FILE [--t0 S --t1 S] [--band LO HI] [-R N ...]` scales a span and runs rtl_433 on it, printing its decodes as JSON (needs rtl_433 installed: exit 2 without it; see usage.md, "Decoding a recording with rtl_433"). Built, tested on a synthetic frame and over the air once (VSG60 to BB60D).
`iqmeasure.extract(path, t0, t1, band, max_rate, rate, center_hz)` returns that span as an `IQSlice` (iq, rate, center_hz, t0, t1), band-limited and decimated, read in chunks; `tools/iq-decode` feeds it to rtl_433.
`tools/iq-lte FILE [--block-center HZ] [--t0 S --t1 S] [--decode]` says whether an LTE-looking block is an LTE downlink (cyclic-prefix, PSS and SSS checks, numpy only; PCI from the SSS) and, with `--decode`, reads its MIB, SIB1 and every other SIB (`si`: schedule, carriers, SIB16 network time; 6 s by `--si-seconds`) through an outside srsRAN 4G `pdsch_ue` under `$FMRX_LTE_TOOLS` (nothing of ours decodes; exit 2 without those tools). Method: knowledge/lte-verify.md; the SIBs after SIB1 (schedule, capture length, network time, NR): knowledge/lte-broadcast.md. Built, tested on synthetic signals and a stub decoder; on real air (2026-10-16 and 2026-10-22, 10 MHz cells) the PCI, MIB, SIB1 and the SI schedule matched. With the window open, `fmctl cells lte [decode] [PATH]` runs it and replies with its JSON. The carrier must be at the file centre or given by `--block-center`: a Receive-tab capture is centred 300 kHz below the tuned station (`fmctl cells lte` passes the station itself), and an off-centre carrier reads as `possible` with no cells.
`tools/iq-nr FILE [--ssb-hz HZ] [--scs 15|30] [--t0 S --seconds S] [--decode]` lists the 5G NR cells whose sync block (SSB) is in a capture: the raster point (GSCN), spacing, PCI, SSB index, period and a chance (PSS, SSS and PBCH DM-RS, numpy only; 0.1 s searched by default). With `--decode` it reads each cell's MIB, SIB1 (operators, tracking area, cell identity, barring, carrier point A), the other SI that SIB1 schedules and SIB9's network time, through srsRAN's NR library (`run_nr.sh`) under `$FMRX_LTE_TOOLS` (SI-RNTI only; 6 s by `--decode-seconds`, about a minute; exit 2 without those tools). Method, output and limits: knowledge/nr-verify.md. Built, tested on synthetic signals and a stub decoder; on real air (2026-10-23, two 850 MHz cells) the MIB, SIB1, other SI and SIB9 time matched a hand decode. Other SI that SIB1 does not schedule is listed in `si.unscheduled` when it comes in the slots read (one cell sent SIB2 in SIB1's own slots). The outside program reads the strongest cell on an SSB frequency; for a weaker sector there the decode retries aimed at its PCI (`-P`), which gives its MIB (`mib.targeted`) but usually not its SIB1; with no MIB either it gets `decoded_pci` and a reason, never the stronger cell's broadcast. With the window open, `fmctl cells nr [decode] [PATH]` runs it and replies with its JSON.

## Decoding a recording

Follow workflow.md; gridstream.md shows it end to end. In short: a
waterfall picture of the whole capture and of one burst before any
demodulation; filter to the signal's width before a discriminator (a wide
filter makes noise of it); take the symbol rate from the preamble and track
the timing (a DPLL) on long packets; try the framings in turn; let a CRC,
agreeing across packets, be the proof. Check a vectorised search's hits by
hand before believing them.

rtl_433 as a second decoder: apt's 23.11 predates some decoders
(Gridstream); build master in the scratchpad if needed. Feed it a narrow
file (filtered, about 250 kS/s, signal off DC) with no settings-like words
in its name (`100k` reads as a sample rate).

URH as a second opinion, headless, through its Python library (details in
rtl-software.md, "URH"): cut the signal to a narrow cf32 file (signal at
0 Hz, filtered, about 1 MS/s), then in `~/.local/share/pipx/venvs/urh/bin/python`
with `QT_QPA_PLATFORM=offscreen`, `Signal.auto_detect()` measures the
modulation, samples a symbol, centre and noise on its own, and
`ProtocolAnalyzer.get_protocol_from_signal()` gives each packet's bits.
Its baud guess can be double: set `samples_per_symbol` from the preamble and
run it again. Its field finder needs packets that differ; with one repeated
packet it has nothing to compare.

## Tools outside the gnu env

Details in rtl-software.md, "What this machine already has".

- URH (pipx): `urh` opens `.cfile` recordings as cf32 (set the rate from
  the `.sigmf-meta`). `urh_cli` receives from a radio only, and can
  transmit (`-tx`): never without being asked.
- apt: rtl_433, inspectrum, gqrx, multimon-ng, direwolf, minimodem, fldigi,
  qsstv, welle.io, dump1090-mutability, gr-satellites, wsjtx, sox, audacity,
  sonic-visualiser.

## The repo is public

Nothing captured goes into a commit: no meter or device IDs, no decoded
payloads, no recordings (recordings/ and challenge/ are git-ignored). For
cells that means no real PCIs, operator codes, tracking areas, cell
identities, carrier frequencies or times in a file, test or commit message:
use made-up ones (PCI 123, PLMN 001-01, TAC 0x1234, SSB 600.15 MHz). Real
decodes live outside the repo (the ops folder, `$FMRX_LTE_TOOLS/work`).
Knowledge files describe method and format, with placeholder IDs.
