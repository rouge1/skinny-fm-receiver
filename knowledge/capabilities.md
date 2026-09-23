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
| Signal Hound BB60D | ✅ | Goes through the toolkit's `bb60_source` (raw SoapySDR, since gr-soapy can't drive it). Receive rates 2.5, 5 and 10 MS/s (default 10 since 2026-09-21: CPU and reception measured the same at all three, see [roadmap.md](roadmap.md) item 6); sweep rates 10, 20 and 40 MS/s for LO hopping, but the window sweeps it with its own sweep instead (below). |
| HackRF One | ✅ | Goes through gr-soapy with the toolkit's 3-stage gain plan. Off air on 2026-09-22 (`hw_hackrf_check.py`): found by the auto-detect; 89.3 in stereo (90°, coherence 1.00) with RDS 98-99% blocks good at 2 MS/s; recorded and played back with the same PI; the Center moved 700 kHz past the station with RDS kept (204/208 blocks). |
| HackRF gain default 40% | ✅ | Measured on 89.3, live gain changes: 30% → RDS 99%, SNR 40 dB; **40% → 99%, 41-43 dB**; 54% and 67% → channel at +2 dBFS, RDS lost (0 groups). |
| Overload warning on the HackRF (clipping) | ✅ | It has no overload flag, so `dsp.clip_probe` samples the IQ as the RF spectrum does and counts I or Q at full scale. Over 1% clipped, the status line reads "Input overloaded - turn the RF gain down (N% of samples clipped)". Measured at 2 MS/s: 40% gain 0-0.4%, 47% 77%, 54% ~100%. At 10 MS/s even 40% clipped 4%: the wider filter lets more strong stations reach the 8-bit ADC. The BB60D keeps its own ADC flag. |
| The HackRF's clipped share, always shown | 🧪 ✅ | "HackRF One - Receiving at 2 MS/s - clipped 0.13%" in Receive and Sweep, as ble-scanner shows it. Full scale is I or Q ≥ 125/127, ble-scanner's threshold; in Receive the share is smoothed over about 0.3 s. Green under 0.3%, amber to 1%, then the overload warning. Off air (2026-09-22): 0.00% at the default 40% on the FM band. |
| Clipping counted in the HackRF's sweep | 🧪 ✅ | Only the frames each step measures, not the settle. The readout is the last complete sweep's worst step, held for a sweep, so the warning names it: "(95.6% of samples clipped in the step centred on 95 MHz)" at 60% gain. Over the full range at the default 40%, a UHF TV step (533.5 MHz) clipped 2.5% here. |
| Ettus USRP (UHD) | ⚠️ | Takes an IP address, or finds the first USRP when left blank. Only the "not found" error path has been tested. |
| IQ recording as a source (playback) | 🧪 ✅ | Plays this app's recordings, and the toolkit's `.cfile`+`.json` captures, in real time on a loop. Tuning moves the channel within the recorded band. A real off-air recording played back and decoded the same PI. |
| Radio picked in the window, no launcher | ✅ | The app opens straight into the window. The first run uses whichever radio is plugged in (BB60D, then HackRF). After that it reopens the last radio used. |
| Stop releases the radio | ✅ | **Stop** closes the device, so other programs can use it; **Start** opens it again. Off air with a HackRF, `hackrf_info` opened it straight after Stop while the app ran on. Fixed 2026-09-22: after any sweep the HackRF stayed busy, because the old sweep block (kept referenced on purpose) still held the radio's block; stopped sweeps now let go of it (`sweep_sink.detach`). Switching radios had the same leak. |
| Clear error when a radio is missing or busy | ✅ | The status line explains; nothing crashes. Another radio can be picked straight after. Fixed 2026-09-22: started on a BB60D that was not plugged in, the HackRF picked next was "no match". Loading the BB60's SoapySDR module by hand, in a process that had not used SoapySDR yet, left every other driver unloaded. Now all the modules on the search path are loaded (`radios._load_bb60_module`, `test_tuning.py`). Checked in the window, headless: BB60D not found, then HackRF receiving. |
| BB60D ADC overload warning | ⚠️ | Carried over from the toolkit: "Input overloaded - turn the RF gain down". Not seen in this session's runs. |

## Sweep (FFT) - wider than the radio's bandwidth

| Capability | Status | Notes |
|---|---|---|
| **The BB60D's own sweep, 9 kHz to 6 GHz** | ✅ | `bb60_sweep`, through Signal Hound's API. It sweeps in the device rather than by hopping the IQ stream's LO. Off air on 2026-09-22 (`hw_bb60_check.py`): 9 kHz to 6 GHz in 229-233 ms (26 GHz/s) at any RBW from 30 kHz to 1 MHz; the FM band in 12 ms at 10 kHz. It found the same six strongest FM stations as the stitched sweep. It borrows the device the IQ stream opened, so no second open is needed (see the switching row under App). |
| Levels in dBm in the BB60D's own sweep | ✅ | Calibrated by the device. The axis, the pointer readout and the station list say dBm; they say dBFS again in Receive. The strongest station read -41 dBm at 300 kHz RBW and the floor -107 dBm at 10 kHz. |
| RBW for the BB60D's own sweep | 🧪 ✅ | **Auto** keeps a sweep near 80,000 points: 300 kHz over 9 kHz to 6 GHz (76,801 points), 1 kHz over the FM band. Or pick 1 kHz to 1 MHz. An RBW too fine for the span is raised to keep a sweep under 1.5 million points, and the info line says so. The step bandwidth, FFT, frames and settle rows are hidden for it. |
| The gain slider reaches the BB60D's own sweep | ✅ | Mapped as for its IQ stream: the 30 dB attenuator comes off first (10 dB steps), then gain goes on. Off air the floor was -84 dBm at 0% and -107 dBm at the default 60%. |
| **AGC in the BB60D's own sweep** | 🧪 ✅ | An **AGC** box beside the RF gain slider. Ticked, the device sets its gain and attenuation for a reference level (Signal Hound's recommendation), and the **Ref level knob is that reference**: it moves to 5 dB over the strongest input, in 5 dB steps, rising at once and falling only once the input has dropped 10 dB for 3 s. The input is the most power in any 27 MHz of the sweep (the front end's IF): steady to ±0.2 dB at any RBW, where a station's peak swings by up to 15 dB. Off air (2026-09-22) on the FM band it settled at -25 dBm and held for 15 s, with stations within ±1.5 dB of the slider's 60%, the same floor, and no overloads in 717 sweeps. A reference over one station only (-30/-35) overloaded now and then. Receive keeps the slider: the SoapySDR module has no automatic gain (probed: only its RF and ATT gains). |
| BB60D health: temperature, USB voltage and current | 🧪 ✅ | `bbGetDeviceDiagnostics`, read with the status line: the numbers are its tooltip ("Signal Hound BB60D: 30.8 °C, USB 4.68 V, 1.15 A"), and below Signal Hound's 4.4 V it warns "USB voltage low (4.31 V): measurements may be off". Probed off air: about 10 us a call while streaming, in its own sweep and in real time, nothing dropped. The current comes back in mA despite its name. Not yet tried on the Mac's library. |
| **Real time on the BB60D, for spans up to 27 MHz** | 🧪 ✅ | A **Real time** button in the Sweep tab, which stays down while it watches. The API FFTs every sample at 50% overlap, instead of stepping across the span, so nothing lasting longer than its intercept time is missed: 307 us at 10 kHz RBW, 4.8 us at 631 kHz. Off air over the FM band (2026-09-22): 30 frames a second, 36-56% of a core for the API alone and about 46% for the whole window. The trace is each frame's maximum over 33 ms. The RBW runs from 2.47 to 631 kHz; Auto is 10 kHz, 4,199 points over the FM band. Pressing it drops the sweep to the 27 MHz window on the tuner (slid inside the radio's range at the ends, never cut short), and letting it out gives back the span that was there - unless the bounds have been edited meanwhile, which it leaves alone. A tuner put outside the window moves the window, so it is the tuner that says where to watch. Bounds widened past 27 MHz are swept instead, with the button still down and the info line saying why. Off air (2026-09-22): pressed on 95.1 MHz from the full 9 kHz-6 GHz sweep, it watched 81.6 to 108.6 MHz; let out, the full range came back. Not on a Mac: Signal Hound's Mac library has no real time, and the button is greyed. |
| Density map behind the trace (real time) | ✅ | 525 x 256 per frame: how often each level was hit, column by column, in the theme's waterfall colours on a log scale, clear where nothing was hit. The view's Ref level and Range place it; turning either re-plans the device 150 ms after the knob rests. The reference level places the map without changing any level, since the gain is set by hand. Off air the map's highest hit at 89.3 MHz was -43.6 dBm, against the trace's peak of -43.7. |
| Own sweep capped at 30 a second | ✅ | The API's processing runs on this computer. Uncapped, the FM band ran at 88 sweeps a second on 87% of a core; capped at 30 a second, 28%. A full-range sweep (4.2 a second) is never held back, and costs 92-97% of a core, nearly all of it the API's. |
| Sweep any span from 1 MHz to 6 GHz by hopping the LO | ✅ | Every other radio. Each step keeps only the flat middle of the band. Steps tile the span bin for bin, with no resampling, gaps or overlaps (`SweepPlan`). |
| The tuner in the Sweep tab too | 🧪 ✅ | The same tuner as Receive's, as digits with its ▲/▼, moving by the Step: one frequency, shown in both places, whichever moves it (the digits, the roller, a click on the spectrum, the station list, Receive's own tuner). It is the sweep's marker, what **Listen** tunes to, and what **Real time** centres on. |
| Start and Stop bounds as digits | 🧪 | In the Sweep tab, in MHz to the kHz, like the Tuner: hover a digit and roll the wheel, or type a value. The bounds stay inside the radio's sweep range and at least 200 kHz apart (Signal Hound's suggested minimum span). The sweep re-plans 150 ms after the digits stop changing, without a restart. |
| Full range of the radio (the default band) | 🧪 ✅ | The first preset. 9 kHz to 6 GHz on the BB60D, 1 MHz to 6 GHz on a HackRF; it follows the radio when you switch radios. New settings start on it. |
| FFT only, no demodulation | ✅ | At each step: skip stale samples, average N FFT frames, place the result. About half a core for the whole app at 20 MS/s on the BB60D. |
| Stale-sample skip after a retune | 🧪 ✅ | Skips what the flowgraph has queued (read from the block counters) plus the radio's settle time. Test: a simulated radio with 20 ms retune latency shows ghosts with no settle time and none with 25 ms. Off air: no ghosts. |
| Stitching accuracy | ✅ | A 2-step FM-band sweep differs from single-LO references by a median of 0.95 dB, with 0 ghosts (BB60D, 2026-09-21; the window now sweeps the BB60D in the device). The HackRF check measures it at each settle time. A ghost counts only where the other step's reference has a signal at the same place in its step, as a stale frame would; bins over a quiet reference with no such source (weak stations fading between the sweeps) are reported apart. |
| Band presets | ✅ | Full range of the radio, FM 87.5-108, Japan 76-95, OIRT 65.8-74, VHF 30-300, or Custom. |
| Adjustable step bandwidth, FFT size (RBW), frames per step, settle time | ✅ | Step bandwidth restarts the radio. The rest re-plan the running sweep with no restart. |
| One-step sweeps rate-limited | ✅ | Capped at 30 per second. Without the cap, 40 MS/s on the BB60D ran 4000+ per second at 140% CPU. |
| HackRF DC-spike notch | 🧪 ✅ | ±30 kHz at each step's LO, filled in by straight-line interpolation. Off air, the spike stands 27-30 dB over the floor at the LO; the stitched sweep matched single-LO references to 0.3-0.8 dB. |
| **The HackRF's own sweep** | 🧪 ✅ | Its firmware's sweep mode through libhackrf (`hackrf_sweep.py`), with `hackrf_sweep`'s method: 20 MHz steps, interleaved, keeping the two 5 MHz quarters 2.5-7.5 MHz either side of each LO (no DC spike, no notch). Off air (2026-09-22): 1 MHz-6 GHz in 0.75 s (8 GHz/s, where LO hopping took tens of seconds), the FM band in 38 ms (capped at 30 a second) with the stations the BB60D finds. Levels in dBFS on the window's scale; up to 15 FFTs averaged per tuning from each block's second half (the floor's spread fell from about 30 dB to 6.6). Clipping counted per tuning, so the warning names the worst: at 40% a UHF TV tuning (LO 533.5 MHz) clipped 60-70% over the full range, the FM band 0.1-0.3%. The IQ stream's device is closed for it and opened again for Receive: 60-100 ms each way, RDS decoding after. 22% of a core for the sweep, about 50% with the window drawing 79k points. Not yet tried on a Mac. |
| HackRF sweep settle time 20 ms (LO hopping, now the USRP's) | ✅ | Measured off air, FM band in 2 steps at 20 MS/s: ghosts at 0 ms (557-563 bins) and at 5 ms in one run of two (34), none at 10, 20 or 40 ms. The default was an estimated 40 ms (92-98 ms a sweep); now 20 ms, twice the safe minimum (53 ms a sweep). |
| Station finder | 🧪 ✅ | Finds channels above a threshold (default 15 dB over the floor) on a 100 kHz raster; each must be a local maximum. Off air it found 22 stations across the FM band. It looks only between 65.8 and 108 MHz (OIRT, Japan and the rest), and measures the floor there too, however wide the sweep. |
| A new plan never shows the old plan's sweep | 🧪 | Fixed 2026-09-22. Re-planning the LO-hopping sweep kept its last complete sweep, so the window could pair the new frequencies with the old levels for a moment. `test_sweep.py` checks it. |
| Station list → Listen | ✅ | Double-click a station, or the spectrum, to switch to Receive tuned there. The list shows the RDS name of any station already listened to. |
| Pause / resume | 🧪 | |
| Peak hold | 🧪 | |
| Averaging over sweeps (Average dial in Sweep) | ⚠️ | |

Measured on the BB60D (FM band, 4096-point FFT, 16 frames per step):

| Step bandwidth | Steps | Sweep time | Rate | App CPU |
|---|---|---|---|---|
| 20 MS/s (default) | 2 × 16.8 MHz | ~90-105 ms | ~200-230 MHz/s | ~0.5 core |
| 40 MS/s | 1 × 24 MHz | 33 ms (capped) | ~600 MHz/s | ~1.1 cores (the BB60 driver's own DSP) |

The BB60D's own sweep, for comparison (off air, 2026-09-22):

| Span | RBW | Points | Sweep time | Rate | CPU |
|---|---|---|---|---|---|
| 9 kHz to 6 GHz | 300 kHz (auto) | 76,801 | 229-233 ms | 26 GHz/s | 92-97% of a core, nearly all the API's |
| 9 kHz to 6 GHz | 30 kHz to 1 MHz | 19k to 614k | 231-233 ms | 26 GHz/s | |
| FM band | 10 kHz | 4,199 | 12 ms, capped to 30 a second | | 28% |
| FM band, real time | 10 kHz (auto) | 4,199 + a 525 x 256 map | 30 frames a second | nothing over 307 us missed | 36-56% (API), ~46% (window) |

## Receive (IQ) - one station, narrower bandwidth

| Capability | Status | Notes |
|---|---|---|
| FM demodulation, 75 kHz deviation, 250 kHz MPX | 🧪 ✅ | Two-stage channeliser: an xlating filter to about 1 MS/s, then a resampling channel filter to 500 kS/s. The discriminator runs at 500 kS/s and its output is low-passed and halved to the 250 kS/s multiplex. Handles any IQ rate. Since the discriminator moved to 500 kS/s (2026-09-21), the synthetic test's stereo separation rose from 34 to 43 dB: less noise folds back into the multiplex. |
| IQ bandwidth selector (the flowgraph's bandwidth) | ✅ | In the Radio card. Per radio, e.g. BB60D 2.5/5/10 MS/s and HackRF 2-20 MS/s. A rate the computer can't use is listed greyed out, with why: the BB60D's 2.5 MS/s on a Mac (`test_gui.py` part 5). Changing it rebuilds the chain, keeping the Center if the tuner still fits. On the BB60D, 2.5, 5 and 10 MS/s all measured about 55% of a core with no drops and the same reception. |
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
| Automatic mono when there is no pilot | 🧪 ✅ | Stereo needs the 19 kHz pilot to stand 10 dB over the noise beside it (the 16-18 and 20-22 kHz guard band, scaled to the pilot band's width); it is let go below 6 dB. Fixed 2026-09-22: the toolkit's fixed level (1e-4) called every empty channel stereo, because noise alone reads about 6e-3 there, three times a real 9% pilot. On a BB60D band recording the old test said stereo on 34 of 35 channels; now exactly the five with a pilot (17-37 dB over the noise; empty channels -1 to +2 dB). Off air with a HackRF on the Mac mini: empty channels mono in every poll; 89.3, 90.1, 99.1 and 102.1 stereo (13-35 dB); 100.3, a weak one (RDS 72% good), read 8-12 dB and held stereo throughout. Tuned 100 kHz off a station, it reads mono, where the old test said stereo. `test_receive_chain.py`: noise, mono at SNR 5 and 35 dB never lock; stereo at SNR 35 and 10 dB always do. No real mono station has been tried. |
| De-emphasis 75 µs (RBDS) / 50 µs (RDS) | 🧪 | Follows the Standard selector, changed live. |
| RDS / RBDS decoding | 🧪 ✅ | The toolkit's `rds_core`, unchanged: PI (with call sign), PS, station name, RadioText, RT+ Now Playing, program type, TP/TA/TMC, clock. Off air: 93-100% good blocks on 99.1, 90.9, 89.3 and 95.1. That matches or beats the toolkit's offline decoder on the same recordings. |
| Signal readouts | ✅ | Channel power (dBFS), SNR over the floor, pilot and stereo state, RDS decode quality. |
| Multiplex (MPX) spectrum | 🧪 ✅ | 0-125 kHz: mono, 19 kHz pilot, 38 kHz stereo, 57 kHz RDS. |

## Views: bandwidth and amplitude on dials

| Capability | Status | Notes |
|---|---|---|
| RF spectrum + waterfall (both modes), MPX spectrum (Receive) | ✅ | pyqtgraph; colours from the theme. |
| **Span** dial - view bandwidth, log scale | 🧪 ✅ | From 50 kHz (5 kHz on the MPX view) up to the whole band or sweep, 6 GHz included (shown in GHz). Centred on the station in Receive. Fixed 2026-09-22: a saved span was clamped to the previous view's limit before the new extent was known. A full-range sweep opened at 1 GHz of 6, and a sweep could open at the Receive band's width. |
| **Ref level** and **Range** dials - amplitude | 🧪 | Top of the scale (-160…+20 dB) and dB from top to bottom (10-180 dB). The waterfall colours follow them. |
| **Average** dial | 🧪 | Frames averaged in Receive (tested), sweeps averaged in Sweep. |
| Every knob turns with the wheel under the pointer | 🧪 | No click first: Span ×1.25 a notch, Ref 2 dB, Range 5 dB, Average 1, Volume 2%, Step one setting; Shift for a fifth. The toolkit's click-to-move guard still holds sliders and dropdowns until clicked. |
| Knobs light orange under the pointer | 🧪 | The arc, rim and pointer turn the theme's on-air orange. Slate and Walnut add an orange glow round the knob, like the toolkit's lit tiles; Reading Room lifts it on a shadow instead. It fades in and out over 150 ms, and stays while you drag. On a real X server (Xvfb, 2026-09-22) the glow followed the pointer on Volume, Step and Span. Before this, a clicked knob kept its light for as long as it had focus, so Volume, the knob most often clicked, showed no change under the pointer. Focus now shows only as a ring when it came by Tab. |
| Plot controls at the right, readout at the left | ✅ | Under each plot, the Span, Ref level, Range and Average knobs, Peak hold, Waterfall and Full span sit at the right-hand end, with the pointer readout at the left. |
| Mouse zoom and pan along frequency, with the Span dial following | 🧪 | The wheel zoom is exercised by `test_gui.py` with real wheel events; the left-drag pan is not. In Sweep the view stops at 0 Hz and 6 GHz, on the spectrum and the waterfall alike: `test_gui.py` pans and zooms each past both ends (through the calls the mouse makes) and checks both stop there. The waterfall's plot now starts where the spectrum's does. Before, it started 35 px further left, so a full-span waterfall reached about 240 MHz below 0 Hz. |
| Wheel over the channel band: filter wider/narrower | 🧪 | Real wheel events in `test_gui.py`: 5 kHz a notch, 1 kHz with Shift; elsewhere the wheel still zooms. The band brightens under the pointer. |
| Middle-drag the channel band to tune | 🧪 | Real mouse events in `test_gui.py`: the tuner follows, snapped to the Step, stops at the band's edge, and the view does not move under the pointer. A recording's new part starts when the drag ends, not at every move. |
| Right-click the spectrum to put the tuner there | 🧪 | One item, **Tuner to 99.100 MHz**, naming the frequency under the pointer as Snap would round it, so it is plain where it lands before anything moves; Escape or a click away does nothing. Choosing it tunes exactly as a click does, so sweeping it moves the marker, the band and both sets of digits and receives nothing, and in Receive it stops at the edge of the band around the Center. The menu is built once per view and its text rewritten, since a new one per click would pile up under the view that owns it; only the RF spectrum asks for one (`tuner_menu=True`) - a tuner means nothing on the multiplex or audio views. |
| The channel band on the sweep too | 🧪 ✅ | The orange bar is drawn on the sweep, on the tuner and as wide as the channel filter, and the same middle-drag moves it (the wheel over it still changes the width). Nothing is retuned meanwhile - sweeping, `engine.tune` only records the frequency - so it is a way of saying where **Listen**, the Receive tab and **Real time** will start from. The real-time window follows only when the drag ends, not at every move. Drawn at least 7 px wide, since a 200 kHz channel on a 6 GHz sweep is a hundredth of a pixel and nothing the pointer could find; the frequency it stands for is unchanged. Seen on the BB60D over the FM band. |
| Cursor readout (frequency and level) | ⚠️ | In the plot's own unit (dBFS, or dBm in the BB60D's own sweep). |
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
| Recordings folder chooser | ⚠️ | Default: `recordings/` in the project. **Folder...** in the Record box or the Recordings tab. |
| One name for one press of Record | 🧪 | Every kind is named from one base (`fm-98.70MHz-20260922-181500-audio.wav`, `...-iq-band.cfile`), `-2` after the time for a second recording in the same second. Before 2026-09-22 each kind took its own time, and could land a second apart. |
| The station's RDS saved with the recording | 🧪 ✅ | `<name>-recording.json`: the files, start, length, retunes with their times, the station name, PI, call sign and PTY, and each RadioText and Now Playing with its time from the start. A RadioText is logged once it has held 2 s (it arrives a segment at a time); a name once it has held 8 s, since a station scrolling words through its PS names each fragment in turn. Off air, 90.1's PS scrolls ("Elevatio", then "n"...): the first try kept "n", now "Elevatio" is kept with its call sign WJOU. |

## Recordings tab

| Capability | Status | Notes |
|---|---|---|
| The folder's recordings, newest first (Ctrl+3) | 🧪 ✅ | One line for each press of Record: station, RDS name and call sign, date, length, kinds and size (`library.scan`). Older recordings whose kinds were named a second apart count as one. The list follows the folder as it changes. Listed the three BB60D recordings made on 2026-09-22 correctly. |
| The radio closes while in Recordings | 🧪 | Free for other programs; going back to Sweep or Receive opens it again, tuned where it was. The tab is remembered like the others; opened in Recordings, the app opens no radio. |
| IQ playback: spectrum, waterfall, multiplex, RDS and sound | 🧪 ✅ | Through Receive's chain, on the station it was made of; in a band recording, click any other station. The BB60D's 10 MS/s band recording (454 MB) and 500 kS/s channel recording played back headless with their spectra and RDS (WJOU, 0x6DEC). Nobody has listened yet. |
| WAV playback: the sound's spectrum and waterfall | 🧪 | Left and right together, 0-24 kHz (`dsp.WavChain`). The sound card sets the pace; its buffers are held to about 20 ms each, so the spectrum stays with the sound (GNU Radio's default would put it a third of a second ahead). The RadioText and Now Playing come from the recording's log. The synthetic station's 1 kHz and 2.5 kHz tones show at their frequencies. |
| Play, pause, loop, stop at the end | 🧪 | Pausing an IQ recording stops the flowgraph and starts it again as it stood, so RDS and the averages carry on and the file keeps its exact place; a WAV pauses on silence. The place is counted where samples leave the throttle, which runs at the real-time pace. |
| The overview strip: the whole recording, click or drag to jump | 🧪 ✅ | Time along it, frequency up it, in the theme's waterfall colours. IQ max-pooled over the band; WAV on a log scale, 50 Hz to 16 kHz. It samples the file (four FFTs a column), so the 454 MB band recording took 0.05 s and a WAV 0.2 s, in the background. |
| Choose which file plays | 🧪 | The whole band, then the channel, then the WAV; parts are listed with the station each was on. |
| Names learnt on playback | 🧪 ✅ | Playing an older recording's IQ keeps what it decodes on the recorded station (the name once it has held 8 s, the PI and call sign) in a `-recording.json` made for it. Learnt "Elevatio" / WJOU from the 90.1 recording. |
| Delete, Show in folder | 🧪 Delete, ⚠️ Show | Delete asks first, then removes every file of the recording, descriptions included. Show in folder opens the system's file manager, untested here. |

## App

| Capability | Status | Notes |
|---|---|---|
| One launch script: `./fm-receiver` | ✅ | Activates the `gnu` conda environment (override with `FMRX_CONDA_ENV`). |
| Settings remembered | 🧪 | `~/.config/fm-receiver/config.json`: radio, tab, frequency, gain and rates per radio, views, audio, recording, playback loop, window layout. |
| Three themes: Slate, Reading Room, Walnut | 🧪 | From the toolkit's design tokens. Picked with the toolkit's theme disc in the header (its ground, rule and trace colours; a click moves to the next). |
| Theme name as a tooltip | 🧪 | "Theme: Walnut" over the disc and over the word Themes. Tooltips now show while another window, such as the terminal, has the focus. Before, Qt showed them only in the active window, so this one never appeared then (seen on Xvfb). |
| The left column fits every theme | 🧪 | It is measured again on each theme change. In Walnut, 46 px of the Receive tab had been cut off under the spectrum. Walnut's digit entries (Center, Tuner, Channel filter, sweep bounds) use Libre Caslon Text, its reading face. Limelight, its nameplate face, is 29% wider than Slate's digits. |
| Keyboard shortcuts | ⚠️ | Ctrl+1/2/3 switch tab, Ctrl+Left/Right step, Ctrl+M mute, Ctrl+R record, Ctrl+Up/Down volume. The digit entries' own keys are tested. |
| Fast Sweep ⇄ Receive switching | ✅ | 0.2-0.3 s on the BB60D with LO hopping. The device stays open across the switch; reopening it took 1.6 s. With its own sweep, it takes 19 ms from Receive and 198 ms back, on the one open device. The station came back at the same level (-44.6 dBFS against -44.5) with the same PI. The module sets gain and attenuation afresh each time its stream starts, so nothing from the sweep leaks into the stream. |
| Clean shutdown on Ctrl+C / SIGTERM | ⚠️ | A Python timer keeps ticking so signals get through Qt's event loop (a toolkit rule). |
| Runs on Linux and on a Mac (Apple Silicon) | ✅ Linux, ✅ Mac | One conda environment for both, `environment.yml`: conda's solver gives the same versions for `linux-64` and `osx-arm64`. Where they differ, the code asks `sys.platform`: the BB60D library's name and place, where the SoapySDR module and libraries are found, and real time. On the Mac mini (M4, macOS 26.6, 2026-09-22): the environment made from `environment.yml` has the same versions as Linux; `run_all.py` passed 4/4; `./fm-receiver` found conda (Homebrew's Miniforge) and ran on its screen; the HackRF opened without root and `hw_hackrf_check.py` passed (RDS 302/304, stereo, record and play back, the clipping warning at 75%, the HackRF free after). The sound card played (silence, no underruns); not yet listened to. Two font fixes came from it: "Monospace" is a Linux alias (Qt picked American Typewriter on the Mac), so Menlo there; and the plot titles are in pixels, since 10 pt is 13 px on Linux but 10 on a Mac. |
| The BB60D on a Mac | ✅ within the limits below | Signal Hound's Mac library (5.0.11, built on an M4) links Homebrew's libusb. On the Mac mini the steps in [usage.md](usage.md#the-bb60d-on-a-mac) ran word for word from a clean state, with no `sudo`: Signal Hound's module (`SignalHound/soapy-bb60`, MIT) built into the conda environment, the library beside it, unaltered, and the module pointed at it through its rpath. `/usr/local/lib` does not work there: dyld (macOS 26) looks for the library's plain install name only in the current folder. With the device (2026-09-22) it opens without root, and two faults in the library showed. **`bbCloseDevice` crashes** (SIGTRAP), even straight after opening, so on a Mac the app keeps the device open until it quits (`bb60_source.KEEP_OPEN`); a closed engine hands it to the next radio in the process. **IQ at 2.5 MS/s and below is unreliable** (levels 1,000x too high, NaN, values to 1e37, differently each run; 5-40 MS/s sound every time), so 2.5 MS/s is shown greyed out there, with why in its tooltip (`test_gui.py` part 5; seen in the window with the BB60D on the Mac mini, 2026-09-22: a saved 2.5 gave 10 MS/s, receiving). No real time: the Mac library does sweeps and IQ only, so the box is greyed. `hw_bb60_check.py` passed there: stitching 0 ghosts; RDS on 90.1 (WJOU) 245/304; the Center moved with the station held; the full-range own sweep 233 ms (as on Linux); switches 59 ms to it, 209 ms back; recorded and played back; after the engine closed, a new radio reopened the kept device and decoded again. The window swept the full range and quit cleanly, the BB60D free after. |

## Tests

| Test | What it proves | Needs |
|---|---|---|
| `tools/tests/test_sweep.py` | Plan tiling, DC notch, station finder, a re-plan dropping the old sweep, the stale-sample skip against a slow simulated radio | nothing |
| `tools/tests/test_tuning.py` | The tuner's clamp (band edge, radio range, DC spike), the digit entry's carries, wheel and typing, the BB60 module's load leaving the other SoapySDR drivers loaded, and no ghost in the spectrum after the LO moves (with a check that it fails without the fix) | nothing (offscreen) |
| `tools/tests/test_receive_chain.py` | Synthetic stereo+RDS station through the real engine in both 38 kHz conventions: RDS, 34 dB separation, mute, WAV and IQ recordings with metadata; and stereo only for a pilot over the noise beside it: never on noise or a mono station, always on stereo at SNR 35 and 10 dB | nothing |
| `tools/tests/test_gui.py` | The window driven like a user: RDS on screen, mute, volume, dials, channel filter, the wheel and middle-drag on the channel band, the tuner's digits and roller, recording across a retune, the theme disc, Stop/Start, settings saved; sweep with a simulated radio (full range first, then the FM preset, the bounds' digits) → station list → double-click → Receive → the Radio card (Center, edge, DC spike, peak hold); a radio that sweeps itself, as the BB60D does (9 kHz to 6 GHz, RBW row, dBm, FM-only station list, RBW raised, real time: its button dropping the sweep to the 27 MHz window on the tuner, the window following a tuner put outside it, the density map placed by Ref level and Range, a wider span swept instead, and the span given back when the button is let out; the channel band on the sweep, middle-dragged to the station with nothing received meanwhile, and drawn wide enough to grab when zoomed out; the right-click menu, offering the snapped frequency, moving nothing until it is chosen, and then landing the tuner and its band; back and forth to Receive); the same where the library has no real time, as on a Mac; a receive rate this computer can't use (the BB60D's 2.5 MS/s on a Mac) greyed out with why, passed over by the keys and the wheel, and a saved choice of it giving the default; the knob glow with real pointer moves; the left column in every theme; tooltips with the window inactive | nothing (offscreen) |
| `tools/tests/test_recordings.py` | The recordings list from file names (an older recording's kinds a second apart, a WAV header cut short), the overview, the WAV source's seek, loop and end, the RDS log and its steady name; then the Recordings tab driven like a user on a recording the window made: the radio closed, the name listed, the band played with RDS, pause and resume keeping it, a jump on the strip, tuning in the band, stop at the end and loop, the WAV with its tones and logged RadioText, delete, back to Receive, reopened in Recordings | nothing (offscreen) |
| `tools/tests/hw_bb60_check.py` | Off air: the strongest FM stations from its own sweep, RDS on the strongest station that has it, record and play back, moving the Center, its own sweep (9 kHz to 6 GHz speed, stations, FM band at 10 kHz, real time with its frame rate and the density map's orientation, the gain slider, AGC against the slider's default, back to Receive with the same PI, both switches timed), and another program opening it after close. It reads the device's health while receiving and sweeping. It receives at the lowest rate offered (2.5 MS/s; 5 on a Mac) on the first strong station whose RDS decodes cleanly. On a Mac: no real time, and in place of another program, a new radio in the same process gets the kept device back and decodes again | BB60D + antenna |
| `tools/tests/hw_hackrf_check.py` | Off air: LO-hopping sweep ghosts at five settle times, RDS at four gains, the DC spike, record/Center/playback, its own sweep (the FM band's stations, the full range's time, clipping per tuning, Receive after it with the same PI, both switches timed), another program opening the HackRF after close and after the window's Stop, and the window's clipped readout and warning in Receive and in a sweep (the gain raised until it clips: 60% on the Linux bench, 75% on the Mac mini) | HackRF + antenna, not in use elsewhere |
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
- IQ files are read as cf32 only (this app's own and the toolkit's captures).
- A WAV plays at 48 kHz only (16-bit or float), which is what Record makes.
- Sweep sample rates are limited by the radio and by Python. The BB60D at
  40 MS/s uses about a core, mostly in the vendor driver.
- **A full-range sweep's waterfall is coarse when zoomed.** Each row is
  pooled to 4,096 columns, about 1.5 MHz each over 6 GHz, so zooming into
  one band shows wide blocks. The spectrum trace keeps every point.
- The BB60D's own sweep costs about a core while it sweeps 9 kHz to 6 GHz,
  nearly all of it the vendor API's processing.

## Ideas / backlog

Planned work, split into phases, is in [roadmap.md](roadmap.md).

- 💡 A scan/seek button in Receive: step to the next station above a threshold.
- 💡 A memory list of favourite stations with their RDS names.
- 💡 Scheduled recordings, and splitting long recordings by size.
- 💡 Decoding HD Radio (IBOC) - its sidebands are visible either side of 99.1.
- 💡 An audio spectrum view while receiving (a WAV plays with one), and an FM deviation / modulation meter.
- 💡 A sweep export (CSV of frequency and level) and saved sweep screenshots.
