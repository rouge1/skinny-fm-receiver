# Finding 5G NR sync blocks in a capture

Acronyms used here: NR (5G's radio), SSB (synchronisation signal block: the
PSS, SSS and PBCH of one cell, four OFDM symbols), GSCN (global
synchronisation channel number: the number of an SSB's frequency on the sync
raster), PCI (physical cell ID, 0 to 1007), PSS and SSS (primary and secondary
synchronisation signals), DM-RS (demodulation reference signal), PBCH (physical
broadcast channel), MIB and SIB1 (the broadcast system information), CFO
(carrier frequency offset), SCS (subcarrier spacing), CP (cyclic prefix),
L_max (the most SSBs a cell sends in one period).

How to find the NR cells whose SSB is in a recording, with where each sits on
the raster, its SCS, PCI, SSB index, half-frame, period and CFO, and a chance
for each. The tool is `tools/iq-nr`, backed by `tools/fm_receiver/iqnr.py`
(numpy only). It is the NR counterpart of the PSS and SSS search in
`iqlte.py` (see [lte-verify.md](lte-verify.md)).

Placeholders are used throughout: PCI 123, GSCN 8000 (3721.44 MHz at 30 kHz),
centre 3720 MHz. Real captures, PCIs, GSCNs, ARFCNs, PLMNs, cell identities
and their frequencies stay in the user's recordings folder, never in the repo
or in these notes.

## What it does and does not do

It lists the SSBs in a capture: the GSCN, SCS, PCI, the SSB index and
half-frame (where the DM-RS says), the period, the CFO and a chance. The
search decodes nothing. It reads the PSS, SSS and the PBCH's DM-RS, and never
the PBCH data bits. The MIB, SIB1, the other SI and SIB9's time come from
`--decode`, which hands the block to an outside decoder, as `--decode` does
for LTE in [lte-verify.md](lte-verify.md). See
[Decoding a listed SSB](#decoding-a-listed-ssb---decode).

NR's sync signals are m-sequences, not LTE's Zadoff-Chu sequences, so
`iq-lte` can say only that an NR carrier is "possible". Use `iq-nr` on a
suspected NR carrier. `iq-lte` is not changed by this.

## The method

The steps follow `iqnr.py`'s docstring, which also has the wrong turns.

1. **Raster points (38.104 5.4.3.1, table 5.4.3.1-1).** The SSB's centre
   (SS_REF, subcarrier 120 of the SSB) can sit only on the sync raster. Below
   3 GHz it is N x 1.2 MHz + M x 50 kHz, with M in {1, 3, 5}, and GSCN =
   3N + (M - 3)/2 (GSCN 2 to 7498). From 3 GHz it is 3 GHz + N x 1.44 MHz, with
   GSCN = 7499 + N (7499 to 22255). With no `--ssb-hz`, every raster point
   whose whole SSB lies inside the capture (centre +/- 0.45 of the sample rate)
   is tried, at 15 kHz and at 30 kHz.

2. **Band cut.** The candidate's band is taken out of one FFT of the whole
   capture, moved to DC and inverse-transformed at 256 x SCS: 3.84 MS/s at
   15 kHz, 7.68 MS/s at 30 kHz. The FFT is shared by every candidate. The
   output is also made at half that rate for the PSS scan.

3. **PSS (38.211 7.4.2.2).** Three roots of a length-127 m-sequence, one per
   N_ID2 (0 to 2). The PSS replicas are correlated over
   the whole capture at half rate, each at five frequency offsets spread over
   +/- SCS/3 (the CFO grid). The score at each lag is the best normalised
   magnitude over the replicas. Peaks at least ten times the median are
   candidates, at most 16 plus one per 5 ms. The winning replica gives N_ID2.

4. **Timing and equalisation.** At each peak the four symbols are cut starting
   four samples early (inside the CP, so no leakage). A timing error is a
   linear phase ramp across the subcarriers, which wrecks a 127-subcarrier
   correlation. The ramp is found by a matched search over 801 slopes, then
   refined by a parabola, and removed from all four symbols. The slope is also
   the fractional timing, which gives the burst's start.

5. **SSS (38.211 7.4.2.3) and the PCI.** The SSS bins are scored against the
   336 sequences of that N_ID2. Each score is in [0, 1] and needs no noise
   estimate. Under "no sequence" the score's tail is exponential: E = -126
   ln(1 - s) is exponential(1). Bursts that agree on (N_ID2, N_ID1) are one
   cell, and they count, they do not add evidence. The chance is the tail of
   the best burst's E times every trial made: 336 hypotheses, the PSS peaks
   tried, the candidate frequencies and the spacings searched. A cell is
   listed only when that chance is at most 1e-3 (`chance`). The PCI is
   3 N_ID1 + N_ID2. `sss_score` is the winning SSS score and `sss_next` the
   runner-up's.

6. **DM-RS: SSB index and half-frame (38.211 7.4.1.4.1, table 7.4.3.1-1).**
   With the PCI known, the eight candidate i_bar sequences are correlated with
   the DM-RS of each burst, one symbol at a time (the CFO turns the phase from
   symbol to symbol, so the three symbols are not added coherently). At L_max 4
   the winning i_bar is i_SSB + 4 n_hf, so the half-frame bit is read. At
   L_max 8 it is i_SSB alone and `half_frame` is null. Each burst votes for its
   winner. `ssb_index` is the index with the most votes, ties broken by the
   summed DM-RS evidence. `dmrs_chance` is the tail over eight trials. The
   fields are null unless `dmrs_chance` is at most 1e-2. When `dmrs_chance`
   is above 1e-2, all bursts count as one index for the period, so `period_ms`
   can be non-null while `ssb_index` is null.

   The candidate positions of i_SSB in the half-frame are in 38.213 4.1
   (cases A, B and C, with 15 or 30 kHz). The tool does not test those
   positions; it reads i_SSB from the DM-RS.

7. **CFO (38.211 5.3.1, the cyclic prefix).** A CP sample equals the sample one
   useful symbol later, so an offset turns their product by 2 pi f nfft / fs.
   The CP products of all the bursts of the cell are summed and the phase read.
   It is unambiguous to +/- SCS/2 (7.5 kHz at 15 kHz, 15 kHz at 30 kHz).
   `cfo_hz` is the offset from `ssb_hz`.

8. **Period (38.213 4.1).** The bursts of one SSB index (one i_bar at L_max 8,
   one i_SSB at L_max 4) are timed. The median of the gaps (at least 4 ms)
   within 1.15 x the smallest gap is snapped to the nearest of 5, 10, 20, 40,
   80 or 160 ms. It is null with fewer than two bursts of one index, or when
   the median gap is more than 15 % of the nearest period away from it.

9. **Power and order.** `relative_power_db` is the SSB power (PSS and SSS
   subcarriers, noise included) against the strongest. Entries are listed by
   power, strongest first. A raster search keeps one entry per (GSCN, PCI),
   the one with the lower chance.

## Commands

Run from the repo root in the `gnu` conda env. numpy is the only dependency.

```sh
tools/iq-nr CAPTURE.cfile                                    # rate and centre from CAPTURE.sigmf-meta
tools/iq-nr CAPTURE.cfile --rate 20000000 --center 3720000000 --seconds 0.5 --scs 30
tools/iq-nr CAPTURE.cfile --ssb-hz 3721440000 --scs 30       # one frequency only
tools/iq-nr CAPTURE.cfile --t0 1.5 --seconds 2 --max-cells 8
```

A `.cfile` is raw complex64: float32 I then Q, interleaved, no header.

Options:

| Option | Meaning |
|---|---|
| `FILE` | the recording: `.cfile` (with its `.sigmf-meta`, or with `--rate` and `--center`), `.sdvr` (an IQ Sceptre DVR) or `.cdif` (a Sceptre BLUE recording) |
| `--rate HZ` | samples per second, for a `.cfile` with no `.sigmf-meta`; overrides the sidecar |
| `--center HZ` | centre frequency, for a bare `.cfile`; overrides the sidecar. A centre of 0 Hz in the sidecar or file counts as none: pass `--center`. `--center 0` itself is accepted. |
| `--t0 S` | start of the span, seconds (default 0) |
| `--seconds S` | length of the span (default 0.1 s; at most 2 s, a longer request is cut and `reason` says so) |
| `--ssb-hz HZ` | search only this SSB frequency (default: every raster point in the capture) |
| `--scs 15\|30` | search only this spacing (default: both) |
| `--max-cells N` | most SSBs to list (default 4) |

Only the span is read, never the whole file, so memory stays with the span
length.

The output is one JSON document on stdout. The top level has `file`, `rate`,
`center_hz`, `t0`, `seconds`, `verdict`, `reason` and `ssbs`. `verdict` is
`nr` when at least one SSB is listed, otherwise `none`. `reason` is an empty
string, or says why the span was cut, the file ended early, or samples were overwritten during an `.sdvr` read.

Made-up output for a 30 kHz, L_max 8 SSB (PCI 123, GSCN 8000, centre 3720 MHz;
values invented, nid1 and nid2 consistent with PCI 123):

```json
{
  "file": "CAPTURE.cfile",
  "rate": 20000000,
  "center_hz": 3720000000,
  "t0": 0,
  "seconds": 0.5,
  "verdict": "nr",
  "reason": "",
  "ssbs": [
    {
      "ssb_hz": 3721440000,
      "gscn": 8000,
      "scs_khz": 30,
      "pci": 123,
      "nid1": 41,
      "nid2": 0,
      "ssb_index": 5,
      "half_frame": null,
      "l_max": 8,
      "period_ms": 20,
      "bursts": 24,
      "first_ssb_s": 0.0123,
      "cfo_hz": 812.4,
      "pss_score": 0.42,
      "sss_score": 0.97,
      "sss_next": 0.11,
      "chance": 2.1e-09,
      "dmrs_score": 0.88,
      "dmrs_chance": 1.4e-12,
      "relative_power_db": 0,
      "detail": "PCI 123 (N_ID1 41, N_ID2 0), 24 bursts, SSS score 0.970, chance 2.1e-09, CFO 812 Hz"
    }
  ]
}
```

The fields of each `ssbs` entry:

| Key | Meaning |
|---|---|
| `ssb_hz` | the SSB's frequency searched (a raster point in raster mode) |
| `gscn` | the raster number of `ssb_hz`, or null when `ssb_hz` is not on the raster |
| `scs_khz` | 15 or 30 |
| `pci`, `nid1`, `nid2` | the physical cell ID, 3 N_ID1 + N_ID2 |
| `ssb_index` | the SSB index (i_SSB) with the most votes; null unless the DM-RS chance is at most 1e-2 |
| `half_frame` | 0 or 1 at L_max 4 (the half-frame bit of i_bar); null at L_max 8, and when `ssb_index` is null |
| `l_max` | 4 at or below 3 GHz, 8 above (from `ssb_hz`) |
| `period_ms` | 5, 10, 20, 40, 80 or 160; null with fewer than two bursts of one index, or no clean snap |
| `bursts` | bursts of this cell found in the span |
| `first_ssb_s` | start of the first burst, seconds from the span's start |
| `cfo_hz` | carrier offset from `ssb_hz`, from the CP products |
| `pss_score` | mean PSS match of the cell's bursts, 0 to 1 |
| `sss_score` | mean SSS score of the bursts, 0 to 1 (1 is a perfect sequence) |
| `sss_next` | mean of the runner-up SSS score, for comparison with `sss_score` |
| `chance` | chance that the PCI is noise; listed only when at most 1e-3 |
| `dmrs_score` | DM-RS match of the winning index, 0 to 1 |
| `dmrs_chance` | chance of the DM-RS match over eight trials |
| `relative_power_db` | SSB power against the strongest entry (0 for it, negative below) |
| `detail` | a one-line summary for people |

The `chance` has three factors that depend on the search, so it is not the
same number in every mode. It is multiplied by 336, the PSS peaks submitted
to the SSS test, and the number of candidate frequency and spacing pairs. A
raster search has many more candidates than an `--ssb-hz` run, so it sets a
higher bar: a weak cell can be listed with `--ssb-hz` and missing from a
raster run of the same capture.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The run completed, whatever the verdict (`none` is 0) |
| 1 | A bad option or value (`--t0` below 0, `--seconds` at or below 0, a non-finite number, `--max-cells` below 1, `--rate` at or below 0, `--scs` not 15 or 30, an unknown option), a file that cannot be read or that the reader refuses (missing, a sidecar that is not a JSON object), a sweep file (a `.sdvr` of spectra), no centre frequency (none in the sidecar and no `--center`), or a non-finite sample in the span. The reason is on stderr, nothing on stdout |

## Limits

- **CFO.** The PSS grid covers +/- 5 kHz at 15 kHz and +/- 10 kHz at 30 kHz
  (+/- SCS/3). That is the range the tests check. The CP estimate (step 7) is
  unambiguous further, to +/- SCS/2, but nothing before the PSS covers that.
- **Span.** Default 0.1 s from the start, at most 2 s. A longer request is
  cut, and `reason` says so.
- **Minimum length and rate.** Under 2 ms, or under 3.84 MS/s, the result is
  `[]` and the verdict `none`; there is no exception. At 30 kHz the band needs
  7.68 MS/s, so between 3.84 and 7.68 MS/s only 15 kHz is searched.
- **L_max.** 4 at or below 3 GHz and 8 above, taken from the frequency of each
  candidate. At L_max 8 there is no half-frame bit, so `half_frame` is null.
- **Null fields.** `ssb_index` and `half_frame` are null unless the DM-RS
  chance is at most 1e-2. `period_ms` is null as in step 8. A null is not a
  failure of the cell.
- **Several SSBs of one cell (known weakness).** A cell that sends several
  SSBs in each period (indexes 0, 1 and 3, say) is scored burst by burst.
  `ssb_index` is the index that won the most bursts, not the strongest one and
  not the set. The other indexes are not listed. Do not read `ssb_index` as
  the set of SSBs the cell sends.
- **A given `ssb_hz` off the raster.** The search runs at the frequency given,
  as for any other. Checked on the synthetic signal (SSB at 600.00 MHz, which is
  not a raster point, PCI 123, 15 kHz): the cell is listed with `gscn` null
  and the right PCI. The CFO grid is still +/- SCS/3 around the frequency
  given, so an SSB further off than that is not expected to be found; that is
  not tested. The tests use raster points only.
- **Chance depends on the mode.** See the note under the JSON table.
- **Synthetic only in the repo.** Every check in the tests uses the signal of
  `tools/tests/nr_synth.py` (the truth is in the signal's dictionary). The real
  air check is the planner's (G11 in the phase spec), run on captures that are
  not in this repo; what it found is under Status, without the values.
- **The search decodes nothing.** It never reads the PBCH data; the MIB, SIB1
  and SIB9 come from `--decode`.

## Wrong turns

These are the ones the docstring says were hit while building it. The method
above avoids each.

- A PSS correlation with no frequency grid: at 5 kHz the replica turns 2 rad
  over one symbol and the peak is gone. The five-offset grid removes it.
- The timing taken as a whole sample, then FFT there: a half-sample error
  loses most of an SSS correlation at 0 dB. The ramp is estimated, not rounded.
- The CFO read from the phase step between the PSS, SSS and DM-RS symbols.
  That phase also depends on where the SSB sits in the carrier. The CP product
  has no such term.
- Summing the evidence of bursts that agree on one cell ID. A bursty (uplink
  like) signal gave 1e-14 chances for four false cells. Agreement counts the
  bursts only.
- Combining the DM-RS of the three symbols coherently. The CFO turns the phase
  between symbols, so they are added in evidence, one symbol at a time.

## What is tested

Two suites. Each check is a line that says ok or FAIL. The acceptance letters
are the spec's (the phase spec, `tools/swarm-records/specs/n1.md`, section
"Acceptance targets"). A "counted" check passes when enough of its cases pass
(for example 38 of 40); the spec's gate for each is in the same line.

Run the two files with `python tools/tests/test_iqnr.py` and
`python tools/tests/test_iq_nr.py`, or with `tools/tests/run_all.py`. None needs
a radio.

| Letter | What it checks | File | Checks |
|---|---|---|---|
| A | right PCI, 15 kHz case A, L_max 4, 10 PCIs x 2 SNRs x 2 seeds (40 cases; 38 needed, 20 of 20 at 20 dB) | test_iqnr.py | 2 |
| B | right PCI, 30 kHz case C, 20 MHz, L_max 4 and 8 (20 cases; 19 needed) | test_iqnr.py | 1 |
| C | raster search: right GSCN, SCS and PCI (18 of 20); no entry at a wrong GSCN (19 of 20) | test_iqnr.py | 2 |
| D | two carriers in one capture, both PCIs at their own GSCNs (4 of 5) | test_iqnr.py | 1 |
| E | `ssb_index` and `half_frame` right (22 of 24); `period_ms` right at 20 and 10 ms (10 of 10) | test_iqnr.py | 2 |
| F | `[]` on the five negatives and `synth_lte` (30 of 30); 1 ms and 1 MS/s captures `[]`, no exception (2 of 2) | test_iqnr.py | 2 |
| G | raster table: the spec's nine examples both ways, exact to 1 Hz; `hz_to_gscn(600.10 MHz)` is None; GSCN 1 and 22256 raise ValueError | test_iqnr.py | 4 |
| H | CFO: right PCI at +/- 5 kHz (15 kHz) and +/- 10 kHz (30 kHz) (4 of 4) | test_iqnr.py | 1 |
| I | time and memory: raster search under 30 s and `ssb_hz` search under 5 s on 100 ms at 20 MS/s; `iq-nr` on a 1 s, 40 MS/s file with `--seconds 0.1` runs and peak RSS is under 700 MB | both | 2 + 2 |
| J | command: exit 0 and one JSON document with the spec's fields; verdict `nr`; rate and centre read from the sidecar; strongest SSB is PCI 123 at GSCN 1500; noise gives `none` and `[]`; `--seconds 5` cut to 2 with a reason; five refusals with exit 1 | test_iq_nr.py | 12 |
| - | no entry with chance above 1e-3 or a repeated (GSCN, PCI) (zero tolerance, all cases) | test_iqnr.py | 2 |
| - | every entry has the spec's fields; `raster_in_band` equals the list computed in the test | test_iqnr.py | 2 |
| - | `half_frame` is null at L_max 8 | test_iqnr.py | 1 |
| - | imports only numpy, the standard library and `fm_receiver` (G10) | both | 1 + 1 |

Total: 23 checks in `test_iqnr.py` and 15 in `test_iq_nr.py`.

The timing and memory checks (I) are the only ones that depend on the
machine. Their limits are in the spec: raster under 30 s, `ssb_hz` under 5 s,
the command under 700 MB peak RSS, and both files under 240 s together.

## Our own MIB decoder (`--mib`, roadmap Round 28.1)

`tools/iq-nr FILE --mib` decodes each listed cell's PBCH with
`fm_receiver/iqnr_phy.py` (numpy only, written from 38.211 and 38.212; the
polar sequence, the input and sub-block interleavers and the PBCH payload
interleaver are copied from 38.212 V19.4.0, and a test pins them). It takes
every sync block the search kept, each with its own DM-RS `i_bar`. For each
block it:
1. estimates the channel per symbol from the PBCH DM-RS (least squares, a
   moving average over 5 DM-RS, linear between);
2. takes QPSK soft bits and undoes the PBCH scrambling;
3. sums the repeated bits and undoes the sub-block interleaving;
4. decodes the polar code with a successive-cancellation list of 8, the
   CRC 24C choosing the path;
5. undoes the payload scrambling and interleaving.

Each entry gains `mib` (the first block's MIB whose CRC checked, with
`ssb_s`, that block's time, `snr_db` per PBCH RE and the path `metric`) and
`mib_bursts` (`[decoded, blocks]`). `intra_freq_reselection` is
`allowed` or `notAllowed`, where the outside program gives `true` for
allowed. L_max 4 only (SSBs at or under 3 GHz). Without `--mib` the output
is unchanged.

On real air (2026-10-24, the four saved NR recordings), every MIB field
equalled the outside program's on every cell it decoded. The SFNs agreed
once moved to the same block. Every block of every cell decoded, 5 of 5,
including a sector 12 dB under the strongest (PBCH SNR -2 dB) that the
outside program never read. Its MIB names another search space 0, so its
SIB1 control channel is in other slots than the strongest sector's.

Tests: `tools/tests/test_iqnr_phy.py` (synthetic; `nr_synth` can now carry
a real PBCH).

## Decoding a listed SSB (`--decode`)

`tools/iq-nr FILE --decode` goes one step past the search. For each SSB the
search listed, it hands a block of the recording to an outside program, and
reads back the MIB, SIB1, the other system information and, where SIB9
carries it, the network time. The program is `$FMRX_LTE_TOOLS/run_nr.sh`,
srsRAN 4G's NR library (AGPL). It is not in this repo and is never installed
here. No code of ours decodes NR yet (roadmap Round 28 plans it): `iqnr_decode.py` cuts the samples, runs that
program, reads its tagged lines and sends the RRC messages to pycrate.

The program reads only the broadcast channel. It searches the SI-RNTI
(0xFFFF) and no other RNTI. No other RNTI is named, passed or searched.

The RRC messages are parsed with pycrate in a subprocess, under
`$FMRX_LTE_TOOLS/pyc/bin/python`. `iqnr_decode.py` never imports pycrate. This
is the same arrangement `iqlte_decode.py` uses for LTE.

Without `--decode` the output is unchanged: no `decode` key, and the search
span stays the short one (`--seconds`, 0.1 s by default).

## Decode options and exit codes

| Option | Meaning |
|---|---|
| `--decode` | decode each listed SSB (the `decode` key described below) |
| `--decode-seconds S` | how long from `--t0` the decode reads. Default 6 s. At most 10 s: a longer value is cut to 10 s and `decode.reason` says so. A value of 0 or less is refused: exit 1, with a message on stderr. |

The decode span does not change the SSB search. `--seconds` still sets the
search, and `--decode-seconds` sets only the decode's read.

Exit codes for `--decode`:

- **Exit 1** for a refused option (for example a `--decode-seconds` of 0 or
  less). The message on stderr says what was refused, and nothing is written
  to stdout.
- **Exit 2** when `run_nr.sh` is missing from the tools folder (or the folder
  is not there). The tools folder is `$FMRX_LTE_TOOLS`. When `FMRX_LTE_TOOLS`
  is unset, the tool uses a default folder instead, so exit 2 also comes then
  if that folder has no `run_nr.sh`. The message on stderr names `run_nr.sh`.
  Nothing is written to stdout.
- **Exit 0** otherwise. A missing pycrate interpreter is not an exit code. It
  is a reason on the decode: `sib1` is null, `reason` names pycrate, and the
  MIB is still reported.

The other exit codes are those of the search (see the Exit codes table above).

## How the decode works

1. **Cut and resample.** The SSB is mixed so it sits at 0 Hz, band-limited
   and resampled to 23.04 MS/s. The output is a cf32 file in a private
   temporary folder. The span is never held whole: 6 s at 40 MS/s is 1.9 GB as
   complex64. The folder is deleted in every case.

2. **Pass 1.** `run_nr.sh` is run once with the file and the SSB's position
   (see the contract below). Its MIB, point A, SI blocks and CRC failures are
   collected. SIB1 is the most common block with `sii` 0.

3. **SIB1.** The SIB1 candidates are parsed with pycrate, most common first.
   The first that parses as SIB1 is used, so one corrupt block that passed the
   CRC does not hide the cell.

4. **Pass 2.** When SIB1 schedules other SI and carries `offsetToPointA`, the
   program is run again with the time allocation list (`-t`) and
   `offsetToPointA` (`-a`). The `-t` values are SIB1's
   `pdsch-TimeDomainAllocationList` `startSymbolAndLength` values, in list
   order. `-t` is left out when SIB1 has no list.

   Then every distinct block of both passes is parsed, whatever its SI
   indicator (`sii`), and each SystemInformation message counts as other SI.
   A cell may send SI that SIB1 does not schedule, and in SIB1's own slots
   with `sii` 0: one barred cell on air sent SIB2 that way, with SIB1
   scheduling nothing. SIB9's time is read from these blocks, scheduled or not.

   Pass 1 reads CORESET 0 in every slot, so it also sees other SI sent in
   its own slots. It cannot decode it, because that SI's DM-RS counts from
   CRB 0 and needs `-a`. Those blocks come back as `NR_SI_CRC_FAIL` or
   `NR_SI_ERR` with `sii` 1. When pass 1 had any and SIB1 schedules nothing,
   pass 2 runs all the same, and `reason` says so. Pass 1's other `sii` 0
   failures are SIB1's repeats. On the barred cell, SIB1 came every 80 ms
   with redundancy version 0, which decodes. Its repeats 20 and 40 ms later
   used versions 2 and 3, which do not decode alone: 31 of the 38 failures
   were those, and the other 7 were version-0 copies of SIB1 that failed.

5. **Clean up.** The temporary folder is deleted, on success and on every
   failure path.

## The decode output

Each entry of `ssbs` gets a `decode` object. Its keys are these.

| Key | Meaning |
|---|---|
| `ran` | true when the decode ran |
| `decoded_pci` | only when the outside program read another cell than this entry (a stronger cell on the same SSB frequency, such as another sector), and the retry aimed at this entry's PCI read no MIB of it (or the program has no `-P`): the PCI it read. `mib` and `sib1` are then null, and `reason` names both PCIs and what the retry said. When the retry reads this cell's MIB, there is no `decoded_pci`: the decode is this cell's, `mib.targeted` is true and `reason` names the stronger cell |
| `mib` | the MIB's fields: `pci`, `sfn`, `ssb_index`, `half_frame`, `scs_common_khz`, `ssb_subcarrier_offset`, `dmrs_typeA_position`, `coreset0_index`, `search_space0_index`, `cell_barred`, `intra_freq_reselection`, `ssb_sample`, `cfo_hz`, `snr_db`, and any other field the program gives. Null when there is no MIB. For a weaker sector decoded by aiming at its PCI, `targeted` is true, `strongest_pci` and `strongest_ssb_sample` name the stronger cell, and `cfo_hz` and `snr_db` are null (the SSB entry's own values stand) |
| `sib1` | null when SIB1 was not parsed. Otherwise an object: `plmns` (`"MCC-MNC"` strings, with the MNC's digit count kept, so `"001-01"` and `"001-001"` are different), `tac`, `cell_identity` (36 bits), `cell_barred`, `si_window_slots`, `si_schedule` (a list of `periodicity_rf`, `broadcast_status` and `sibs`), and `carrier` (`point_a_arfcn`, `point_a_mhz`, `bands`, `carrier_prb`, `scs_khz`, `offset_to_point_a`) |
| `si` | null when SIB1 scheduled no other SI and none was heard. Otherwise: `sibs_heard` (count of each SIB heard), `sibs` (each SIB's contents), `scheduled` (the SIBs SIB1 schedules), `missing` (scheduled and on air, but not heard) and `unscheduled` (heard, but not scheduled by SIB1; `reason` names them too) |
| `time` | null when there is no network time. Otherwise: `utc` (ISO 8601 with milliseconds), `gps`, `leap_seconds`, `local_offset_minutes`, `dst_hours`, and `reference_time`. Each is present only when SIB9 carries it. `reference_time` holds the raw `referenceTimeInfo-r16` fields and `gps_reading` (ISO 8601 GPS time) |
| `time_reason` | null when `time` is set. Otherwise one of the values below |
| `reason` | the reasons that apply, joined with `; `; null when there are none. It can hold the cut from `--decode-seconds`, a `run_nr.sh` error, a pycrate note, the SIBs not heard, and `the other SI were not searched (...)`. When there is no MIB, the reason is the `NR_NO_MIB` reason if the program gave one, else `no MIB decoded`. When there is no SIB1, the reason is the `NR_DONE` reason if the program gave one, else `no SIB1 heard in N s` (when no SIB1 block was heard at all; when blocks were heard but none parsed, the note says so and no stop reason is added) |
| `blocks` | counts over the passes: `sib1` and `other` (the `NR_SI` lines with `sii` 0 and the rest, summed over both passes), and `crc_fail` (the larger of the two passes' `NR_SI_CRC_FAIL` counts, not the sum, since both passes read the same span) |
| `seconds` | the length of the span decoded, after any cut |

A missing `time` is a result, not a failure. `time_reason` says why: a
SIB9 that is not scheduled, a SIB9 that is scheduled on demand, and so on. The
entry is still listed and the exit is still 0.

The values `time_reason` can take:

- `no MIB`
- `no SIB1`
- `SIB1 does not schedule SIB9`
- `SIB9 is not broadcast (si-BroadcastStatus notBroadcasting: on demand)`
- `SIB9 was heard but carries no timeInfo`
- `SIB9 scheduled but not heard in N s`, with ` (the other SI were not searched: ...)` added when pass 2 did not run. The text after the colon is `SIB1 has no offsetToPointA` or `pass 2 did not run`

## The outside program's contract

The argv is fixed. The first pass is:

```
run_nr.sh FILE 23040000 0 SSB_HZ SCS_KHZ
```

`FILE` is the cf32 file, `23040000` the sample rate, `0` the SSB's offset in
Hz (the SSB is at 0 Hz), `SSB_HZ` its absolute frequency and `SCS_KHZ` 15 or
30. The second pass adds `-t SLIV,...` (when SIB1 has a list) and
`-a OFFSET_TO_POINT_A`. When the first pass's MIB has another PCI than the
entry's, the first pass runs again with `-P PCI` (the entry's), and the
second pass keeps it. The full form is:

```
run_nr.sh FILE SRATE SSB_OFFSET_HZ SSB_ABS_HZ SCS_KHZ [-m MAX_MS] [-t SLIV,...] [-a OFFSET_TO_POINT_A] [-P PCI]
```

With `-P` the program still runs its blind search first, for the carrier
offset. Then it searches every 1 ms subframe from the file's start for that
PCI's PSS and a PBCH with a good CRC. `NR_MIB` then has `targeted` true,
`strongest_pci` and `strongest_ssb_sample`. Aimed at the strongest cell
itself on air, this path gave the same MIB and SIB1, the SSB 3 samples from
the blind search's. A program without `-P` exits non-zero, and the decode
then ends as for another cell (below).

The decode never passes `-m`. The program's contract accepts it, and the
decode code does not use it.

Its stdout is lines of the form `TAG {json}`. Any other line, and any line
whose JSON does not parse (a truncated last line, say), is ignored. The tags
are:

- `NR_MIB`: the MIB was decoded.
- `NR_NO_MIB`: no MIB. It may carry a `reason`.
- `NR_CORESET0`: CORESET 0 and point A. It carries `pointA_hz`.
- `NR_SI`: one SI block as hex. `sii` 0 is SIB1, 1 another SI message.
- `NR_SI_CRC_FAIL`: an SI block that failed its CRC. Counted.
- `NR_SI_ERR`: a diagnostic. Not counted.
- `NR_DONE`: the run's end. It carries a `reason` when the run stopped early.

The program exits 0 when it ran. A non-zero exit is a note on `reason`, and
the lines it printed are still read.

## The outside program's local fixes

The program is built outside the repo from srsRAN 4G 23.11's NR library. For
SI decoding it does three things. This is the method, not the program itself,
which is not in this repo.

- The other SI messages' PDSCH DM-RS count from common RB 0 (point A, from
  SIB1's `offsetToPointA`). Only SIB1's count from CORESET 0 (38.211
  7.4.1.1.2). The CORESET 0 reference is not used for the other SI-RNTI PDSCH.
- The other SI use SIB1's time allocation list (38.214 table 5.1.2.1.1-1).
- The DM-RS position must be copied: for cells with position 3, the MIB's
  `dmrs-TypeA-Position` goes into the PDSCH DM-RS configuration.

## Decode limits

- **TDD special slots** are not handled.
- **SSBs above 3 GHz with 30 kHz CORESET 0** are not supported: srsRAN lacks
  the 30/30 table.
- **On-demand SIB9** (`notBroadcasting`) gives no time. The reason says so.
- **The span.** The decode reads only the span given (`--t0` and
  `--decode-seconds`, at most 10 s). A SIB9 that comes around later than the
  span is not heard.
- **The time from SIB9** is SIB9's `timeInfoUTC`, converted as LTE SIB16 is.
  The decode reports that time as SIB9 carries it and does not check it
  against the capture's timing. When SIB9 has no `timeInfo`, `time` is null
  and `time_reason` says so.
- **A weaker sector gives its MIB, not yet its SIB1.** The outside program
  locks to the strongest cell on an SSB frequency, so a weaker sector there
  (another PCI) is decoded by aiming at its PCI (`-P`). On air, one site's
  three sectors shared one SSB frequency (2026-10-24). The second, 7 dB
  under the strongest by `iq-nr`'s `relative_power_db`, gave its own MIB that way,
  with the same SFN as the strongest and its SSB one SSB slot later. It gave
  no SIB1: it uses the same CORESET 0 and search space as the strongest, so
  its PDCCH lands on the stronger cell's. The third gave no MIB, so it kept
  `decoded_pci`. The program's `snr_db` and `cfo_hz` for an aimed cell are
  not to be trusted, so the decode sets them to null in `mib` (and `reason`
  says so); the SSB entry's `relative_power_db` and `cfo_hz` stand: for that sector it said -21 dB and -552 Hz, where
  `iq-nr` measured 7 dB under the strongest and +252 Hz (the strongest:
  +332 Hz, and the program's own +338 Hz). Its SSB timing agreed with
  `iq-nr`'s to a microsecond.
- **Unscheduled SI is searched in every slot, but only in CORESET 0.** Pass 1
  reads every slot. Pass 2 runs when SIB1 schedules other SI, or when pass 1
  saw `sii` 1 blocks fail, and needs `offsetToPointA`. SI under another
  CORESET or search space is not looked for. On the barred cell (SIB1
  scheduling nothing), every SI-RNTI grant in 6 s was SIB1, one of its
  repeats, or the SIB2 in SIB1's slots: nothing came in its own slots.

## Decode tests

`tools/tests/test_iq_nr_decode.py` tests the decode with a stub
`run_nr.sh`, a Python script behind an sh wrapper. It prints the contract's
lines from blocks that the test encodes with pycrate. The stub is never the
real program, and the real program is never named or run. The checks that
need pycrate skip when no interpreter imports it. Nothing in the tests runs
on air; the real-air check is below.

## Status

Built, and tested on the synthetic signal of `nr_synth.py` (the suites above)
and, for `--decode`, with a stub program (the decode tests above).

On real air (the planner's check, G11; BB60D recordings at 20 MS/s, values
kept outside the repo):

- **Search (2026-10-22).** Three 850 MHz NR captures: every SSB found by hand
  was listed, plus two weaker sectors of the same sites. Two LTE carriers and
  an ATSC channel gave `none`. About 5 s for the default span.
- **Decode (2026-10-23).** Two cells, 6 s each, compared field by field with
  the outside program run by hand. The MIB matched on every field on both.
  The first cell (not barred, one operator): SIB1's operator, tracking area
  and cell identity matched, the carrier point A matched the formula in the
  glossary, SIB2, 3, 4, 5 and 9 were heard, and SIB9's UTC was 2.4 s after the
  capture's start (by the recording's start time: the `.sigmf-meta` of that
  day held the stop time, a bug fixed since). The second (barred, two
  operators): the operators and cell identity matched; `time` null with "SIB1
  does not schedule SIB9". Since 2026-10-23 the SIB2 the outside program heard
  on it is heard too, and listed in `si.unscheduled`: it came in SIB1's slots.
  Each decode took about a minute.

See [capabilities.md](capabilities.md) and Round 26 of [roadmap.md](roadmap.md).
