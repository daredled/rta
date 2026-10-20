# RTA for tuning car audio

Real-time spectrum analyzer (RTA) and time alignment measurement in Python, built for the
**Dayton Audio iMM-6** microphone. **All you need is a laptop**: it plays the test signals into
the car through the iMM-6's headphone pass-through and records the microphone at the same time.

Everything runs through `main.py`:

- `rta` (`rta.py`) plays pink noise, captures the microphone, applies the iMM-6 calibration and
  shows the response in 1/3-octave bands (configurable) alongside a target curve. Has a
  subwoofer mode and labeled overlays to compare settings.
- `delay` (`delay.py`) plays left/right sweeps, measures the arrival time difference and tells
  you which distances to enter in the head unit's time alignment.
- `generate` (`gen_noise.py`) creates WAV files of the test signals, only needed for the
  [phone fallback](#fallback-phone--laptop).

![RTA in 1/3 octave with a 5-position spatial average](docs/rta_spatial_average.png)

*Screenshots use simulated car responses fed through `--wav`, not real measurements.*

## Setup

### Connections

The laptop's combined headphone+mic jack does both jobs:

```
laptop combo jack ── iMM-6 ── pass-through ── AUX cable ── head unit AUX input
   (mic in + headphone out)
```

- Use a **4-pole TRRS extension cable** (CTIA, 1–2 m) between the laptop and the iMM-6, so the
  microphone can sit at head height while the laptop rests on the seat.
- If your laptop has separate headphone and mic jacks, a TRRS splitter (one 4-pole female to
  two 3-pole plugs) gives the same setup.
- If the head unit has no AUX input, pair the laptop over Bluetooth and choose the car as the
  output device. Keep the laptop jack as the input, not the car's hands-free microphone.
- Hum or whine with the laptop charging from the car is a ground loop: unplug the charger or
  add a ground loop isolator on the AUX cable.

If the combo jack can't play and record at the same time with the iMM-6 plugged in, see the
[phone fallback](#fallback-phone--laptop).

### Installation

> Microphone access from WSL is unreliable: run it with **native Windows** Python
> (or directly on macOS/Linux).

Requires [uv](https://docs.astral.sh/uv/). On Linux also: `sudo apt install libportaudio2`
(on Windows and macOS PortAudio ships with `sounddevice`).

```bash
uv sync                                          # create .venv and install dependencies
uv run main.py rta --list-devices                # find the mic (input) and headphones (output)
uv run main.py rta --device 1 --out-device 2 --cal 7012345.txt
```

`--device` is the microphone input and `--out-device` is the laptop's headphone output (often
the same sound card). Start with the car's volume low; `--play-level` sets the signal level
(default -18 dBFS RMS).

Test without a microphone: `uv run main.py rta --wav signals/pink_noise_LR.wav` (should look
flat; create the file first with `uv run main.py generate`). Every command accepts `-v` for
debug output and `--help` for its options.

### Microphone settings

- On Windows, disable the microphone's **audio enhancements / noise suppression / AGC**
  (Settings → Sound → microphone → Audio enhancements: off) and set a fixed gain.
  Otherwise Windows reshapes the signal and the measurement is useless.
- Set the gain so the level sits between -40 and -12 dBFS with no CLIPPING warning.

### Calibration file

Each iMM-6 has its own file matched to its serial number (printed on the microphone). Download
it from the iMM-6 page on daytonaudio.com by entering the serial. Use it with `--cal file.txt`.
If you point the microphone at the roof, use the 90° file if available; if you point it
toward the front of the car, use the 0° file.

## Suggested in-car procedure

Before starting: head unit EQ flat, loudness/bass boost/"sound effects" off, fader and balance
centered. Engine off, or idling with the fan off. The microphone goes at the driver's head
height. The examples use `--device 1 --out-device 2 --cal 7012345.txt`; use your own values.

### 1. Rattle test

```bash
uv run main.py rta --device 1 --out-device 2 --play rattle
```

This plays a slow 20–120 Hz sine sweep (60 s, on loop). Raise the volume to a moderate-to-loud
level and listen for buzzing door panels, mirrors, the license plate, etc. Fix them first: they
add distortion that no EQ can remove.

### 2. Time alignment (`main.py delay`)

1. Tape-measure the distance from your head to each speaker and enter it in the head unit's
   time alignment (Pioneer units take distances). That alone gets most of the benefit.
2. Put the microphone at your head position and run:

   ```bash
   uv run main.py delay --device 1 --out-device 2 --left-cm 95   # your tape-measured left distance
   ```

   It plays and records 12 s of alternating left/right sweeps, finds each sweep's impulse
   response and prints how much later one side arrives, as a distance:

   ```
   The right side arrives 1.50 ms later than the left side (spread ±0.00 ms) -> 51.4 cm farther acoustically.
   Time alignment (distance entry on the head unit):
     left = D,  right = D + 51 cm
     With --left-cm 95: left = 95 cm, right = 146 cm
   ```

3. Apply it and measure again: the difference should be close to 0 ms. If time alignment was
   already on, the measured difference adds to the current settings.

![Left/right impulse responses](docs/delay.png)

With the laptop playing over a cable, playback and recording share one clock, with no codec or
buffering in between, which makes this measurement very consistent. Only relative timing is
measured, which is all the head unit needs. `delay` warns when the cycles disagree (typical of
Bluetooth glitches). For the rear speakers, use the fader to play only the rear pair.

### 3. Subwoofer (`main.py rta --sub`)

Sub mode plays pink noise limited to 40–200 Hz and shows 20–300 Hz in 1/12 octave, absolute
dBFS, slow averaging, and the level of the octave around the crossover (`--xo`, default 80 Hz).
Keep the sub and the front speakers on:

```bash
uv run main.py rta --device 1 --out-device 2 --cal 7012345.txt --sub --xo 80 --labels "phase normal,phase reverse"
```

1. **Phase/polarity:** wait for the curve to settle and press **o** to keep it as an overlay
   ("phase normal"). Switch the sub phase on the head unit, wait, press **o** again. Keep the
   setting with the higher, smoother response around the crossover. The wrong one shows a dip.
2. **Sub distance:** with the better phase, step the sub's distance in the time alignment
   and keep an overlay for each value. Keep the one with the strongest, flattest crossover region.
3. **Level:** go back to the normal mode (full-range pink noise, next step) and set the sub
   level so the bass matches the target curve.

![Sub phase test: normal vs reverse](docs/rta_sub_phase.png)

### 4. EQ

```bash
uv run main.py rta --device 1 --out-device 2 --cal 7012345.txt
```

1. This plays pink noise on both channels. Set a normal-to-loud listening level.
2. Move the microphone through 4–6 positions around your head (±15 cm) and press **a** at each
   one to add it to the spatial average (orange curve). This avoids correcting peaks/dips that
   only exist at a single point.
3. Press **e** to see the suggested per-band EQ correction and apply it on the head unit's EQ.
   Boosts are limited to +6 dB: a deep, narrow dip is usually an acoustic null that EQ can't fix.

   ```
   Suggested EQ correction (average of 5 positions, limits -12/+6 dB):
        125 Hz   +5.7 dB  ++++++
        157 Hz   +6.0 dB  ++++++
        ...
      2.52 kHz   -4.4 dB  ----
   ```

4. Measure again, compare (**o** overlays help here too) and save with **s** (CSV + PNG in
   `captures/`).
5. Optional: `--play pink-L` and `--play pink-R` measure each side separately.

1/6-octave resolution (`--bands 6`) shows more detail:

![RTA in 1/6 octave](docs/rta_sixth_octave.png)

## Fallback: phone + laptop

Use this only if the laptop's combo jack causes problems, for example:

- the headphone output goes silent, or the mic isn't detected, while the iMM-6 is plugged in;
- the pass-through output is noisy or hums even with the charger unplugged;
- the laptop can't open the input and output at the same time.

Then a phone plays the test signals and the laptop only records:

1. Create the WAV files and copy them to the phone:

   ```bash
   uv run main.py generate --type all   # creates every test signal in signals/
   ```

2. Play the matching file on loop from the phone, connected to the car (cable/USB/CarPlay is
   better than Bluetooth). Turn off the phone's Music EQ and Sound Check.
3. Run the same commands as above with `--no-play` instead of `--out-device`:

   ```bash
   uv run main.py rta --device 1 --cal 7012345.txt --no-play
   uv run main.py delay --device 1 --left-cm 95 --no-play
   ```

| File | Use |
|---|---|
| `rattle_sweep.wav` | Rattle test: slow 20–120 Hz sine sweep (60 s) |
| `delay_sweeps.wav` | Time alignment: alternating left/right sweeps for `main.py delay` |
| `sub_noise_LR.wav` | Subwoofer phase/distance: pink noise limited to 40–200 Hz |
| `pink_noise_LR.wav` | EQ: same pink noise on both channels |
| `pink_noise_L.wav`, `pink_noise_R.wav` | EQ: one side at a time |
| `white_noise_*.wav` | White noise, same layout as pink (rarely needed) |

All files loop seamlessly. Time alignment still works this way (the clock drift between phone
and laptop is estimated and removed), but it's less robust, especially over Bluetooth: if
`delay` warns that the cycles are inconsistent, measure again.

## Keys (`main.py rta`)

| Key | Action |
|---|---|
| space | freeze / resume |
| r | reset averaging and peak hold |
| h | show/hide peak hold |
| a / c | add position / clear spatial average |
| o / O | keep the live curve as a labeled overlay / remove the last overlay |
| n | normalized (0 dB = 300–3000 Hz average) / absolute dBFS |
| ↑ / ↓ | slower / faster averaging |
| e | print EQ correction |
| s | save CSV + PNG (overlays included) |
| q | quit |

## Useful options

- `--play pink|pink-L|pink-R|sub|rattle|white`: signal the laptop plays (default `pink`, or
  `sub` with `--sub`). `--no-play` for the phone fallback.
- `--bands 6` (or 12, 24) for more resolution. 3 = 1/3 octave, same as a 31-band EQ.
- `--target flat|car|none|file.txt` target curve. `car` = about +10 dB bass boost and a gentle
  roll-off toward the treble (typical in cars). You can use your own "frequency dB" file.
- `--tau 2` for steadier averaging (pink noise fluctuates; 1–2 s is reasonable).
- `--sub`, `--xo 80`: subwoofer mode and crossover frequency.
- `--labels "a,b,c"`: names for the overlays, in order (otherwise A, B, C...).
- `main.py delay --seconds 20` for more cycles, `--save-wav` to keep the recording.
