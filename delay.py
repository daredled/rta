#!/usr/bin/env python3
"""Measure the left/right arrival time difference for time alignment.

Play signals/delay_sweeps.wav on loop from the phone, put the microphone at
the listening position (driver's head) and run this script. It records the
alternating sweeps, turns each one into an impulse response and reports how
much later one side arrives than the other, as the distance difference to
enter in the head unit's time alignment.

Only relative timing is measured: the phone and laptop clocks are not linked,
so the absolute latency is unknown, but it is the same for both sides. Clock
drift between the two devices is estimated from the sweep spacing and removed.
"""
import argparse
import logging
import sys
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

import gen_noise as gn
import logsetup

log = logging.getLogger(__name__)

SPEED_OF_SOUND = 343.0  # m/s at about 20 °C
PLOT_PRE_MS, PLOT_POST_MS = 5.0, 25.0


def impulse_envelope(rec, fs):
    """Deconvolve the recording with the inverse sweep and return the envelope.

    Index i of the result corresponds to a sweep that started at sample i of
    the recording (plus the acoustic delay).
    """
    inv = gn.inverse_sweep(gn.DELAY_F1, gn.DELAY_F2, gn.DELAY_SWEEP_S, fs)
    n = len(rec) + len(inv) - 1
    nfft = 1 << (n - 1).bit_length()
    spec = np.fft.fft(rec, nfft) * np.fft.fft(inv, nfft)
    # Analytic signal (negative frequencies removed): its magnitude is the envelope.
    spec[nfft // 2 + 1:] = 0
    spec[1:nfft // 2] *= 2
    env = np.abs(np.fft.ifft(spec))
    return env[len(inv) - 1:len(inv) - 1 + len(rec)]


def find_peaks(env, fs, rel_threshold=0.1, min_gap_s=0.3):
    """Time (s, sub-sample) and height of each impulse in the envelope."""
    idx = np.flatnonzero(env > rel_threshold * env.max())
    if idx.size == 0:
        return np.array([]), np.array([])
    groups = np.split(idx, np.flatnonzero(np.diff(idx) > min_gap_s * fs) + 1)
    times, heights = [], []
    for grp in groups:
        i = grp[np.argmax(env[grp])]
        offset = 0.0
        if 0 < i < len(env) - 1:
            a, b, c = env[i - 1], env[i], env[i + 1]
            if a - 2 * b + c != 0:
                offset = 0.5 * (a - c) / (a - 2 * b + c)
        times.append((i + offset) / fs)
        heights.append(env[i])
    return np.array(times), np.array(heights)


def measure(rec, fs):
    """Find left->right->left sweep triples and the right-minus-left arrival delay."""
    env = impulse_envelope(rec, fs)
    times, heights = find_peaks(env, fs)
    log.debug("impulses found at (s): %s", " ".join(f"{t:.4f}" for t in times))
    gaps = np.diff(times)
    tol = 0.2
    is_lr = np.abs(gaps - gn.DELAY_T1) < tol
    is_rl = np.abs(gaps - gn.DELAY_T2) < tol

    deltas, level_diffs, lefts, drifts = [], [], [], []
    for i in range(len(gaps) - 1):
        if is_lr[i] and is_rl[i + 1]:
            # The full left->left period is known, which gives the clock ratio
            # between phone and laptop; the remainder of the left->right gap is the delay.
            ratio = (gaps[i] + gaps[i + 1]) / (gn.DELAY_T1 + gn.DELAY_T2)
            deltas.append(gaps[i] - ratio * gn.DELAY_T1)
            level_diffs.append(20 * np.log10(heights[i + 1] / heights[i]))
            lefts.append((times[i], ratio))
            drifts.append(ratio)
    log.debug("per-cycle right-left delay (ms): %s", " ".join(f"{d * 1000:+.3f}" for d in deltas))
    if not deltas:
        return None
    return {
        "delta_s": float(np.median(deltas)),
        "spread_s": float(np.std(deltas)),
        "cycles": len(deltas),
        "level_db": float(np.median(level_diffs)),
        "drift_ppm": float((np.median(drifts) - 1) * 1e6),
        "env": env,
        "lefts": lefts,
    }


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        fs, width, nch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV files are supported")
    return np.frombuffer(raw, dtype="<i2").reshape(-1, nch)[:, 0].astype(np.float64) / 32768, fs


def record(args):
    import sounddevice as sd

    log.info("Recording %g s... (delay_sweeps.wav must be playing on loop)", args.seconds)
    rec = sd.rec(int(args.seconds * args.fs), samplerate=args.fs, channels=args.channel,
                 device=args.device, dtype="float32")
    sd.wait()
    return rec[:, args.channel - 1].astype(np.float64)


def report(res, left_cm):
    delta_ms = res["delta_s"] * 1000
    dist_cm = res["delta_s"] * SPEED_OF_SOUND * 100
    later, sooner = ("right", "left") if delta_ms >= 0 else ("left", "right")
    louder = "right" if res["level_db"] >= 0 else "left"
    lines = [
        f"Detected {res['cycles']} left/right cycles (clock drift {res['drift_ppm']:+.0f} ppm).",
        f"The {later} side arrives {abs(delta_ms):.2f} ms later than the {sooner} side "
        f"(spread ±{res['spread_s']*1000:.2f} ms) -> {abs(dist_cm):.1f} cm farther acoustically.",
        f"The {louder} side is roughly {abs(res['level_db']):.1f} dB louder (impulse peak, approximate).",
        "",
        "Time alignment (distance entry on the head unit):",
        f"  {sooner} = D,  {later} = D + {abs(dist_cm):.0f} cm",
        f"  where D is the tape-measured distance from your head to the {sooner} speaker.",
    ]
    if left_cm is not None:
        right_cm = left_cm + dist_cm
        lines.append(f"  With --left-cm {left_cm:g}: left = {left_cm:.0f} cm, right = {right_cm:.0f} cm")
    lines += ["If time alignment was already on, the difference adds to the current settings.",
              "After applying it, measure again: the difference should be close to 0 ms."]
    log.info("\n".join(lines))


def plot(res, fs, out_png, show):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    env = res["env"]
    pre, post = int(PLOT_PRE_MS / 1000 * fs), int(PLOT_POST_MS / 1000 * fs)
    t_ms = (np.arange(-pre, post)) / fs * 1000
    segs = {"left": [], "right": []}
    for t_left, ratio in res["lefts"]:
        i_left = int(round(t_left * fs))
        # The right sweep is plotted on the left sweep's timeline, so its offset is the delay.
        i_right = int(round((t_left + ratio * gn.DELAY_T1) * fs))
        for name, i in (("left", i_left), ("right", i_right)):
            if i - pre >= 0 and i + post <= len(env):
                segs[name].append(env[i - pre:i + post])
    ref = max(np.max(np.mean(segs[n], axis=0)) for n in segs if segs[n])

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.canvas.manager.set_window_title("Delay")
    for name, color in (("left", "tab:blue"), ("right", "tab:red")):
        if segs[name]:
            ax.plot(t_ms, np.mean(segs[name], axis=0) / ref, color=color, lw=1.5, label=name)
    delta_ms = res["delta_s"] * 1000
    ax.axvline(0, color="tab:blue", ls=":", lw=1)
    ax.axvline(delta_ms, color="tab:red", ls=":", lw=1)
    ax.set_title(f"Arrival: right - left = {delta_ms:+.2f} ms "
                 f"({res['delta_s'] * SPEED_OF_SOUND * 100:+.1f} cm)")
    ax.set_xlabel("Time relative to left arrival (ms)")
    ax.set_ylabel("Impulse envelope (normalized)")
    ax.set_xlim(-PLOT_PRE_MS, PLOT_POST_MS)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    log.info("Saved %s", out_png)
    if show:
        plt.show()


def parse_device(value):
    return int(value) if value.isdigit() else value


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--device", type=parse_device, help="input device index or part of its name")
    p.add_argument("--channel", type=int, default=1, help="microphone input channel (default: 1)")
    p.add_argument("--fs", type=int, default=48000, help="sample rate (default: 48000)")
    p.add_argument("--seconds", type=float, default=12.0, help="recording length in s (default: 12)")
    p.add_argument("--left-cm", type=float, help="tape-measured distance to the left speaker, to print both values")
    p.add_argument("--wav", type=Path, help="analyze a recorded WAV instead of recording (testing)")
    p.add_argument("--save-wav", action="store_true", help="also save the recording to captures/")
    p.add_argument("--no-plot", action="store_true", help="don't open the plot window (PNG is still saved)")
    logsetup.add_arguments(p)
    args = p.parse_args()
    logsetup.setup(args)

    try:
        return run(args)
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        return 1


def run(args):
    if args.list_devices:
        import sounddevice as sd
        log.info("%s", sd.query_devices())
        return 0

    if args.wav:
        rec, fs = read_wav(args.wav)
    else:
        rec, fs = record(args), args.fs

    out = Path("captures")
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.save_wav:
        gn.write_wav(out / f"delay_{stamp}.wav", rec, rec, fs)

    peak = np.max(np.abs(rec))
    if peak >= 0.99:
        log.warning("the recording clipped; lower the microphone gain or the volume")
    elif peak < 0.01:
        log.warning("the recording is very quiet; raise the volume or the microphone gain")

    res = measure(rec, fs)
    if res is None:
        log.error("couldn't find the left/right sweep pattern. Check that delay_sweeps.wav is "
                  "playing on loop, both speakers work, and the level is well above the noise.")
        return 1
    report(res, args.left_cm)
    plot(res, fs, out / f"delay_{stamp}.png", show=not args.no_plot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
