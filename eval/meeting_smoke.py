"""Check the synthetic German MP3 through Open WebUI with and without a collection grant."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "ops"))
from activate import private_file
from import_meeting import COLLECTION, DESCRIPTION
from setup_digitom_demo import call

QUESTION = "Was wurde in Meeting001 besprochen?"
SAMPLE_SHA = "a56c7c789b28d4c67d10fcb9c8304ea32f83e809560cae1fa7d8ad9f1b4612d9"
EXPECTED = (("00:01–00:06", "Spielplätzen", "Montag"), ("00:12–00:16", "Foto", "Standort", "Bauhof"),
            ("00:23–00:27", "Anna", "Freitag", "Anleitung"), ("00:34–00:36", "Kosten", "offen"))


def signin(url, path):
    client = requests.Session()
    client.trust_env = False
    info = call(client, "POST", url + "/api/v1/auths/signin", json=json.loads(private_file(path)))
    client.headers["Authorization"] = "Bearer " + info["token"]
    return client, info


def chat(client, url, model, question, *, history=(), selection=(), stream=False):
    body = {"model": model, "messages": [*history, {"role": "user", "content": question}], "stream": stream,
            "files": [{"type": "collection", "id": kb} for kb in selection]}
    with client.post(url + "/api/chat/completions", json=body, stream=stream, timeout=(8, 400)) as response:
        response.raise_for_status()
        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        chunks = []
        for line in response.iter_lines():
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                event = json.loads(line[6:])
                for choice in event.get("choices", []):
                    chunks.append(choice["delta"].get("content") or "")
        return "".join(chunks)


def exercise(args):
    url = f"http://127.0.0.1:{args.port}"
    api = url + "/api/v1"
    admin, _ = signin(url, args.admin_file)
    reader, user = signin(url, args.reader_file)
    if user["role"] != "user":
        raise ValueError("Für die Rechteprüfung ist ein normales Testkonto erforderlich.")
    fixture = json.loads(args.fixture.read_bytes())
    original = api + f"/files/{fixture['id']}/content"
    collection_path = api + "/knowledge/" + fixture["collection"]
    kb = call(admin, "GET", collection_path)
    if ((kb["name"], kb.get("description")) != (COLLECTION, DESCRIPTION)
            or hashlib.sha256(admin.get(original, timeout=15).content).hexdigest() != SAMPLE_SHA):
        raise ValueError("Die Rechteprüfung ist auf die mitgelieferte synthetische Beispielaufnahme beschränkt.")
    saved = kb.get("access_grants") or []
    other = [g for g in saved if not (g["principal_type"] == "user" and g["principal_id"] == user["id"])]
    grant = {"principal_type": "user", "principal_id": user["id"], "permission": "read"}
    cache = ROOT / ("private-meetings/" + fixture["id"] + ".json")
    results = []

    def grants(values):
        call(admin, "POST", collection_path + "/access/update", json={"access_grants": values})

    def check(label, ok, **details):
        results.append({"fall": label, "bestanden": bool(ok), **details})
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(label, bool(ok), flush=True)

    try:
        grants(other)
        hidden = chat(reader, url, args.model, QUESTION)
        missing = chat(reader, url, args.model, "Was wurde in Meeting987654321 besprochen?")
        check("Ohne Freigabe und unbekannte Nummer", hidden == missing and "lesen darfst" in hidden)
        check("Erratene Sammlungs-ID ohne Leserecht", hidden == chat(
            reader, url, args.model, QUESTION, selection=[fixture["collection"]]))
        check("Original ohne Freigabe gesperrt", reader.get(original, timeout=10).status_code in (401, 403, 404))
        grants(other + [grant])
        start = time.monotonic()
        answer = chat(reader, url, args.model, QUESTION, stream=True)
        elapsed = round(time.monotonic() - start, 3)
        check("Gestreamte Antwort mit vier sachlich und zeitlich richtigen Belegen",
              all(any(all(term in line for term in cue) for line in answer.splitlines()) for cue in EXPECTED)
              and f"/files/{fixture['id']}/content" in answer, sekunden=elapsed, antwort=answer)
        before = cache.stat().st_mtime_ns
        history = [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": answer}]
        detail = chat(reader, url, args.model, "Wer erstellt die Anleitung und bis wann?", history=history)
        check("Folgefrage zur Zuständigkeit und Frist",
              all(w in detail for w in ("Anna", "Freitag", "00:23–00:27")) and "00:12–00:16" not in detail,
              antwort=detail)
        check("Transkript für zweite Frage wiederverwendet", before == cache.stat().st_mtime_ns)
        unknown = chat(reader, url, args.model, "Welche Telefonnummer wurde in Meeting001 genannt?")
        check("Nicht besprochene Information offen gelassen", "keine belegte Aussage" in unknown)
        raw = reader.get(original, timeout=15)
        check("Verlinktes Original ist die Test-MP3", raw.ok and hashlib.sha256(raw.content).hexdigest() == SAMPLE_SHA)
        grants(other)
        check("Leserecht im selben Gespräch entzogen", chat(reader, url, args.model,
              "Wer erstellt die Anleitung?", history=history) == hidden)
        check("Original nach Entzug gesperrt", reader.get(original, timeout=10).status_code in (401, 403, 404))
        check("Öffentlicher Tabellenpfad", "330.150,00" in chat(
            admin, url, "freiburg-stadtdaten", "Mit welchen Einnahmen rechnet die VHS 2026?"))
    finally:
        grants(saved)
        admin.close()
        reader.close()
    return all(r["bestanden"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-file", type=Path, required=True)
    parser.add_argument("--reader-file", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="freiburg-stadtdaten")
    parser.add_argument("--port", type=int, default=8077)
    if not exercise(parser.parse_args()):
        raise SystemExit(1)
