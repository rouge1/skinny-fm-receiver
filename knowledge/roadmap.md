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
Center-move step in the BB60D check. One choice is still open: the
waterfall palettes (item 1).

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

  To switch, name the letter. B and C are these stops, one line each in
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
