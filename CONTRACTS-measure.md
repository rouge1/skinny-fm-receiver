# Contracts: measuring a signal from a file (the measurement tools, phases m1 to m3)

A tool takes a recording of IQ and says what the signal *is*, as numbers and a class, before any decoder is
written (`knowledge/workflow.md` steps 3 and 4; roadmap Round 22, "classify, don't decode"). Read-only on every
input. Synthetic signals in tests: no captured data in this public repo.

## `tools/fm_receiver/iqmeasure.py` (numpy only; no gnuradio, Qt, scipy, `library`, `radios`, `dsp`)

```python
class IQSourceError(Exception): ...

class IQSource:
    path: str; kind: str            # 'cfile' | 'sdvr' | 'cdif'
    rate: float                     # samples/s
    center_hz: float                # the band's centre, absolute
    samples: int; seconds: float
    full_scale_dbm: float | None    # None when the file carries no level calibration
    def read(self, start: int, count: int) -> np.ndarray   # complex64, clipped to the file, never writes

def open_iq(path, rate=None, center_hz=None) -> IQSource
```
- `.cfile`: cf32 interleaved; rate and centre from the `.sigmf-meta` beside it (`core:sample_rate`,
  `captures[0]["core:frequency"]`); a bare `.cfile` needs `rate=` (and optionally `center_hz=`), else
  `IQSourceError`. `full_scale_dbm` is None.
- `.sdvr`: via `sceptre_dvr.scan` + `sceptre_dvr.Reader` (the IQ of an IQ DVR; a sweep DVR raises
  `IQSourceError`); `full_scale_dbm` from the DVR (`dvr_iq.Dvr.full_scale_dbm` shows how).
- `.cdif`: via `sceptre_blue`; a `.fft` (spectrum) raises `IQSourceError`.
- Anything else, a missing file, or a file too short to measure (< 1024 samples): `IQSourceError`.

```python
def measure(iq, rate, center_hz=0.0, full_scale_dbm=None, t0=0.0, band=None) -> dict   # the card
def measure_file(path, t0=None, t1=None, band=None, rate=None, center_hz=None) -> dict
def card_path(path) -> str | None          # '<base>.card.json' beside a .cfile, else None
def write_card(path, card) -> str         # writes card_path(path); IQSourceError for .sdvr/.cdif/.fft
```
- `iq` is a complex array; `t0`/`t1` are seconds into the file (default: all, at most the first 30 s);
  `band=(f_lo_hz, f_hi_hz)` is absolute: analysis is restricted to it (band-limited in the frequency domain with a four-bin raised-cosine skirt (m7), then
  decimated to at least twice its width).
- `write_card` never writes beside a `.sdvr`, `.cdif` or `.fft` (the app does not write there), and never
  overwrites a file that is not a card (`schema` key).

### The card (JSON-safe; `None` where not measurable; floats rounded to 6 significant digits)
```
schema: 1
source:  path, kind, rate_sps, center_hz, t0_s, t1_s, samples
level:   power_dbfs, power_dbm (None without full_scale_dbm), noise_floor_dbfs, snr_db
occupied: bandwidth_hz (99 % of the power), bandwidth_20db_hz (width at 20 dB below the peak),
          centre_hz (absolute, the power centroid)
tones:   [{freq_hz (absolute), level_db (above the floor)}], strongest first, at most 8, each > 15 dB above
         the floor, at least 8 bins apart (two peaks closer than that are one tone), and within 60 dB of the strongest (a window's sidelobes are not tones)
bursts:  count, durations_s (first 32), median_duration_s, gaps_s (first 32), repeat_interval_s (median start
         to start; None under 3 bursts), duty (0..1)
envelope: levels (1 or 2 amplitude levels, low first), depth (0..1), keyed (bool)
frequency: levels_hz (offsets from the centre, 1 or 2, low first), deviation_hz, keyed (bool)
symbol_rate_baud: float | None
class:   'noise' | 'carrier' | 'ook' | 'ask' | 'fsk' | 'fm' | 'am' | 'unknown'
evidence: [str, ...]        # one short sentence per rule that fired
```

### Where the envelope and frequency are measured
On the signal, not the whole span: the IQ is shifted to the occupied centre and band-limited (FFT-domain)
to max(3 x `bandwidth_hz`, 20 kHz) before the envelope, the instantaneous frequency, the bursts and the
symbol rate are taken, then decimated. A single tone in a 28 MHz-wide recording therefore shows its own
frequency steadiness, not the noise of the whole band.

### The class rules (deterministic, applied in this order)
1. `noise`: `snr_db` < 6.
2. `ook`: the envelope is keyed with two levels and the low level is below 10 % of the high one (< -20 dB).
3. `ask`: the envelope is keyed with two levels and the low level is at least 10 % of the high one.
4. `fsk`: the instantaneous frequency is keyed (two levels more than 3 within-level standard deviations
   apart) and the envelope is constant (coefficient of variation < 0.1).
5. `fm`: continuous, the envelope constant (< 0.1), the frequency's standard deviation >= 500 Hz.
6. `am`: continuous, the envelope varying (coefficient of variation >= 0.1), the frequency steady.
7. `carrier`: one tone, no modulation measured.
8. `unknown` otherwise (never guess).
"Keyed" means the values (taken inside bursts, or over the whole span when there are none) form two clusters
that a threshold separates cleanly.

## fmctl additions (`tools/fm_receiver/control.py`)
| command | does | refuses |
|---|---|---|
| `filter KHZ` | the channel filter knob (Receive), clamped to its range; replies the value set | outside Receive |
| `step KHZ` | the Step knob; one of its values | a value it does not have (error lists them) |
| `radio KIND` | the radio chooser | an unknown kind (error lists them) |
| `start` / `stop` | the Run button | while recording |
| `open PATH` | load a file as the Open IQ dialog does | while recording; a missing or unreadable file |
| `delete N confirm` | delete the Nth recording of the list (`library.delete`, the app's own only) | without the word `confirm`; external or Sceptre files (as `library.delete` does) |
| `recordings-folder PATH` | the app's recordings folder, through `config.update_config`; the list is re-read | a path that is not a directory |
| `marker FREQ` | read-only: the level at FREQ (MHz) on the RF trace (live or peak hold), in that view's unit | a frequency off the shown band |
| `measure [SECONDS] [card]` | (phase m2) `iq-measure` on the last capture, in a subprocess with a 60 s limit, replies the card; `card` also writes it | no capture yet |
| `status` | gains `sweep: {start_mhz, stop_mhz}`, `file` (the file loaded or None), `level_unit` | |

Each reply is JSON like the existing commands; errors are the existing error shape.

## Tools (phase m2)
- `tools/iq-measure FILE [--t0 S --t1 S] [--band LO HI] [--rate R --center HZ] [--card]`: prints the card
  (JSON, stdout); `--card` also writes it by `write_card`; exits 1 on `IQSourceError`, with the message on
  stderr. Plain Python, numpy only, runs anywhere (like `tools/dvr-sweep`).
- `tools/iq-picture FILE OUT.png [--t0 S --t1 S] [--band LO HI] [--rate R --center HZ] [--nfft N]`: a spectrum
  panel (mean and peak) over a waterfall of the span, matplotlib `Agg`, dBm when the file has a calibration,
  else dBFS; the title names file, centre, rate and span. Imports no gnuradio, Qt, `library` or `radios`.

## Cost of a measurement (m5)
`measure_file` reads the span in chunks and band-limits by mixing and decimating chunk by chunk: it never holds
more than a few million complex samples at once, whatever the file's rate or length. A real 28 MS/s, 8.6 s
IQ DVR (240 million samples) measures in under 25 s and under 600 MB of extra memory.

## Decoding with rtl_433 (phase m6)
Workflow step 7 hands a measured signal to rtl_433, the referee, without a decoder of ours.

```python
# iqmeasure.py
class IQSlice(NamedTuple): iq: np.ndarray; rate: float; center_hz: float; t0: float; t1: float
def extract(path, t0=None, t1=None, band=None, max_rate=1e6, rate=None, center_hz=None) -> IQSlice
```
- `band=(f_lo_hz, f_hi_hz)` absolute; None means the whole file when its rate is at most `max_rate`, else
  +/- 250 kHz about the file's centre. The result is mixed so the band centre is 0 Hz, band-limited and
  decimated to a rate between 2x the band and `max_rate` (the file's own rate when it is already low), read in
  chunks (no whole-span array: the m5 rule), `center_hz` being the band's centre. At most 50 million output
  samples; more is an `IQSourceError` that says to shorten the span or narrow the band.

`tools/iq-decode FILE [--t0 S --t1 S] [--band LO HI] [--rate R --center HZ] [-R N ...] [--out-rate R]
[--keep PATH] [--timeout S]`: `extract`, then scale so the 99.9th percentile of |x| is 0.5 (the gain is
reported: a capture's level is far below rtl_433's detection floor), write a cf32 file in a temp directory
(deleted unless `--keep`), run `rtl_433 -r cf32:FILE -s RATE -f CENTER -F json -M level [-R N ...]` with
**stdout redirected to a file** (a pipe lost decodes in testing), and print one JSON document:
```
file, center_hz, rate_sps, gain_db, rtl_433 (its version line), protocols ('default' or the -R list),
packets (int), decodes [first 50 JSON objects rtl_433 printed, as they were],
summary [{model, id (when present), count}]
```
Exit 0 whatever the decode count (0 is an answer); 1 on `IQSourceError`; 2 when `rtl_433` is not installed
(message names how to install it). `--out-rate` defaults to 250000 (what rtl_433's pulse detector expects)
and is capped by the extracted rate. Plain Python, numpy only (rtl_433 is a subprocess); runs on Linux and macOS.

Known unknown (to be investigated, not assumed): on one over-the-air capture rtl_433 decoded the Nexus frame
only with `-R 19`, and found nothing with its default protocol set, while a synthetic file decoded in both.

## Bursts, bits and frames (amendment, 2026-10-15)

Added after the Silver Spring mesh (`knowledge/silver-spring.md`). The contracts above are unchanged:
`iqmeasure` is not touched, `iq-decode`'s default path and its pinned tests are not changed, and the
new path is opt-in. Numpy only (no scipy, gnuradio, Qt, `library`, `radios`); tests use synthetic
signals and placeholder bytes only.

```python
# tools/fm_receiver/iqburst.py
find_bursts(source, t0=None, t1=None, nfft=None, over_db=12.0, min_s=150e-6, min_frames=2,
            max_bw_hz=5e6, max_run_share=0.5, edge_share=0.04, chunk_frames=4096,
            merge_gap_hz=300e3) -> [dict(t0, t1, f_lo, f_hi, centre_hz, bw_hz, snr_db)]
demod_fsk(iq, fs, rates=DEFAULT_RATES, min_preamble=16, margin_bits=16)
    -> None | dict(rate_baud, preamble_bits, eye, centre_offset_hz, deviation_hz, bits, candidates)
# tools/fm_receiver/iqframes.py
align(bit_frames) -> (starts, sync_bits, kept_indices)
structure(frames) -> dict(positions, length_fields, lengths)
search(frames, scramblers=None, crcs=None, starts=None, tail_slack=4, length_fields=(),
       min_hits=3, ...) -> dict(hits, tried, unaccepted, unique_frames)
lfsr_hints(bit_frames, starts) -> [dict(frame, bit_offset, complexity, window)]
analyse(bit_frames, variants, min_hits) -> dict
# tools/fm_receiver/iqcatalog.py
CRCS (71 entries, widths 8/16/24/32, each reproducing its reveng check value), SCRAMBLERS,
crc(), crc_update(), crc_final(), keystream(), descramble()
```

- `demod_fsk`'s bits are as sliced (positive frequency is 1): polarity and byte bit order are not
  resolved there; `iq-frames` tries both. The rate is the candidate with the longest alternating
  run (>= `min_preamble`), the eye only breaks a tie. The sample point follows zero crossings, so a
  clock +-1500 ppm off over 1,000 symbols is bit-exact. Bits sent while the power is more than about
  20 dB down are not promised.
- `search` counts a hit by *distinct checked regions* (identical bytes, or the same bytes under the
  same rule, are one observation), does not try a hypothesis whose chance of a hit per frame
  exceeds 5 %, reports `p_tail` (binomial) and `p_adjusted` (times the hypotheses tried), and lists
  only `p_adjusted` under 1e-3. A free per-frame seed over every non-zero state makes the keystream
  start inseparable from the CRC start, so it is reported once.
- `knowledge/catalogs.md` carries the catalog as two JSON blocks; `test_iq_catalog.py` fails when
  they differ from `iqcatalog.py`, or when a CRC misses its check value.
- `tools/iq-demod FILE [--t0 S --t1 S] [--band LO HI] [--rate R --center HZ] [--rates LIST]
  [--over-db DB] [--min-snr DB] [--min-preamble N] [--max-bursts N] [--no-bits] [--frames-out PATH]`:
  one JSON document (`bursts`, each with its span, `rate_baud`, `preamble_bits`, `eye`, `bits`),
  exit 1 on a bad option or a file that cannot be read.
- `tools/iq-frames FRAMES.json | --hex FILE [--variants LIST] [--min-hits N] [--tail-slack N]
  [--no-lfsr]`: one JSON document, exit 1 on a bad option, unreadable input or under `--min-hits`
  frames.
- `tools/iq-decode FILE --bursts [-R N ...] [--max-bursts N]`: each burst of the span found,
  cut to 80 % of the decoder's sample rate in band, scaled by itself (99.9th percentile of |x| to
  0.5), padded with 4 ms of silence and written in turn at the rate `rtl_433 -R help` gives for
  protocol N as `(-s RATE)` (else `--out-rate`, default 250000). The output gains `bursts`. The
  tool makes no claim that a decoder emits a packet.
