# Digital radio: HD Radio on the local FM band, and the other standards

This note covers the digital radio a US listener with these SDRs can find,
and which FM stations here carry it. It has four parts:

- **What is on air here.** HD Radio decoded from a capture of the band
  (checked).
- **How HD Radio works.** Enough to recognise it on the spectrum and decode it.
- **How to decode it on Linux.** nrsc5, built without root.
- **The other digital standards.** Whether any can be heard in the US.

Researched on 2026-09-27. The desk research was done by Haiku agents searching
the web. Their station list was then checked against the air: the table below
comes from nrsc5 decoding an 8 s BB60D capture of 80-120 MHz, not from web
lists. "unverified" marks what was not checked.

## On air here (decoded 2026-09-27)

The receiver is in the Washington, DC area. Here is how the table was made:

- The capture was one `fmctl 'capture 8 iq-band'`: 40 MS/s centred on
  100 MHz, from the BB60D.
- Each channel was cut out at nrsc5's input rate, 744,187.5 S/s cs16. The
  two stages were a mix, then `resample_poly` 1/5 and 4/43.
- Each channel was decoded with `nrsc5 -r` once for each program, 0-3.

Station names, subchannel names and now-playing text come from the stations'
own SIS and PSD data, not from web lists. "Mode" is the primary service
mode: MP1 is plain hybrid, and MP2/MP3 are extended hybrid, with more
digital carriers. BER is nrsc5's average bit error rate: under ~0.05 decodes
cleanly, and around 0.2 is marginal.

| MHz | Station (SIS) | Mode | BER | HD1 | HD2 | HD3 | HD4 |
|---|---|---|---|---|---|---|---|
| 88.5 | WAMU | MP2 | 0.016 | WAMU (NPR news) | Bluegrass (a bluegrass show was playing) | – | – |
| 90.9 | WETA | MP1 | 0.22 | WETA Classical | classical (a Bach cantata was playing) | classical (Britten was playing) | – |
| 93.3 | WFLS (Fredericksburg, VA) | MP3 | 0.18 | synced, SIS only; no PSD at this BER | | | |
| 93.9 | WKYS | MP1 | 0.010 | WKYS | WOL 1450 AM, news/talk | – | – |
| 94.7 | WIAD, "94.7 The Drive" ('80s) | MP3 | 0.074 | The Drive | HD2 announced; no PSD in 8 s | – | – |
| 95.5 | WPGC | MP3 | 0.049 | WPGC | 106.7 The Fan (sports) | – | – |
| 99.5 | WIHT, "HOT 99.5" | MP1 | 0.039 | HOT 99.5 | Pride Radio (iHeart) | – | – |
| 100.3 | WBIG, "BIG 100.3" | MP1 | 0.075 | Classic rock | HD2 (no name in PSD) | – | – |
| 103.5 | WTOP | MP3 | 0.044 | WTOP news | Federal News Network 1500 AM | American Standards By The Sea | WRNR 93.5 FM (Annapolis) |
| 104.1 | WLNO | MP1 | 0.23 | Spanish (ads in Spanish) | – | – | – |
| 105.1 | WAVA | MP1 | 0.004 | WAVA (Christian teaching) | Oromo-language programming | Amharic-language programming | – |
| 105.9 | WMAL | MP1 | 0.053 | NewsTalk WMAL | HD2 (PSD: public-service spots) | – | – |
| 106.7 | WJFK, "106.7 The Fan" | MP2 | 0.035 | The Fan (sports) | 980 AM "The Team" | 1580 AM "The Bet" | – |
| 107.7 | WWWT (WTOP's repeater) | MP3 | 0.013 | WTOP news | Federal News Network | American Standards By The Sea | Intense 102.9 |

A dash means no audio service was announced. The rest of what was checked
fell into three groups:

- **Synced, but no station data.** 96.3 (WHUR) and 107.3. There is a weak
  digital signal, but not enough of it for SIS in 8 s.
- **Sidebands, but no decode.** 91.9 and 98.7 show flat sidebands on the
  spectrum, but at too low a level to decode.
- **No HD found.** 89.3, 89.9, 90.1, 91.3, 92.3, 92.5, 92.7, 94.3, 95.9,
  96.7, 97.1, 97.9, 99.1, 101.1, 102.3, 102.7, 105.7, 106.3, 106.5, 107.9.
  Some are simply weak here. A station that is weak on analog won't show HD
  either, so "no HD" means "none heard at this antenna", not "not
  transmitted".

The web list the agent built differed from the air on several points. Trust
the table above:

- It called 94.7 WIAD "Classic Hits" with a Channel Q HD2. The air says
  "94.7 The Drive, Nobody Plays More '80s". The HD2 name wasn't sent.
- It gave 103.7 as "WRXR", which is a Chattanooga station, not a DC one.
- It listed HD on 101.1 WWDC, 97.1 WASH and 98.7 WMZQ. None decoded here.
- It gave WTOP HD4 as a "WYRE/AAA simulcast". The air says WRNR 93.5 FM.
  It also had The Gamut on HD3, where the air carries "American Standards By
  The Sea".
- It said WFED left WTOP HD2 in 2022. The air shows Federal News Network
  1500 AM on HD2, on both WTOP and WWWT.

### Still to check (unverified)

The research gave these, and they were not tested here:

- **AM HD.** WSHE 820 is reported as the first all-digital (MA3) AM station
  in the US, in 2018, carrying the WTOP family's freeform "The Gamut". WTEM
  980, WOL 1450 and WSBN 630 are also listed. The BB60D covers AM (9 kHz
  up), but no AM antenna was tried. nrsc5 decodes AM with `--am`.
- **Baltimore stations.** These may reach here with a better antenna or a
  longer capture: WERQ 92.3, WIYY 97.9, WQSR 102.7, WJZ-FM 105.7 and WWMX
  106.5. None decoded in this capture.

## How HD Radio (NRSC-5, "IBOC") works

### FM hybrid layout

The station keeps its analog FM in the middle, ±100 kHz. OFDM sidebands sit
on either side, and together they make a ~400 kHz footprint:

- **MP1 (hybrid).** The primary main sidebands run from ±129.4 to
  ±198.4 kHz: 10 frequency partitions per side, about 69 kHz wide.
- **Extended hybrid (MP2, MP3, MP11).** Adds 1, 2 or 4 partitions on the
  inner edge of each sideband, down to about ±101 kHz. More carriers means
  more capacity for HD2-HD4. WTOP, WWWT, WIAD and WPGC use MP3, and WAMU
  and WJFK use MP2.
- **All-digital (MP5, MP6).** Drops the analog signal. These modes are
  experimental in the US.
- **OFDM.** Subcarrier spacing is 363.4 Hz (1,488,375/4,096). The symbol
  rate is ~344.5 Hz, with a ~2.9 ms symbol. An L1 frame lasts ~1.486 s.
- **Level.** The nominal total digital power is -20 dBc against the analog
  carrier. Since 2010 the FCC has let stations run up to -14 dBc, and up
  to -10 dBc with an interference showing. This capture measured about
  -10 to -20 dBc for the digital sidebands. (Levels recalled from FCC
  practice; the agent's per-mode dBc figures didn't match the standard, so
  they are left out.)

### Services

- **HD1** is the main program, usually the analog simulcast. HD2-HD4 are
  extra audio programs, carved from the same bit rate. The table shows
  how often HD2/HD3 carry a co-owned AM or a sister station.
- **The HDC codec** is proprietary. It is AAC-like, but not standard
  HE-AAC.
- **SIS** (Station Information Service) sends the call sign, facility ID,
  location, slogan and a service list.
- **PSD** (Program Service Data) sends the title and artist for each
  program. It carries ads and slogans too.
- **AAS/LOT** data services carry album art, station logos, and traffic and
  weather maps. nrsc5 can save these with `--dump-aas-files DIR`.
- **Diversity delay.** The analog signal is delayed ~8 s so it lines up
  with the digital audio. A receiver blends to analog when the digital
  signal drops, which is why HD1 and FM sound the same after the blend.

### AM

- **MA1 (hybrid).** Digital carriers sit either side of the analog, over
  about ±15 kHz.
- **MA3 (all-digital).** No analog, about 20 kHz wide. This is WSHE 820.

### On this app's spectrum

HD Radio shows as two flat, rectangular shoulders either side of the
station. Each is about 70 kHz wide at 130-200 kHz from the carrier, sits
10-20 dB below the FM hump, and has sharp edges. Analog FM alone rolls off
smoothly by ±100-120 kHz.

Take care with a neighbour. A station 200 kHz away puts its hump where the
shoulder would be, and a station 400 kHz away puts its own shoulder just
past it. A shoulder counts only when it is flat on both sides and ends at
±198 kHz. In `peaks`, the shoulders do not show up as separate signals.

The **IQ – channel** recording with the channel filter opened to 400 kHz
keeps both sidebands (see usage.md). At 500 kS/s it needs resampling to
744,187.5 S/s before nrsc5 can read it.

## Decoding on Linux: nrsc5

- **Source.** Upstream is github.com/theori-io/nrsc5; this note used
  revision 0225922, from 2026-09-07. It is not in Ubuntu 24.04's apt. It
  decodes HDC through a patched FAAD2, which its build fetches and patches
  itself.
- **Build without root.** It was built here without sudo or apt. A
  throwaway conda env (`conda create -p … -c conda-forge libtool libao
  rtl-sdr fftw autoconf automake pkg-config cmake make c-compiler`) held
  the tools, then:

  ```
  cmake .. -DUSE_SSE=ON -DCMAKE_PREFIX_PATH=$ENV -DCMAKE_BUILD_RPATH=$ENV/lib
  ```

  followed by `make`.
- **Build with apt.** The apt route needs `libtool libao-dev
  librtlsdr-dev` on top of cmake, autoconf and libfftw3-dev.
- **Usage.**
  - `nrsc5 94.7 0`: an RTL-SDR, HD1. The program number is 0-3.
  - `-H host:port`: rtl_tcp. This app's RTL-SDR path already runs one.
  - `-o out.wav`: audio to a file.
  - `-r file --iq-input-format cs16`: an IQ file, either **cu8 at
    1,488,375 S/s** or **cs16 at 744,187.5 S/s**, with the station at
    0 Hz. AM is cs16 at 46,512 S/s with `--am`.
  - Keys 0-3 switch the program while it runs.
- **Output.** nrsc5 logs sync, MER and BER, SIS, the service list, and PSD
  title/artist to stderr. That is what the table above was built from.
- **GUIs and other tools.**
  - NRSC5 Studio (github.com/LTCAshraven/nrsc5-studio; .deb available) and
    nrsc5-gui/-dui wrap the CLI.
  - SDR++ has HD Radio support through nrsc5-based plugins (unverified).
  - gr-nrsc5 (github.com/argilo/gr-nrsc5) is a GNU Radio **transmitter**.
    It is for test signals, not a receiver.
- **In this app.** The Receive tab's **HD Radio** box does it live
  (2026-09-27): it pipes the station's IQ to `nrsc5 -r -` and plays its
  audio (usage.md, "HD Radio"). For a survey of every station at once,
  record the band with **IQ – band** and resample offline, as the table
  above was made: about 5 min of CPU for 38 channels × 8 s.
- **Offline, from a Sceptre DVR** (2026-09-30): **`tools/dvr-hd DVR.sdvr`**.
  A Sceptre DVR made from its IQ tab (`sceptre-dvr.md`) holds 27 MHz of the
  band at 28 MS/s for about 8.6 s, so every station in it can be decoded
  without the app. The tool finds the stereo stations (the pilot's SNR),
  runs nrsc5 on each for HD1-HD4 and writes the WAVs and a `summary.json`
  (services, kbps, MER, BER); `--info`, `--scan`, named stations, `--programs`
  and `--keep-iq` are in usage.md. It needs only numpy, scipy and nrsc5 (no
  GNU Radio). What it does, and by hand:
  1. Read the runs in time order (`sceptre_dvr.scan`), mix the station to
     0 Hz, decimate by 14 (28 to 2 MS/s) with a low-pass, then resample by
     11907/32000 to **744,187.5 S/s** and write cf32. Filter in chunks with
     the overlap trimmed, or the chunk joins glitch every few milliseconds.
  2. `nrsc5 -r station.cf32 --iq-input-format cf32 -o hd1.wav -t wav 0`,
     then again with `1`, `2`, `3` for HD2-HD4. Its audio is 44.1 kHz stereo.
  - **The rate must be exact.** The 1,488,375 S/s of the cu8 format above is
    wrong for cf32 and cs16: nrsc5 then exits cleanly with an empty WAV
    (68 bytes) and no log at all.
  - **Why offline.** Played in the app the DVR loops every 8.6 s and nrsc5
    loses sync at each seam, so HD1 played whole but HD2 and HD3, which take
    longer to come in, were audible only 40-80% of the time. One pass through
    nrsc5 gave every program 6 s unbroken (92-98% audible).
  - **What came out** (the capture of 2026-09-29, -20 dBm reference, 19
    stereo stations in 88-108 MHz): five stations with HD Radio decoded, with
    two to three programs each at 22-47 kbps, service names and all, at MER
    of only 1-4 dB; two more had a digital signal too weak to decode (negative
    MER). A better signal than the live app's: 99.5 counted as lost live, and
    decoded offline. MER, BER and the service names come from nrsc5's log as
    usual.

## Other digital radio, and the US

- **DAB / DAB+** is not used in the US; it is on air in Europe, the UK,
  Australia and elsewhere. It needs Band III (174-240 MHz), which every
  radio here covers. Decoders are `welle.io` (apt, 2.4) and `dablin` (apt,
  1.15). There is nothing to hear here.
- **DRM (shortwave, MW)** has no US domestic DRM broadcasts. WINB in Red
  Lion, PA has run DRM tests on shortwave, beamed to Europe/North Africa
  (7325, 9265 and 15670 kHz, per drm.org). Its reception in the US is
  unverified. International DRM from Europe can reach the East Coast. The
  decoder is Dream (not in apt: build it). The BB60D and a HackRF with a
  shortwave antenna could try; an RTL-SDR needs direct sampling or an
  upconverter.
- **DRM+ (VHF)** is not authorised in the US.
- **ATSC 3.0** is TV. Radio-style audio services over it are in tests,
  with Sinclair and Fraunhofer among others, in a few markets. Treat it as
  unverified here.
- **SiriusXM** is on 2320-2345 MHz and encrypted. You can see it on the
  spectrum (HackRF or BB60D), but it can't be decoded.

## Sources

- **Standards.**
  - NRSC-5-E (nrscstandards.org), and the FM transmission spec
    1026s (Rev. G) for the sideband and partition layout.
  - Wikipedia "NRSC-5", and sigidwiki "HD Radio (FM)".
- **nrsc5.** The theori-io/nrsc5 README, and rtl-sdr.com's articles on
  nrsc5 and NRSC5 Studio.
- **Stations** (desk research only; the air overrides it):
  - hdradio.com/stations/?state=DC
  - Wikipedia's "List of radio stations in Washington, D.C." and the
    station articles
- **Other standards.** welle.io; Dream on sourceforge.net/projects/drm;
  drm.org's WINB test page; atsc.org's ATSC 3.0 audio news.
