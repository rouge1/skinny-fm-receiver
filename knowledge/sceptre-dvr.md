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

## What can be done with an IQ DVR

- **Play it in the app** (`--file dvr.sdvr`, or the open dialog): the whole
  27 MHz is there, tunable across ±12.45 MHz of the centre (87.55 to 112.45
  MHz for 100 MHz). Reception was good (SNR up to 20-24 dB, the stereo pilot
  locked, RDS with full station names) at a **reference level of -20 to
  -30 dBm**; at 0 dBm the same stations were about 10 dB lower in SNR (2-10
  dB) and a 1 s test decoded no RDS (HD Radio was not tried at 0 dBm). Set it
  in Sceptre's IQ tab before recording.
- **Decode HD Radio offline** with nrsc5 for whole, unbroken programs:
  `digital-radio.md`, "Offline, from a Sceptre DVR". The app's loop restarts
  nrsc5 every 8.6 s, which costs the slower programs.
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
- **The IQ's dBm.** Linear, so one constant (ADC counts to volts, tied to the
  reference level); not measured, and not needed for FM, RDS or HD Radio,
  which use the shape of the signal. The app shows dBFS. To put a dBm axis on
  it, line the IQ's FFT up against Sceptre's own spectrum at the same
  resolution (good to a few dB), or record a test tone of known level.
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
