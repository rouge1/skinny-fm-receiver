Task: add `IQSlice` and `extract` to tools/fm_receiver/iqmeasure.py (phase m6), with tests.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (last section "Decoding with rtl_433"), tools/fm_receiver/iqmeasure.py (especially
`open_iq`, `_band_offsets`, `_banded`, `_decimated`, `measure_file`, `CHUNK`), and tools/tests/test_iq_measure.py.

Edit only tools/fm_receiver/iqmeasure.py and tools/tests/test_iq_measure.py (APPEND new checks and add them to
GROUPS; change nothing existing).

Behaviour (contract):
```python
class IQSlice(NamedTuple): iq: np.ndarray; rate: float; center_hz: float; t0: float; t1: float
def extract(path, t0=None, t1=None, band=None, max_rate=1e6, rate=None, center_hz=None) -> IQSlice
```
- open with `open_iq(path, rate=rate, center_hz=center_hz)`; the span t0..t1 in seconds as `measure_file` clips it
  (but default t1 is the end of the file, not 30 s; the output limit below applies).
- `band=(f_lo, f_hi)` absolute Hz. None: the whole file when its rate is at most `max_rate`, else +/- 250 kHz about
  the file's centre. Use `_band_offsets` to clip, `_decimated`/`_banded` to filter, mix and decimate, reading in
  chunks (never one array of the whole span at the input rate: the m5 rule; at most 2**22 samples per read).
- Output: the band centre moved to 0 Hz, band-limited, decimated to a rate between 2x the band and `max_rate`
  (when the file's rate is already <= max_rate and the band is the whole file: no filtering needed, the file's own
  rate; pass samples through as complex64). `center_hz` = the band's absolute centre; `rate` = the real output
  rate (src.rate / dec); `t0`, `t1` the span actually extracted, seconds.
- Do not read the span as one block even when the file is at the low rate: use `src.read` in chunks and
  concatenate the OUTPUT.
- More than 50 million output samples: raise IQSourceError saying to shorten the span or narrow the band (check
  before reading). A span of fewer than MIN_SAMPLES samples is an IQSourceError like measure_file's.
- Sceptre sweep files and anything open_iq refuses stay IQSourceError (just let open_iq's error through).

Tests to append (each must fail before the code exists; synthetic data in the test's temp folder):
1. A 2 MS/s .cfile (0.2 s, with sigmf-meta, centre 100 MHz) holding a tone at +200 kHz and a second tone at
   -600 kHz: extract(band=(100.15e6, 100.25e6), max_rate=250000): center_hz == 100.2e6 (within 1 Hz); rate between
   2x band and 250000; the tone is at about 0 Hz in the output (within 200 Hz) and the other tone is >= 40 dB down.
2. No band, file rate 2 MS/s, max_rate 1e6: center_hz equals the file's centre, the output rate <= 1e6 and spans
   +/- 250 kHz: a tone at +100 kHz appears at +100 kHz in the output (within 200 Hz); one at +400 kHz is >= 40 dB
   down.
3. A file at 250 kS/s with max_rate 1e6 and no band: rate == 250000, length equals the file's samples, and
   the samples equal the file's (allclose).
4. t0/t1: extract(t0=0.05, t1=0.1) of a 2 MS/s file returns t0, t1 as given (within 1 sample) and the length is
   about (t1 - t0) * out rate.
5. A counting source (wrap `iqmeasure.open_iq`): no `read` larger than 2**22 samples for a 2 MS/s 0.5 s file with
   a narrow band, and the total samples requested is at most 1.1 x the span.
6. More than 50 million output samples raises IQSourceError: use max_rate=1e9 on a 60 MS/s... make it cheap: monkeypatch
   open_iq to return an IQSource whose `samples` is huge (e.g. 3e8 at 1 MS/s) and whose fetch would raise if
   called; extract must raise IQSourceError before reading.
7. A file with a rate argument: a bare .cfile with `rate=` and `center_hz=` works.

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py
All existing checks must still pass (139 before yours). Numpy only; no whole-span arrays at the input rate.
End with a short summary: files changed, test result.
