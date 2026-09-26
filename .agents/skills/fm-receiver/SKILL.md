---
name: fm-receiver
description: Use when working on the FM receiver app, including its features, how to run and test it, the BB60D, HackRF and RTL-SDR radios, or planned work. Points to knowledge/ as the source of truth.
---

Use the knowledge/ folder as your primary source of truth for domain
knowledge, workflows, commands and constraints:

- knowledge/capabilities.md: what the app can do, and what has been verified
- knowledge/usage.md: the user guide
- knowledge/roadmap.md: planned work, in phases
- knowledge/google_bb60d.md: links to Signal Hound's BB60 API documentation
- knowledge/rtl-software.md: Linux SDR decoders and tools, their apt
  packages, and which could be fed like the rtl_433 card
- knowledge/rf-ctf.md: RF CTF challenge types and the tools that decode them

When the user has the window open, work it with `tools/fmctl` (`status`,
`tune`, `gain`, `mode`, `screenshot`, `wait`...; `fmctl help` lists them)
instead of opening the radio yourself: the window holds the device, and
`fmctl` moves its controls where the user can see them. See usage.md,
"Controlling the window from a script". `fmctl` exits 2 if no window is
listening.

Tools installed on this machine outside the app's `gnu` env (details in
rtl-software.md, "What this machine already has"):

- Universal Radio Hacker 2.10.0, through pipx: `urh` (GUI) and `urh_cli`
  in `~/.local/bin`. The GUI opens the app's `.cfile` recordings as cf32
  (set the sample rate from the `.sigmf-meta`). `urh_cli` has no IQ-file
  input: it receives from a radio (`-rx -d RTL-TCP|RTL-SDR|HackRF|USRP`)
  and prints bits using demodulation settings from the GUI (`-mo`, `-sps`,
  or a saved project file). It can also transmit (`-tx`): never without
  being asked. A radio URH opens is taken from the app, and the HackRF may
  be in use by another session.
- apt decoders: rtl_433, inspectrum, gqrx, multimon-ng, direwolf,
  minimodem, fldigi, qsstv, welle.io, dump1090-mutability, gr-satellites,
  wsjtx, sox, audacity, sonic-visualiser.
