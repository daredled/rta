#!/usr/bin/env python3
"""Generate test signal WAV files to play from the iPhone.

Signals (--type):
    pink    pink_noise_{LR,L,R}.wav   pink noise, both / left only / right only (EQ)
    white   white_noise_{LR,L,R}.wav  white noise, same layout
    sub     sub_noise_LR.wav          pink noise limited to 40-200 Hz (sub phase/delay tests)
    rattle  rattle_sweep.wav          slow 20-120 Hz sine sweep (listen for buzzes and rattles)
    delay   delay_sweeps.wav          alternating left/right sweeps for delay.py
    all     all of the above

Noise files are generated in the frequency domain, so they are periodic: when
played on loop there is no click at the seam. The sweep files also loop cleanly.
"""
import argparse
import logging
import wave
from pathlib import Path

import numpy as np

import logsetup

log = logging.getLogger(__name__)

# Layout of delay_sweeps.wav, shared with delay.py. The left->right and
# right->left spacings differ so the analyzer can tell which sweep is which.
DELAY_F1, DELAY_F2 = 100.0, 20000.0  # sweep range (Hz)
DELAY_SWEEP_S = 0.75                 # sweep length (s)
DELAY_T1, DELAY_T2 = 1.25, 1.75      # left->right and right->left spacing (s)


def make_noise(seconds, fs, kind="pink", fmin=15.0, fmax=22000.0, rms_dbfs=-18.0, seed=1234):
    n = int(seconds * fs)
    rng = np.random.default_rng(seed)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    spec = rng.normal(size=f.size) + 1j * rng.normal(size=f.size)

    mag = np.zeros_like(f)
    band = (f >= fmin) & (f <= min(fmax, fs / 2))
    # Pink: power ~ 1/f  ->  amplitude ~ 1/sqrt(f).  White: constant amplitude.
    mag[band] = 1.0 / np.sqrt(f[band]) if kind == "pink" else 1.0

    x = np.fft.irfft(spec * mag, n)
    x *= 10 ** (rms_dbfs / 20) / np.sqrt(np.mean(x**2))
    peak = np.max(np.abs(x))
    if peak > 0.999:
        x *= 0.999 / peak
        log.warning("level reduced to avoid clipping (peak %+.1f dBFS)", 20 * np.log10(peak))
    log.debug("%s noise %g-%g Hz: RMS %.1f dBFS, peak %.1f dBFS", kind, fmin, fmax,
              20 * np.log10(np.sqrt(np.mean(x**2))), 20 * np.log10(np.max(np.abs(x))))
    return x


def log_sweep(f1, f2, seconds, fs, fade_s=0.005):
    """Exponential sine sweep from f1 to f2 with short fades at both ends."""
    t = np.arange(int(seconds * fs)) / fs
    rate = seconds / np.log(f2 / f1)
    x = np.sin(2 * np.pi * f1 * rate * (np.exp(t / rate) - 1))
    nf = int(fade_s * fs)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, nf))
    x[:nf] *= ramp
    x[-nf:] *= ramp[::-1]
    return x


def inverse_sweep(f1, f2, seconds, fs):
    """Inverse filter for log_sweep: convolving the sweep with it gives an impulse."""
    x = log_sweep(f1, f2, seconds, fs)
    t = np.arange(len(x)) / fs
    rate = seconds / np.log(f2 / f1)
    # Time-reversed sweep, attenuated 6 dB/octave toward the low end.
    return x[::-1] * np.exp(-t / rate)


def delay_sequence(fs, pairs=10, peak_dbfs=-6.0):
    """Left sweep, then right sweep DELAY_T1 later, repeating every DELAY_T1 + DELAY_T2."""
    sweep = log_sweep(DELAY_F1, DELAY_F2, DELAY_SWEEP_S, fs) * 10 ** (peak_dbfs / 20)
    n1 = int(round(DELAY_T1 * fs))
    period = int(round((DELAY_T1 + DELAY_T2) * fs))
    left, right = np.zeros(period * pairs), np.zeros(period * pairs)
    for p in range(pairs):
        left[p * period:p * period + len(sweep)] = sweep
        right[p * period + n1:p * period + n1 + len(sweep)] = sweep
    return left, right


def write_wav(path, left, right, fs):
    data = np.stack([left, right], axis=1)
    pcm = np.round(np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--type", choices=["pink", "white", "sub", "rattle", "delay", "all"], default="pink",
                   help="signal to generate (default: pink)")
    p.add_argument("--seconds", type=float, default=30.0, help="noise duration in seconds (default: 30)")
    p.add_argument("--fs", type=int, default=48000, help="sample rate (default: 48000)")
    p.add_argument("--level", type=float, default=-18.0, help="noise RMS level in dBFS (default: -18)")
    p.add_argument("--fmin", type=float, default=15.0, help="pink/white lower limit in Hz (default: 15)")
    p.add_argument("--fmax", type=float, default=22000.0, help="pink/white upper limit in Hz (default: 22000)")
    p.add_argument("--out", type=Path, default=Path("signals"), help="output folder (default: signals)")
    logsetup.add_arguments(p)
    args = p.parse_args()
    logsetup.setup(args)

    args.out.mkdir(parents=True, exist_ok=True)
    kinds = ["pink", "white", "sub", "rattle", "delay"] if args.type == "all" else [args.type]

    def save(name, left, right):
        path = args.out / name
        write_wav(path, left, right, args.fs)
        log.info("Created %s", path)

    for kind in kinds:
        if kind in ("pink", "white"):
            x = make_noise(args.seconds, args.fs, kind, args.fmin, args.fmax, args.level)
            silence = np.zeros_like(x)
            for suffix, left, right in [("LR", x, x), ("L", x, silence), ("R", silence, x)]:
                save(f"{kind}_noise_{suffix}.wav", left, right)
        elif kind == "sub":
            x = make_noise(args.seconds, args.fs, "pink", 40.0, 200.0, args.level)
            save("sub_noise_LR.wav", x, x)
        elif kind == "rattle":
            x = log_sweep(20.0, 120.0, 60.0, args.fs, fade_s=0.05) * 10 ** (-6 / 20)
            save("rattle_sweep.wav", x, x)
        elif kind == "delay":
            save("delay_sweeps.wav", *delay_sequence(args.fs))


if __name__ == "__main__":
    main()
