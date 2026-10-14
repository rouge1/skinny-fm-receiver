Task: write the tool `tools/iq-decode` (phase m6). The tests exist: tools/tests/test_iq_decode.py (read it; make it pass).

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (last section "Decoding with rtl_433"), tools/swarm-records/specs/m6.md ("Behaviour"),
tools/iq-measure (the pattern: argparse, `_refuse`, sys.path insert, error to stderr), `extract` and `IQSlice` in
tools/fm_receiver/iqmeasure.py, and tools/tests/test_iq_decode.py.

Edit only the NEW file tools/iq-decode (executable, chmod +x). Plain Python, numpy only; rtl_433 is a subprocess
found by shutil.which (absent: exit 2, message says how to install: `apt install rtl-433` / `brew install rtl_433`).

Behaviour: as in the contract. Notes:
- extract(path, t0, t1, band, max_rate=<default 1e6>, rate, center_hz). Then scale so the 99.9th percentile of |x| is
  0.5 (guard a zero or non-finite result: exit 0 with packets 0 and gain_db null), report gain_db = 20*log10(scale).
- Resample to --out-rate (default 250000, capped by the extracted rate) when the extracted rate is above it: block
  average / integer decimation with a simple FIR is fine, in chunks, no whole-file arrays beyond the output (50 M
  max). If the extracted rate is not an integer multiple, use the nearest integer decimation and report the rate
  that results in rate_sps.
- Write cf32 (complex64 interleaved little-endian) to a file in tempfile.mkdtemp() (honours TMPDIR), run
  `rtl_433 -r cf32:FILE -s RATE -f CENTER -F json -M level [-R N ...]` with stdout=an open FILE handle (NOT a pipe)
  and stderr to another file (read it for the version line `rtl_433 version ...`), --timeout S (default 120)
  enforced via subprocess timeout; delete the temp dir unless --keep PATH (then the cf32 file goes to PATH).
- Parse stdout lines as JSON, ignore lines that do not parse; `decodes` first 50; `summary` by (model, id) with
  count; `packets` = number of JSON lines that have a "model" key.
- -R accepts repeated values; `protocols` is 'default' or the list of ints.
- Exit 0 whatever the decode count; 1 on IQSourceError or bad options (message to stderr, nothing on stdout); 2 when
  rtl_433 is missing.
- Runtime: 5 s of a 2 MS/s file in under 10 s.

INVESTIGATION (part of the task): write in the docstring ONE paragraph, measured, or "not reproduced": why an
over-the-air capture decoded with `-R 19` but not with rtl_433's default protocol set. Do NOT guess. The synthetic
frame decodes either way; try to reproduce on synthetic variants and record the numbers: frame built as in the test
(Nexus-TH, 36 bits, 12 rows) at various levels (the contract's scaling), noise sigma 0.003 to 0.03 per component,
DC offset, a 1/2/3 row burst, a frequency offset of 30 to 100 kHz at 250 kS/s, rates 250k/1M/2M, with `-R 19`
versus no -R; also `rtl_433 -R help` / `-G` to see whether protocol 19 is in the default set of this build
(23.11), and run rtl_433 with `-v` on a case that decodes only with -R 19, if you find one, to see which protocols
claimed the pulses. Write the table of what decoded, as one paragraph with the numbers, and the tool's default
stays the default set unless you find a case where -R 19 is strictly better (then say so in the paragraph; do not
change the default).

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py
Also run test_iq_measure.py and test_iq_tools.py. If you think a test is wrong, say so in your summary: do not edit
it. End with a short summary: what you did, test results, and the investigation's conclusion.
