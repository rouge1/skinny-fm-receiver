# Signal Hound BB60D and VSG60A: calibration

Notes from Signal Hound's manuals and API reference, read 2026-10-21 by the RF
bench toolkit session (its copy: `/data/python/SDR/knowledge/signal-hound-calibration.md`).
Sources at the end. The last section says what this means for this app.

**Neither instrument has a calibration source inside it.** There is no internal
reference tone or level standard. Both are calibrated at the factory, in an
environmental chamber, and the correction tables are stored on the device and
applied by the API. What the BB60D does for itself is apply its stored
corrections at its present temperature. Anything traceable needs outside
equipment: Signal Hound's field calibration software and a lab's standards, or a
return to Signal Hound.

## Side by side

| | BB60D (receiver) | VSG60A (transmitter) |
|---|---|---|
| Factory | adjusted in a chamber; corrections stored on the device | I/Q balance, DC offset and output level adjusted across frequency and temperature in a chamber, stored in internal flash, applied in real time on the PC |
| Temperature | recalibrates at open and on every `bbInitiate`, at the temperature then; Signal Hound asks for it again after a change of 2 °C | the stored corrections cover temperature; nothing to call |
| Self-calibration call | none needed: `bbSelfCal` is for the BB60A only | none |
| Temperature reading | `bbGetDeviceDiagnostics`, 1/8 °C | none found in the manual |
| Recommended cycle | one year | one year |
| Field calibration needs | two signal generators (an MXG or equivalent), a function generator, a power meter and E9304A sensor, a spectrum analyser to 7.5 GHz, a splitter, a 10 dB pad, two couplers, GPIB | a power meter and E9304A sensor, and a Signal Hound SM200A/B as the analyser |
| User adjustment | the 10 MHz timebase, through the calibration software | the 10 MHz timebase, and the DC offset to null LO feedthrough |

## The BB60D and temperature

The API's theory of operation says "The device is temperature sensitive", and
that it should be "re-calibrated when significant temperature shifts occur
(+/- 2 °C)". The device can't do this by itself, because it would interrupt
streaming: "Therefore, we leave calibration to the programmer."

The `bbSelfCal` reference adds that a change of 2 °C "measurably" alters the IF's
shape and amplitude, and that a swing of 10 °C or more can put a dB or more of
error into the amplitude.

On the BB60D, as on the BB60C, the call is `bbInitiate`, not `bbSelfCal`:
"simply call bbInitiate again which will re-calibrate the device at its current
operating temperature." One calibration is also made when the device is opened.

`bbGetDeviceDiagnostics` returns the temperature, updated after each sweep and
"periodically while I/Q streaming", with the USB voltage and current. Below
4.4 V on the USB, readings "may be out of spec". This app already reads it (see
capabilities.md, "BB60D health").

`bbInitiate` calls `bbAbort` first, so recalibrating interrupts the stream.
Through the SoapySDR module, whether a deactivate and reactivate of the stream
does the same has not been checked.

## The VSG60A

The manual describes no self-calibration and no temperature call. Its
corrections were measured across temperature at the factory and are applied as
it runs. Its internal 10 MHz is a VCTCXO with a 16-bit tuning DAC: initial
accuracy ±1 ppm, ageing ±1 ppm a year, and "periodic adjustments will typically
keep the internal timebase to better than 0.1 ppm" at a stable temperature. Its
absolute level error, ±2.0 dB, is specified only in CW mode from −55 to
+7 dBm; a tone below −55 dBm has no specified accuracy.

## The 10 MHz adjustment is limited

Both timebases can be adjusted in the field by the calibration software, and the
adjustment is "permanent until the device is returned to Signal Hound". On the
BB60D there is "a limited amount of FLASH memory dedicated to 10 MHz reference
adjustments, so adjustments should not be performed more than once per month",
and adjusting it without a reference within 0.2 ppm "is discouraged". An external
10 MHz on the reference input corrects frequency without touching the flash, and
does nothing for level.

## Warm-up

No warm-up time is specified for measuring. The field calibration manual asks
for "at minimum 15 minutes of idling time" connected to the PC before a
calibration starts. In a Signal Hound forum thread, warming the unit up first was
suggested for drifting readings.

## Seen on the bench

The BB60D's noise floor moved about 1 dB from one day to the next. The same
−68 dBm VSG60A tone over a cable read 20.1–20.3 dB SNR on 2026-10-20 and 21.3 dB
on 2026-10-21: within what Signal Hound describes for temperature.

## What it means for this app

- **Levels in dBm are specified to ±2.0 dB** (non-native, flattop RBWs) and
  +2.0/−2.6 dB (native, Nuttall RBWs), both at RBW ≤100 kHz (BB60D user manual,
  section 6). Observed, they repeat within about 1 dB: the floor from day to day,
  and the BB60D's sweep against Sceptre's. Neither is a check against a tone of
  known level; a VSG60A tone would count only from −55 to +7 dBm in CW, where its
  own ±2.0 dB applies. A change of a dB or two is not evidence of a change at the
  transmitter.
- **Frequency is good enough for LTE.** The PSS search tolerates about ±10 kHz
  (lte-verify.md). ±1 ppm is ±0.75 kHz at 750 MHz and ±3.6 kHz at 3.6 GHz.
- **A long session** (a band survey, a long recording): the temperature is in the
  status line. A drift of 2 °C or more is Signal Hound's mark for recalibrating.
  Whether the window's Stop and Start does that through SoapySDR is unchecked.
- **Before a careful level measurement**, let the radio run about 15 minutes.

## Sources

- BB60 API, theory of operation: https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/theory_of_operation.html
- BB60 API reference (`bbSelfCal`, `bbGetDeviceDiagnostics`, `bbInitiate`, the 10 MHz options): https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/bb__api_8h.html
- BB60D user manual, section 5 "Calibration and Adjustment" and the preliminary specifications: https://signalhound.com/sigdownloads/BB60C/BB60D-User-Manual.pdf
- VSG60A product manual, sections 4 "Calibration" and 5 "Adjustments", and the timebase: https://signalhound.com/sigdownloads/VSG60/VSG60A-Product-Manual.pdf
- Field Calibration Software user manual, v1.13.13, 4 Sep 2024: https://signalhound.com/sigdownloads/CalSoftware/Field-Calibration-User-Manual.pdf
- BB60D product page (factory calibration, the optional certificate, temperature ranges 0 to +65 °C standard, −40 to +65 °C Option 1): https://signalhound.com/products/bb60d-6-ghz-real-time-spectrum-analyzer/
