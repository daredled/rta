#!/usr/bin/env python3
"""RTA: real-time spectrum analyzer for tuning audio with pink noise.

Captures audio from the microphone (e.g. Dayton iMM-6), applies the
microphone's calibration file and shows the response in 1/N-octave bands.
With pink noise as the test signal, a flat system shows as a horizontal line.

Keys (with the plot window focused):
    space    freeze / resume
    r        reset averaging and peak hold
    h        show / hide peak hold
    a        add the current curve to the spatial average (multiple positions)
    c        clear the spatial average
    n        toggle normalized (0 dB over 300-3000 Hz) / absolute dBFS
    ↑ / ↓    slower / faster averaging
    e        print the suggested per-band EQ correction to the console
    s        save capture (CSV + PNG) to the captures/ folder
    q        quit
"""
import argparse
import csv
import re
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

REF_LO, REF_HI = 300.0, 3000.0  # range where the curve is normalized to 0 dB

# Built-in target curves (frequency Hz, dB). "car" is a typical in-car curve:
# bass boost and a gentle treble roll-off.
TARGETS = {
    "flat": [(20, 0), (20000, 0)],
    "car": [(20, 10), (31.5, 10), (50, 9), (80, 7), (125, 4), (200, 1.5), (300, 0),
            (1000, 0), (2000, -0.5), (4000, -2), (8000, -4), (12500, -5.5), (20000, -8)],
}

TAU_STEPS = [0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0]  # averaging time constants (s)

NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def load_curve_file(path):
    """Read a curve file (calibration or target): 'frequency dB' per line.

    Skips headers and lines that don't start with two numbers, so it accepts
    Dayton Audio, REW, CSV, tab-separated, etc.
    """
    freqs, dbs = [], []
    for line in Path(path).read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or not (line[0].isdigit() or line[0] == "."):
            continue
        nums = NUM_RE.findall(line.replace(",", " ").replace(";", " "))
        if len(nums) >= 2:
            freqs.append(float(nums[0]))
            dbs.append(float(nums[1]))
    if len(freqs) < 2:
        sys.exit(f"Error: no 'frequency dB' data found in {path}")
    order = np.argsort(freqs)
    return np.asarray(freqs)[order], np.asarray(dbs)[order]


def interp_log(f, fp, dbp):
    """Interpolate dB on a log-frequency scale (ends held constant)."""
    return np.interp(np.log10(f), np.log10(fp), dbp)


def ref_mean(f, db):
    sel = (f >= REF_LO) & (f <= REF_HI)
    return float(np.mean(db[sel])) if sel.any() else float(np.mean(db))


class Ring:
    """Thread-safe ring buffer for microphone samples."""

    def __init__(self, size):
        self.buf = np.zeros(size, dtype=np.float32)
        self.pos = 0
        self.lock = threading.Lock()
        self.peak = 0.0

    def write(self, x):
        n = len(x)
        with self.lock:
            self.peak = max(self.peak, float(np.max(np.abs(x))) if n else 0.0)
            if n >= len(self.buf):
                self.buf[:] = x[-len(self.buf):]
                self.pos = 0
                return
            end = self.pos + n
            if end <= len(self.buf):
                self.buf[self.pos:end] = x
            else:
                k = len(self.buf) - self.pos
                self.buf[self.pos:] = x[:k]
                self.buf[:n - k] = x[k:]
            self.pos = end % len(self.buf)

    def latest(self, n):
        with self.lock:
            out = np.roll(self.buf, -self.pos)[-n:].copy()
            peak, self.peak = self.peak, 0.0
        return out, peak


class Analyzer:
    """Hann-windowed FFT -> averaged PSD -> power per 1/N-octave band."""

    def __init__(self, fs, nfft, n_oct, fmin, fmax, cal=None):
        self.fs, self.nfft = fs, nfft
        self.window = np.hanning(nfft).astype(np.float32)
        self.psd_scale = 2.0 / (fs * np.sum(self.window**2))
        self.freqs = np.fft.rfftfreq(nfft, 1.0 / fs)

        # 3% tolerance so the nominal 20 Hz and 20 kHz bands are included.
        k = np.arange(np.ceil(n_oct * np.log2(fmin * 0.97 / 1000)),
                      np.floor(n_oct * np.log2(fmax * 1.03 / 1000)) + 1)
        self.fc = 1000.0 * 2.0 ** (k / n_oct)
        lo = self.fc * 2.0 ** (-0.5 / n_oct)
        hi = self.fc * 2.0 ** (0.5 / n_oct)
        self.bw = hi - lo
        self.lo_idx = np.searchsorted(self.freqs, lo)
        self.hi_idx = np.searchsorted(self.freqs, hi)
        # Narrow low-frequency bands may contain no bin: interpolate those.
        self.empty = self.hi_idx <= self.lo_idx

        self.cal_db = np.zeros_like(self.fc) if cal is None else interp_log(self.fc, *cal)
        self.avg_psd = None

    def reset(self):
        self.avg_psd = None

    def update(self, x, alpha):
        spec = np.fft.rfft(x * self.window)
        psd = (np.abs(spec) ** 2) * self.psd_scale
        if self.avg_psd is None:
            self.avg_psd = psd
        else:
            self.avg_psd += alpha * (psd - self.avg_psd)

    def bands_power(self):
        """Power per band (linear), corrected by the microphone calibration."""
        psd = self.avg_psd
        cs = np.concatenate([[0.0], np.cumsum(psd)])
        count = np.maximum(self.hi_idx - self.lo_idx, 1)
        mean_psd = (cs[self.hi_idx] - cs[self.lo_idx]) / count
        if self.empty.any():
            mean_psd[self.empty] = np.interp(self.fc[self.empty], self.freqs, psd)
        power = mean_psd * self.bw
        return power * 10 ** (-self.cal_db / 10)


def open_mic(args, ring):
    import sounddevice as sd

    def callback(indata, frames, t, status):
        if status:
            print(status, file=sys.stderr)
        ring.write(indata[:, args.channel - 1])

    stream = sd.InputStream(device=args.device, channels=args.channel, samplerate=args.fs,
                            blocksize=1024, dtype="float32", callback=callback)
    stream.start()
    return stream


def play_wav(path, fs, ring, stop):
    """Simulate the microphone by looping a WAV in real time (for testing)."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != fs:
            sys.exit(f"Error: the WAV is {w.getframerate()} Hz; use --fs {w.getframerate()}")
        width, nch = w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width != 2:
        sys.exit("Error: only 16-bit WAV files are supported")
    data = np.frombuffer(raw, dtype="<i2").reshape(-1, nch)[:, 0].astype(np.float32) / 32768
    block, pos, t0 = 1024, 0, time.monotonic()
    sent = 0
    while not stop.is_set():
        idx = (np.arange(block) + pos) % len(data)
        ring.write(data[idx])
        pos = (pos + block) % len(data)
        sent += block
        delay = t0 + sent / fs - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def parse_device(value):
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--device", type=parse_device, help="input device index or part of its name")
    p.add_argument("--channel", type=int, default=1, help="microphone input channel (default: 1)")
    p.add_argument("--fs", type=int, default=48000, help="sample rate (default: 48000)")
    p.add_argument("--fft", type=int, default=16384, help="FFT size (default: 16384)")
    p.add_argument("--bands", type=int, default=3, choices=[1, 2, 3, 6, 12, 24],
                   help="resolution in 1/N octave (default: 3 -> 1/3 octave)")
    p.add_argument("--fmin", type=float, default=20.0)
    p.add_argument("--fmax", type=float, default=20000.0)
    p.add_argument("--cal", type=Path, help="microphone calibration file (Dayton iMM-6)")
    p.add_argument("--target", default="car",
                   help="target curve: 'car', 'flat', 'none' or a file path (default: car)")
    p.add_argument("--tau", type=float, default=1.0, help="averaging time constant in s (default: 1)")
    p.add_argument("--wav", type=Path, help="analyze a looping WAV instead of the microphone (testing)")
    args = p.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    cal = load_curve_file(args.cal) if args.cal else None
    if cal is None:
        print("Warning: no calibration file (--cal); the curve includes the microphone's own response.")

    if args.target == "none":
        target = None
    elif args.target in TARGETS:
        target = tuple(np.array(v, dtype=float) for v in zip(*TARGETS[args.target]))
    else:
        target = load_curve_file(args.target)

    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.ticker import FixedLocator, NullFormatter, NullLocator

    for key in list(matplotlib.rcParams):
        if key.startswith("keymap."):
            matplotlib.rcParams[key] = []

    an = Analyzer(args.fs, args.fft, args.bands, args.fmin, args.fmax, cal)
    fc = an.fc
    target_db = None if target is None else interp_log(fc, *target)
    if target_db is not None:
        target_db -= ref_mean(fc, target_db)

    ring = Ring(max(args.fft * 2, args.fs))
    stop = threading.Event()
    if args.wav:
        threading.Thread(target=play_wav, args=(args.wav, args.fs, ring, stop), daemon=True).start()
        source = f"WAV: {args.wav.name}"
        stream = None
    else:
        stream = open_mic(args, ring)
        source = "microphone"

    state = {
        "paused": False, "normalized": True, "show_peak": True,
        "tau_i": int(np.argmin(np.abs(np.array(TAU_STEPS) - args.tau))),
        "peak": None, "spatial": [], "last_t": time.monotonic(),
        "cur_power": None, "level": -np.inf, "clip_until": 0.0,
    }

    fig, ax = plt.subplots(figsize=(12, 6.5))
    fig.canvas.manager.set_window_title("RTA")
    ax.set_xscale("log")
    ax.set_xlim(args.fmin / 1.12, args.fmax * 1.12)
    ticks = [f for f in (20, 31.5, 50, 63, 100, 125, 200, 250, 500, 1000, 2000, 4000, 5000, 8000, 10000, 16000, 20000)
             if args.fmin <= f <= args.fmax]
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xticklabels([f"{t/1000:g}k" if t >= 1000 else f"{t:g}" for t in ticks])
    ax.set_xlabel("Frequency (Hz)")
    ax.grid(True, which="major", alpha=0.3)

    (l_target,) = ax.plot(fc, target_db if target_db is not None else np.full_like(fc, np.nan),
                          "--", color="tab:green", lw=1.5, label=f"target ({args.target})")
    (l_peak,) = ax.plot(fc, np.full_like(fc, np.nan), "-", color="tab:red", lw=0.8, alpha=0.6, label="peak hold")
    (l_spatial,) = ax.plot(fc, np.full_like(fc, np.nan), "-", color="tab:orange", lw=2.5, label="spatial average")
    (l_live,) = ax.plot(fc, np.full_like(fc, np.nan), "o-", color="tab:blue", lw=1.8, ms=3.5, label="live")
    if target_db is None:
        l_target.set_visible(False)
    ax.legend(loc="lower left", fontsize=9)
    status = ax.text(0.01, 0.98, "", transform=ax.transAxes, va="top", family="monospace", fontsize=9)
    fig.text(0.5, 0.005, "space=freeze  r=reset  h=peak  a=add position  c=clear average  "
             "n=normalize  ↑↓=speed  e=EQ  s=save  q=quit", ha="center", fontsize=8, color="gray")
    fig.tight_layout(rect=(0, 0.02, 1, 1))

    def to_db(power):
        return 10 * np.log10(np.maximum(power, 1e-20))

    def display(power):
        """Convert power to dB for display (normalized or absolute)."""
        db = to_db(power)
        return db - ref_mean(fc, db) if state["normalized"] else db

    def spatial_power():
        return np.mean(state["spatial"], axis=0) if state["spatial"] else None

    def apply_ylim():
        if state["normalized"]:
            ax.set_ylim(-25, 20)
            ax.set_ylabel("Relative level (dB, 0 dB = 300–3000 Hz average)")
        else:
            ax.set_ylim(-110, 0)
            ax.set_ylabel("Band level (dBFS)")
    apply_ylim()

    def update(_frame):
        now = time.monotonic()
        dt, state["last_t"] = now - state["last_t"], now
        x, peak = ring.latest(args.fft)
        if peak >= 0.99:
            state["clip_until"] = now + 1.0
        state["level"] = 20 * np.log10(max(np.sqrt(np.mean(x[-4096:] ** 2)), 1e-10))

        if not state["paused"]:
            alpha = 1.0 - np.exp(-dt / TAU_STEPS[state["tau_i"]])
            an.update(x, alpha)
            state["cur_power"] = an.bands_power()
            state["peak"] = state["cur_power"].copy() if state["peak"] is None \
                else np.maximum(state["peak"], state["cur_power"])

        cur = state["cur_power"]
        if cur is None:
            return
        l_live.set_ydata(display(cur))
        if state["show_peak"] and state["peak"] is not None:
            # Peak hold is drawn with the same offset as the live curve.
            offset = ref_mean(fc, to_db(cur)) if state["normalized"] else 0.0
            l_peak.set_ydata(to_db(state["peak"]) - offset)
        else:
            l_peak.set_ydata(np.full_like(fc, np.nan))
        sp = spatial_power()
        l_spatial.set_ydata(display(sp) if sp is not None else np.full_like(fc, np.nan))

        clip = "  CLIPPING! lower the microphone gain" if now < state["clip_until"] else ""
        status.set_text(
            f"source: {source}   level: {state['level']:6.1f} dBFS{clip}\n"
            f"bands: 1/{args.bands} oct   averaging τ={TAU_STEPS[state['tau_i']]:g}s   "
            f"positions averaged: {len(state['spatial'])}"
            f"{'   [FROZEN]' if state['paused'] else ''}"
            f"{'' if cal is not None else '   [UNCALIBRATED]'}"
        )
        status.set_color("red" if clip else "black")

    def reference_curve():
        """Curve used for EQ/saving: the spatial average if present, otherwise the live one."""
        sp = spatial_power()
        return sp if sp is not None else state["cur_power"]

    def eq_correction(power):
        db = to_db(power)
        db -= ref_mean(fc, db)
        if target_db is None:
            return None
        # Limit boosts: a big boost eats amplifier power and is usually an acoustic null.
        return np.clip(target_db - db, -12.0, 6.0)

    def print_eq():
        power = reference_curve()
        if power is None:
            return
        corr = eq_correction(power)
        if corr is None:
            print("No target curve (--target none): cannot compute a correction.")
            return
        src = f"average of {len(state['spatial'])} positions" if state["spatial"] else "live curve"
        print(f"\nSuggested EQ correction ({src}, limits -12/+6 dB):")
        for f, c in zip(fc, corr):
            label = f"{f/1000:.3g} kHz" if f >= 1000 else f"{f:.3g} Hz"
            bar = ("+" * int(round(c)) if c > 0 else "-" * int(round(-c)))
            print(f"  {label:>9}  {c:+5.1f} dB  {bar}")

    def save():
        power = reference_curve()
        if power is None:
            return
        out = Path("captures")
        out.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_abs = to_db(state["cur_power"])
        sp = spatial_power()
        corr = eq_correction(power)
        with open(out / f"rta_{stamp}.csv", "w", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["freq_hz", "live_dbfs", "live_rel_db", "spatial_rel_db", "target_db", "eq_correction_db"])
            live_rel = db_abs - ref_mean(fc, db_abs)
            sp_rel = None if sp is None else to_db(sp) - ref_mean(fc, to_db(sp))
            for i, f in enumerate(fc):
                wr.writerow([f"{f:.1f}", f"{db_abs[i]:.2f}", f"{live_rel[i]:.2f}",
                             "" if sp_rel is None else f"{sp_rel[i]:.2f}",
                             "" if target_db is None else f"{target_db[i]:.2f}",
                             "" if corr is None else f"{corr[i]:.2f}"])
        fig.savefig(out / f"rta_{stamp}.png", dpi=120)
        print(f"Saved {out / f'rta_{stamp}.csv'} and .png")

    def on_key(event):
        k = event.key
        if k == " ":
            state["paused"] = not state["paused"]
        elif k == "r":
            an.reset()
            state["peak"] = None
        elif k == "h":
            state["show_peak"] = not state["show_peak"]
        elif k == "a" and state["cur_power"] is not None:
            state["spatial"].append(state["cur_power"].copy())
            print(f"Position {len(state['spatial'])} added to the spatial average")
        elif k == "c":
            state["spatial"].clear()
        elif k == "n":
            state["normalized"] = not state["normalized"]
            apply_ylim()
        elif k == "up":
            state["tau_i"] = min(state["tau_i"] + 1, len(TAU_STEPS) - 1)
        elif k == "down":
            state["tau_i"] = max(state["tau_i"] - 1, 0)
        elif k == "e":
            print_eq()
        elif k == "s":
            save()
        elif k == "q":
            plt.close(fig)

    fig.canvas.mpl_connect("key_press_event", on_key)
    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # noqa: F841 (keep a reference)
    try:
        plt.show()
    finally:
        stop.set()
        if stream is not None:
            stream.stop()
            stream.close()


if __name__ == "__main__":
    main()
