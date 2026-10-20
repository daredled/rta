# RTA for tuning car audio

Real-time spectrum analyzer (RTA) in Python, built for the **Dayton Audio iMM-6**
microphone and pink noise played from an iPhone.

- `gen_noise.py` generates pink noise WAV files (both channels, left only, right only) to copy to the iPhone.
- `rta.py` captures the microphone, applies the iMM-6 calibration and shows the response in
  1/3-octave bands (configurable) alongside a target curve.

## Installation

> Microphone access from WSL is unreliable: run it with **native Windows** Python
> (or directly on macOS/Linux).

Requires [uv](https://docs.astral.sh/uv/). On Linux also: `sudo apt install libportaudio2`
(on Windows and macOS PortAudio ships with `sounddevice`).

```bash
uv sync                          # create .venv and install dependencies
uv run gen_noise.py              # creates signals/pink_noise_{LR,L,R}.wav
uv run rta.py --list-devices     # find the microphone's device index
uv run rta.py --device 1 --cal 7012345.txt
```

Test without a microphone: `uv run rta.py --wav signals/pink_noise_LR.wav` (should look flat).

## Using the microphone with a laptop

- The iMM-6 has a **TRRS** plug (phone-style). It works in a laptop's combined
  headphone+mic jack. If your laptop has separate jacks, use a USB adapter with a TRRS
  input (cheap "headset" USB sound cards work).
- On Windows, disable the microphone's **audio enhancements / noise suppression / AGC**
  (Settings → Sound → microphone → Audio enhancements: off) and set a fixed gain.
  Otherwise Windows reshapes the signal and the measurement is useless.
- Set the gain so the level sits between -40 and -12 dBFS with no CLIPPING warning.

## Calibration file

Each iMM-6 has its own file matched to its serial number (printed on the microphone). Download
it from the iMM-6 page on daytonaudio.com by entering the serial. Use it with `--cal file.txt`.
If you point the microphone at the roof, use the 90° file if available; if you point it
toward the front of the car, use the 0° file.

## Suggested in-car procedure

1. Head unit: flat EQ, loudness/bass boost/"sound effects" off, fader and balance centered.
   iPhone: Music EQ off, Sound Check off. Prefer cable/USB/CarPlay over Bluetooth.
2. Play `pink_noise_LR.wav` on loop at a normal-to-loud listening level (engine off, or idling
   with the fan off, to reduce noise).
3. Place the microphone at the driver's head height. Move it through 4–6 positions around
   that spot (±15 cm) and press **a** at each one to add it to the spatial average (orange
   curve). This avoids correcting peaks/dips that only exist at a single point.
4. Press **e** to see the suggested per-band EQ correction and apply it on the head unit's EQ.
   Boosts are limited to +6 dB: a deep, narrow dip is usually an acoustic null that EQ
   can't fix.
5. Measure again, compare, and save with **s** (CSV + PNG in `captures/`).
6. Optional: use `pink_noise_L.wav` and `pink_noise_R.wav` to compare each side.

## Keys

| Key | Action |
|---|---|
| space | freeze / resume |
| r | reset averaging and peak hold |
| h | show/hide peak hold |
| a / c | add position / clear spatial average |
| n | normalized (0 dB = 300–3000 Hz average) / absolute dBFS |
| ↑ / ↓ | slower / faster averaging |
| e | print EQ correction |
| s | save CSV + PNG |
| q | quit |

## Useful options

- `--bands 6` (or 12, 24) for more resolution. 3 = 1/3 octave, same as a 31-band EQ.
- `--target flat|car|none|file.txt` target curve. `car` = about +10 dB bass boost and a gentle
  roll-off toward the treble (typical in cars). You can use your own "frequency dB" file.
- `--tau 2` for steadier averaging (pink noise fluctuates; 1–2 s is reasonable).
