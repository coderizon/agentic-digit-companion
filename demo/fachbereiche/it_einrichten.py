"""Bindet das fiktive RaumPlan-Handbuch in die vorhandene, geschützte Fachbereichsdemo ein."""
import argparse
import fcntl
import json
import os
from pathlib import Path

from fall import MARKER, PIPE
from zugang import ROOT, call, signin

HERE = Path(__file__).resolve().parent
NAME = "IT-Service · Handbuch RaumPlan"
DESCRIPTION = "Fiktive IT-Hilfe der Übungsstadt Lindenau · DIGIT Companion v1"


def setup(admin_file, portal):
    state = ROOT / "run/fachbereiche"
    sources = json.loads((state / "quellen.json").read_bytes())
    admin, _ = signin(sources["url"], admin_file)
    api = sources["url"] + "/api/v1"

    def req(method, path, **kw):
        return call(admin, method, api + path, **kw)

    grants = [{"principal_type": "group", "principal_id": sources["groups"][group], "permission": "read"}
              for group in ("planung", "finanzen")]
    rows = [k for k in req("GET", "/knowledge/search", params={"query": NAME})["items"] if k["name"] == NAME]
    if len(rows) > 1 or rows and rows[0]["description"] != DESCRIPTION:
        raise ValueError("Der Sammlungsname ist anderweitig vergeben.")
    collection = rows[0] if rows else req("POST", "/knowledge/create", json={
        "name": NAME, "description": DESCRIPTION, "access_grants": grants})
    prefix = "/knowledge/" + collection["id"]
    req("POST", prefix + "/access/update", json={"access_grants": grants})
    handbook = HERE / "IT-Handbuch RaumPlan.md"
    files = [f for f in req("GET", prefix + "/files", params={"limit": 100})["items"] if f["filename"] == handbook.name]
    if len(files) > 1:
        raise ValueError("Mehrere Handbuchfassungen in der Sammlung.")
    if files:
        file_id = files[0]["id"]
    else:
        uploaded = req("POST", "/files/?process=true&process_in_background=false",
                       files={"file": (handbook.name, handbook.read_bytes(), "text/markdown")})
        file_id = uploaded["id"]
        req("POST", prefix + "/file/add", json={"file_id": file_id})
    content = admin.get(api + "/files/" + file_id + "/content", timeout=25)
    content.raise_for_status()
    if content.content != handbook.read_bytes():
        raise ValueError("Die eingelesene Fassung weicht vom mitgebrachten Übungshandbuch ab.")
    fn = req("GET", "/functions/id/" + PIPE)
    if (fn.get("meta") or {}).get("description") != MARKER or not fn.get("is_active"):
        raise ValueError("Die aktive Fachbereichsfunktion fehlt.")
    body = {key: fn[key] for key in ("id", "name", "meta")}
    body["content"] = (HERE / "chat_pipe.py").read_text(encoding="utf8")
    req("POST", "/functions/id/" + PIPE + "/update", json=body)
    enabled = req("GET", "/functions/id/" + PIPE)
    if not enabled.get("is_active") or enabled["content"] != body["content"]:
        raise ValueError("Funktion wurde nicht vollständig übernommen.")
    conf = {"url": sources["url"], "name": "IT-Handbuch RaumPlan", "file": file_id,
            "collection": collection["id"], "portal": portal.rstrip("/")}
    dest = state / "it-handbuch.json"
    tmp = dest.with_suffix(".new")
    tmp.write_text(json.dumps(conf, ensure_ascii=False), encoding="utf8")
    tmp.replace(dest)
    print("Handbuch indexiert und für beide Demo-Fachgruppen freigegeben. DIGIT-Funktion aktiv.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--admin-file", required=True, type=Path)
    p.add_argument("--portal", required=True, help="KIX-Adresse aus Sicht des Demo-Browsers")
    args = p.parse_args()
    os.umask(0o077)
    with (ROOT / "run/fachbereiche-setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        setup(args.admin_file, args.portal)
