"""Install or update the Freiburg knowledge filter using Open WebUI's admin API."""
import argparse
import getpass
from pathlib import Path
from urllib.parse import urlsplit

import requests


FILTER_ID = "freiburg_wissenssammlungen"


def install(session, url):
    api = url.rstrip("/") + "/api/v1/functions"
    source = Path(__file__).with_name("webui_knowledge_filter.py").read_text(encoding="utf-8")
    payload = {"id": FILTER_ID, "name": "Freiburger Wissenssammlungen", "content": source,
               "meta": {"description": "Zugriff auf im Freiburger Stadtchat ausgewählte Wissenssammlungen"}}
    existing = session.get(api + "/list", timeout=15)
    existing.raise_for_status()
    item = next((f for f in existing.json() if f["id"] == FILTER_ID), None)
    if item:
        read = session.get(f"{api}/id/{FILTER_ID}", timeout=15)
        read.raise_for_status()
        item = read.json()
    if not item or item["content"].strip() != source.strip():
        endpoint = f"{api}/id/{FILTER_ID}/update" if item else api + "/create"
        saved = session.post(endpoint, json=payload, timeout=45)
        if not saved.ok:
            raise RuntimeError(f"WebUI-Filter konnte nicht gespeichert werden (HTTP {saved.status_code}): "
                               + str(saved.json().get("detail", ""))[:250])
    for prop, suffix in (("is_active", "toggle"), ("is_global", "toggle/global")):
        current = session.get(f"{api}/id/{FILTER_ID}", timeout=15)
        current.raise_for_status()
        if not current.json().get(prop):
            changed = session.post(f"{api}/id/{FILTER_ID}/{suffix}", timeout=20)
            changed.raise_for_status()
    print("Die Verbindung für ausgewählte Freiburger Wissenssammlungen ist aktiv.")


def admin_options(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--url", default="http://127.0.0.1:8077")
    p.add_argument("--token-file", type=Path, help="Optional file containing the WebUI administrator session token")
    args = p.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme != "https" and (parsed.scheme != "http" or parsed.hostname not in {"localhost","127.0.0.1","::1"}):
        p.error("Für eine entfernte WebUI ist HTTPS erforderlich.")
    return args


def admin_session(args):
    client = requests.Session()
    if args.token_file:
        token = args.token_file.read_text().strip()
    else:
        email = input("Open-WebUI-Admin-E-Mail: ").strip()
        password = getpass.getpass("Open-WebUI-Passwort: ")
        login = client.post(args.url.rstrip("/") + "/api/v1/auths/signin",
                            json={"email": email, "password": password}, timeout=15)
        login.raise_for_status()
        token = login.json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    return client


if __name__ == "__main__":
    args = admin_options(__doc__)
    with admin_session(args) as client:
        install(client, args.url)
