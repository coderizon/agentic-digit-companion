"""Ein zeitlich begrenzter Sprachtest mit Freibot und dem DIGIT-KIX-Testsystem."""
import argparse
import getpass
import io
import json
import os
import tempfile
import time
import uuid
import wave
import winsound
from pathlib import Path

import requests
import paramiko

from companion import ssh, tts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="reachy-mini.local")
    parser.add_argument("--t4", default="digit-t4-demo")
    parser.add_argument("--ssh-config", type=Path)
    parser.add_argument("--robot-known-hosts", type=Path, default=Path.home() / ".ssh" / "known_hosts_freibot")
    parser.add_argument("--akustiktest", action="store_true",
                        help="Spielt einen erfundenen Fall und dessen Testfreigabe auf dem Laptop ab.")
    args = parser.parse_args()
    chat = str(uuid.uuid4())
    python = "cd /workspace/freiburg-rag && "
    api = f"http://{args.host}:8000/api/"
    client = requests.Session()
    client.trust_env = False

    def get(route):
        r = client.get(api + route, timeout=6)
        r.raise_for_status()
        return r.json()

    def speak(text):
        print("Freibot:", text, flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            audio = tts(text, Path(tmp) / "meldung.wav")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            dauer = wav.getnframes() / wav.getframerate()
        name = "digit-ticket-" + uuid.uuid4().hex + ".wav"
        up = client.post(api + "media/sounds/upload", files={"file": (name, audio, "audio/wav")}, timeout=15)
        up.raise_for_status()
        try:
            result = client.post(api + "media/play_sound", json={"file": name}, timeout=20)
            result.raise_for_status()
            time.sleep(dauer + 0.4)
        finally:
            client.delete(api + "media/sounds/" + name, timeout=6).raise_for_status()

    def listen(spoken_sample, seconds):
        with tempfile.TemporaryDirectory() as tmp:
            sound = Path(tmp) / "probe.wav"
            if args.akustiktest:
                tts(spoken_sample, sound)
            else:
                input("Eingabetaste drücken und nach »Jetzt sprechen« zum Roboter sprechen. ")

            def ready():
                print("Jetzt sprechen.", flush=True)
                if args.akustiktest:
                    winsound.PlaySound(str(sound), winsound.SND_FILENAME | winsound.SND_ASYNC)

            # ALSA's plug device converts the robot's microphone array to 16 kHz mono.
            _, audio, err = robot.exec_command(
                f"arecord -q -D plug:reachymini_audio_src -t raw -f S16_LE -r 16000 -c 1 -d {seconds}",
                timeout=seconds + 8)
            first = audio.read(640)
            if len(first) != 640:
                raise RuntimeError("Freibots Mikrofon ist nicht bereit: " + err.read(300).decode(errors="replace"))
            ready()
            pcm = first + audio.read(32000 * seconds - len(first))
            if audio.channel.recv_exit_status() or len(pcm) != 32000 * seconds:
                raise RuntimeError("Unvollständige Mikrofonaufnahme.")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(pcm)
            wav = buf.getvalue()
        return ssh(args, args.t4, python + ".audio/bin/python demo/fachbereiche/sprachfrage.py --transkribieren",
                   wav).decode("utf-8").strip()

    def turn(text, previous=""):
        data = json.dumps({"text": text, "previous": previous, "session": chat}).encode()
        raw = ssh(args, args.t4, python + "run/fachbereiche-mcp/bin/python demo/fachbereiche/freibot_ticket.py", data)
        return json.loads(raw)

    status = get("daemon/status")
    if status.get("robot_name") != "Freibot" or status.get("simulation_enabled") or status.get("state") != "running":
        raise SystemExit("Der laufende echte Freibot wurde nicht gefunden.")
    lock = get("daemon/robot-app-lock-status")
    if lock.get("state") != "free":
        raise SystemExit("Freibot wird gerade verwendet. Bitte zuerst die aktive Robotersitzung beenden.")
    robot = paramiko.SSHClient()
    robot.load_system_host_keys()
    if args.robot_known_hosts.exists():
        robot.load_host_keys(str(args.robot_known_hosts))
    try:
        robot.connect(args.host, username="pollen", password=os.environ.pop("FREIBOT_SSH_PASSWORD", None), timeout=5)
    except paramiko.SSHException as exc:
        if not isinstance(exc, paramiko.AuthenticationException) and str(exc) != "No authentication methods available":
            raise
        robot.connect(args.host, username="pollen", password=getpass.getpass("SSH-Passwort für Freibot: "),
                      allow_agent=False, look_for_keys=False, timeout=5)
    try:
        gespraech(args, listen, turn, speak)
    finally:
        robot.close()
        client.close()


def gespraech(args, listen, turn, speak):
    if not args.akustiktest:
        print("DIGIT Companion · Ticket an Freibot melden\n"
              "Bitte nur einen erfundenen IT-Testfall verwenden. Du hast zwölf Sekunden für die Meldung.\n"
              "Freibot liest sie dir vor. Erst nach deiner Freigabe wird ein Ticket in Test-KIX angelegt.\n",
              flush=True)
    sample = "Der Testdrucker druckt blaue Streifen. Auch nach dem Neustart."
    text = listen(sample, 8 if args.akustiktest else 12)
    print("Erkannte Meldung:", text, flush=True)
    if not text or (args.akustiktest and not all(k in text.casefold() for k in ("druck", "streifen"))):
        raise SystemExit("Testmeldung nicht eindeutig verstanden. Es wurde kein Ticket gesendet.")
    draft = turn(text)
    speak(draft["speech"])
    if draft["status"] != "entwurf":
        return
    print("Entwurf:", draft["ref"], flush=True)
    # Keep exactly this reference for all retries. A vague utterance cannot submit a draft.
    for _ in range(2):
        consent = listen("Ja, bitte das Ticket erstellen.", 5)
        print("Erkannte Entscheidung:", consent, flush=True)
        if not consent:
            continue
        result = turn(consent, draft["reply"])
        speak(result["speech"])
        if result["status"] != "unverstanden":
            if result.get("number"):
                print("KIX:", result["number"], result["ref"], flush=True)
            return
    print("Keine eindeutige Freigabe. Der Entwurf wurde nicht gesendet.", flush=True)


if __name__ == "__main__":
    main()
