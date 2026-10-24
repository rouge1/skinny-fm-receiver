# FM Receiver

An FM broadcast receiver for SDRs (Signal Hound BB60D, HackRF One, RTL-SDR,
Ettus USRP) in three tabs:

- **Sweep (FFT)** covers a span wider than the radio can see at once (the
  whole FM band, or anything up to 6 GHz) as one spectrum and waterfall, and
  lists the stations it finds. The BB60D and HackRF sweep in the device
  (the BB60D in dBm, with AGC); a USRP's or RTL-SDR's LO is hopped and the
  FFTs stitched. An RTL-SDR can be on another computer: the app runs
  `rtl_tcp` there over ssh.
- **Receive (IQ)** runs the radio at an IQ bandwidth (up to 27 MHz on the
  BB60D) and demodulates one station: stereo audio, RDS/RBDS, the multiplex
  spectrum.
- **Recordings** lists what you recorded and plays it back.

The Sweep and Receive views have dials for span, reference level, amplitude
range and averaging (A fits the scale to the trace). The audio has mute and volume. You can record the audio
(WAV) and the IQ (channel or whole band, with SigMF metadata), and play IQ
recordings back as if they were a radio. The status line warns of overload
(on a HackRF or RTL-SDR it always shows the share of samples clipped).
`tools/fmctl` works the open window from a script or an LLM: tuning, gain,
sweeps, the view's dials (`view`), the signals on the spectrum as numbers
(`peaks`), screenshots, IQ captures, and what is in a capture (`measure`,
`cells`).

Tools for what a recording holds, each run on a file:

- `tools/iq-measure` and `tools/iq-picture`: a signal's card (level,
  bandwidth, bursts, modulation class, LTE and 5G NR cells) and a waterfall
  picture (`--cells` boxes the cells' sync signals).
- `tools/iq-demod`, `tools/iq-frames`, `tools/iq-decode`: bursts to bits,
  frames, a scrambler and CRC search, and rtl_433 on a span.
- `tools/iq-lte` and `tools/iq-nr`: the LTE and 5G NR cells in a capture
  (cell ID, timing), and with `--decode` what each tower broadcasts to every
  phone (operators, tracking area, cell identity, neighbour carriers, network
  time). Only the broadcast is read, and nothing is sent. The decode is done
  by srsRAN, which you build separately; it is not part of this repo.
- `tools/dvr-hd`, `tools/dvr-sweep`, `tools/dvr-to-iq`: Sceptre DVR files (HD
  Radio from one, its sweep spectra, a channel of it as a `.cfile`).

```sh
./fm-receiver                # opens straight into the window
./fm-receiver --help
tools/fmctl status           # work the open window from a script (fmctl help)
```

- [knowledge/usage.md](knowledge/usage.md): how to use it
- [knowledge/capabilities.md](knowledge/capabilities.md): what it can do, and what has been verified
- [knowledge/roadmap.md](knowledge/roadmap.md): what is planned next
- [knowledge/lte-verify.md](knowledge/lte-verify.md),
  [knowledge/nr-verify.md](knowledge/nr-verify.md): how LTE and NR cells are
  found and their broadcast read ([glossary](knowledge/glossary.md))
- `tools/fm_receiver/`: the code
- `tools/tests/`: tests (`python tools/tests/run_all.py`)

Runs on Linux and on a Mac with Apple Silicon, in the `gnu` conda
environment (GNU Radio 3.10, PyQt5, pyqtgraph, SoapySDR):
`conda env create -f environment.yml`. The BB60D needs Signal Hound's
library as well; see *Setting up* in [usage.md](knowledge/usage.md). Parts
of it are adapted from the RF bench toolkit at `/data/python/SDR`; the
files that were copied say so at the top.
