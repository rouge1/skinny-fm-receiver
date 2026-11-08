# Finding a 2.4 GHz mouse link: a BLE connection on the LE 2M PHY

Worked example, 2026-11-08, with the BB60D. A wireless mouse with a Logitech
Bolt receiver, to be identified from the air. Written as method and result:
no addresses, channel numbers, frequencies or payloads from the capture are
given here (this repo is public), and the scripts were one-off, in a scratch
folder: no tool for this is in the repo yet.

The answer: a Bluetooth Low Energy connection on the **LE 2M PHY**, hopping
over many data channels, polled at the 7.5 ms minimum connection interval.
The BLE identification is confirmed by three checks that agree with each
other (below). That the link is the mouse's is supported by the dongle on /
off / on test, not by decoding anything of the mouse itself.

## What the first look got wrong

The device was named as being at one frequency in the middle of the 2.4 GHz
band. Three captures there (20 MS/s, 3 s) showed only the room: an access
point's beacons every 102.4 ms (100 TU), at the same level with the device on
or off. Nothing narrow came from the mouse.

- **The link was not there.** BLE links use adaptive frequency hopping and
  drop channels that WiFi occupies. The access point sat on the middle WiFi
  channel, so the link was absent from the very window being watched.
  A frequency taken from a user, a scan or a spec is where something was once
  seen; for a hopping link look at the whole band.
- **A different narrow signal turned up** in the device-on captures only, a
  few tens of short bursts near the middle of the band. It did not follow the
  mouse (it was absent with the mouse moving and the dongle in), and is
  unexplained. Do not read "present when the device is on" as "the device"
  unless it also goes when the device's own link is removed.
- **A wrong protocol guess cost a round.** The dongle was first assumed to be
  a Unifying one (1-2 Mbit/s proprietary GFSK, 1 MHz-class channels). `lsusb`
  named it a Bolt receiver, which is BLE based. Read the USB IDs of the
  user's receiver before guessing the protocol.

## Step 1: find it by removing it (peak-hold A/B/A over the whole band)

A peak-hold sweep over the whole 2.4 GHz band (80 MHz) for 15 s, mouse moving
all the time, read as the held level at each megahertz (`fmctl marker FREQ`
at every MHz, with `peakhold clear` between runs, since the peaks list merges
into one blob when WiFi fills the band).

| Run | Upper part of the band, mean held level |
|---|---|
| dongle in | about -73 dBm |
| dongle out | at the noise floor, about -96 dBm |
| dongle in again | about -84 dBm |

The middle of the band (the WiFi channel) did not change. With the dongle in,
the spots that stood out sat on **even megahertz, 2 MHz apart**, 15-40 dB
over the odd-MHz neighbours: the BLE channel grid. The channels that were up
changed between the first and last run, which is what a hopping link with a
channel map does; 15 s of peak hold shows only some of them. The A/B/A is the
evidence that the signal belongs to the dongle's link: one A/B alone could
be a neighbour's WiFi stopping.

## Step 2: look at the bursts (IQ, 20 MS/s centred on the busy part)

An IQ capture of 3 s centred on the part of the band that lit up, mouse
moving. A spectrogram with 12.8 us frames, with the power in a +-0.8 MHz box
on each even-MHz channel, finds the bursts and which channel each is on.

- **Timing.** The gaps between bursts are multiples of **7.5 ms** (7.5, 15,
  30, 45, 67.5, 120 ms). 7.5 ms is the shortest BLE connection interval, and
  what low-latency HID devices use. Many bursts come in pairs about 0.19 ms
  apart (the central's packet, then the peripheral's reply, 150 us
  inter-frame space after it).
- **Length.** The strongest bursts all last about 129 us.
- **Symbol rate: 2 Msym/s, not 1.** Demodulating each burst as FM and
  histogramming the intervals between zero crossings gives peaks at 5, 10,
  15 and 20 samples at 10 MS/s, 5 being the tallest: the symbol is 0.5 us.
  A 1 Mbit/s search found nothing, and this is why.
- **Deviation:** about +-500 kHz (half of the 5th-to-95th percentile swing
  of the discriminator), the LE 2M PHY's nominal value.

## Step 3: the three checks that make it BLE

All three need the burst's bits first. How they were taken, and the traps:

1. Take the burst out with a +-1.3 MHz low-pass after moving its channel to
   0 Hz, to 10 MS/s (5 samples per symbol). Discriminator, median removed
   (the carrier offset), a short smoothing filter.
2. Pick the symbol phase (0-4) with the largest mean |frequency| at the
   sample points; slice the sign.
3. **Find the preamble from where its alternation ends, not from the burst
   start.** The amplitude gate leaves 20-30 bits of noise and ramp before the
   real preamble, so a search over the first few bits finds nothing. Take the
   longest run of alternating bits (at least 14) and let its end set the
   alignment. A 2M preamble is 16 bits; its last bit is the opposite of the
   access address's first, so the alternation runs on into the address.

**Check 1, whitening.** BLE whitens PDU and CRC with a 7-bit LFSR
(x^7 + x^4 + 1) seeded with the channel index, so a packet on the wrong
channel mapping, or at the wrong bit alignment, dewhitens to noise. Data
channel index from frequency: index 0-10 at 2404-2424 MHz, 11-36 at
2428-2478 MHz, every 2 MHz. Register seed per the specification: position 0
set, positions 1-6 the channel index with the MSB in position 1.

Scanning the alignment by a few bits either side, with both bit orders of the
seed, only one combination made the dewhitened header's **length byte the same
in every packet**, on every channel: 203 of 203 packets had the same length.
The wrong combinations gave random lengths. The packets also split into two
header values only, two flag patterns (the SN and NESN bits, with LLID 2: an
L2CAP data PDU) in about equal numbers, as the two directions of one
connection would.

**Check 2, one access address.** All 203 packets, on 9 data channels, carry
the **same 32-bit access address**. A BLE connection has one access address
chosen at random for it; a stray burst would not repeat one.

**Check 3, one CRC start value.** The CRC-24 of a connection starts from a
value chosen at random for that connection, which the sniffer does not know.
It can be run backwards instead: start from the CRC the packet carries, step
the CRC register back through the dewhitened header and payload, and what is
left is the start value. (Polynomial x^24 + x^10 + x^9 + x^6 + x^4 + x^3 +
x + 1; the CRC read most significant bit of the register first.) **All 203
packets gave the same start value**, a 24-bit number: the chance of two
packets agreeing by accident is 2^-24, so this is the referee. With the other
bit order of the CRC the 203 start values were 143 different numbers.

So: preamble and access address repeating, a per-channel whitening that makes
the length agree across channels, and a CRC that closes to one start value.
The payloads are not decoded (they are encrypted, as a HID link's are).

## Method in short

1. A peak-hold sweep of the whole band with and without the thing, then with
   it again, read per megahertz. Look for the channel grid of the suspected
   protocol (even MHz for BLE).
2. An IQ capture where it showed; the gaps between bursts (a connection
   interval is a multiple of 1.25 ms), the pairing, the length.
3. The symbol rate from zero-crossing intervals before any bit search.
4. The preamble from where its alternation ends; the access address from
   repeats; whitening and CRC as referees that need no prior knowledge of
   the connection.

## Limits and open points

- Not checked: that the payload length, interval and PHY match Bolt's
  documented link (no source was consulted for them); and the receiver was
  not removed while an independent second BLE device ran, so another BLE
  device nearby, on the same grid, is excluded only by the dongle test.
- Only the dongle's own packets (both directions) were seen; the link-layer
  control PDUs (channel map, PHY update) were not looked for.
- The narrow bursts that appeared with the device on in the first captures
  were never explained.
- Tools: none yet. A scratch script did the burst finding and these checks;
  a numpy-only `iq-ble` (burst finder, rate from zero crossings, access
  address, whitening and CRC check) would fit beside `iq-lte` and `iq-nr`
  and classify a BLE link without decoding the payload.
- Not in `capabilities.md` or `roadmap.md`: add them if an `iq-ble` is built.
