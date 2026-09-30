# Sceptre's DVR file (`dvr.sdvr`)

Signal Hound's Sceptre records what the BB60D receives into one fixed-size
ring file, `dvr.sdvr` (1 GB by default, in its DVR folder), so the DVR viewer
can scroll back through it. This is what is known about that file. It was
worked out from the files alone, and from what Sceptre's own displays said
about the same data (a hovered dBm, the sweep settings, a float export of the
same capture); Sceptre's program and library were never read, decompiled or
disassembled, as Signal Hound's licence forbids. The code that reads it is
`tools/fm_receiver/sceptre_dvr.py`; the test is `tools/tests/test_sceptre_dvr.py`
(synthetic files: no Sceptre data is kept in this repository).

Sceptre may be writing the file while it is read. **Pause the DVR first.**

## What is settled

- **Pages of 4096 bytes.** Page 0 starts with the ASCII `SDVR`. Pages 1 and
  2 describe the streams: at 0, two doubles for the start of the window of
  time the ring holds (whole seconds since 1970, then the fraction), at 16
  two more for its end, at 32 the band recorded (low and high, Hz; 86 to 114
  MHz for a BB60D at 100 MHz). Page 3 can be left over from an earlier use of
  the file (it once held the sweep stream's bin size and stop frequency).
  Data starts at page 4.
- **Chunks.** From page 4 the file is a series of chunks, each one header page
  followed by its data pages. Both kinds have the same header fields: at 0x10
  a two-letter tag, at 0x14 the chunk's time (two doubles as above), at 0x24
  the interval between its samples or sweeps in seconds, at 0x2c the count
  of them. The last four bytes of a header page differ from chunk to chunk
  (a checksum; not worked out, not needed).
  - `CI` (IQ): the centre frequency is a double at 0; the count is 1,048,576
    samples, so 1,024 data pages of **int16 I, then int16 Q, full scale
    32768**. The sample interval gives the rate exactly: 28 MS/s for a BB60D
    at the widest (27 MHz filter) - not the 28.2 that Sceptre's status
    panel shows. The stereo pilot of a broadcast FM station lands on 19.000
    kHz only at 28.0 (a good check of any rate).
  - `SB` (spectrum tile): the count is 16 sweeps; at 0x34 the start
    frequency, at 0x3c the bin width (Hz), at 0x44 the number of bins.
    The data is `count` x `bins` **signed bytes, stored bin by bin** (the 16
    sweeps of a bin side by side). In IQ mode a tile is 16384 bins over the
    recorded band, one sweep per 65536 samples (2.34 ms) of the run it
    follows. These are what the DVR viewer draws its waterfall from. In sweep
    mode (the sweep DVR, `in_1.sweep`) the same block holds 1,228,800 bins
    (9 kHz to 6 GHz in 4.88 kHz) and 16 sweeps of the BB60D's own sweep.
- **The rhythm.** A run is 1,025 pages (header and data), a tile 65, and
  they alternate at an uneven rhythm: after a run come none, one or two
  tiles, so the average is one of each. About 6.4% of the file is tiles.
  A 1 GB ring holds a little over 8.6 s of IQ at 28 MS/s. (Not knowing this,
  the tiles once looked like bursts of interference in the IQ.)
- **The ring wraps.** The newest chunks are early in the file and the oldest
  come after them, so chunks are read in time order (from their times), not
  file order. Chunks older than the window on page 1 are left over from
  before, and are dropped.
- **Reading it.** Walk chunk by chunk from page 4 (a header says how long its
  data is), and step a page at a time where no header is found. Count a run
  that does not follow on from the one before as a gap.

## The IQ's level in dBm (2026-09-30)

Levels in dBFS (as the app shows) are relative to the ADC's full scale, and
that moves with the reference level - the same signal reads 10 dB lower at
-20 dBm than at -30 - so dBFS is enough inside one capture (SNR, one station
against another, shapes, decoding) but cannot compare two captures, a capture
with a sweep, or a level with a limit. A DVR carries what turns it into dBm:

- Each `CI` header has a float32 at byte 8: the size of one count in Sceptre's
  own units, the square root of milliwatts. **Power is `(I² + Q²) × scale²`
  mW**, so `dBm = dBFS + full_scale_dbm`, and `full_scale_dbm` is
  **the reference level + 10** (the scale is 10^((ref + 10)/20) / 32768: 20 dB
  more for each 20 dB of reference level, seen at 0 and -20 dBm).
  `sceptre_dvr.scan` returns `scale` and `full_scale_dbm`; `dvr-hd --info`
  prints it.
- **Checked twice.** (1) A `.cdif` Sceptre extracted from the same DVR (a
  3 MHz channel at 99.269 MHz, 3.5 MS/s) is the DVR's counts (matched 0.995,
  -0.04 dB, correlation 0.998 sample for sample), and its `DATA_GAIN` keyword
  (100.309 dB) is exactly `-20*log10(scale)` from the DVR's header: the power in
  the same 2.5 MHz came out -50.8 dBm from the `.cdif` (`10*log10(|x|²) -
  DATA_GAIN`) and -50.8 dBm from the DVR's own header scale, a difference of
  0.00 dB. So the header scale is Sceptre's own calibration, in two places.
  (2) Against the BB60D's sweep, on a capture at -20 dBm (full scale -10 dBm) against levels the
  BB60D's own calibrated sweep gave for the same band earlier (the reference
  level there was -40 dBm; the environment the same): a strong station's carrier
  in a 21 kHz slice -61.8 dBm against -61 to -62 read by the sweep, and the
  floor above the band -96.7 dBm against -93 to -101. The other reading (a
  complex sample's power as half its squared magnitude) would be 3 dB lower and
  did not fit. Not checked with a test tone of known level: the calibration is
  Sceptre's, and it agrees with itself and with the sweep to about 1 dB.
- The app still shows dBFS for a DVR; adding the offset there is on the roadmap.

## Sceptre's recordings (`.cdif` and `.fft`)

The *Recordings* folder beside the DVR holds what Sceptre exports, in BLUE
(X-Midas) files, `sceptre.db` (SQLite, a row per recording: centre, sample
rate, start, duration, format, min and max) listing them. A `.cdif` is IQ:
`BLUE`/`EEEI` header, type 1001 and format `CF` (complex float32,
little-endian), data at byte 512, then a keyword block after the data
(`SAMPLE_RATE`, `RF_FREQ`, `PRETUNED_CENTER_FREQ`, `DATA_BANDWIDTH`,
`DATA_GAIN`, `TIME_EPOCH`, `SCEPTRE_MIN_VAL`/`MAX_VAL`, and the stream it came
from, e.g. `.../rawcorrector/CorrectedRaw/DVR/Channel 1`). One extracted from
a DVR is the DVR's counts, mixed to the channel, filtered and resampled (to
0.995 of the raw count, its flatness correction): so the floats are counts,
`dBm = 10*log10(|x|²) - DATA_GAIN`, and `DATA_GAIN` is the DVR's header scale
in dB (100.309 at a -20 dBm reference level). `sceptre_blue.py` reads them
(`Blue`: format, rate, centre, start, keywords, samples); `tools/dvr-to-iq`
makes the same kind of channel from a DVR, and matches Sceptre's own (0.998
correlation, 0.04 dB), so a `.cdif` is only needed for its metadata.

## What can be done with an IQ DVR

- **Play it in the app** (`--file dvr.sdvr`, or the open dialog): the whole
  27 MHz is there, tunable across ±12.45 MHz of the centre (87.55 to 112.45
  MHz for 100 MHz). Reception was good (SNR up to 20-24 dB, the stereo pilot
  locked, RDS with full station names) at a **reference level of -20 to
  -30 dBm**; at 0 dBm the same stations were about 10 dB lower in SNR (2-10
  dB) and a 1 s test decoded no RDS (HD Radio was not tried at 0 dBm). Set it
  in Sceptre's IQ tab before recording.
- **Decode HD Radio offline** with nrsc5 for whole, unbroken programs:
  `tools/dvr-hd` (usage.md; method in `digital-radio.md`). The app's loop
  restarts nrsc5 every 8.6 s, which costs the slower programs.
- **A longer capture** needs a narrower IQ rate in Sceptre: at 28 MS/s the ring
  holds 8.6 s, and one HD station needs only about 1.5 MHz. (Not tried.)

## Not settled

- **The dBm of the sweep bytes.** The sweep DVR's tile bytes are an 8-bit
  fixed-point log power. About 0.4 dB per count fits both a float export of
  the same capture and hovered readings (residual around 7 dB - the viewer
  draws coarser tiles than the stored ones, so the readings could not be
  matched cell for cell). The offset is near -115 dBm at byte 0 with a
  reference level of -40 dBm; how the reference level enters is not known.
  The minimum and maximum Sceptre lists for an export (about -210 and
  -40 dBm) are the export's own extremes, not the byte range.
- **A test tone.** The IQ's scale ("The IQ's level in dBm" below) is Sceptre's
  own, in two places that agree to 0.00 dB, and within about 1 dB of the
  BB60D's calibrated sweep. It has not been checked against a source of known
  level (the toolkit's VSG60 is calibrated), and the 3 dB between a peak and an
  rms reading of a complex sample was settled by the sweep check, not by
  Sceptre's documentation.
- **Other stream types.** Only an IQ tab's DVR and a sweep DVR have been seen.
- **Whether the header checksum matters** to Sceptre itself: this reader
  ignores it, and never writes the file.

## A float export is the exact route

*Save Entire DVR Spectrum to Recording Database* with the **32-bit** format
writes a BLUE file (X-Midas: `BLUE` and `EEEI` at the start, type `SF`,
little-endian float32 in dBm, 512-byte header and extended header, data at
byte 3072 in the one seen), one row of 1,228,800 values per sweep, with the
same times as the DVR's. It is what calibrates the sweep DVR's bytes, at 4 GB
for a 1 GB DVR.
