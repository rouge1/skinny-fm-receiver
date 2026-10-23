# Glossary

The acronyms in the LTE and 5G NR verification work ([lte-verify.md](lte-verify.md),
[nr-verify.md](nr-verify.md)) and in the TV and radio tabs. No captured values here: the examples are the standard's
own numbers.

## LTE: the network and its numbers

| Term | Meaning |
|---|---|
| LTE | Long-Term Evolution, 4G cellular. |
| 3GPP | The standards body. Its documents (TS 36.xxx) define LTE. |
| Band 13, Band 14 | Numbered slices of spectrum. Band 13 downlink is 746-756 MHz, Band 14 downlink is 758-768 MHz. |
| Downlink / uplink (DL/UL) | Tower to phone / phone to tower. A tower's downlink is on all the time; uplink is bursty phones. |
| FDD | Frequency-division duplex: downlink and uplink on separate frequencies. The downlink is continuous. |
| TDD | Time-division duplex: one frequency, and downlink and uplink take turns, so the downlink has gaps in a repeating 10 ms pattern (for example Band 41, 2.5 GHz). `iqlte` assumes FDD. |
| eNodeB (eNB) | LTE's name for the base station, the tower's radio. |
| NR-ARFCN | The 5G NR channel number for a frequency (TS 38.104's global raster: 5 kHz steps below 3 GHz, 15 kHz up to 24.25 GHz). |
| NR, SSB, GSCN | 5G New Radio; its Synchronisation Signal Block (sync signals plus the MIB, 20 resource blocks wide); and the sync raster the SSB can sit on. |
| NSA | Non-standalone 5G: NR cells that rely on an LTE anchor and send no SIB1 of their own. |
| NB-IoT, LTE-M | Two low-rate LTE variants for meters and sensors, each with its own MIB and SIB1 (SIB1-NB, SIB1-BR). |
| EARFCN | The channel number for a frequency. LTE carriers are centred on a 100 kHz raster, which is why the PSS check needs the block centred to within about 10 kHz. |

## LTE: who runs the tower (what SIB1 gives)

| Term | Meaning |
|---|---|
| PLMN | Public Land Mobile Network: one operator's network, written as MCC + MNC. A cell may broadcast several. |
| MCC | Mobile Country Code. 310-316 are the United States. |
| MNC | Mobile Network Code: which operator in that country. |
| TAC | Tracking Area Code: the group of cells a tower belongs to. 16 bits. |
| Cell identity | 28 bits: the first 20 are the eNodeB's ID, the last 8 are the sector (cell) within it. |

A PLMN gives an operator's code, not its name. The name comes from the public
code tables (for example, MCC 313 / MNC 100 is the US public-safety network
FirstNet).

## LTE: telling cells apart

| Term | Meaning |
|---|---|
| PCI | Physical Cell ID, 0-503. A short ID that nearby cells use so they are not confused. Not unique worldwide, so two far-apart cells can share one. |
| N_ID(1), N_ID(2) | The two parts of the PCI: PCI = 3 x N_ID(1) + N_ID(2). N_ID(2) is 0-2, N_ID(1) is 0-167. |
| PSS | Primary Synchronization Signal: a fixed pattern sent every 5 ms. Which of three patterns (Zadoff-Chu roots 25, 29, 34) gives N_ID(2), i.e. PCI mod 3. |
| Sector | One of a tower's cells (usually three, one per antenna direction). Sectors of one tower share a timing and have different PSS roots, so they can be told apart by root. |
| SSS | Secondary Synchronization Signal: sent in the symbol just before the PSS. Its pattern gives N_ID(1), and so the full PCI, and which half of the 10 ms frame it is. |
| CRS | Cell-specific Reference Signals: known pilot symbols scattered through the downlink so phones can estimate the channel. Their positions depend on the PCI, so they are a second, independent check of it, and show the antenna-port count. Not built here yet. |
| SFN | System Frame Number: a counter 0-1023, one frame per 10 ms. |

## LTE: the signal

| Term | Meaning |
|---|---|
| OFDM | Orthogonal frequency-division multiplexing: many narrow subcarriers (15 kHz apart in LTE) sent together. |
| CP | Cyclic prefix: a copy of the end of each symbol put on its front. It makes OFDM detectable by autocorrelation. "Normal" CP is the usual one; "extended" is rare. |
| PRB (RB) | Physical Resource Block: 12 subcarriers, 180 kHz. 6, 15, 25, 50, 75, 100 PRB = 1.4, 3, 5, 10, 15, 20 MHz. |
| FFT size (`nfft`) | 128, 256, 512, 1024, 1536, 2048 for those widths. 1024 is a 10 MHz carrier. |

## LTE: the broadcast data

| Term | Meaning |
|---|---|
| PBCH | Physical Broadcast Channel: carries the MIB. |
| MIB | Master Information Block: bandwidth, PHICH setting and the frame number. The port count is read from the PBCH's error check. |
| SIB1 | System Information Block Type 1: the PLMN list, tracking area, cell identity, barring and the band. Repeated about every 20 ms. |
| SIB, SI message | The other System Information Blocks (SIB2 to SIB33), sent inside SI messages at the periods SIB1 schedules ([lte-broadcast.md](lte-broadcast.md)). |
| SI window | The stretch of time (1 to 40 ms, SIB1's `si-WindowLength`) in which one SI message may be sent once per period. |
| SIB16 | The SIB that carries the network time: UTC, GPS time, leap seconds, local time offset. Optional; many cells do not send it. |
| ETWS, CMAS | Earthquake and Tsunami Warning System, Commercial Mobile Alert System (WEA in the US): emergency alerts, broadcast in SIB10 to SIB12 while one is on. |
| PDSCH | Physical Downlink Shared Channel: where SIB1 is sent (user data goes here too; this work does not read it). |
| SI-RNTI | The fixed address 0xFFFF meaning "everyone": the broadcast system information. |
| RNTI | Radio Network Temporary Identifier: a phone's temporary address. |
| RRC | Radio Resource Control, the protocol layer these messages belong to (3GPP TS 36.331). |
| ASN.1, UPER | The notation SIB1 is described in, and the bit-packing rule it is sent with. `pycrate` unpacks it. |
| CRC | Cyclic redundancy check: an error check on a message. A pass means the data is intact. |

## 5G NR: the sync block and the broadcast

| Term | Meaning |
|---|---|
| gNB | NR's name for the base station. |
| SCS | Subcarrier spacing: 15 kHz (as LTE) or 30 kHz below 6 GHz in this work. |
| PSS, SSS (NR) | NR's sync signals: length-127 m-sequences, three PSS (N_ID2) and 336 SSS (N_ID1) per PSS, so 1008 PCIs (3 N_ID1 + N_ID2). |
| PBCH DM-RS | The reference signal inside the SSB's broadcast channel. Its sequence gives the SSB index (and, at L_max 4, the half-frame) without decoding the PBCH. |
| SSB index, L_max | A cell sends up to L_max SSBs per period (4 at or below 3 GHz, 8 above), one per beam; the index says which. |
| k_SSB, CORESET 0, search space 0 | From the MIB: the SSB's subcarrier offset from the resource-block grid, and where and when to look for SIB1's control channel. |
| Point A, `offsetToPointA` | The carrier's reference frequency (subcarrier 0 of resource block 0). SIB1 gives it as an offset in resource blocks from the SSB: point A = SSB centre - 120 x SCS - k_SSB x 15 kHz - `offsetToPointA` x 180 kHz (15 kHz case). |
| SIB9 | NR's network-time SIB: UTC, GPS time, leap seconds, local offset, as LTE SIB16. Optional; sent only when SIB1 schedules it. |
| Cell barred | A MIB and SIB1 flag: phones may not camp on the cell. |

## Our tools

| Term | Meaning |
|---|---|
| IQ | The raw complex samples a radio records. |
| SigMF | The metadata file that sits beside an IQ recording. |
| BB60D | The Signal Hound spectrum analyser this app uses. |
| srsRAN | Open-source LTE software (AGPL). Its `pdsch_ue` reads the MIB and SIB1 from a file, and its NR library (behind `run_nr.sh`) an NR cell's MIB and SIBs. Built separately, not part of this repo. |
| pycrate | A Python library that decodes the SIB1 message. |
| `iqlte`, `iq-lte` | This repo's LTE fingerprint (cyclic prefix, PSS, SSS) and its command. |
| `iqnr`, `iq-nr` | This repo's NR sync-block search (PSS, SSS, PBCH DM-RS on the sync raster) and its command; `--decode` hands each block to srsRAN's NR library. |

## TV and radio

| Term | Meaning |
|---|---|
| ATSC | The US digital TV standard. |
| 8VSB | ATSC's modulation: an 8-level signal with a pilot tone. |
| PSIP | Program and System Information Protocol: the tables inside an ATSC stream. |
| TVCT | Terrestrial Virtual Channel Table: the channel names and numbers. |
| MGT | Master Guide Table: lists the other PSIP tables. |
| EIT | Event Information Table: the programme guide, now and next. |
| MPEG-TS, PID | The transport stream the TV is carried in, and the number labelling each stream in it. |
| MER | Modulation error ratio: how clean a digital signal is, in dB. |
| RDS | Radio Data System: the station name and text sent with FM. |
| HD Radio | Digital audio sent alongside FM, decoded by `nrsc5`. |
