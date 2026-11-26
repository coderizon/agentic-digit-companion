"""Verify Open WebUI's permission-checked, request-scoped knowledge passages."""
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

def checked_scope(envelope, key_file):
    from fastapi import HTTPException

    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
        raise HTTPException(403, "Ungültige Wissensfreigabe.")
    scope = envelope["payload"]
    data = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > 900_000 or not key_file.is_file():
        raise HTTPException(403, "Die Wissensfreigabe lässt sich nicht prüfen.")
    signature = hmac.new(key_file.read_bytes().strip(), data, hashlib.sha256).hexdigest()
    if not isinstance(envelope.get("sig"), str) or not hmac.compare_digest(signature, envelope["sig"]):
        raise HTTPException(403, "Die Wissensfreigabe ist ungültig.")
    if (scope.get("v") != 1 or type(scope.get("at")) is not int or not -20 <= time.time() - scope["at"] <= 120
            or not scope.get("actor") or not isinstance(scope.get("question"), str)
            or not 1 <= len(scope["question"]) <= 3600 or not isinstance(scope.get("collections"), list)
            or not (0 if scope.get("workflow") == "freiburg-meetings" else 1) <= len(scope["collections"]) <= 8):
        raise HTTPException(403, "Die Wissensfreigabe ist abgelaufen oder unvollständig.")
    return scope


def label(text):
    return re.sub(r"[\[\]\\*_<>\x00-\x1f]", " ", str(text)).strip()


def candidates(scope, question, reranker, normalize):
    from evidence import windows
    items = []
    seen = set()
    for kb in scope["collections"]:
        for hit in kb["hits"]:
            fid = str(UUID(hit["file"]))
            name = label(hit["name"]) or "Dokument"
            page = hit.get("page")
            suffix = f" · S. {page}" if page else ""
            link = f"[{name}{suffix} · {label(kb['name'])}](/api/v1/files/{fid}/content"
            link += f"#page={page})" if page and name.lower().endswith(".pdf") else ")"
            for chunk in windows(hit["text"], normalize, width=800) or [normalize(hit["text"])]:
                key = (fid, page, chunk)
                if chunk and key not in seen:
                    seen.add(key)
                    items.append({"titel": name, "art": "Ausgewählte Wissenssammlung " + label(kb["name"]), "sammlung": kb["id"],
                                  "text": chunk, "link": link})
    if not items:
        return []
    scores = reranker.score(question, [item["titel"] + "\n" + item["text"] for item in items])
    ranked = [item for _, item in sorted(zip(scores, items), key=lambda pair: -pair[0])]
    if len(scope["collections"]) > 1 and re.search(r"\bbeide|\bjeweils\b|vergleich|unterschied|gegenüber", question, re.I):
        first = {}
        for item in ranked:
            first.setdefault(item["sammlung"], item)
        chosen = list(first.values())
        chosen.extend(item for item in ranked if item not in first.values())
        return chosen[:max(5, len(first))]
    return ranked[:5]
