# FM Receiver: capabilities

This is the running list of what the FM receiver can do, how each piece works,
and how far it has been proven. Add a row when you add a capability, and move
a row's status up when you verify it. How to *use* each feature is in
[usage.md](usage.md).

The app was built on 2026-09-21. It borrows code and rules from the RF bench
toolkit's FM + RDS receiver (`/data/python/SDR`, commit `20c76e4`). The code
is in `tools/fm_receiver/` and the tests are in `tools/tests/`.

## Status key

| Mark | Meaning |
|---|---|
| ✅ | Verified off air on real hardware (Signal Hound BB60D) |
| 🧪 | Verified by an automated test with synthetic signals, no radio |
| ⚠️ | Implemented, not yet verified on the hardware it is for |
| 💡 | Idea, not built |

## Radios

| Capability | Status | Notes |
|---|---|---|
| Signal Hound BB60D | ✅ | Goes through the toolkit's `bb60_source` (raw SoapySDR, since gr-soapy can't drive it). Receive rates 2.5, 5 and 10 MS/s (default 10 since 2026-09-21: CPU and reception measured the same at all three, see [roadmap.md](roadmap.md) item 6); sweep rates 10, 20 and 40 MS/s. |
| HackRF One | ✅ | Goes through gr-soapy with the toolkit's 3-stage gain plan. Off air on 2026-09-22 (`hw_hackrf_check.py`): found by the auto-detect; 89.3 in stereo (90°, coherence 1.00) with RDS 98-99% blocks good at 2 MS/s; recorded and played back with the same PI; the Center moved 700 kHz past the station with RDS kept (204/208 blocks). |
| HackRF gain default 40% | ✅ | Measured on 89.3, live gain changes: 30% → RDS 99%, SNR 40 dB; **40% → 99%, 41-43 dB**; 54% and 67% → channel at +2 dBFS, RDS lost (0 groups). |
| Overload warning on the HackRF (clipping) | ✅ | It has no overload flag, so `dsp.clip_probe` samples the IQ as the RF spectrum does and counts I or Q at full scale. Over 1% clipped, the status line reads "Input overloaded - turn the RF gain down (N% of samples clipped)". Measured at 2 MS/s: 40% gain 0-0.4%, 47% 77%, 54% ~100%. At 10 MS/s even 40% clipped 4%: the wider filter lets more strong stations reach the 8-bit ADC. Receive only; the BB60D keeps its own ADC flag. |
| Ettus USRP (UHD) | ⚠️ | Takes an IP address, or finds the first USRP when left blank. Only the "not found" error path has been tested. |
| IQ recording as a source (playback) | 🧪 ✅ | Plays this app's recordings, and the toolkit's `.cfile`+`.json` captures, in real time on a loop. Tuning moves the channel within the recorded band. A real off-air recording played back and decoded the same PI. |
| Radio picked in the window, no launcher | ✅ | The app opens straight into the window. The first run uses whichever radio is plugged in (BB60D, then HackRF). After that it reopens the last radio used. |
| Stop releases the radio | ✅ | **Stop** closes the device, so other programs can use it; **Start** opens it again. Off air with a HackRF, `hackrf_info` opened it straight after Stop while the app ran on. Fixed 2026-09-22: after any sweep the HackRF stayed busy, because the old sweep block (kept referenced on purpose) still held the radio's block; stopped sweeps now let go of it (`sweep_sink.detach`). Switching radios had the same leak. |
| Clear error when a radio is missing or busy | ✅ | The status line explains; nothing crashes. |
| BB60D ADC overload warning | ⚠️ | Carried over from the toolkit: "Input overloaded - turn the RF gain down". Not seen in this session's runs. |

## Sweep (FFT) - wider than the radio's bandwidth

| Capability | Status | Notes |
|---|---|---|
| Sweep any span from 1 MHz to 6 GHz by hopping the LO | ✅ | Each step keeps only the flat middle of the band. Steps tile the span bin for bin, with no resampling, gaps or overlaps (`SweepPlan`). |
| FFT only, no demodulation | ✅ | At each step: skip stale samples, average N FFT frames, place the result. About half a core for the whole app at 20 MS/s on the BB60D. |
| Stale-sample skip after a retune | 🧪 ✅ | Skips what the flowgraph has queued (read from the block counters) plus the radio's settle time. Test: a simulated radio with 20 ms retune latency shows ghosts with no settle time and none with 25 ms. Off air: no ghosts. |
| Stitching accuracy | ✅ | A 2-step FM-band sweep differs from single-LO references by a median of 0.95 dB, with 0 ghosts (`hw_bb60_check.py`). |
| Band presets | ✅ | FM 87.5-108, Japan 76-95, OIRT 65.8-74, VHF 30-300, or Custom. |
| Adjustable step bandwidth, FFT size (RBW), frames per step, settle time | ✅ | Step bandwidth restarts the radio. The rest re-plan the running sweep with no restart. |
| One-step sweeps rate-limited | ✅ | Capped at 30 per second. Without the cap, 40 MS/s on the BB60D ran 4000+ per second at 140% CPU. |
| HackRF DC-spike notch | 🧪 ✅ | ±30 kHz at each step's LO, filled in by straight-line interpolation. Off air, the spike stands 27-30 dB over the floor at the LO; the stitched sweep matched single-LO references to 0.3-0.8 dB. |
| HackRF sweep settle time 20 ms | ✅ | Measured off air, FM band in 2 steps at 20 MS/s: ghosts at 0 ms (557-563 bins) and at 5 ms in one run of two (34), none at 10, 20 or 40 ms. The default was an estimated 40 ms (92-98 ms a sweep); now 20 ms, twice the safe minimum (53 ms a sweep). |
| Station finder | 🧪 ✅ | Finds channels above a threshold (default 15 dB over the floor) on a 100 kHz raster; each must be a local maximum. Off air it found 22 stations across the FM band. |
| Station list → Listen | ✅ | Double-click a station, or the spectrum, to switch to Receive tuned there. The list shows the RDS name of any station already listened to. |
| Pause / resume | 🧪 | |
| Peak hold | 🧪 | |
| Averaging over sweeps (Average dial in Sweep) | ⚠️ | |

Measured on the BB60D (FM band, 4096-point FFT, 16 frames per step):

| Step bandwidth | Steps | Sweep time | Rate | App CPU |
|---|---|---|---|---|
| 20 MS/s (default) | 2 × 16.8 MHz | ~90-105 ms | ~200-230 MHz/s | ~0.5 core |
| 40 MS/s | 1 × 24 MHz | 33 ms (capped) | ~600 MHz/s | ~1.1 cores (the BB60 driver's own DSP) |

## Receive (IQ) - one station, narrower bandwidth

| Capability | Status | Notes |
|---|---|---|
| FM demodulation, 75 kHz deviation, 250 kHz MPX | 🧪 ✅ | Two-stage channeliser: an xlating filter to about 1 MS/s, then a resampling channel filter to 500 kS/s. The discriminator runs at 500 kS/s and its output is low-passed and halved to the 250 kS/s multiplex. Handles any IQ rate. Since the discriminator moved to 500 kS/s (2026-09-21), the synthetic test's stereo separation rose from 34 to 43 dB: less noise folds back into the multiplex. |
| IQ bandwidth selector (the flowgraph's bandwidth) | ✅ | In the Radio card. Per radio, e.g. BB60D 2.5/5/10 MS/s and HackRF 2-20 MS/s. Changing it rebuilds the chain, keeping the Center if the tuner still fits. On the BB60D, 2.5, 5 and 10 MS/s all measured about 55% of a core with no drops and the same reception. |
| Radio card: the Center (the radio's LO) set by hand | 🧪 ✅ | A digit entry. The tuner stays put if the new band still holds it, else it is pulled in to the nearer edge. **Center on tuner** puts the LO 300 kHz below the tuner. Off air (`hw_bb60_check.py`): the centre moved 700 kHz past 99.1, which kept decoding the same PI (211/212 blocks good); moved 3 MHz, the tuner was pulled to the edge. |
| Dashed line at the Center, on the spectrum and the waterfall | ✅ | Yellow (the theme's `warn`) in Slate and Reading Room; verdigris in Walnut, whose amber was too near its orange tuner marker and tan trace (`widgets.MARKERS`). Seen on the BB60D in all three themes. |
| Tuner confined to the band around the Center | 🧪 ✅ | The digits, the roller, keys, typing, clicks and drags all stop at the edge (the usable band less 150 kHz, to the kHz), and say so under the Center. Only the Center moves the band. A pick from outside Receive (the Sweep list, `--freq`) still places the Center itself. The reach is shaded on the spectrum, with a dotted line at each limit. |
| Tuner kept off a HackRF's DC spike | 🧪 | At least 100 kHz from the LO, passed over in the direction of travel - half a channel, so the spike (27-30 dB, measured) stays out of the channel. None on the BB60D, which samples at IF. |
| Tuner as digits: hover a digit, roll the wheel | 🧪 | Carries as arithmetic does (the tens of 90 MHz up gives 100 MHz; the ones of 99 MHz up gives 100). 1 kHz resolution, clamped to the radio's range, leading zeros dimmed. Up/Down/PageUp/PageDown on the hovered or selected digit; type a digit, Enter or double-click to type a frequency; Escape abandons it. |
| Step roller beside the tuner | 🧪 | ▲/▼: click (hold to repeat) or wheel, one Step per notch. Replaces the < and > buttons. |
| A 200 kHz Step on the Americas' odd-tenth channels | 🧪 | Stepping and Snap round to 88.1, 88.3 … 107.9. Before 2026-09-21 a 200 kHz step rounded onto even tenths (99.0, 99.2), where no US station is. |
| Live channel-filter bandwidth, 60-400 kHz | 🧪 ✅ | A digit entry in kHz with its own ▲/▼ (5 kHz a click), or the wheel over the orange band on the spectrum (5 kHz a notch, 1 kHz with Shift). New taps on the running filter, no rebuild. Past ~250 kHz the audio takes in any neighbour that close; 400 kHz is there to keep an HD Radio station's sidebands in the channel recording. |
| Radio, Tuner and RDS boxes in the Receive tab | ✅ | Radio: Center + Center on tuner, Tuner range, IQ bandwidth. Tuner: the tuner, its ▲/▼ and the Step knob, then the channel filter. RDS: station, standard, stereo and snap, Clear RDS, signal, audio, then the RDS details (moved in from beside the MPX view, which now has the bottom right to itself). Seen on the BB60D in Slate and Walnut. |
| Step as a knob, four settings | 🧪 | 10, 50, 100, 200 kHz; the wheel over it moves one setting. |
| Tuner's marker hidden except while tuning | 🧪 | Fades in (120 ms) on any tune, stays while the middle button holds the channel band, and fades out (500 ms) 0.9 s after the tuner is still. Always shown in Sweep. |
| Center line fades while the Center moves | 🧪 | Out in 120 ms as the Center changes, back 0.6 s after it stops, so the spectrum under it can be seen while scrolling. |
| Stereo decoding | 🧪 ✅ | L-R taken from the pilot PLL squared. Synthetic test: 34 dB separation, L and R the right way round. Off air: real stereo (L/R correlation 0.53). |
| Automatic 38 kHz phase (standard sine vs cosine convention) | 🧪 ✅ | Measures the L-R axis. Picks the standard phase on real stations (coherence 1.00) and the cosine phase that the toolkit's transmitter uses. The wrong phase would recover no stereo at all. |
| Stereo on/off | 🧪 | |
| Automatic mono when there is no pilot | ⚠️ | The pilot threshold is the toolkit's (1e-4, about a 2% pilot). A mono station hasn't been tried yet. |
| De-emphasis 75 µs (RBDS) / 50 µs (RDS) | 🧪 | Follows the Standard selector, changed live. |
| RDS / RBDS decoding | 🧪 ✅ | The toolkit's `rds_core`, unchanged: PI (with call sign), PS, station name, RadioText, RT+ Now Playing, program type, TP/TA/TMC, clock. Off air: 93-100% good blocks on 99.1, 90.9, 89.3 and 95.1. That matches or beats the toolkit's offline decoder on the same recordings. |
| Signal readouts | ✅ | Channel power (dBFS), SNR over the floor, pilot and stereo state, RDS decode quality. |
| Multiplex (MPX) spectrum | 🧪 ✅ | 0-125 kHz: mono, 19 kHz pilot, 38 kHz stereo, 57 kHz RDS. |

## Views: bandwidth and amplitude on dials

| Capability | Status | Notes |
|---|---|---|
| RF spectrum + waterfall (both modes), MPX spectrum (Receive) | ✅ | pyqtgraph; colours from the theme. |
| **Span** dial - view bandwidth, log scale | 🧪 ✅ | From 50 kHz (5 kHz on the MPX view) up to the whole band or sweep. Centred on the station in Receive. |
| **Ref level** and **Range** dials - amplitude | 🧪 | Top of the scale (-160…+20 dB) and dB from top to bottom (10-180 dB). The waterfall colours follow them. |
| **Average** dial | 🧪 | Frames averaged in Receive (tested), sweeps averaged in Sweep. |
| Every knob turns with the wheel under the pointer | 🧪 | No click first: Span ×1.25 a notch, Ref 2 dB, Range 5 dB, Average 1, Volume 2%, Step one setting; Shift for a fifth. A ring lights on the knob under the pointer. The toolkit's click-to-move guard still holds sliders and dropdowns until clicked. |
| Mouse zoom and pan along frequency, with the Span dial following | 🧪 | The wheel zoom is exercised by `test_gui.py` with real wheel events; the left-drag pan is not. |
| Wheel over the channel band: filter wider/narrower | 🧪 | Real wheel events in `test_gui.py`: 5 kHz a notch, 1 kHz with Shift; elsewhere the wheel still zooms. The band brightens under the pointer. |
| Middle-drag the channel band to tune | 🧪 | Real mouse events in `test_gui.py`: the tuner follows, snapped to the Step, stops at the band's edge, and the view does not move under the pointer. A recording's new part starts when the drag ends, not at every move. |
| Cursor readout (frequency and level) | ⚠️ | |
| Click the spectrum to tune, double-click to listen | ⚠️ | **Snap to step** rounds to the Step (was a fixed 100 kHz). The same tune path as the drag, which is tested; the click event itself isn't. |
| Dial handling | ⚠️ | Drag up/down (Shift for fine), double-click to reset. (The wheel is tested - see above.) |
| Peak hold | 🧪 | |
| No ghost of the last band or channel after a retune | 🧪 | Fixed 2026-09-21: peak hold kept the old channel's MPX, and the old band's spectrum drawn at the new frequencies after the LO moved. Now the spectra and RDS drop every frame made from samples queued before a retune (counted, as the sweep does), and peak hold starts again. `test_tuning.py`, a radio 75 ms slow to retune: 7 dB (noise) where the old tone was, against 85 dB without the fix. |
| Waterfall palettes from each theme's colours | ✅ | Slate *Ice*, Reading Room *Ink on paper*, Walnut *Dial glow*, blended in Oklab from the theme's tokens (`widgets.WATERFALL`). Other candidates were rendered for choosing; see [roadmap.md](roadmap.md) item 1. |
| Waterfall on/off, Full span button | ⚠️ | |
| View settings saved separately for Sweep, Receive and MPX | 🧪 | |

## Audio

| Capability | Status | Notes |
|---|---|---|
| Stereo out to the sound card | ✅ | The GNU Radio audio sink opened on the bench. Tested muted, so nobody has actually listened to it yet. |
| Mute (button, Ctrl+M) | 🧪 ✅ | Output gain goes to 0. The meters and any recording carry on. |
| Volume (dial, Ctrl+Up/Down) | 🧪 | Square law, up to 1.5× at 100%. |
| L/R level meters (peak, RMS, peak hold) | 🧪 ✅ | They show the level before the volume control. |
| Runs without a sound card (`--no-audio`, or none found) | 🧪 | |

## Recording

| Capability | Status | Notes |
|---|---|---|
| Audio to WAV | 🧪 ✅ | 48 kHz, 16-bit stereo, recorded after de-emphasis and before volume/mute. Written by its own thread. |
| IQ, channel: 500 kS/s, station at 0 Hz | 🧪 ✅ | 4 MB/s. Through the channel filter, so up to 400 kHz of it. 250 kS/s before 2026-09-21; those recordings still play. |
| IQ, whole band, at the IQ bandwidth | 🧪 | 8 bytes per sample (20 MB/s at 2.5 MS/s). |
| Any combination at once, one Record button (Ctrl+R) | 🧪 | |
| SigMF metadata + toolkit `.json` sidecar | 🧪 | `.cfile` (cf32_le) data plus `.sigmf-meta` and `.json`, so the toolkit's `scripts/test_rds_core.py` reads them. |
| Retune while recording IQ starts a new file (`-part2`, …) | 🧪 | Each file has one centre frequency and its own metadata. A band recording continues in the same file while only the channel moves. |
| Recordings folder chooser | ⚠️ | Default: `recordings/` in the project. |

## App

| Capability | Status | Notes |
|---|---|---|
| One launch script: `./fm-receiver` | ✅ | Activates the `gnu` conda environment (override with `FMRX_CONDA_ENV`). |
| Settings remembered | 🧪 | `~/.config/fm-receiver/config.json`: radio, mode, frequency, gain and rates per radio, views, audio, recording, window layout. |
| Three themes: Slate, Reading Room, Walnut | 🧪 | From the toolkit's design tokens. Picked with the toolkit's theme disc in the header (its ground, rule and trace colours; a click moves to the next). |
| Keyboard shortcuts | ⚠️ | Ctrl+1/2 switch mode, Ctrl+Left/Right step, Ctrl+M mute, Ctrl+R record, Ctrl+Up/Down volume. The digit entries' own keys are tested. |
| Fast Sweep ⇄ Receive switching | ✅ | 0.2-0.3 s on the BB60D. The device stays open across the switch; reopening it took 1.6 s. |
| Clean shutdown on Ctrl+C / SIGTERM | ⚠️ | A Python timer keeps ticking so signals get through Qt's event loop (a toolkit rule). |

## Tests

| Test | What it proves | Needs |
|---|---|---|
| `tools/tests/test_sweep.py` | Plan tiling, DC notch, station finder, the stale-sample skip against a slow simulated radio | nothing |
| `tools/tests/test_tuning.py` | The tuner's clamp (band edge, radio range, DC spike), the digit entry's carries, wheel and typing, and no ghost in the spectrum after the LO moves (with a check that it fails without the fix) | nothing (offscreen) |
| `tools/tests/test_receive_chain.py` | Synthetic stereo+RDS station through the real engine in both 38 kHz conventions: RDS, 34 dB separation, mute, WAV and IQ recordings with metadata | nothing |
| `tools/tests/test_gui.py` | The window driven like a user: RDS on screen, mute, volume, dials, channel filter, the wheel and middle-drag on the channel band, the tuner's digits and roller, recording across a retune, the theme disc, Stop/Start, settings saved; sweep with a simulated radio → station list → double-click → Receive → the Radio card (Center, edge, DC spike, peak hold) | nothing (offscreen) |
| `tools/tests/hw_bb60_check.py` | Off air: stitching against references, RDS on the strongest station that has it, record and play back, moving the Center | BB60D + antenna |
| `tools/tests/hw_hackrf_check.py` | Off air: sweep ghosts at five settle times, RDS at four gains, the DC spike, record/Center/playback, mode switch, another program opening the HackRF after close and after the window's Stop, and the window's clipping warning | HackRF + antenna, not in use elsewhere |
| `tools/tests/run_all.py [--hw] [--hackrf]` | Runs all of the above | |

## Known limitations

- **The USRP has not been run on hardware.** The HackRF has (2026-09-22).
- **The HackRF clips easily.** Its ADC is 8 bits, and the whole of its
  baseband filter reaches it: at 10 MS/s, 40% gain already clipped 4% of
  samples here. Mind the overload warning, and turn the gain down at wide
  IQ bandwidths.
- The HackRF check's DC-spike width reading varied between runs (2 kHz, then
  ~100 kHz above +10 dB) - probably the strong station 300 kHz away lifting
  the floor near DC. Its height (27-30 dB) was steady.
- **Not every station sends RDS.** 102.1 was the strongest signal here but
  sent a pilot and stereo with nothing at 57 kHz; the toolkit's decoder found
  no RDS in it either.
- An IQ recording's playback can't sweep: its band is fixed.
- The BB60D check's stitching test compares a sweep with references taken
  moments apart, so a signal that comes and goes between them can count as
  one "ghost" bin. It happened once on 2026-09-21 (1 bin of ~2850); the two
  reruns straight after found none.
- IQ files are read as cf32 only (this app's own and the toolkit's captures).
- Sweep sample rates are limited by the radio and by Python. The BB60D at
  40 MS/s uses about a core, mostly in the vendor driver.

## Ideas / backlog

Planned work, split into phases, is in [roadmap.md](roadmap.md).

- 💡 Native HackRF sweep through `hackrf_sweep`'s firmware mode, which is far faster than LO hopping in software.
- 💡 A scan/seek button in Receive: step to the next station above a threshold.
- 💡 A memory list of favourite stations with their RDS names.
- 💡 Scheduled recordings, and splitting long recordings by size.
- 💡 Decoding HD Radio (IBOC) - its sidebands are visible either side of 99.1.
- 💡 An audio spectrum view, and an FM deviation / modulation meter.
- 💡 A sweep export (CSV of frequency and level) and saved sweep screenshots.
