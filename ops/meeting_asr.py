"""Private CPU worker. Read one local recording, write its timed utterances to stdout."""
import json
import math
import sys
from pathlib import Path

import av
import numpy as np
from faster_whisper import WhisperModel


def utterances(segments, duration):
    output = []
    current = []

    def finish():
        if current:
            output.append({"start": round(current[0].start, 2),
                           "end": min(round(duration, 2), round(current[-1].end, 2)),
                           "text": "".join(w.word for w in current).strip()})
            current.clear()

    for segment in segments:
        for word in segment.words or ():
            if not word.word.strip() or not (0 <= word.start < word.end <= duration + 0.1):
                continue
            if current and (word.start - current[-1].end > 1.1 or word.end - current[0].start > 22):
                finish()
            current.append(word)
            if word.word.rstrip().endswith((".", "!", "?")):
                finish()
    finish()
    return output


def transcribe(source, model_path):
    # The protected upload must be an MP3. Do not auto-open external media references.
    pcm = bytearray()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(str(source), mode="r", format="mp3", options={"protocol_whitelist": "file"}) as audio:
        for frame in audio.decode(audio=0):
            frame.pts = None
            for part in resampler.resample(frame):
                pcm.extend(part.to_ndarray().tobytes())
        for part in resampler.resample(None):
            pcm.extend(part.to_ndarray().tobytes())
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    del pcm
    if not len(samples):
        raise ValueError("Die MP3 enthält keine Audiodaten.")
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         cpu_threads=6, num_workers=1, local_files_only=True)
    segments, info = model.transcribe(samples, language="de", beam_size=5,
                                      word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
                                      vad_parameters={"min_silence_duration_ms": 600})
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise ValueError("Aufnahme ohne gültige Laufzeit.")
    return {"duration": round(info.duration, 2), "segments": utterances(segments, info.duration)}


if __name__ == "__main__":
    print(json.dumps(transcribe(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False))
