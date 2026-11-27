"""Kurze Audioaktion auf einem Reachy Mini Wireless. Normale SDK-Funktionen, kein Dauerbetrieb."""
import contextlib
import io
import os
import sys
import tempfile
import time
import wave
from pathlib import Path


def record(mini, seconds):
    import numpy as np
    from scipy.signal import resample_poly
    from math import gcd
    pieces = []
    mini.media.start_recording()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            sample = mini.media.get_audio_sample()
            if sample is None:
                time.sleep(0.005)
            else:
                pieces.append(sample)
    finally:
        mini.media.stop_recording()
    if not pieces:
        raise RuntimeError("Reachy hat keine Audiodaten geliefert.")
    samples = np.concatenate(pieces, axis=0)
    if np.issubdtype(samples.dtype, np.integer):
        samples = samples.astype(np.float32) / max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    rate = int(mini.media.get_input_audio_samplerate())
    divisor = gcd(rate, 16000)
    audio = resample_poly(samples, 16000 // divisor, rate // divisor)[:seconds * 16000]
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def play(mini, data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
    if not 0 < duration <= 75:
        raise ValueError("Der vorgelesene Text ist zu lang.")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "antwort.wav"
        path.write_bytes(data)
        mini.media.play_sound(str(path))
        time.sleep(duration + .4)


if __name__ == "__main__":
    mode = sys.argv[1]
    raw = b""
    with contextlib.redirect_stdout(sys.stderr):
        from reachy_mini import ReachyMini
        with ReachyMini() as mini:
            if mode == "hoeren":
                raw = record(mini, int(sys.argv[2]))
            elif mode == "sprechen":
                play(mini, sys.stdin.buffer.read(3500000))
            else:
                raise ValueError("Unbekannte Audioaktion.")
    sys.stdout.buffer.write(raw)
