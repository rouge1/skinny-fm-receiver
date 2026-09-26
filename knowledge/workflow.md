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
