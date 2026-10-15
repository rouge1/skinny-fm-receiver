# Silver Spring Networks mesh: finding and decoding it off air

How a second 902-928 MHz hopping network, found in the ISM survey of
2026-10-14 (BB60D, Receive at 40 MS/s), was identified as a Silver Spring
Networks (Itron) narrowband FHSS mesh and its frames decoded. It follows the
steps of [workflow.md](workflow.md); [gridstream.md](gridstream.md) is the
same method on a different network. This records what we measured, what
worked and what was wrong on the way.

The addresses in these frames belong to real devices near the bench, and
this repo is public. No address, seed table, channel table or payload byte
from the capture is written here; the recordings, cuts and scripts live in
the scratchpad or in `recordings/`, which git ignores.

## The result

| Item | Value |
|---|---|
| System | Silver Spring Networks / Itron FHSS mesh (rtl_433 decoder 383, `silver_spring_mesh.c`) |
| Band | 902-928 MHz, hopping; centres sit on a **300 kHz** step: `f = 978.8 MHz - 0.3 MHz x channel` fits every frame |
| Modulation | 2-FSK, constant envelope, **h about 0.5** at every rate |
| Rates | **100, 150, 200 and 300 kBd**, deviation +-25, +-37, +-51, +-78 kHz |
| Preamble | `0x55...` as the radio saw it: **the bit stream is the complement** of the standard one (`0xAA...`) |
| Sync (SFD) | as seen `0C 5F`; complemented it is **`F3 A0`**. rtl_433 searches both polarities |
| Header (3 bytes) | `channel(8) | FCTRL(4) EXT(1) length-high(3) | length-low(8)`; FCTRL = 0 and EXT = 0 on every frame |
| Body | scrambled PSDU(`length`) then scrambled FCS(4) |
| Scrambler | additive 8-bit LFSR `x^8+x^4+x^3+x^2+1` (tap 0x8E), output bit 7, **seed per hop**, found by trying 1-255 |
| FCS | CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xFFFFFFFF, no final XOR, MSB first) over the descrambled PSDU |
| Result | 32 of 38 cut bursts verify the full CRC-32; 26 of the 27 frames from the first 100 kBd pass checked 3 of 4 FCS bytes |

The seed is not the channel byte and not a plain function of it; the same
channel gives the same seed (checked on a dozen repeats), so a table could be
built per network, but the decoder recovers it by CRC every time.

## What a frame carries

After descrambling, the PSDU is `FCTRL | [destination EUI-64] | [source
EUI-64] | TLVs`. FCTRL bit 0 adds the destination, bit 1 the source. Every
EUI-64 seen in the headers carried the Silver Spring OUI (`00:13:50`) with
`05 01` as the next two bytes; the route objects inside the body list the
next hops as `00:13:50:FF:FE:...` (an EUI-48 padded with `FF FE`). Itron's own
`00:07:81` also occurs in rtl_433's corpus.

TLVs are a 2-byte header then a value. The first bit selects the layout: DLL
(9-bit type, 7-bit length) or MPDU (5-bit type, 11-bit length). What the
frames were:

| Frame class | Seen as | Carries |
|---|---|---|
| Poll (FCTRL 3) | 100 kBd, always | CLI (D2), channel-sync (D8) |
| Poll acknowledgment (FCTRL 1, bare CLI) | the exchange's rate | CLI |
| Data (FCTRL 1, sequence + MPDU) | the exchange's rate | seq (D4), RSSI (D9), FET (D1), CLI, route or IPv6 (M16), M20 |
| Data acknowledgment (FCTRL 1, bare RSSI) | the exchange's rate | RSSI, FET |
| Broadcast (FCTRL 2, source only) | 100 kBd | FET, route advertisement (M16, always the same shape), nested M17, and an inner CRC-32 the walk verifies |

Two things worth knowing: every poll was at 100 kBd, and **the answers came
back at a higher rate** (200 or 300 kBd in six of seven poll
acknowledgments, 100 in one), with the data at 150-300 kBd, so a decoder
fixed at one rate sees half of every exchange; and the four broadcasts' own inner CRC passed,
which checks the TLV walk independently of the outer FCS.

RSSI came out -87 to -98 in signed-byte units (a candidate, not calibrated).
The management payload (UDP 648) is encrypted and was not touched.

## 1. Find the signal

```sh
tools/fmctl 'mode sweep; radio bb60; start; sweep 900 930; peakhold on; wait 120; peaks 6 902 928'
```

About 20 hops from -47 dBm down, 70-500 kHz wide at RBW 4.9 kHz. Many sat
on 200 kHz marks and half on the marks between them: the first guess, a
200 kHz plan from 902.2 MHz, was wrong; 300 kHz is what the channel byte
shows.

## 2. Record the IQ

```sh
tools/fmctl 'mode receive; tune 915; center 915; capture 8'
```

7.6 s of the band, 2.4 GB. A hop is on one channel for 1.4-11 ms, so a
single channel records almost nothing: capture the band, and take several
captures to get dozens of frames (five captures gave 27 clean 100 kBd
frames; the 4 GB cap is about 12 s).

## 3. Waterfall pictures, and finding every burst

The waterfall of the band shows a few short dashes. A script finds every
transmission: a 2048-point STFT (51 us, 19.5 kHz bins), cells 12 dB over each
bin's median, closed and labelled, then cut to the burst (shift to 0 Hz,
decimate by 16 to 2.5 MS/s). Drop events at the band edges (the BB60D's
filter skirt gives false ones at 898 and 931 MHz).

## 4. Detect the modulation

The cut burst: a flat envelope (CV 0.02), two frequency levels, zero-crossing
intervals in multiples of one symbol period. `iq-measure` says "noise" on
these cuts: its band is the whole capture and a 3 ms burst is below its
floor estimate. Measure the cut, not the file.

## 5. Demodulate

Centre the burst, low-pass at 0.7 x the rate, FM-discriminate, smooth over a
third of a symbol, slice at the median. The timing was found by trying the
phases and a few percent of rate, picking the largest `mean|f| / std|f|`; a
clean burst scored 17-22, a wrong rate 1.5. At 100 kBd that is 25 samples per
symbol on a 2.5 MS/s cut. The rate was found per burst by trying 100, 150,
200 and 300 kBd and keeping the one that gives an alternating preamble
followed by the sync: the right rate gives 55-225 alternating bits, a wrong
one 3-12.

## 6. Detect the encoding

The body looked random in every test that works on bytes (entropy 7.86 of 8,
no CRC at any offset). It was not: frames of different length shared runs of
147-151 identical bits at offsets that are not byte-aligned (a random pair
gives 23), and one frame held segments with linear complexity 9 (random
about 64). An 8-bit LFSR does that, and a byte-position comparison cannot
see it. The next step was to read a public decoder, not to guess.

## 7. Recover the data, and confirm

- Complement the bits; `F3 A0` appears. The next three bytes read FCTRL = 0
  and a length that equals the frame's own size minus 9 (sync 2, header 3,
  FCS 4).
- Try the seed 1-255; keep the one whose CRC-32/MPEG-2 over the descrambled
  PSDU equals the descrambled FCS. About 1 in 16 million per seed by chance
  on a full 4-byte FCS.
- Walk the TLVs as `silver_spring_mesh.c` documents; the inner DLL-6 CRC
  confirms the walk on broadcast frames.

## What went wrong, so it is not repeated

- **A fixed-grid assumption**: "200 kHz channels from 902.2 MHz" was copied
  from a plan, not measured. The channel byte gave the 300 kHz step; test a
  grid against all the centres, not three.
- **"Differentially coded"**: XOR of adjacent bits turns any constant sync
  into another constant, so it proves nothing. A claim about line coding needs
  a test that a constant would fail.
- **Entropy of about 1500 pooled bytes**: the plug-in estimate of random data
  is 7.87, not 8.0, so 7.86 says nothing.
- **Byte positions only**: a scrambler seeded per hop and a frame not aligned
  to bytes defeats position-by-position XOR. Compare bit-level longest common
  substrings against a shuffled null.
- **Search in code only**: "no public decoder uses sync 0C5F" came from
  grepping code. The decoder existed; the sync was its complement. Before
  ruling a system out on its sync, test the complement, bit-reversed and
  byte-swapped.
- **Trimmed bursts**: cutting 50 samples off each end of a burst loses the
  last FCS byte. Read the frame to the length the header gives.
- **rtl_433 on a small cut, then on bursts**: built from master (the apt 23.11
  has no decoder 383; it needs `-s 1600k`), it detected the FSK burst but gave no
  packet from a 3 ms cut file, and again none from `iq-decode --bursts -R 383` on
  120 bursts at 1.6 MS/s, each scaled and padded. Its analyser saw 33 pulses in a
  4.4 ms burst of about 440 symbols and guessed "No clue": the FSK detector does
  not resolve 10 us symbols at h = 0.5 here. The cause was not established, and
  rtl_433 is not claimed to decode this network. The decode in this file is the
  scripts' own, following the layout the decoder documents.

## The tools this produced (2026-10-15)

The scripts that did the work above became `tools/iq-demod`, `tools/iq-frames`
and the opt-in `iq-decode --bursts`, with the catalog of
[catalogs.md](catalogs.md); [usage.md](usage.md) has the commands. On the same
7.6 s capture they give, without being told the system: 320 bursts, 87 with a
32-bit preamble, 33 aligned on `F3 A0` (the complement of the sync as sliced),
and one accepted hit, the Silver Spring LFSR with CRC-32/MPEG-2 at the payload's
start, on 22 of 26 distinct checked regions (adjusted p about 1e-150). The 30
bursts of the 32 verified by hand that it found were found at the right rate.

What the first version got wrong, fixed by the synthetic tests:

- a sync consensus that swallowed the preamble's last bit (frames one bit out of
  byte alignment), and that dropped a frame with one bit error;
- the same frame heard twice, or frames sharing their checked bytes, counted as
  independent evidence: a fixed-seed scrambler and an 8-bit CRC "matched" 12 of
  64 frames. Hits now count distinct checked regions;
- a free per-frame seed makes the keystream's start offset indistinguishable
  from the CRC's, so five start offsets reported one hit; reported once now;
- a discriminator that ran on noise after the burst corrupted the last bit, the
  frame's own check byte; it is now gated by the envelope.

## Mapping the mesh from the route fields (2026-10-15)

The frames carry their own topology, so a map needs no extra radio work: only
the verified PSDUs. Three field types give links.

| Source | Layout (rtl_433 decoder 383's comments) | Link it gives |
|---|---|---|
| Poll (FCTRL 3) | destination then source EUI-64 | sender polled that node |
| Route advertisement (M16, first byte `0x21`, in broadcasts) | `0x21`, max hop, `0x1000`, 16 zero bytes, egress EUI-64, descriptor (next-hop count, 3 flag bytes, path cost 2, link cost 2), then count x next-hop EUI-64; 36 + 8 x count bytes | advertiser reaches that egress through that next hop, at a raw cost |
| Source route (M16 with a 4-byte header, in data frames) | protocol id, TTL, route offset (6 bits), address count (6 bits), then count x EUI-64 | the listed addresses are a path; the offset indexes the frame's own receiver |

Two address forms occur: an EUI-64 with `05 xx` after the OUI is a node, and
an EUI-48 padded with `FF FE` is a router or egress point (the adverts name
the egress this way). A replies' address field is the polled node's; replies
carry no source.

Result on five 7.6-10 s captures of the 915 MHz band, 82 distinct verified
frames (47 polls, 26 acknowledgments or data, 9 broadcasts): 51 nodes and 5
routers, 77 links. Three nodes poll many others (one polls about 11), every
advertiser lists routes to two different egress points, and a source route
listed in one data frame lays the same routers and next hops that the adverts
name. The 65 frames beyond the first capture came from the demodulated bits of
the other four captures (their IQ no longer existed), run through the same
descramble and CRC check.

Limits: the direction of a source route is the listed order, not proven;
path costs are raw candidates; a link seen once in a few seconds says nothing
about links not seen; and the map is as complete as the CRC-verified frames,
which were 65 of 249 bursts in the later captures. The pictures and address
lists are kept in the git-ignored `recordings/` and not in this repo.

## Sources

- rtl_433 `src/devices/silver_spring_mesh.c` (decoder 383): header, scrambler,
  FCS, TLV and route layouts, flex line
  `-X "n=ssnmesh,m=FSK_PCM,s=10,l=10,r=5000"`.
- Patents it cites: US20090303972A1 (scrambled payload, per-channel seed),
  US20090310511A1 (MAC format), US20080051036A1 (network header).
