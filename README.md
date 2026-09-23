# FM Receiver

An FM broadcast receiver for SDRs (Signal Hound BB60D, HackRF One, Ettus
USRP) in three tabs:

- **Sweep (FFT)** covers a span wider than the radio can see at once (the
  whole FM band, or anything up to 6 GHz) as one spectrum and waterfall, and
  lists the stations it finds. The BB60D and HackRF sweep in the device
  (the BB60D in dBm, with AGC, and a real-time mode for spans up to 27 MHz);
  a USRP's LO is hopped and the FFTs stitched.
- **Receive (IQ)** runs the radio at a narrow IQ bandwidth and demodulates one
  station: stereo audio, RDS/RBDS, the multiplex spectrum.
- **Recordings** lists what you recorded and plays it back.

The Sweep and Receive views have dials for span, reference level, amplitude
range and averaging. The audio has mute and volume. You can record the audio
(WAV) and the IQ (channel or whole band, with SigMF metadata), and play IQ
recordings back as if they were a radio. The status line warns of overload
(on a HackRF it always shows the share of samples clipped).

```sh
./fm-receiver                # opens straight into the window
./fm-receiver --help
```

- [knowledge/usage.md](knowledge/usage.md): how to use it
- [knowledge/capabilities.md](knowledge/capabilities.md): what it can do, and what has been verified
- [knowledge/roadmap.md](knowledge/roadmap.md): what is planned next
- `tools/fm_receiver/`: the code
- `tools/tests/`: tests (`python tools/tests/run_all.py`)

Runs on Linux and on a Mac with Apple Silicon, in the `gnu` conda
environment (GNU Radio 3.10, PyQt5, pyqtgraph, SoapySDR):
`conda env create -f environment.yml`. The BB60D needs Signal Hound's
library as well; see *Setting up* in [usage.md](knowledge/usage.md). Parts
of it are adapted from the RF bench toolkit at `/data/python/SDR`; the
files that were copied say so at the top.
