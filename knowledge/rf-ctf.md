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
| OOK / ASK | pulse widths or on/off bits, often UART 8N1 or a PT2262/EV1527 code | URH, inspectrum, `rtl_433 -A` (pulse analyser), GNU Radio | `inspectrum`, `rtl-433`; URH through pipx (all installed) |
| 2-FSK / GFSK | two tones become bits, then UART 8N1 or a sync word + packet | URH (auto demod), inspectrum (measure baud), GNU Radio quad demod + clock recovery, minimodem | `inspectrum`, `minimodem`, GNU Radio; URH through pipx (all installed) |
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
| FM subcarrier / SCA | audio on 67/92 kHz above the stereo MPX | GNU Radio, then FM-demod the subcarrier | GNU Radio (installed) |
| DMR / P25 / D-Star | digital voice speaking the flag, or data | dsd-fme / dsd-neo | not in apt: build |
| LoRa | chirp spread spectrum payload | gr-lora_sdr | conda into `gnu`, or build |
| Zigbee / 802.15.4 | 2.4 GHz O-QPSK packets (HackRF) | gr-ieee802-15-4 → Wireshark | build; `wireshark` (installed) |
| BLE | advertising payloads | Ubertooth, Wireshark; ble-scanner on this machine | `ubertooth`, `wireshark` (installed) |
| Wi-Fi | beacons / handshakes | aircrack-ng suite, Wireshark (needs a Wi-Fi card, not an SDR) | `aircrack-ng` |
| ADS-B | callsign or squawk fields | dump1090 / readsb | `dump1090-mutability`; readsb script |
| AIS | ship name / message text | AIS-catcher | install script / build |
| GPS | the flag in a faked GPS signal (HackRF transmit) | gps-sdr-sim (make it), gnss-sdr (receive it) | `gnss-sdr`; gps-sdr-sim is one `gcc` |
| Frequency hopping | fragments on several channels | wide capture (BB60D 40 MS/s), then per-channel demod; aliasing tricks | – |

NFC/RFID challenges need a Proxmark-class reader, not these radios.

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

apt (all present in 24.04; all installed on this machine by 2026-09-25
except `aircrack-ng`):

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
- gr-lora_sdr: `conda install -n gnu -c tapparelj -c conda-forge gnuradio-lora_sdr`
  (unverified that the channel's build matches the env's GNU Radio)
- redsea, dsd-fme, aptdec, gps-sdr-sim, gr-ieee802-15-4: build from source
- AIS-catcher: its install script. SatDump: its release .deb.
- baudline: free binary from baudline.com (dormant)
