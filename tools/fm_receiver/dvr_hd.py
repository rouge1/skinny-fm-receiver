"""HD Radio from a Sceptre DVR's IQ, decoded offline with nrsc5.

The app plays a DVR on a loop, and nrsc5 loses sync at each seam, so the
slower programs (HD2, HD3) break up. Read once through, each program comes
out whole. This is the library behind ``tools/dvr-hd``; it needs only numpy
and scipy (no GNU Radio), and the ``nrsc5`` program.

For a station: mix it to 0 Hz, decimate by whole steps to about 2 MS/s
(filtering in chunks, each with its overlap trimmed, or the joins glitch
every few milliseconds), resample to nrsc5's **744,187.5 S/s** and write
complex float32. nrsc5 -r FILE --iq-input-format cf32 -o out.wav -t wav N
then decodes program N (0 is HD1). At any other rate nrsc5 exits cleanly with
an empty WAV and no log at all.
"""

import os
import re
import shutil
import subprocess

import numpy as np
from scipy import signal

from .dvr_iq import Dvr, EDGE_HZ  # noqa: F401

#: nrsc5's input rate for cf32 and cs16, FM.
NRSC5_RATE = 744187.5
#: The rate the first stage decimates to, at least.
MID_RATE = 2e6
#: The stereo pilot's frequency, and where to look for it (Hz).
PILOT_HZ = 19000.0
_EXTRA = ('/usr/local/bin', '/opt/homebrew/bin', os.path.expanduser('~/.local/bin'))


def find_nrsc5(path=None):
    """The nrsc5 program's path (``path`` if it is one), or None."""
    if path:
        return path if os.access(path, os.X_OK) else None
    found = shutil.which('nrsc5')
    if found:
        return found
    for folder in _EXTRA:
        candidate = os.path.join(folder, 'nrsc5')
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def raster(lo_hz, hi_hz, first=87.9e6, step=200e3):
    """The FM channels (US raster) inside ``lo_hz`` to ``hi_hz``."""
    n = int(np.ceil((lo_hz - first) / step))
    out, f = [], first + max(n, 0) * step
    while f <= hi_hz + 1:
        out.append(round(f, 1))
        f += step
    return out


def channelize(dvr, station_hz, out_path=None, seconds=None):
    """The station at 0 Hz at NRSC5_RATE, as complex64 (the whole-number stage
    leaves at least 2 MS/s); written to ``out_path`` if given."""
    lo, hi = dvr.usable()
    if not lo <= station_hz <= hi:
        raise ValueError(f"{station_hz / 1e6:.1f} MHz is outside the DVR's "
                         f"usable band, {lo / 1e6:.2f} to {hi / 1e6:.2f} MHz")
    return dvr.extract(station_hz, NRSC5_RATE, out_path, seconds=seconds,
                       min_mid=MID_RATE)


def pilot_scan(dvr, channels, seconds=0.75):
    """For each channel (Hz), the stereo pilot's SNR in dB and deviation
    in kHz, from the first ``seconds``: ``[(hz, snr_db, dev_khz)]``."""
    decim = 120 if dvr.rate > 20e6 else max(1, int(dvr.rate // 233e3))
    n = min(dvr.samples, int(seconds * dvr.rate))
    x = dvr.reader.read(0, n)
    t = np.arange(n, dtype=np.float64)
    rows = []
    for hz in channels:
        w = 2 * np.pi * (hz - dvr.center_hz) / dvr.rate
        y = signal.resample_poly(
            x * np.exp(-1j * ((w * t) % (2 * np.pi))).astype(np.complex64),
            1, decim, window=('kaiser', 7.0))
        fs = dvr.rate / decim
        d = np.angle(y[1:] * np.conj(y[:-1])) * fs / (2 * np.pi)
        f, p = signal.welch(d, fs, nperseg=1 << 14)
        sel = (f > 18.6e3) & (f < 19.4e3)
        k = int(np.argmax(p[sel]))
        fp = f[sel][k]
        tone = (f > fp - 60) & (f < fp + 60)
        noise = np.median(p[(f > fp - 1500) & (f < fp + 1500) & ~tone])
        rows.append((hz, float(10 * np.log10(p[sel][k] / noise)),
                     float(np.sqrt(2 * (p[tone] - noise).clip(0).sum() * f[1]) / 1e3)))
    return rows


_SERVICE = re.compile(r'SIG Service: type=audio number=(\d+) name=(.*)')
_RATE = re.compile(r'Audio bit rate: ([\d.]+) kbps')
_MER = re.compile(r'MER: (-?[\d.]+) dB \(lower\), (-?[\d.]+) dB \(upper\)')
_BER = re.compile(r'BER: [\d.]+, avg: ([\d.]+)')
_STATION = re.compile(r'Station name: (.*)')
_SLOGAN = re.compile(r'Slogan: (.*)')


def summarise(log):
    """What nrsc5's log says: services by number, the last bit rate, MER
    (lower, upper), BER, station name and slogan; ``synced`` if it locked."""
    def last(pattern, cast=str):
        found = pattern.findall(log)
        return cast(found[-1]) if found else None

    mers = _MER.findall(log)
    return {
        'synced': 'Synchronized' in log,
        'station': (last(_STATION) or '').strip() or None,
        'slogan': (last(_SLOGAN) or '').strip() or None,
        'services': {int(n): name.strip() for n, name in _SERVICE.findall(log)},
        'kbps': last(_RATE, float),
        'mer_db': (float(mers[-1][0]), float(mers[-1][1])) if mers else None,
        'ber': last(_BER, float),
    }


def decode(nrsc5, iq_path, program, wav_path, timeout=300):
    """Run nrsc5 on a cf32 file for program ``program`` (0 is HD1).

    Returns ``(summary, seconds of audio)``; the WAV is removed if it holds
    none (nothing decoded)."""
    proc = subprocess.run(
        [nrsc5, '-r', iq_path, '--iq-input-format', 'cf32', '-o', wav_path,
         '-t', 'wav', '-l', '2', str(program)],
        capture_output=True, text=True, timeout=timeout)
    info = summarise(proc.stderr + proc.stdout)
    seconds = 0.0
    if os.path.exists(wav_path):
        try:
            import wave
            with wave.open(wav_path) as w:
                seconds = w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            seconds = 0.0
        if seconds <= 0.05:
            os.remove(wav_path)
            seconds = 0.0
    return info, seconds
