"""Acceptance checks through Open WebUI, including a full two-turn demand interview."""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from setup_digitom_demo import MODEL, START, call, setup


def exercise(client, url, out, alternate=False):
    kb = setup(client, url)
    api = url + "/api/v1"
    a, b, past, template = (kb["files"][name] for name in
        ("01-maengelmeldungen.txt", "02-terminvergabe.txt", "03-vergleichsbedarf.txt", "04-steckbrief.txt"))
    rows = []

    def record(name, ok, **details):
        rows.append(dict(fall=name, bestanden=bool(ok), **details))
        out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(name, bool(ok), flush=True)

    def chat(name, question, history=(), selection=(), present=(), absent=()):
        messages = list(history) + [{"role": "user", "content": question}]
        start = time.monotonic()
        response = client.post(url + "/api/chat/completions", json={
            "model": MODEL, "messages": messages, "stream": False,
            "files": [{"type": "collection", "id": k["id"], "name": k["name"]} for k in selection]},
            timeout=(8, 420))
        data = response.json()
        answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or str(data.get("detail", ""))
        ok = response.ok and all(s in answer for s in present) and all(s not in answer for s in absent)
        record(name, ok, http=response.status_code, sekunden=round(time.monotonic() - start, 3), antwort=answer)
        return messages + [{"role": "assistant", "content": answer}]

    first = chat("Vorhandenen Service, Verantwortung und Vergleichsfall finden", START,
                 present=("Mobile Mängelmeldung", "Serviceteam Außendienst und Meldungen", "Beschädigte Parkbänke", a, past,
                          "Welche Stelle", "Woran"), absent=(b, "Wer soll den Dienst nutzen", "Welche Angabe gibt es"))
    chat("Steckbrief nach freier zweiter Nachricht", "Die Meldungen sollen beim Bauhof landen. "
         "Ziel ist, Schäden innerhalb von zwei Arbeitstagen zu sichten. Bitte erstelle den Steckbrief.", first,
         present=("**Zielgruppe:** Unser Außendienst", "**Empfänger:** Bauhof", "**Erfolgskriterium:** Schäden innerhalb von zwei Arbeitstagen zu sichten",
                  "Entwurf zur fachlichen Prüfung", template), absent=("Noch zu klären", "Typ:"))
    chat("Frühen Entwurf mit erkennbaren Lücken erstellen", "Wir möchten Schäden an Spielgeräten fotografieren. Bitte erstelle einen Steckbrief.",
         present=(a, "**Empfänger:** offen", "**Erfolgskriterium:** offen", "Noch zu klären", template), absent=(b,))
    chat("Unabhängiger zweiter Anwendungsfall", "Wir möchten online freie Zeitfenster für die Sprechstunde zur Terminbuchung anbieten.",
         present=("Online-Terminvergabe", "Serviceteam Digitale Termine", b), absent=(a, past, "Serviceteam Außendienst"))
    chat("Unbekanntes Thema offenlassen", "Das Amt braucht eine Lösung, um Reisekostenabrechnungen zu prüfen.",
         present=("kein passender Service belegt", "Zuständigkeit bleibt offen"), absent=(a, b, past, "Serviceteam"))
    ambiguous = chat("Bei mehreren passenden Services nachfragen", "Wir suchen etwas für Spielgeräte und Beratungstermine.",
                     present=("Mehrere passende", a, b), absent=("**Serviceverantwortung:**",))
    chat("Gewählten Service im Folgegespräch berücksichtigen", "Ich meine die Online-Terminvergabe.", ambiguous,
         present=(b, "Serviceteam Digitale Termine"), absent=(a, past))
    for fid, expected in ((a, "Mobile Mängelmeldung"), (past, "Beschädigte Parkbänke melden"), (template, "Pflichtangaben")):
        raw = client.get(api + f"/files/{fid}/content", timeout=12)
        record("Angeklicktes Original · " + expected, raw.ok and expected in raw.content.decode("utf-8"))
    if alternate:
        # New, isolated synthetic collections, only on the disposable acceptance instance.
        def sample(name, body):
            collection = call(client, "POST", api + "/knowledge/create",
                              json={"name": name, "description": "Synthetische Abnahmeprobe", "access_grants": []})
            file = call(client, "POST", api + "/files/?process=true&process_in_background=false",
                        files={"file": ("Probe.txt", body.encode(), "text/plain")})
            call(client, "POST", api + f"/knowledge/{collection['id']}/file/add", json={"file_id": file["id"]})
            return collection, file["id"]
        other, fid = sample("Ersatzkatalog · Funktionsprobe",
            "Typ: IT-Service\nKennung: TEST-44\nTitel: Meldedienst West\nLeistung: Erfassung von Spielplatzschäden."
            "\nZuständig: Musterstelle West\nSuchbegriffe: Spielplatz, Spielgerät, Foto, Standort, beschädigt")
        chat("Explizite Sammlung ersetzt die Übungsdaten auch im bestehenden Chat",
             "Bitte prüfe den Service für die Schäden an Spielgeräten anhand dieser Sammlung.", first,
             selection=[other], present=("Meldedienst West", "Musterstelle West", fid),
             absent=(a, b, past, template, "Serviceteam Außendienst", "Übungsfall mit fiktiven Quellen"))
        chat("Fehlender Service in der Ersatzsammlung löst keinen Rückfall auf Übungsdaten aus",
             "Wir möchten Zeitfenster für die Sprechstunde zur Terminbuchung anbieten.", selection=[other],
             present=("kein passender Service belegt",), absent=(a, b, past, fid, "Serviceteam"))
        prose, note = sample("Fließtext · Funktionsprobe",
             "Arbeitsnotiz der fiktiven Verwaltung. Schäden an Spielgeräten mit Foto und Standort erfasst der Service Werkstatt-Post. "
             "Für den Service Werkstatt-Post ist die Fachgruppe Kiesel zuständig. Sie bearbeitet die gemeldeten Spielplatzschäden.")
        chat("Unformatierte Arbeitsnotiz mit Originalbeleg auswerten",
             "Welcher Service erfasst Schäden an Spielgeräten mit Foto und Standort und wer ist für ihn zuständig?",
             selection=[prose], present=("Werkstatt-Post", "Fachgruppe Kiesel", note), absent=(a, b, past, template, "Serviceteam"))
    return all(r["bestanden"] for r in rows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--token-file", type=Path)
    p.add_argument("--test-only-alternate", action="store_true", help="Create additional synthetic collections on a disposable instance")
    args = p.parse_args()
    with requests.Session() as session:
        if args.token_file:
            session.headers["Authorization"] = "Bearer " + args.token_file.read_text().strip()
        else:
            if args.url not in ("http://127.0.0.1:8077", "http://127.0.0.1:18079"):
                p.error("Use a local test instance or a session token file.")
            login = call(session, "POST", args.url + "/api/v1/auths/signin", json={"email": "admin@localhost", "password": ""})
            session.headers["Authorization"] = "Bearer " + login["token"]
        if not exercise(session, args.url.rstrip("/"), args.output, args.test_only_alternate):
            raise SystemExit(1)
