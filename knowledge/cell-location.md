# Where a cell is: locating a tower from broadcast only

Acronyms are in [glossary.md](glossary.md). Finding a cell and reading its
broadcast is in [lte-verify.md](lte-verify.md), [lte-broadcast.md](lte-broadcast.md)
and nr-verify.md. This note is about a different question: where the tower
is, or where we are, using only what we receive, and sending nothing to an
outside service.

Scope: receive only, broadcast only. Nothing here transmits, and nothing
sends cell identities to an online database. Marks: **checked** (a source or
our own capture), **unchecked** (reasoning, or one source not re-opened).

## What the broadcast does not tell us

- No coordinates. Neither LTE nor 5G NR system information carries the
  site's position. **checked** (the SIBs decoded so far: LTE SIB1-SIB5,
  SIB15, SIB24; NR SIB1-SIB5, SIB9)
- "GPS" in NR SIB9 is GPS *time*, not a position: `timeInfoUTC` is UTC,
  `gps` is the same instant on the GPS clock (UTC plus the leap seconds, 18 in
  2026), and `referenceTimeInfo-r16` restates it as days, seconds and
  milliseconds from 1980-01-06. **checked** (real air, 2026-10-23)
- The tracking area code and cell identity name a cell, not a place.
  Crowd-sourced databases (OpenCellID, CellMapper) map PLMN + TAC + cell ID
  to an estimated position, but that means sending the IDs out; this project
  does not do it.
- Positioning broadcasts exist (LTE's positioning SIBs, NR posSIBs, which
  carry reference-station coordinates for phones), but none of the cells
  heard so far schedules them, and the one LTE SIB15 heard was empty.
  **checked**

## Ways that work without a database

### 1. Bearings with a directional antenna (most practical)

A small 850 MHz log-periodic or Yagi (about $20-40), turned in steps of
20-30 degrees, a short capture at each step. `iq-nr` gives each cell's PSS
and SSS scores and relative power per PCI, so the bearing is for that one
cell, apart from other towers on the same band (`iq-lte` does the same for
LTE cells). The heading where the level peaks is the bearing. Two or three
bearings from places a few hundred metres apart cross at the tower.
**unchecked** (not tried yet; no directional antenna here)

A tool mode that prints one PCI's level per capture would make the sweep a
loop of `capture` and a number. Not built.

### 2. Walk or drive the area

Record the SSB level at several points, each point's position from a phone,
and the tower sits where the level is highest. Crude, but needs nothing more
than what is here. Sectors help: a site's sectors share N_ID1 and differ in
N_ID2 (PCIs 3*N_ID1 + 0, 1, 2), often in different SSB slots, and the
strongest sector is the one facing you. **checked** (three sectors of one
site seen on one capture, 7 and 11 dB apart)

### 3. Range from SIB9 timing and a GPS-locked capture (an experiment)

SIB9's `referenceTimeInfo-r16` stamps a frame boundary to 10 ns. If the
capture's own sample clock is tied to GPS time, the gap between that stamp
and when the boundary actually arrives is the travel time: 300 m per
microsecond. That is a circle around the tower; several cells give a fix.

What it needs:
- GPS time-stamping on the BB60D. Signal Hound support says it is reachable
  through the C API only (not their desktop software), and they quoted about
  50 ns resolution at 40 MS/s IQ with an external GPS receiver; the
  time-stamps read zero until the receiver has delivered its first PPS
  pulse. **unchecked** (forum answers; read the BB60 API manual before
  wiring or coding)
- A GPS receiver with a PPS output (see below).

Limits:
- 50 ns is about 15 m. The larger unknown is the tower's own fixed transmit
  delay (cables, radio), which is not broadcast; one tower alone may come
  out hundreds of metres off. Comparing two or more cells, or calibrating on
  a tower you can see, removes much of it. **unchecked**
- Today's recordings are stamped by the PC clock, good to milliseconds,
  which is hundreds of kilometres of range: useless for this.

## A GPS receiver for time-stamping

The part that matters is a **PPS output** (a pulse at the start of each
second) plus NMEA over serial. A microcontroller board (ESP32) is optional.

| option | about | notes |
|---|---|---|
| u-blox NEO-M8N or NEO-6M breakout with a PPS pin, plus a USB-serial adapter | $10-25 | simplest; many cheap boards only drive an LED from PPS, so pick one with the pad broken out |
| LilyGO T-Beam (ESP32 + u-blox GPS + LoRa) | $30-40 | the "ESP board" choice; check that GPS PPS reaches a header pin on the revision bought |
| u-blox timing modules (NEO-M8T, ZED-F9T) | $60-200 | steadier PPS (about 10-20 ns) and a fixed-position timing mode; only worth it once the cheap one works |
| USB GPS "puck" | $15 | avoid: most give NMEA over USB and no PPS |

Before wiring: the modules' PPS is 3.3 V; check the level the BB60D's
trigger/sync port takes and how the API arms it (the BB60 API manual). The
antenna needs a sky view (outdoors or a window) and a few minutes to lock.
Jackson Labs' GPS-disciplined receivers were the vendor's own suggestion for
a ready-made unit.

## What not to do

- Timing advance or any round-trip measurement: it needs the phone side to
  transmit. Out of scope.
- Distance from signal strength alone: off by a factor of several.

## Sources

- [BB60 API manual](https://signalhound.com/sigdownloads/BB60C/BB60-API-Manual.pdf) (not yet read for the GPS functions)
- [Signal Hound forum: BB60C GPS timestamp receive problem](https://signalhound.com/?p=156622)
- [Signal Hound forum: GPS receiver for BB60 timestamping](https://signalhound.com/?p=245659)
- [SparkFun: syncing PPS to NMEA UTC](https://community.sparkfun.com/t/synchronizing-pps-signal-to-nmea-utc-values-which-modules/11215)
