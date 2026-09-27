#!/usr/bin/env python3
"""Fox-hunt a short 2-FSK beacon with a HackRF: one line per burst, with its level.

Made for a Raspberry Pi (4 or 5) with a HackRF on USB: numpy and the
``hackrf_transfer`` command (apt install hackrf python3-numpy) are all it
needs. It listens on one carrier, finds each burst, checks it looks like the
beacon (constant envelope, a 50 kbaud 0101 preamble) and prints its level.
Walk with it: the level rises as you get closer. Turn the gains down when it
says CLIP; a clipped level stops rising.

    beacon_hunt.py                          # 910 MHz, HackRF
    beacon_hunt.py --freq 906 --lna 16 --vga 10 --beep
    beacon_hunt.py --file cut.cfile --rate 1e6 --offset 100e3   # a cf32 recording

The receiver is tuned 200 kHz below the carrier, which keeps the HackRF's
DC spike out of the channel. It only listens (hackrf_transfer -r): nothing
is transmitted.
"""
import argparse
import shutil
import subprocess
import sys
import time

import numpy as np

CHANNEL_RATE = 250e3      # after decimation: 5 samples a bit at 50 kbaud


def lowpass(cutoff, rate, taps=63):
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / rate * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


class Hunter:
    def __init__(self, rate, offset, baud, threshold_db, min_ms, max_ms, beep):
        self.rate = rate
        self.dec = int(round(rate / CHANNEL_RATE))
        if abs(rate / self.dec - CHANNEL_RATE) > 1:
            sys.exit(f"rate {rate:g} must be a multiple of {CHANNEL_RATE:g}")
        self.fo = rate / self.dec
        self.offset = offset
        self.baud = baud
        self.threshold = 10 ** (threshold_db / 10)
        self.min_n = int(min_ms * 1e-3 * self.fo)
        self.max_n = int(max_ms * 1e-3 * self.fo)
        self.beep = beep
        self.h = lowpass(80e3, rate)
        self.phase = 0.0
        self.tail = np.zeros(0, np.complex64)      # channel samples carried over
        self.t0 = None                             # time of self.tail[0]
        self.floor = None
        self.best = None
        self.last = None
        self.count = 0

    def feed(self, x, t_start):
        """x: complex samples at self.rate; t_start: time of x[0] in seconds."""
        n = np.arange(len(x))
        lo = np.exp(-2j * np.pi * (self.offset * n / self.rate + self.phase))
        self.phase = (self.phase + self.offset * len(x) / self.rate) % 1.0
        y = np.convolve(x * lo.astype(np.complex64), self.h, 'same')[::self.dec]
        if self.t0 is None:
            self.t0 = t_start
        y = np.concatenate([self.tail, y.astype(np.complex64)])
        p = np.abs(y) ** 2
        # the floor: a low percentile, slowly tracked
        f = np.percentile(p, 20) + 1e-20
        self.floor = f if self.floor is None else 0.9 * self.floor + 0.1 * f
        on = p > self.floor * self.threshold
        # smooth over a few samples so an FSK transition doesn't split a burst
        on = np.convolve(on, np.ones(5), 'same') > 0
        edges = np.flatnonzero(np.diff(on.astype(np.int8)))
        starts = list(edges[~on[edges]] + 1) if len(edges) else []
        if on[0]:
            starts.insert(0, 0)
        keep_from = len(y)
        for a in starts:
            rest = np.flatnonzero(~on[a:])
            if len(rest) == 0:                     # runs past the end: wait
                keep_from = min(keep_from, a)
                break
            b = a + rest[0]
            if self.min_n <= b - a <= self.max_n:
                self.report(y[a:b], self.t0 + a / self.fo)
        # carry the unfinished part (or the last 3 ms) into the next block
        keep_from = min(keep_from, max(0, len(y) - int(3e-3 * self.fo)))
        self.tail = y[keep_from:]
        self.t0 += keep_from / self.fo

    def looks_like_beacon(self, z):
        env = np.abs(z[3:-3])
        if env.std() / env.mean() > 0.25:          # amplitude keyed: not ours
            return False
        fi = np.angle(z[1:] * np.conj(z[:-1]))
        pre = fi[3:int(0.5e-3 * self.fo)]
        v = pre - np.median(pre)
        crossings = np.count_nonzero(np.diff(np.sign(v)) != 0)
        expected = self.baud * len(pre) / self.fo  # one crossing a bit in 0101
        return 0.7 * expected < crossings < 1.3 * expected

    def report(self, z, t):
        if not self.looks_like_beacon(z):
            return
        level = 10 * np.log10(np.mean(np.abs(z) ** 2) + 1e-20)
        snr = level - 10 * np.log10(self.floor)
        clip = np.max(np.abs(z)) > 0.9
        self.count += 1
        self.best = level if self.best is None else max(self.best, level)
        gap = '' if self.last is None else f'  +{t - self.last:6.2f} s'
        self.last = t
        bar = '#' * max(0, int(snr / 2))
        print(f"{time.strftime('%H:%M:%S')}  burst {self.count:4d}  {level:6.1f} dBFS"
              f"  SNR {snr:5.1f} dB  best {self.best:6.1f}{gap}"
              f"{'  CLIP' if clip else ''}  {bar}", flush=True)
        if self.beep:
            sys.stdout.write('\a')
            sys.stdout.flush()


def from_hackrf(args, hunter):
    if shutil.which('hackrf_transfer') is None:
        sys.exit('hackrf_transfer not found: apt install hackrf')
    tuned = args.freq * 1e6 - args.offset
    cmd = ['hackrf_transfer', '-r', '-', '-f', str(int(tuned)), '-s', str(int(args.rate)),
           '-l', str(args.lna), '-g', str(args.vga), '-a', '1' if args.amp else '0']
    print(f"listening at {args.freq:.3f} MHz (tuned {tuned / 1e6:.3f}), "
          f"{args.rate / 1e6:g} MS/s, LNA {args.lna} dB, VGA {args.vga} dB"
          f"{', amp on' if args.amp else ''}. Ctrl-C stops.", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    block = int(args.rate * 0.1) * 2               # 0.1 s of int8 I/Q
    t = 0.0
    try:
        while True:
            raw = proc.stdout.read(block)
            if not raw:
                sys.exit('hackrf_transfer stopped (is the HackRF in use or unplugged?)')
            s = np.frombuffer(raw[:len(raw) // 2 * 2], np.int8).astype(np.float32) / 128
            x = (s[0::2] + 1j * s[1::2]).astype(np.complex64)
            hunter.feed(x, t)
            t += len(x) / args.rate
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()                           # let go of the HackRF
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def from_file(args, hunter):
    x = np.memmap(args.file, dtype=np.complex64, mode='r')
    step = int(args.rate * 0.1)
    for i in range(0, len(x), step):
        hunter.feed(np.asarray(x[i:i + step]), i / args.rate)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--freq', type=float, default=910.0, help='carrier, MHz (default 910)')
    ap.add_argument('--rate', type=float, default=2e6, help='sample rate (default 2e6)')
    ap.add_argument('--offset', type=float, default=200e3,
                    help='carrier above the tuned frequency, Hz (default 200e3)')
    ap.add_argument('--baud', type=float, default=50e3)
    ap.add_argument('--lna', type=int, default=24, help='HackRF LNA gain, 0-40 in 8s')
    ap.add_argument('--vga', type=int, default=20, help='HackRF VGA gain, 0-62 in 2s')
    ap.add_argument('--amp', action='store_true', help='the HackRF front-end amp (+11 dB)')
    ap.add_argument('--threshold', type=float, default=10, help='dB over the floor')
    ap.add_argument('--min-ms', type=float, default=1.5)
    ap.add_argument('--max-ms', type=float, default=2.4)
    ap.add_argument('--beep', action='store_true', help='ring the terminal bell per burst')
    ap.add_argument('--file', help='a cf32 recording instead of the HackRF')
    args = ap.parse_args()
    hunter = Hunter(args.rate, args.offset, args.baud, args.threshold,
                    args.min_ms, args.max_ms, args.beep)
    (from_file if args.file else from_hackrf)(args, hunter)


if __name__ == '__main__':
    main()
