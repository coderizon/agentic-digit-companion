"""Generate the entirely synthetic German recording used by the meeting smoke test."""
import argparse
import subprocess
import tempfile
import wave
from pathlib import Path


CUES = (
    (1, "Wir testen die mobile Meldung von Schäden auf Spielplätzen. Der Test beginnt am Montag."),
    (12, "Der Außendienst schickt ein Foto und den Standort an den Bauhof."),
    (23, "Anna erstellt bis Freitag eine kurze Anleitung für den Außendienst."),
    (34, "Die Kosten für neue Geräte sind noch offen."),
)


def make_mp3(out, voice_path):
    from piper import PiperVoice
    speaker = PiperVoice.load(str(voice_path))
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with wave.open(str(root / "meeting.wav"), "wb") as mix:
            for index, (start, line) in enumerate(CUES):
                take = root / f"{index}.wav"
                with wave.open(str(take), "wb") as wav:
                    speaker.synthesize_wav(line, wav)
                with wave.open(str(take), "rb") as voice:
                    if not index:
                        mix.setparams(voice.getparams())
                    gap = start * voice.getframerate() - mix.getnframes()
                    if gap < 0:
                        raise ValueError("Die Testaussagen überlappen sich.")
                    mix.writeframes(b"\0" * gap * voice.getsampwidth() * voice.getnchannels())
                    mix.writeframes(voice.readframes(voice.getnframes()))
        subprocess.run(["ffmpeg", "-v", "error", "-n", "-i", str(root / "meeting.wav"),
                        "-codec:a", "libmp3lame", "-q:a", "3", str(out)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--voice", required=True, type=Path, help="Lokale deutsche Piper-Stimme (.onnx)")
    args = parser.parse_args()
    make_mp3(args.output, args.voice)
