"""Negative examples for the LTE verifier: signals that are NOT LTE.

numpy only. Every synth takes ``(seed=0, fs=20e6, dur=0.1)`` and returns
``(iq complex64, fs)``: ``dur`` seconds at ``fs``, normalised to unit mean
power (the uplink one to unit power during its bursts, see synth_uplink_like).
They are hard on purpose: each has OFDM-like or pilot-like structure that a
structure detector could mistake for LTE's.

    synth_noise(seed=0, fs=20e6, dur=0.1)        white complex Gaussian noise
    synth_tone(seed=0, fs=20e6, dur=0.1)         a tone at a seeded offset, 30 dB above noise
    synth_wifi_like(seed=0, fs=20e6, dur=0.1)    64-point OFDM, 312.5 kHz spacing, CP 16
    synth_atsc_like(seed=0, fs=20e6, dur=0.1)    8VSB-like single sideband, 10.76 MBd
    synth_uplink_like(seed=0, fs=20e6, dur=0.1)  SC-FDMA-like bursts, 10 MHz, CP 72

spectral_width(iq, fs) gives the 99% occupied bandwidth in Hz.
"""
import numpy as np


def _rng(seed):
    return np.random.default_rng(seed)


def _unit_power(x):
    return x / np.sqrt(np.mean(np.abs(x) ** 2))


def synth_noise(seed=0, fs=20e6, dur=0.1):
    """White complex Gaussian noise, unit power."""
    n = int(round(dur * fs))
    rng = _rng(seed)
    x = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    return x.astype(np.complex64), fs


def synth_tone(seed=0, fs=20e6, dur=0.1):
    """A complex tone at a seeded offset in +-4 MHz, 30 dB above white noise: a
    constant envelope with one spectral line, which a narrowband search can
    mistake for structure."""
    n = int(round(dur * fs))
    rng = _rng(seed)
    f = rng.uniform(-4e6, 4e6)
    t = np.arange(n) / fs
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * np.sqrt(0.001 / 2)
    x = np.exp(2j * np.pi * f * t) + noise
    return _unit_power(x).astype(np.complex64), fs


def synth_wifi_like(seed=0, fs=20e6, dur=0.1):
    """64-point OFDM, 312.5 kHz spacing, CP 16 (80 samples per symbol),
    52 used subcarriers (+-1..+-26, DC empty), random QPSK, no pilots.
    The 52 subcarriers span about 16.25 MHz of a 20 MHz channel."""
    N, cp, sym_len = 64, 16, 80
    n = int(round(dur * fs))
    nsym = n // sym_len + 2
    used = np.r_[1:27, N - 26:N]
    rng = _rng(seed)
    bits = rng.integers(0, 4, (nsym, used.size))
    qpsk = np.exp(1j * (np.pi / 4 + np.pi / 2 * bits))
    X = np.zeros((nsym, N), dtype=complex)
    X[:, used] = qpsk
    x = np.fft.ifft(X, axis=1)
    td = np.concatenate([x[:, -cp:], x], axis=1).ravel()[:n]
    return _unit_power(td).astype(np.complex64), fs


def _rrc(t, beta, Ts):
    """Root-raised-cosine impulse response h(t), scaled by 1/Ts."""
    x = t / Ts
    out = np.empty_like(x)
    at0 = np.abs(x) < 1e-9
    at_sing = np.abs(np.abs(4 * beta * x) - 1) < 1e-9
    reg = ~(at0 | at_sing)
    xr = x[reg]
    num = np.sin(np.pi * xr * (1 - beta)) + 4 * beta * xr * np.cos(np.pi * xr * (1 + beta))
    den = np.pi * xr * (1 - (4 * beta * xr) ** 2)
    out[reg] = num / den
    out[at0] = 1 - beta + 4 * beta / np.pi
    b = beta
    out[at_sing] = (b / np.sqrt(2)) * (
        (1 + 2 / np.pi) * np.sin(np.pi / (4 * b)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * b)))
    return out / Ts


def _analytic(x):
    """Analytic (one-sided) version of a real signal, via FFT."""
    N = x.size
    h = np.zeros(N)
    h[0] = 1
    h[N // 2] = 1
    h[1:N // 2] = 2
    return np.fft.ifft(np.fft.fft(x) * h)


def synth_atsc_like(seed=0, fs=20e6, dur=0.1):
    """8-level symbols at 10.76 MBd, RRC roll-off 0.115, real 8VSB-like
    waveform made single-sideband (analytic), so it occupies about 6 MHz
    from DC up. Small pilot 0.31 MHz above DC at 11.3 dB below the signal
    (as in 8VSB). Symbol timing is evaluated per sample, so the
    non-integer samples-per-symbol (about 1.86 at 20 MS/s) is exact."""
    Rs, beta = 10.76e6, 0.115
    Ts = 1.0 / Rs
    n = int(round(dur * fs))
    rng = _rng(seed)
    levels = np.arange(-7, 8, 2) / np.sqrt(21.0)  # unit mean power
    K = int(dur * Rs) + 20
    a = rng.choice(levels, K)
    t = np.arange(n) / fs
    ts = t + 8 * Ts  # so the first symbols have full pulses
    kc = np.floor(ts / Ts + 0.5).astype(int)
    s = np.zeros(n)
    for j in range(-8, 9):
        k = kc + j
        ok = (k >= 0) & (k < K)
        kk = np.clip(k, 0, K - 1)
        s += np.where(ok, a[kk] * _rrc(ts - k * Ts, beta, Ts), 0.0)
    s = s / np.sqrt(np.mean(s ** 2))
    A = np.sqrt(2 * 10 ** (-1.13))  # pilot-to-signal power -11.3 dB
    s = s + A * np.cos(2 * np.pi * 0.31e6 * t)
    z = _analytic(s)
    return _unit_power(z).astype(np.complex64), fs


def synth_uplink_like(seed=0, fs=20e6, dur=0.1):
    """Bursty SC-FDMA-like uplink, 10 MHz occupied: 1024-point IFFT at
    20 MS/s (19.53 kHz spacing), 512 contiguous used subcarriers (DC empty),
    DFT-spread QPSK, 72-sample CP. Transmits 2 ms, then 5 ms gap, repeating.
    Power is normalised to 1 during the bursts, so the mean over the whole
    block is about 2/7 of that (about -5.4 dB)."""
    N, cp, M = 1024, 72, 512
    used = np.r_[N - 256:N, 1:257]
    n = int(round(dur * fs))
    sym_len = N + cp
    nsym = n // sym_len + 2
    rng = _rng(seed)
    bits = rng.integers(0, 4, (nsym, M))
    d = np.exp(1j * (np.pi / 4 + np.pi / 2 * bits))
    D = np.fft.fft(d, axis=1) / np.sqrt(M)  # DFT spreading
    X = np.zeros((nsym, N), dtype=complex)
    X[:, used] = D
    x = np.fft.ifft(X, axis=1)
    td = np.concatenate([x[:, -cp:], x], axis=1).ravel()[:n]
    period = int(round(7e-3 * fs))
    burst = int(round(2e-3 * fs))
    active = (np.arange(n) % period) < burst
    td = td * active
    td = td / np.sqrt(np.mean(np.abs(td[active]) ** 2))
    return td.astype(np.complex64), fs


def spectral_width(iq, fs, frac=0.99, nfft=4096):
    """Occupied bandwidth (Hz) holding `frac` of the power, from a Hann-
    windowed averaged periodogram: the span between the (1-frac)/2 and
    (1+frac)/2 quantiles of the cumulative spectrum."""
    n_seg = len(iq) // nfft
    seg = iq[:n_seg * nfft].reshape(n_seg, nfft) * np.hanning(nfft)
    P = np.fft.fftshift((np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0))
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / fs))
    cum = np.cumsum(P) / P.sum()
    lo = np.interp((1 - frac) / 2, cum, f)
    hi = np.interp((1 + frac) / 2, cum, f)
    return hi - lo


if __name__ == "__main__":
    cases = [
        ("noise", lambda: synth_noise()),
        ("tone", lambda: synth_tone()),
        ("wifi_like", lambda: synth_wifi_like()),
        ("atsc_like", lambda: synth_atsc_like()),
        ("uplink_like", lambda: synth_uplink_like()),
    ]
    print(f"{'name':12s} {'fs MS/s':>8s} {'samples':>8s} {'power dB':>9s} {'width99 MHz':>12s}")
    for name, fn in cases:
        iq, fs = fn()
        p_db = 10 * np.log10(np.mean(np.abs(iq.astype(complex)) ** 2))
        w = spectral_width(iq, fs) / 1e6
        print(f"{name:12s} {fs / 1e6:8.2f} {len(iq):8d} {p_db:9.2f} {w:12.2f}")
