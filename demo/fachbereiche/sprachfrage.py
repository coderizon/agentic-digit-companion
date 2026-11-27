"""Lesezugriff auf das Übungshandbuch für Reachy. Eine kurze WAV oder eine Testfrage über stdin."""
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])
STATE = ROOT / "run/fachbereiche"


def transcribe():
    import numpy as np
    from faster_whisper import WhisperModel

    pcm = sys.stdin.buffer.read(450000)
    with wave.open(io.BytesIO(pcm), "rb") as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, 16000, 2):
            raise ValueError("Erwartet werden 16 kHz, mono, PCM16.")
        if not 2400 <= wav.getnframes() <= 192000:
            raise ValueError("Zugelassen sind bis zu zwölf Sekunden pro Frage.")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.
    model = WhisperModel(str(ROOT / "models/meeting-whisper-small"), device="cpu", compute_type="int8",
                         cpu_threads=5, local_files_only=True)
    parts, _ = model.transcribe(samples, language="de", beam_size=4, vad_filter=True,
                                condition_on_previous_text=False, hotwords="RaumPlan, Mitarbeitendenportal, RP-12")
    return " ".join(p.text.strip() for p in parts if p.text.strip())


def demo_token():
    import httpx

    source = json.loads((STATE / "it-handbuch.json").read_bytes())
    credential = json.loads((STATE / "planung.credentials.json").read_bytes())
    with httpx.Client(trust_env=False, timeout=20) as client:
        r = client.post(source["url"] + "/api/v1/auths/signin", json=credential)
        r.raise_for_status()
    return r.json()["token"]


def answer(question):
    from it_service import find_section, identity, web_context

    settings, web = web_context(demo_token())
    with web:
        identity(web, settings)
        section = find_section(web, settings, question)
    if section:
        title, text = section
        return {"frage": question, "antwort": text, "quelle": settings["name"] + ", Abschnitt " + title,
                "datei": settings["file"]}
    return {"frage": question, "antwort": "Im IT-Handbuch finde ich dafür keine passende Anleitung. "
            "Du kannst die Störung im DIGIT Companion schildern und dort einen Ticketentwurf prüfen.",
            "quelle": settings["name"]}


if __name__ == "__main__":
    if sys.argv[1:] == ["--transkribieren"]:
        print(transcribe(), end="")
    else:
        if sys.argv[1:] == ["--text"]:
            frage = sys.stdin.buffer.read(512).decode("utf-8").strip()
        elif not sys.argv[1:]:
            data = sys.stdin.buffer.read(450001)
            if len(data) > 450000:
                raise ValueError("Die Aufnahme ist zu groß.")
            task = subprocess.run([str(ROOT / ".audio/bin/python"), __file__, "--transkribieren"], input=data,
                                  stdout=subprocess.PIPE, timeout=65, check=True)
            frage = task.stdout.decode().strip()
        else:
            raise SystemExit("Aufruf mit WAV auf stdin oder --text mit UTF-8.")
        if frage:
            print(json.dumps(answer(frage), ensure_ascii=False))
        else:
            print(json.dumps({"frage": "", "antwort": "Ich habe keine Frage verstanden. Bitte sprich noch einmal."},
                             ensure_ascii=False))
