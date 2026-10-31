# fldigi: what it decodes, and what a command-line tool of ours would need

fldigi is the standard program for amateur-radio "sound card" digital modes:
text, and a few picture modes, carried on audio tones inside a voice
channel. This file lists what it does, so we can build the useful part into a
command-line tool of our own (roadmap: not yet planned). fldigi itself does
not suit a session without a screen (see "Why not just drive fldigi").

Checked on 2026-10-31 against Ubuntu 24.04's `fldigi` 4.2.03-1build3: its
`--help`, its man page, `--xmlrpc-list`, `--build-info`, and the mode names
compiled into `/usr/bin/fldigi`. Mode parameters (baud, tones, bandwidth)
are from the published mode descriptions, not measured here: confirm each
against its specification before building it. fldigi is not kept installed:
the decision that day was to build our own.

## Licence: write ours from the specs

fldigi is GPL-3+. This repo is public and none of fldigi's code comes into it,
the same rule as srsRAN (CLAUDE.md): our decoders are written from the mode
descriptions, and fldigi is at most a referee, run outside the repo.

## Why not just drive fldigi

- It decodes only live audio, through its window: a sound card, PulseAudio,
  or a WAV played into it from its File menu ("Audio > Playback"). There is
  no "decode this file, print the text" command.
- The man page describes one (`--benchmark-input audio.wav
  --benchmark-modem ID --benchmark-output out.txt`), but it is a build-time
  option Ubuntu's package leaves out: tried, fldigi opened its window
  instead. It is not in `--help` or `--build-info`.
- Remote control is XML-RPC (port 7362, 176 methods): set the mode
  (`modem.set_by_name`), the audio carrier (`modem.set_carrier`), and read
  the decoded text (`text.get_rx`). Nothing in the list plays a file in.
- Started on an invisible screen (xvfb) with a fresh settings folder, it
  never answered XML-RPC within 30 s: its first-run setup dialog is
  probably waiting for a click.

So the chain would be IQ → our demodulator → WAV → a hidden fldigi → its
text box → back: too many moving parts for one mode decode.

## The modes

Names are fldigi's own (as in its mode menu and `modem.set_by_name`). "Out"
is what the decoder produces. "Here" is what else on this machine decodes
it today.

| Family | fldigi names | What it is | Out | Here |
|---|---|---|---|---|
| CW | `CW` | On/off keyed tone, Morse; fldigi follows 5-200 WPM and tracks the speed | text | multimon-ng `MORSE_CW` (weak); our waterfall |
| RTTY | `RTTY` | 2-FSK, Baudot (ITA2) 5-bit with start and stop bits; default 45.45 baud, 170 Hz shift; shift and baud are settable (to 300 baud), and 7/8-bit ASCII framing | text | minimodem `--rtty` |
| PSK | `BPSK31` `BPSK63` `BPSK63F` `BPSK125` `BPSK250` `BPSK500` `BPSK1000` `PSK10` | Differential BPSK with raised-cosine shaped symbols (BPSK31: 31.25 baud, about 60 Hz wide), Varicode text; no FEC (the `F` modes add it) | text | none |
| QPSK | `QPSK31` `QPSK63` `QPSK125` `QPSK250` `QPSK500` | QPSK with a rate-1/2 convolutional code, same symbol rates | text | none |
| PSK-R and multi-carrier | `PSK63RC4` ... `PSK1000RC2`, `PSK125X12` ... `PSK1000X2`, `PSK500C4` ... | `R`: robust (FEC and an interleaver); `C`/`RC`/`X`/`RX` followed by a number: that many PSK carriers side by side | text | none |
| 8PSK | `8PSK125` ... `8PSK1200F`, `PSK8P*` | 8PSK; `F` and `FL`: with FEC (and a longer interleave) | text | none |
| MFSK | `MFSK4` `MFSK8` `MFSK11` `MFSK16` `MFSK22` `MFSK31` `MFSK32` `MFSK64` `MFSK128`, `MFSK64L` `MFSK128L` | One of 16 or 32 tones at a time (MFSK16: 16 tones, 15.625 baud, about 316 Hz), convolutional FEC and an interleaver (`L`: longer); can also send a small picture | text, picture | none |
| Olivia | `OLIVIA-8/250` and the rest: 4, 8, 16, 32 or 64 tones in 125-2000 Hz | MFSK with Walsh-function FEC spread over a long block: decodes far under the noise, slowly | text | none |
| Contestia | `CONTESTIA_4_125` ... `CONTESTIA_64_2000` | Olivia's structure with a 6-bit character set: about twice as fast, less robust | text | none |
| DominoEX | `DOMX4` `DOMX5` `DOMX8` `DOMX11` `DOMX16` `DOMX22` `DOMX44` `DOMX88`, `DOMXMicro` | IFK (incremental frequency keying: the data is the step between successive tones, so drift does not matter), 18 tones, no FEC by default | text | none |
| THOR | `THOR4` ... `THOR100`, `THOR25x4` `THOR50x1` `THOR50x2`, `THOR-M` | DominoEX's IFK with FEC and an interleaver | text | none |
| Throb | `THROB1` `THROB2` `THROB4`, ThrobX | Characters as single tones or tone pairs out of 9 (11 for ThrobX), 1, 2 or 4 baud | text | none |
| Hellschreiber | `FELDHELL` `SLOWHELL` `HELLX5` `HELLX9` `FSKH105` `FSKH245` `HELL80` | Characters drawn as on/off pixels, column by column (Feld Hell: 7 columns of 14 pixels, 122.5 pixels a second); fldigi shows it as a strip of printing, not text | picture of text | none (bench round 67: our scratch reader) |
| MT63 | `MT63-500S` `MT63-500L` `MT63-1000S` `MT63-1000L` `MT63-2000S` `MT63-2000L` | 64 parallel carriers across 500, 1000 or 2000 Hz, Walsh-function FEC, a short (S) or long (L) interleave; survives deep fading | text | none |
| FSQ | `FSQ1.5` `FSQ2` `FSQ3` `FSQ4.5` `FSQ6` | Fast Simple QSO: slow IFK for HF chat, with directed messages | text | none |
| IFKP | `IFKP 0.5` `IFKP 1.0` `IFKP 2.0` | IFK+, a cousin of FSQ; can send a small avatar picture | text, picture | none |
| OFDM | `OFDM-500F` `OFDM-750F` `OFDM-3500` | Newer multi-carrier PSK modes with FEC (4.1 and later); details not checked | text | none |
| NAVTEX / SITOR-B | `NAVTEX` `SITORB` | Maritime safety broadcasts: 100 baud FSK, 170 Hz shift, CCIR 476 (a 7-bit code with four marks in each character, every character sent twice); 518 and 490 kHz; fldigi ships a station list | text | none |
| WEFAX | `WEFAX576` `WEFAX288` | Weather fax: an FM subcarrier, 1500 Hz black to 2300 Hz white, 120 lines a minute; 576 and 288 are the index of cooperation (picture width) | picture | none |
| Measuring | `WWV`, frequency analysis, `FMT` | Calibrate the sound card's clock to a time signal; measure a carrier's frequency closely (FMT: on-air frequency tests) | numbers | our `iq-measure` |

Also in fldigi, and relevant to a tool of ours:

- **RSID** (Reed-Solomon ID): a short 15-tone MFSK burst a station may send
  before transmitting, which names the mode and its audio frequency. fldigi
  listens for it across the whole passband and switches to that mode. It
  is a classifier in the protocol itself, which is our "classify first"
  rule.
- **Multi-channel PSK** (the "PSK viewer"): decodes every BPSK31 signal in
  the passband at once, one line each.
- **AFC and squelch** per mode, and a waterfall to click a signal.
- **Not relevant to us:** rig control (hamlib, flrig), logging and contests,
  macros, ARQ and KISS links (flarq, flmsg), PSK Reporter spotting,
  transmitting.

## What a tool of ours would need

The aim is the one CLAUDE.md sets: say what a signal is, and decode where
decoding is what tells it (here: the text is the flag). Each mode costs a
demodulator, so start where a CTF round is most likely to hide one.

1. **Input.** IQ (`.cfile` or a span from `iqmeasure.extract`), with the
   SSB or FM demodulation to audio done by us, or a WAV directly. Output:
   JSON like the other `iq-*` tools (mode, audio frequency, text, a chance
   that it is right), plus a PNG for the picture modes.
2. **Find the mode first.** RSID when it is there. Otherwise measure: the
   number of tones and their spacing, the symbol rate, whether the phase
   carries the data (PSK), the occupied bandwidth. These name most
   families. `iq-measure` already measures most of this.
3. **Then decode, in this order** (most likely in an RF CTF first, then
   cost):
   - CW, RTTY: done by other tools (multimon-ng, minimodem); bring them in
     only if those fail on a real round.
   - BPSK31/63 and QPSK31/63: common, and nothing here decodes them.
     Needs differential BPSK, Varicode, and the QPSK convolutional code
     (Viterbi).
   - Hellschreiber: a picture, not a bit stream: draw the columns (bench
     round 67 was one, half credit, read by a scratch script).
   - WEFAX and NAVTEX: picture and text broadcast modes.
   - MFSK16, Olivia and Contestia, MT63: more work (FEC, interleavers,
     Walsh codes).
   - DominoEX, THOR, Throb, FSQ, IFKP, the OFDM modes: last.
4. **Check each decoder** by our own transmitter for that mode (a loopback,
   like the LoRa check in rf-ctf.md), and against a recording with known
   text. fldigi can be the referee for a recording only with its window
   (a WAV played in), and it is not kept installed.
5. **Where it lives:** numpy only, like `iqmeasure`, so it runs anywhere.
