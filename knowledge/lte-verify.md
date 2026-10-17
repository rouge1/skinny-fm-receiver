# Verifying an LTE carrier found in a sweep

Acronyms (PCI, MIB, SIB1, MCC, FDD and the rest) are explained in [glossary.md](glossary.md).

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
     when its chance is at most 1e-3.
   The verdict is `lte` when the prefix and PSS checks both pass at 1e-3. The
   tool assumes the carrier is centred, normal CP and FDD. It does not run the
   duplex check (a TDD pattern would show as gaps) or the CRS check (named in
   the output, not run). Read the `checks` list in the output, not the verdict
   alone.
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
`frame_offset_samples`, the `checks` list, and `decode` (null without
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

## Decoding the broadcast: `--decode`

`--decode` reads the MIB and SIB1 of a cell that the fingerprint called `lte`
with a PCI. Nothing of ours decodes: the work is done by srsRAN 4G's
`pdsch_ue`, which the user builds separately.

What the decode does:

- It is limited to the broadcast channel: the MIB and the SIB1, the system
  information transmitted with the SI-RNTI. No user data is read.
- It supports a 10 MHz carrier only in this phase. Other widths report
  `ran: false` with the reason.
- The block is written at 15.36 MS/s as a cf32 file in a private temporary
  folder, which is removed afterwards.
- It tries two frame offsets half a 10 ms frame apart: `o`, the SSS frame
  offset at 15.36 MS/s, and `o` minus 76800 samples (modulo one frame). The
  half-frame label does not say which of the two starts subframe 0, so both
  are tried. If there is no SSS offset, `o` is 0. For each offset it tries the
  antenna port counts 4, 2 and 1 in that order. The first run
  whose output contains `Decoded MIB` wins; each run has a 120 s limit.
- The SIB1 is taken from the transport blocks `pdsch_ue` dumped: the most
  common subframe-5 payload of 32 bytes or more. It is parsed by a Python with
  pycrate, which runs as a separate process at `pyc/bin/python` under the tools
  directory. If that is missing or fails, `sib1` is null with `sib1_reason`,
  and the MIB is still reported.

Where the outside tools live:

- `FMRX_LTE_TOOLS` is the directory that holds them. Set it explicitly: when
  it is unset the tool uses a built-in default path, which is not a location
  to rely on.
- `run_pdsch.sh` is a wrapper in that directory. It takes five arguments: the
  cf32 file, the PCI, the frame offset in samples, the number of 1 ms subframes
  in the file, and the port count. It runs `pdsch_ue` and prints its output.
- `pyc/bin/python` is a Python with pycrate installed.

The srsRAN side is the user's to build. It is AGPL-licensed, is not in this
repo, and this repo never installs, copies or commits it. The one local change
to `pdsch_ue` that the wrapper relies on: after each transport block it decodes,
it appends that block to a file whose name it takes from the `PDSCH_DUMP`
environment variable, as a record of a marker byte (0xA5), the subframe number,
the length as two bytes little-endian, then the bytes. Without that patch no
transport block is dumped, so `sib1` is null; the MIB comes from the program's
text output alone.

Output of `--decode` is in the same JSON under `decode` (the strongest cell's;
per-cell decodes are the planned l4 tool step, below): `ran`, `mib` (`pci`,
`ports`, `prb`, `type`, `cp`, `sfn`), `offset_samples`, `tried` (each offset
and port count with whether it decoded), `sib1` (`plmns`, `tac`,
`cell_identity`, `enb_id`, `cell_id`, `freq_band`, `cell_barred`,
`q_rx_lev_min`) or null with `sib1_reason`.

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

The JSON the tool prints: the planned l4 tool step adds a `cells` list (the
same entries as above) to `tools/iq-lte`'s output, and with `--decode` gives
each cell its own `decode` (the same shape as the top-level one, or
`{"ran": false, "reason": ...}`), decoded at that cell's frame offset. Until
that step is merged, `iq-lte` does not print `cells`, and its `decode` is still
the strongest cell's only. The per-cell decode is not verified on air.

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
- **`tools/iq-lte` bug found on first real use:** pycrate's BIT STRING is
  `(value, nbits)`, and the srsRAN wrapper's default filter drops the MIB block's
  lines. Both fixed; a stub test cannot catch either.
