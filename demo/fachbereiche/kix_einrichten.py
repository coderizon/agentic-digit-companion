"""Richtet die KIX-Testanbindung mit einem eigenen Agentenkonto und einem fiktiven Meldekontakt ein."""
import argparse
import json
import os
import secrets
from pathlib import Path

from kix_client import get, kix_session

ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])


def post(client, resource, key, data):
    response = client.post(resource, json={key: data})
    response.raise_for_status()
    return response.json()


def main(args):
    os.umask(0o077)
    state = ROOT / "run/fachbereiche"
    target = state / "kix.privat.json"
    pending = state / "kix-zugang.vorbereitet.json"
    admin = {**json.loads(args.admin_file.read_bytes()), "api": args.api}
    if target.is_file():
        config = json.loads(target.read_bytes())
        if config["api"] != args.api:
            raise ValueError("Eine andere KIX-Instanz ist bereits eingebunden.")
        with kix_session(config) as client:
            get(client, "tickets", limit=1)
        print("Bestehender KIX-Zugang geprüft.")
        return
    if not pending.exists():
        with pending.open("x", encoding="utf-8") as f:
            json.dump({"api": args.api, "login": "digit-itservice", "password": secrets.token_urlsafe(30) + "!7z"}, f)
    config = json.loads(pending.read_bytes())
    if config["api"] != args.api:
        raise ValueError("Der vorbereitete Zugang gehört zu einer anderen Instanz.")
    with kix_session(admin) as client:
        def entries(resource, key):
            return get(client, resource, limit=200).get(key) or []

        roles = entries("system/roles", "Role")
        picked = [r for r in roles if r["Name"] in args.role and r.get("ValidID", 1) == 1]
        if set(r["Name"] for r in picked) != set(args.role) or any("admin" in r["Name"].lower() or r["Name"] == "Superuser" for r in picked):
            raise ValueError("Die angegebenen Agentenrollen fehlen oder umfassen Administration.")
        known = [u for u in entries("system/users", "User") if u.get("UserLogin") == config["login"]]
        if not known:
            actor = post(client, "system/users", "User", {"UserLogin": config["login"], "UserPw": config["password"],
                         "IsAgent": 1, "IsCustomer": 0, "RoleIDs": [r["ID"] for r in picked], "ValidID": 1})
            user_id = actor["UserID"]
        else:
            with kix_session(config):
                pass
            user_id = known[0].get("UserID") or known[0]["ID"]
        def contact_id(email, first, last, user=None):
            rows = [c for c in entries("contacts", "Contact") if c.get("Email") == email]
            if rows:
                person = rows[0]
                if person["Firstname"] != first or person["Lastname"] != last or (user and person.get("AssignedUserID") != user):
                    raise ValueError("Der Demokontakt ist anderweitig belegt.")
                return person.get("ID") or person["ContactID"]
            body = {"Firstname": first, "Lastname": last, "Email": email, "ValidID": 1}
            if user:
                body["AssignedUserID"] = user
            return post(client, "contacts", "Contact", body)["ContactID"]
        contact_id("service@lindenau.invalid", "IT-Service", "Lindenau", user_id)
        config["contact_id"] = contact_id("meldungen@lindenau.invalid", "DIGIT Companion", "Demo-Meldestelle")
        for kind in ("queue", "type", "priority", "state"):
            config[kind + "_id"] = getattr(args, kind)
    with kix_session(config) as client:
        get(client, "tickets", limit=1)
    tmp = target.with_suffix(".new")
    tmp.write_text(json.dumps(config), encoding="utf8")
    tmp.replace(target)
    pending.unlink()
    print("KIX-Testzugang eingerichtet. Agentenrollen:", ", ".join(args.role))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--admin-file", required=True, type=Path)
    p.add_argument("--api", required=True)
    p.add_argument("--role", required=True, action="append")
    for key in ("queue", "type", "priority", "state"):
        p.add_argument("--" + key, required=True, type=int)
    main(p.parse_args())
