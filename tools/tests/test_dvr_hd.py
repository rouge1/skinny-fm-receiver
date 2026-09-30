"""tools/dvr-hd: HD Radio from a Sceptre DVR, offline - no radio, no nrsc5.

A synthetic DVR (``signals.write_sdvr``) holds one FM station 300 kHz off
its centre. A stand-in takes nrsc5's place, reading the IQ file it is given
as nrsc5 would and writing a log and a tone. It must:

- channelize the station to 0 Hz at 744,187.5 S/s, the length the DVR's
  seconds give, with its pilot at 19 kHz and 6-7 kHz deviation, and nothing
  of it a channel away;
- scan the raster for stereo stations and find that one alone;
- list what a DVR holds (--info) and its stereo stations (--scan) without
  nrsc5;
- run the tool end to end: the stand-in fed the right file for each program,
  the WAVs of the programs that decoded kept and the empty ones removed, the
  services and rates in summary.json;
- say what is wrong, and exit, for a file that is not a DVR, a station outside
  the band, and no nrsc5.

Run:  python tools/tests/test_dvr_hd.py [--keep]     (about 25 s)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import dvr_hd  # noqa: E402
from tests import signals  # noqa: E402

TOOL = os.path.join(os.path.dirname(HERE), 'dvr-hd')
RATE = 2.5e6
CENTRE = 98.4e6
STATION = 98.7e6
SECONDS = 3.0

STAND_IN = '''#!{python}
import struct, sys, wave
import numpy as np
a = sys.argv
path = a[a.index('-r') + 1]
wav = a[a.index('-o') + 1]
program = int(a[-1])
assert a[a.index('--iq-input-format') + 1] == 'cf32'
n = len(np.fromfile(path, dtype=np.complex64))
print('samples', n, file=sys.stderr)
if program < 2:
    print('12:00:00 Synchronized', file=sys.stderr)
    print('12:00:01 Station name: TEST', file=sys.stderr)
    print('12:00:01 SIG Service: type=audio number=1 name=TEST HD1', file=sys.stderr)
    print('12:00:01 SIG Service: type=audio number=2 name=Second one', file=sys.stderr)
    print('12:00:02 Audio bit rate: %d.5 kbps' % (30 + program), file=sys.stderr)
    print('12:00:02 MER: 3.5 dB (lower), 2.5 dB (upper)', file=sys.stderr)
    print('12:00:02 BER: 0.09, avg: 0.085, min: 0.08, max: 0.1', file=sys.stderr)
    t = np.arange(44100) / 44100.0
    tone = (0.3 * np.sin(2 * np.pi * (3000 + 500 * program) * t) * 32767).astype('<i2')
    with wave.open(wav, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(np.column_stack([tone, tone]).tobytes())
else:
    open(wav, 'wb').write(b'\\0' * 68)         # what a failed decode leaves
'''


def demod(x, fs):
    return np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)


def tone_dev(d, fs, hz):
    f, p = signal.welch(d, fs, nperseg=1 << 14)
    sel = (f > hz - 60) & (f < hz + 60)
    noise = np.median(p[(f > hz - 1500) & (f < hz + 1500) & ~sel])
    return np.sqrt(2 * (p[sel] - noise).clip(0).sum() * f[1])


def make_dvr(folder):
    left, right = signals.tones(SECONDS)
    mpx = signals.multiplex(SECONDS, left, right)
    iq = signals.fm_iq(mpx, RATE, STATION - CENTRE) * 0.9
    path = os.path.join(folder, 'station.sdvr')
    signals.write_sdvr(path, iq, RATE, CENTRE, run=1 << 17)
    return path


def library_checks(path):
    dvr = dvr_hd.Dvr(path)
    lo, hi = dvr.usable()
    assert abs(lo - (CENTRE - RATE / 2 + 400e3)) < 1 and abs(hi - (CENTRE + RATE / 2 - 400e3)) < 1
    out = dvr.channelize(STATION)
    want = dvr.samples / dvr.rate * dvr_hd.NRSC5_RATE
    assert abs(len(out) - want) < 4, (len(out), want)
    d = demod(out, dvr_hd.NRSC5_RATE)
    d = d[int(0.3 * dvr_hd.NRSC5_RATE):]                  # past the filter's start
    assert abs(np.mean(d)) < 1500, np.mean(d)              # at 0 Hz, not off to a side
    pilot = tone_dev(d, dvr_hd.NRSC5_RATE, 19000.0)
    print(f"channelized: {len(out) / dvr_hd.NRSC5_RATE:.2f} s, mean {np.mean(d):.0f} Hz, "
          f"pilot deviation {pilot / 1e3:.2f} kHz")
    assert 6.0e3 < pilot < 7.5e3, pilot
    wrong = dvr.channelize(STATION - 600e3)
    dw = demod(wrong, dvr_hd.NRSC5_RATE)[int(0.3 * dvr_hd.NRSC5_RATE):]
    assert tone_dev(dw, dvr_hd.NRSC5_RATE, 19000.0) < 1.0e3, 'a pilot where there is no station'
    try:
        dvr.channelize(CENTRE + RATE / 2)
    except ValueError as exc:
        assert 'outside' in str(exc), exc
    else:
        raise AssertionError('a station at the band edge should be refused')

    chans = dvr_hd.raster(lo, hi)
    assert STATION in chans and all(abs(c / 1e5 - round(c / 1e5)) < 1e-6 for c in chans), chans
    rows = dvr.pilot_scan(chans)
    best = max(rows, key=lambda r: r[1])
    others = [r[1] for r in rows if r[0] != STATION]
    print(f"pilot scan: {len(chans)} channels; best {best[0] / 1e6:.1f} MHz "
          f"{best[1]:.0f} dB, next {max(others):.0f} dB")
    assert best[0] == STATION and best[1] > 25 and max(others) < 12, rows

    info = dvr_hd.summarise(
        "12:00:00 Synchronized\n12:00:01 Station name: ABC\n"
        "12:00:01 SIG Service: type=audio number=1 name=One\n"
        "12:00:01 SIG Service: type=data number=44 name=TPEG\n"
        "12:00:02 Audio bit rate: 31.2 kbps\n"
        "12:00:02 MER: -1.5 dB (lower), 2.5 dB (upper)\n"
        "12:00:02 BER: 0.09, avg: 0.085, min: 0.08, max: 0.1\n")
    assert info == {'synced': True, 'station': 'ABC', 'slogan': None,
                    'services': {1: 'One'}, 'kbps': 31.2,
                    'mer_db': (-1.5, 2.5), 'ber': 0.085}, info
    assert dvr_hd.summarise('')['synced'] is False


def tool_checks(folder, path):
    fake = os.path.join(folder, 'fake-nrsc5')
    with open(fake, 'w') as fh:
        fh.write(STAND_IN.format(python=sys.executable))
    os.chmod(fake, 0o755)

    def run(*args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True,
                              text=True, timeout=300, cwd=folder)

    out = os.path.join(folder, 'out')
    r = run(path, '98.7', '-o', out, '--nrsc5', fake, '--programs', '3')
    assert r.returncode == 0, r.stderr + r.stdout
    print(r.stdout.strip().splitlines()[-2])
    s = json.load(open(os.path.join(out, 'summary.json')))
    st = s['stations']['98.7']
    assert sorted(k for k in st if not k.startswith('_')) == ['HD1', 'HD2'], st
    assert st['HD1']['name'] == 'TEST HD1' and st['HD2']['name'] == 'Second one', st
    assert st['_nrsc5']['ber'] == 0.085 and st['_nrsc5']['mer_db'] == [3.5, 2.5], st
    assert sorted(f for f in os.listdir(out) if f.endswith('.wav')) == \
        ['hd_98.7_HD1.wav', 'hd_98.7_HD2.wav'], os.listdir(out)   # HD3 removed
    assert not any(f.endswith('.cf32') for f in os.listdir(out))   # IQ not kept

    # Scanning: no station named, the one stereo station found alone; the IQ kept.
    out2 = os.path.join(folder, 'out2')
    r = run(path, '-o', out2, '--nrsc5', fake, '--programs', '1', '--keep-iq')
    assert r.returncode == 0, r.stderr + r.stdout
    s = json.load(open(os.path.join(out2, 'summary.json')))
    assert list(s['stations']) == ['98.7'], s['stations']
    iq = os.path.join(out2, 'iq_98.7.cf32')
    n = os.path.getsize(iq) // 8
    held = dvr_hd.Dvr(path).seconds                        # whole runs only
    assert abs(n - held * dvr_hd.NRSC5_RATE) < 8, (n, held)
    print('tool: named station, then a scan finding it alone; IQ kept when asked')

    # Info and scan only: no nrsc5 needed, nothing written.
    r = run(path, '--info', '--nrsc5', os.path.join(folder, 'no-such-program'))
    assert r.returncode == 0 and 'runs of 131072 samples' in r.stdout, r.stdout + r.stderr
    assert 'band 97.15-99.65 MHz' in r.stdout, r.stdout
    r = run(path, '--scan', '--nrsc5', os.path.join(folder, 'no-such-program'))
    lines = [l for l in r.stdout.splitlines() if l.startswith('  ')]
    assert r.returncode == 0 and len(lines) == 1 and '98.7 MHz' in lines[0], r.stdout
    assert not os.path.exists(os.path.join(folder, 'dvr-hd-station')), 'scan wrote a folder'
    print('info and scan: what the DVR holds; the one station, no nrsc5, nothing written')

    # Refusals.
    junk = os.path.join(folder, 'junk.sdvr')
    open(junk, 'wb').write(bytes(40960))
    r = run(junk, '98.7', '--nrsc5', fake)
    assert r.returncode != 0 and 'not a Sceptre DVR' in r.stderr, (r.returncode, r.stderr)
    r = run(path, '104.1', '-o', os.path.join(folder, 'out3'), '--nrsc5', fake)
    assert 'outside' in r.stdout and 'no HD' not in r.stdout, r.stdout
    r = run(path, '98.7', '--nrsc5', os.path.join(folder, 'no-such-program'))
    assert r.returncode != 0 and 'nrsc5 is not installed' in r.stderr, r.stderr
    print('refused: not a DVR, a station outside the band, no nrsc5')


def main():
    folder = tempfile.mkdtemp(prefix='fmrx-dvrhd-')
    try:
        path = make_dvr(folder)
        library_checks(path)
        tool_checks(folder, path)
    finally:
        if '--keep' in sys.argv:
            print('kept', folder)
        else:
            shutil.rmtree(folder, ignore_errors=True)
    print('OK')


if __name__ == '__main__':
    main()
