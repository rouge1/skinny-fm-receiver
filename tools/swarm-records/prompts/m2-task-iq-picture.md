Task: write the command line tool tools/iq-picture (new, executable): a PNG picture of an IQ recording, a spectrum
panel (mean and peak) over a waterfall of the span.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (section "Tools" and the iqmeasure interfaces), CLAUDE.md, tools/dvr-sweep (the pattern for
a tool: argparse, sys.path insert of its own folder, `from fm_receiver import ...`), tools/fm_receiver/iqmeasure.py
(the m1 core, read-only) and the acceptance test tools/tests/test_iq_tools.py. The tests are the spec: do not edit any
test or any other file.

Edit only:
- tools/iq-picture (new file; make it executable: chmod +x)

Behaviour (CONTRACTS-measure.md "Tools"): `tools/iq-picture FILE OUT.png [--t0 S --t1 S] [--band LO HI] [--rate R
--center HZ] [--nfft N]`. Open the file with iqmeasure.open_iq (rate/centre as the contract), restrict to the span
(default all, at most the first 30 s) and band (absolute Hz), spectrum by numpy FFT (Hann window, averaged power for
the mean panel, max for the peak panel, waterfall rows of consecutive blocks). matplotlib with the Agg backend,
selected before pyplot is imported; units dBm when the source has full_scale_dbm, else dBFS; the title names the file,
centre, rate and span; axes are labelled with units (frequency in MHz or kHz as is readable, time in seconds, a
colour bar with the level unit); nothing clipped; at least 800 x 500 px (figsize 12x8 at dpi 110 as dvr-sweep is fine).
It writes only the PNG. Exit 1 with a message on stderr (and no PNG) on iqmeasure.IQSourceError. It imports no
gnuradio, PyQt5, library, radios (matplotlib and numpy only, plus fm_receiver.iqmeasure).
The module defines at module level `peak_panel(path, t0=None, t1=None, band=None, rate=None, center_hz=None,
nfft=None)` returning `(freq_hz, peak_db)`: numpy arrays, the absolute frequency axis and the peak-hold level the top
panel plots (the test uses it to find a two-tone file's lines). Importable without running main.

HARD BUDGET (measured by the manager): a 30 s, 1 MS/s complex64 .cfile must render in under 10 s and under 1 GB
extra memory. Read the file in chunks through `iqmeasure.open_iq(path).read(start, count)`, accumulate the mean and
peak spectra and the waterfall rows per chunk, never hold the file or a full-resolution waterfall in memory: bin the
waterfall to at most about 400 rows x 2000 columns (peak or mean of the cells) before drawing.

Done when the picture-related checks of `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py` pass (the iq-measure ones fail until tools/iq-measure exists; that is
another task). Also look at your own PNG by rendering a synthetic two-tone file and checking the dimensions.
End with a short summary: file changed, what you did, the test result, anything you could not do.
