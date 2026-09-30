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

Sceptre may be writing the file while it is read. Pausing the DVR first is
still the surest read, but a running one can be read too (see "A DVR that is
still recording"): the reader copes with what a live file does.

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

## A DVR that is still recording (2026-09-30)

Read on a live sweep DVR (52 tiles of 16 sweeps, one every 3.7 s, about 190 s
in the ring) and tested on synthetic files; not yet tried on a live IQ DVR.
What a file being written does, and what the reader does about it
(`sceptre_dvr.scan`, `scan_sweeps`, `Reader`, `Sweeps`):

- **A file counts as live** if it was written in the last 8 s (a sweep DVR
  writes a tile every 3.7 s); `live=True/False` overrides.
- **Page 1's window is not to be believed.** A sweep DVR's still described an
  earlier IQ recording (hours old), so a live file, or one whose window holds
  none of its chunks, is read by its chunks: the newest unbroken stretch in
  time (no gap over a second, or twice a chunk's length plus half a second) is
  the recording, and what lies before a break is left over. A paused IQ DVR
  whose window fits is read by the window, as before.
- **The recording's kind is the newest chunk's**: its interval and centre for
  IQ, its start, bin width, bin count and sweeps for tiles. An old recording
  of another kind left in the ring (an IQ mode's 16384-bin tile beside a sweep
  DVR's 1,228,800) is left out.
- **The newest chunk is left out**, in case it is half written. (Seen: a tile
  appears whole about 10 s after its timestamp, and tiles already in the ring
  do not change; a half-written one has not been caught, so this is
  cautious, not observed.)
- **Overwrites are counted.** The oldest chunk is the next one Sceptre
  overwrites. `Reader.fill` and `Sweeps.read` compare each chunk's header time
  with what the scan saw, after copying, and count a change in `overwritten`;
  `Sweeps` also leaves that tile out. A scan is a snapshot: rescan for the
  chunks written since, and expect the oldest to go.
- **Sweep tiles.** `Sweeps.read(f_lo, f_hi, first, stop)` gives `(times,
  freqs, levels)`: every sweep of the tiles, in time order, cropped to the
  band, as rows of bins (the stored bin-by-bin order undone): int8 bytes, or
  float32 dBm with `dbm=True`. `tools/dvr-sweep DVR [--band LO HI] [--png
  FILE] [--bytes]` reports what a DVR holds and draws it in dBm.

## A tile's own scale: the dark bands, and the dBm of the bytes (2026-09-30)

The raw bytes of a sweep DVR show dark and light bands across the whole
waterfall, each exactly one tile (3.7 s) or several long, all frequencies at
once, up to 6 dB. They are not in Sceptre's own viewer (user's screenshot of
the same kind of capture), and they are not the signal: **each tile carries
its own scale**, two float32s in its header page, at 8 (`gain`) and 12
(`offset`), and
**`byte = gain * dBm + offset`**, so **`dBm = (byte - offset) / gain`**
(`Sweeps.read(dbm=True)`, `Sweeps.calibration`). Found from the files alone:

- The level is constant within a tile (sd 0.17 of a byte) and steps between
  tiles. A per-tile gain and offset fitted from 39 bands across the span
  (taking the long-run level of each band as the reference) gave each tile a
  byte offset equal to `1.006 * offset - 121.13 * gain - 1.1` (R² 1.000) and a
  slope that followed `gain` (correlation 0.98): the formula above.
- Applying it, the level of each band varies 0.25 dB between tiles (4.6 bytes
  before), so the bands are gone and the picture matches Sceptre's own.
- The scale is set by the tile's extremes: every tile has both +127 and -127.
  +127 is at bin 0 (the 4.9 kHz bin, in all 16 sweeps) at about **-53 dBm**
  (sd 1.4 dB between tiles), and -127 is one bin, somewhere 1 to 5 GHz, at
  **-181 to -205 dBm**, a different value each tile (the deepest of 20
  million samples). `gain` is 254 over the span between them, 1.6 to 2.0, so a
  count is 0.50 to 0.62 dB, and `offset` is 205 to 233. That is why a fixed
  0.4 dB a count with an offset fitted to the byte could not be pinned down
  before (residual 7 dB), and why the bytes of two tiles cannot be compared.
- Absolute level: FM carriers read about -59 dBm (99.9th percentile) and the
  band's floor -99 dBm (10th percentile) at a -40 dBm reference, where the
  BB60D's own calibrated sweep gave -61 to -62 and -93 to -101; the noise
  floor falls from -87 dBm at 40 MHz to -118 dBm above 2.4 GHz. Not checked
  against a float export of the same capture, which would settle the
  absolute scale to a fraction of a dB, nor against a test tone.
- Bytes at +-127 are the ends of the scale, not measurements: bin 0 is always
  at the top. Not verified for an IQ DVR's tiles (16384 bins), whose header
  floats have not been looked at.

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

- **The sweep DVR's dBm against a float export.** The tile header's gain and
  offset ("A tile's own scale") give dBm that is steady between tiles and
  close to the BB60D's calibrated sweep (a couple of dB), but it has not been
  matched cell for cell to a `.fft` export of the same capture, or checked
  with a test tone. How the reference level enters the top (-53 dBm at a
  -40 dBm reference) is not known. An IQ DVR's tiles were not looked at.
- **A test tone.** The IQ's scale ("The IQ's level in dBm" below) is Sceptre's
  own, in two places that agree to 0.00 dB, and within about 1 dB of the
  BB60D's calibrated sweep. It has not been checked against a source of known
  level (the toolkit's VSG60 is calibrated), and the 3 dB between a peak and an
  rms reading of a complex sample was settled by the sweep check, not by
  Sceptre's documentation.
- **A live IQ DVR.** The live handling was written from a live sweep DVR and
  synthetic files. A running IQ DVR has yet to be read (Sceptre was on the
  Sweep tab): whether its window is kept current, and whether its newest run
  is ever seen half written.
- **Other stream types.** Only an IQ tab's DVR and a sweep DVR have been seen.
- **Whether the header checksum matters** to Sceptre itself: this reader
  ignores it, and never writes the file.

## A float export is the exact route

*Save Entire DVR Spectrum to Recording Database* with the **32-bit** format
writes a BLUE file (X-Midas: `BLUE` and `EEEI` at the start, type `SF`,
little-endian float32 in dBm, 512-byte header and extended header, data at
byte 3072 in the one seen), one row of 1,228,800 values per sweep, with the
same times as the DVR's. It is what would check the sweep DVR's dBm exactly, at 4 GB
for a 1 GB DVR.
