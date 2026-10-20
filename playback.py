"""Audio output helpers: loop a test signal on an output device while measuring."""
import logging

import numpy as np

log = logging.getLogger(__name__)


def parse_device(value):
    """Device argument: an index ("3") or part of the device name ("Headphones")."""
    return int(value) if value.isdigit() else value


def start_loop(left, right, fs, device=None, name="signal"):
    """Play a stereo signal on loop in the background; returns the open stream.

    With the iMM-6 the output is usually the laptop's headphone output, which
    goes through the microphone's pass-through jack and an AUX cable to the car.
    """
    import sounddevice as sd

    data = np.stack([left, right], axis=1).astype(np.float32)
    pos = 0

    def callback(outdata, frames, t, status):
        nonlocal pos
        if status:
            log.warning("audio output: %s", status)
        idx = (np.arange(frames) + pos) % len(data)
        outdata[:] = data[idx]
        pos = (pos + frames) % len(data)

    stream = sd.OutputStream(device=device, channels=2, samplerate=fs, dtype="float32", callback=callback)
    stream.start()
    log.info("Playing %s on loop on output device: %s", name, sd.query_devices(stream.device)["name"])
    log.debug("output stream open: device=%s, %d Hz, latency %.1f ms",
              stream.device, stream.samplerate, stream.latency * 1000)
    return stream
