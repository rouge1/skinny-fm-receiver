# Roadmap

Planned changes, grouped into phases. When an item ships, tick it here and
add it to [capabilities.md](capabilities.md) with its status mark. Record
answers and measurements next to the item that raised them.

## Round 2: tuning, the radio card, and view polish (requested 2026-09-21)

Nine requests, numbered as they were asked. They fall into three phases.
Each phase can be used on its own:

| Phase | Items | Size | Depends on |
|---|---|---|---|
| 1. Quick fixes | 1, 2, 3, 6, 8, 9a | small | nothing |
| 2. Digit tuner widget | 5, 7 (the number field) | medium | nothing |
| 3. Radio card and spectrum mouse | 4, 7 (the wheel), 9b | medium-large | phase 2 |

**Status:** all three phases shipped on 2026-09-21. All tests pass
(`run_all.py --hw`: 5/5), including the new `test_tuning.py` and a
Center-move step in the BB60D check. The waterfall palettes (item 1) stay
on A, decided 2026-09-22.

Phase 3 depends on phase 2 for two reasons. The radio card's Center field
uses phase 2's widget. And phase 3 changes the tuning model: the center
becomes yours to set, instead of the app placing it. Dragging the filter
only makes sense under that model.

---

### Phase 1: quick fixes

- [x] **1. Waterfall palettes for Walnut and Reading Room.** Today Slate
  and Reading Room both use `inferno` and Walnut uses `magma`
  (`widgets.py` `restyle`). Build each palette from the theme's own
  colours so the waterfall matches the window:
  - *Reading Room*, "ink on paper": the floor is the paper (`well`), and
    stronger signals go through the pulse blue to the iron-gall ink.
    Signals come out dark on light, the way the rest of that theme reads.
  - *Walnut*, "dial glow": the floor is the wood (`well`), and stronger
    signals go through the tan (`trace`) and the valve orange (`live`)
    to cream (`ink`).
  - *Slate*, "ice": the same idea for Slate, so all three match: slate
    blue rising to the trace's pale ice, then white.

  **Shipped** as `widgets.WATERFALL`: stops given as token names (read at
  draw time) or hex, blended in Oklab. Candidates were rendered on a real
  FM-band sweep from the BB60D, with the old colormap for comparison:

  | Theme | A (in use) | B | C |
  |---|---|---|---|
  | Slate | Ice | Ice and ember (ice, then orange at the top) | Ember (slate to orange to cream) |
  | Reading Room | Ink on paper (ultramarine to ink) | Teal ink | Sepia and rust |
  | Walnut | Dial glow (tan to cream) | Valve (red-brown, orange, amber) | Amber |

  **Kept on A** (2026-09-22), then **Slate switched to B**, Ice and ember
  (2026-09-22); Reading Room and Walnut stay on A. To switch later, name
  the letter. B and C are these stops, one line each in
  `WATERFALL`:

  ```python
  'slate':        B ((0.0, 'well'), (0.4, '#1d3a50'), (0.75, 'trace'), (1.0, 'live'))
                  C ((0.0, 'well'), (0.3, 'rule'), (0.7, 'live'), (1.0, '#fff3dc'))
  'reading-room': B ((0.0, 'well'), (0.35, '#c7dcd8'), (0.7, 'trace'), (1.0, 'ink'))
                  C ((0.0, 'well'), (0.35, '#e3d3b8'), (0.7, 'live'), (1.0, '#3a1606'))
  'walnut':       B ((0.0, 'well'), (0.35, '#5a2412'), (0.65, 'live'), (0.85, 'warn'), (1.0, 'ink'))
                  C ((0.0, 'well'), (0.45, '#6b4a1a'), (0.8, 'warn'), (1.0, '#fff6e0'))
  ```
- [x] **2. Max hold keeps ghosts after a tune.** Cause, found in the code:
  when a tune moves the LO, `set_extent` clears the peak trace. But the
  RF probe's running average (`rf_probe`) is not reset, and for a few
  milliseconds the radio still delivers samples from the old LO. The
  first frames after the tune therefore show the old spectrum at the new
  frequencies, and max hold keeps them. Fix: when the LO moves, reset the
  probe, drop frames until the radio has settled, and clear max hold. A
  test should check that no peak survives a retune.

  **Shipped.** There were two ghosts, not one. The one you saw was most
  likely the MPX view's peak hold, which kept the last channel's
  multiplex after every tune. The other was the RF view after an LO move.
  Now `ReceiveChain.discard_stale` has the spectrum probes and the RDS
  decoder drop every frame made from samples queued before a retune. It
  counts them from the block counters, as the sweep does. Peak hold then
  starts again: the MPX view's on every tune, the RF view's when the LO
  moves. `test_tuning.py` uses a radio that is 75 ms slow to retune. After
  the move the old tone's spot reads 7 dB over the floor (noise); with the
  fix switched off it reads 85 dB.
- [x] **3. Theme picker as the toolkit's disc.** Replace the Theme combo
  with `ThemeDisc` from `/data/python/SDR/RFbenchToolkit.py`: the "Themes"
  label, then a disc in the theme's ground, ringed in its rule, with a dot
  in its trace colour. A click moves to the next theme; the tooltip gives
  the theme's name. Copy it into `style.py` or `widgets.py`, marked
  "FM receiver", with its paint guarded. **Shipped** in `widgets.py`.
- [x] **6. IQ bandwidth: 2.5, 5 or 10 MS/s.** Measured on the BB60D on
  2026-09-21 at 99.1 MHz. Each rate ran for 8 s after 3 s to settle, and
  10 MS/s was run twice:

  | Rate | CPU (one core) | Samples dropped | Pilot | RDS blocks good | Band IQ recording |
  |---|---|---|---|---|---|
  | 2.5 MS/s | 59–61% | 0 | locked | 100% | 20 MB/s (1.2 GB/min) |
  | 5 MS/s | 52–55% | 0 | locked | 97–100% | 40 MB/s (2.4 GB/min) |
  | 10 MS/s | 54–58% | 0 | locked | 91–100% | 80 MB/s (4.8 GB/min) |

  The rates made no difference to CPU or reception. The RDS spread is
  run-to-run noise: 10 MS/s scored 100% on its first run. The CPU is flat
  for two reasons. The BB60's API decimates from its 80 MS/s ADC at every
  rate. And everything after the channel filter runs at 250 kHz whatever
  the rate.
  - Pros of 10 MS/s: about 7.5 MHz of the band is on screen (75% usable),
    against about 1.9 MHz at 2.5 MS/s. You can tune further before the
    LO has to move, and each LO move is a short gap. Phase 3's drag tuning
    also gets more room.
  - Cons of 10 MS/s: Band IQ recordings are four times larger. The RF
    view's bins are four times wider (2.4 kHz against 610 Hz with a
    4096-point FFT), which is still far finer than an FM channel.
  - HackRF is different: more USB traffic and CPU, and more strong
    stations reaching its 8-bit ADC. This finding is for the BB60D only.

  **Plan:** make 10 MS/s the BB60D's default receive rate. Mention in
  usage.md that 2.5 MS/s is the choice for long Band IQ recordings.
  **Shipped.** A rate you already saved is still used.
- [x] **8. "Snap to 100 kHz".** What it does now: with it on, a click
  (or double-click) on the spectrum or waterfall tunes to the nearest
  100 kHz channel. With it off, a click tunes to exactly where you
  clicked. It has no effect on the spin box or the < > buttons.
  **Proposal:** make it "Snap to step", so it follows the Step setting
  (a 200 kHz raster in the Americas, 100 kHz in Europe), and have it
  apply to dragging (9b) as well as clicks. **Shipped.**
- [x] **9a. Rename "Station (MHz)" to "Tuner".** Change the labels,
  tooltips, usage.md and capabilities.md. `station_hz` stays in the code
  and the recording metadata, because files already written use that
  name. **Shipped.**

### Phase 2: the digit tuner

- [x] **5. Tuner as hover-and-wheel digits, with a step roller.**
  Why there are < > buttons as well as the spin box arrows: the arrows
  always move 0.1 MHz, while < > move by the Step setting (10, 50, 100 or
  200 kHz) and round onto that raster. At the default 100 kHz step they
  do the same thing, so the question is fair. The new widget:
  - The frequency drawn as digits, for example `0098.700 MHz`, with the
    leading zeros dimmed.
  - Hovering over a digit highlights it. The wheel adds or subtracts
    that digit's place value, and the result carries naturally:
    - the tens digit of 90 MHz, wheel up → 100 MHz;
    - the ones digit of 99 MHz, wheel up → 100 MHz.
  - The value is clamped to the attached radio's range: the BB60D covers
    9 kHz to 6 GHz, the HackRF 1 MHz to 6 GHz, and a USRP whatever its
    daughterboard reports.
  - A click lets you type a frequency; Up and Down step the hovered
    digit.
  - A **step roller** to the right of the digits replaces < and >: a
    ▲/▼ pair. The wheel over it moves by one Step, and so do its clicks.
    Ctrl+Left and Ctrl+Right stay.
  - Resolution is 1 kHz (three decimals).

  **Shipped** as `widgets.DigitEntry` and `widgets.StepRoller`. The digit
  entry replaced the big frequency label as well as the spin box. It
  always takes the wheel, so rolling over it never scrolls the left
  column.
- [x] **7a. Channel filter as a number.** The same widget in kHz, for
  example `200 kHz`, with the wheel over its digits and no arrows. It
  replaces the slider. Range 60–236 kHz, as now. **Shipped.**

### Phase 3: the radio card and the spectrum mouse

- [x] **4. Radio card above the Tuner.** A card that controls the radio
  itself:
  - **Center (MHz)**, using the same digit widget. This is the SDR's LO.
  - **IQ bandwidth**, moved here from the Receive tab.
  - A yellow dashed line at the center frequency on the spectrum and the
    waterfall. Its colour comes from the theme's `warn` token and is read
    at draw time. It is dashed so it cannot be mistaken for the orange
    tuner marker.
  - The tuner lives inside the usable band around the center. Changing
    the center keeps the tuner where it is if it still fits, and
    otherwise pulls it inside.
  - On the HackRF the tuner is kept at least 100 kHz from the center
    because of the DC spike. The BB60D samples at IF and has no spike,
    so it has no such limit.

  **Shipped.** The tuner's reach is shaded on the spectrum, with a dotted
  line at each limit. **Center on tuner** puts the LO 300 kHz below the
  tuner. The old "RF gain" group, which was also titled "Radio", is now
  titled "RF gain". Off air on the BB60D: moving the Center 700 kHz past
  99.1 kept the station decoding (same PI, 211/212 blocks good).
- [x] **7b. Wheel over the orange filter changes its width.** Over the
  filter the wheel narrows or widens it, 5 kHz per notch, or 1 kHz with
  Shift. Everywhere else the wheel zooms the view as it does now.
  **Shipped** (`widgets.ChannelBand`); the band brightens under the
  pointer and has a tooltip.
- [x] **9b. Middle-button drag on the filter to tune.** Press the middle
  button on the orange filter and drag: the tuner follows the pointer,
  confined to the usable band around the center, so the LO never moves
  during a drag. It snaps to the step if Snap is on. Elsewhere a
  middle-button drag pans the view as it does now. The channel audio
  follows in real time, because a channel move is only a new offset on
  the running chain with no restart.

  **Shipped.** Two details. pyqtgraph gives the drag to the band only
  because the band claims the middle button on hover; without that, the
  view under it takes every button to pan. And the view is not re-centred
  while dragging, or it would slide under the pointer. A channel IQ
  recording starts its new part when the drag ends.

---

### Decisions (2026-09-21)

1. **Tuning past the edge of the band**: the tuner stops at the edge, for
   the digits, the roller, the keyboard, typing, clicks and drags. Only
   moving the Center moves the band. (A jump from outside Receive - a
   station picked in the Sweep list, or `--freq` - still places the
   center itself, since there is no band yet to stay inside.)
2. **Waterfall palettes**: built from each theme's own colours, Slate
   included so all three match. Pick from the rendered candidates.
3. **Snap**: "Snap to step".
4. **Tuner resolution**: 1 kHz.
5. **Default IQ bandwidth on the BB60D**: 10 MS/s.

## Round 3: markers, boxes, knobs (requested 2026-09-21)

All shipped the same day. `run_all.py`: 4/4. The BB60D check passed twice
after one run flagged a single transient bin; see capabilities.md,
*Known limitations*.

- [x] **1. Walnut: the center line and the tuner marker in different
  colours.** Walnut's `warn` amber sat too near its valve-orange tuner
  marker and its tan trace. The center line is now verdigris (`#7cc7bd`).
  It was chosen over sage (`good`) and a lemon yellow, all rendered side by
  side on a real FM band. The colours are in `widgets.MARKERS`.
- [x] **2. The center line fades while the Center moves.** It fades out in
  120 ms as the Center changes and comes back 0.6 s after it stops.
- [x] **3. The tuner marker is hidden unless tuning.** It fades in on any
  tune, stays while the middle button holds the channel band, and fades
  out 0.9 s after the tuner stops. In Sweep it is always shown, since it
  marks your pick there.
  - The band item is never sent the middle-button press, so the spectrum
    view watches its scene for the button going down over the band and
    coming back up.
- [x] **4. Three boxes in the Receive tab.**
  - Radio: Center and Center on tuner, Tuner range, then IQ bandwidth. IQ
    bandwidth wasn't in the requested order, so it went last.
  - Tuner: the tuner, its ▲/▼ and the Step knob (10/50/100/200 kHz), then
    the channel filter with its own ▲/▼.
  - RDS: station, standard, stereo and snap to step, Clear RDS, signal,
    audio, then the RDS details, which moved in from the bottom right.
  - **Decisions:** the channel filter goes up to **400 kHz** (asked for as
    "2 MB"; 400 kHz was picked from 2 MHz, 400 kHz or 236 kHz), and the RDS
    details **move into the box**.
  - 400 kHz needed a wider channel. The channel and the discriminator now
    run at 500 kS/s, then the discriminator's output is low-passed and
    halved to the 250 kS/s multiplex. There is no rebuild when the filter
    widens.
  - Side effects: stereo separation rose from 34 to 43 dB in the synthetic
    test, and channel IQ recordings are now 500 kS/s (4 MB/s).
- [x] **5. Every knob turns with the wheel under the pointer.**
  - The toolkit's click-to-move guard held the wheel back from any
    unclicked dial. The knobs now opt out of it with a `wheel_on_hover`
    property. Sliders and dropdowns keep the guard.
  - QDial's own wheel moved 3 of its 1000 steps a notch, too little to see,
    so each knob now sets its own step per notch.
  - A ring lights round the knob under the pointer.

## HackRF off air (2026-09-22)

The first run on a real HackRF One, with `tools/tests/hw_hackrf_check.py`.
Another session, ble-scanner, was waiting to use the same HackRF, so it was
handed over (and checked free with `hackrf_info`) as soon as the testing was
done.

- [x] **It works.** The auto-detect finds it. 89.3 plays in stereo with RDS
  at 98-99% of blocks good. A recording plays back with the same PI, and
  moving the Center keeps the station.
- [x] **Fixed: Stop didn't free the HackRF after a sweep.** The engine keeps
  old chains referenced on purpose, and the old sweep block still held the
  radio's block, so the device stayed open to other programs until the app
  quit. Switching radios had the same leak. Stopped sweeps now let go of
  the radio (`sweep_sink.detach`). This is checked off air (another program
  opens the HackRF after Stop while the app runs on) and offline
  (`test_tuning.py`).
- [x] **Sweep settle 40 ms → 20 ms.** Ghosts at 0 ms, and at 5 ms in one of
  two runs; none from 10 ms. The default is now twice that minimum, and a
  sweep takes 53 ms instead of 95.
- [x] **Gain default 40% confirmed.** 54% and above put the channel at
  +2 dBFS and lost RDS. The toolkit's 54 dB suggestion came from another
  antenna and doesn't hold here.
- [x] **New: an overload warning on the HackRF.** It has no overload flag,
  so the app counts the samples at full scale. Over 1% clipped, the status
  line warns and gives the share.
- 💡 The clipping probe runs in Receive only. A sweep at too much gain
  clips just as well, so a sweep-side count would be the next step.
- 💡 At 10 MS/s, 40% gain already clips 4%. A per-rate default gain for
  the HackRF, or a note in the Radio card, may be worth it.
- [x] **HackRF gain split: LNA before VGA?** Measured 2026-09-22: no; see
  *HackRF: its own sweep, and the gain split*. ble-scanner measured this at
  2.4 GHz for Bluetooth on 2026-09-22. It uses the same toolkit gain plan
  (LNA share 0.45).
  - With LNA + VGA fixed at 62 dB and the same clipping, moving 8 dB from
    the VGA to the LNA (LNA 32 / VGA 30) raised the signals about 2-4 dB
    over an unchanged noise floor, with +2.3 dB SNR, and decoded 27% more
    packets.
  - It now uses an LNA share of 0.55, with a default of 60% = AMP 14 /
    LNA 32 / VGA 30.
  - FM may differ: strong stations make LNA gain an intermodulation risk.
    Here the 40% → 47% cliff is just the LNA stepping from 16 to 24 dB at
    VGA 24. The ADC fills at about 48 dB of LNA + VGA, however it's split.
  - To decide: on the HackRF, compare equal totals with more LNA and less
    VGA, by RDS decode rate and SNR at the same clipped share (a few
    minutes of radio time). Check with ble-scanner first, since it shares
    the HackRF.
- 💡 **Show the HackRF's clipped share at all times**, not only above 1%,
  as ble-scanner does ("clipped 0.13%").
  - Its method, for agreement: full scale is |I| or |Q| ≥ 125/127. It
    tests every 4th sample, and smooths per 16 ms chunk with
    0.95·old + 0.05·new, which covers about 0.3 s.
  - Its guide is to keep it under about 1%. At 2.4 GHz it saw 0.1-0.6% at
    its best gain and 2-8% where packets were lost, which agrees with our
    1% warning.
  - Ours tests 4096-sample frames 30 times a second (6% of samples at
    2 MS/s, 1% at 10 MS/s). That is enough for FM's steady stations, but
    might miss clipping in short bursts such as Wi-Fi.
  - It also found the peak level useless as a guide: brief bursts put it
    at +3 dBFS even at the best gain.

## Round 4: knob glow, layout, full-range sweep (requested 2026-09-22)

All shipped the same day. `run_all.py`: 4/4. The BB60D check passed off air
with two new stages (its own sweep, and letting go of the device). On later
runs its older stitching stage flagged 2-15 bins that came and went, at
either settle time; stages 2-6 passed on the final code. Fixed
2026-09-22: see *Loose ends* below.

- [x] **1. Knobs light orange under the pointer.**
  - The arc, rim and pointer turn the on-air orange. Slate and Walnut add
    an orange glow round the knob, as the toolkit's tiles are lit (a
    `QGraphicsDropShadowEffect` with no offset). Reading Room lifts it on
    a shadow (offset down, in `shade`), because a glow barely shows on
    paper. It fades over 150 ms, the tiles' lift time.
  - **Why Volume didn't light:** a clicked knob kept its light for as long
    as it had focus, so hovering it again changed nothing, and Volume is
    the knob most often clicked. On a real X server (Xvfb) its hover was
    otherwise the same as the others'. The light now follows the pointer
    and drags only; focus from Tab shows as a ring.
- [x] **2. Walnut's digit entries in its reading face.** Limelight, 29%
  wider than Slate's digits, had pushed 46 px of the Receive tab under the
  spectrum. Center, Tuner, Channel filter and the sweep bounds now use Libre
  Caslon Text (`widgets.DIGIT_FACE`). The left column is also measured again
  on every theme change, so no theme can cut it off.
- [x] **3. The theme's name as a tooltip.** The disc already had one, but
  Qt shows tooltips only in the active window, and the terminal usually
  has the focus. The window now shows them regardless
  (`WA_AlwaysShowToolTips`), and the word Themes carries the tooltip too.
- [x] **4-5. Plot controls at the right.** Under the RF spectrum and under
  the multiplex, the knobs, Peak hold, Waterfall and Full span sit at the
  right-hand end, with the pointer readout at the left. **Decision:** the
  same row pushed right, picked over a column beside each plot.
- [x] **6. Sweep the radio's full range; bounds as controls.**
  - **The BB60D sweeps itself** (`bb60_sweep`, Signal Hound's API through
    ctypes). It covers 9 kHz to 6 GHz in 229-233 ms, where LO hopping
    manages 200-600 MHz/s. It borrows the device the SoapySDR module
    opened; both share one `libbb_api` in the process. Switching takes
    19 ms from Receive and 198 ms back, and nothing leaks into the IQ
    stream: the same level and PI came back afterwards.
  - Start and Stop as digit entries, to the kHz, kept inside the radio's
    range. The first preset, *Full range of the radio*, is now the default
    and follows the radio when you switch.
  - An RBW choice (Auto, or 1 kHz to 1 MHz) replaces the step bandwidth,
    FFT, frames and settle rows for the BB60D. Levels are in dBm there.
  - Stations are listed only between 65.8 and 108 MHz.
  - Its sweep is capped at 30 a second: the API's processing runs on this
    computer, and the FM band uncapped used 87% of a core.
  - Found on the way: re-planning the hopping sweep kept the old plan's
    last sweep, and the Span dial clamped a saved span to the previous
    view's limit. Both fixed.
- [x] **Native HackRF sweep** (`hackrf_sweep`'s firmware mode): its full
  range by LO hopping is about 400 steps, tens of seconds a sweep.
  **Shipped 2026-09-22**: see *HackRF: its own sweep, and the gain split*.
- 💡 **A waterfall at the zoomed view's resolution.** Over 6 GHz each
  waterfall column is about 1.5 MHz, so zooming into a band shows blocks.
  Rows could be pooled to the visible range, at the cost of history rows
  not lining up after a zoom.
- 💡 Station finding beyond FM (TV, airband, ISM) would need other
  detectors and a way to listen to them; out of scope for an FM receiver.

### From Signal Hound's API reference (2026-09-22)

Read from the links in [google_bb60d.md](google_bb60d.md). Every constant
`bb60_sweep` uses matched the header. The minimum sweep span was raised
from 100 kHz to its suggested 200 kHz. Its other leads:

- [x] **Real-time mode** (`BB_REAL_TIME`), shipped the same day as a
  **Real time** box in the Sweep tab, for spans up to 27 MHz. Off air on the
  FM band: 30 frames a second, nothing over 307 us missed at 10 kHz RBW,
  and about 46% of a core for the whole window. The density map is drawn
  behind the trace, and checked the right way up: its highest hit at
  89.3 MHz matched the trace's peak to 0.1 dB.
  - [x] It became a **button** that stays down, beside the tuner the Sweep
    tab now shows: pressing it drops the sweep to its 27 MHz window on the
    tuner, and letting it out gives the span back. Placing the tuner is
    how you say where to watch, so a span too wide to watch is no longer
    a dead end. The orange channel band is drawn on the sweep as well, so
    the tuner can be placed by middle-dragging it to a station - nothing
    is retuned there, it only says where Listen, Receive and Real time
    will start.
  - Found by probing: row 0 of the map is the bottom of the scale, and the
    reference level places the map even with the gain set by hand.
  - [x] The persistence frame (`alphaFrame`, hits fading from 1 to 0) is
    fetched but not drawn yet. Blending it in would show where a burst
    just was. **Shipped 2026-09-22**: see *Real-time polish*.
  - [x] The waterfall could be drawn from real-time frames at full rate
    too, so a burst leaves its mark there as well. **Shipped 2026-09-22**.
- 💡 **Auto gain by reference level**, which Signal Hound recommends.
  Gain and attenuation go on auto, with the reference level about 5 dB
  above the strongest input expected. Now the RF gain slider sets them by
  hand, as it does for the IQ stream, and the API then ignores the
  reference level. It could be tied to the view's Ref level, or be an
  Auto position on the slider.
- 💡 **Device health** from `bbGetDeviceDiagnostics`: temperature, USB
  voltage and current. Below 4.4 V the measurements may be off, which is
  worth a warning. First check it can be called while the SoapySDR
  module's stream runs.
- 💡 **Sweep time** (0.001-0.1 s, fixed at 0.001 now). A longer dwell per
  frequency catches intermittent signals, at the cost of sweep speed.
- 💡 **Spur rejection** (`BB_SPUR_REJECT`), for CW signals.
- The API tunes up to 6.4 GHz, past the BB60D's specified 6 GHz. The
  sweep stops at 6 GHz, as asked.

## Round 5: the Recordings tab (requested 2026-09-22)

A tab that lists the recordings and plays them back, looking like the
spectrum and waterfall, with the sound. The design was agreed before it
was built, and every question was answered with the recommendation:

- [x] **A third tab, Recordings** (Ctrl+3), one line per press of Record.
  The radio closes while it is open, and opens again on the way out.
- [x] **IQ plays as in Receive**: spectrum, waterfall, multiplex, RDS and
  the sound made from it; in a band recording, click another station.
- [x] **A WAV shows its sound's spectrum and waterfall** (question 1: the
  spectrum, not a waveform).
- [x] **Both kept: the IQ plays, the WAV is the second choice** (question 2).
- [x] **Stop at the end, Loop as an option** (question 3). It had always
  looped.
- [x] **Record saves the RDS** (question 4): the name, PI, call sign, and the
  RadioText and Now Playing with their times, in `-recording.json`. Playing
  an older recording's IQ fills in what it decodes.
- [x] **The overview strip** (question 5): the whole recording, time along it
  and frequency up it, and the place to click to jump.

Found on the way: a station that scrolls words through its PS has no one
name. Its decoder's most common PS changes with each fragment, and the
first build kept "n" for 90.1. Now a name must hold for 8 s to be kept.

Playback was listened to on a sound card (2026-09-22). Still to do: try
Show in folder on both systems.

## Linux and Mac (requested 2026-09-22)

The app is to run on a Mac mini (M4, 24 GB) as well as on Linux, from the
same checkout.

- [x] **One environment for both**: `environment.yml`, the tested
  versions. conda's solver gives the same set for `linux-64` and
  `osx-arm64`.
- [x] **The BB60D's library by platform**: `libbb_api.so.5` on Linux,
  `libbb_api.5.dylib` on a Mac (the install name of Signal Hound's Mac
  build), in `bb60_sweep` and the hardware test.
- [x] **The SoapySDR module and libraries on a Mac**: Homebrew's module
  folder, and dyld's list of loaded libraries in place of `/proc` (for the
  ADC overload count).
- [x] **Signal Hound's library in the environment on a Mac**, not
  `/usr/local/lib`: dyld on macOS 26 doesn't look there for a plain name.
  The module is pointed at it by rpath. No `sudo` needed.
- [x] **Real time greyed out on a Mac**: Signal Hound's Mac library does
  sweeps and IQ only. Tested with the flag forced off (`test_gui.py`
  part 4).
- [x] **Signal Hound's library is not committed.** Their licence (the SDK's
  `LICENSE.rtf`, section 1.C-E) allows copies to be distributed, but not to
  people who don't own the hardware, and this repository is public. Setup
  steps are in [usage.md](usage.md#setting-up) instead.
- [x] **The BB60D kept open on a Mac**: Signal Hound's Mac library
  (5.0.11) crashes in `bbCloseDevice` (SIGTRAP), even straight after
  opening, so the app opens the device once, marks it closed for SoapySDR
  (whose `close()` then skips the module's destructor), and reuses it until
  it quits (`bb60_source.KEEP_OPEN`). Stop no longer frees it for other
  programs there.
- [x] **No IQ below 5 MS/s from the BB60D on a Mac**: the Mac library's IQ
  at 2.5 MS/s and below comes out wrong, differently each time. 2.5 MS/s
  is listed greyed out, with the reason as its tooltip
  (`Radio.unavailable_rates`), rather than left out.
- [ ] **Tell Signal Hound** (support@signalhound.com) about both faults in
  the Mac library, with the steps that show them: open then close; and
  IQ at 2.5, 1.25 and 0.625 MS/s against 5 and up. When a fixed library
  comes out, drop `KEEP_OPEN` and the 5 MS/s floor for it.
- [x] **Run it on the Mac**: `run_all.py`, `--hackrf` and `--hw` all
  passed on the Mac mini (2026-09-22), after the HackRF's clipping check
  learned to raise the gain until it clips and the BB60D's to pick a
  station with clean RDS. Left: listening to it there. Watch for the sound device,
  the fonts' metrics on a Retina screen (the left column is measured, so
  it should fit), and whether the BB60D opens without root. Signal Hound's
  README says it may need root, but their text is copied from Linux.
  **Done 2026-09-22:** it ran on the Mac mini, and the BB60D opened
  without root.

## Loose ends (2026-09-22)

- [x] **Waterfall palettes stay on A**, and the Mac run (no root needed) and
  listening to the Recordings tab's playback are recorded above.
- [x] **The flaky stitching test.** It compared a sweep with references
  taken seconds apart, so a weak station fading between them counted as a
  ghost (at 93.6, 102.5 and 107.5 MHz, none with a signal a step away to be
  a copy of).
  - The BB60D check no longer stitches: the window has swept the BB60D in
    the device since Round 4, so the stage tested a path it doesn't use.
    Stage 1 now lists the stations from ten of its own FM-band sweeps.
  - The HackRF check, where hopping is real, counts a ghost only where the
    other step's reference has a signal at the same place in its step, and
    prints the rest apart. Its references now cover each step whole, past
    108 MHz.

## Round 6: clipping, auto gain, device health (requested 2026-09-22)

Four items, in two pairs by radio. Each pair ends with one hardware run, so
the HackRF is borrowed from ble-scanner once.

| Phase | Items | Radio time | Size |
|---|---|---|---|
| 1. HackRF clipping | 1, 2 | HackRF, ~5 min (+ the gain split, if wanted) | small |
| 2. BB60D health | 4 | BB60D, a probe, then the check | small |
| 3. BB60D auto gain | 3 | BB60D | medium |

Health goes before auto gain because it starts with a yes/no probe, and
because auto gain needs a probe of its own that decides how much of it
there is.

### Phase 1: the HackRF's clipping, always and in sweeps

- [x] **1. Show the clipped share at all times.** For a radio with
  `clip_warn` (the HackRF), the status line reads
  "HackRF One - Receiving at 2 MS/s - clipped 0.13%" in Receive and Sweep.
  - Full scale moves from `|I| or |Q| ≥ 0.98` to ble-scanner's 125/127
    (0.984), so the two apps agree.
  - Smoothed as ble-scanner does, about 0.3 s: `0.95·old + 0.05·new` per
    16 ms of samples becomes one exponential step per 400 ms status tick,
    weighted by the samples the probe saw (`clip_probe.take()` returns
    them too).
  - Colours: `good` under 0.3%, `warn` from 0.3% to 1%, and over 1% the
    existing "Input overloaded - turn the RF gain down (4.1% of samples
    clipped)" in `bad`. The 0.3% comes from ble-scanner's 0.1-0.6% at its
    best gain.
  - Code: `dsp.clip_probe` (the threshold, and returning `(hit, n)`), and
    `app._check_health` (the smoothing and the text).
- [x] **2. Count clipping in the LO-hopping sweep.** `sweep.sweep_sink`
  counts full-scale samples in each step's frames, just before
  `power_spectrum`: only the samples that were measured, not the settle
  or the stale backlog. It exposes them through `take_clipped()` in the
  same form as `clip_probe`, so `_check_health` reads whichever is running.
  - Per step as well, so the warning can name where: "clipped 3.2% at
    98.6 MHz" (the worst step's centre), since in a sweep one strong
    station usually does it.
  - The BB60D's own sweep is unchanged: it has an overload flag, already
    counted (`sweeper.overflows`).
- Tests: `test_tuning.py` or `test_sweep.py` feed a signal source with a
  known share over full scale through `clip_probe` and `sweep_sink`, and
  check the share and the step. `test_gui.py` checks the status text at
  0, 0.5% and 5%.
- Off air (`hw_hackrf_check.py`, stage 8 extended): the readout at the
  default gain in Receive and Sweep, and the sweep's warning when the gain
  is raised until it clips.
- Optional while the HackRF is here: the **LNA/VGA split** measurement
  (see *HackRF off air*), about 5 more minutes.

**Shipped 2026-09-22**, with one change from the plan: a sweep reports the
worst step of its last complete sweep, held for a sweep, rather than a
share smoothed over time. The first off-air run showed why. The window's
default sweep is the full range, which takes tens of seconds, and at the
default 40% a UHF TV step (533.5 MHz) clipped 2.5%; an average over every
step hides that, and a per-tick count made the warning come and go. Off
air: 0.00% at 40% on the FM band, sweeping and receiving; at 60%, 50% in
Receive and 95.6% in the step centred on 95 MHz.

### Phase 2: the BB60D's health

- [x] **4. Temperature, USB voltage and current** from
  `bbGetDeviceDiagnostics(handle, &temp, &volts, &amps)`, on the handle
  `bb60_sweep.device_handle()` finds.
  - **Probe first** (a scratchpad script, by behaviour only): call it
    with the IQ stream running, during the device's own sweep, and in
    real time; check the return code, that the numbers are sensible, and
    that the stream drops nothing (`health()['dropped']`) and the RDS
    keeps decoding. On the Mac, whether the library exports it (a
    `hasattr` on the loaded library) and the same calls. If it fails
    while streaming, fall back to reading it only in the device's own
    sweep, or drop the item.
  - Read every 5 s from `_check_health`, in a `try`, and added to
    `BB60.health()` as `temp_c`, `usb_v` and `usb_a`.
  - Shown (decided 2026-09-22) as the status line's tooltip ("BB60D: 41.2 °C, USB 4.95 V,
    0.52 A"), and in the status line itself only when something is wrong:
    below 4.4 V, "USB voltage low (4.31 V) - measurements may be off" in
    `warn`. A temperature limit only if Signal Hound's manual gives one.
- Tests: `test_gui.py` with a fake `health()` for the tooltip and the low
  voltage warning. The BB60D check prints the readings and checks their
  ranges.

### Phase 3: auto gain on the BB60D

- [x] **3. Auto gain by reference level.** Signal Hound's recommendation:
  gain and attenuation on auto (`BB_AUTO_GAIN`, `BB_AUTO_ATTEN`, both
  -1), and the reference level about 5 dB above the strongest signal
  expected; the API then picks the gain and attenuation.
  - **Decided 2026-09-22: an AGC box beside the RF gain slider** (the one
    slider under the tabs, shared by Sweep and Receive), for the BB60D
    only. When on, the slider greys out, the label reads "AGC", and the
    view's **Ref level knob is the reference level**: the app sets it to
    the strongest signal in the last sweep plus 5 dB, rounded to 5 dB and
    moved only when that changes by 5 dB or more, and **the knob visibly
    moves** when it does. A hand turn of the knob sets the reference level
    until the peak next moves by 5 dB.
  - **The device's own sweep** is certain: `bb60_sweeper._configure_now`
    already calls `bbConfigureRefLevel` and `bbConfigureGainAtten`.
  - **Receive is a probe**, because the IQ stream goes through the
    SoapySDR module: does it offer an automatic gain mode
    (`hasGainMode`), or a setting for the reference level
    (`getSettingInfo`)? If so, Auto works in Receive with the channel's
    peak plus 5 dB. If not, in Receive the Auto box keeps its tick but the
    slider sets the gain, with a tooltip saying why (decided 2026-09-22).
  - Saved per radio in the config (`gain_auto`), like the gain.
- Tests: `test_sweep.py` for the reference-level rule (hysteresis,
  rounding, limits). The BB60D check runs its own sweep with Auto on, and
  expects the FM band's floor within a few dB of the 60% setting and no
  overload.

**Phases 2 and 3 shipped 2026-09-22.** `run_all.py` 5/5; the BB60D check
passed four times with its new health and AGC stages; the window was driven
on the BB60D (the knob moved from -80 to -25 dBm as AGC was ticked and held
there for 15 s, no overloads).

- **Health**: the probe passed in every mode (10 us a call, nothing
  dropped, RDS unharmed). The current comes back in mA, not A. Signal
  Hound's reference gives no temperature limit, so only the voltage warns.
  Still to try: the Mac's library.
- **The SoapySDR module has no automatic gain**: `hasGainMode` is false,
  its only gains are `RF` and `ATT`, and its settings are the two ports.
  So AGC is for the device's own sweep, as decided.
- **What AGC follows changed twice**, both times from off-air evidence:
  - *A sweep's peak* (plus 5 dB, as planned) hunted: at 1 kHz RBW an FM
    station's peak swings from -54 to -39 dBm sweep to sweep, and the knob
    climbed from -20 to -5 dBm. Holding the highest peak for 3 s made it
    ratchet up instead.
  - *One station's power* (summed over 200 kHz) is steady, ±0.2 dB at any
    RBW, since FM's envelope is constant, but at the -30 dBm it gave, the
    device overloaded now and then (3 sweeps in 800).
  - *The most power in any 27 MHz* - what the front end takes in at once -
    is what it follows now: -33.6 dBm here, so -25 dBm, with no overloads
    in 717 sweeps interleaved with the slider's 60% (none either).
- On the FM band AGC lands where the tested 60% is (stations within 1.5 dB,
  the same floor). Its use is elsewhere: the device sets each band on its
  own in a full-range sweep, which one slider can't.
- Ticking AGC sets the Ref level from the last sweep before the device
  follows it, so it never runs a sweep at a knob left at -80 dBm.
- 💡 Try AGC over the full range against the slider, band by band, and on
  the Mac.

## HackRF: its own sweep, and the gain split (requested 2026-09-22)

Both on the HackRF, handed over from ble-scanner for the session.

- [x] **The gain split for FM: keep LNA share 0.45.** Equal LNA + VGA
  totals split four ways (8/32, 16/24, 24/16, 32/8 at 40 dB; 16/32 to 40/8
  at 48), 8 s each, on 99.1, 89.3 and 90.1, two passes interleaved:
  - SNR moved by 2 dB at most across the splits, in no one direction; the
    present 16/24 (the 40% default) was best or level on 89.3 and 90.1
    (90.1, the weakest: 18.1 dB against 16.3-16.6).
  - RDS: 89.3 at 100% with 8/32 and 16/24, 88% at 32/8; 90.1 at 100%
    throughout; 99.1 scattered from 56 to 88% with no pattern.
  - 48 dB in all (47%) gained nothing and cost 89.3's RDS (41-86%) with no
    clipping - overload by the strong stations. 40% stays the default.
  - So ble-scanner's gain at 2.4 GHz (more LNA) doesn't carry to FM here.
  - A first run reset the RDS decoder before each 6 s window and read 0%
    where it had not resynced; the counts above are blocks decoded within
    each window, without a reset.
- [x] **The HackRF's own sweep** (`hackrf_sweep.py`), in the window for the
  HackRF in place of LO hopping. Done in the process through libhackrf
  rather than by running `hackrf_sweep`, so the samples are to hand: the
  clipped readout carries over (per tuning), and the levels are dBFS on
  the window's scale.
  - 1 MHz-6 GHz in 0.75 s (8 GHz/s); the FM band in 38 ms, capped at 30 a
    second; the same stations as the BB60D.
  - The IQ stream's SoapySDR device is let go for it and opened again for
    Receive (60-100 ms each way). `hackrf_exit` is never called: it would
    end the USB context the SoapySDR module shares.
  - One FFT per tuning at the full range's 264 points scattered the floor
    over 30 dB; averaging up to 15 from each block's second half brought
    that to 6.6 dB for no extra radio time.
  - Found on the way: at 40% a UHF TV tuning (LO 533.5 MHz) clips 60-70%
    over the full range, and a 600 MHz LTE one did once. The FM band clips
    0.1-0.3%. The warning says which.
  - Found on the way: Round 6 had put the BB60D's health check in the
    receive stage the HackRF check shares, which failed it; fixed.
  - The LO-hopping sweep stays for the USRP, and the HackRF check keeps its
    settle-time stage on it.
  - 💡 Try it on the Mac (libhackrf by its name, then the environment's
    `lib`).

## Real-time polish (requested 2026-09-22)

- [x] **Persistence.** Measured on the BB60D: the density frame itself keeps
  a pixel after its hits stop, and the alpha frame is how recent they were -
  1 when hit, falling by exactly 0.920 a frame, to 0.042 and then 0: about
  0.4 s as a time constant at 30 frames a second, gone 1.3 s after. No pixel
  had alpha without being in the map. So the map is drawn with alpha as each
  pixel's opacity (`SpectrumView.set_density`, RGBA in the theme's colours,
  2.3 ms a draw): a burst fades out where it was.
- [x] **Every frame reaches the window.** Real time makes 30 frames a second
  and the window draws 15, so every other frame - and a burst in it - never
  reached the trace, peak hold or waterfall. The sweepers now keep the most
  each point reached since the window last looked (`take_held`), and the
  window draws that; a waterfall row is the most of every frame since the
  row before. Rows stay at 10 a second, so the waterfall keeps its 22 s.
  The HackRF's own sweep, at 30 sweeps a second on the FM band, does the
  same. Off air: 30.0 frames a second, 15.7 draws, each with all the frames
  since the last; the HackRF on the FM band 25 sweeps a second, 15.6 draws,
  every one with a held maximum, and its hardware check passed.
- With averaging on (Average above 1x), the trace is still the average of
  whole sweeps; the waterfall rows are held maxima either way.

## Round 7: boxes, auto scale, the BB60D's widest IQ (requested 2026-09-22)

- [x] **1. No lines on the waterfall.** The Center's dashed line and the
  tuner's orange marker are drawn on the spectrum only, in Sweep and
  Receive; the waterfall is left clear.
- [x] **2. A wider IQ bandwidth on the BB60D.** 20 and 40 MS/s added to
  Receive (the hardware has them; only 2.5-10 were offered). Off air on
  89.3: 40 MS/s 84% of a core (10 MS/s 57%), no samples lost, RDS 552/552;
  27 MHz on screen, the tuner ±11.85 MHz from the Center.
  `hw_bb60_check.py` now receives there too. Default stays 10 MS/s. Not yet
  on the Mac.
- [x] **3. Channel filter to 500 kHz?** Not changed. It stops at 400 kHz
  because the channel is sampled at 500 kS/s (the IQ – channel recording's
  rate) and the filter needs room to roll off inside that. An FM station
  with its HD Radio sidebands is ±200 kHz, which 400 kHz holds. Going wider
  would mean a 1 MS/s channel - twice the work after the channelizer - for
  nothing FM uses.
- [x] **4. Folding boxes.** Tuner and RDS fold to their title on a chevron;
  Radio folds to its Center row. Remembered (`folded` in the settings).
  Rows are on a grid (`widgets.Form`): Qt 5's `QFormLayout` keeps the
  spacing of hidden rows.
- [x] **5. A: auto scale.** Ref level and Range fitted to the trace on
  screen and centred on it; the span is left alone ("max bandwidth" is
  still Full span). Under AGC only the Range moves.
- [x] **6. A Sweep box.** The Sweep tab now has a Sweep box and a Tuner box
  (the tuner and Listen), as Receive has.
- [x] **7. Real time hidden.** Receive at 40 MS/s shows 27 MHz 30 times a
  second and plays the station, so the button is hidden; `--realtime`
  shows it, and everything behind it works as before.
- [x] Found on the way: `--sweep START STOP` passed MHz as Hz, so
  `--sweep 87.5 108` swept 9-209 kHz. And `test_recordings.py` failed on a
  busy machine: the recording's clock started before 3 s of test files
  were written, pushing the first RadioText past the second it was checked
  at.

## Round 8: the left column and the readout (requested 2026-09-22)

- [x] **1. The pointer's readout at the top left** of the row under each
  spectrum, level with the dials' names (it was at the bottom).
- [x] **2. dBFS** answered: decibels relative to full scale - 0 is the
  converter's largest value, so levels read below it. The BB60D's own
  sweep is calibrated, in dBm.
- [x] **3. Folded Tuner** shows the tuner and its ▲/▼, no Step or channel
  filter.
- [x] **4. Folded RDS** shows Now playing and RadioText.
- [x] **5, 6. Audio and Record hidden in Sweep**: nothing to hear or record
  while sweeping. They were under the tabs from before the modes split.
- [x] **7. RF gain inside the Sweep tab**, in its own box under Sweep and
  Tuner; under the tabs in Receive and Recordings.
- [x] **8. Listen to the right of the Sweep tab's tuner.**
- [x] Found on the way: the tabs were as tall as their tallest page (Qt 5
  sizes a tab widget so, in its size hint and its height for width), which
  left a gap under Receive's folded boxes. `widgets.PageTabs` sizes to the
  page on show.

## Round 9 (requested 2026-09-22)

- [x] **The pointer's readout in the plot's bottom-left corner**, for every
  spectrum (Round 8 had put it at the top of the row under the plot).
- [x] **Stations found in the Sweep tab**, under the Sweep box; while
  sweeping, the spectrum and waterfall take the whole right-hand side.
- [x] **Folding slides**: 0.2 s, the chevron turning with it. Found on the
  way: squeezed by the layout, the rows piled on top of each other
  mid-slide, and the left column (sized to its minimums, and told of a
  new size one event at a time through the tab widget) squeezed the boxes
  above. The rows are now held and clipped, the height fixed per frame and
  the column refitted at once.
- [x] **The Recordings tab's time scrubber colours**, in all three themes:
  see Round 10.

## Round 10: the themes' spectrum (requested 2026-09-23)

A review of all three themes, shown as before-and-after screenshots, and
kept whole:

- [x] The Center line a thin dashed `ink_3` in every theme.
- [x] The channel band a faint tint with crisp edges.
- [x] Out of the tuner's reach dimmed (the plot's `well` over the trace).
- [x] A faint fill under the trace, and a frame round each plot.
- [x] The scrubber's playhead in `ink`, the played part dimmed.
- [x] Reading Room: the waterfall through the trace's teal; Stop/Start in ink.
- [x] Walnut: the trace in cream; a darker waterfall floor.
- [x] The Recordings scrubber's colours (Round 9's open item) - the playhead
  and the played part above.

## Round 11 (requested 2026-09-23)

- [x] **The level axis:** lit under the pointer; the wheel zooms the scale
  (Ref level and Range together, about the pointer's level); a middle drag
  moves the Ref level; the knobs follow. Sweep and Receive alike.
- [x] **Folded Radio box:** Center and Center on tuner glide to the middle.
- [x] **Folded Tuner box:** the tuner glides to the middle; the Step knob
  fades out, and back in as it opens.
- [x] **The readout on a panel**, so the trace can't wash it out.

## Round 12: the waterfall in time (requested 2026-09-23)

- [x] **A time scale** down the waterfall's left side, "now" at the top.
- [x] **Five minutes kept**, shown 2 s to 5 min at a time; zoomed out, each
  screen row the most of what it covers.
- [x] **Hover and wheel on the time scale**, as on the level axis.
- [ ] **Waterfall DVR.** A highlighted window on the time scale: the wheel
  makes it bigger or smaller, and a middle-button drag moves it back and
  forth in time, to look at (and later play back) a stretch of the past.
  Needs the IQ kept as well as the picture - the history is only the
  waterfall's rows today.

## Round 13: the RTL-SDR, here and over the network (requested 2026-09-23)

Frees the BB60D and HackRF for ble-scanner: an RTL-SDR can't do BLE, but
is fine for FM. First step towards using several radios over a network.

- [x] **An RTL-SDR through rtl_tcp**, the app as its client: no new
  packages on the far side, only rtl_tcp.
- [x] **Here or on another computer**: blank address runs rtl_tcp
  locally; an ssh host runs it there, started and stopped by the app.
- [x] **Off air over the network** (the Mac mini's R820T): Receive with
  stereo and RDS, the LO-hopping sweep, settle measured.
- [x] **Local on real hardware**: a dongle plugged into the machine the app
  runs on (Linux, 2026-09-23). Not yet on the Mac locally.
- [ ] **Several radios over the network**: the next step (to be planned).
