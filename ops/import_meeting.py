"""Register an MP3 in a private Open WebUI knowledge collection on the Freiburg host."""
import argparse
import fcntl
import hashlib
import os
import re
import sys
from pathlib import Path
from uuid import UUID

from connect_webui import admin_session, install
from setup_digitom_demo import call


ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[1])
COLLECTION = "Meetings · Geschützte Aufnahmen"
DESCRIPTION = "Lokale Meetingaufnahmen für den Freiburger Stadtchat."


def meeting_collection(client, api, collection_id=None):
    if collection_id:
        kb = call(client, "GET", f"{api}/knowledge/{UUID(collection_id)}")
    else:
        matches = [kb for kb in call(client, "GET", api + "/knowledge/search", params={"query": COLLECTION})["items"]
                   if kb["name"] == COLLECTION]
        if matches and (len(matches) != 1 or matches[0].get("description") != DESCRIPTION):
            raise ValueError("Der Sammlungsname ist bereits vergeben. Bitte die gewünschte Sammlungs-ID angeben.")
        kb = matches[0] if matches else call(client, "POST", api + "/knowledge/create", json={
            "name": COLLECTION, "description": DESCRIPTION, "access_grants": []})
    if any(g.get("principal_type") == "user" and g.get("principal_id") == "*" for g in kb.get("access_grants") or []):
        raise ValueError("Die gewählte Sammlung ist für alle Konten freigegeben. Bitte eine geschützte Sammlung wählen.")
    return kb


def import_audio(client, url, source, name, collection_id=None):
    match = re.fullmatch(r"Meeting([0-9]{1,9})", name, re.I)
    if not match or source.suffix.lower() != ".mp3":
        raise ValueError("Eine MP3 und eine Kennung wie Meeting001 werden benötigt.")
    meeting = f"Meeting{int(match.group(1)):03d}"
    filename = meeting + ".mp3"
    api = url.rstrip("/") + "/api/v1"
    with source.open("rb") as audio, (ROOT / "run/meeting-import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        kb = meeting_collection(client, api, collection_id)
        path = f"{api}/knowledge/{kb['id']}"
        rows = call(client, "GET", path + "/files", params={"query": filename, "limit": 100})["items"]
        matches = [r for r in rows if r["filename"].lower() == filename.lower()]
        if len(matches) > 1:
            raise ValueError("Die Meetingnummer ist in dieser Sammlung mehrfach vergeben.")
        if matches:
            fid = matches[0]["id"]
            with client.get(f"{api}/files/{fid}/content", stream=True, timeout=(10, 240)) as existing:
                existing.raise_for_status()
                old = hashlib.sha256()
                for chunk in existing.iter_content(1024 * 1024):
                    old.update(chunk)
            if old.hexdigest() != hashlib.file_digest(audio, "sha256").hexdigest():
                raise ValueError("Unter dieser Nummer liegt eine andere Aufnahme. Bitte eine freie Nummer wählen.")
        else:
            file = call(client, "POST", api + "/files/?process=false", files={"file": (filename, audio, "audio/mpeg")})
            fid = file["id"]
            # WebUI needs indexable content to attach a file. Only the catalogue label is embedded.
            # The original bytes stay available via WebUI's authenticated file route.
            try:
                call(client, "POST", f"{api}/files/{fid}/data/content/update",
                     json={"content": f"Geschützte Originalaufnahme {meeting}.mp3."})
                call(client, "POST", path + "/file/add", json={"file_id": fid})
            except Exception:
                # A timed-out add can already have committed. Only undo our own orphan upload.
                linked = call(client, "GET", path + "/files", params={"query": filename, "limit": 100})["items"]
                if not any(row["id"] == fid for row in linked):
                    rollback = client.delete(f"{api}/files/{fid}", timeout=30)
                    rollback.raise_for_status()
                    raise
    return {"id": fid, "name": matches[0]["filename"] if matches else filename, "collection": kb["id"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--meeting", help="Standardmäßig wird der Dateiname ohne .mp3 verwendet.")
    parser.add_argument("--collection", help="Vorhandene Open-WebUI-Sammlungs-ID")
    parser.add_argument("--prepare", action="store_true", help="Transkript für lange Aufnahmen sofort vorbereiten.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FREIBURG_WEBUI_PORT", 8077)))
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args()
    args.url = f"http://127.0.0.1:{args.port}"
    with admin_session(args) as client:
        install(client, args.url)
        recording = import_audio(client, args.url, args.audio, args.meeting or args.audio.stem, args.collection)
    if args.prepare:
        sys.path.insert(0, str(ROOT / "app"))
        from meeting_chat import transcript
        transcript(ROOT, recording)
    print(f"{recording['name']} ist in der geschützten Sammlung {recording['collection']} verfügbar.")
