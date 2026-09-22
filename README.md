# FM Receiver

An FM broadcast receiver for SDRs (Signal Hound BB60D, HackRF One, Ettus
USRP) with two modes:

- **Sweep (FFT)** hops the radio across a span wider than it can see at once
  (the whole FM band, or anything up to 6 GHz) and stitches the FFTs into one
  spectrum and waterfall. It lists the stations it finds.
- **Receive (IQ)** runs the radio at a narrow IQ bandwidth and demodulates one
  station: stereo audio, RDS/RBDS, the multiplex spectrum.

Both views have dials for span, reference level, amplitude range and
averaging. The audio has mute and volume. You can record the audio (WAV) and
the IQ (channel or whole band, with SigMF metadata), and play IQ recordings
back as if they were a radio.

```sh
./fm-receiver                # opens straight into the window
./fm-receiver --help
```

- [knowledge/usage.md](knowledge/usage.md): how to use it
- [knowledge/capabilities.md](knowledge/capabilities.md): what it can do, and what has been verified
- [knowledge/roadmap.md](knowledge/roadmap.md): what is planned next
- `tools/fm_receiver/`: the code
- `tools/tests/`: tests (`python tools/tests/run_all.py`)

Needs the `gnu` conda environment (GNU Radio 3.10, PyQt5, pyqtgraph,
SoapySDR). Parts of it are adapted from the RF bench toolkit at
`/data/python/SDR`; the files that were copied say so at the top.
