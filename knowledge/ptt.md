# PTT (push-to-talk) radio: what is on air, how it looks, what classifies it

Research note, 2026-11-01. Not yet checked against a capture from this app.
Written for the US. The aim is the same as the rest of this repo: say what a
signal *is* from how it looks (bandwidth, burst timing, modulation levels,
sync words), not a decoder per protocol.

**How it was made.** Six independent research runs, two per angle: Claude
Haiku 5.5 subagents (web search and fetch) and Muse Spark 1.3 Contributor
through OpenCode (`:online`, read-only `swarm-research` agent). Where they
agreed the fact is stated plainly. Where only one source or one model said
it, or only a search snippet backed it, it is marked *unverified*. CFR text
was fetched for the FRS/GMRS/MURS figures; most digital-mode figures come
from sigidwiki, vendor and forum pages. Check anything you build on.

## What "PTT" covers

| Family | RF look | Section |
|---|---|---|
| Analog narrowband FM (FRS, GMRS, MURS, CB, business, marine, ham) | one 11-20 kHz FM stripe, keyed on and off | 1 |
| Digital land mobile (DMR, P25, NXDN, dPMR, TETRA) and ham digital voice (D-STAR, Fusion, M17) | 6.25-25 kHz, 4-level FSK or PSK, some TDMA | 2 |
| Cellular and app PTT (PoC, MCPTT, FirstNet, Zello) | ordinary LTE/NR: no PTT signature | 3 |
| Satellite PTT (Iridium) | L-band TDMA bursts | 3 |

## 1. Analog PTT

### Services (US)

| Service | Channels | Notes |
|---|---|---|
| FRS (47 CFR 95 B) | 22 x 12.5 kHz: 1-7 at 462.5625-462.7125, 8-14 at 467.5625-467.7125, 15-22 at 462.5500-462.7250 MHz | no licence; ERP 2 W on 1-7 and 15-22, 0.5 W on 8-14 (95.567) |
| GMRS (95 E) | 30: 16 main (462.550-462.725 and 467.550-467.725), 14 interstitial | licence; mobile/base/repeater up to 50 W, 462 MHz interstitials 5 W ERP, 467 MHz interstitial handhelds 0.5 W (95.1767). Repeater: output 462.55xx, input +5 MHz (secondary source) |
| MURS (95 J) | 151.820, 151.880, 151.940 (11.25 kHz); 154.570, 154.600 (20 kHz) | no licence, 2 W, no repeaters |
| CB (95 D) | 40 x 10 kHz, 26.965-27.405 MHz | AM, SSB; 4 W AM, 12 W PEP SSB (secondary). FM status *unverified* |
| Part 90 business / itinerant | VHF 150-174, UHF 450-470 MHz | licensed. 12.5 kHz-capable gear: 11.25 kHz authorised bandwidth (90.209); 2.5 kHz deviation (secondary) |
| Marine VHF | 25 kHz spacing; ch 16 = 156.800 MHz | NBFM, about 5 kHz deviation (*unverified*) |
| Ham 2 m / 70 cm FM | 146.520 simplex (*unverified* here); repeater offset 600 kHz (2 m), 5 MHz (70 cm) | |

### Signalling inside the FM (seen after demodulation, not in IQ)

- **CTCSS**: continuous sub-audible tone, EIA/TIA-603 set of 37 tones,
  67.0-250.3 Hz (more in extended sets, e.g. Motorola's 254.1 Hz). Deviation
  roughly 0.5 kHz or less (*unverified*). Detect with a low-frequency FFT or
  Goertzel bank on the discriminator output.
- **DCS (CDCSS)**: a 134.4 bit/s stream repeating a 23-bit Golay (23,12) word:
  9 code bits, 3 fixed bits, 11 check bits. Written D023N / D023I (normal /
  inverted). Rate and structure from one model; *unverified* against a source.
- **DTMF**: rows 697/770/852/941 Hz, columns 1209/1336/1477/1633 Hz.
- **Selcall** (for example ZVEI-1): five tones per digit, 70 ms each. Digit
  tones 2400, 1060, 1160, 1270, 1400, 1530, 1670, 1830, 2000, 2200 Hz.
  Not common on US business bands (*unverified*).
- **Squelch tail / reverse burst**: a noise burst of order 100-300 ms after
  unkey, removed on repeaters by a phase-flipped CTCSS burst. Timing
  *unverified*.

### On a waterfall

A single stripe of about 11-16 kHz that switches hard on and off, seconds
long with long gaps. A repeater shows a correlated pair (600 kHz or 5 MHz
apart) with a hang time of 1-2 s. Broadcast FM is the opposite: 200 kHz wide,
always on, 19 kHz pilot and 57 kHz RDS. CB is AM or SSB, so it has a
carrier-plus-sidebands or a single-sideband look, with skip fading.

## 2. Digital PTT

| Mode | Channel | Modulation | Rate | Frame / sync |
|---|---|---|---|---|
| DMR (Tier I/II/III; MotoTRBO, Hytera) | 12.5 kHz | 4FSK, h=0.27: +-648 / +-1944 Hz | 4800 sym/s, 9600 bit/s | 2-slot TDMA, 30 ms slot, 60 ms frame, 360 ms voice superframe. 48-bit syncs in the **ETSI table** (TS 102 361-1 Table 9.2): see below |
| P25 Phase 1 | 12.5 kHz | C4FM (CQPSK also permitted) | 4800 sym/s, 9600 bit/s | 48-bit (24 symbol) frame sync; 0x5575F5FF77FF is the value shown in a lab capture (Blaze et al., USENIX Security 2011, Fig. 8), not read as a normative constant; NAC 12 bit (0x293 in that capture) |
| P25 Phase 2 | 12.5 kHz, trunked only | H-DQPSK outbound, H-CPM inbound | 6000 sym/s (Viavi Phase 2 note; H-DQPSK outbound), 12 kbit/s | 2-slot TDMA, 30 ms slots |
| NXDN | 6.25 or 12.5 kHz | 4FSK, symbols +-1 = +-350 Hz, +-3 = +-1050 Hz, RC alpha 0.2 | 2400 baud, 4800 bit/s (6.25 kHz, read in TS 1-E); 12.5 kHz mode: 9600 bit/s, 4800 sym/s, dibit deviations +-800 / +-2400 Hz (TS 1-A v1.3 Tables 2.3-1 and 3.3-1, read in a second project's text extract) | frame sync word CDF59 (20 bit = 10 symbols -3,+1,-3,+3,-3,-3,+3,+3,-1,+3); 80 ms frame of 384 bits: preamble, FSW 20, LICH 16, SCCH 60, then FACCH1 or 4 x VCH 72 (NXDN TS 1-E v1.1, 4.4-4.5) |
| dPMR | 6.25 kHz | 4FSK, h=0.29, 1050 Hz outer deviation | 2400 baud, 4800 bit/s | 80 ms frame, 320 ms superframe. Frame syncs FS1 57FF5F75D577 (48 bit, header), FS2 5FF77D and FS3 7DDFF5 (24 bit; superframe / end), FS4 FD55F5DF7FDD (48 bit, packet data); ETSI TS 102 490 and 658 agree |
| TETRA (not US-common) | 25 kHz (*unverified* in spec text), 4 slots | pi/4-DQPSK | 18 ksym/s (derived from the 255-symbol, 14.17 ms slot), 36 kbit/s | 56.67 ms frame, 18-frame multiframe (1.02 s), 60 multiframes = 61.2 s hyperframe. Training sequences, EN 300 392-2 9.4.4.3: normal 1 `1101000011101001110100`, normal 2 `0111101001000011011110`, sync burst (38 bit) `11000001100111001110100111000001100111` |
| D-STAR DV | 6 kHz or less (JARL v6.0a 3.1.2) | GMSK (QPSK, 4FSK allowed), BT 0.5 | 4.8 kbit/s max; header 1200 bit/s MSK | 64-bit `1010` bit sync, 15-bit frame sync `111011001010000`, 24-bit resync `101010101011010001101000` (JARL 4.1.1, p.25/29) |
| Yaesu System Fusion | 12.5 kHz | C4FM | 4800 sym/s, 9600 bit/s | 40-bit sync (hex *unverified*) |
| M17 | 9 kHz occupied, 12.5 kHz spacing | RRC alpha 0.5 4FSK, h=1/3, symbols +-0.8 / +-2.4 kHz | 4800 sym/s, 9600 bit/s | 16-bit syncs: LSF 0x55F7, BERT 0xDF55, stream 0xFF5D, packet 0x75FF; 384-bit frame = 40 ms; CRC 0x5935 (spec v3.0.0-draft, Table 2.3) |

Checked against the spec text (second pass, section "Primary sources"):
DMR, dPMR, TETRA (training sequences, timing), D-STAR and M17. Still
second-hand: P25 and NXDN rows, Fusion, and the TETRA 25 kHz carrier width.

**DMR sync patterns** (TS 102 361-1 V2.6.1 Table 9.2, 48 bit; voice and data
are symbol-wise complements):

| Pattern | Hex |
|---|---|
| BS sourced voice | 755FD7DF75F7 |
| BS sourced data | DFF57D75DF5D |
| MS sourced voice | 7F7D5DD57DFD |
| MS sourced data | D5D7F77FD757 |
| RC sync | 77D55F7DFD77 |
| TDMA direct slot 1 voice / data | 5D577F7757FF / F7FDD5DDFD55 |
| TDMA direct slot 2 voice / data | 7DFFD5F55D5F / D7557F5FF7F5 |
| Reserved | DD7FF5D757DD |

The earlier open-source value for mobile data was wrong or conflicting;
use D5D7F77FD757. Burst: 108 + 48 (sync or embedded signalling) + 108 bits =
264 bits in 27.5 ms of a 30 ms slot. The 4-bit colour code is in the slot
PDU; All-System CC = 0xF. Data type is a 4-bit field (CSBK 0011, voice LC
header 0001, terminator 0010, data header 0110, idle 1001).

### Telling them apart in IQ (inference, not sourced)

1. **Occupied bandwidth**: about 6 kHz = D-STAR, NXDN 6.25, dPMR; 9-10 kHz =
   DMR, P25, Fusion, M17; about 22-25 kHz = TETRA.
2. **Burst timing**: DMR and P25 Phase 2 chop every 30 ms (one slot busy,
   one idle in a single call); TETRA frames are 56.67 ms. P25 Phase 1,
   NXDN, dPMR, D-STAR, Fusion and M17 are continuous while keyed.
3. **Discriminator levels**: 4FSK/C4FM give four levels at about +-1 and +-3
   deviation steps; GMSK gives two smooth levels; DQPSK/H-DQPSK give no level
   staircase (phase transitions instead).
4. **Sync word** correlated on the symbol stream names the protocol (DMR's
   six 48-bit syncs, M17's four 16-bit ones, P25's 48-bit sync).

Open decoders, for reference (we are not planning to add voice decoding):
dsd-fme (P25 P1/P2, DMR, NXDN, YSF, M17; ISC-style licence per its README,
the full mode list was not confirmed), OP25 (boatbod fork), SDRTrunk (Java),
m17-cxx-demod, MMDVM/Pi-Star for hotspots. AMBE/IMBE vocoders carry patent
friction.

### Encryption and law (US, not legal advice)

DMR offers basic privacy (XOR), RC4-40 and AES options; P25 offers ADP
(RC4-40), DES-OFB and AES-256. Analog cannot be encrypted.
18 U.S.C. 2511(2)(g)(ii) excepts radio communications that are readily
accessible to the public, including public-safety and private land mobile
systems, but an exception tied to "not scrambled or encrypted"; prohibited
interception carries up to five years (2511(4)(a)). 47 U.S.C. 605 is aimed at
divulging what you intercepted; enforcement policy was not confirmed. One run
cited 18 U.S.C. 2512 on devices (*unverified*). State scanner laws vary
(not checked). The practical line for this project: classify, never attempt
to decrypt.

## 3. Cellular, app and satellite PTT

**Bottom line (both runs agree):** cellular PTT is ordinary LTE/NR IP traffic.
At the air interface it cannot be told from video, web or VoLTE. Floor control,
talkgroups and emergency flags sit above PDCP, and the payload is encrypted.

- **3GPP MCPTT**: stage 1 TS 22.179; architecture TS 23.379 (it replaced
  TS 23.179 from Rel-14); call control TS 24.379; floor control TS 24.380;
  media TS 26.179 (AMR-WB mandatory, EVS optional). Voice bearer 5QI 65.
  No longer LTE-only in later releases.
- **FirstNet PTT (AT&T)**: described as built on MCPTT; launch date reports
  conflict (2019 vs 2020). Runs on AT&T's LTE including Band 14 (758-768 MHz
  down, 788-798 MHz up), but Band 14 carries all FirstNet traffic, not only PTT.
- **Carrier PoC**: AT&T Enhanced PTT and Verizon PTT+ are described as
  Kodiak-based PoC integrated with the carrier core (one run; another called
  Verizon's a carrier product and, separately, a 3GPP MC-PTT service). They do
  not interoperate without gateways.
- **Zello**: an internet app (proprietary protocol, Opus audio) over any data
  connection. Nothing at RF beyond generic LTE/NR or Wi-Fi.
- **Iridium PTT**: commercial since 2015; vendor datasheet gives 2.4 kbit/s.
  The phone link is L-band 1616-1626.5 MHz TDMA/FDMA; secondary sources say
  DQPSK at 25 ksym/s, 31.5 kHz carriers, 90 ms frames (*unverified*).

### The one hook for our LTE/NR decoder

A cell can broadcast group traffic, and the cell's SIBs say so:

- LTE **SIB13** carries MBMS (eMBMS) control (MCCH acquisition, MBSFN area);
  **SIB15** MBMS service-area / mobility information; **SIB2** reserves
  MBSFN subframes.
- LTE **SC-PTM** (Rel-13): **SIB20** gives the SC-MCCH configuration; group
  data goes to a G-RNTI on the PDSCH. SC-RNTI is FFFC (patent text, secondary).
  One run wrongly tied SC-PTM to SIB13; the other had it right (SIB13 is
  eMBMS, SIB20 is SC-PTM).
- NR **MBS** (Rel-17): SIB20 and SIB21 are named in one run; *unverified*.
- No SIB carries an "MCPTT supported" flag (a negative found in no source,
  *unverified*). MCPTT is announced at the IMS/application layer.

So `iq-lte` / `iq-nr` could report "SIB13 / SIB20 present" as a fact about
the cell's broadcast capability. It must never be reported as "PTT": presence
does not show PTT is in use, and absence only says unicast.

## Primary sources (second pass)

Which specs are free, checked 2026-11-01. The PDFs were read from the
scratchpad and are **not** in the repo (ETSI, JARL, the M17 project and
Crown-copyright MPT 1327 have their own terms; link, do not copy).

| Spec | Free? | URL |
|---|---|---|
| DMR TS 102 361-1 V2.6.1 (air interface), -2 V2.5.1, -3 V1.3.1, -4 V1.12.1 (Tier III trunking) | yes | etsi.org/deliver/etsi_ts/102300_102399/10236101/02.06.01_60/ts_10236101v020601p.pdf (and 10236102, 10236103, 10236104 alike). etsi.org returned 403 without a browser User-Agent |
| dPMR TS 102 490 (licence-free) and TS 102 658 (licensed, Modes 1-3) | yes | etsi.org/deliver/etsi_ts/102400_102499/102490/ and 102600_102699/102658/. Newer revisions exist (one run saw 102 490 V1.9.1, 102 658 V2.6.1); the sync values were read in V1.8.1 and V2.3.1 |
| TETRA EN 300 392-2 V3.8.1 | yes | etsi.org/deliver/etsi_en/300300_300399/30039202/03.08.01_60/en_30039202v030801p.pdf |
| M17 protocol spec v3.0.0-draft (2025-11-16) | yes | m17project.org/wp-content/uploads/2025/11/M17_spec.pdf |
| D-STAR, JARL Digitalization Technology Standard v6.0a | yes | jarl.org/English/7_dstar/STD6_0_E.pdf |
| D-STAR technical requirements (ARRL-hosted, 2005) | yes | centennial-qp.arrl.org/files/file/D-STAR.pdf |
| MPT 1327 (1988/91/97) | downloadable; Crown Copyright | sigidwiki.com/images/8/85/Mpt1327.pdf |
| P25 TIA-102 suite (BAAA-B air interface, BBAB Phase 2, AABC-E-3 control channel, CAAB-E modulation, BBAD-A) | **no**: sold by TIA/IHS; free only to US government agencies on request | project25.org lists the current documents (P25_SC_25-04-001, April 2025) |
| NXDN Technical Standard (Part 1 sub-parts A, C, D, E, F; CAI v2.0) | **official copy unclear**: behind a guest registration and copyright form at nxdn-forum.com/download_file/ (not submitted). Sub-part E (Type-D CAI) v1.1, June 2012, was read from a third-party mirror, www.qsl.net/kb9mwr/projects/dv/nxdn/NXDN-TS-1-E_v0101.pdf (copyright Icom and JVC Kenwood: link, do not copy) | public overview decks: 2014 overview, 2011 trunking, IWCE 2017 |
| EIA/TIA-603 (CTCSS), DCS, Yaesu System Fusion, LTR, SmartNet, EDACS PHY | none found free | |

### Facts read in those specs

- **DMR** (361-1): 12.5 kHz, 4FSK at 4800 sym/s, h = 0.27 (clause 10.2.2.1,
  Table 10.3). Tier I = licence-free direct mode with continuous
  transmission, Tier II = licensed conventional (direct or repeater), Tier
  III = trunking (361-4). Tier III control channels (361-4 clause 5.3):
  dedicated TSCC, composite TSCC (may carry payload when others are busy)
  and asynchronous; signalling in CSBK / MBC blocks (BPTC(196,96)),
  with C_GRANT, C_ALOHA, C_BCAST (Table 5.1). Opcode and field layouts
  (Annex B.1) were *not* extracted.
- **dPMR** (490 / 658): 6.25 kHz FDMA. Header frame 80 ms (preamble 72 +
  FS1 48 + HI0 120 + CC 24 + HI1 120 bits), payload frame 80 ms (FS2 or CC 24
  + CCH 72 + 4 x TCH 72), superframe 320 ms, end frame 96 bits. 658's three
  modes: 1 peer to peer, 2 centralised repeater, 3 managed repeater with a
  beacon channel. The 24-bit channel-code algorithm was not extracted.
- **TETRA** (300 392-2): slot 255 symbols, frame 4 slots, the 18th frame of a
  multiframe is the control frame; 6-bit colour code expands to a 30-bit
  extended colour code for scrambling (expansion not extracted); MCCH is
  timeslot 1 in normal mode.
- **M17**: 40 ms preamble (192 symbols alternating +-3); LSF = 30 bytes,
  240 bits (224 + CRC-16); rate-1/2 K=5 convolutional FEC, punctured to 368
  bits; stream LICH is 40-bit LSF chunks (modulo-6 counter).
- **D-STAR**: header MSK at 1200 bit/s (1200 Hz = 1, 1800 Hz = 0); scrambler
  x^7 + x^4 + 1 initialised to ones; GMSK deviation is not stated in the
  JARL text. End-of-transmission is a 32-bit sync, `000100110101111`, 0.
- **MPT 1327**: 1200 bit/s FFSK; control-channel codeword sync 0xC4D7,
  traffic sync 0x3B28; preamble 16+ bits of `1010`. The two sync values were
  checked in the text.
- **NXDN** (public decks): 4-level FSK, AMBE+2; 6.25 kHz = 4800 bit/s,
  12.5 kHz = 9600 bit/s; Type-C has a control channel, Type-D has none.
  The 6.25 kHz Type-D air interface is read in TS 1-E v1.1 (third-party
  mirror, see the table): symbol rate 2400/s, transmission rate 4800 bit/s;
  dibit 01 = +3 = +1050 Hz, 00 = +1 = +350 Hz, 10 = -1, 11 = -3; root
  raised cosine, roll-off 0.2; preamble of repeated +3 +3 -3 -3 ending in
  5775FD; frame sync word CDF59 (10 symbols); necessary bandwidth 4K00. The
  frame is 80 ms and 384 bits (FSW 20, LICH 16, SCCH 60, then FACCH1 144 or
  voice 4 x 72). Not checked: LICH field values, Type-C (sub-part C). The 12.5 kHz
  mode is in TS 1-A (see the table).
- **P25**: the FAQ says the standards are free to US government agencies
  through TIA's request form (not used). The 48-bit sync
  0x5575F5FF77FF appears in Blaze et al. (USENIX 2011, Fig. 8) as a frame
  sync followed by NID 293...; a second project (SDR, branch ptt-research)
  found it and the Phase 2 rate of 6000 sym/s in a Viavi note. No normative
  TIA text read. The Phase 2 sync 0x575D57F7FF (a Go package) remains an
  unverified lead.

## What Sceptre labels (from the new-voice-journey project)

The nvj project (`/data/python/new-voice-journey`, `apps/session_reader.py`)
reads Sceptre's session database and the WAV names it writes. This is the
vocabulary a classifier of ours should be compared against. Read from nvj's
code and demo data only; no captures were opened here.

- **Filenames**: `{proto}_voice_{freq}hz_slot{n}_{timestamp}.wav` (DMR, with a
  TDMA slot) or `{proto}_voice_{freq}hz_{timestamp}.wav` (the rest).
- **Session DB** (`intercepts`, `intercept_files`, `intercept_attachments`,
  `intercept_rssis`): centre frequency, bandwidth, start time (ns), average
  RSSI, and a JSON attachment with `standard` and `technology` strings.
- **Protocol values nvj maps**: `dmr`, `nxdn`, `ctcss`, `dcs`. Any other
  `standard` or `technology` string passes through unchanged, so Sceptre may
  label more (P25, dPMR, ...) than nvj has special cases for. Which ones it
  really emits is not known from this code.
- **Attachment fields nvj uses**: `encryption` (bool), `source_address`,
  `destination_address`, `call_type` (group, or individual / unit_to_unit /
  private), `channel_type`, `color_code` (DMR), and for analog `tone_code`
  and `tone_freq`.
- **How nvj groups calls**: DMR and NXDN by source and destination address,
  group (talkgroup = destination) or individual (an address pair); CTCSS and
  DCS by frequency and tone. So "PTT" there means conventional,
  address-based voice; there is no trunking-system view (no WACN, system or
  site).

## Versions and variants of each family (from memory, unverified)

The families above are not single protocols. For classifying, these are the
splits that change what is on air:

- **DMR**: Tier I (licence-free, 446 MHz, Europe), Tier II (conventional, 2
  slots), Tier III (trunked; vendor forms: Motorola Capacity Plus, Linked
  Capacity Plus and Connect Plus, Hytera XPT). Vendor extensions
  (MotoTRBO, Hytera) sit on the same air interface. Direct mode (talkaround,
  no repeater) is one slot per call, both slots on a repeater.
- **P25**: Phase 1 (FDMA, C4FM or CQPSK), Phase 2 (TDMA, trunked), conventional
  or trunked; the control channel is continuous 9600 bit/s data. Motorola's
  older SmartNet/SmartZone (3600 baud control) is analog or P25-era trunking
  of its own.
- **NXDN**: Type-C (trunked) and Type-D (conventional); 4800 (6.25 kHz) and
  9600 (12.5 kHz) variants; sold as IDAS (Icom) and NEXEDGE (Kenwood).
- **Other trunking and analog signalling** a PTT survey may meet: EDACS,
  LTR, MPT-1327 (all analog-voice trunking), and ProVoice. Not looked up.
- **Ham**: D-STAR DV and DD, Fusion's four voice modes (DN, VW, FR, data),
  M17 stream versus packet.

These variants matter because the same channel width and 4FSK staircase
holds several of them; the sync word and the control-channel behaviour
separate them. A research pass on each variant (sync words, burst structure,
control channel signalling) would be the next step.

## What this suggests for the app (not planned work)

A PTT *classifier* would use: occupied bandwidth class, keyed on/off duty,
30 ms TDMA slotting, the 4-level FSK staircase, and (for analog) a
sub-audible tone line. Voice decoding is out of scope, as with the other
modes. If this goes onto `knowledge/roadmap.md`, it needs a capture first:
nothing above has been checked on air here.

## What a wide UHF capture of a PTT band looks like (first look, 2026-11-02)

One 20 MS/s IQ capture of 20 MHz of the UHF land-mobile band (93 s, int16,
a training recording of mixed traffic; details are not kept here), looked at
with a 9.8 kHz-bin spectrogram of the whole file and a 12.5 kHz x 2 ms
spectrogram of 10 s. Spectrum only: no demodulation, so nothing below says
which protocol a channel carries.

- **Crowded and mixed.** About 80 occupied groups over the 20 MHz, from 10 kHz
  (one channel) up to 300-400 kHz (many adjacent channels, as on a trunked
  site), 40 to 65 dB over a flat floor. About 90 of the 1600 12.5 kHz bins are
  on all the time (continuous carriers: control channels, repeaters or data),
  the rest are keyed.
- **Keyed activity has three timescales**: voice-like transmissions of 1 to 9 s
  with gaps of 0.6 to 5 s; repeating bursts of 180 to 300 ms about 1.8 s
  apart (data or beacons, on three separate groups, so the same kind of
  thing); and many on-times under 10 ms. The last are mostly an artefact of
  looking at one FSK channel through 12.5 kHz bins (the energy hops between
  bins), so **burst timing needs a channel filter first, not a bin**.
- **What it settles for the app.** A fixed-bin occupancy plot separates
  continuous from keyed channels and gives transmission and gap lengths, but
  not the protocol: for that the channel has to be mixed to baseband, its
  bandwidth class and symbol rate measured, and its slot timing (30 ms DMR /
  P25 Phase 2, 56.67 ms TETRA) read from the envelope. That is the classifier
  this note has been heading for, and this capture is a test case for it.
- **Per-channel pass (same capture, 20 s, two clusters of 12.5 kHz-spaced
  channels, mixed to baseband, 6.5 kHz filter, FM discriminator).** Nine of
  the fourteen channels tried show a four-level staircase with adjacent
  levels about 1300 Hz apart, i.e. symbol levels near +-650 and +-1950 Hz
  (measured 641-674 Hz for the inner level): 4FSK at 4800 sym/s, h = 0.27. In
  those, **the DMR 48-bit sync of TS 102 361-1 Table 9.2 matched with 0 of 24
  symbol errors**, again every 144 symbols (one 30 ms slot), over runs of 4
  to 15 s. This is the first check of our DMR deviation, symbol rate and sync
  values against an off-air signal. Two things it exposed:
  - In the table **each data sync is the symbol-for-symbol negation of the
    matching voice sync** (BS, MS and both direct-mode pairs). A receiver that
    does not know its polarity cannot tell BS voice from BS data. Here the
    sync appeared in nearly every slot, which fits data/control bursts
    (voice carries it only in burst A of a superframe), so the reading is BS
    data in normal polarity; the polarity itself was not independently
    checked.
  - A discriminator seen through a filter as wide as the channel spacing
    shows the neighbour's energy as extra levels: use a filter well inside
    half the spacing (6.5 kHz for 12.5 kHz).
  **Slot type decoded (Golay (20,8), TS 102 361-1 B.3.1, Table 9.22):** the
  10 bits either side of the 48-bit sync, 5 symbols each, decode to a colour
  code and data type with at most 2 bit errors on essentially every sync
  hit (about 1700 bursts on 9 channels; a hit's stray decodes are rare). The
  colour code agreed with the capture's own decoder on all five channels
  that overlapped it in time. Data types are CSBK, Idle, Terminator with LC
  and a few Voice LC headers, never voice frames: this settles that the sync
  is the **BS data** sync in normal polarity (the voice-sync reading would
  give nonsense types). So the receive chain keeps polarity and the
  voice/data negation above is resolved here. A CSBK on a base-station
  channel is what a Capacity Plus control channel sends (a guess; the
  CSBK opcodes were not decoded).
  One more channel showed the same four levels with no DMR hit and
  **matched the NXDN frame sync word CDF59 (20 bit, symbols -3 +1 -3 +3 -3 -3
  +3 +3 -1 +3) with 0 errors, 87 of 87 hit-to-hit spacings exactly 192 symbols
  = 384 bits = the 80 ms frame at 4800 sym/s**: the 12.5 kHz NXDN mode
  (9600 bit/s) of TS 1-A, first check of our NXDN sync and frame length. The
  measured inner level was 700 Hz against a nominal 800 (the filters used
  droop the outer levels), so the +-800/+-2400 Hz figures were not confirmed
  by this pass, only the level ratio and the framing. The rest were analog FM
  (a two-level or tone-like discriminator with a 100-130 Hz line, which looks
  like CTCSS, though the capture's own decoder labels two of them otherwise) or
  not resolved. No P25 sync matched anywhere. Not read: the content of any
  burst.
- Still to do: a polarity check, colour code and slot occupancy per channel,
  the unresolved channels, and the same coarse pass over a TETRA capture.

## Open questions (unverified)

- Sync words and symbol rates for Fusion; P25 (all); NXDN Type-C; P25 Phase 2 sync; normative P25 text.
- FRS deviation, marine and ham deviation, CB FM status, CTCSS deviation.
- Squelch tail timing; DCS parameters beyond the 134.4 bit/s rate.
- Whether any carrier uses SC-PTM or eMBMS for MCPTT group calls.
- NR MBS SIB numbering; TS 36.331 text itself was not read.
- Current 3GPP release numbers for MCPTT.

## Sources

CFR: law.cornell.edu/cfr/text/47/95.563, 95.567, 95.1763, 95.1767, 95.2767,
95.2773; 90.209 (kiwi.hallikainen.org mirror). 3GPP: DynaReport 22179,
23179, 24379, 24380, 26179; 3gpp.org Release 13 page. Signal wikis:
sigidwiki.com (DMR, P25, NXDN, dPMR, TETRA, M17, ZVEI Selcall).
Others: repeater-builder.com CTCSS chart; n6jet.com DMR/M17/D-STAR pages;
github.com/g4klx/MMDVM and BrandMeister/DMRHost (sync words);
github.com/lwvmobile/dsd-fme; lists.osmocom.org op25-dev 2012/000061;
m17project.org; law.cornell.edu/uscode/text/18/2511; urgentcomm.com
(FirstNet PTT); en.wikipedia.org Zello, Iridium constellation;
5gstore.com (Band 14); github.com/5g-mag/tech (MBS).
