"""Anmeldung und Chat-Aufruf für die Fachbereichsdemo über die normale WebUI-API."""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "ops"))
from activate import private_file
from setup_digitom_demo import call


def signin(url, credentials):
    client = requests.Session()
    client.trust_env = False
    actor = call(client, "POST", url + "/api/v1/auths/signin", json=json.loads(private_file(credentials)))
    client.headers["Authorization"] = "Bearer " + actor["token"]
    return client, actor


def chat(client, url, model, question, *, selection=()):
    response = client.post(url + "/api/chat/completions", timeout=(8, 350), json={
        "model": model, "stream": False, "messages": [{"role": "user", "content": question}],
        "files": [{"type": "collection", "id": ident} for ident in selection]})
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
