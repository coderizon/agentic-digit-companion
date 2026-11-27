"""A real handbook lookup, a personal draft and an explicitly confirmed KIX write."""
import asyncio
import fcntl
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kix_client import confirmed_ticket, create_ticket, kix_session, lookup


ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])
STATE = ROOT / "run/fachbereiche"
CODE = re.compile(r"\bRP[\s‐‑–-]*(\d{2,5})\b", re.I)
REF = re.compile(r"\bDC-[A-F0-9]{12}\b")
YES = re.compile(r"ja(?:\s+bitte)?(?:\s+(?:(?:das\s+)?(?:als\s+)?(?:ticket\s+)?(?:anlegen|erstellen|melden|senden|absenden)"
                 r"|(?:erstell(?:e)?|sende|melde)\s+(?:das\s+)?ticket))?(?:\s+bitte)?")
NO = {"nein", "nein danke", "bitte nicht", "abbrechen", "doch nicht"}
OFFER = "Soll ich diesen Entwurf an den IT-Service senden?"


def clean(value):
    return " ".join(value.split())[:1800]


def inline(value):
    return re.sub(r"([\\\[\]*_<>\x60])", r"\\\1", value)


def decision(text):
    words = " ".join(re.sub(r"[,!.?]", " ", text.lower()).split())
    return "send" if YES.fullmatch(words) else "cancel" if words in NO else None


def database():
    db = sqlite3.connect(STATE / "it-faelle.sqlite3", timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS cases (
        ref TEXT PRIMARY KEY, owner TEXT NOT NULL, chat TEXT NOT NULL, title TEXT NOT NULL,
        description TEXT NOT NULL, state TEXT NOT NULL, kix_id INTEGER, number TEXT)""")
    return db


def web_context(token):
    settings = json.loads((STATE / "it-handbuch.json").read_bytes())
    return settings, httpx.Client(base_url=settings["url"] + "/api/v1/",
                                 headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=40)


def identity(client, settings):
    me = client.get("auths/")
    me.raise_for_status()
    collection = client.get("knowledge/" + settings["collection"])
    collection.raise_for_status()
    return me.json()["id"]


def find_section(client, settings, question):
    found = client.post("retrieval/query/collection", json={"collection_names": [settings["collection"]],
                        "query": question, "k": 4, "hybrid": False})
    found.raise_for_status()
    result = found.json()
    metadata = [m for group in (result.get("metadatas") or []) for m in group]
    if not any(m.get("file_id") == settings["file"] for m in metadata):
        return None
    source = client.get("files/" + settings["file"] + "/content")
    source.raise_for_status()
    sections = re.findall(r"^##\s+([^\n]+)\n(.+?)(?=^##\s|\Z)", source.content.decode("utf-8-sig"),
                          flags=re.M | re.S)
    # An unrelated error code can have an almost identical embedding.
    codes = {m.group(1) for m in CODE.finditer(question)}
    topic = question.lower()
    if not codes and re.search(r"erst(?:en|es|malig|anmeldung)|erstmals", topic):
        term = "erstanmeldung"
    elif not codes and re.search(r"raum (?:finden|anzeigen)|raumansicht|meine räume|raumdetails", topic):
        term = "raum anzeigen"
    elif not codes and re.search(r"(?:wie|was).*störung melden", topic):
        term = "störung melden"
    else:
        term = None
    for title, passage in sections:
        if ((codes and codes == {m.group(1) for m in CODE.finditer(title)})
                or (term and term in title.lower())):
            return title.strip(), " ".join(passage.split())
    return None


def saved_case(db, ref, owner, chat_id):
    return db.execute("SELECT * FROM cases WHERE ref=? AND owner=? AND chat=?",
                      (ref, owner, chat_id)).fetchone()


def new_draft(db, owner, chat_id, question, settings):
    ref = "DC-" + uuid.uuid4().hex[:12].upper()
    match = CODE.search(question)
    label = "Fehler RP-" + match.group(1) if match else "Störungsmeldung"
    title = "RaumPlan · " + label + " · " + ref
    body = (question + "\n\nIm IT-Handbuch lag für diesen Fall keine wirksame Lösung vor."
            + "\nQuelle: " + settings["name"] + "\nÜbergeben aus dem DIGIT Companion. Referenz " + ref)
    db.execute("INSERT INTO cases(ref,owner,chat,title,description,state) VALUES(?,?,?,?,?,'draft')",
               (ref, owner, chat_id, title, body))
    return ref


def send_case(token, chat_id, ref):
    if not REF.fullmatch(ref):
        raise ValueError("Ungültiger Ticketentwurf")
    settings, web = web_context(token)
    with web:
        owner = identity(web, settings)
    # Serialize repeated approvals and remember ambiguous API outcomes.
    with closing(database()) as db, (STATE / (ref + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        case = saved_case(db, ref, owner, chat_id)
        if not case or case["state"] == "cancelled":
            return {"state": "closed"}
        if case["state"] == "sent":
            return {"state": "sent", "number": case["number"], "id": case["kix_id"]}
        conf = json.loads((STATE / "kix.privat.json").read_bytes())
        with kix_session(conf) as kix:
            ticket_id = case["kix_id"]
            if case["state"] == "pending" and not ticket_id:
                hits = lookup(kix, "tickets", "Ticket", "Title", case["title"])
                if len(hits) != 1:
                    return {"state": "pending"}
                ticket_id = int(hits[0]["TicketID"])
            if not ticket_id:
                if not db.execute("UPDATE cases SET state='pending' WHERE ref=? AND state='draft'", (ref,)).rowcount:
                    return {"state": "closed"}
                try:
                    ticket_id = create_ticket(kix, conf, case)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {400, 401, 403, 404, 422}:
                        db.execute("UPDATE cases SET state='draft' WHERE ref=?", (ref,))
                    raise
            db.execute("UPDATE cases SET kix_id=? WHERE ref=?", (ticket_id, ref))
            number = confirmed_ticket(kix, ticket_id, case)
            db.execute("UPDATE cases SET state='sent',number=? WHERE ref=?", (number, ref))
    return {"state": "sent", "number": number, "id": ticket_id}


async def call_kix_tool(token, chat_id, ref):
    env = {"FREIBURG_ROOT": str(ROOT), "DIGIT_SESSION": token, "DIGIT_IT_CHAT": chat_id,
           "DIGIT_IT_APPROVED": ref}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("werkzeuge.py")),
                                                                 "server"], env=env)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as peer:
        await peer.initialize()
        reply = await peer.call_tool("kix_ticket_anlegen", {})
        if reply.isError:
            return {"state": "unknown"}
        return json.loads(reply.content[0].text)


def process(request):
    os.umask(0o077)
    token, chat_id = request["token"], request.get("chat") or ""
    question, previous = clean(request["question"]), request.get("previous") or ""
    settings, web = web_context(token)
    with web, closing(database()) as db:
        owner = identity(web, settings)
        if set(request.get("selections", [])) - {settings["collection"]}:
            return "Bitte wähle für die IT-Hilfe das IT-Handbuch RaumPlan oder starte ohne angehängte Sammlung."
        action = decision(question)
        if action:
            match = REF.search(previous)
            case = saved_case(db, match[0], owner, chat_id) if match else None
            if not case or (OFFER not in previous and "Ticketnummer" not in previous
                            and "Übermittlung" not in previous):
                return "In diesem Gespräch wartet gerade kein Ticketentwurf auf deine Bestätigung."
            ref = case["ref"]
            if action == "cancel":
                if db.execute("UPDATE cases SET state='cancelled' WHERE ref=? AND state='draft'", (ref,)).rowcount:
                    return "Entwurf verworfen. Es wurde kein Ticket angelegt."
                return "Dieser Entwurf ist bereits abgeschlossen oder wird gerade übermittelt."
            result = asyncio.run(call_kix_tool(token, chat_id, ref))
            if result["state"] == "sent":
                url = settings["portal"].rstrip("/") + "/tickets/" + str(int(result["id"]))
                return ("**Das Ticket ist beim IT-Service eingegangen.**\n\n"
                        + "**Ticketnummer " + result["number"] + "**\n\n[Ticket in KIX öffnen](" + url + ")"
                        + "\n\nEntwurf " + ref)
            if result["state"] == "closed":
                return "Dieser Entwurf ist abgeschlossen. Es wurde kein weiteres Ticket angelegt."
            return ("KIX hat die Übermittlung noch nicht bestätigt. Du kannst mit „Ja“ den Stand erneut "
                    "prüfen. Entwurf " + ref)
        failed = bool(re.search(r"nicht geholfen|hilft nicht|klappt (?:weiterhin |immer noch )?nicht|"
                                r"geht (?:weiterhin |immer noch )?nicht|fehler (?:bleibt|besteht)", question, re.I))
        if failed and request.get("last_user") and "IT-Handbuch" in previous:
            issue = clean(clean(request["last_user"]) + " – Rückmeldung: " + question)
        else:
            issue = question
        link = "[IT-Handbuch RaumPlan](/api/v1/files/" + settings["file"] + "/content)"
        section = None if request.get("handbook_followup_draft") else find_section(web, settings, issue)
        if section and not failed:
            title, passage = section
            return passage + "\n\n**Quelle:** " + link + " · " + title
        ref = new_draft(db, owner, chat_id, issue, settings)
        code = CODE.search(issue)
        if failed:
            info = "Die Handbuchschritte haben das Problem laut deiner Rückmeldung nicht gelöst."
        elif code:
            info = "Im IT-Handbuch finde ich keine Anleitung für **RP-" + code.group(1) + "**."
        else:
            info = "Im IT-Handbuch finde ich keine passende Anleitung für diese Störung."
        return (info + "\n\n**Ticketentwurf für den IT-Service**\n\n" + inline(issue)
                + "\n\nGeprüfte Quelle: " + link + " · Entwurf " + ref
                + "\n\n" + OFFER + " Antworte mit **Ja** oder **Nein**.")


if __name__ == "__main__":
    print(json.dumps({"reply": process(json.load(sys.stdin))}, ensure_ascii=False))
