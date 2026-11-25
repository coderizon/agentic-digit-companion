"""Wait for the four local processes and their actual application readiness."""
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

root = Path(sys.argv[1])
ctl = [str(root / ".venv/bin/supervisorctl"), "-c", str(root / "ops/supervisord.conf"), "status"]
url = "http://127.0.0.1:" + os.environ.get("FREIBURG_GATEWAY_PORT", "8090")
ui = "http://127.0.0.1:" + os.environ.get("FREIBURG_WEBUI_PORT", "8077")
headers = {"Authorization": "Bearer " + (root / "app/chat.key").read_text().strip()}
timeout = int(os.environ.get("FREIBURG_START_TIMEOUT", "1200"))
if timeout <= 0:
    raise SystemExit("FREIBURG_START_TIMEOUT muss eine positive Anzahl Sekunden sein.")
deadline = time.monotonic() + timeout
last = ""
with requests.Session() as client:
    client.trust_env = False
    while time.monotonic() < deadline:
        status = subprocess.run(ctl, capture_output=True, text=True, timeout=12).stdout.strip()
        states = {fields[0]: fields[1] for line in status.splitlines() if len(fields := line.split()) > 1}
        if "FATAL" in states.values():
            break
        try:
            if all(states.get(k) == "RUNNING" for k in ("reader", "search", "gateway", "webui")):
                health = client.get(url + "/health", timeout=(2, 8))
                health.raise_for_status()
                ui_config = client.get(ui + "/api/config", timeout=(2, 8))
                ui_config.raise_for_status()
                models = client.get(url + "/v1/models", headers=headers, timeout=(2, 8))
                models.raise_for_status()
                ids = {model["id"] for model in models.json()["data"]}
                if health.json().get("topk") and health.json().get("leser") and "freiburg-digitom-bedarf" in ids:
                    print("Alle vier Dienste bereit. Suchindex, lokales Modell und Anmeldung erreichbar.")
                    break
        except (requests.RequestException, ValueError, KeyError) as error:
            last = str(error)
        time.sleep(4)
    else:
        raise SystemExit(f"Startzeit überschritten. {last}\n{status}\nProtokolle: {root}/logs")
if "FATAL" in states.values():
    raise SystemExit(f"Ein Dienst konnte nicht starten.\n{status}\nProtokolle: {root}/logs")
