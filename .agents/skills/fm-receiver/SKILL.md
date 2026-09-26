---
name: fm-receiver
description: Use when working on the FM receiver app (its code, features, tests, the BB60D, HackRF and RTL-SDR radios, planned work), when driving the user's open window with tools/fmctl, or when finding, recording and decoding an RF signal with it (a band survey, IQ capture, waterfalls, demodulation, framing, CRCs). Points to knowledge/ as the source of truth.
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
- knowledge/rtl-software.md: the SDR tools on this machine and in apt, and
  which could be fed from a recording
- knowledge/rf-ctf.md: RF CTF challenge types and the tools that decode them
- knowledge/google_bb60d.md: links to Signal Hound's BB60 API documentation

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

- Look: `status`, `screenshot PATH`, `peaks [DB [START STOP]]` (the
  spectrum's signals as numbers; from the held trace while `peakhold on`).
- Move: `tune`, `center`, `gain`, `agc`, `rate`, `mode`, `sweep START STOP`,
  `volume`, `mute`.
- Record: `capture SECONDS [iq-band|iq-channel|audio]` replies with the
  files and their `.sigmf-meta`; `record ...` works the Record box.
- `wait SECONDS` lets the window run between commands: your shell can't
  sleep in the foreground, and RDS or peak hold need seconds.

Things that bit before:

- Bursts (ISM sensors, hopping meters) don't show on the live trace or an
  averaged waterfall: use `peakhold on`, wait, then `peaks`.
- The whole band is 8 bytes a sample: 320 MB/s at the BB60D's 40 MS/s.
  `capture` refuses over 4 GB. Recordings go to `<checkout>/recordings/`
  (git-ignored).
- `tune` far away moves the Center itself; an IQ file's band is fixed.

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
payloads, no recordings (recordings/ and challenge/ are git-ignored).
Knowledge files describe method and format, with placeholder IDs.
