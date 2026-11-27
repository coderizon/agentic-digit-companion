"""Windows-Sprachdemo für Reachy und das IT-Handbuch. Die T4 erhält nur die ausdrücklich gestellte Frage."""
import argparse
import base64
import io
import json
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path


def ssh(args, host, command, data=b"", timeout=90):
    cfg = ["-F", str(args.ssh_config)] if args.ssh_config else []
    return subprocess.run(["ssh", "-T", *cfg, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                           "-o", "ConnectTimeout=7", host, command],
                          input=data, stdout=subprocess.PIPE, check=True, timeout=timeout).stdout


def robot(args, action, data=b""):
    source = (Path(__file__).parent / "roboter_audio.py").read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    command = "/venvs/apps_venv/bin/python -c \"import base64;exec(base64.b64decode('" + encoded + "'))\" "
    return ssh(args, args.robot, command + action, data)


def tts(text, out):
    script = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $de = $voice.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'de' } | Select-Object -First 1
    if (-not $de) { throw 'Eine deutsche Windows-Stimme fehlt.' }
    $voice.SelectVoice($de.VoiceInfo.Name)
    $pcm = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $voice.SetOutputToWaveFile($env:DIGIT_SPEECH_OUT, $pcm)
    $voice.Speak([IO.File]::ReadAllText($env:DIGIT_SPEECH_IN, [Text.Encoding]::UTF8))
} finally { $voice.Dispose() }
"""
    with tempfile.TemporaryDirectory() as directory:
        src = Path(directory) / "text.txt"
        src.write_text(text, encoding="utf8")
        env = {**os.environ, "DIGIT_SPEECH_IN": str(src), "DIGIT_SPEECH_OUT": str(out)}
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                        base64.b64encode(script.encode("utf-16-le")).decode("ascii")], env=env, check=True, timeout=45)
    return out.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--t4", required=True, help="SSH-Alias für die DIGIT-Demo")
    parser.add_argument("--robot", help="SSH-Alias für den eigenen, bereits gekoppelten Reachy")
    parser.add_argument("--ssh-config", type=Path)
    parser.add_argument("--server-root", default="/workspace/freiburg-rag")
    q = parser.add_mutually_exclusive_group()
    q.add_argument("--text", help="Test mit einer getippten Frage")
    q.add_argument("--wav", type=Path, help="Test mit einer kurzen Aufnahme (mono, 16 kHz, PCM16)")
    parser.add_argument("--antwort-wav", type=Path, help="Optionale Ausgabedatei für einen Hörtest")
    parser.add_argument("--sekunden", type=int, choices=range(2, 13), default=7)
    args = parser.parse_args()
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", args.server_root):
        parser.error("Ungültiges Installationsverzeichnis.")
    if not all(re.fullmatch(r"(?:[a-zA-Z0-9][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9_.-]*", h)
               for h in (args.t4, args.robot) if h):
        parser.error("Bitte einen SSH-Alias oder Hostnamen angeben.")
    base = args.server_root
    command = "cd " + base + " && FREIBURG_ROOT=" + base + " run/fachbereiche-mcp/bin/python demo/fachbereiche/sprachfrage.py"
    if args.text:
        pcm = args.text.encode("utf8")
        command += " --text"
    elif args.wav:
        pcm = args.wav.read_bytes()
    elif args.robot:
        input("Eingabetaste drücken und anschließend die Frage an Reachy stellen. ")
        print("Aufnahme für", args.sekunden, "Sekunden.", flush=True)
        pcm = robot(args, "hoeren " + str(args.sekunden))
    else:
        parser.error("Für den ersten Test --text, --wav oder --robot angeben.")
    if not args.text:
        with wave.open(io.BytesIO(pcm), "rb") as source:
            if source.getsampwidth()!=2 or source.getframerate()!=16000 or source.getnchannels()!=1:
                raise ValueError("Die Aufnahme ist keine Mono-WAV mit 16 kHz und 16 Bit.")
    response = json.loads(ssh(args, args.t4, command, pcm))
    print("Frage:", response["frage"], "\nAntwort:", response["antwort"], flush=True)
    if response.get("quelle"):
        print("Quelle:", response["quelle"], flush=True)
    speech = response["antwort"] + (" Quelle: " + response["quelle"] + "." if response.get("quelle") else "")
    with tempfile.TemporaryDirectory() as directory:
        dest = args.antwort_wav.resolve() if args.antwort_wav else Path(directory) / "antwort.wav"
        wav = tts(speech, dest)
        if args.robot:
            robot(args, "sprechen", wav)


if __name__ == "__main__":
    main()
