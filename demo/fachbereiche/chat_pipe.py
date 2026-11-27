"""
title: DIGIT Fachbereichswerkzeuge
description: Freigegebene Freiburger Fachquellen und ein privater Prüfentwurf per MCP.
version: 1.1.0
"""
import asyncio
import json
import os
import re
from pathlib import Path
from uuid import UUID

import httpx
from open_webui.utils.misc import get_last_user_message


class Pipe:
    def __init__(self):
        self.name = "DIGIT Fachbereichswerkzeuge"

    async def pipe(self, body, __request__, __user__, __event_emitter__=None, __task__=None, __files__=None, __chat_id__=None):
        if __task__:
            if re.search(r"raumplan|IT-Handbuch|RP[- ]\d{2,5}", str(body.get("messages", [])), re.I):
                return "IT-Hilfe für RaumPlan"
            return "Lageprüfung Kleineschholz"
        root = Path(os.environ.get("FREIBURG_ROOT") or "/workspace/freiburg-rag")
        conf = json.loads((root / "run/fachbereiche/quellen.json").read_bytes())
        question = (get_last_user_message(body.get("messages") or []) or "").strip()
        question = re.sub(r"^<attached_files>[\s\S]*?</attached_files>\s*", "", question)
        bearer = __request__.headers.get("Authorization", "")
        token = bearer[7:] if bearer.lower().startswith("bearer ") else __request__.cookies.get("token", "")
        if not token:
            return "Für die Fachquellen ist eine Anmeldung in Open WebUI erforderlich."
        async with httpx.AsyncClient(base_url=conf["url"], headers={"Authorization": "Bearer " + token},
                                     trust_env=False, timeout=265) as client:
            me = await client.get("/api/v1/auths/")
            me.raise_for_status()
            if me.json()["id"] != __user__["id"]:
                raise ValueError("Die Sitzung stimmt nicht mit dem angemeldeten Konto überein.")
            kb = dict(conf["collections"])
            it_path = root / "run/fachbereiche/it-handbuch.json"
            if it_path.is_file():
                kb["it"] = json.loads(it_path.read_bytes())["collection"]
            probes = await asyncio.gather(*(client.get("/api/v1/knowledge/" + ident) for ident in kb.values()))
            for p in probes:
                if not p.is_success and p.status_code not in (401, 403, 404):
                    p.raise_for_status()
            allowed = {key: p.json()["name"] for key, p in zip(kb, probes) if p.is_success}
            if not ({"planung", "finanzen"} & allowed.keys()):
                return "Dieser Fachbereichschat setzt eine Freigabe für den Übungsfall voraus."
            attachments = list(__files__ or []) + list(body.get("files") or [])
            explicit = {item.get("id") for item in attachments if isinstance(item, dict)
                        and item.get("type") in {"collection", "knowledge"}}
            if not explicit.issubset({kb[area] for area in allowed}):
                return "Für die gewählte Wissenssammlung fehlt die Lesefreigabe."
            if re.search(r"(?:welche|meine|zeig.{0,9}meine).{0,16}(wissensquellen|wissenssammlungen|wissensdatenbanken|sammlungen)", question, re.I):
                return "Für dein Konto sind diese Wissenssammlungen freigegeben:\n\n" + "\n".join(
                    "- **" + name + "**" for name in allowed.values())
            history = body.get("messages", [])[:-1]
            previous_reply = next((m.get("content", "") for m in reversed(history) if m.get("role") == "assistant"), "")
            previous_reply = previous_reply if isinstance(previous_reply, str) else ""
            it_followup = ("IT-Handbuch" in previous_reply or re.search(r"DC-[A-F0-9]{12}", previous_reply)) and re.search(
                r"^\s*(?:ja\b|nein\b|abbrechen|doch nicht|bitte nicht)|nicht geholfen|hilft nicht|immer noch|weiterhin|fehler (?:bleibt|besteht)", question, re.I)
            it_question = re.search(r"\bRaumplan(?:er)?\b|\bRP[\s‐‑–-]*\d{2,5}\b|IT[- ]Handbuch|IT[- ]Service|KIX[- ]Ticket", question, re.I)
            if "it" in kb and (it_question or it_followup or kb["it"] in explicit):
                if "it" not in allowed:
                    return "Für das IT-Handbuch fehlt dir die Lesefreigabe."
                last_user = next((m["content"] for m in reversed(history)
                                  if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
                request = {"token": token, "chat": __chat_id__, "question": question, "previous": previous_reply,
                           "last_user": last_user, "selections": list(explicit)}
                return await self.it_help(root, request)
            finance = re.search(r"finanzvermerk|prüfschwelle|interne.{0,15}kämmerei", question, re.I)
            plan = re.search(r"arbeitsvermerk|interne.{0,15}stadtplanung", question, re.I)
            draft = re.search(r"(?:erstelle?|lege|speichere?|verfasse?|bereite).{0,65}(?:prüfvermerk|prüfentwurf)", question, re.I | re.S)
            note_followup = (isinstance(previous_reply, str) and re.search(r"/notes/[0-9a-f-]{36}", previous_reply)
                             and re.search(r"\b(?:das|diesen|den|ihn|davon|daraus|als)\b|^\s*(?:bitte\s+)?excel", question, re.I))
            excel = bool(re.search(r"\b(?:excel\w*|xlsx)\b", question, re.I) and
                         (re.search(r"prüf(?:vermerk|entwurf)|lageprüfung", question, re.I) or note_followup))
            missing_parcel = re.search(r"Flurstücksnummer.{0,28}(?:fehlt|offen|unbekannt|liegt nicht vor)|(?:ohne|fehlende) Flurstücksnummer", question, re.I)
            new_case = bool(draft and re.search("Kleineschholz", question, re.I) and missing_parcel)
            export_only = excel and not new_case
            if (finance and "finanzen" not in allowed) or ((plan or draft or excel) and "planung" not in allowed):
                return ("Für diese interne Quelle hast du keine Lesefreigabe. Die öffentlichen Unterlagen "
                        "zum Quartier Kleineschholz kannst du in diesem Chat nutzen.")
            if draft or excel:
                if explicit and not {kb["gemeinsam"], kb["planung"]}.issubset(explicit):
                    return ("Für diesen Prüfvermerk brauche ich die gemeinsame Plansammlung und die "
                            "internen Prüfunterlagen der Stadtplanung. Bitte wähle beide Sammlungen.")
                if not export_only and not new_case:
                    return ("Der Übungsablauf erstellt einen persönlichen Prüfvermerk für Kleineschholz. "
                            "Bitte gib an, ob die Flurstücksnummer fehlt.")
                if __event_emitter__:
                    status = "Excel-Liste aus deinen Notizen erstellen …" if export_only else "Quellen prüfen und persönlichen Entwurf vorbereiten …"
                    await __event_emitter__({"type": "status", "data": {"description": status, "done": False}})
                process = await asyncio.create_subprocess_exec(
                    str(root / "run/fachbereiche-mcp/bin/python"), str(root / "demo/fachbereiche/werkzeuge.py"), "client",
                    env={**os.environ, "FREIBURG_ROOT": str(root)}, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                try:
                    command = {"token": token, "question": question, "export_only": export_only}
                    stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(command).encode()), 265)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                if __event_emitter__:
                    await __event_emitter__({"type": "status", "data": {"description": "Werkzeugprüfung abgeschlossen", "done": True}})
                if process.returncode:
                    return "Der Werkzeugdienst konnte den Vorgang nicht bestätigen. Prüfe deine Notizen, bevor du es erneut versuchst."
                result = json.loads(stdout)
                if result.get("stopped"):
                    return "Der Assistent hat die Bearbeitung vor dem Speichern beendet."
                if result.get("missing_note"):
                    return ("In deinen Notizen fehlt der Prüfvermerk für Kleineschholz. Zum Anlegen kannst du schreiben: "
                            "„Erstelle einen privaten Prüfvermerk für die Lageprüfung im Kleineschholz. Die Flurstücksnummer fehlt.“")
                nid = str(UUID(result["id"]))
                note_link = "[Prüfvermerk öffnen](/notes/" + nid + ")"
                if result.get("export_failed"):
                    return (note_link + "\n\nDein Prüfvermerk ist gespeichert. Der Excel-Export ist fehlgeschlagen. "
                            "Mit „Exportiere meinen Prüfvermerk als Excel“ kannst du ihn erneut anfordern.")
                fid = str(UUID(result["file_id"]))
                download = "[Excel-Liste herunterladen](/api/v1/files/" + fid + "/content?attachment=true)"
                if export_only:
                    return "Dein Prüfvermerk zur Lageprüfung Kleineschholz ist als Excel-Liste verfügbar.\n\n" + download + " · " + note_link
                line = ("Ich habe den privaten Prüfvermerk in deinen Notizen angelegt." if result["angelegt"]
                        else "Der private Prüfvermerk liegt bereits in deinen Notizen.")
                if result["angelegt"]:
                    line += ("\n\n**Offen** ist die Flurstücksnummer. Sobald sie vorliegt, kann die Stadtplanung "
                             "die Fläche mit Anlage 1 abgleichen.")
                return (line + "\n\n" + download + " · " + note_link
                        + "\n\n*Über MCP ausgeführt: Planbezug und Arbeitsvermerk geprüft, persönlichen Entwurf bestätigt.*")

            selected = [area for area in ("gemeinsam", "planung", "finanzen") if area in allowed]
            if finance:
                selected = ["finanzen"]
            elif plan:
                selected = ["planung"]
            visual = bool(re.search(r"lageplan|rote (?:linie|grenze)|umgriff|anlage 1|maßstab", question, re.I))
            if visual and not finance and not plan:
                selected = ["gemeinsam"]
            if explicit:
                selected = [area for area in selected if kb[area] in explicit]
            if not selected:
                return "Die Frage passt zu keiner der ausgewählten, freigegebenen Sammlungen."
            messages = [{"role": m["role"], "content": m["content"]} for m in body.get("messages", [])[:-1]
                        if m["role"] in {"user", "assistant"} and isinstance(m.get("content"), str)][-6:]
            messages.append({"role": "user", "content": question})
            answer = await client.post("/api/chat/completions", json={"model": "freiburg-stadtdaten", "messages": messages,
                    "stream": False, "files": [{"type": "collection", "id": kb[area]} for area in selected]})
            answer.raise_for_status()
            text = answer.json()["choices"][0]["message"]["content"]
            if visual and "gemeinsam" in selected:
                fid = conf["files"]["Entwicklungssatzung Kleineschholz mit Lageplan.pdf"]
                text += "\n\n[**Amtlichen Lageplan öffnen · Anlage 1, Seite 3**](/api/v1/files/" + fid + "/content#page=3)"
                if conf.get("planbild"):
                    text += " · [Planbild ansehen](" + conf["planbild"] + ")"
            if finance:
                text += "\n\n*Der interne Finanzvermerk ist ein fiktiver Übungsfall.*"
            return text

    async def it_help(self, root, request):
        process = await asyncio.create_subprocess_exec(
            str(root / "run/fachbereiche-mcp/bin/python"), str(root / "demo/fachbereiche/it_service.py"),
            env={**os.environ, "FREIBURG_ROOT": str(root)}, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), 120)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode:
            # Keep private contact details, request bodies and tokens out of WebUI logs.
            print("DIGIT-IT: Werkzeugprozess endete mit Status", process.returncode, flush=True)
            return "Die IT-Hilfe konnte den Vorgang gerade nicht verifizieren. Bitte versuche es erneut."
        return json.loads(stdout)["reply"]
