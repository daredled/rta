#!/usr/bin/env python3
"""Generate pink (or white) noise WAV files to play from the iPhone.

The noise is generated in the frequency domain, so the file is periodic:
when played on loop there is no click at the seam.

By default it creates three files:
    pink_noise_LR.wav  -> same noise on both channels (overall measurement)
    pink_noise_L.wav   -> left channel only (measure the left side)
    pink_noise_R.wav   -> right channel only (measure the right side)
"""
import argparse
import wave
from pathlib import Path

import numpy as np


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
        print(f"  warning: level reduced to avoid clipping (peak {20*np.log10(peak):+.1f} dBFS)")
    return x


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
    p.add_argument("--type", choices=["pink", "white"], default="pink", help="noise type (default: pink)")
    p.add_argument("--seconds", type=float, default=30.0, help="duration in seconds (default: 30)")
    p.add_argument("--fs", type=int, default=48000, help="sample rate (default: 48000)")
    p.add_argument("--level", type=float, default=-18.0, help="RMS level in dBFS (default: -18)")
    p.add_argument("--fmin", type=float, default=15.0, help="lower limit in Hz (default: 15)")
    p.add_argument("--fmax", type=float, default=22000.0, help="upper limit in Hz (default: 22000)")
    p.add_argument("--out", type=Path, default=Path("signals"), help="output folder (default: signals)")
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    x = make_noise(args.seconds, args.fs, args.type, args.fmin, args.fmax, args.level)
    silence = np.zeros_like(x)
    name = f"{args.type}_noise"

    for suffix, left, right in [("LR", x, x), ("L", x, silence), ("R", silence, x)]:
        path = args.out / f"{name}_{suffix}.wav"
        write_wav(path, left, right, args.fs)
        print(f"Created {path}")


if __name__ == "__main__":
    main()
