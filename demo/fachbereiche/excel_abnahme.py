"""Abnahme des Excel-Downloads über die beiden echten WebUI-Übungskonten."""
import json
import os
import re
from io import BytesIO
from pathlib import Path

import requests
from openpyxl import load_workbook

from excel_export import FILENAME, MIME, note_rows, workbook_bytes
from fall import MODEL
from zugang import chat, signin


def main():
    root = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])
    folder = root / "run/fachbereiche"
    config = json.loads((folder / "quellen.json").read_bytes())
    url = config["url"]
    planning, planner = signin(url, folder / "planung.credentials.json")
    finance, _ = signin(url, folder / "finanzen.credentials.json")
    request = "Exportiere meinen Prüfvermerk als Excel."
    create = "Erstelle einen privaten Prüfvermerk für die Lageprüfung im Kleineschholz. Die Flurstücksnummer fehlt."
    created = chat(planning, url, MODEL, create)
    match = re.search(r"\[Excel-Liste herunterladen\]\((/api/v1/files/([a-f0-9-]{36})/content\?attachment=true)\)", created)
    note_link = re.search(r"/notes/([a-f0-9-]{36})", created)
    assert match and note_link, "Excel-Link oder Notizlink im Chat fehlt"
    download = url + match[1]
    response = planning.get(download, timeout=15)
    response.raise_for_status()
    assert response.headers["Content-Type"].split(";")[0] == MIME
    assert response.headers["Content-Disposition"].startswith("attachment;")
    file = planning.get(url + "/api/v1/files/" + match[2], timeout=15).json()
    assert file["user_id"] == planner["id"] and file["filename"] == FILENAME
    note = planning.get(url + "/api/v1/notes/" + note_link[1], timeout=15).json()
    wb = load_workbook(BytesIO(response.content))
    sheet = wb["Prüfvermerk"]
    rows = list(sheet.iter_rows(min_row=5, max_col=2, values_only=True))
    assert sheet["A2"].value == note["title"] and rows == note_rows(note)
    by_field = dict(rows)
    assert "Flurstücksnummer" in by_field["Offen"] and "Stadtplanung" in by_field["Nächster Schritt"]
    assert sum(field == "Quelle" for field, _ in rows) == 2
    print(f"OK  Chatlink, XLSX-Dateityp, persönlicher Eigentümer und {len(rows)} Zeilen mit beiden Quellen", flush=True)
    again = chat(planning, url, MODEL, request)
    assert match[1] in again and note_link[1] in again
    print("OK  Export des bestehenden Prüfvermerks verwendet denselben Download", flush=True)
    limited = chat(planning, url, MODEL, request, selection=[config["collections"]["gemeinsam"]])
    denied = chat(finance, url, MODEL, request)
    assert "wähle beide" in limited and "/files/" not in limited
    assert "keine Lesefreigabe" in denied and "/files/" not in denied
    assert finance.get(download, timeout=10).status_code in (401, 403, 404)
    assert requests.get(download, timeout=10).status_code in (401, 403, 404)
    print("OK  Sammlungsauswahl, Kämmereikonto und anonymer Direktdownload geprüft", flush=True)
    # Exercise an edited note without touching the demo user's saved work.
    edited = {"data": {"content": {"md": "Extra: =1+1\nOffen: wurde geklärt"}}}
    test = load_workbook(BytesIO(workbook_bytes("Geänderter Vermerk", note_rows(edited)))).active
    assert test["B5"].data_type == "s" and test["B5"].value == "=1+1" and test["B6"].value == "wurde geklärt"
    print("OK  Geänderte Notizwerte und Formelausdrücke als wörtlicher Text", flush=True)


if __name__ == "__main__":
    main()
