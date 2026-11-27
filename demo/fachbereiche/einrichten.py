"""Gruppen, Übungsquellen und den optionalen Open-WebUI-Chat einrichten."""
import argparse
import fcntl
import hashlib
import json
import os
import secrets
from pathlib import Path

import requests

from fall import COLLECTIONS, MARKER, MODEL, PEOPLE, PIPE, PLAN_IMAGE, originals
from zugang import signin, call


HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("FREIBURG_ROOT") or HERE.parents[1])


def setup(args):
    state = ROOT / "run/fachbereiche"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    url = "http://127.0.0.1:" + str(args.port)
    api = url + "/api/v1"
    admin, _ = signin(url, args.admin_file)
    do = lambda method, path, **kw: call(admin, method, api + path, **kw)
    groups = {g["name"]: g for g in do("GET", "/groups/")}
    info = {"url": url, "collections": {}, "groups": {}, "files": {}}
    public_path = "/static/freiburg/" + PLAN_IMAGE + ".png"
    preview = admin.get(url + public_path, timeout=15)
    if preview.ok and hashlib.sha256(preview.content).hexdigest() == PLAN_IMAGE:
        info["planbild"] = public_path
    users = {}
    for role, (name, area, localpart) in PEOPLE.items():
        group_name = area + " · Vorführung"
        group = groups.get(group_name)
        if group and group.get("description") != MARKER:
            raise ValueError("Gruppenname wird bereits verwendet: " + group_name)
        if not group:
            group = do("POST", "/groups/create", json={"name": group_name, "description": MARKER,
                       "permissions": {"features": {"notes": True}, "workspace": {"knowledge": True}}})
        info["groups"][role] = group["id"]
        path = state / (role + ".credentials.json")
        if not path.exists():
            with path.open("x") as fh:
                json.dump({"email": localpart + "." + secrets.token_hex(3) + "@demo.example.org",
                           "password": secrets.token_urlsafe(24) + "!a7"}, fh)
        creds = json.loads(path.read_bytes())
        with requests.Session() as anon:
            probe = anon.post(api + "/auths/signin", json=creds, timeout=15)
        if probe.status_code in {400, 401, 403}:
            do("POST", "/auths/add", json=dict(creds, name=name, role="user"))
        elif not probe.ok:
            probe.raise_for_status()
        session, actor = signin(url, path)
        if actor["role"] != "user" or actor["name"] != name:
            raise ValueError("Unerwartetes Demokonto.")
        users[role] = session
        do("POST", "/groups/id/" + group["id"] + "/users/add", json={"user_ids": [actor["id"]]})

    def grant(roles):
        return [{"principal_type": "group", "principal_id": info["groups"][role], "permission": "read"} for role in roles]

    for role, docs in originals(ROOT).items():
        title = COLLECTIONS[role]
        entries = do("GET", "/knowledge/search", params={"query": title})["items"]
        matches = [x for x in entries if x["name"] == title]
        if len(matches) > 1 or matches and matches[0].get("description") != MARKER:
            raise ValueError("Sammlungsname ist bereits belegt: " + title)
        grants = grant(PEOPLE if role == "gemeinsam" else [role])
        kb = matches[0] if matches else do("POST", "/knowledge/create", json={
                "name": title, "description": MARKER, "access_grants": grants})
        prefix = "/knowledge/" + kb["id"]
        do("POST", prefix + "/access/update", json={"access_grants": grants})
        info["collections"][role] = kb["id"]
        files = {f["filename"]: f["id"] for f in do("GET", prefix + "/files", params={"limit": 100})["items"]}
        for filename, data, mime in docs:
            if filename in files:
                response = admin.get(api + "/files/" + files[filename] + "/content", timeout=30)
                response.raise_for_status()
                if hashlib.sha256(response.content).digest() != hashlib.sha256(data).digest():
                    raise ValueError("Abweichende Originaldatei: " + filename)
            else:
                uploaded = do("POST", "/files/?process=true&process_in_background=false",
                              files={"file": (filename, data, mime)})
                do("POST", prefix + "/file/add", json={"file_id": uploaded["id"]})
                files[filename] = uploaded["id"]
            info["files"][filename] = files[filename]
        print(title, len(docs), flush=True)
    (state / "quellen.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    if args.sources_only:
        return

    fn = {"id": PIPE, "name": "DIGIT Fachbereichswerkzeuge", "content": (HERE / "chat_pipe.py").read_text(),
          "meta": {"description": MARKER}}
    current = next((f for f in do("GET", "/functions/list") if f["id"] == PIPE), None)
    if current and (current.get("meta") or {}).get("description") != MARKER:
        raise ValueError("Die Function-ID wird bereits verwendet.")
    do("POST", "/functions/id/" + PIPE + "/update" if current else "/functions/create", json=fn)
    if not do("GET", "/functions/id/" + PIPE)["is_active"]:
        do("POST", "/functions/id/" + PIPE + "/toggle")

    def model(identifier):
        res = admin.get(api + "/models/model", params={"id": identifier}, timeout=15)
        if res.ok:
            return res.json()
        if res.status_code in {400, 401, 403, 404}:
            return None
        res.raise_for_status()

    base = model("freiburg-stadtdaten")
    items = [{k: g[k] for k in ("principal_type", "principal_id", "permission")} for g in base["access_grants"]]
    items.extend(g for g in grant(PEOPLE) if g not in items)
    do("POST", "/models/model/access/update", json={"id": base["id"], "name": base["name"], "access_grants": items})
    fn_base = model(PIPE)
    if fn_base and (fn_base.get("meta") or {}).get("fachdemo") != MARKER:
        raise ValueError("Die Basis-Modell-ID ist bereits vergeben.")
    if not fn_base:
        do("POST", "/models/create", json={"id": PIPE, "name": "DIGIT Fachbereichswerkzeuge",
           "base_model_id": None, "params": {}, "meta": {"fachdemo": MARKER}, "access_grants": grant(PEOPLE)})
    do("POST", "/models/model/access/update", json={"id": PIPE, "name": "DIGIT Fachbereichswerkzeuge",
                                                     "access_grants": grant(PEOPLE)})
    named = model(MODEL)
    if named and (named.get("meta") or {}).get("fachdemo") != MARKER:
        raise ValueError("Die Modell-ID ist bereits vergeben.")
    icon = ((model("freiburg-meetings") or {}).get("meta") or {}).get("profile_image_url")
    body = {"id": MODEL, "name": "DIGIT Companion · Fachbereiche", "base_model_id": PIPE,
            "params": {}, "is_active": True, "access_grants": grant(PEOPLE),
            "meta": {"fachdemo": MARKER, "profile_image_url": icon,
                     "description": "Plan und Ratsvorlage zum Quartier Kleineschholz, interne Unterlagen nach Fachbereich.",
                     "suggestion_prompts": [{"title": ["Meine Wissensquellen", "Freigegebene Sammlungen ansehen"],
                                             "content": "Welche Wissensquellen kann ich hier nutzen?"}]}}
    do("POST", "/models/model/update" if named else "/models/create", json=body)
    do("POST", "/models/model/access/update", json={"id": MODEL, "name": body["name"], "access_grants": grant(PEOPLE)})
    for session in users.values():
        current = call(session, "GET", api + "/users/user/settings", params={"raw": True}) or {}
        ui = {**(current.get("ui") or {}), "models": [MODEL], "preferredLanguage": "de-DE"}
        call(session, "POST", api + "/users/user/settings/update", json={**current, "ui": ui})
    print("Beide Fachbereiche eingerichtet.", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--admin-file", required=True, type=Path)
    p.add_argument("--port", type=int, default=8077)
    p.add_argument("--sources-only", action="store_true")
    args = p.parse_args()
    os.umask(0o077)
    with (ROOT / "run/fachbereiche-setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        setup(args)
