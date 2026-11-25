"""Kernlogik des Ingestion-Watchers (Spec: docs/OpenWebUI_Ingestion_Watcher_Spec.md, Rev. 4).

Poll-only-Transport: kein eingehender HTTP-Endpunkt. Erkennung ist
zweistufig (Spec 6.4): meta.file_hash aus dem Listing als Vorfilter,
watcher-eigene SHA-256 ueber die heruntergeladenen Bytes als Autoritaet.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from db_richtlinien_ai.openwebui_client import OpenWebUIAuthError, OpenWebUIError
from db_richtlinien_ai.pdf_pages import extract_page_records, read_jsonl
from db_richtlinien_ai.watcher_state import WatcherState, save_state


@dataclass(frozen=True)
class Candidate:
    file_id: str
    filename: str
    meta_hash: str | None
    updated_at: str


def select_work(items: list[dict], state: WatcherState) -> list[Candidate]:
    work: list[Candidate] = []
    for entry in items:
        filename = str(entry.get("filename") or "")
        if not filename.lower().endswith(".pdf"):
            continue
        file_id = str(entry.get("id") or "")
        updated_at = str(entry.get("updated_at") or "")
        meta = entry.get("meta") or {}
        meta_hash = meta.get("file_hash")
        if meta_hash is not None:
            meta_hash = str(meta_hash)

        # id_cache zuerst: deckt Dateien ohne meta.file_hash ab UND Dateien,
        # deren meta.file_hash luegt — nach einer Mismatch-Eskalation ist der
        # echte (Watcher-)Hash verbucht, der Listing-Hash aber nie.
        cached = state.id_cache.get(f"{file_id}:{updated_at}")
        if cached and (cached in state.processed or cached in state.quarantined):
            continue
        if meta_hash is not None and (meta_hash in state.processed or meta_hash in state.quarantined):
            continue
        work.append(Candidate(file_id, filename, meta_hash, updated_at))
    return work


@dataclass(frozen=True)
class Admission:
    action: str  # "ingest" | "bootstrap" | "quarantine"
    reason: str
    final_path: Path | None


def _normalized_stem(filename: str) -> str:
    return Path(filename).stem.replace(" ", "_")


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def admit_download(
    tmp_path: Path,
    filename: str,
    sha256: str,
    raw_dir: Path,
    quarantine_dir: Path,
    probe=None,
) -> Admission:
    """Zulassung vor data/raw/ (Spec 6.2 + 6.12).

    Reihenfolge: Bootstrap (byte-identisch vorhanden, Bedingung 4) ->
    Kollision (gleicher normalisierter Stem, anderer Inhalt/Name: ablehnen,
    nie ueberschreiben) -> Probe (extract_page_records auf der Einzeldatei,
    Bedingung 2) -> Aufnahme per atomarem rename.
    """
    probe = probe or extract_page_records

    existing_exact = raw_dir / filename
    if existing_exact.exists() and _sha256_of(existing_exact) == sha256:
        tmp_path.unlink(missing_ok=True)
        return Admission("bootstrap", "byte-identisch bereits in data/raw", None)

    stem = _normalized_stem(filename)
    for existing in raw_dir.rglob("*"):
        if existing.is_file() and existing.suffix.lower() == ".pdf" and _normalized_stem(existing.name) == stem:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            tmp_path.replace(quarantine_dir / filename)
            return Admission("quarantine", "collision", quarantine_dir / filename)

    try:
        probe(tmp_path, tmp_path.parent)
    except Exception as exc:  # noqa: BLE001 — jede Ablehnung der Pipeline zaehlt
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(quarantine_dir / filename)
        return Admission("quarantine", f"probe: {exc}", quarantine_dir / filename)

    final_path = raw_dir / filename
    tmp_path.replace(final_path)
    return Admission("ingest", "neu", final_path)


MISMATCH_ESCALATION_THRESHOLD = 3

_PIPELINE_SCRIPTS = (
    "scripts/extract_pages.py",
    "scripts/ocr_backfill.py",
    "scripts/build_page_groups.py",
    "scripts/build_doc_types.py",
    "scripts/build_bm25_index.py",
    "scripts/ingest_qdrant.py",
)


def run_pipeline(root: Path, runner=None) -> None:
    """Die Pipeline-Schritte seriell, im Repo-Root, mit demselben venv-Python."""
    runner = runner or subprocess.run
    for script in _PIPELINE_SCRIPTS:
        result = runner(
            [sys.executable, script, *(["--require"] if script.endswith("ocr_backfill.py") else [])],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        del result  # check=True wirft bei Fehler; Ausgabe haengt an der Exception


def verify_indexed(filenames: list[str], pages_path: Path) -> list[str]:
    """Bedingung 3: Hash erst verbuchen, wenn die Datei wirklich in pages.jsonl steht."""
    indexed = {record.get("source_file") for record in read_jsonl(pages_path)}
    return [name for name in filenames if name not in indexed]


def refresh_rag_api(url: str, api_key: str = "", opener=None) -> None:
    from db_richtlinien_ai.openwebui_client import _default_opener

    opener = opener or _default_opener
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        opener(request, 300)  # eager rebuild kann Sekunden dauern (Spec 6.10 A)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"rag-api-Reload fehlgeschlagen: {exc}") from exc


def check_mismatch(
    state: WatcherState, file_id: str, meta_hash: str | None, actual_sha256: str
) -> str:
    """Bedingung 1: meta.file_hash ist Vorfilter, nicht Autoritaet.

    Stimmen Listing-Hash und tatsaechlicher Download-Hash dauerhaft nicht
    ueberein, wird nach MISMATCH_ESCALATION_THRESHOLD Versuchen mit dem
    Watcher-Hash weitergearbeitet statt endlos zu verwerfen (Livelock).
    """
    if meta_hash is None or meta_hash == actual_sha256:
        state.mismatches.pop(file_id, None)
        return "ok"
    count = state.mismatches.get(file_id, 0) + 1
    if count >= MISMATCH_ESCALATION_THRESHOLD:
        state.mismatches.pop(file_id, None)
        return "escalate"
    state.mismatches[file_id] = count
    return "retry"


def _quarantine_existing(pdf_path: Path, quarantine_dir: Path, state, reason: str) -> None:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256_of(pdf_path)
    pdf_path.replace(quarantine_dir / pdf_path.name)
    state.quarantined[digest] = {"filename": pdf_path.name, "reason": reason, "at": time.time()}
    state.pending.pop(digest, None)


def poll_once(cfg: dict, client, state, now: float, pipeline=None, refresh=None, probe=None) -> dict:
    """Ein vollstaendiger Poll-Zyklus (Spec 5.1). Gibt den Status-Dict zurueck."""
    pipeline = pipeline or run_pipeline
    refresh = refresh or refresh_rag_api
    probe = probe or extract_page_records
    raw_dir: Path = cfg["raw_dir"]
    quarantine_dir: Path = cfg["quarantine_dir"]

    status: dict = {
        "last_successful_poll": None,
        "auth_failure": None,
        "last_error": None,
        "last_run": None,
        "last_refresh": None,
        "quarantine": [],
        "escalations": [],
    }

    try:
        items = client.list_knowledge_files(cfg["knowledge_id"])
    except OpenWebUIAuthError as exc:
        status["auth_failure"] = str(exc)
        status["quarantine"] = list(state.quarantined.values())
        return status
    except OpenWebUIError as exc:
        status["last_error"] = f"Listing fehlgeschlagen: {exc}"
        status["quarantine"] = list(state.quarantined.values())
        return status

    run: dict = {"at": now, "ingested": [], "bootstrapped": [], "outcome": "ok"}
    to_record: dict[str, dict] = {}  # sha256 -> processed-Eintrag, erst nach Verifikation

    for candidate in select_work(items, state):
        try:
            tmp_path, digest = client.download_file(candidate.file_id, raw_dir)
        except OpenWebUIError as exc:
            status["last_error"] = f"Download {candidate.filename}: {exc}"
            continue

        state.id_cache[f"{candidate.file_id}:{candidate.updated_at}"] = digest
        verdict = check_mismatch(state, candidate.file_id, candidate.meta_hash, digest)
        if verdict == "retry":
            tmp_path.unlink(missing_ok=True)
            continue
        if verdict == "escalate":
            status["escalations"].append(candidate.filename)

        admission = admit_download(tmp_path, candidate.filename, digest, raw_dir, quarantine_dir, probe=probe)
        if admission.action == "bootstrap":
            entry = {
                "filename": candidate.filename, "file_id": candidate.file_id, "processed_at": now,
            }
            if digest in state.pending:
                to_record[digest] = entry
            else:
                state.processed[digest] = entry
                run["bootstrapped"].append(candidate.filename)
        elif admission.action == "quarantine":
            state.quarantined[digest] = {
                "filename": candidate.filename, "reason": admission.reason, "at": now,
            }
        else:
            to_record[digest] = {
                "filename": candidate.filename, "file_id": candidate.file_id, "processed_at": now,
            }

    if to_record:
        # A failed OCR/index/reload must be retried, not silently bootstrapped
        # as processed just because the PDF already exists in data/raw.
        state.pending.update(to_record)
        if cfg.get("state_path"):
            save_state(state, cfg["state_path"])
        try:
            pipeline(cfg.get("root", raw_dir.parent.parent))
            missing = verify_indexed([e["filename"] for e in to_record.values()], cfg["pages_path"])
            if missing:
                status["last_error"] = (
                    "Nach dem Lauf nicht in pages.jsonl (Hash nicht verbucht): " + ", ".join(missing)
                )
            refresh(cfg["rag_api_reload_url"], cfg.get("rag_api_key", ""))
            status["last_refresh"] = now
            for digest, entry in to_record.items():
                if entry["filename"] not in missing:
                    state.processed[digest] = entry
                    state.pending.pop(digest, None)
                    run["ingested"].append(entry["filename"])
        except Exception as exc:  # noqa: BLE001 — Fehlzuordnung folgt
            run["outcome"] = "failed"
            status["last_error"] = str(exc)
            for pdf in list(raw_dir.rglob("*")):
                if not (pdf.is_file() and pdf.suffix.lower() == ".pdf"):
                    continue
                try:
                    probe(pdf, raw_dir)
                except Exception as probe_exc:  # noqa: BLE001
                    _quarantine_existing(pdf, quarantine_dir, state, f"attribution: {probe_exc}")

    status["last_run"] = run
    status["last_successful_poll"] = now
    status["quarantine"] = list(state.quarantined.values())
    return status


def load_watcher_config(path: Path, root: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not str(data.get("knowledge_id") or "").strip():
        raise ValueError(
            "config/watcher.json: knowledge_id muss gesetzt sein (ID der Ziel-Knowledge-Base)."
        )

    def resolve(key: str, default: str) -> Path:
        raw = Path(data.get(key, default))
        return raw if raw.is_absolute() else root / raw

    return {
        "root": root,
        "openwebui_base_url": data.get("openwebui_base_url", "http://localhost:3000"),
        "knowledge_id": data["knowledge_id"],
        "poll_interval_seconds": float(data.get("poll_interval_seconds", 60)),
        "raw_dir": resolve("raw_dir", "data/raw"),
        "quarantine_dir": resolve("quarantine_dir", "data/quarantine"),
        "state_path": resolve("state_path", "data/watcher_state.json"),
        "status_path": resolve("status_path", "data/watcher_status.json"),
        "pages_path": resolve("pages_path", "data/processed/pages.jsonl"),
        "rag_api_reload_url": data.get("rag_api_reload_url", "http://localhost:8090/admin/reload"),
        "rag_api_key": data.get("rag_api_key", ""),
        "stale_after_intervals": float(data.get("stale_after_intervals", 5)),
    }
