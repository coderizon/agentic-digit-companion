"""Concurrent two-turn DIGITOM interviews with distinct regular Open WebUI accounts."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from setup_digitom_demo import COLLECTION, MODEL, START

GOAL = "Sichtung innerhalb von zwei Arbeitstagen"


def client(token):
    s = requests.Session()
    s.trust_env = False
    s.headers["Authorization"] = "Bearer " + token
    return s


def user_id(url, token):
    with client(token) as s:
        res = s.get(url + "/api/v1/auths/", timeout=20)
        res.raise_for_status()
        user = res.json()
        if user.get("role") != "user":
            raise ValueError("Für diesen Benchmark reguläre Testbenutzer verwenden.")
        return user["id"]


def source_ids(url, token):
    with client(token) as s:
        api = url + "/api/v1/knowledge"
        hits = s.get(api + "/search", params={"query": COLLECTION}, timeout=20)
        hits.raise_for_status()
        collection, = [k for k in hits.json()["items"] if k["name"] == COLLECTION]
        files = s.get(api + "/" + collection["id"] + "/files", params={"limit": 100}, timeout=20)
        files.raise_for_status()
        return {f["filename"]: f["id"] for f in files.json()["items"]}


def chat(session, url, messages, timeout):
    started = time.monotonic()
    try:
        response = session.post(url + "/api/chat/completions", json={
            "model": MODEL, "messages": messages, "stream": False}, timeout=(8, timeout))
        answer = (response.json()["choices"][0]["message"]["content"] if response.ok else "")
        return response.status_code, answer, round(time.monotonic() - started, 3)
    except (requests.RequestException, ValueError, IndexError, KeyError) as exc:
        return type(exc).__name__, "", round(time.monotonic() - started, 3)


def run(args):
    url = f"http://127.0.0.1:{args.port}"
    if args.accounts_file.stat().st_mode & 0o077:
        raise ValueError("Die Token-Datei muss Modus 600 haben.")
    tokens = json.loads(args.accounts_file.read_bytes())
    if not isinstance(tokens, list) or len(tokens) < args.parallel or not all(isinstance(t, str) and t for t in tokens):
        raise ValueError("Die Datei muss genügend gültige Open-WebUI-Testbenutzertokens als JSON-Liste enthalten.")
    tokens = tokens[:args.parallel]
    with ThreadPoolExecutor(max_workers=4) as preflight:
        users = list(preflight.map(lambda token: user_id(url, token), tokens))
    if len(set(users)) != args.parallel:
        raise ValueError("Für jede parallele Person ist ein eigenes Testkonto erforderlich.")
    sources = source_ids(url, tokens[0])
    gate = threading.Barrier(args.parallel + 1, timeout=60)

    def interview(pair):
        index, token = pair
        recipient = f"Bauhof Bezirk {index:03}"
        messages = [{"role": "user", "content": START}]
        with client(token) as s:
            gate.wait()
            started = time.monotonic()
            http1, first, t1 = chat(s, url, messages, args.timeout)
            first_ok = http1 == 200 and all(x in first for x in ("Mobile Mängelmeldung",
                "Serviceteam Außendienst und Meldungen", sources["01-maengelmeldungen.txt"], sources["03-vergleichsbedarf.txt"]))
            http2, t2, profile_ok = "ausgelassen", None, False
            if http1 == 200 and first:
                messages.extend([{"role": "assistant", "content": first}, {"role": "user", "content":
                    f"Empfänger: {recipient}. Erfolgskriterium: {GOAL}. Bitte erstelle den Steckbrief."}])
                http2, profile, t2 = chat(s, url, messages, args.timeout)
                profile_ok = http2 == 200 and all(x in profile for x in (
                    f"**Empfänger:** {recipient}", f"**Erfolgskriterium:** {GOAL}",
                    "Entwurf zur fachlichen Prüfung", sources["04-steckbrief.txt"]))
                profile_ok = profile_ok and profile.count("**Empfänger:**") == 1
            return dict(n=index, http1=http1, http2=http2, service_belegt=first_ok, steckbrief_belegt=profile_ok,
                        t1_s=t1, t2_s=t2, dialog_s=round(time.monotonic() - started, 3))
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        jobs = [pool.submit(interview, item) for item in enumerate(tokens, 1)]
        start = time.monotonic()
        gate.wait()
        rows = [future.result() for future in jobs]
    duration = round(time.monotonic() - start, 3)
    correct = sorted(row["dialog_s"] for row in rows if row["service_belegt"] and row["steckbrief_belegt"])
    report = dict(zeit_utc=datetime.now(timezone.utc).isoformat(), benutzerkonten=len(set(users)),
        daten="synthetischer DIGITOM-Katalog", schnittstelle="Open WebUI /api/chat/completions, lokal",
        turns_pro_person=2, lauf_s=duration, dialoge_korrekt=len(correct),
        p50_korrekte_dialoge_s=correct[math.ceil(.50 * len(correct)) - 1] if correct else None,
        p95_korrekte_dialoge_s=correct[math.ceil(.95 * len(correct)) - 1] if correct else None,
        status_erste_nachricht=dict(Counter(str(r["http1"]) for r in rows)),
        status_folgeantwort=dict(Counter(str(r["http2"]) for r in rows)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(report, dialoge=rows), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return len(correct) == len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8077, help="Port einer lokalen Test-WebUI")
    parser.add_argument("--accounts-file", type=Path, required=True, help="Private JSON-Liste mit Sitzungstokens regulärer Testkonten")
    parser.add_argument("--parallel", type=int, required=True, choices=range(1, 201), metavar="1..200")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(0 if run(parser.parse_args()) else 1)
