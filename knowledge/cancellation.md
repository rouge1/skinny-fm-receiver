# Successive interference cancellation

How this receiver reads a weak cell sitting under a strong one (roadmap Round
28.5 for NR, 29.5 for LTE), why it works, where else the idea applies, and
its limits. The method and the measurements are in
[nr-verify.md](nr-verify.md#cancelling-a-stronger-sector-roadmap-round-285)
and [lte-verify.md](lte-verify.md#a-weaker-sector-under-a-stronger-one-cancellation-roadmap-round-295);
the code is `iqnr_phy.known_grid`, `fit_known` and `cancel`, and
`iqnr_decode.own_decode_cells` (NR), and `iqlte_phy.known_cell` with
`known=` on its decoders (LTE).

## The idea

Normally a stronger signal on top of a weaker one is just noise to the weaker
one. Once the stronger one is decoded, though, it is no longer unknown. From
its decoded bits the receiver can regenerate exactly what the transmitter
sent, reference signals included. The only unknown left is the radio channel
it came through, and thousands of known symbols pin that down far better than
the few pilots a normal receiver uses. So the receiver rebuilds it, fits it to
the recording, subtracts it, and reads what is underneath. Then it repeats
down the list, strongest first: that is the "successive" part.

## On the 850 MHz NR site (2026-10-25)

Three sectors of one site send their SIB1 and system information in the same
slots, on the same resource blocks.

- **Why combining alone failed.** The first sector sent the same thing as the
  second in every copy, about 13 dB louder. Summing copies made the
  interference grow exactly as fast as the wanted signal.
- **What made cancellation work:**
  - The phase fix (38.211 5.4's per-symbol phase taken at the cell's own
    carrier). Without it the "known" signal drifted about 330 Hz over every
    slot.
  - A channel model that follows that drift within a slot: a delay slope and
    a frequency, then per resource block a gain that moves over the slot. It
    removed 10 to 17 dB of the stronger sector on real air, and 30 dB in the
    synthetic test.
  - Subtracting only transmissions that passed their CRC, so a wrong decode
    is never subtracted.
  - Subtracting only transmissions whose slots line up in time, so another
    site's transmission is not removed by mistake.
- **The result:** the second sector's SIB1 in every one of its 143 copies,
  and the third sector's once. Without cancellation neither decoded at all.
- **The limits we hit.** Whatever the channel fit misses stays behind as
  residual, and the weak cell still has to clear the noise on its own. On the
  shared system information, about a third of what is left repeats from copy
  to copy and is not the first sector's channel mismatch or another cell
  sending the same bytes (Round 28.6). It sets a floor roughly 1.5 dB above
  the second sector, so its other SI decodes only rarely.

## On the 700 MHz LTE carriers (2026-10-26)

Two LTE carriers, each with two sectors of one site, frames aligned. The
sectors send their PBCH in the same REs every frame, and their SIB1 DCI and
SIB1 in the same subframes.

- **What is rebuilt.** LTE's reference signals are on in every subframe and
  known from the PCI alone, so the CRS, PSS, SSS and PBCH come back from the
  stronger cell's MIB without any other decode. Its PCFICH, SIB1 DCI and SIB1
  are added where each passed its CRC. The fit is simpler than NR's: per RB a
  gain and a slope in time for each antenna port and for the synchronisation
  signals, the port's PBCH and data riding on its CRS channel at the cell's
  own power ratio (a cell may send its CRS at another power: 1.5 to 3 dB
  under on one carrier here). LTE upconverts continuously, so there is no
  per-symbol phase to undo.
- **The pair (2 dB apart).** Combining alone (29.4) already read the weaker
  sector, by summing two or three copies of each 80 ms period. Cancelling the
  stronger one makes every copy decode alone.
- **The other carrier (9 dB apart).** Here neither technique works alone.
  Cancellation reads the weaker sector's MIB (summed over the 40 ms TTI),
  then its SIB1 DCIs (a fifth by summing copies), then its SIB1 (by
  summing, in every period). Each stage needed the stronger sector taken out.
- **What it costs.** Taking a cell out can disturb a marginal read of the one
  under it (a PCFICH turned once), so a subframe that gives nothing after
  cancelling is read again as it is.
- **Another site.** A cell whose frames start elsewhere straddles the
  reading cell's FFT windows, so it is fitted at its own timing (with the
  other cells of that timing: alone, one sector's fit takes in its
  neighbour's broadcast), modulated again and subtracted from the samples.
  On synthetic signals that reads a cell 10 dB down. The one candidate here,
  a third PCI on the other carrier, shows no CRS at its timing with the
  site gone, where a cell as strong as the site's second would.

Unlike the NR site, combining helped on top of cancellation here (no copy
of the second cell's SIB1 decoded alone; every period did summed). Likely
why: the two sectors' SIB1 differ (the cell identity), so what cancellation
leaves of the stronger one does not repeat in step with the weaker.

## Two techniques, two conditions

Cancellation and combining are different tools, and each needs something
different from the signal:

- **Combining** (Round 28.4) adds up repeats of the same transmission. It
  needs the repeats to be identical (or known variants, such as redundancy
  versions) and the noise to differ between them. It fails when the
  interference repeats too, as it did here.
- **Cancellation** needs the stronger signal to be *decodable and exactly
  regenerable*: a known modulation and coding, a check (a CRC) that the decode
  is right, and the timing and frequency to put it back where it was. It does
  not need the stronger signal to repeat.

Used together, cancellation removes what repeats and combining then averages
what does not.

## Can it be applied to Bluetooth, Wi-Fi and the rest of the ISM bands?

In principle yes, to any signal whose physical layer is known, and it is an
established technique in radio research (decoding collided packets, two
transmitters heard at once). Nothing here is built for these bands yet. How
well it would work differs:

- **Wi-Fi (802.11 OFDM).** Close to the NR case: OFDM, pilots, a preamble that
  gives the channel, a CRC on every frame. A stronger frame can be decoded,
  re-encoded and subtracted to read a weaker one that collided with it.
  Research systems have done this for collisions between Wi-Fi frames,
  including ones that collide the same way twice (the retransmission
  repeats), solving the two collisions together.
- **Bluetooth Low Energy.** GFSK, narrow (1 or 2 MHz), hopping. The modulation
  is not linear, so the stronger packet is regenerated by running the
  modulator itself, not a symbol map, and its frequency, deviation and timing
  are fitted rather than a per-subcarrier gain. Packets are short, which gives
  few symbols to fit with, and two packets must land on the same channel at
  the same time to collide at all. Advertising packets are repeated on three
  channels, so combining has material.
- **ISM telemetry (sensors, meters, remotes).** Usually simple FSK or OOK
  with a checksum, often sent several times in a row. Repeats suit combining;
  cancellation works when one transmitter's packets are decodable and another
  one's sit under them.

The physical layer does not need the payload to be readable: an encrypted
Wi-Fi frame is still bits with a CRC, and can be regenerated without being
understood. That does not change this app's rule. We decode only where
decoding is what classifies a signal, and for cellular only the broadcast;
the same would hold here. Nothing is ever transmitted.

## Any signal that repeats?

Repetition helps combining, not cancellation. For cancellation, what matters
is whether the interferer can be decoded and rebuilt. A repeating signal
whose structure is unknown (an unmodulated tone, say, or a radar pulse) can
still be subtracted, but by measuring it rather than decoding it: averaging
its repeats gives its shape. That works only when the wanted signal does not
repeat in step with it. Otherwise the wanted signal averages in too, which is
the trap combining fell into here.

## The limits

- **The decode must be right.** A wrong decode subtracted adds a signal
  instead of removing one. A CRC is the guard: no CRC, no cancellation.
- **How deep it goes is set by how well the channel is known.** The residual
  is the rebuilt signal times the error in the fitted channel. A channel that
  moves faster than the model, or a model with too few known symbols, leaves
  more behind. Here it was 10 to 17 dB on real air.
- **The transmitter and the receiver are not perfect.** Phase noise,
  amplifier distortion and the transmitter's own error vector are part of
  what was sent but not of what is rebuilt. Real transmitters are allowed
  errors of a few percent (around -15 to -30 dB), which caps the depth. The
  receiver's linearity and ADC resolution cap it too: a strong signal that
  overloads the front end distorts the weak one under it in a way no
  subtraction undoes. An 8-bit radio (the HackRF, the RTL-SDR) has less room
  than the BB60D.
- **The weak signal still has to clear the noise.** Cancellation removes
  interference, not noise; combining is what helps against noise.
- **Timing and frequency.** The rebuilt signal must line up with the received
  one to a small fraction of a symbol, and its frequency offset must be
  followed. On the NR site that took fitting the drift within each slot.
- **One stronger signal at a time.** Each cancellation needs that signal
  decoded first, so the weakest cell waits for every stronger one, and the
  cost grows with the number of cells (about 195 s for three NR cells against
  50 s for one).
