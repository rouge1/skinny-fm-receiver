# RTL-SDR software on Linux: decoders, receivers and tools

This is a survey of the Linux software that decodes or displays what an
RTL-SDR (and usually the HackRF and BB60D too) can receive. For each tool it
gives the Ubuntu 24.04 (noble) package, where one exists, and how the tool
takes its samples, because that decides whether it could be fed the way
Receive's rtl_433 card is (`rtl433.py`: the radio's samples piped in, JSON
read back). RF CTF challenges and the tools that solve them are in
[rf-ctf.md](rf-ctf.md).

Researched on 2026-09-25. The apt facts were checked with `apt-cache policy`
on this machine. The upstream facts (URLs, activity, formats) come from the
projects' READMEs and release pages. "unverified" marks what could not be
confirmed.

## What this machine already has

- The `rtl_*` tools (`rtl_sdr`, `rtl_tcp`, `rtl_fm`, `rtl_power`, `rtl_test`,
  `rtl_eeprom`, `rtl_adsb`, `rtl_biast`) are inside the `gnu` conda env only.
  They come from conda-forge's `rtl-sdr` 2.0.2, which `environment.yml` pins.
  Ubuntu's `rtl-sdr` package (2.0.1) is **not** installed, only its library
  `librtlsdr2`. With the env off, none of them is on the PATH.
- apt: `rtl-433` 23.11 (inputs file, rtl_tcp, RTL-SDR, SoapySDR), `hackrf`
  (includes `hackrf_sweep`), `ubertooth`, `gnuradio` 3.10.9 (includes
  `gnuradio-companion`), `soapysdr-tools`, `python3-soapysdr`, and SoapySDR
  modules for RTL-SDR, HackRF, Airspy, bladeRF, LMS7, UHD, Mirics and the
  SoapyRemote client.
- The udev rules for RTL-SDR (`/etc/udev/rules.d/rtl-sdr.rules`,
  `/lib/udev/rules.d/60-librtlsdr2.rules`) are in place. No DVB blacklist
  exists, and none is needed: librtlsdr detaches `dvb_usb_rtl28xxu` when it
  opens the dongle (see [usage.md](usage.md)).

### RTL-SDR Blog V4 and V4L

Ubuntu's `librtlsdr2`/`rtl-sdr` 2.0.1 already supports the **V4** (R828D):
its HF upconversion, notch filters, input switching and bias tee. Debian took
the support in 0.6.0-5 (August 2023) from osmocom commit 1261fbb, which
predates the v2.0.1 tag. rtl-sdr.com's warning to use their fork dates from
older driver stacks. The **V4L** (R828S tuner) is different: osmocom added it
on 2026-08-11, so it needs the RTL-SDR Blog fork
(github.com/rtlsdrblog/rtl-sdr-blog) or a build of current osmocom master.
Whether conda-forge's 2.0.2 has it: unverified, and probably not.

## General receivers, servers and utilities

| Tool | What it is | apt on 24.04 | Radios / input | Upstream, status |
|---|---|---|---|---|
| rtl-sdr tools | `rtl_fm`, `rtl_tcp`, `rtl_power`, `rtl_sdr`, `rtl_test`, `rtl_biast` … | `rtl-sdr` 2.0.1 | RTL-SDR | github.com/osmocom/rtl-sdr, active |
| gqrx | Qt receiver on GNU Radio + gr-osmosdr | `gqrx-sdr` 2.17.4 | RTL, HackRF, Airspy, rtl_tcp … | github.com/gqrx-sdr/gqrx, active (upstream 2.17.7) |
| SDR++ | Light, fast receiver GUI | not in apt: GitHub release .deb / nightly (upstream advises against distro builds) | Broad, incl. rtl_tcp and SpyServer | github.com/AlexandreRouma/SDRPlusPlus, active |
| SDRangel | Rx+Tx GUI with many demods (DMR via dsdcc, ADS-B, AIS, DAB …) | not in apt: Flathub `org.sdrangel.SDRangel`, snap, upstream .deb | Broad | github.com/f4exb/sdrangel, active |
| CubicSDR | SoapySDR GUI receiver | `cubicsdr` 0.2.7 | Any Soapy device | github.com/cjcliffe/CubicSDR, slow (last tag 2022) |
| GNU Radio Companion | Flowgraph editor | in `gnuradio` (installed) | Soapy / osmosdr / file | gnuradio.org |
| gr-osmosdr | osmosdr source block | `gr-osmosdr` 0.2.5 | RTL, HackRF, Airspy … | Osmocom Gitea. The GNU Radio wiki now points to gr-soapy for 3.10. |
| SoapyRemote server | Share any Soapy radio over the network | `soapyremote-server` 0.5.2 (client installed) | Any Soapy device | github.com/pothosware/SoapyRemote |
| rx_tools | `rx_fm`/`rx_sdr`/`rx_power`: rtl_* over SoapySDR | not in apt: build | Any Soapy device (HackRF, BB60D …) | github.com/rxseger/rx_tools |
| rtl_power + heatmap.py | CSV sweep logger, waterfall PNG | `rtl_power` in `rtl-sdr`; heatmap.py is a script | RTL-SDR | github.com/CGrassin/rtl_power_scripts |
| rtl-power-fftw | Faster, finer rtl_power | not in apt: build | RTL-SDR | github.com/AD-Vega/rtl-power-fftw, activity unverified |
| soapy_power | rtl_power for any Soapy device | not in apt: `pip install soapy_power` | Any Soapy device | github.com/xmikos/soapy_power |
| kalibrate-rtl | ppm calibration from GSM towers | not in apt: build | RTL-SDR (HackRF via forks) | github.com/steve-m/kalibrate-rtl. Few 2G towers remain in many countries. |
| LTE-Cell-Scanner | ppm calibration from LTE cells | not in apt: build | RTL, HackRF, USRP | activity unverified |
| RTLSDR-Airband | Many AM/NFM channels at once, to Icecast or files | not in apt: build | RTL (multi-dongle), Soapy | github.com/rtl-airband/RTLSDR-Airband, active |
| csdr | Command-line DSP pipeline (OpenWebRX uses it) | not in apt: build | IQ on stdin | github.com/jketterl/csdr, active |
| OpenWebRX+ | Web SDR server with many decoders (SSTV, AIS, HFDL, FLEX …) | not in apt: its own PPA supports 24.04, or Docker | RTL, Soapy, SDRplay | github.com/luarvique/openwebrx, active |
| SpyServer | Airspy's streaming server (SDR#, SDR++ clients) | not in apt: binary from airspy.com | Airspy, RTL-SDR | closed freeware |
| inspectrum | Offline IQ analyser: measure symbol rate, extract symbols | `inspectrum` 0.3.1 | cu8/cs16/cf32/SigMF files | github.com/miek/inspectrum |
| Universal Radio Hacker | Record, demodulate and reverse-engineer protocols | not in apt: `pip install urh` (or snap) | RTL, HackRF, Soapy, files | github.com/jopohl/urh, active |
| SigDigger (suscan) | Live and offline signal analyser: PSK/FSK/ASK inspectors | not in apt: AppImage from releases | Soapy devices, files | github.com/BatchDrake/SigDigger, active |
| baudline | Time-frequency analyser | not in apt: free binary from baudline.com | Audio, files | dormant |
| Linrad, qradiolink | Weak-signal Rx / GNU Radio digital-voice transceiver | not in apt: build / AppImage | various | niche |

SDR# is Windows only.

## Pagers, ISM, meters and packet data

| Tool | Decodes | Band | apt on 24.04 | Input → output | Upstream |
|---|---|---|---|---|---|
| rtl_433 | 244 decoders in 23.11 (ISM devices): weather sensors, TPMS, remotes, doorbells | 315/345/433.92/868/915 MHz | `rtl-433` 23.11 (installed) | RTL, Soapy, rtl_tcp, cu8/cs16/cf32 files → JSON, CSV, MQTT | github.com/merbanan/rtl_433 (already the rtl_433 card) |
| multimon-ng | POCSAG 512/1200/2400, FLEX, EAS, DTMF, AFSK1200/2400, FSK9600, Morse, ZVEI, X10. **Not** NAVTEX. | pager bands, VHF/UHF | `multimon-ng` 1.3.0 | 22050 Hz S16LE audio on stdin (from `rtl_fm`) → text | github.com/EliasOenal/multimon-ng |
| direwolf | APRS / AX.25, FX.25, IL2P | 144.39 (US) / 144.8 (EU) MHz | `direwolf` 1.7 | audio (stdin with `-r 24000 -`, soundcard, UDP) → text, KISS TCP, APRS-IS | github.com/wb2osz/direwolf |
| minimodem | Bell 103/202, RTTY, generic FSK at any baud | any | `minimodem` 0.24 | audio → text | github.com/kamalmostafa/minimodem |
| rtlamr | Itron ERT/AMI meters: SCM, SCM+, IDM, NetIDM, R900 | 900 MHz ISM (US) | not in apt: `go install github.com/bemasher/rtlamr@latest` | **rtl_tcp** → text/JSON | github.com/bemasher/rtlamr |
| rtl-wmbus + wmbusmeters | Wireless M-Bus meters (T1/C1/S1) | 868.95 / 868.3 MHz | not in apt: build rtl-wmbus; wmbusmeters from its own repo, snap or build | rtl_sdr IQ on stdin → telegrams → wmbusmeters JSON/MQTT | github.com/xaelsouth/rtl-wmbus, github.com/wmbusmeters/wmbusmeters |
| gr-lora_sdr | LoRa PHY (SF7-12), Meshtastic frames | 433/868/915 MHz | not in apt: `conda install -c tapparelj -c conda-forge gnuradio-lora_sdr` into `gnu`, or build | GNU Radio blocks | github.com/tapparelj/gr-lora_sdr |
| TPMS | tyre pressure sensors | 315/433 MHz | via `rtl-433` (many TPMS decoders) | as rtl_433 | standalone rtl_tpms is unmaintained |

Example pipelines:

```bash
rtl_fm -f 152.84M -s 22050 - | multimon-ng -t raw -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -a FLEX -
rtl_fm -f 144.8M -s 24000 - | direwolf -c sdr.conf -r 24000 -D 1 -
rtl_tcp & rtlamr -server=127.0.0.1:1234 -format=json
rtl_sdr -f 868.95M -s 1600000 - | rtl_wmbus | wmbusmeters stdin:rtlwmbus auto   # unverified flags
```

## Aviation and maritime

| Tool | Decodes | Band | apt on 24.04 | Input → output | Upstream, status |
|---|---|---|---|---|---|
| readsb (+ tar1090 map) | ADS-B / Mode S | 1090 MHz | not in apt: wiedehopf's `readsb-install.sh` builds a .deb, or Docker | RTL, Soapy, HackRF; **`--ifile` with `--iformat UC8/SC16` (file or stdin IQ)** → Beast, SBS, `aircraft.json`, JSON port | github.com/wiedehopf/readsb, very active |
| dump1090-fa | ADS-B / Mode S | 1090 MHz | not in apt: FlightAware repo (mostly Pi) or build | RTL, Soapy, HackRF, `--ifile` → Beast, SBS, `aircraft.json` | github.com/flightaware/dump1090, active |
| dump1090-mutability | ADS-B / Mode S | 1090 MHz | `dump1090-mutability` 1.15 | RTL, `--ifile` → Beast, SBS (no JSON) | archived 2019, use readsb |
| gr-air-modes | ADS-B / Mode S | 1090 MHz | `gr-air-modes` (GR 3.10 build) | GNU Radio block → text, SQLite, KML | upstream stale |
| dump978-fa | UAT (US only) | 978 MHz | not in apt: build / Docker | SoapySDR only → raw + JSON on ports/stdout | github.com/flightaware/dump978 |
| acarsdec | ACARS | 129-131 MHz | not in apt: build (needs libacars) | owns RTL/Soapy/Airspy, or audio/file → JSON (file, UDP, MQTT) | github.com/TLeconte/acarsdec (f00b4r0 fork active), 3.7 (2025) |
| dumpvdl2 | VDL Mode 2 | 136.6-136.975 MHz | not in apt: build (libacars, SoapySDR) | **`--iq-file -` raw IQ on stdin (U8/S16)**, RTL, Soapy → JSON | github.com/szpajder/dumpvdl2, 2.7.0 (2026). vdlm2dec is archived. |
| dumphfdl | HFDL (ACARS over HF) | 2-22 MHz channels | not in apt: build (libacars, liquid-dsp, SoapySDR) | **`--iq-file` stdin/file (CU8/CS16)**, Soapy → JSON | github.com/szpajder/dumphfdl, 1.7.0 (2025) |
| JAERO | Inmarsat Aero / STD-C | 1.5 GHz L-band | not in apt: build (Qt) | audio or ZMQ → text | github.com/jontio/JAERO, slow |
| AIS-catcher | AIS channels A and B | 161.975 / 162.025 MHz | not in apt: build, install script (.deb), Docker | RTL, Soapy, HackRF, rtl_tcp, SpyServer, files → NMEA, JSON, web map | github.com/jvde-github/AIS-catcher, very active (0.70, 2026) |
| rtl-ais | AIS | 162 MHz | not in apt: build | RTL direct only → NMEA over UDP | github.com/dgiardini/rtl-ais, stale |
| gnuais | AIS | 162 MHz | `gnuais` 0.3.3 | discriminator audio only → NMEA | old |
| fldigi (NAVTEX mode) | NAVTEX / SITOR-B | 518 / 490 kHz | `fldigi` 4.2.03 | USB audio → GUI, log | w1hkj.com |
| acarshub + acars_router | Web dashboard for acarsdec/dumpvdl2/dumphfdl JSON | – | Docker only (sdr-enthusiasts) | JSON/ZMQ | github.com/sdr-enthusiasts/docker-acarshub |

## Digital voice and trunking

The AMBE/IMBE vocoder (mbelib) is not in Debian or Ubuntu, because of
DVSI's patents. That is why the apt `dsdcc` finds frames but plays no voice.
None of these tools decodes traffic encrypted with a key you don't have, and
the law on listening and recording differs by country: check it locally.

| Tool | Protocols | apt on 24.04 | Input → output | Upstream, status |
|---|---|---|---|---|
| dsd-fme | P25 P1/P2, DMR, NXDN, YSF, dPMR, D-Star, X2-TDMA, EDACS/ProVoice, M17 | not in apt: build (includes its own mbelib) | discriminator audio on stdin, **rtl_tcp**, Pulse, TCP → audio, ncurses UI, event log | github.com/lwvmobile/dsd-fme, very active |
| dsd-neo | as dsd-fme, fuller M17 | not in apt: build | as dsd-fme | github.com/arancormonk/dsd-neo, newest fork |
| dsdcc (`dsdccx`) | DMR, D-Star, dPMR, YSF (no P25, no NXDN) | `dsdcc` 1.9.3, **built without mbelib** | 48 kS/s S16LE audio on stdin → 8 kS/s audio, or MBE frames | github.com/f4exb/dsdcc. Voice needs an AMBE dongle via `libserialdv`, or your own build. |
| dsd (classic) | P25 P1, DMR (partial), D-Star | not in apt: build + mbelib | audio stdin → audio | github.com/szechyjs/dsd, superseded |
| mbelib / mbelib-neo | AMBE/IMBE vocoder library | not in apt: build | – | github.com/arancormonk/mbelib-neo (active) |
| OP25 (boatbod) | P25 P1/P2 conventional and trunked, SmartNet | not in apt: GNU Radio OOT build (check it builds on GR 3.10) | owns the radio (osmosdr), files, UDP → UDP audio, web UI | github.com/boatbod/op25, active |
| trunk-recorder | Trunked P25 P1/P2, SmartNet; conventional DMR/analog | not in apt: build (GR 3.7-3.10) or Docker | owns the radio (GNU Radio sources) → a WAV plus JSON per call | github.com/TrunkRecorder/trunk-recorder, 5.2 |
| SDRTrunk | P25 P1/P2, DMR, NXDN, MPT-1327, LTR, Fleetsync, MDC-1200 | not in apt: Java release bundle from GitHub | owns the USB radio, or recordings → audio, streaming | github.com/DSheirer/sdrtrunk, very active |
| osmo-tetra-sq5bpf + telive | TETRA voice (TEA0 only), SDS, signalling | not in apt: build | GNU Radio demod → `tetra-rx` → UDP → telive | github.com/sq5bpf/telive |
| tetra-kit | TETRA downlink | not in apt: build | unverified | gitlab.com/larryth/tetra-kit, quiet |
| m17-cxx-demod | M17 (Codec2) | not in apt: build (needs `libcodec2-dev`, in apt) | 48 kS/s baseband on stdin → audio | github.com/mobilinkd/m17-cxx-demod |
| codec2 | Codec2 vocoder tools | `codec2` 1.2.0 | – | rowetel.com |
| GopherTrunk | P25, DMR, TETRA, NXDN and more; own IMBE/AMBE in Go | not in apt: static binary | owns RTL pool → TUI and web console | github.com/MattCheramie/GopherTrunk, months old, unproven |

## Broadcast and satellites

| Tool | Decodes | Band | apt on 24.04 | Input → output | Upstream, status |
|---|---|---|---|---|---|
| redsea | RDS / RBDS, RadioText+, TMC | FM 87.5-108 MHz | not in apt: build (meson, `libsndfile1-dev`, `libliquid-dev`, all in apt) | `rtl_fm -M fm -l 0 -A std -s 171k` MPX on stdin, or WAV/MPX file → line JSON | github.com/windytan/redsea, 1.x, active |
| gr-rds | RDS | FM | `gr-rds` 3.10 | GNU Radio blocks → RDS fields | github.com/bastibl/gr-rds |
| welle.io / welle-cli | DAB / DAB+ | VHF Band III 174-240 MHz | `welle.io` 2.4 (ships `welle-io` and `welle-cli`) | RTL, Soapy, Airspy, rtl_tcp → audio, DLS text, slideshow; welle-cli has a web server | github.com/AlbrechtL/welle.io |
| Qt-DAB | DAB / DAB+ | Band III | not in apt: AppImage | RTL, HackRF, Airspy, SDRplay, Lime, Pluto | github.com/JvanKatwijk/qt-dab |
| nrsc5 | HD Radio (US hybrid FM) | FM band | not in apt: build | RTL direct, IQ file → audio, station info | github.com/theori-io/nrsc5 |
| Dream | DRM (digital shortwave/AM) | HF, needs an upconverter or V4 HF | not in apt: build | audio / IQ via soundcard → audio | drm.sourceforge.net |
| SatDump | Meteor-M LRPT, Metop, FengYun, Elektro, GOES HRIT, NOAA APT archives, Inmarsat and many more | 137 MHz VHF and L-band | not in apt: .deb / AppImage from GitHub releases | RTL, HackRF, Airspy, Soapy, baseband files → images, products | github.com/SatDump/SatDump, 1.2.x |
| meteor_demod + meteor_decode | Meteor-M LRPT | 137.1 / 137.9 MHz | not in apt: build | IQ WAV → soft symbols → images | github.com/dbdexter-dev/meteor_demod |
| aptdec / noaa-apt | NOAA APT | 137 MHz | not in apt: build / release binary | WAV → PNG | github.com/Xerbo/aptdec, github.com/martinber/noaa-apt |
| goestools | GOES HRIT/EMWIN | **L-band 1694.1 MHz** (dish + LNA/filter) | not in apt: build | RTL, Airspy → images | github.com/pietern/goestools |
| gr-satellites | Telemetry of hundreds of amateur satellites and CubeSats | VHF/UHF/S-band | `gr-satellites` 5.5 | GNU Radio, IQ/WAV files, UDP → frames, telemetry, JSON | github.com/daniestevez/gr-satellites, active |
| gpredict | Satellite pass prediction and rotator/radio Doppler control | – | `gpredict` 2.3 | TLE download | github.com/csete/gpredict |
| wsjtx | FT8, FT4, WSPR, JT65 … | HF (with upconverter / V4 HF), 6 m, 2 m | `wsjtx` 2.7.0-rc3 | USB audio from a receiver (e.g. gqrx UDP audio) → decodes | sourceforge.net/projects/wsjt |

NOAA-15, 18 and 19 were retired in 2025, so there is no live APT now.
aptdec and noaa-apt only matter for old recordings or a CTF WAV. The live
137 MHz weather target is Meteor-M LRPT, with SatDump.

## Fit with this app

Receive's rtl_433 card pipes the radio's samples into a process and reads
JSON back. These tools take IQ or MPX the same way and write JSON, so they
could become cards like it:

| Tool | What it takes | What it writes |
|---|---|---|
| dumpvdl2, dumphfdl | IQ on stdin (`--iq-file -`, U8/S16) | JSON |
| readsb | IQ on stdin (`--ifile -` with `--iformat`) | JSON port / `aircraft.json` |
| redsea | MPX on stdin | line JSON (an independent check of `rds_core.py`) |
| multimon-ng, direwolf | demodulated audio | text |
| AIS-catcher | file / rtl_tcp / Soapy | JSON / NMEA |

These tools want to own the radio themselves: SDRTrunk, OP25,
trunk-recorder, acarsdec, rtl-ais and dump978. The app would need a second
radio for them, or would have to serve its own through rtl_tcp or
SoapyRemote.

## What to install

The Ubuntu names starting `rtl` are only `rtl-sdr` and `rtl-433` (installed).
The rest have their own names. All of the packages below exist in 24.04's
apt (checked 2026-09-25):

```bash
# The base: system rtl_* tools (off the conda env), a receiver GUI, IQ analysis
sudo apt install rtl-sdr gqrx-sdr inspectrum sox

# Data modes: pagers, APRS, generic FSK, HF digital modes, SSTV
sudo apt install multimon-ng direwolf minimodem fldigi qsstv

# Broadcast, aviation, satellites
sudo apt install welle.io dump1090-mutability gpredict gr-satellites wsjtx

# Audio/spectrogram inspection (flags painted in a waterfall, audio stego)
sudo apt install audacity sonic-visualiser

# Optional: codec2 tools, another Soapy GUI, sharing a radio over the network
sudo apt install codec2 cubicsdr soapyremote-server
```

Not in apt, but worth having, roughly in order:

1. **Universal Radio Hacker**: `pip install urh`. Unknown OOK/FSK/PSK to bits.
2. **SDR++**: the .deb from GitHub releases.
3. **redsea**: build it. It decodes RDS to JSON, useful for checking `rds_core.py`.
4. **AIS-catcher**: its install script, or build it.
5. **readsb + tar1090**: wiedehopf's install script (use instead of dump1090-mutability).
6. **dsd-fme** (or dsd-neo): build it, for DMR/P25/NXDN voice.
7. **SatDump**: the .deb from GitHub releases, for Meteor-M.
8. **dumpvdl2 / dumphfdl / acarsdec**: build them (libacars first).
9. **rtlamr**: `go install`. **gr-lora_sdr**: conda into `gnu`.
10. **SigDigger**: AppImage.

Installing `gqrx-sdr`, `gr-satellites` or `gr-osmosdr` pulls in more of
apt's GNU Radio 3.10.9, which sits beside the conda env's GNU Radio. The app
runs in the conda env, so the two don't meet. Still, don't install GNU Radio
OOT modules into both.
