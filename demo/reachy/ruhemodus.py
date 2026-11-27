"""Keep the conversation off during quiet mode. Recognize the wake phrase on the robot."""
import json
import select
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

from vosk import KaldiRecognizer, Model, SetLogLevel


APP = "reachy_mini_conversation_app"
MARKER = Path.home() / ".local/state/freibot/ruhe"
MODEL = Path.home() / ".local/share/freibot/ruhe/vosk-model-small-de-0.15"
# The German dictionary represents the workshop name as separate words.
WAKE = ("hallo frei bot", "hallo freibot", "hallo frei bord")
QUIET = ("sei mal ruhig", "sag mal ruhig", "sei bitte ruhig", "sei ruhig",
         "sei mal still", "sei bitte still", "sei still", "frei bot sei mal still",
         "frei boot sei mal still", "freibot sei mal still", "freiburg sei mal still",
         "freiburg ruhe", "frei bot ruhe", "frei boot ruhe", "frei bord ruhe", "freibot ruhe")


def api(path, post=False):
    req = Request("http://127.0.0.1:8000/api/apps/" + path, method="POST" if post else "GET")
    with urlopen(req, timeout=9) as response:
        return json.load(response)


def current():
    status = api("current-app-status") or {}
    return (status.get("info") or {}).get("name"), status.get("state")


def command_detector(model, commands):
    if commands == QUIET:
        # Free vocabulary keeps an unrelated sentence from being forced into a short command.
        recognizer = KaldiRecognizer(model, 16000)
    else:
        phrases = [w for w in commands if all(model.vosk_model_find_word(s) >= 0 for s in w.split())]
        if not phrases:
            raise RuntimeError("Dem Sprachmodell fehlen die Wörter für Hallo Freibot.")
        distractors = ["hallo", "hallo zusammen", "hallo freiburg", "hallo patrick", "freiburg ruhe", "[unk]"]
        recognizer = KaldiRecognizer(model, 16000, json.dumps(phrases + distractors))
    recognizer.SetWords(True)
    return recognizer


def detected(recognizer, data, commands):
    if not recognizer.AcceptWaveform(data):
        return False
    result = json.loads(recognizer.Result())
    words = result.get("result") or []
    if not words or result.get("text") not in commands:
        return False
    return all(w["conf"] >= (0.45 if w["word"] in ("bot", "boot") else 0.70) for w in words)


def listen(model, quiet):
    commands = WAKE if quiet else QUIET
    recognizer = command_detector(model, commands)
    cmd = ["arecord", "-q", "-D", "plug:reachymini_audio_src", "-t", "raw",
           "-f", "S16_LE", "-r", "16000", "-c", "1"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0) as mic:
        try:
            check_app_at = time.monotonic() + 3
            while MARKER.exists() == quiet:
                if not quiet and time.monotonic() >= check_app_at:
                    if current() != (APP, "running"):
                        break
                    check_app_at = time.monotonic() + 3
                if not select.select([mic.stdout], [], [], 2)[0]:
                    if mic.poll() is not None:
                        raise RuntimeError("Mikrofon wurde beendet.")
                    continue
                data = mic.stdout.read(4000)
                if not data:
                    raise RuntimeError("Mikrofon liefert keine Daten.")
                if detected(recognizer, data, commands):
                    if quiet:
                        print("Lokales Aufweckwort erkannt.", flush=True)
                    else:
                        MARKER.touch(mode=0o600)
                        print("Lokalen Ruhebefehl erkannt.", flush=True)
                    break
        finally:
            mic.terminate()
            mic.wait(timeout=5)


def resume():
    name, state = current()
    if name == APP and state == "running":
        return True
    if name and name != APP and state in ("running", "starting"):
        return False
    if state != "starting":
        api("start-app/" + APP + "/no-evict", True)
    for _ in range(20):
        time.sleep(0.5)
        if current() == (APP, "running"):
            return True
    return False


def main():
    SetLogLevel(-1)
    # Load before quiet can be requested, to catch missing data early.
    model = Model(str(MODEL))
    command_detector(model, WAKE)
    command_detector(model, QUIET)
    MARKER.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    print("Lokale Aufweckerkennung bereit.", flush=True)
    while True:
        if not MARKER.exists():
            if current() == (APP, "running"):
                listen(model, quiet=False)
            else:
                time.sleep(1)
            continue
        name, state = current()
        if name == APP and state in ("running", "starting"):
            api("stop-current-app", True)
        if name and state in ("running", "starting", "stopping"):
            time.sleep(0.4)
            continue
        print("Gespräch pausiert, Mikrofon lokal.", flush=True)
        listen(model, quiet=True)
        while not resume():
            time.sleep(2)
        MARKER.unlink(missing_ok=True)
        print("Gespräch wieder aktiv.", flush=True)


if __name__ == "__main__":
    main()
