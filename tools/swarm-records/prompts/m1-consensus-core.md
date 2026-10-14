You are the CONSENSUS reviewer on the FM receiver. Read-only: do not edit, create or delete files in the working
directory, do not run git commands that change state, and do not list or read anything outside the working
directory. You may run the tests and short probes (to probe an entry, write its file to the system temp dir with
`git show <branch>:tools/fm_receiver/iqmeasure.py`, never into the worktree).

Three models each implemented the measurement core (tools/fm_receiver/iqmeasure.py, contract
CONTRACTS-measure.md, acceptance test tools/tests/test_iq_measure.py) on branches m1/core-grokw,
m1/core-musefree and m1/core-haiku; inspect each with `git diff main m1/core-<model> -- tools/fm_receiver/iqmeasure.py`.
All three pass the acceptance test (114 of 114). Reviewers sometimes misread code, so VERIFY every claim against
the code (or a probe) before keeping it. Quote the file:line you checked. Reject claims that are false, that ask
to pin details the contract leaves open, or that are out of scope. Keep severity honest. The "30 s of 1 MS/s must
take under 5 s and under 1 GB extra memory" target is in the spec: a confirmed breach is [high].

Score and pick the winner ONLY by the spec's pre-registered rubric and winner rule, pasted here:
- Bake-off scoring rubric (0 to 100, core only): correctness against the contract and the tests, and bugs a
  reviewer finds that the tests miss, 50; contract and class-rule compliance, including never writing and the
  refusals, 30; clarity and fit with the surrounding code, 20.
- Winner rule: the highest score; a tie goes to the entry with fewer failing review items, then the shorter diff.
Do not add your own criteria.

REVIEWS:
--- grokw-by-haiku
verdict: changes
issues:
- [high] tools/fm_receiver/iqmeasure.py:131-146 `measure_file` with `t0` past the end or `t1 < t0` gives an empty slice, and `_welch` (line 781) raises `OverflowError`, not `IQSourceError`. Check the clipped span against `MIN_SAMPLES` and raise `IQSourceError` before measuring.
- [high] tools/fm_receiver/iqmeasure.py:142 and 834-844 memory: a 30 s, 1 MS/s `measure_file` peaks at about 1.2 GB traced (about 1.4 GB RSS over baseline), over the 1 GB limit. The whole span is read at once and `_boxcar` makes float64 cumsums over all of it. Process magnitude, instantaneous frequency and boxcar smoothing in chunks, and avoid whole-span float64 temporaries. Time (3.3 s) is fine.
- [medium] tools/fm_receiver/iqmeasure.py:103-107 `measure` accepts arrays under 1,024 samples (100 samples gives a card; 1,023 and span checks likewise), and an empty array raises `OverflowError`. Raise `IQSourceError` below `MIN_SAMPLES`.
- [medium] tools/fm_receiver/iqmeasure.py:103 NaN or inf input is measured: an all-NaN array and a noise array with one NaN both return class `am`. Refuse non-finite input with `IQSourceError`.
- [low] tools/fm_receiver/iqmeasure.py:198 and 221-223 a NaN rate is accepted (`open_iq(rate=nan)`, or a `.sigmf-meta` with NaN), because `rate <= 0` misses NaN. Use `not rate > 0`. A non-numeric `rate` also escapes as `ValueError` at line 198 instead of `IQSourceError`.
--- grokw-by-musefree
verdict: changes
issues:
- [high] tools/fm_receiver/iqmeasure.py:141 empty span (t0 past EOF, t1<=t0) is allowed through and dies in _welch with OverflowError; fix: raise IQSourceError for an empty span
- [high] tools/fm_receiver/iqmeasure.py:142 the whole span is read in one slice, 30 s at 1 MS/s costs ~1.35 GB extra; fix: stream the analysis in chunks
- [medium] tools/fm_receiver/iqmeasure.py:291 NaN power/floor leaves snr as NaN so NaN input skips the noise rule and reports am with snr_db None; fix: map non-finite snr to None before classify
- [medium] tools/fm_receiver/iqmeasure.py:104 measure() float(rate/center/t0) raises TypeError/ValueError on None/junk; fix: validate and raise IQSourceError
- [medium] tools/fm_receiver/iqmeasure.py:198 bare .cfile rate/center_hz unvalidated (rate=0/-1 accepted, junk raises ValueError); fix: require positive finite rate else IQSourceError
- [medium] tools/fm_receiver/iqmeasure.py:751 band indexing raises ValueError/TypeError/IndexError and a 3-tuple is silently accepted; fix: require a finite (lo,hi) pair else IQSourceError

--- haiku-by-grokw
I'll review `iqmeasure.py` against the contract and tests, then probe edge cases the tests may miss.The contract and the reader wiring look consistent so far. Next I’ll probe the edge cases and the 30 s measurement cost.verdict: changes
issues:
- [medium] tools/fm_receiver/iqmeasure.py:444 A band narrower than rate/(2*CHUNK) sets dec > CHUNK, so step is 0 and range() raises ValueError. Cap the hop so each step is a positive multiple of dec (or cap dec at CHUNK).
- [medium] tools/fm_receiver/iqmeasure.py:486 NaN or non-finite IQ makes the noise-floor ratio NaN and math.log10 raises ValueError. Reject non-finite input with IQSourceError, or emit a card whose bad levels are None, before the log.
- [medium] tools/fm_receiver/iqmeasure.py:432 An inverted band, a band outside the recording, or a band that decimates to fewer than 64 samples raises ValueError from measure_file (also line 475). Raise IQSourceError so those refusals match the other source failures.
- [low] tools/fm_receiver/iqmeasure.py:259 TONE_GAP is 8, so two local peaks 3–7 bins apart are dropped. The contract minimum is 3 bins. Keep the local-maximum test and separate tones by 3 bins.
--- haiku-by-musefree
verdict: changes
issues:
- [high] tools/fm_receiver/iqmeasure.py:274 all-zero input yields occupied.bandwidth_hz negative (~-1e6) with a divide-by-zero warning instead of >=0/None; guard total<=0 in _occupied.
- [high] tools/fm_receiver/iqmeasure.py:468 a band containing no signal reports snr ~85 dB with spurious band-edge tones and class unknown instead of noise; fix floor estimation and rectangular-mask edge artifacts on the banded path.
- [medium] tools/fm_receiver/iqmeasure.py:432 bad band (inverted/outside) and too-few-samples after decimation raise ValueError from measure_file instead of IQSourceError; wrap them as IQSourceError so the CLI exits 1 cleanly.
- [low] tools/fm_receiver/iqmeasure.py:45 TONE_GAP=8 enforces 8-bin separation while the spec/docstring require at least 3 bins apart, merging tones 3-7 bins apart; use 3 and narrow the 9-bin power window accordingly.

--- musefree-by-grokw
I'll review `iqmeasure.py` against the contract and the acceptance test, then probe edge cases the tests may miss.The contract and the implementation are in hand. Next I’ll probe edge cases and the 30 s memory bound.verdict: changes
issues:
- [high] tools/fm_receiver/iqmeasure.py:528 `measure_file` keeps every read chunk alive (line 737) and mean-squares the whole span as complex128, so 30e6 samples peak near 1.4 GB. Free the chunks after concatenating and compute power from complex64 in pieces.
- [medium] tools/fm_receiver/iqmeasure.py:653 Frequency keying is an Otsu ratio of 0.9, which needs the levels about 6 within-level standard deviations apart, so a shift of just over 3 is not keyed and is classed as FM. Key frequency when the two cluster means differ by more than 3 within-level standard deviations.
--- musefree-by-haiku
verdict: changes
issues:
- [high] tools/fm_receiver/iqmeasure.py:530-531 Band-restricted floor is wrong: the brick-wall leaves half the decimated spectrum empty, so the median is about -143 dBFS. Pure in-band noise then reads SNR 103 dB and a fake tone at 100.219 MHz. Fix: take the floor from the in-band bins only.
- [high] tools/fm_receiver/iqmeasure.py:600 With `band`, FSK levels are offsets from the band's centre, not the card centre (`eff_center` is shifted). A 100 kHz FSK in band (100.05–100.15 MHz) gives levels -4.7/+4.7 kHz instead of 95/105 kHz. Fix: drop the `- (eff_center - center)` shift.
- [high] tools/fm_receiver/iqmeasure.py:528 Memory: a 30 s complex64 `measure_file` peaks at about 1.17 GB above baseline, over the 1 GB limit. The complex128 copy and the `parts` list are the main cost. Fix: compute power in chunks and drop `parts` after the concatenate. With `band`, the full-span complex128 FFT peaks at about 2.7 GB; band filtering needs chunking or a bounded span.
- [high] tools/fm_receiver/iqmeasure.py:39-41 Tone listing uses 20 dB, but the contract says each tone > 15 dB above the floor. Tones between 15 and 20 dB are dropped. Fix: use 15 dB and handle the test's weak tone another way, or record the deviation.
- [medium] tools/fm_receiver/iqmeasure.py:398 `measure` on a band with no energy in it (or a band near the centre with a CW) raises `ValueError: Too many bins for data range` from `np.histogram`, not IQSourceError. Fix: guard `hi - lo` against float resolution in `_cluster_keying`.
- [medium] tools/fm_receiver/iqmeasure.py:242 `write_card` opens the target with 'w' before `json.dump`, so a NaN or non-serialisable card leaves a truncated or partial file (`e.card.json` was created empty). Fix: serialise to a string first, then write.
- [medium] tools/fm_receiver/iqmeasure.py:578-579 A clean noiseless CW (constant envelope, float rounding only) is classed `ask` with keyed levels [1.0, 1.0]. Fix: require a relative spread (for example, max/min > 1.01) before the envelope counts as keyed.
- [medium] tools/fm_receiver/iqmeasure.py:657-658 A CW at 20 dB SNR is classed `fm` because noise drives the smoothed frequency std over 500 Hz. The spec rule is literal, but the card is wrong for a real carrier; consider the 'fm' test on a noise-corrected frequency std, or report it.
- [low] tools/fm_receiver/iqmeasure.py:539 `measure(..., rate=0)` raises ZeroDivisionError, not IQSourceError. Fix: refuse rate <= 0 up front.
- [low] tools/fm_receiver/iqmeasure.py:135-146 `measure_file` with `t0` past the end silently returns a 0-sample noise card; a non-numeric `t0`/`center_hz` raises ValueError. Fix: raise IQSourceError for an empty span and wrap the float conversions.

Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
scores:
- m1/core-<model>: <0-100> — <one line why>
winner: m1/core-<model>
fix list for the winner (for the author):
- [high|medium|low] <file:line> <one line: what to change> (source: reviewer)
fix lists for the other entries are not needed.
rejected:
- <one line: the claim and why it's wrong> (source)
