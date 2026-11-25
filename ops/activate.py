"""Provision the DIGITOM workspace and check a two-turn interview through its actual WebUI."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from digitom_chat import exercise


def private_file(path):
    stat = path.stat()
    if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError(f"{path} muss dir gehören und darf nur für dich lesbar sein (chmod 600).")
    return path.read_text(encoding="utf-8").strip()


def activate(args):
    url = f"http://127.0.0.1:{args.port}"
    with requests.Session() as client:
        client.trust_env = False
        config = client.get(url + "/api/config", timeout=12)
        config.raise_for_status()
        first = config.json().get("onboarding") is True
        if args.admin_token_file:
            token = private_file(args.admin_token_file)
        else:
            if args.admin_file:
                login = json.loads(private_file(args.admin_file))
                email, password = login["email"], login["password"]
            else:
                print("Erstes lokales Administratorkonto anlegen." if first else "Bestehendes Open-WebUI-Administratorkonto verwenden.")
                email = input("E-Mail: ").strip()
                password = getpass.getpass("Passwort: ")
                if first and password != getpass.getpass("Passwort bestätigen: "):
                    raise ValueError("Die beiden Eingaben unterscheiden sich.")
            if not email or not password:
                raise ValueError("E-Mail und Passwort werden benötigt.")
            body = {"email": email, "password": password}
            if first:
                body["name"] = "DIGITOM Administration"
            endpoint = "/api/v1/auths/" + ("signup" if first else "signin")
            auth = client.post(url + endpoint, json=body, timeout=20)
            if not auth.ok:
                raise ValueError(f"Open-WebUI-Anmeldung: HTTP {auth.status_code}. Konto und Passwortvorgaben prüfen.")
            token = auth.json()["token"]
        client.headers["Authorization"] = "Bearer " + token
        account = client.get(url + "/api/v1/auths/", timeout=15)
        account.raise_for_status()
        if account.json().get("role") != "admin":
            raise ValueError("Die Einrichtung benötigt ein Administratorkonto dieser Open WebUI.")
        if not exercise(client, url, args.output):
            raise ValueError(f"Mindestens eine Abnahmefrage ist fehlgeschlagen. Einzelheiten in {args.output}.")
        print(f"Bedarfsassistent, Folgefrage und Originalquellen geprüft. Ergebnis: {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--output", type=Path, required=True)
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument("--admin-file", type=Path)
    credentials.add_argument("--admin-token-file", type=Path)
    options = parser.parse_args()
    try:
        activate(options)
    except (KeyError, ValueError, OSError, RuntimeError, requests.RequestException) as error:
        raise SystemExit(str(error)) from None
