You are the CONSENSUS reviewer on FM Receiver (PyQt5 + numpy app). Read-only: do not edit, create or delete files,
do not run git commands that change state, and do not list or read anything outside the working directory. You may
run short probes (python: /home/user/miniconda3/envs/gnu/bin/python, PYTHONPATH=tools, QT_QPA_PLATFORM=offscreen).

Three models implemented the new file tools/fm_receiver/spectra.py in a bake-off. Entries are on branches
p3/spectra-grokw, p3/spectra-musefree, p3/spectra-haiku; inspect each with
`git show p3/spectra-<m>:tools/fm_receiver/spectra.py` (it is the only file changed). All three pass
tools/tests/test_spectra.py. Spec: CONTRACTS.md (section spectra.py) and tools/swarm-records/specs/p3.md. Reviewers
sometimes misread, so VERIFY every claim against the code and the helpers (tools/fm_receiver/sceptre_dvr.py,
sceptre_blue.py) by a short probe where possible, and quote the file:line you checked. For claims that apply to
the shared helper rather than the entry (e.g. sceptre_dvr.Sweeps.bin_range), say whether each entry works around it.
Note which findings concern an entry's own behaviour only, by entry.

Score and pick the winner ONLY by the spec's pre-registered rubric and winner rule:
- Bake-off scoring rubric (0 to 100): correctness against the spec and the tests, and bugs a reviewer finds that
  the tests miss, 50; spec compliance including the only-what-it-needs reading and no writes, 30; clarity and fit
  with the surrounding code, 20.
- Winner rule: the highest score; a tie goes to the entry with fewer failing review items, then the shorter diff.
Do not add your own criteria.

REVIEWS (file author in the heading, reviewer after "by"):
=== A-grokw-by-haiku (entry = first name)
verdict: changes
issues:
- [medium] tools/fm_receiver/spectra.py:135-138 `_load` assumes every tile yields `per` rows, but `Sweeps.read` skips a tile Sceptre overwrote, so `read` returns `times` longer than `db` (I got 128 times vs 112 rows) and rows shift. Fix: read each tile separately, and where a tile is gone fill its rows with NaN (or drop them from `times` too).
- [medium] tools/fm_receiver/spectra.py:156-170 and 141-153 a truncated file opens silently with fewer rows (a 5000-byte cut of a .fft gave 0 rows, no error; a half-cut .sdvr gave 32 tiles). The spec says a truncated file raises SpectraError. Fix: compare the file's declared data size (BLUE `data_size`/`data_start`, DVR last chunk end) with the file size and raise SpectraError when it is cut short.
- [medium] tools/fm_receiver/spectra.py:133-138 each stretch converts the whole tile width to float32 before the band slice, so a narrow band still reads and converts all bins (about 4x the tile's size in temporary memory). Fix: pass the band to `Sweeps.read` (`f_lo`/`f_hi` from `i0`/`i1`) so only those bins are read.
- [low] tools/fm_receiver/spectra.py:28-29 the 1e-9 tolerance on the ratio drops a sweep that is up to 1e-9 interval past `t1` (`read(0, 3*row_seconds*(1+1e-10))` returns 3 rows, not 4). Fix: tighten the tolerance (about 1e-12) or document it as deliberate.
- [low] tools/fm_receiver/spectra.py:82-103 `SpectrumFile` has no `close()`, so the 1 GB memmap stays mapped until garbage collection. Fix: add `close()` that drops `_sweeps`/`_data` and call it from the library when it drops a track.
=== A-grokw-by-musefree (entry = first name)
verdict: changes
issues:
- [high] tools/fm_receiver/spectra.py:136 _load reads touched tiles at full width with dbm=True then slices [:, i0:i1], materializing ~1.2M-bin tiles as float32; pass the band to Sweeps.read so only needed bins are copied/converted
- [medium] tools/fm_receiver/spectra.py:42 bin_of rounds to nearest (floor(x+0.5)) so an off-centre f_lo/f_hi picks the wrong bin; use floor covering like Sweeps.bin_range
- [low] tools/fm_receiver/spectra.py:162 accepts any BLUE type >=2000 with 2-D float32, but CONTRACTS.md specifies type 2001; require type == 2001
- [low] tools/fm_receiver/spectra.py:76 column frequencies via cumsum difference suffer cancellation at GHz start_hz; use start_hz + mean-index * bin_hz

=== A-haiku-by-grokw (entry = first name)
I'll review the spectrum reader against the contract and tests, without changing any files.The row-bound and bin-span math is the risky part; I'll probe those edges against the contract.verdict: changes
issues:
- [medium] tools/fm_receiver/spectra.py:95 A frequency span outside the file (or f_lo > f_hi) takes the hi <= lo path and returns no rows; keep the selected ks and an (n, 0) float32 block, as the .fft path does.
- [medium] tools/fm_receiver/spectra.py:63 max_cols=0 is falsy, so the column cap is skipped and the full band is returned. Treat 0 as a real limit and return (n, 0) without calling _pool (arange(0) // 0).
=== A-haiku-by-musefree (entry = first name)
verdict: changes
issues:
- [medium] tools/fm_receiver/spectra.py:109 empty band on a DVR drops the rows (returns (0, 0)) while .fft keeps them ((n, 0)); return ks with shape (len(ks), 0) instead
- [low] tools/fm_receiver/spectra.py:63 `if max_cols` treats max_cols=0 as no limit and returns full width; test `is not None` so 0 gives 0 columns

=== A-musefree-by-grokw (entry = first name)
I'll review `spectra.py` against the spec, tests, and the reader contracts, without changing any files.verdict: changes
issues:
- [medium] tools/fm_receiver/spectra.py:38 A band that misses the file raises SpectraError (the DVR path does the same at line 127 by wrapping SdvrError). Return an empty float32 result, as a time range outside the file already does.
- [low] tools/fm_receiver/spectra.py:48 max_rows or max_cols of 0 raises SpectraError. Return 0 rows or 0 columns; 1 already keeps the first row, or one column that is the max and the mean frequency.
- [medium] tools/fm_receiver/spectra.py:146 A full-band .fft read hands back a read-only memmap (np.asarray does not copy), so in-place edits fail and the mapping stays tied to the result. Copy into an owned float32 array.
=== A-musefree-by-haiku (entry = first name)
verdict: changes
issues:
- [high] tools/fm_receiver/spectra.py:35-36 `_band` takes floor/ceil of a float bin position with no tolerance, so a frequency that is exactly a bin centre often gets two bins (a probe on a 12345.678 Hz grid got 10064 of 20000 single-bin bands wrong). Use `floor(x + 1e-9)` and `ceil(x - 1e-9) + 1`. The DVR path hands f_lo/f_hi to `sceptre_dvr.Sweeps.bin_range`, which has the same bug, so spectra must compensate there (e.g. pass bin-midpoint frequencies). The test passes only because its grid happens to round exactly.
- [medium] tools/fm_receiver/spectra.py:131-134 and 165-166 assume a sweep DVR's tiles are uniformly spaced. `sceptre_dvr._streak` keeps tiles across gaps up to `max(1 s, 2*tile seconds + 0.5)`, so a gap inside the streak shifts every later row: row k = tile*per + sweep, and `times` and `seconds` (rows * row_seconds) ignore it. Fix: always map rows to k from the absolute tile times (as the else branch at 138-141 does), and set `seconds` from the streak's end minus start.
- [low] tools/fm_receiver/spectra.py:81-92 and 168-193 never close the memmaps (`Sweeps`' `_raw`, `Blue.data`), and there is no `close()`. A library that caches many SpectrumFiles keeps every file mapped. Add a `close()` and call it when the library drops a track.
- [low] tools/fm_receiver/spectra.py:160-170 a tile whose gain is 0 (from `sceptre_dvr.Sweeps.read(dbm=True)`) gives inf/NaN dBm with a numpy warning and no error. Reject a non-positive gain in `_from_dvr` or in `_read_dvr`.

Rules: merge duplicates. Reject claims that are false, that ask to pin details the contract leaves open, or that are
out of scope. Keep severity honest.

Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
scores:
- <model>: <0-100> - <one line why>
winner: <model>
fix list per entry (for each author):
<model>:
- [high|medium|low] <file:line> <what to change> (source: reviewer names)
rejected:
- <one line: the claim and why it's wrong> (source)
