# RF CTF challenges and the tools to solve them

This covers how RF capture-the-flag challenges hide a flag in a signal, and
which Linux tools turn each kind back into text. It also records what worked
on our own `AFT{...}` hunts. The wider decoder survey, and the full apt list,
are in [rtl-software.md](rtl-software.md).

Researched on 2026-09-25 on Ubuntu 24.04. The apt names were checked with
`apt-cache policy`. "unverified" marks what could not be confirmed.

## Who runs them

- **RF Hackers Sanctuary (RFHS)** runs the RFCTF at DEF CON's RF Hacking
  Village, and at BSides and ShmooCon events (rfhackers.com, github.com/rfhs).
- **Hack-A-Sat** has waveform reverse-engineering challenges: QAM, PSK,
  satellite telemetry.
- **GRCon CTF** (the GNU Radio conference) is heavy on IQ and DSP puzzles.
- **NSA Codebreaker** opens every September and has had unknown-signal tasks.
- One-off RF challenges turn up at UTCTF ("RF is spOOKy"), Capture the Signal
  (cts.ninja) and SDR WCTF. picoCTF, CSAW, NahamCon and HTB Cyber Apocalypse
  have the odd RF-flavoured task, but none is a regular track there
  (unverified).

Most flags come in one of two ways: a live transmitter you must find and
record, or an IQ file handed out (`.cfile`/`.cf32`, `.cu8`, `.cs16`,
`.sigmf-data` + `.sigmf-meta`, or a WAV).

## Challenge types

| Type | How the flag is carried | Decode with | apt on 24.04 |
|---|---|---|---|
| OOK / ASK | pulse widths or on/off bits, often UART 8N1 or a PT2262/EV1527 code | URH, inspectrum, `rtl_433 -A` (pulse analyser), GNU Radio | `inspectrum`, `rtl-433`; URH through pipx |
| 2-FSK / GFSK | two tones become bits, then UART 8N1 or a sync word + packet | URH (auto demod), inspectrum (measure baud), GNU Radio quad demod + clock recovery, minimodem | `inspectrum`, `minimodem`, GNU Radio; URH through pipx |
| PSK (BPSK/QPSK) | symbols need Costas + timing recovery, sometimes differential | GNU Radio, SigDigger, URH, fldigi (PSK31), gr-satellites | `fldigi`, `gr-satellites`; SigDigger AppImage |
| QAM / exotic | Hack-A-Sat style constellations | GNU Radio, numpy | – |
| AFSK / Bell 202 | 1200/2200 Hz audio tones | minimodem `1200`, multimon-ng `AFSK1200`, direwolf | `minimodem`, `multimon-ng`, `direwolf` |
| APRS / AX.25 | AFSK1200 packets with text | direwolf, multimon-ng | `direwolf`, `multimon-ng` |
| Morse / CW | keyed carrier or tone | fldigi, multimon-ng `MORSE_CW`, or read the waterfall | `fldigi`, `multimon-ng` |
| RTTY / Baudot | 45.45 baud FSK, 5-bit code | fldigi, `minimodem --rtty` | `fldigi`, `minimodem` |
| POCSAG / FLEX | pager messages | multimon-ng (POCSAG solid, FLEX weaker) | `multimon-ng` |
| DTMF | touch-tone digits (then maybe a phone-keypad code) | multimon-ng `DTMF`, sox/Audacity | `multimon-ng`, `sox`, `audacity` |
| SSTV | picture in audio (Robot36, Martin, Scottie, PD) | qsstv | `qsstv` |
| HF fax / weatherfax | scan-line picture | fldigi (WEFAX) | `fldigi` |
| NOAA APT / Meteor LRPT | weather-satellite picture, often as a WAV | aptdec / noaa-apt (APT), SatDump (both) | not in apt: builds / releases |
| Waterfall art | text or a QR code drawn in the spectrum | inspectrum, gqrx/SDR++, sonic-visualiser, Audacity spectrogram, this app's waterfall | `inspectrum`, `gqrx-sdr`, `sonic-visualiser`, `audacity` |
| Audio stego | text in the audio spectrum, LSBs, reversed or sped-up speech | sonic-visualiser, Audacity, sox | same |
| RDS | PS / RadioText on an FM station | this app, redsea, gr-rds | `gr-rds`; redsea build |
| FM subcarrier / SCA | audio on 67/92 kHz above the stereo MPX | GNU Radio, then FM-demod the subcarrier | GNU Radio (the `gnu` env) |
| DMR / P25 / D-Star | digital voice speaking the flag, or data | dsd-fme / dsd-neo | not in apt: build |
| LoRa | chirp spread spectrum payload | gr-lora_sdr | the `lora` conda env (not `gnu`) |
| Zigbee / 802.15.4 | 2.4 GHz O-QPSK packets (HackRF) | gr-ieee802-15-4 → Wireshark | build; `wireshark` |
| BLE | advertising payloads | Ubertooth, Wireshark; ble-scanner on this machine | `ubertooth`, `wireshark` |
| Wi-Fi | beacons / handshakes | aircrack-ng suite, Wireshark (needs a Wi-Fi card, not an SDR) | `aircrack-ng` |
| ADS-B | callsign or squawk fields | dump1090 / readsb | `dump1090-mutability`; readsb script |
| AIS | ship name / message text | AIS-catcher | install script / build |
| GPS | the flag in a faked GPS signal (HackRF transmit) | gps-sdr-sim (make it), gnss-sdr (receive it) | `gnss-sdr`; gps-sdr-sim is one `gcc` |
| Frequency hopping | fragments on several channels | wide capture (BB60D 40 MS/s), then per-channel demod; aliasing tricks | – |

NFC/RFID challenges need a Proxmark-class reader, not these radios.

fldigi's own modes (PSK31, MFSK, Olivia, MT63, Hell, NAVTEX, WEFAX), and the
command-line decoder of ours planned for them: [fldigi.md](fldigi.md).

## Solving workflow

1. **Find it.** Use this app's sweep, the waterfall, gqrx or SDR++. Compare
   the shape and sound with sigidwiki.com. Look for a repeat interval.
2. **Record IQ.** Use a rate that covers the whole signal, with margin. The
   Recordings tab works, or `rtl_sdr` / `hackrf_transfer`. Keep the centre
   frequency and rate with the file (SigMF: `pip install sigmf`).
3. **Look at it.** In inspectrum, measure the burst length, symbol period and
   tone spacing; its cursors give the baud directly. SigDigger or URH are the
   alternatives.
4. **Demodulate.** Let URH guess first. If it fails, a small GNU Radio
   flowgraph: envelope for OOK, quad demod for FSK, Costas + symbol sync for
   PSK. Or numpy on `np.fromfile(f, np.complex64)` (for `.cu8`:
   `np.uint8`, minus 127.5).
5. **Decode.** For a standard mode, use its decoder (see the table). For a
   raw bitstream, try UART 8N1 first (both bit orders, both polarities), then
   Manchester, then a sync word + length + CRC.
6. **Get the flag.** Read the ASCII. Otherwise try base64 or hex, or
   CyberChef (cyberchef.io, or a local copy). A picture may simply show it.

## Rules from the bench night (2026-10-29/30)

A night of blind rounds, with a signal generator cabled into the BB60D,
scored most signals right. The misses came from what looked like part of
something else, so it was passed over. Three rules came out of it
(roadmap Round 31). `iq-measure` now flags each case. The rules still
apply where it does not.

1. **Follow every pure line in programme audio.** A steady or keyed tone
   under speech or music, or a DTMF pair, can be the challenge, with the
   programme only its cover. Demodulate it and look at the audio's
   spectrum, not only at the RF. A narrow line that stands over the audio
   beside it is a lead. One that comes and goes is keyed (Morse-like). The
   card's `audio` section lists the lines, says which are keyed, and gives
   the DTMF digits in order.
2. **Capture longer than the loop.** A challenge often repeats, and what
   it sends once a loop can sit anywhere in it. That night a DTMF burst
   came in a 72 s loop, after a 30 s capture had ended. Find the repeat
   (the bursts' `repeat_interval_s`, or the audio's `loop_s`). Then
   capture at least two repeats. If the repeat is unknown, capture longer
   than the longest gap you have seen. The card's `warnings` say when the
   span read is shorter than that, and when the recording is longer than
   the part that was read.
3. **A line with its own width is a signal until shown otherwise.** A
   narrow line beside a strong signal (a beacon next to a TV picture, say)
   is not a spur or an image just because it is small. It is ruled out
   only when one of these shows it:
   - its mirror about the centre is at least 20 dB stronger with the same
     shape (an image: `beside`'s `image: true`);
   - it moves when the radio is retuned by an odd amount, such as
     1.234 MHz, so it belongs to the receiver;
   - it stays with the generator off and the cable off;
   - it is in the bench's known-spurs list (kept in the ops folder, not in
     this public repo).

   Otherwise demodulate it like any other signal.

## Our own hunts (AFT{...})

- 453.086 MHz: 2-FSK, ±10 kHz, 2000 baud, UART 8N1 LSB-first ASCII.
- 902-928 MHz slow hopper: OOK at 1000 baud, UART 8N1, 170 ms packets every
  1.17 s, four fragments headed `Hn/4:`.

Both used UART 8N1 with idle-mark runs around the payload and a ~1.17 s
repeat. The ISM band's real GFSK meter traffic was a decoy: `rtl_433` or
`rtlamr` identify it quickly, so it can be ruled out.

## Practice material

- RFHS wiki, including its SDR challenge list:
  https://github.com/rfhs/rfhs-wiki/wiki. Tools and a container:
  https://github.com/rfhs/rfctf-sdr-tools, https://github.com/rfhs/rfctf-container
- Signal Identification Guide: https://www.sigidwiki.com/wiki/Signal_Identification_Guide
- IQEngine, a browser IQ viewer with shared recordings: https://www.iqengine.org
- GRCon22 CTF write-up (Daniel Estévez): https://destevez.net/2022/10/grcon22-capture-the-flag/
- Hack-A-Sat 2023 RF walkthroughs (3 parts):
  https://medium.com/@kwmcclintick/reverse-engineering-radio-frequency-signals-solutions-walkthrough-hack-a-sat-capture-the-flag-2023-74e46f088cc9
- Frequency-hopping CTF solved by aliasing:
  https://www.rtl-sdr.com/solving-a-frequency-hopping-ctf-challenge-with-aliasing/
- NSA Codebreaker: https://nsa-codebreaker.org (past unofficial solutions on GitHub)

## Install for CTF

What this machine has, checked on 2026-10-31 (`dpkg-query`, `command -v`):

- Installed: `rtl-433` 23.11, `multimon-ng` 1.3.0, `direwolf` 1.7,
  `minimodem` 0.24, `qsstv` 9.5.8, `dump1090-mutability`, `hackrf`,
  `soapysdr-tools`, URH 2.10.0 with sigmf (pipx), `sox` 14.4.2, `xvfb`
  (an invisible screen, for checking a desktop program). The `rtl_*` tools
  are in the `gnu` env only.
- gr-lora_sdr: in the `lora` conda env (GNU Radio 3.10.11), not `gnu`
  (below).
- Speech to text: faster-whisper 1.2.1 in the `audio_llm` env, with the
  tiny, small and medium models downloaded.
- Not installed: `inspectrum`, `gqrx-sdr`, `audacity`, `sonic-visualiser`
  (installed and removed again on 2026-10-31: viewers for a person at the
  screen, which a headless session does not use); `fldigi` and SDR++
  (installed and removed the same day: fldigi decodes only through its
  window, see [fldigi.md](fldigi.md); SDR++ is a receiver like this app),
  `gr-satellites`, `gnss-sdr`, `welle.io`, `wsjtx`,
  `ubertooth`, `aircrack-ng`; aptdec, AIS-catcher, readsb, redsea,
  dsd-fme, SatDump, SigDigger, gr-ieee802-11.

apt (all present in 24.04):

```bash
sudo apt install rtl-sdr inspectrum gqrx-sdr sox multimon-ng minimodem direwolf \
    fldigi qsstv audacity sonic-visualiser gr-satellites gnss-sdr dump1090-mutability
# Wi-Fi challenges only:
sudo apt install aircrack-ng
```

Not in apt:

- URH: installed with `pipx install urh` (2.10.0). `urh` opens this app's
  `.cfile` as cf32; `urh_cli` only works with a live radio (no IQ-file input).
  See rtl-software.md, "URH".
- sigmf: installed into URH's venv with `pipx inject urh sigmf` (1.13.0);
  run it with `~/.local/share/pipx/venvs/urh/bin/python`. Keep these out
  of the `gnu` env unless they are needed next to GNU Radio.
- SigDigger: AppImage from https://github.com/BatchDrake/SigDigger/releases
- SDR++: .deb from https://github.com/AlexandreRouma/SDRPlusPlus/releases
- gr-lora_sdr: in its own env, `lora` (`conda run -n lora python ...`).
  It needs gnuradio-core older than 3.10.12, so installing it into `gnu`
  (3.10.12) does not solve. A transmit-to-receive loopback through it
  (2026-10-31) decoded every frame at SF7, SF9 (0 dB, 2 kHz off) and SF12
  (-10 dB, 1 kHz off). At SF12 with 4 samples a chip, the receiver wants
  16k samples at once, over GNU Radio's default buffer: call
  `set_min_output_buffer(65536)` on the block that feeds `lora_sdr_lora_rx`,
  or it stops with "requesting more input data than we can provide" and
  decodes nothing.
- redsea, dsd-fme, aptdec, gps-sdr-sim, gr-ieee802-15-4, gr-ieee802-11:
  build from source (gr-ieee802-11 and gr-ieee802-15-4 into a conda env,
  with gr-foo, branch `maint-3.10`)
- AIS-catcher: its install script. SatDump: its release .deb.
- baudline: free binary from baudline.com (dormant)
