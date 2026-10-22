# Broadcast metadata beyond SIB1

Acronyms are in [glossary.md](glossary.md). How a cell is found and its MIB
and SIB1 read is in [lte-verify.md](lte-verify.md); this note covers what
comes after: the other system information blocks (SIBs) a cell broadcasts,
how to know which ones it sends, how long to record, and what each one tells
us. It also covers 5G NR and NB-IoT as far as they were researched.

Scope: broadcast tower metadata only, what every phone hears before it
attaches. Never user traffic, calls, paging records aimed at phones,
subscriber identities, or anything a phone learns only after attaching.
Placeholders are used throughout (PCI 123, PLMN 001-01, EARFCN 1234); real
values stay in the user's recordings folder and the outside tools folder.

Sources: a research phase run on 2026-10-22 (three models from different
angles, then a merge that checked the claims on the web), plus one real-air
check the same day. Marks: **checked** (a source or our own capture),
**unchecked** (from memory or one source not re-opened).

## Which SIBs a cell sends: SIB1's schedule

SIB1 carries `schedulingInfoList` and `si-WindowLength`. Each entry of the
list is one SI message with a period (`si-Periodicity`, `rf8` to `rf512`:
80 ms to 5.12 s) and the SIBs mapped into it (`sib-MappingInfo`). SIB2 is
always in the first entry and is not named in its mapping. SIBs from later
releases (SIB24 and up) are listed in an extension, `schedulingInfoListExt-r12`,
deep inside SIB1's `nonCriticalExtension` chain; their entries are numbered
after the main list's. An entry can have an empty mapping besides the first;
on the real cell checked, one such entry was never seen on air.

Where SI message *n* (1-based) is sent, for FDD (TS 36.331 5.2.3):
*x* = (*n* − 1) · *w* in ms, with *w* the window length; the window starts in
subframe *x* mod 10 of the frame whose SFN mod *T* = floor(*x* / 10), *T*
being the period in frames. Windows do not overlap. With a 10 ms window,
message *n* sits in the frame with SFN mod *T* = *n* − 1. **Checked** on real
air: over 100 frames every message turned up exactly as many times as this
predicts (for example a 16-frame period seen 6 times, a 64-frame period once).
Inside its window the block can be in any downlink subframe except 5 of even
frames (SIB1's); on the real cell they were all in subframe 2.

**How long to record:** one period of the slowest SIB you want plus one
window. SIB1 alone needs 20 ms; a 1 s capture covers 16-, 32- and 64-frame
periods but can miss a 128-frame one; SIB24 at `rf512` needs at least 5.12 s
plus a window, so record 6 s. SFN starts anywhere, so a shorter capture may
or may not catch a long-period message.

`systemInfoValueTag` in SIB1 changes when any SI content changes: two
captures with the same tag hold the same SIBs.

## The LTE SIBs

| SIB | What it holds | Use to us | Confidence |
|---|---|---|---|
| SIB1 | PLMN list (the operator), TAC, cell identity, band, the schedule above, `hyperSFN` and eDRX flag (Rel-13) | the operator | checked (decoded on real air) |
| SIB2 | RACH, paging cycle, PRACH, power control, timers, `ul-CarrierFreq` (the paired uplink EARFCN), access barring | uplink frequency of this cell; no operator | checked (real air) |
| SIB3 | reselection settings for this frequency (priority, thresholds) | priority of this carrier | checked (real air) |
| SIB4 | same-frequency neighbour PCIs, and the CSG (femtocell) PCI range | neighbours on this carrier | checked (real air) |
| SIB5 | other LTE carriers phones may move to: `dl-CarrierFreq` (EARFCN), extra bands, priority, thresholds | the operator's other downlinks; no PLMN of its own, so the frequencies belong to SIB1's PLMN | checked (real air) |
| SIB6, SIB7, SIB8 | UMTS, GSM and CDMA2000 neighbours (SIB8 also CDMA system time) | none now: US UMTS is off and GSM ended 3 Aug 2026 | checked (source) |
| SIB9 | home eNB (femto) name | rare | checked (source) |
| SIB10, SIB11, SIB12 | ETWS primary and secondary warning, CMAS (WEA) alert text; only scheduled while an alert is on | emergency alerts, broadcast to all | checked (source) |
| SIB13, SIB15 | MBMS (broadcast TV and multicast) control | rare | checked (source) |
| SIB16 | network time: UTC, GPS time, leap seconds, daylight saving, local time offset | the time and time zone, without attaching | role checked (source); field layout unchecked |
| SIB17 to SIB21 | WLAN offload, sidelink, V2X | not an operator path | checked (source) |
| SIB24 | NR (5G) carrier frequencies and priorities for reselection | where the NR cells are | checked (source) |
| SIB25 to SIB33 | later releases (access control, NTN) | not an operator path | checked (source) |

There is no LTE SIB22 or SIB23 (those numbers are NB-IoT's).

### Network time (SIB16)

SIB16 is the only broadcast time in LTE. If SIB1 does not schedule it, no
length of recording finds it. **Checked** on real air: the one carrier with
SIB1 at the second location schedules SIB2 to SIB5, SIB15 and SIB24, and no
SIB16. The time a phone on such a network shows comes from a message sent
after it attaches, which is out of scope. Without SIB16 the cell gives only
its frame counter (SFN, 10.24 s cycle) and `hyperSFN` (SIB1, Rel-13), which
count time but do not say the date or time of day.

Other broadcast time sources this app can read or might: the FM station clock
in RDS (already read: `fmctl status`, RDS `clock`); NR SIB9 (GPS and UTC
time) on an NR cell that broadcasts SIB1; another operator's LTE cell that
schedules SIB16; ATSC's system time table.

## Reading them from a recording

The chain is the one `--decode` uses (lte-verify.md): resample a centred
10 MHz block to 15.36 MS/s, run srsRAN 4G's `pdsch_ue` on the file with the
SI-RNTI, dump each transport block, parse it with pycrate as
`BCCH-DL-SCH-Message`. SIB1 is `c1: systemInformationBlockType1`; every other
SI message is `c1: systemInformation`, whose `sib-TypeAndInfo` holds one or
more SIBs.

Stock `pdsch_ue` looks for SI-RNTI only in subframe 5 of even frames (SIB1).
For the rest the outside build carries two local, env-gated changes (default
behaviour unchanged; the originals are kept beside them as `.orig`):

- `PDSCH_ALL_SF=1`: search every downlink subframe for the SI-RNTI.
- `PDSCH_FORCE_CFI=1|2|3`: force the control region size, to rule out a bad
  PCFICH read.
- `PDSCH_DUMP=FILE` (the older change): append every decoded block to FILE as
  records of 0xA5, subframe, length low, length high, then the bytes.

Run the whole capture (`-n` about the number of subframes in it), split the
dump into records, keep each distinct payload once, and parse each. Group by
SIB to count how often each was seen against the schedule.

## Finding the operator of a carrier with no SIB1

Some carriers send a clean MIB and PCFICH but no SI-RNTI and no paging in any
subframe (likely carrier-aggregation secondaries, configured for each phone
after it attaches). Nothing on them names the operator, and a longer capture
of them adds nothing. Broadcast-only paths, cheapest first:

1. **SIB5 of a cell that has SIB1.** Its EARFCN list is that operator's other
   downlinks. A silent carrier's EARFCN on the list ties it to that operator.
   A miss proves nothing: CA secondaries are often left out of SIB5. An
   operator's SIB5 lists only its own carriers, so to name a second operator,
   find one of its cells that sends SIB1 (other bands, better antenna).
2. **PCI and SFN.** A silent carrier whose cells share PCIs and SFN with a
   SIB1 cell is likely on the same site; PCIs repeat nationally, so this is
   corroboration, not an operator.
3. **NB-IoT.** An in-band or guard-band NB-IoT anchor (NPSS/NSSS on one PRB)
   has its own MIB-NB and SIB1-NB, which can carry a PLMN. srsRAN's
   `npdsch_ue`; its file input is unchecked.
4. **NR.** An NR cell that sends SIB1 has its own SIB5 of LTE EARFCNs. Go
   there only if SIB24 says where NR is.

A public licence database (FCC ULS) can name who holds a frequency; that is
not broadcast data and is a separate check.

## 5G NR and NB-IoT (researched, not built)

| Item | What it tells us | Notes | Confidence |
|---|---|---|---|
| NR SSB (PSS, SSS) | PCI 0-1007, timing | 4 symbols x 240 subcarriers: about 3.6 MHz at 15 kHz spacing, 7.2 MHz at 30 kHz; 20 MS/s is plenty | checked (source) |
| NR sync raster (GSCN) | where an SSB can sit | below 3 GHz: N x 1200 kHz + M x 50 kHz, M in {1, 3, 5}; 3-24.25 GHz every 1.44 MHz (TS 38.104 5.4.3) | checked (source) |
| NR MIB (PBCH, polar coded) | SFN, spacing, CORESET#0, or a flag that the cell sends no SIB1 | no SIB1 fits an NSA cell, whose SI goes to each phone after attach | checked (source) |
| NR SIB1 | PLMN, TAC, schedule of the other SI | CORESET#0 PDCCH, then PDSCH, then ASN.1 (TS 38.331) | checked (source) |
| NR SIB5 | LTE EARFCNs (ties LTE carriers to an NR PLMN) | | checked (source) |
| NR SIB6, SIB7, SIB8, SIB9 | ETWS, CMAS, and GPS/UTC time (SIB9) | only if SIB1 schedules them | checked (source) |
| LTE-M (SIB1-BR) | its own SIB1, scheduled by a MIB field (`schedulingInfoSIB1-BR`; zero means none) | `pdsch_ue` would need the BR schedule | checked (source) |
| NB-IoT (MIB-NB, SIB1-NB) | mode (in-band, guard, standalone), PLMN | srsRAN `npdsch_ue` | checked (source); PLMN in SIB1-NB unchecked |

No small numpy NR decoder was found. A file-replay NR SIB1 path exists in
Sni5Gect's broadcast worker (AGPL); its other parts are out of scope.

## Tools

| Tool | Decodes | Input | Licence |
|---|---|---|---|
| srsRAN 4G `pdsch_ue` (outside build, with the changes above) | MIB, SIB1, other SI | file or radio | AGPL-3.0 |
| srsRAN 4G `npdsch_ue` | NB-IoT MIB-NB, SIB1-NB, SIB2-NB | radio (file unchecked) | AGPL-3.0 |
| pycrate (`pycrate_asn1dir.RRCLTE`, in the outside venv) | `BCCH-DL-SCH-Message` UPER | bytes | LGPL-2.1 |
| LTE-Cell-Scanner | PBCH to SIB from a raw `.bin` (`--loadbin`) | file or radio | AGPL-3.0 |
| gr-lte | MIB, PCFICH only | file or radio | GPL-3.0 |
| Sni5Gect (broadcast worker only) | NR SSB, MIB, SIB1 | file or radio | AGPL-3.0 |

None of these is installed by this repo or imported by its code.

## Out of scope (named only so it is avoided)

LTESniffer, NR-Scope, 5GSniffer, the UE-tracking and injection parts of
Sni5Gect, QCSuper; `pdsch_ue` with any RNTI other than the SI-RNTI; paging
records on the P-RNTI; dedicated RRC (CA secondary configuration, NSA SIB1
delivered to a phone); the time message sent to an attached phone.

## Wrong turns

- The merge first claimed the stock SIB1 path also yields SIB5, SIB10-12 and
  SIB16. It does not: those are not in SIB1's subframe; they need the search
  of every subframe.
- One answer said pycrate has no LTE RRC module. It does (`RRCLTE`), at least
  in the version in the outside venv.
- A 1 s capture holds most SIBs but not SIB15 (128 frames) or SIB24
  (512 frames) unless the SFN happens to fall right. Read SIB1's periods
  before choosing a capture length.

## Sources

- TS 36.331 5.2.1.2, 5.2.3, 6.2.2 (`BCCH-DL-SCH-Message`), 6.3.1:
  https://itecspec.com/3gpp/36.331/s/5.2.1.2 ,
  https://itecspec.com/3gpp/36.331/s/bcch-dl-sch-message ,
  https://itecspec.com/3gpp/36.331/s/systeminformationblocktype5
- LTE SIB catalogue through SIB33: https://www.sharetechnote.com/html/Handbook_LTE_SIB.html
- CMAS: https://www.sharetechnote.com/html/Handbook_LTE_CMAS.html
- TS 38.331 SIB4, SIB5, SIB6, SIB9: https://itecspec.com/3gpp/38.331/s/5.2.2.4.5 ,
  https://itecspec.com/3gpp/38.331/s/sib5 , https://itecspec.com/3gpp/38.331/s/sib6 ,
  https://itecspec.com/3gpp/38.331/s/sib9
- NR PCI and MIB: https://www.sharetechnote.com/html/5G/5G_PSS.html ,
  https://www.sharetechnote.com/html/5G/5G_Mib_Sib.html
- GSCN (TS 38.104 5.4.3): https://www.cafetele.com/articles/article-nr-arfcn-gscn-calculation.html
- NSA SI by dedicated signalling: ETSI TS 138 331 V15.28.0, 5.2.1
- `pdsch_ue` SIB1-only gate: https://github.com/srsran/srsRAN_4G/blob/master/lib/examples/pdsch_ue.c
- pycrate: https://github.com/P1sec/pycrate/wiki/Using-the-pycrate-asn1-runtime
- NB-IoT: https://docs.srsran.com/en/latest/app_notes/source/nbiot/source/index.html
- Sni5Gect: https://github.com/asset-group/Sni5Gect-5GNR-sniffing-and-exploitation
- GSM and UMTS shutdowns: https://www.fierce-network.com/wireless/t-mobile-shut-down-2g-gsm-network-summer
