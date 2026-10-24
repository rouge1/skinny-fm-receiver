# Verifying an LTE carrier found in a sweep

Acronyms (PCI, MIB, SIB1, MCC, FDD and the rest) are explained in [glossary.md](glossary.md).
What a cell broadcasts after SIB1 (SIB2 to SIB24, network time, NR, NB-IoT)
and how to read it is in [lte-broadcast.md](lte-broadcast.md).

How to tell whether an LTE-looking block in a sweep is LTE, and, with an
outside decoder, which cell it is. The method is the Round 25 plan in
[roadmap.md](roadmap.md). The app classifies (numpy only); `tools/iq-lte`
reports the structure of a capture; the broadcast system information is read
only with `--decode`, through a program that is not part of this repo.

Placeholders are used throughout: PCI 123, PLMN 001-01, TAC 0x1234. Real
captures, PCIs, PLMNs, TACs and cell IDs stay in the user's recordings
folder, never in the repo or in these notes.

## The method

1. **Candidate from the sweep (no capture).** Look for a block whose occupied
   width is near an LTE size: 1.4, 3, 5, 10, 15 or 20 MHz, about 0.9 of the
   channel. Find the band it sits in. Downlink and uplink both look like this;
   an uplink block (a phone) has no broadcast to read, so stop there and look
   for the downlink partner, which is 30 MHz up in the bands that pair (Band 12
   and 17 near 704 to 716 MHz). The band-plan lookup is not in `iq-lte`: the
   block centre is given to the tool by hand.
2. **Capture the block.** With the window open, `fmctl capture 2 iq-band`
   records the block at 20 MS/s (about 17.8 MHz of it; 10 MS/s is too narrow
   for a 9 MHz occupied width).
3. **Structural fingerprint.** `tools/iq-lte` runs the checks that need only
   the samples. They are independent, and each has a chance estimate:
   - cyclic-prefix autocorrelation at the useful symbol (normal CP): the
     15 kHz OFDM grid and its seven prefix positions per slot;
   - PSS search: the three Zadoff-Chu roots (N_ID(2)) found in the symbol
     before each slot's last symbol, with a small frequency grid;
   - SSS search: N_ID(1) and which half-frame is subframe 0, from the SSS
     beside the PSS. The physical cell ID is 3 x N_ID(1) + N_ID(2), given only
     when its chance is at most 1e-3;
   - CRS check (`iqlte.crs_search`, added 2026-10-21): at the SSS timing, the
     port-0 reference signals scored against all 504 PCIs (and both
     half-frames). It runs only when the SSS gave a PCI, reports that PCI's
     coherence and chance and the strongest CRS PCI, and does not change the
     verdict. Each entry of `cells` gets its own `crs` at its own timing, with
     the other cells left out of the null, so a weak second cell's PCI is
     checked by its pilots as well as its SSS.
   - duplex check (added 2026-10-21): FDD puts the SSS in the symbol just
     before the PSS; TDD puts it three symbols earlier (last symbol of slot 1,
     PSS in symbol 2 of slot 2). `cells` tries the FDD position first and the
     TDD one only where FDD found nothing, and each cell carries `duplex`. The
     output's `duplex` is `FDD`, `TDD`, or null without a cell ID. For a TDD
     carrier the PCI and frame offset come from `cells` (the `sss` check reads
     the FDD position only), and the CRS check is run at that timing.
   The verdict is `lte` when the prefix and PSS checks both pass at 1e-3. The
   tool assumes the carrier is centred and normal CP. Read the `checks` list
   in the output, not the verdict alone. `--decode` refuses a TDD carrier (the
   srsRAN wrapper is FDD only).
4. **Broadcast decode (only with `--decode`).** Read the MIB and the SIB1 with
   an outside srsRAN 4G program. See below.

Uplink, bursty, and wideband OFDM that is not LTE come out as not `lte`.

## Commands

```sh
tools/iq-lte CAPTURE.cfile                                # the structure, as JSON
tools/iq-lte CAPTURE.cfile --block-center 751000000       # the LTE carrier is not at the file centre
tools/iq-lte CAPTURE.cfile --t0 1 --t1 1.2                # a span (default 0.2 s from 0; at most 2 s)
tools/iq-lte CAPTURE.cfile --rate 20000000 --center 751000000   # a bare .cfile with no .sigmf-meta
tools/iq-lte CAPTURE.sdvr                                 # an IQ Sceptre DVR works too
tools/iq-lte CAPTURE.cfile --decode                       # also the MIB and SIB1 (needs srsRAN, below)
tools/iq-lte CAPTURE.cfile --decode --ports 2 1           # antenna port counts to try, in order
```

The output is one JSON document on stdout: `verdict`, `nid2`, `nid1`, `pci`,
`nfft` and `bandwidth_mhz` (the class from the occupied width), `half_frame`,
`frame_offset_samples`, `duplex`, the `checks` list, `cells` (each with its `duplex` and `crs`:
`pci`, `score`, `chance`, `best_pci`), and `decode` (null without
`--decode`). `nfft` is the bandwidth class (1024 for 10 MHz), not the
transform used. A span shorter than 20 ms gets no SSS result.

A run on a 10 MHz downlink with PCI 123 (placeholder values) reads, in part:

```json
{
  "verdict": "lte",
  "nid2": 0,
  "nid1": 41,
  "pci": 123,
  "nfft": 1024,
  "bandwidth_mhz": 10,
  "decode": {
    "ran": true,
    "mib": {"pci": 123, "ports": 2, "prb": 50, "type": "FDD", "cp": "normal", "sfn": 0},
    "sib1": {"plmns": [{"mcc": "001", "mnc": "01"}], "tac": 4660}
  }
}
```

The TAC 4660 above is 0x1234, the placeholder TAC.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | The run completed, whatever the verdict (a negative is 0) |
| 1 | A bad option or value, a file that cannot be read, or a sweep file (`.sdvr` of spectra); the reason is on stderr, nothing on stdout |
| 2 | `--decode` was asked and the outside tools are missing: the tools directory (from `FMRX_LTE_TOOLS`, or the built-in default when that is unset or empty) or `run_pdsch.sh` in it. A missing pycrate interpreter is not exit 2: the MIB is still reported, with `sib1` null |

### The CRS check

The CRS sits in symbols 0 and 4 of each slot on every sixth subcarrier,
shifted by PCI mod 6, with values from a Gold sequence seeded by the PCI and
the slot (36.211 6.10.1). Each hypothesis takes its pilots, removes its own
sequence and measures how alike neighbouring pilots are (the channel changes
little over 90 kHz). Only phases are used, so a tone cannot dominate. The
chance is the larger of the analytic noise tail and the other hypotheses'
spread, times the 1008 hypotheses.

Tested (`test_iqlte_crs.py`): our generator at 20 to -10 dB, 5, 10 and 15 MHz,
both half-frames, two cells at one timing (0 and -6 dB, each confirmed), a wrong
timing and five non-LTE negatives (none passes), and a waveform made by
srsRAN's `pdsch_enodeb` (1 and 2 ports), an implementation that shares no code
with ours. The test makes that file when `pdsch_enodeb` is built under
`$FMRX_LTE_TOOLS`.

**Through a radio (2026-10-21).** Two files were played from a VSG60A at
751.0 MHz, about -40 dBm, over a bare cable into the BB60D, and captured 2 s at
20 MS/s through the window: srsRAN's `pdsch_enodeb` downlink (FDD, 2 ports,
made-up PCI 217) and our generator's TDD downlink (made-up PCI 321). FDD: verdict
`lte`, PCI 217, duplex FDD, CRS coherence 0.998 (chance at the floor), and srsRAN
decoded the MIB (2 ports, 50 PRB). TDD: PCI 321 from `cells`, duplex TDD, CRS
coherence 0.55 (the uplink subframes carry none), and `--decode` refused it.
The `sss` check read noise on the TDD file, as it should: it looks at the FDD
position only.

**On real air (2026-10-22, BB60D, antenna).** The three 10 MHz carriers at the
second location, 1 s at 20 MS/s each: every cell `cells` found, two per carrier
(six in all, one of them about 8 dB under its neighbour and never decoded by
srsRAN), was confirmed by its own CRS at the floor chance (1e-300), coherence
0.30 to 0.88, all FDD. The values stay with the recordings.

Limit: the timing must be the SSS's. A timing off by whole subframes lines the
pilots up with the wrong slot numbers, and another PCI can then match part of
them (weakly).

### TDD

Tested (`test_iqlte_tdd.py`) on our generator only: uplink-downlink
configuration 1 and special subframe 7, with the uplink and guard symbols
silent; 10 MHz at 20 and 0 dB, 5 and 20 MHz, two cells at one timing, five
negatives, and `tools/iq-lte` (card and `--decode` refusal). srsRAN's
`pdsch_enodeb` has no TDD mode, so there is no second implementation. The frame
timing is checked two other ways: it equals the FDD signal's offset for the
same seed, and the CRS (which matches only at the right slot numbers) confirms
the PCI there. A TDD carrier's CRS scores lower (about 0.5 at 20 dB): the
uplink subframes carry none. Through a radio (VSG60A to BB60D over a cable,
2026-10-21; see the CRS section): right. Not yet run on air: no TDD carrier was found here
(Band 41 at 2.5 GHz was empty; CBRS, Band 48, 3.5-3.75 GHz, swept 2026-10-22 at
full gain: noise only, floor about -120 dBm).

## Decoding the broadcast: `--decode`

`--decode` reads the MIB, the SIB1 and every other SIB of a cell that the
fingerprint called `lte` with a PCI (the other SIBs since 2026-10-22; what they
hold is in [lte-broadcast.md](lte-broadcast.md)). Nothing of ours decodes: the work is done by srsRAN 4G's
`pdsch_ue`, which the user builds separately.

What the decode does:

- It is limited to the broadcast channel: the MIB and the system information
  transmitted with the SI-RNTI (SIB1 and the SI messages). No user data is read.
- It reads `--si-seconds` (default 6, at most 10) from `--t0`, not the
  fingerprint's short span: the slowest SI message (SIB24, every 512 frames)
  needs 5.12 s plus a window. A span longer than the fingerprint's is read,
  mixed and resampled in pieces of about 2 M samples (each with a guard on
  both sides that is dropped) and written straight to the decoder's file, so
  it is never held whole: resampling 6 s at 20 MS/s in one FFT took several
  GB and got the terminal killed for memory pressure.
- It supports 10 MHz carriers (nfft 1024) and 15 MHz carriers (nfft 1536,
  added 2026-10-19, not yet run on a real 15 MHz carrier). Other widths report
  `ran: false` with the reason.
- The block is written at 15.36 MS/s (10 MHz) or 23.04 MS/s (15 MHz) as a cf32
  file in a private temporary folder, which is removed afterwards. The PRB count
  (50 or 75) reaches the wrapper as the `PRB` environment variable, so
  `run_pdsch.sh` must pass `-p ${PRB:-50}` to `pdsch_ue`; an older wrapper with a
  fixed `-p 50` decodes only 10 MHz.
- It tries two frame offsets half a 10 ms frame apart: `o`, the SSS frame
  offset at 15.36 MS/s, and `o` minus 76800 samples (modulo one frame). The
  half-frame label does not say which of the two starts subframe 0, so both
  are tried. If there is no SSS offset, `o` is 0. For each offset it tries the
  antenna port counts 4, 2 and 1 in that order. The first run
  whose output contains `Decoded MIB` wins; each run has a limit of 120 s or
  50 ms per subframe, whichever is longer (every subframe is searched), passed
  to the wrapper as `PDSCH_TIMEOUT`.
- The SIB1 is taken from the transport blocks `pdsch_ue` dumped: the most
  common subframe-5 payload of 32 bytes or more. It is parsed by a Python with
  pycrate, which runs as a separate process at `pyc/bin/python` under the tools
  directory. If that is missing or fails, `sib1` is null with `sib1_reason`,
  and the MIB is still reported.
- Every distinct block in the dump is then parsed by the same Python as a
  `BCCH-DL-SCH-Message`, and `si` is built from them (below). If that fails,
  `si` is null with `si_reason`.

Where the outside tools live:

- `FMRX_LTE_TOOLS` is the directory that holds them. Set it explicitly: when
  it is unset the tool uses a built-in default path, which is not a location
  to rely on.
- `run_pdsch.sh` is a wrapper in that directory. It takes five arguments: the
  cf32 file, the PCI, the frame offset in samples, the number of 1 ms subframes
  in the file, and the port count. It runs `pdsch_ue` and prints its output.
- `pyc/bin/python` is a Python with pycrate installed.

The srsRAN side is the user's to build. It is AGPL-licensed, is not in this
repo, and this repo never installs, copies or commits it. The local changes
to it that the decode relies on, each read from the environment so the stock
behaviour stays the default:

- `PDSCH_DUMP` (`pdsch_ue.c`): after each transport block it decodes whose CRC
  passed, it appends that block to the named file, as a record of a marker
  byte (0xA5), the subframe number, the length as two bytes little-endian,
  then the bytes. Without it no transport block is dumped, so `sib1` and `si`
  are null; the MIB comes from the program's text output alone. (Until
  2026-10-22 blocks were dumped whatever their CRC, so one corrupt copy could
  get in; the parse counts those as `blocks_failed`.)
- `PDSCH_ALL_SF` (`pdsch_ue.c`): look for the SI-RNTI in every downlink
  subframe, not only subframe 5 of even frames. Without it only SIB1 is heard,
  and `si` carries a `note` that says so.
- `PDSCH_FORCE_CFI` (`ue_dl.c`), for diagnosis only: force the control region
  size. The decode does not set it.
- `run_pdsch.sh` takes its `pdsch_ue` time limit from `PDSCH_TIMEOUT` (120 s
  when unset).

Output of `--decode` is in the same JSON under `decode` (the strongest cell's;
per-cell decodes are in the `cells` list, below): `ran`, `mib` (`pci`,
`ports`, `prb`, `type`, `cp`, `sfn`), `offset_samples`, `tried` (each offset
and port count with whether it decoded), `sib1` (`plmns`, `tac`,
`cell_identity`, `enb_id`, `cell_id`, `freq_band`, `cell_barred`,
`q_rx_lev_min`) or null with `sib1_reason`, and `si` or null with `si_reason`:

- `blocks`, `blocks_failed` (distinct blocks, and those that did not parse),
  `capture_frames` (10 ms frames decoded), `window_ms`, `value_tag`;
- `schedule`: one entry per SI message in SIB1 (the Rel-12 extension list
  after the main one), with `si` (1-based), `period_frames`, `sibs` (SIB2 is
  always in the first), `sfn_mod_period` and `subframe` (where its window
  opens), `covered` (the capture held one period plus one window) and `heard`;
- `sibs`: each SIB heard (`sib2` ... `sib33`) with `count`, `scheduled`, the
  whole decoded `value` as JSON, and for some a `summary`: SIB2 `ul_earfcn`,
  SIB3 `priority`, SIB4 `neighbour_pcis`, SIB5 `carriers` (`earfcn`, `band`,
  `mhz`, `priority`; a 65535 entry is read from the v9e0 list), SIB16 the time,
  SIB24 `nr_carriers` (`nr_arfcn`, `mhz`, `ssb_khz`, `priority`);
- `time`: SIB16's network time (`utc`, `utc_10ms`, `leap_seconds`, `gps`,
  `local_offset_minutes`, `local`, `dst_hours`, as far as the cell sends them),
  or null with `time_reason`: not scheduled (the cell sends no time), scheduled
  but not heard, or not heard in a capture shorter than its period;
- `alerts`: `sib10`, `sib11` or `sib12` when an ETWS or CMAS alert was heard;
- `note` when no SI message besides SIB1 was decoded.

EARFCNs become MHz through TS 36.101's table for the bands in
`iqlte_decode._EARFCN_DL` (the US ones and a few others; `band` and `mhz`
are null outside it), NR-ARFCNs through TS 38.104's global raster.

## Several cells on one carrier: `cells`

`iqlte.cells(iq, fs, max_cells=4)` lists every cell on the carrier whose SSS
clears the same 1e-3 line, strongest first. Each entry gives the PCI, N_ID(1),
N_ID(2), the frame offset in samples, the half-frame, the power relative to the
strongest (0 dB for it, negative below), the SSS chance and a detail string.
A cell whose PSS is found but whose SSS is not is left out: a PCI is never
guessed. An empty list is returned for noise, for a capture under 20 ms and for
one under 1.92 MS/s, without an exception.

The method:

1. Each of the three PSS roots is correlated on its own against that root's
   useful symbol, and the correlation is folded onto one 10 ms frame by the
   mean of the magnitudes. The fold is one frame, so a neighbour half a frame
   away is not lost, and the half-frame mate of a cell is the same cell found
   twice.
2. Peaks of one root more than a short guard apart are different timings:
   different towers, which may share a root. Peaks of different roots within a
   few samples are one timing: sectors of one tower, which are time-synchronised.
3. At a shared timing a least-squares fit gives one gain per root. The SSS
   hypotheses are scored strongest root first. A PCI that clears 1e-3 has its
   SSS subtracted before the next is scored, and the same root is scored again
   after that, because two cells on one root and one timing can only be told
   apart by their SSS.
4. The fold is first tried at zero frequency offset. Only when its best peak is
   still at the noise is a 2 kHz grid from -10 kHz to +10 kHz tried, and the
   winning offset is used to derotate the SSS.

The wrong turns that shaped this (a fold on 5 ms, blanking the half-frame mate,
a regenerated data subframe subtracted, and others) are listed in the
`iqlte.py` docstring. Single-cell results are unchanged: `fingerprint`'s `pci`
is still the strongest cell's, and `cp_autocorr`, `pss_search` and `sss_search`
are not touched.

Limits of `cells`:

- **Sectors and towers are told apart differently.** Sectors share a timing and
  differ by root; a second tower shows as a second timing. A tower whose sector
  has the same root and the same timing as another cell is separated by its SSS
  alone, so it is reported only if that SSS clears the line.
- **A weak cell.** The synthetic targets were a cell 6 dB under the strong one
  and a cell 10 dB under it. At 10 dB under, the strong cell is the one the
  target requires; the weak one is not required to appear.
- **Frequency offset.** Zero offset, then a grid to +/-10 kHz. A larger offset
  is not covered by `cells`. The single-cell path still covers +/-6 kHz only.
- **Three cells are the tested case.** `max_cells` defaults to 4. Four or more
  cells are not tested.
- **Synthetic only.** Real air has not been checked. Two carriers the planner
  holds (Band 14 and Band 17) are the check; see the status below.

The JSON the tool prints: a `cells` list (the same entries as above) in
`tools/iq-lte`'s output. With `--decode`, each cell gets its own `decode` (the
same shape as the top-level one, or `{"ran": false, "reason": ...}`), decoded
at that cell's frame offset. The strongest cell's decode stays the top-level
`decode`. The decode time budget is about 15 minutes for all cells together.
The per-cell decode is synthetic and stub-tested only; the planner's real-air
check is pending, so it is not verified on air.

`cells` reports `half_frame` 0 always: the frame offset is only known modulo
the 5 ms half-frame, so the decode tries both boundaries.

## Status

Built, tested on synthetic signals and a stub decoder, and run on real air once
(2026-10-16, BB60D, the strongest block of the 698-800 MHz sweep: a 10 MHz FDD
downlink in Band 13). The whole method worked end to end on two separate
recordings of that cell: the fingerprint gave the PCI, srsRAN decoded the MIB
(4 ports, 50 PRB, FDD, normal CP) with that PCI and the frame timing, and SIB1
gave the PLMN, tracking area code, cell identity and band, with the band indicator
agreeing with the carrier's frequency. SIB1 blocks repeated identically across
the capture and a second decoder (pycrate) parsed the same fields. The values
are not recorded here: they are kept with the recordings. See
[capabilities.md](capabilities.md).

### Limits seen on real air

- **One cell per carrier was assumed.** A second carrier (Band 14) showed
  LTE structure in every segment but the PSS root alternated between two values: two
  cells on one frequency. The detector then flips between them and the SSS often
  finds neither, so no PCI. Telling several cells apart is the `cells` step above
  (phase l4), built and tested on synthetic signals; its real-air check is pending.
- **Centre the block on the carrier.** The PSS check tolerates about 10 kHz.
  An LTE carrier's centre is on a 100 kHz raster of the absolute frequency, so
  try the raster values near the plateau's middle (`--block-center`). A block
  sitting off-centre or beside other carriers can also read as the wrong bandwidth
  class (a 10 MHz carrier read as 15 MHz).
- **The cyclic-prefix check alone proves little.** On real captures it passed
  with a chance near 1e-300 on blocks the other checks did not confirm. Trust the PSS
  and SSS together, not CP alone.
- **The generic card calls a block "noise"** when it is only a few dB over the floor of
  a 17.8 MHz capture; run `iq-lte`, not `iq-measure`, on a suspected carrier.
- **A first scan of other downlink bands** (the 600, 700, 850, 1900, 2100 and 2600
  MHz bands, 15 MHz tiles) found no further confirmed cell; the high bands gave
  no candidate block at all. That says "not found with this antenna and this block
  test", not "no towers".
- **A second location (2026-10-19, BB60D).** The 700 MHz band held three 10 MHz
  carriers (Bands 12/17, 13 and 14). The Band 13 cell decoded fully (MIB and SIB1,
  operator named). On the other two carriers the same two cells (two PCIs) showed
  on both carriers, synchronised (equal SFN at one instant), with 2 antenna ports.
  Their MIBs decoded every time but SIB1 never did, over 12 captures: 20 and
  40 MS/s, gain 50 to 100 %, 0.2 to 2 s spans. `pdsch_ue` found no SI-RNTI DCI in
  subframe 5, with about 4 dB SNR on the PBCH. So the operator is not proven for
  those carriers. Not established: whether it is the weak signal, the two
  synchronised cells overlapping, or both. More captures of the same kind did not
  help; a better antenna is the next test. Tried 2026-10-23 with the outside
  antenna (6 s live captures centred on each carrier, through `fmctl cells lte
  decode`): both cells' MIBs on the lower carrier, neither on the upper, and
  still no SIB1 on either. The 600 MHz and 850 MHz bands and 1.9-2.7 GHz
  gave no confirmed cell.
- **A quiet carrier failed the PSS check (fixed 2026-10-22).** On the three
  700 MHz carriers at the second location the verdict was `possible` with no PCI,
  although `cells` found both cells on each. The PSS check, not the SSS, failed
  (chance about 0.5). With little traffic the data symbols are nearly empty, so
  the folded prefix profile is broad humps on the reference and control symbols,
  and the seven prefix peaks, kept only half a prefix apart, fell on three
  symbols and missed the PSS symbol. They are now kept half a symbol apart: all
  three carriers read `lte` with the strongest cell's PCI, every check passing.
  Our generator only fills every symbol, so it never showed this;
  `lte_synth`'s `idle=True` (QPSK in the control region only) reproduces it,
  and `test_iqlte_idle.py` fails every check with the old spacing. Still
  missed: echoes spread over 40 samples at 3 dB SNR.
- **Draw the block before trusting its width.** A carrier 2.3 MHz off the file
  centre read as 15 MHz; centred, it read 10 MHz and its MIB said 50 PRB. Step 3
  of [workflow.md](workflow.md) (`tools/iq-picture`) shows the occupied edges and
  the true centre.
- **`tools/iq-lte` bug found on first real use:** pycrate's BIT STRING is
  `(value, nbits)`, and the srsRAN wrapper's default filter drops the MIB block's
  lines. Both fixed; a stub test cannot catch either.
