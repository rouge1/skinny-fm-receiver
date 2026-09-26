# Gridstream: decoding a smart-meter network off air

How the 902-928 MHz hopping traffic found in the ISM survey (2026-09-25) was
decoded to Landis+Gyr Gridstream packets. It follows the steps of
[workflow.md](workflow.md) in order. The format itself is in the sources at
the end; this records what we measured, what worked and what did not.

The addresses in the packets are real meters near the bench, and this repo
is public: the IDs below are placeholders (`AAAAAAAA`, `BBBBBBBB`). The
recordings and the scripts that did the work live in `recordings/`, which
git ignores.

## The result

| Item | Value |
|---|---|
| System | Landis+Gyr Gridstream, protocol v4 and v5 on one network |
| Band | 902-928 MHz, a new channel for nearly every packet (a median 5.3 MHz from the last) |
| Modulation | 2-FSK, deviation about +-5-6 kHz, constant envelope |
| Symbol rate | 9600 Bd nominal; the meters ran at 9597-9617 Bd |
| Framing | `AA...` preamble, then UART 8N1, LSB first, from the sync on |
| Sync | v4 `00 FF 2A`; v5 the same bytes with `11` in place of the `FF` frame's start bit |
| CRC | CRC-16, poly 0x1021, init **0xB5E3**, no final XOR, over the payload after the length field, stored big-endian in its last two bytes |
| Decoded | 10 packets with a good CRC out of 53 transmissions in 5 s; two `D2` packets complete but with no check that fits (below) |

0xB5E3 is not in rtl_433's table of utilities: each utility is given its own
init so neighbouring networks ignore each other. Which utility this is was
not established.

## 1. Find the signal

A BB60D sweep of 300 MHz-1 GHz (its own sweep, RBW 30 kHz, about 23 sweeps a
second) with **peak hold** for 90 s, through `fmctl`:

```sh
tools/fmctl 'sweep 300 1000; peakhold on; wait 90; peaks 8 902 928'
```

902-928 MHz held about 20 narrow signals (50-70 kHz wide at that RBW) spread
over the band, 18-24 dB over the floor. The live trace and the waterfall
showed nothing: each burst lasts tens of ms, and averaging dilutes it. Peak
hold is what finds hopping traffic.

## 2. Record the IQ

The whole band at once, in Receive at 40 MS/s (27 MHz on show, 902.85-926.55
usable around a 915 MHz Center):

```sh
tools/fmctl 'mode receive; tune 915; center 915; capture 5'
```

5.1 s, 204 M samples, 1.63 GB of cf32, with its `.sigmf-meta`. No samples
lost.

## 3. Waterfall pictures

- **The whole capture** (`bursts.py`): a 1024-point STFT (39 kHz bins, 25.6
  us a frame), each bin's median as its floor, cells over +12 dB, joined in
  time and frequency (`scipy.ndimage.label`). 974 fragments; joined when on
  the same frequency (+-80 kHz) with gaps under 5 ms, **53 transmissions** of
  2 ms or more on 40 channels, many exactly 21.7-21.9 ms long.
- **The band over 250 ms** (`wf.py`): 2048-point FFTs, each row the maximum
  of 8 frames (0.4 ms) so short bursts keep their level. The hops show as
  short vertical streaks at a new frequency each time; a second kind of
  burst, 0.3-0.5 MHz wide and a few ms long, as horizontal smears.
- **One burst close up** (`cut.py`, `closeup.py`): shifted to 0 Hz and
  decimated to 2 MS/s, 256-point FFTs every 16 us. Two tones about 10 kHz
  apart, a ladder of alternation (the preamble) for 5 ms, a steadier stretch
  (the sync), then data: 34.4 ms in all. The envelope is flat.

## 4. The modulation

- Envelope: constant through the burst, so no amplitude keying.
- Instantaneous frequency (below): two levels, so 2-FSK.
- The preamble's alternation, from an FFT of the discriminator over it, is
  **4.807 kHz, so 9613 Bd**; its tones -10.9 and +1.2 kHz from where the burst
  was cut (the carrier 917.2999 MHz, deviation +-6 kHz).
- Occupied bandwidth by the 99% rule said 1.9 MHz: meaningless here, the cut
  was 2 MHz wide and mostly noise. Read the width off the close-up instead.

## 5. Demodulate

Per transmission (`gridstream2.py`):

1. Cut from 6 ms before to 6 ms after, shift to 0 Hz, decimate 40 MS/s to
   200 kS/s (two FIR stages, 20 then 10), low-pass at 25 kHz (`filtfilt`).
2. Quadrature discriminator: `angle(z[n] conj(z[n-1])) fs / 2 pi`.
3. The burst's extent: power over 6x the median of its first 3 ms.
4. The slicing level: midway between the 10th and 90th percentiles of the
   frequency over the burst.
5. The symbol rate from the preamble (4 ms of it, FFT zero-padded to 2^18,
   parabolic peak), then the bit timing from a **DPLL**.
6. Each symbol's value: the mean frequency over its middle half.

**The first filter was too wide.** At +-50 kHz the discriminator was mostly
noise: 397 runs, most a sample or two long, and a symbol rate fitted to them
of 142.9 kBd with a 7.6% misfit. +-20-25 kHz, and the rate from the preamble
rather than from run lengths, fixed it.

### Timing: why a DPLL

| Timing | CRC good (of 53) | What failed |
|---|---|---|
| Fixed 9600 Bd, the best phase, a fixed grid | 9 | The 77-byte packet: 0.13% off, half a symbol adrift by about byte 37 |
| The preamble's rate, a fixed grid | 9 | A 29-byte packet whose preamble said 9592 Bd; the loop settled at 9611, and 0.2% is enough over 29 bytes |
| **The preamble's rate and a DPLL** | **10** | none with a CRC (the `D2` packets below) |

The meters' clocks differ (9597-9617 Bd), and 5 ms of preamble gives a rate
good to only about +-0.1%, so no fixed grid holds across a 700-symbol
packet. UART framing forgives some drift, since every byte starts on an
edge, which is why a fixed grid still got 9.

The DPLL moves each symbol boundary toward every edge it sees (a
second-order loop: phase gain 0.1, period gain 0.0005). Its timing error
stayed at 0.05-0.15 of a symbol (rms) on every packet, the longest
included. **Its first version made things worse** (6 packets): it took edges
from the raw discriminator, which chatters with noise, and one packet's
period ran off to 8747 Bd. What fixed it:

- edges from the frequency smoothed over a quarter symbol;
- only edges within 0.35 of a symbol of an expected boundary;
- the period held within 1% of the preamble's.

## 6. The encoding

The workflow's framings, tried in turn on the first packet's 330 bits:

- **Manchester**, both conventions, both offsets: 63-85 bad pairs in 140.
- **NRZ bytes after the preamble**, 8 offsets: nothing recognisable.
- **UART 8N1**, LSB first: every frame after the preamble framed right, and
  it read `00 FF 2A`, then a type and a length that matched what followed.

The packet, as rtl_433's decoder lays it out (`src/devices/gridstream.c`):

```
AA AA AA AA AA        preamble (1010...), not framed
00 FF 2A              sync (v5: the FF frame has 11 in place of its start bit)
D5                    type: mesh routing, two meter IDs
LL LL                 length: every byte after it, the CRC included
CI                    control information: the first byte the CRC covers
AAAAAAAA              destination meter ID
BBBBBBBB              source meter ID
NN                    counter
...                   payload
CC CC                 CRC-16
```

Lengths seen: 0x11, 0x16, 0x17 and 0x47. The long 0x47 packets carry a Unix
time (bytes 14-17 counting from `2A`) and an uptime (22-25). Ours read the
recording's own time to within a second, and an uptime of 364.5 days.
**Read the uptime at 22-25**: the four bytes straight after the time
(18-21) are something else, and taking them for the uptime gave "28.5 hours"
at first.

`D2` packets are short: `00 FF 2A D2`, a one-byte length, a CI, data, two
check bytes.

**Two ways the first decoder lost packets:**

- It searched for the v4 sync bit for bit, so every v5 packet was "no
  sync". Search for both.
- It stopped at the first framing error. Read the length from the header,
  then exactly that many frames, and let the CRC judge.

## 7. Confirm by the CRC

The CRC was found from the packets themselves, in three steps:

1. **The polynomial, free of init and final XOR.** A CRC is linear, so for
   two messages of one length `crc(a) ^ crc(b) = crc0(a ^ b)`, the bare
   polynomial's CRC of their XOR, whatever the init and final XOR. Three
   packets of 23 bytes and three of 29 made six such pairs; **0x1021 fitted
   all six** (the other polynomials tried fitted none).
2. **The init**, with 0x1021, by trying all 65536 on the six packets
   together, for each place the CRC might start: only "after the length"
   gave a final XOR of zero, with init 0xB5E3.
3. **An independent check**: the seventh packet, a length none of the six
   had and kept out of the solve, passed too.

Init 0x45FC with final XOR 0xF01F fits every packet equally; 0xB5E3 with no
final XOR is the form rtl_433 uses.

**A numpy search over all 32768 polynomials at once gave nonsense** (every
polynomial "fitting"): a mistake in its integer arithmetic, and a test loop
that skipped every pair. A plain-Python check of four polynomials exposed
both. Check a vectorised search's hits by hand before believing them.

The `D2` packets fail with 0xB5E3, and no init fits both of the two we
have, from any starting byte. rtl_433 says a `D2` frame with CI 0x52 ends in
an AES authentication tag, not a CRC; ours had CI 0x21 and 0x53. Two samples
are too few to say more.

## A second decoder: rtl_433

rtl_433 has had a Gridstream decoder since master of 2024-12-31
(`-R 271/272/273`, 9.6k/19.2k/38.4k); apt's 23.11 predates it. Built from
master with 0xB5E3 added to `known_crc_init[]` (a one-line change), it
decoded 4 of the transmissions, all with a good CRC under network ID
`b5e3`, agreeing with ours field for field, and found the first v5 packet
ours had missed.

Getting it to see anything took three tries:

- **Keep words out of the file name.** rtl_433 reads settings from a file
  name's words: `100k` in one was taken for a sample rate, and another name
  gave "Input format invalid".
- **Keep the noise bandwidth narrow.** At 1 MS/s the packets stood only 2-5
  dB over the noise (a 20 kHz signal in 1 MHz of noise) and rtl_433 found no
  pulses at all. Filtered to +-30 kHz and decimated to 250 kS/s they stood
  6-14 dB up, and it decoded.
- Put the signal off DC (here +50 kHz), as a radio's would be.

With `-M time:rel` on that 250 kS/s file, its times came out a quarter of
the true offsets.

Ours still decoded the weaker packets rtl_433 did not see: it measures each
packet's tones and picks its timing before deciding bits, where rtl_433 must
first find the burst over the noise.

## Not done

- The regular transmitter on 912.70, 919.49 and 907.50 MHz, every 1.7 s,
  about 22 ms a packet: no sync in any of them. Another device, or other
  settings.
- The `0x55` broadcast packets (a meter's WAN address, uptime, ID): none in
  5 s; a longer capture would have them.
- What the rest of a `D5` payload means: neither source documents it.

## Sources

- [Landis+Gyr GridStream Protocol, RECESSIM wiki](https://wiki.recessim.com/view/Landis+Gyr_GridStream_Protocol):
  framing, the v4/v5 sync, packet types, the CRC and its per-utility init.
- [rtl_433 PR #2616](https://github.com/merbanan/rtl_433/pull/2616/files) and
  `src/devices/gridstream.c` on master: byte offsets, the CI byte, the init
  table, `D2`/CI 0x52 encryption.
- [gr-smart_meters](https://github.com/BitBangingBytes/gr-smart_meters): a GNU
  Radio decoder for Gridstream.
- [swannman/gridstream-protocol](https://github.com/swannman/gridstream-protocol):
  a capture corpus from one utility, which rtl_433's CI handling was checked
  against.
