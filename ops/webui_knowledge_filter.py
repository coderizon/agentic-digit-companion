"""
title: Freiburger Wissenssammlungen
description: Übergibt freigegebene Fundstellen aus den im Chat ausgewählten Sammlungen an den Freiburger Stadtchat.
version: 1.2.0
"""
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from open_webui.internal.db import get_async_db_context
from open_webui.models.access_grants import AccessGrants
from open_webui.models.config import Config
from open_webui.models.files import File
from open_webui.models.groups import Groups
from open_webui.models.knowledge import Knowledge, KnowledgeFile, Knowledges
from open_webui.models.users import Users
from open_webui.retrieval.utils import get_sources_from_items
from open_webui.utils.misc import get_last_user_message


def collection_id(item):
    if not isinstance(item, dict) or item.get("type") not in {"collection", "knowledge"}:
        return None
    try:
        return str(UUID(str(item["id"])))
    except (ValueError, KeyError) as exc:
        raise ValueError("Die ausgewählte Wissenssammlung hat keine gültige Kennung.") from exc


def relevant_excerpt(text, question, limit=2200):
    if len(text) <= limit:
        return text
    terms = {term for term in re.findall(r"\w{4,}", question.casefold())}
    starts = range(0, len(text), limit - 300)
    best = max(starts, key=lambda n: len(terms.intersection(re.findall(r"\w{4,}", text[n:n + limit].casefold()))))
    return text[best:best + limit]


MEETING_ID = re.compile(r"\bmeeting[\s_-]*(\d{1,9})\b", re.I)


def asked_meeting(question, messages):
    def extract(text):
        ids = {f"Meeting{int(value):03d}" for value in MEETING_ID.findall(text)}
        if len(ids) > 1:
            raise ValueError("Bitte nenne für die Auswertung genau eine Meetingnummer.")
        return next(iter(ids), None)

    current = extract(question)
    if current:
        return current
    # Only continue a meeting conversation when the preceding answer actually cited audio.
    # The history supplies a search term, never authorization or transcript text.
    if not re.match(r"^(?:und\b|wer\b|wann\b|bis wann\b|was\b|welche\b|welcher\b|wie\b|fass\b|worum\b|gab es\b)", question, re.I):
        return None
    last_answer = next((m.get("content") for m in reversed(messages[:-1]) if m.get("role") == "assistant"), None)
    if isinstance(last_answer, str):
        match = re.search(r"Originalaufnahme (Meeting\d{3,9})\.mp3\]\(/api/v1/files/", last_answer)
        if match:
            words = set(re.findall(r"\w{4,}", question.lower())) - {
                "eine", "einen", "einer", "wird", "wurde", "werden", "welche", "welcher",
                "welchen", "wann", "noch", "sind", "haben", "diesem", "diese", "bitte"}
            if (re.search(r"\b(?:dazu|dabei|dafür|darüber|besprochen|meeting|aufnahme|gespräch)\b", question, re.I)
                    or len(question.split()) <= 2 or words.intersection(re.findall(r"\w{4,}", last_answer.lower()))):
                return match.group(1)
    return None


async def permitted_recordings(user_id, name, collection_ids):
    """Resolve an exact recording name through current collection membership and read grants."""
    async with get_async_db_context() as db:
        groups = await Groups.get_groups_by_member_id(user_id, include_inherited=True, db=db)
        query = (select(File.id, File.filename).select_from(Knowledge)
                 .join(KnowledgeFile, KnowledgeFile.knowledge_id == Knowledge.id)
                 .join(File, File.id == KnowledgeFile.file_id)
                 .where(func.lower(File.filename) == name.lower() + ".mp3"))
        if collection_ids:
            query = query.where(Knowledge.id.in_(collection_ids))
        query = AccessGrants.has_permission_filter(
            query, Knowledge, {"user_id": user_id, "group_ids": [group.id for group in groups]},
            "knowledge", "read", db=db)
        rows = (await db.execute(query.distinct().limit(2))).all()
    return [{"id": ident, "name": filename} for ident, filename in rows]


def attach_scope(body, metadata, attached, payload):
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    path = Path(os.environ.get("FREIBURG_UI_SIGN_KEY_FILE", "/srv/freiburg-openrag/app/ui.key"))
    proof = hmac.new(path.read_bytes().strip(), data, hashlib.sha256).hexdigest()
    body["freiburg_knowledge"] = {"payload": payload, "sig": proof}
    body["files"] = [item for item in attached if not collection_id(item)]
    if "files" in metadata:
        metadata["files"] = [item for item in (metadata["files"] or []) if not collection_id(item)]
    return body


class Filter:
    async def inlet(self, body, __request__, __user__, __model__, __metadata__):
        info = __model__.get("info") or {}
        model_ids = (body.get("model"), __model__.get("id"), info.get("base_model_id"))
        workflow = "freiburg-digitom-bedarf" in model_ids
        if not workflow and "freiburg-stadtdaten" not in model_ids:
            return body
        body.pop("freiburg_knowledge", None)
        attached = list(body.get("files") or [])
        meta = info.get("meta") or {}
        model_items = meta.get("knowledge") or []
        items = attached + list(__metadata__.get("files") or []) + list(model_items)
        ids = list(dict.fromkeys(i for item in items if (i := collection_id(item))))
        messages = body.get("messages") or []
        question = get_last_user_message(messages) or ""
        question = re.sub(r"^<attached_files>[\s\S]*?</attached_files>\s*", "", question).strip()
        meeting = asked_meeting(question, messages) if not workflow else None
        # The demonstration is a fallback with ordinary collection permissions.
        # Selecting any other collection removes it from the entire lookup.
        demo_id = meta.get("freiburg_demo_collection_id") if workflow else None
        if demo_id:
            default = collection_id({"type": "collection", "id": demo_id})
            ids = [i for i in ids if i != default] or [default]
        if not ids and not meeting:
            return body
        if len(ids) > 8:
            raise ValueError("Bitte wähle für eine Frage höchstens acht Wissenssammlungen aus.")
        user = await Users.get_user_by_id(__user__["id"])
        if not user:
            raise ValueError("Die Anmeldung für die Wissenssammlung fehlt.")
        if not question or len(question) > 3600:
            raise ValueError("Bitte gib eine Frage mit höchstens 3.600 Zeichen zur Sammlung ein.")
        if meeting:
            files = await permitted_recordings(user.id, meeting, ids)
            return attach_scope(body, __metadata__, attached, {
                "v": 1, "at": int(time.time()), "actor": user.id, "question": question,
                "workflow": "freiburg-meetings", "meeting": meeting, "recordings": files, "collections": []})
        turns = []
        if workflow:
            for message in body.get("messages") or []:
                if message.get("role") == "user":
                    value = get_last_user_message([message]) or ""
                    value = re.sub(r"^<attached_files>[\s\S]*?</attached_files>\s*", "", value).strip()
                    if value:
                        if re.search(r"^(?:neuer|anderer|neues)\s+(?:bedarf|anliegen|thema)", value, re.I):
                            turns = []
                        turns.append(value[:3600])
            turns = (turns[:-1] + [question])[-10:]
        queries = [question]
        if workflow:
            queries = list(dict.fromkeys([" ".join(turns)[-2600:],
                          "IT-Service im Servicekatalog, fachliche Zuständigkeit und ähnliche frühere Bedarfe",
                          "Bedarfsteckbrief Vorlage Pflichtangaben und Rückfragen"]))
        limit = max(1, min(12, int(await Config.get("rag.top_k") or 5)))
        collections = []
        for ident in ids:
            kb = await Knowledges.get_knowledge_by_id(ident)
            if not kb or (user.role != "admin" and not await Knowledges.check_access_by_user_id(ident, user.id, permission="read")):
                raise ValueError("Für mindestens eine ausgewählte Wissenssammlung fehlt die Lesefreigabe.")
            if (kb.meta or {}).get("source") == "external":
                raise ValueError("Der Freiburger Stadtchat unterstützt lokal hochgeladene Wissenssammlungen.")
            # Rebuild search items from authorized database IDs. Names, links,
            # collection_names and document text supplied by clients are ignored.
            hits = await get_sources_from_items(
                request=__request__, items=[{"type": "collection", "id": ident}],
                queries=queries, embedding_function=lambda text, prefix: __request__.app.state.EMBEDDING_FUNCTION(
                    text, prefix=prefix, user=user),
                k=limit, reranking_function=None, k_reranker=limit, r=0,
                hybrid_bm25_weight=0.5, hybrid_search=False, user=user,
            )
            found = []
            checked = {}
            for hit in hits:
                for text, meta in zip(hit.get("document") or [], hit.get("metadata") or []):
                    if not isinstance(text, str) or not text.strip() or not isinstance(meta, dict):
                        continue
                    try:
                        fid = str(UUID(str(meta.get("file_id"))))
                    except ValueError:
                        continue
                    if fid not in checked:
                        checked[fid] = await Knowledges.has_file(ident, fid)
                    if not checked[fid]:
                        continue
                    page = meta.get("page_label")
                    if not str(page or "").isdigit():
                        page = meta["page"] + 1 if type(meta.get("page")) is int else None
                    found.append({"file": fid, "name": str(meta.get("name") or meta.get("source") or "Dokument")[:180],
                                  "page": int(page) if page and int(page) > 0 else None,
                                  "text": relevant_excerpt(text.strip(), question)})
                    if len(found) >= limit:
                        break
                if len(found) >= limit:
                    break
            collections.append({"id": ident, "name": kb.name[:160], "hits": found})
        payload = {"v": 1, "at": int(time.time()), "actor": user.id, "question": question, "collections": collections}
        if workflow:
            payload.update(workflow="freiburg-digitom-bedarf", turns=turns, demo=bool(demo_id and ids == [demo_id]))
        return attach_scope(body, __metadata__, attached, payload)
