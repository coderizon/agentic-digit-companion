"""Lokaler MCP-Server und Client für einen eng begrenzten Prüfentwurf."""
import asyncio
import fcntl
import hashlib
import html
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP

from fall import PLAN_FILE, PLAN_NOTE
from excel_export import export_file


ROOT = Path(os.environ.get("FREIBURG_ROOT") or Path(__file__).resolve().parents[2])
RUNDIR = ROOT / "run/fachbereiche"
CONFIG = json.loads((RUNDIR / "quellen.json").read_bytes())
MCP = FastMCP("DIGIT Companion")
TITLE = "Lageprüfung Kleineschholz · privater Prüfentwurf"
READ = "lagepruefung_lesen"
WRITE = "pruefvermerk_anlegen"
EXPORT = "pruefvermerk_exportieren"
evidence = None


def session():
    return httpx.Client(base_url=CONFIG["url"] + "/api/v1/", timeout=25, trust_env=False,
                        headers={"Authorization": "Bearer " + os.environ["DIGIT_SESSION"]})


def get_json(client, url):
    r = client.get(url)
    r.raise_for_status()
    return r.json()


def read_case(client):
    # Current WebUI permissions guard both the collection and the underlying file.
    for area in ("planung", "gemeinsam"):
        get_json(client, "knowledge/" + CONFIG["collections"][area])
    fid = CONFIG["files"][PLAN_NOTE]
    text = client.get("files/" + fid + "/content")
    text.raise_for_status()
    excerpt = "Fehlt sie, fordert die Stadtplanung die Flurstücksnummer bei der anfragenden Stelle an."
    if excerpt not in text.content.decode("utf-8"):
        raise ValueError("Die Arbeitsanweisung hat sich geändert.")
    pdf = CONFIG["files"][PLAN_FILE]
    get_json(client, "files/" + pdf)
    return {"fall": "Kleineschholz", "offen": "Flurstücksnummer", "anweisung": excerpt,
            "grundlage": "Entwicklungssatzung, § 2 und Anlage 1, Planstand 17.09.2021.",
            "links": {"plan": "/api/v1/files/" + pdf + "/content#page=3",
                      "arbeitsvermerk": "/api/v1/files/" + fid + "/content"}}


@MCP.tool()
def lagepruefung_lesen() -> dict:
    """Lies Planbezug und interne Arbeitsanweisung für die authentifizierte Stadtplanung."""
    global evidence
    with session() as client:
        evidence = read_case(client)
    return evidence


def note_body(case, key):
    paragraphs = [
        "Fiktiver Übungsfall · Stadtplanung · Persönlicher Entwurf",
        "Anliegen: Lageprüfung einer angefragten Fläche im Entwicklungsbereich Kleineschholz.",
        "Offen: Die Flurstücksnummer fehlt. Eine Zuordnung der Fläche ist noch nicht möglich.",
        "Nächster Schritt: " + case["anweisung"] + " Danach die Lage mit der Umgrenzung in Anlage 1 abgleichen.",
        "Planbezug: " + case["grundlage"],
        "Status: Rückfrage vorzubereiten. Eine Stellungnahme oder Beauftragung ist damit nicht erteilt."]
    blocks = [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs]
    html_text = "".join("<p>" + html.escape(p) + "</p>" for p in paragraphs)
    md = "\n\n".join(paragraphs)
    for label, link in (("Amtlicher Lageplan · Anlage 1", case["links"]["plan"]),
                        ("Interner Arbeitsvermerk · Übungsfall", case["links"]["arbeitsvermerk"])):
        blocks.append({"type": "paragraph", "content": [{"type": "text", "text": label,
                      "marks": [{"type": "link", "attrs": {"href": link}}]}]})
        html_text += '<p><a href="' + html.escape(link, quote=True) + '">' + html.escape(label) + '</a></p>'
        md += "\n\n[" + label + "](" + link + ")"
    return {"title": TITLE, "access_grants": [], "meta": {"digit_demo_v1": key},
            "data": {"content": {"json": {"type": "doc", "content": blocks}, "html": html_text, "md": md}}}


@contextmanager
def personal_note(client):
    user_id = get_json(client, "auths/")["id"]
    key = hashlib.sha256(("schulung-kleineschholz-v1:" + user_id).encode()).hexdigest()
    with (RUNDIR / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cached = RUNDIR / (key + ".id")
        ids = [cached.read_text().strip()] if cached.is_file() else []
        ids.extend(row["id"] for row in get_json(client, "notes/") if row["title"] == TITLE
                   or (row.get("meta") or {}).get("digit_demo_v1") == key)
        for ident in dict.fromkeys(ids):
            response = client.get("notes/" + ident)
            if response.status_code in (401, 403, 404):
                continue
            response.raise_for_status()
            note = response.json()
            if note.get("user_id") == user_id and (note.get("meta") or {}).get("digit_demo_v1") == key:
                cached.write_text(note["id"])
                yield note, cached, key
                return
        yield None, cached, key


@MCP.tool()
def pruefvermerk_anlegen() -> dict:
    """Lege nach der Quellenprüfung einen privaten Entwurf in den Notizen des angemeldeten Kontos ab."""
    if evidence is None or os.environ.get("DIGIT_ALLOW_DRAFT") != "1":
        raise ValueError("Ein ausdrücklicher Auftrag und die Quellenprüfung werden benötigt.")
    with session() as client:
        case = read_case(client)
        with personal_note(client) as (note, cached, key):
            if note:
                return {"id": note["id"], "angelegt": False}
            response = client.post("notes/create", json=note_body(case, key))
            response.raise_for_status()
            ident = response.json()["id"]
            cached.write_text(ident)
            return {"id": ident, "angelegt": True}


@MCP.tool()
def pruefvermerk_exportieren() -> dict:
    """Stelle den aktuellen persönlichen Prüfvermerk als private Excel-Datei bereit."""
    if evidence is None:
        raise ValueError("Die Quellenprüfung wird benötigt.")
    with session() as client:
        read_case(client)
        with personal_note(client) as (note, _, _key):
            if not note:
                return {"missing_note": True}
            return {"id": note["id"], "file_id": export_file(client, note)}


@MCP.tool()
def kix_ticket_anlegen() -> dict:
    """Übermittle den freigegebenen, an Konto und Chat gebundenen Ticketentwurf und lies die KIX-Nummer zurück."""
    ref = os.environ.get("DIGIT_IT_APPROVED")
    if not ref:
        raise ValueError("Für diesen Aufruf liegt keine Ticketfreigabe vor.")
    from it_service import send_case
    return send_case(os.environ["DIGIT_SESSION"], os.environ.get("DIGIT_IT_CHAT", ""), ref)


def decide(question, tools, last_result):
    port = os.environ.get("FREIBURG_READER_PORT", "18189")
    secret = (ROOT / "app/reader.key").read_text().strip()
    with httpx.Client(base_url=f"http://127.0.0.1:{int(port)}/v1/", trust_env=False, timeout=115,
                      headers={"Authorization": "Bearer " + secret}) as client:
        model = get_json(client, "models")["data"][0]["id"]
        prompt = {"auftrag": question, "verfuegbare_werkzeuge": tools, "bereits_gelesen": last_result}
        response = client.post("chat/completions", json={
            "model": model, "temperature": 0, "max_tokens": 120, "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False}, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Du steuerst einen lokalen Verwaltungsassistenten. "
                "Wähle für den ausdrücklichen Auftrag genau das nächste angebotene Werkzeug. Lies zuerst "
                "die Grundlage. Wenn sie vorliegt, kann ein ausdrücklich gewünschter privater Entwurf angelegt "
                "werden. Bei unpassendem Auftrag oder fehlender Berechtigung wähle stopp. "
                'Antworte nur mit {"werkzeug":"<Name oder stopp>"}.'},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]})
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"].strip()
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw))["werkzeug"]


async def act(request):
    question = request["question"]
    export_only = request.get("export_only", False)
    # The end user's bearer travels over local process pipes, never in a prompt or
    # the command line. Only these environment values are forwarded to the server.
    env = {"FREIBURG_ROOT": str(ROOT), "DIGIT_SESSION": request["token"], "DIGIT_ALLOW_DRAFT": "0" if export_only else "1"}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as peer:
        await peer.initialize()
        inventory = {t.name: t.description for t in (await peer.list_tools()).tools}
        prior = None
        trace = []
        steps = (READ, EXPORT) if export_only else (READ, WRITE, EXPORT)
        note_result = {}
        for expected in steps:
            # Format and destination are fixed by the export action. The model
            # only makes the two existing decisions for creating a draft.
            if not export_only and expected != EXPORT and await asyncio.to_thread(
                    decide, question, {expected: inventory[expected], "stopp": "Kein Aufruf"}, prior) != expected:
                return {"stopped": True, "tools": trace}
            out = await peer.call_tool(expected, {})
            if out.isError:
                if expected == EXPORT and note_result:
                    return {**note_result, "export_failed": True, "tools": trace}
                raise RuntimeError("Die autorisierte Werkzeugausführung ist fehlgeschlagen.")
            prior = json.loads(out.content[0].text)
            if expected == WRITE:
                note_result = prior
            trace.append(expected)
        return {**note_result, **prior, "tools": trace}


if __name__ == "__main__":
    if sys.argv[1:] == ["server"]:
        MCP.run(transport="stdio")
    elif sys.argv[1:] == ["client"]:
        print(json.dumps(asyncio.run(act(json.load(sys.stdin))), ensure_ascii=False))
