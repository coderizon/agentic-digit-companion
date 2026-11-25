"""Content-addressed extraction and embedding plans; no service mutations."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from db_richtlinien_ai.pdf_pages import (
    extract_page_records, list_pdfs, read_jsonl, write_jsonl,
)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_incrementally(raw_dir: Path, output: Path, *, adopt_existing=False, extractor=None) -> dict:
    """Reuse complete cached PDFs only when their source bytes are unchanged.

    --adopt-existing is an explicit, one-time migration of a trusted existing
    corpus. Normal imports never trust legacy records without a source hash.
    Extraction errors leave the previous JSONL intact.
    """
    from dataclasses import asdict

    extractor = extractor or extract_page_records
    cached = defaultdict(list)
    if output.exists():
        for record in read_jsonl(output):
            cached[record["source_path"]].append(record)
    pdfs = list_pdfs(raw_dir)
    if not pdfs:
        raise ValueError(f"No PDFs found in {raw_dir}")
    records = []
    stats = {"reused_documents": 0, "extracted_documents": 0, "adopted_documents": 0}
    for pdf in pdfs:
        source = pdf.relative_to(raw_dir).as_posix()
        digest = file_digest(pdf)
        old = sorted(cached.get(source, []), key=lambda r: int(r["page"]))
        complete = bool(old) and [int(r["page"]) for r in old] == list(range(1, len(old) + 1))
        same = complete and all(r.get("source_sha256") == digest for r in old)
        legacy = complete and all(not r.get("source_sha256") for r in old)
        if same or (adopt_existing and legacy):
            batch = [dict(r, source_sha256=digest) for r in old]
            stats["reused_documents" if same else "adopted_documents"] += 1
        else:
            batch = [dict(asdict(r) if not isinstance(r, dict) else r, source_sha256=digest)
                     for r in extractor(pdf, raw_dir)]
            # Detect a PDF being replaced during extraction; never cache mixed bytes.
            if file_digest(pdf) != digest:
                raise RuntimeError(f"PDF changed during extraction: {source}")
            stats["extracted_documents"] += 1
        records.extend(batch)
    ids = [r["id"] for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate page IDs: PDF names collide after normalization")
    if not adopt_existing or stats["adopted_documents"] or stats["extracted_documents"]:
        write_jsonl(records, output)
    return dict(stats, pages=len(records))


def embedding_signature(config: dict[str, Any], dimension: int) -> str:
    identity = {k: config.get(k) for k in ("embedding_model", "embedding_revision", "distance")}
    identity.update(dimension=dimension, normalization="l2", text_version=1)
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def plan_vectors(records: list[dict], existing: list, signature: str, *, adopt_existing=False):
    """Compare actual Qdrant payloads, so interrupted runs remain retry-safe."""
    by_record = {}
    for point in existing:
        key = (point.payload or {}).get("record_id")
        if key in by_record:
            raise ValueError(f"Duplicate record_id in Qdrant: {key}")
        if key:
            by_record[key] = point
    desired = {r["id"] for r in records}
    pending, adopt = [], []
    for record in records:
        point = by_record.get(record["id"])
        payload = point.payload if point else {}
        same_text = point is not None and payload.get("text") == record["text"]
        identity = payload.get("embedding_signature")
        if same_text and identity == signature:
            continue
        if adopt_existing and same_text and identity is None:
            adopt.append(point.id)
        else:
            pending.append(record)
    stale = [p.id for p in existing if (p.payload or {}).get("record_id") not in desired]
    return pending, adopt, stale
