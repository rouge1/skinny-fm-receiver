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
  `band=(f_lo_hz, f_hi_hz)` is absolute: analysis is restricted to it (brick-wall in the frequency domain, then
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
         the floor, at least 3 bins apart, and within 60 dB of the strongest (a window's sidelobes are not tones)
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
