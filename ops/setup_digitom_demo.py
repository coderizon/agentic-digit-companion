"""Install the synthetic DIGITOM service catalogue and its workspace model."""
from pathlib import Path

from connect_webui import admin_options, admin_session, install


ROOT = Path(__file__).resolve().parents[1]
MODEL = "freiburg-digitom-bedarf"
NAME = "DIGITOM · Bedarfsassistent"
COLLECTION = "DIGITOM · Übungsdaten zum Bedarfsgespräch"
MARKER = "Vier fiktive Übungsunterlagen für den DIGITOM-Bedarfsassistenten (Coderizon, Version 1)."
START = "Unser Außendienst möchte kaputte Spielgeräte per Handy mit Foto und Standort melden. Gibt es dafür schon einen Service?"


def call(client, method, url, **options):
    response = client.request(method, url, timeout=(8, 150), **options)
    if not response.ok:
        raise RuntimeError(f"{method} {url.split('/api/')[-1]}: HTTP {response.status_code} "
                           + str(response.json().get("detail", ""))[:250])
    return response.json()


def setup(client, url):
    url = url.rstrip("/")
    install(client, url)
    api = url + "/api/v1"
    entries = call(client, "GET", api + "/knowledge/search", params={"query": COLLECTION})["items"]
    matching = [x for x in entries if x["name"] == COLLECTION]
    if matching and (len(matching) > 1 or matching[0].get("description") != MARKER):
        raise ValueError("Der Name der Übungssammlung ist bereits anderweitig belegt.")
    kb = matching[0] if matching else call(client, "POST", api + "/knowledge/create",
                  json={"name": COLLECTION, "description": MARKER, "access_grants": []})
    files_api = api + f"/knowledge/{kb['id']}"
    files = call(client, "GET", files_api + "/files", params={"limit": 100})["items"]
    by_name = {f["filename"]: f["id"] for f in files}
    for path in sorted((ROOT / "demo/digitom").glob("*.txt")):
        data = path.read_bytes()
        if path.name in by_name:
            original = client.get(api + f"/files/{by_name[path.name]}/content", timeout=20)
            original.raise_for_status()
            if original.content.replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
                raise ValueError(f"Die vorhandene Übungsdatei {path.name} wurde verändert. Bitte fachlich prüfen.")
            continue
        uploaded = call(client, "POST", api + "/files/?process=true&process_in_background=false",
                        files={"file": (path.name, data, "text/plain")})
        call(client, "POST", files_api + "/file/add", json={"file_id": uploaded["id"]})
        by_name[path.name] = uploaded["id"]
    if len(by_name) != 4:
        raise ValueError("Die Übungssammlung muss genau die vier mitgelieferten Unterlagen enthalten.")
    model_api = api + "/models"
    existing = client.get(model_api + "/model", params={"id": MODEL}, timeout=10)
    found = existing.json() if existing.ok else None
    if not existing.ok and existing.status_code not in (400, 401, 404):
        existing.raise_for_status()
    if found and found.get("meta", {}).get("freiburg_demo_collection_id") not in (None, kb["id"]):
        raise ValueError("Für diesen Assistenten wurde bereits eine andere Übungssammlung eingerichtet.")
    if not found or not (found.get("meta") or {}).get("freiburg_demo_collection_id"):
        meta = dict((found or {}).get("meta") or {})
        meta.update(freiburg_demo_collection_id=kb["id"])
        meta.setdefault("description", "Prüft einen Digitalbedarf, findet passende Services und entwirft einen Steckbrief.")
        meta.setdefault("suggestion_prompts", [{"title": ["Spielplatzschäden melden", "Passenden Service finden"], "content": START}])
        model = dict((found or {}), id=MODEL, name=(found or {}).get("name") or NAME, meta=meta,
                     params=(found or {}).get("params") or {})
        call(client, "POST", model_api + ("/model/update" if found else "/create"), json=model)
    advertised = call(client, "GET", url + "/api/models")
    if not any(m["id"] == MODEL for m in advertised["data"]):
        raise RuntimeError("Der Assistent fehlt in der Modellliste. Läuft das aktuelle Gateway?")
    return {"id": kb["id"], "name": COLLECTION, "files": by_name}


if __name__ == "__main__":
    args = admin_options(__doc__)
    with admin_session(args) as session:
        setup(session, args.url)
    print("DIGITOM · Bedarfsassistent ist mit vier fiktiven Quellen eingerichtet.")
