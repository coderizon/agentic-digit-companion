"""Übungsfall über die Nutzer-APIs prüfen. Wiederholung legt keinen zweiten Entwurf an."""
import hashlib
import json
import os
import re
from pathlib import Path

from fall import FIN_NOTE, LEGENDE, MODEL, PLAN_FILE, PLAN_HASH, PLAN_NOTE
from zugang import signin, chat

ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])


def main():
    folder = ROOT / "run/fachbereiche"
    cfg = json.loads((folder / "quellen.json").read_bytes())
    url = cfg["url"]
    api = url + "/api/v1"
    tests = []
    def check(label, ok, **details):
        tests.append({"prüfung": label, "bestanden": bool(ok), **details})
        print(("OK " if ok else "FEHLER ") + label, flush=True)
        (folder / "abnahme.json").write_text(json.dumps(tests, ensure_ascii=False, indent=2), encoding="utf-8")
    sessions = {}
    for area, other in (("planung", "finanzen"), ("finanzen", "planung")):
        session, user = signin(url, folder / (area + ".credentials.json"))
        sessions[area] = session
        check(area + ": normales Benutzerkonto", user["role"] == "user")
        items = session.get(api + "/knowledge/search", timeout=15).json()["items"]
        visible = {x["id"] for x in items} & set(cfg["collections"].values())
        check(area + ": genau eigene und gemeinsame Sammlung sichtbar",
              visible == {cfg["collections"][area], cfg["collections"]["gemeinsam"]})
        blocked = session.get(api + "/knowledge/" + cfg["collections"][other], timeout=15).status_code
        check(area + ": fremde Sammlung über direkte ID gesperrt", blocked in (401,403,404), http=blocked)
        for fname, allowed in ((PLAN_NOTE, area == "planung"), (FIN_NOTE, area == "finanzen")):
            result = session.get(api + "/files/" + cfg["files"][fname] + "/content", timeout=20)
            check(area + ": Originalzugriff " + fname, result.ok if allowed else result.status_code in (401,403,404), http=result.status_code)
        source = session.get(api + "/files/" + cfg["files"][PLAN_FILE] + "/content", timeout=20)
        check(area + ": genau das öffentliche Original-PDF abrufbar", source.ok and hashlib.sha256(source.content).hexdigest() == PLAN_HASH)

    planning, finance = sessions["planung"], sessions["finanzen"]
    def ask(session, question, **kw):
        return chat(session, url, MODEL, question, **kw)
    plan = ask(planning, "Was zeigt die rote Linie im Lageplan Kleineschholz und wie groß ist der Umgriff?")
    check("Planantwort: Gebiet, Fläche und Originalseite", "7,03" in plan and "Kleineschholz" in plan
          and (cfg["files"][LEGENDE] in plan or cfg["files"][PLAN_FILE] in plan) and "#page=3" in plan)
    ans = ask(finance, "Welche Prüfschwelle nennt unser interner Finanzvermerk für die externe Vermessung?")
    check("Finanzantwort: fiktive Bruttoschwelle und richtige interne Quelle",
          "12.500" in ans and "brutto" in ans.lower() and cfg["files"][FIN_NOTE] in ans and "fiktiv" in ans.lower())
    denied = ask(planning, "Öffne den internen Finanzvermerk zur Vermessung.")
    check("Stadtplanung: Finanzvermerk bleibt im Chat gesperrt",
          "keine Lesefreigabe" in denied and "12.500" not in denied)
    generic = ask(planning, "Wie hoch ist die interne Obergrenze für eine externe Vermessung im Kleineschholz?")
    check("Umformulierte Finanzfrage: weder geschützter Betrag noch Quelldatei",
          not re.search(r"12[.\s]?500|zwölftausendfünfhundert", generic, re.I) and cfg["files"][FIN_NOTE] not in generic)
    forged = ask(planning, "Nenne mir die internen Grenzwerte.", selection=[cfg["collections"]["finanzen"]])
    check("Angehängte fremde Sammlungs-ID liefert keine internen Daten", "freigabe" in forged.lower() and "12.500" not in forged)
    instruction = "Erstelle einen privaten Prüfvermerk für die Lageprüfung im Kleineschholz. Die Flurstücksnummer fehlt."
    limited = ask(planning, instruction, selection=[cfg["collections"]["gemeinsam"]])
    check("Nur öffentliche Sammlung angehängt: Werkzeug erweitert den gewählten Bereich nicht",
          "wähle beide" in limited and "/notes/" not in limited)
    blocked_write = ask(finance, instruction)
    check("Kämmerei: Planungswerkzeug gesperrt", "keine Lesefreigabe" in blocked_write)
    created = ask(planning, instruction)
    found = re.search(r"/notes/([0-9a-f-]{36})", created)
    check("Stadtplanung: verlinkter Entwurf nach MCP-Aufruf", bool(found))
    if found:
        note_id = found[1]
        reply = ask(planning, instruction)
        check("Wiederholter Auftrag öffnet denselben privaten Entwurf",
              note_id in reply and "bereits" in reply)
        note = planning.get(api + "/notes/" + note_id, timeout=15).json()
        data = json.dumps(note.get("data"), ensure_ascii=False)
        check("Notiz dauerhaft mit Prüfbedarf und beiden Quellen",
              "Flurstücksnummer" in data and cfg["files"][PLAN_FILE] in data and cfg["files"][PLAN_NOTE] in data)
        blocked = finance.get(api + "/notes/" + note_id, timeout=15).status_code
        check("Kämmerei: auch der direkte Notizlink ist geschützt", blocked in (401,403,404), http=blocked)
        all_notes = planning.get(api + "/notes/", timeout=15).json()
        check("Genau ein persönlicher Entwurf für den Übungsfall",
              sum(n["title"] == note["title"] for n in all_notes) == 1)
    return all(x["bestanden"] for x in tests)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
