# Grok review: KEYED_DEPTH and the band under a strong line (2026-10-30)

Grok (grok-4.7, headless, sandboxed, synthetic signals only) reviewed commits `1c1d26d` and
`514636d` on a `git archive` of the second. Its text follows unchanged. A source of leads:
each finding was checked against the code, and the outcome is here.

| # | Finding | Checked | Done |
|---|---|---|---|
| 1 | The 20 dB width is doubled around the strongest leftover bin, even a blob apart from the carrier | True: `_occupied`'s third value is the full run about `argmax(rest)` | `_modulation_width` follows only the runs that touch the line, out from it; nothing touching it gives 0. Its two blob cases now get the parent's band exactly |
| 2 | "Even about the line" was even about the centroid; near the edge the band got narrower | True | The band is centred on the line; when the span's edge leaves an even band narrower than the old one, the old one stands (996 kHz: 40 kHz, as the parent) |
| 3 | A wider band costs a discarded re-read on signals that stay unread | True | Kept: the cost of reading shallow keying. Noted in the roadmap |
| 4 | The depth tests do not fail before the fix; 3.5e-5, not 4e-5; "3 %" is an absolute 0.03 | True (the first was said in the commit) | Comment and test name corrected |
| 5 | The contract still gives the old band and smoother | True | Amendment "Under a strong line" in CONTRACTS-measure.md |

Found while fixing 2: a whole continuous spectrum (rectangular 4FSK) passed as a "line" at
1e-3 of its peak, the band moved off-centre and 4FSK read `fm`. A line is now 8 bins or fewer.
After the fixes: levels test 30 of 30, the 11 bench clips as on main, the 35 recordings
keep their classes, suite 54 of 54.

---

# Review: KEYED_DEPTH and `_modulation_width`

The shallow-ASK change does what the second commit describes on the three signals it names. 1 against 0.8 at 20 kBd and at 100 kBd, and 1 against 0.9 at 20 kBd, go from `carrier` or `unknown` to `ask`, with the levels and the rate the tests ask for. I did not find a family that used to read right and now reads wrong (AM tone, AM noise, FM mono, FM stereo, a lone 92 kHz subcarrier, a carrier, a carrier plus a spur or a noise blob, OOK, narrow and unbalanced FSK, residual-carrier BPSK as built, analog TV).

What is wrong is the width the new band is taken from when the residual is not a one-sided skirt, the claim that the band is even about the carrier line, the cost of that wider band on signals that stay unreadable, and a depth gate the new tests do not lock.

Scoring used `_analyse` with an empty cell dict, so no LTE or NR search ran. `measure` on one 20 kBd shallow ASK agreed with `_analyse` (same class change). `_finish` does not change the class. Occupied widths here are all under 1 MHz, so `measure` would not have searched cells either (`CELLS_MIN_WIDTH_HZ`). The parent module is `changes.diff` reversed onto a copy of this tree (`work/parent/fm_receiver/iqmeasure.py`): it has neither `KEYED_DEPTH` nor `_modulation_width`.

## Findings

### 1. bug — the 20 dB width is doubled around whatever bin is strongest, and that bin need not touch the carrier

`tools/fm_receiver/iqmeasure.py`, `_modulation_width` lines 1170–1186, using `_occupied` lines 698–704.

The docstring says the return value is twice the one-sided run that ends at the bins taken off the carrier. The commit text says the band comes from the residual spectrum's width 20 dB down. The code does `2 * _occupied(rest)[2]`. `_occupied`'s third value is the full contiguous width around `argmax(rest)`: left edge inclusive, right edge exclusive, times the bin width. That width is one-sided only when the contour dies at the hole on one side. A detached line or blob is already measured across its own peak, and the factor of two doubles it. A second blob on the other side of the carrier is ignored, because only the strongest residual bin is measured.

Hand spectra, bin width 1000 Hz, floor 0, fed to `_modulation_width` on this tree (the parent has no such function):

- Carrier in bin 10, power 1000, and bins 8 and 12 at power 10. The carrier run is bin 10 only (`CARRIER_RUN` times the peak is 1; neighbours are 0). `_occupied` of the residual is 1000 Hz (one bin). The function returns 2000 Hz. The sidebands sit 2000 Hz either side of the line, so twice that one-sided offset is 4000 Hz, and the 20 dB width of the two sidebands together is 4000 Hz as well.
- Carrier in bin 400, power 1000, and a flat 40-bin blob at bins 600–639, power 5. The run is `[400, 400]`. The blob does not touch the hole. `_occupied` of the residual is 40000 Hz. The function returns 80000 Hz.
- Carrier in bin 63 of a 64-bin spectrum, power 1000, with power 20 at bins 61 and 1. The function returns 2000 Hz. `argmax` keeps the first of the two equal sidebands. Nothing wraps.

On a real fine spectrum the same rule widened bands while the class stayed put. Carrier plus a USB noise blob, rate 1 MS/s, 0.4 s, SNR 45 dB, seed 5. The residual peak is the blob (`touches_hole` false).

- Blob 2–12 kHz at about 5% of the carrier power. Before: `unknown`, second-pass band 15409..45255 Hz (width 29846), deviation 1520 Hz. After: `unknown`, band −63..60728 Hz (width 60791), deviation 1521 Hz. Reported width 20264 Hz from a 20 dB contour of 10132 Hz, peak 5066 Hz off the carrier, carrier share 0.951, residual share 0.0489.
- Blob 5–25 kHz at about 20%. Before: `unknown`, band width 71777 Hz, deviation 4399 Hz. After: `unknown`, band width 120850 Hz, deviation 4401 Hz. Contour 20142 Hz, doubled to 40283 Hz, peak 8850 Hz off the line, `touches_hole` false.

Where the contour does start at the hole, the doubling is the estimate the shallow-ASK fix needs. 1 against 0.8 at 20 kBd (the test's generator: 2 MS/s, 0.5 s, offset 30 kHz, SNR 25 dB, seed 1): carrier share 0.988, residual share 0.0119, run 4 bins, contour 15747 Hz starting at the hole, returned width 31494 Hz, band 20000 Hz to 94482 Hz, decimation stays 2, output rate stays 1 MS/s. Class `carrier` to `ask`, levels 0.8014 and 0.9987, rate 19999 Bd. Same transition through `measure` on a sibling draw (noise seed 8): `carrier` to `ask`, levels 0.8015 and 0.9986, rate 19999 Bd, band width 90088 Hz.

Confidence high on the formula and on these inputs. I did not see that wrong doubling change a class.

### 2. risk — "even about the line" is even about the occupied centroid, and near the edge the new band is narrower

`iqmeasure.py` lines 1902–1909. `c` is `centre - center_hz` from `_occupied` (the power centroid), not the argmax bin. `reach` is then limited to the distance from that centroid to each edge of the span, and floored at `TRACK_MIN_HZ / 2` (10 kHz), so `reach` itself does not go negative. The span clamp after that can still cut one side.

Input: 1 against 0.8 at 100 kBd, rate 2 MS/s, 0.25 s, SNR 30 dB, seed 1, carrier at 996 kHz (4 kHz below Nyquist). Centroid 984984 Hz, which is 11016 Hz below the carrier. `bw99` 16235 Hz.

- Before: `unknown`, band 960631..1000000 Hz (width 39369), deviation 378 Hz.
- After: `unknown`, band 969968..1000000 Hz (width 30032), deviation 346 Hz.

The new band extends 15016 Hz either side of the centroid and meets Nyquist exactly. About the carrier it extends 26032 Hz down and 4000 Hz up. The guard the comment describes (a one-sided cut turning ASK into FSK) is not what this band does. Both readings stay `unknown`, so this input does not show that misread. The same shape at 992 kHz in the longer survey: band width 41991 Hz to 36009 Hz, both `unknown`.

At 900 kHz the clamp still leaves a 202 kHz band and the class stays `unknown` (the centred signal needed about ±241 kHz to read `ask`). Confidence high on the geometry, medium that a one-sided spectrum here would be called `fsk`: I did not produce that call.

### 3. risk — a wider second pass costs a discarded re-read, and it can turn decimation off, on signals that stay unreadable

`iqmeasure.py` lines 1902–1911 and the short-block re-read around line 2221. Decimation is `rate // max(2 * bandwidth, TRACK_RATE)` with `TRACK_RATE` 1 MHz (`_decimated`, line 1810). The output rate moves only when the RF bandwidth exceeds 500 kHz.

- 1 against 0.8 at 100 kBd, rate 2 MS/s, 0.3 s, offset 900 kHz, SNR 25 dB, seed 1. Before: `unknown`, one pass, band width 47607 Hz, decimation 2, 0.069 s. After: `unknown`, two identical passes of band 797968..1000000 Hz (width 202032), decimation still 2, 0.489 s. The returned card has no "read in N-sample blocks" line, so the second pass was thrown away. About 7× the time, same class. The centred 100 kBd signal, which does become `ask`, also runs twice (0.075 s to 0.334 s) and keeps the second pass. That cost is the fix working.
- 1 against 0.8 at 400 kBd, rate 4 MS/s, 0.2 s, offset 50 kHz, SNR 30 dB, seed 60. Before: `unknown`, band width 183838 Hz, decimation 4, output 1 MS/s, 0.071 s. After: `unknown`, band width 755859 Hz, decimation 2, output 2 MS/s, 0.080 s. The 20 dB contour is 125977 Hz (touches the hole, carrier share 0.988), doubled to 251953 Hz. Three times that, clipped by nothing, is still too narrow for 400 kBd. Twice the output samples, class unchanged. Wall time on this short capture rose about 13%.
- Noise AM, audio to 300 kHz, peak index 0.4, rate 4 MS/s, 1.0 s, SNR 50 dB, seed 9. `bw99` is 732 Hz on both (the carrier). Before: `unknown`, band ±10 kHz, decimation 4, output 1 MS/s, 0.220 s, envelope depth 0.036. After: `unknown`, band −900147..900146 Hz (width 1800293), decimation 1, output 4 MS/s, 0.296 s, depth 0.119, deviation 59 Hz. Four times the tracked samples, about 1.35× the wall time at this length, class unchanged. The input FFT still dominates at 4 million samples. A 28 MS/s file whose residual contour filled the span would take the second pass at the full rate; I did not time that file.

The three signals the commit fixes stay at 1 MS/s output (their new RF bands are under 500 kHz). Confidence high on the three timings above.

### 4. nit — KEYED_DEPTH is not what the new tests catch, and on synthetics it never changes a call

`iqmeasure.py` lines 112–116, the two-level gate at 1932–1933, the multi-level gate at 2021–2023. Tests in `tools/tests/test_iq_measure_levels.py` lines 199–223.

The cited pair 0.99992 and 0.99999 has depth `(0.99999 − 0.99992) / (0.99999 + 0.99992) = 3.500e-5`. The comment on line 115 calls that 4e-5. The test's four levels 1.0, 0.9999, 0.9998, 0.9997 have extreme depth `(1 − 0.9997) / (1 + 0.9997) = 1.500e-4`. Depth 1e-3 is a ratio of 0.998002, about 0.017 dB. 1 against 0.95 is depth 0.0256 (0.45 dB), which the roadmap already leaves unread.

The official file on this tree: 30 ok, 0 fail. The same file importing the parent module: 27 ok, 3 fail, and the failures are only `ask_shallow`, `ask_shallow_fast`, and `ask_shallower`. Both depth assertions pass on the parent. `short_card` calls `_analyse` with `block=2` directly. It does not go through `measure` and it does not take the fast-clock re-read. The level check on the shallow-ASK tests is `abs(x − y) < 0.03` (line 193), an absolute gap, while the name says 3%.

On the test fixture itself (the FM tone of deviation 2 kHz at 4 kHz, block 2, noiseless), both trees return `fm`, not keyed, one level 0.99985, percentile depth 0.00253, deviation 1997 Hz, the same band. The bench pair under that FM does the same (`fm`, depth 0.00252). The 4ASK control 0.4..1.0 is `ask` order 4 on both, depth 0.431. The FM smear stops `_levels` before the new gate runs.

Clean two-level ASK at DC, no FM, 100 Bd so a 16-sample block sits inside a symbol, 0.5 s at 1 MS/s, depths 3.5e-5, 2e-4, 5e-4, 8e-4, 1e-3, 1.2e-3, 1.5e-3, 2e-3: parent and new return the same class on every one (mostly `carrier`; depth 1.2e-3 is `fsk` on both, a pre-existing call). The bench pair with no FM, blocks of 2 and of 16, is `carrier` on both. The first clean ASK both trees call `ask` is depth 0.002 in 16-sample blocks at 2 kBd: levels 0.99601 and 1.00000, reported depth 0.001999, rate 2000 Bd. That is above the gate, so the gate keeps it. A pure carrier's second-pass envelope already has percentile depth about 0.0010 to 0.0013 (`cw_clean` 0.00131), so a split at the cited 3.5e-5 does not survive this filter. The gate's arithmetic would reject 0.99992 against 0.99999 (`7e-5 < 1e-3 * 1.99991`). I never got `_levels` to return that pair.

`CARRIER_SHARE` 0.5, `MOD_SHARE` 3e-4, `MOD_FLOOR` 2, and `CARRIER_RUN` 1e-3 are not tight against the new tests. The passing ASK cases have carrier share 0.988 and residual share 0.012 and 0.003. Equal-probability 1 against 0.95 has analytic sideband share `6.57e-4`, which is above `MOD_SHARE`, and the measured contour is still only 732 Hz (returned width 1465 Hz), so the band stays ±10 kHz and both trees say `carrier`. A bin-centred Hann of length 16384 (`np.hanning`, the fine window) has 3 bins within −30 dB; the next bin is −93.8 dB. The 4-bin runs on the offset ASK are an off-grid carrier, which matches `CARRIER_RUN`. An almost equal 2-tone FSK (share 0.4989) gets width 0, and the band matches the parent.

Confidence high that the tests do not lock the gate. Confidence high that the gate did not change these synthetics. I have no recording of the 8 s bench FM, so I cannot show the gate firing on the event that motivated it.

### 5. nit — the contract still describes the old band and the old smoother

`CONTRACTS-measure.md` lines 63–67 say the tracking band is `max(3 × bandwidth_hz, 20 kHz)`. Line 339 says the clock average is `rate / (2 × bandwidth_hz)`. The code uses `max(bandwidth_hz, wide)` in both places (lines 1903 and 2002). The shallow-ASK card's `bandwidth_hz` is still the carrier's 99% width (3540 Hz at 20 kBd); only the internal band changed.

Confidence high.

## What I checked and found fine

- Slice `rest[lo:hi+1]` matches `_run_width`'s inclusive ends. On the 40-bin blob the run is the single carrier bin and the 20 dB measure is the 40 bins of the blob.
- No wrap. The edge spectrum above returns one sideband.
- Units of `wide > bw99` are both Hz. On the 20 kBd ASK, 31494 > 3540, and the band becomes 3 times the wider one.
- `reach` is floored at 10 kHz. I did not hit a centroid closer than 15 kHz to an edge on a signal with `wide > bw99` and `bw99` large (the 996 kHz case). A pure carrier already takes the 20 kHz floor, and there the new branch reproduces the old band.
- Floor subtraction. `rest` is `P` minus twice the coarse floor, then the run is zeroed. A clean carrier (SNR 40 dB and noiseless) keeps residual share about 9e-4, above `MOD_SHARE`, but the contour is one bin (183 Hz), the returned width is 366 Hz, and the band stays ±10 kHz. Class `carrier` on both trees.
- Hann skirt versus a neighbour. A −30 dB tone 50 kHz off, and a 40 kHz noise blob at −25 dB and −30 dB placed 50, 80, and 200 kHz off, all stay `unknown` with the ±10 kHz band. The residual peak is the skirt (offset one fine bin, +122 Hz) or, for the −30 dB tone, the tone itself with a one-bin contour doubled to 366 Hz. The neighbour is not pulled in. A −15 dB tone at 40 kHz is `fm` on both trees; `bw99` already spans it, and the new width is 366 Hz, under `bw99`.
- AM tone, index 0.5 at 1 kHz: both `am`, band ±10 kHz. Index 0.3 at 15 kHz: both `am`, band width 90271 Hz both. Index 0.1 at 15 kHz: both `unknown`, band ±10 kHz. The sideband is a detached line, doubled to 366 Hz, which does not reach 15 kHz. That limit is older than these commits.
- AM by noise, 5 kHz, index 0.5: both `am`. The band widens from 20 kHz to 29.7 kHz (`touches_hole` true, width 9888 Hz) and the class holds. 10 kHz at index 0.3: both `unknown`, band 20 kHz to 59.7 kHz.
- FM, deviation 75 kHz, 1 kHz tone: both `fm`, deviation 74483 Hz, carrier share 0.025 so the new width is 0, band identical. FM stereo: both `fm`, subcarriers at 19.000 kHz and 38.000 kHz, width 0. FM whose only modulation is 3 kHz deviation at 92 kHz: both `unknown`, band ±10 kHz. The subcarrier is outside that band (residual peak 92 kHz off, contour one bin). This does not reproduce the bench's false `ask`. Weak FM, deviation 200 Hz: both `carrier` (spread under 500 Hz).
- OOK at 2 kBd and 40 kBd, FSK at 2 kBd with 1 kHz shift, FSK at 4 kBd with 8 kHz shift, unbalanced FSK (90% one tone, 40 kHz): same class, same band. Deep ASK 0.3 against 1.0 at 10 and 100 kBd, centred: both `ask`, and `wide < bw99` so the band is unchanged. The 10 kBd deep case has a 111-bin carrier run, so the −30 dB run swallows the near sidebands and the old 99% width carries the decision.
- BPSK plus a DC offset of 0.3 and of 1.0: both trees say `ask` order 2 and `ook` respectively. The generator is two amplitudes, not constant-envelope PSK. The band is unchanged.
- Simplified AM-VSB television, 0.06 s at 8 MS/s: both `tv`, same band, deviation 92 Hz. Carrier share 0.796, returned width 2929 Hz, under `bw99`.
- 1 against 0.98 at 5 kBd, SNR 80 dB: both `ask`, levels 0.9800 and 1.0000, depth 0.0101, band ±10 kHz. The gate keeps a depth ten times its threshold.
- Phase noise, σ 2 kHz, SNR 50 dB: both `unknown`, band 20 kHz to 36.3 kHz. σ 200 Hz: both `carrier`, band unchanged.
- No case raised, no `lo > hi`, and the 400 kBd and wide-AM cases stayed inside a few times the parent's wall time at these lengths.

## How this was run

Official test, this tree: `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools` and the gnu env's Python 3.12 on `tools/tests/test_iq_measure_levels.py`. Result: 30 ok, 0 fail. The same assertions with `fm_receiver` imported from the reversed parent: 27 ok, 3 fail, the three shallow-ASK checks.

Survey and follow-up scripts live under `/tmp/grok-r08/work/` (`probe.py`, `followup.py`, `followup2.py`). Signals are numpy only. Arrays stayed under about 4 million samples except where a case is named with its length. Nothing opened a radio.
