"""A Sceptre DVR's IQ, cut to any channel: ``Dvr.extract`` and ``write_iq``.

Behind ``tools/dvr-to-iq`` (a DVR to a ``.cfile`` and ``.sigmf-meta`` that
this app, SDR++, GNU Radio and inspectrum can read) and ``tools/dvr-hd``.
Plain numpy and scipy - no GNU Radio.

A channel is the DVR's IQ mixed so ``center_hz`` is at 0 Hz, low-passed and
resampled to ``rate``: whole-number steps first (filtered in chunks, each with
its overlap trimmed, or the joins glitch every few milliseconds), then one
rational resample. Values are in units of the ADC's full scale (1.0) as the
app reads them, or with ``units='mw'`` in Sceptre's own square root of
milliwatts, so that ``|x|**2`` is power in mW (see ``sceptre_dvr``).
"""

import json
import os
import time
from fractions import Fraction

import numpy as np
from scipy import signal

from . import sceptre_dvr

#: A channel's edge: the tuner must keep this far from the DVR band's edge
#: when the channel's width is not known (a station's ``EDGE_HZ`` each side).
EDGE_HZ = 400e3
CHUNK = 1_000_000                       # output samples of the first stage per chunk


class Dvr:
    """A scanned Sceptre DVR, ready to be channelized."""

    def __init__(self, path):
        self.path = path
        self.layout = sceptre_dvr.scan(path)
        self.reader = sceptre_dvr.Reader(path, self.layout)
        self.rate = self.layout['rate']
        self.center_hz = self.layout['center_hz']
        self.samples = self.layout['samples']

    @property
    def seconds(self):
        return self.samples / self.rate

    def usable(self):
        """(low, high) in Hz: where a station's whole channel is in the band."""
        half = self.rate / 2 - EDGE_HZ
        return self.center_hz - half, self.center_hz + half

    @property
    def scale(self):
        """One count in sqrt(mW), from the runs' headers (None if absent)."""
        return self.layout['scale']

    @property
    def full_scale_dbm(self):
        return self.layout['full_scale_dbm']

    def extract(self, center_hz, rate, out_path=None, start=0.0, seconds=None,
                min_mid=None, units='full'):
        """The channel at ``center_hz``, ``rate`` wide in samples/s, as
        complex64 (written to ``out_path`` if given, then not kept).

        ``start`` and ``seconds`` cut the DVR's time. ``min_mid`` is the least
        rate the whole-number stage may leave (default: 1.25 times ``rate``,
        or the DVR's own for a channel wider than that). ``units`` is
        ``'full'`` (1.0 is the ADC's full scale) or ``'mw'`` (``|x|**2`` is
        power in mW, needs the DVR's scale)."""
        half = self.rate / 2
        if rate > self.rate + 1e-6:
            raise ValueError(f"{rate / 1e6:g} MS/s is more than the DVR's "
                             f"{self.rate / 1e6:g} MS/s")
        if abs(center_hz - self.center_hz) + rate / 2 > half + 1:
            raise ValueError(
                f"a channel {rate / 1e6:g} MHz wide at {center_hz / 1e6:.3f} MHz "
                f"is not inside the DVR's band "
                f"{(self.center_hz - half) / 1e6:.3f}-{(self.center_hz + half) / 1e6:.3f} MHz")
        if units == 'mw' and not self.scale:
            raise ValueError("this DVR does not say its scale, so no mW units")
        first = max(0, int(start * self.rate))
        last = self.samples if seconds is None else min(
            self.samples, first + int(seconds * self.rate))
        if first >= last:
            raise ValueError("nothing of the DVR is inside that time")
        gain = np.float32(self.scale * 32768.0) if units == 'mw' else np.float32(1.0)
        shift = center_hz - self.center_hz
        same = abs(shift) < 1e-3 and abs(rate - self.rate) < 1e-3
        sink = open(out_path, 'wb') if out_path else None
        try:
            if same:                                       # the whole band: a copy
                parts = []
                for a in range(first, last, CHUNK):
                    x = self.reader.read(a, min(last, a + CHUNK)) * gain
                    if sink:
                        x.tofile(sink)
                    else:
                        parts.append(x)
                return None if sink else np.concatenate(parts)
            floor = max(rate * 1.25, min_mid or 0.0)
            decim = max(1, int(self.rate // floor))
            mid = self.rate / decim
            ratio = Fraction(rate / mid).limit_denominator(1 << 17)
            step, overlap = decim * CHUNK, decim * 65536
            w = 2 * np.pi * shift / self.rate
            pieces = []
            pos = first
            while pos < last:
                a, b = max(first, pos - overlap), min(last, pos + step + overlap)
                x = self.reader.read(a, b)
                phase = (w * np.arange(a, a + len(x), dtype=np.float64)) % (2 * np.pi)
                x *= np.exp(-1j * phase).astype(np.complex64)
                y = signal.resample_poly(x, 1, decim, window=('kaiser', 7.0)) \
                    if decim > 1 else x
                lo = (pos - a) // decim
                pieces.append(y[lo:lo + min(step, last - pos) // decim]
                              .astype(np.complex64))
                pos += step
            mid_iq = np.concatenate(pieces)
            out = mid_iq if ratio == 1 else signal.resample_poly(
                mid_iq, ratio.numerator, ratio.denominator,
                window=('kaiser', 7.0)).astype(np.complex64)
            out = (out * gain).astype(np.complex64)
            if sink:
                out.tofile(sink)
                return None
            return out
        finally:
            if sink:
                sink.close()


def write_iq(base, dvr, center_hz, rate, start=0.0, seconds=None, station_hz=None,
             units='full', force=False):
    """Cut a channel out of ``dvr`` into ``base``.cfile (complex float32),
    with ``base``.sigmf-meta (SigMF) and ``base``.json (the RF bench
    toolkit's) beside it, as this app's own recordings have. Returns
    ``(cfile path, samples)``."""
    path = base + '.cfile'
    if os.path.exists(path) and not force:
        raise FileExistsError(f"{path} exists (use --force to replace it)")
    dvr.extract(center_hz, rate, path, start=start, seconds=seconds, units=units)
    samples = os.path.getsize(path) // 8
    t0 = dvr.layout['start'] + max(0.0, start)
    when = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t0)) + \
        ('%.6f' % (t0 % 1))[1:] + 'Z'
    what = (f"channel of a Sceptre DVR, {rate / 1e6:g} MS/s at "
            f"{center_hz / 1e6:.6g} MHz, {samples / rate:.2f} s")
    if units == 'mw':
        what += "; |x|^2 is power in mW"
    else:
        what += "; 1.0 is the ADC's full scale"
        if dvr.full_scale_dbm is not None:
            what += f" ({dvr.full_scale_dbm:+.1f} dBm)"
    glob = {'core:datatype': 'cf32_le', 'core:sample_rate': float(rate),
            'core:version': '1.0.0', 'core:dataset': os.path.basename(path),
            'core:recorder': 'dvr-to-iq (fm-receiver)', 'core:hw': 'Signal Hound BB60D (Sceptre DVR)',
            'core:description': what, 'fmrx:units': units, 'fmrx:kind': 'band'}
    if dvr.full_scale_dbm is not None:
        glob['fmrx:full_scale_dbm'] = round(dvr.full_scale_dbm, 3)
    if station_hz:
        glob['fmrx:station_frequency'] = float(station_hz)
    sigmf = {'global': glob,
             'captures': [{'core:sample_start': 0, 'core:frequency': float(center_hz),
                           'core:datetime': when}], 'annotations': []}
    toolkit = {'rate': float(rate), 'offset_hz': float((station_hz or center_hz) - center_hz),
               'station_hz': float(station_hz or center_hz), 'center_hz': float(center_hz),
               'kind': 'band', 'radio': 'Sceptre DVR', 'recorded': when, 'samples': samples}
    for ext, doc in (('.sigmf-meta', sigmf), ('.json', toolkit)):
        with open(base + ext, 'w') as fh:
            json.dump(doc, fh, indent=2)
    return path, samples
