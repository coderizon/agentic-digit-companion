"""Prüft die echte Kette von der angemeldeten Open WebUI bis zum Artikel im KIX-Testsystem."""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from fall import MODEL
from kix_client import get, kix_session, lookup
from zugang import ROOT, signin

STATE = ROOT / "run/fachbereiche"
CONFIG = json.loads((STATE / "it-handbuch.json").read_bytes())
KIX = json.loads((STATE / "kix.privat.json").read_bytes())
REF = re.compile(r"\bDC-[0-9A-F]{12}\b")


def turn(name, question, preceding=()):
    url = CONFIG["url"]
    client, _ = signin(url, STATE / (name + ".credentials.json"))
    with client:
        response = client.post(url + "/api/chat/completions", timeout=(8, 180), json={
            "model": MODEL, "stream": False,
            "messages": [*preceding, {"role": "user", "content": question}]})
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
    return [*preceding, {"role": "user", "content": question}, {"role": "assistant", "content": answer}]


def same_title(kix, ref):
    return lookup(kix, "tickets", "Ticket", "Title", "RaumPlan · Fehler RP-74 · " + ref)


def run():
    known = turn("planung", "RaumPlan zeigt RP-12. Was soll ich tun?")[-1]["content"]
    assert CONFIG["file"] in known and "Sitzung abgelaufen" in known and "Ticketentwurf" not in known
    print("1 Bekannter Code mit passender Quelle.", flush=True)
    question = "Nach dem Laptopwechsel meldet RaumPlan RP-74. Ich habe mich bereits neu angemeldet."
    with kix_session(KIX) as kix:
        abandoned = turn("planung", question)
        ref = REF.search(abandoned[-1]["content"])[0]
        assert not same_title(kix, ref)
        cancelled = turn("planung", "Nein", abandoned)[-1]["content"]
        assert "verworfen" in cancelled
        turn("planung", "Ja", abandoned)
        assert not same_title(kix, ref)
        print("2 Abbruch und spätere Wiederholung ohne Ticket.", flush=True)
        draft = turn("planung", question)
        body = draft[-1]["content"]
        ref = REF.search(body)[0]
        assert CONFIG["file"] in body and "keine Anleitung" in body and question in body
        assert not same_title(kix, ref)
        stolen = turn("finanzen", "Ja", draft)[-1]["content"]
        assert "kein Ticketentwurf" in stolen and not same_title(kix, ref)
        print("3 Unbekannter Code erzeugt nur einen Entwurf. Fremdes Konto kann ihn nicht senden.", flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: turn("planung", "Ja, bitte melden.", draft)[-1]["content"], range(2)))
        print("Bestätigungsantwort:", replies[0], flush=True)
        numbers = [re.search(r"Ticketnummer (\d+)", msg)[1] for msg in replies]
        assert numbers[0] == numbers[1]
        results = same_title(kix, ref)
        assert len(results) == 1, len(results)
        row = results[0]
        ticket_id = row.get("TicketID") or row["ID"]
        assert str(row["TicketNumber"]) == numbers[0] and all("/tickets/" + str(ticket_id) in msg for msg in replies)
        articles = get(kix, f"tickets/{ticket_id}/articles").get("Article") or []
        for article in articles:
            if "Body" not in article:
                aid = article.get("ArticleID") or article["ID"]
                article.update(get(kix, f"tickets/{ticket_id}/articles/{aid}")["Article"])
        assert any(question in a.get("Body", "") and ref in a["Body"] for a in articles)
        print("4 Zwei gleichzeitige Freigaben ergeben genau ein Ticket mit Originalfrage, versuchter Anmeldung und derselben Nummer.", flush=True)
        print(json.dumps({"id": ticket_id, "number": numbers[0], "ref": ref}, ensure_ascii=False))


if __name__ == "__main__":
    run()
