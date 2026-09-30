1)Find the Signal. Real-time spectrum and max-hold waterfalls. 
2)Record the IQ. Streamed to disk as complex samples with SigMF metadata
3)Take waterfall pictures. Spectrograms of the whole transmission and close-ups of 
  single bursts, used to measuer bandwidth, tones and timing.
4)Detect the modulation. Envelope (amplitude keying), instantaneous frequency (FSK), 
  occupied bandwidth and tone count.
5)Demodulate. Shift to 0 Hz, filter, decimate. Then evelope detection for CW/OOK
  or a quadrature discriminator for FSK/FM. Symbol rate from run-length clustering.
6)Detect the encoding. Try the common framings in turn: Morse timing, UART 8N1,
  Manchester (both conventions), NRZ bytes after a sync word, known protocols 
  such as POCSAG.
7)Recover the message/data
  Decode to text, then confirm it by agreement across repeats, by the packet's
  own CRC or BCH check, and other programs like rtl_433 decoder.

Steps 3 and 4 are the ones to make repeatable: measure the same things of
every signal (occupied bandwidth, the envelope's levels and timing, the
instantaneous frequency's levels, tones, symbol rate, burst length and repeat
interval, level in dBm) and let those name it, before any decoder is written.
There are too many modulations and protocols to write a decoder for each; see
the roadmap, Round 22. The RF bench toolkit (`/data/python/SDR`) can make
signals of known parameters to check the measurements on: ISM sensor frames
(OOK, PPM, PWM, Manchester), NTSC, RDS and FM video.
