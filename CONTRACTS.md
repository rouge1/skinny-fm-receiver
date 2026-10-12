# Contracts for Phase 3: spectrum recordings played without sound

Files that are Sceptre's spectrum: a **sweep DVR** (`.sdvr` whose chunks are spectrum tiles: `SB`) and a
**spectrum `.fft`** (BLUE type 2001, format `SF`: float32 dBm, a row a sweep). Read-only; synthetic in tests.

## `tools/fm_receiver/spectra.py` (new)

```python
class SpectraError(Exception): ...

class SpectrumFile:
    path: str
    kind: str            # 'dvr-sweep' | 'fft'
    start: float | None  # seconds since 1970 of the first sweep
    rows: int            # sweeps in the file (a DVR: the newest unbroken stretch of tiles, sceptre_dvr.scan_sweeps)
    row_seconds: float   # between sweeps
    seconds: float       # rows * row_seconds
    bins: int
    start_hz: float      # of bin 0
    bin_hz: float
    stop_hz: float       # of the last bin: start_hz + (bins - 1) * bin_hz

    def read(self, t0=0.0, t1=None, f_lo=None, f_hi=None, max_rows=None, max_cols=None):
        """-> (times, freqs, db): times (n,) seconds from the file's start (row k is k * row_seconds), freqs
        (m,) Hz, db (n, m) float32 dBm.
        Rows: those k with t0 <= k * row_seconds < t1 (t1 None: to the end). Empty, not an error, outside the
        file. max_rows: if there are more, an even thinning (the first row kept).
        Bins: from the bin f_lo falls in to the one f_hi falls in (None: the whole row), then at most max_cols
        columns, each the *loudest* of the bins it covers (a narrow carrier must not vanish); freqs is each
        column's mean frequency.
        A sweep DVR's dBm comes from each tile's own gain and offset (sceptre_dvr.Sweeps.read(dbm=True)), and a
        stretch reads only the tiles it touches."""

def open_spectra(path) -> SpectrumFile
    # extension picks the kind. SpectraError for: a missing file, anything that is not a spectrum file, a DVR with
    # no tiles (IQ only), a BLUE file that is not 2-D float32 (SF), another extension.
```

## `tools/fm_receiver/library.py`

- `EXTERNAL_KINDS` gains `'.fft': 'fft'`; a `.sdvr` may now give a spectrum track. New track kinds `'dvr-sweep'`
  and `'fft'`; `Track.is_spectrum` is True for them (and `is_iq` False). Track fields: `seconds` (the file's),
  `start`, `center_hz` (the band's middle), `rate` None, `error`, `layout` the `SpectrumFile`'s facts (or the
  object), `external` True.
- A `.sdvr` with IQ stays an IQ track exactly as now. It also gets a `'dvr-sweep'` track when its tiles are a
  *sweep's*: it has no IQ, or the tiles' span (`bins * bin_hz`) is more than 1.5 times the IQ rate (an IQ DVR's own
  tiles cover only its IQ band and are not a second recording). Both tracks belong to one Recording.
- `GROUPS` gains a fourth, last title `'Sceptre spectrum'` (Recordings whose only track is spectrum). A Recording
  with both tracks is in `'Sceptre IQ DVR'`. `Recording.kinds_text()`: `'Sceptre sweep DVR'`, `'Sceptre FFT'`.
- `extent(track)` is `(start_hz, stop_hz)`. `render(track, t0, t1, rows, cols=None, ...)` for a spectrum track:
  row r is the sweep nearest the middle of slot r of `[t0, t1)`, NaN where that is outside `[0, seconds)`, in dBm;
  `cols` None is the file's own bins (thinned by max to at most 16384); pooling is by max. Returns the freqs of
  the columns, as for IQ.
- Listing reads headers only and caches them like the IQ ones (a 1 GB ring is not read to list it).
- Never written to, never deleted (`delete` refuses, as for the other Sceptre files).

## `tools/fm_receiver/app.py` (and `control.py`)

- Choosing a spectrum track draws it (waterfall from the file, the mini map) like any other.
- Play on one: **no engine and no radio**. `engine.running` stays False and `engine.radio` None. A timer
  (about 25 Hz) moves the playhead in real time from where it is; `_playhead()` reads it; rows of the waterfall and
  the trace come from the file at the playhead, in dBm (`level_unit` 'dBm'); the audio box does nothing.
- Pause holds; `_seek_to` jumps (paused or playing); at the end it stops (Play, playhead on the end) unless Loop
  is on, when it starts again from 0. Leaving the tab or choosing another recording stops it like any playback.
- `fmctl status` / `playback` reports it like the others (recording, file, playing, position, duration).
