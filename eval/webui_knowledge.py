"""Exercise Open WebUI's real admin, file, collection and chat routes in an isolated test installation."""
import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from connect_webui import install


QUESTION = "Welche Abholkennung nennt das Prüfprotokoll für den Leseraum?"


def login(url, email, password, signup=False):
    session = requests.Session()
    data = {"email": email, "password": password}
    endpoint = "signup" if signup else "signin"
    if signup:
        data.update(name="Freiburg Integrationstest", password_confirm=password)
    result = session.post(url + "/api/v1/auths/" + endpoint, json=data, timeout=30)
    result.raise_for_status()
    user = result.json()
    session.headers["Authorization"] = "Bearer " + user["token"]
    return session, user


def post(session, url, **kwargs):
    response = session.post(url, timeout=120, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{url.split('/api/')[-1]}: HTTP {response.status_code}; "
                           + str(response.json().get("detail", ""))[:280])
    return response.json()


def add_collection(client, url, name, token, grants=()):
    kb = post(client, url + "/api/v1/knowledge/create",
              json={"name": name, "description": "Synthetisches Prüfprotokoll", "access_grants": list(grants)})
    filename = name.lower().replace(" ", "-") + ".txt"
    document = (f"Prüfprotokoll Leseraum\n\nFür den Leseraum lautet die Abholkennung {token}. "
                "Sie wird bei der Abholung am Lesetisch vorgezeigt. Diese Angabe gehört zum fiktiven Testbestand.")
    file = post(client, url + "/api/v1/files/?process=true&process_in_background=false",
                files={"file": (filename, document.encode(), "text/plain")})
    post(client, url + f"/api/v1/knowledge/{kb['id']}/file/add", json={"file_id": file["id"]})
    return {"name": name, "id": kb["id"], "file": file["id"], "code": token}


def run_chat(client, url, collections, question=QUESTION):
    attachments = [{"type": "collection", "id": kb["id"], "name": kb["name"]} for kb in collections]
    start = time.monotonic()
    reply = client.post(url + "/api/chat/completions",
                        json={"model": "freiburg-stadtdaten", "messages": [{"role": "user", "content": question}],
                              "files": attachments, "stream": False}, timeout=(8, 600))
    data = reply.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    if not reply.ok:
        text = str(data.get("detail", ""))[:260]
    return reply.status_code, text, round(time.monotonic() - start, 3)


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out)


def main(args):
    url = args.url.rstrip("/")
    state = json.loads(args.fixture.read_bytes()) if args.fixture.is_file() else {}
    if args.prepare:
        if not state:
            suffix = secrets.token_hex(4)
            state = {"domain": suffix + ".example.net", "password": secrets.token_urlsafe(26)}
            write_private(args.fixture, state)
        for role in ("admin", "reader"):
            email = role + "@" + state["domain"]
            try:
                client, user = login(url, email, state["password"])
            except requests.HTTPError:
                if role == "admin":
                    client, user = login(url, email, state["password"], signup=True)
                else:
                    owner, _ = login(url, "admin@" + state["domain"], state["password"])
                    post(owner, url + "/api/v1/auths/add", json={"name": "Freiburg Testleser", "email": email,
                                                                 "password": state["password"], "role": "user"})
                    client, user = login(url, email, state["password"])
            if (role == "admin" and user["role"] != "admin") or (role == "reader" and user["role"] != "user"):
                raise RuntimeError("Die Prüfung benötigt eine isolierte Installation mit Admin und normalem Leser.")
            state[role] = user["id"]
            write_private(args.fixture, state)
        admin, _ = login(url, "admin@" + state["domain"], state["password"])
        install(admin, url)
        grant = {"principal_type": "user", "principal_id": state["reader"], "permission": "read"}
        post(admin, url + "/api/v1/models/model/access/update", json={
            "id": "freiburg-stadtdaten", "name": "Freiburger Stadtdaten", "access_grants": [grant]})
        if "a" not in state:
            state["a"] = add_collection(admin, url, "Leseraum Nord", "KIESEL-731", [grant])
            write_private(args.fixture, state)
        if "b" not in state:
            state["b"] = add_collection(admin, url, "Leseraum Süd", "FICHTE-482")
            write_private(args.fixture, state)
        print("Zwei Sammlungen mit eigenen Dateien und getrennten Leserechten eingerichtet.", flush=True)
        if not args.output:
            return
    admin, _ = login(url, "admin@" + state["domain"], state["password"])
    reader, _ = login(url, "reader@" + state["domain"], state["password"])
    a, b = state["a"], state["b"]
    grant = {"principal_type": "user", "principal_id": state["reader"], "permission": "read"}
    uri = url + f"/api/v1/knowledge/{a['id']}/access/update"
    post(admin, uri, json={"access_grants": [grant]})
    results = []
    def check(name, client, kbs, question=QUESTION, expected=(), forbidden=(), denied=False):
        http, answer, seconds = run_chat(client, url, kbs, question)
        ok = ((http in (400, 401, 403, 404) and "Lesefreigabe" in answer) if denied
              else http == 200 and all(w in answer for w in expected))
        ok = bool(ok and all(w not in answer for w in forbidden))
        results.append({"fall": name, "http": http, "korrekt": ok, "sekunden": seconds, "antwort": answer})
        print(name, http, ok, seconds, flush=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check("Freigegebene Sammlung", reader, [a], expected=(a["code"], a["file"]), forbidden=(b["code"],))
    original = reader.get(url + f"/api/v1/files/{a['file']}/content", timeout=15)
    results.append({"fall": "Originaldatei mit Freigabe", "http": original.status_code,
                    "korrekt": original.ok and a["code"] in original.text})
    check("Andere Auswahl", admin, [b], expected=(b["code"], b["file"]), forbidden=(a["code"],))
    check("Mehrere Sammlungen", admin, [a, b],
          "Nenne die beiden Abholkennungen aus den Prüfprotokollen für den Leseraum Nord und Süd, mit ihren Quellen.",
          expected=(a["code"], b["code"], a["file"], b["file"]))
    check("Fehlende Lesefreigabe", reader, [b], forbidden=(a["code"], b["code"]), denied=True)
    post(admin, uri, json={"access_grants": []})
    check("Entzogene Lesefreigabe", reader, [a], forbidden=(a["code"], b["code"]), denied=True)
    original = reader.get(url + f"/api/v1/files/{a['file']}/content", timeout=15)
    results.append({"fall": "Originaldatei nach Entzug", "http": original.status_code,
                    "korrekt": original.status_code in (400, 401, 403, 404) and a["code"] not in original.text})
    check("Ohne Auswahl", admin, [], forbidden=(a["code"], b["code"]))
    check("Öffentliche Haushaltstabelle", admin, [], "Mit welchen Einnahmen rechnet die VHS 2026?",
          expected=("330.150,00", "content.csv"), forbidden=(a["code"], b["code"]))
    membership = url + f"/api/v1/knowledge/{b['id']}/file/"
    post(admin, membership + "remove?delete_file=false", json={"file_id": b["file"]})
    try:
        check("Datei aus Sammlung entfernt", admin, [b], expected=("keinen ausreichenden Beleg",),
              forbidden=(a["code"], b["code"], b["file"]))
    finally:
        post(admin, membership + "add", json={"file_id": b["file"]})
    if not all(x["korrekt"] for x in results):
        raise SystemExit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True, help="Dedicated Open WebUI test installation")
    p.add_argument("--fixture", required=True, type=Path, help="Private local test state file in a dedicated directory")
    p.add_argument("--prepare", action="store_true")
    p.add_argument("--output", type=Path)
    main(p.parse_args())
