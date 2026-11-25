"""Persistenter Zustand + Status-Datei des Ingestion-Watchers.

Der State ist die Wahrheit darueber, was schon verarbeitet bzw. in
Quarantaene ist (Schluessel: watcher-eigene SHA-256 ueber die
heruntergeladenen Bytes, Spec 6.4). Die Status-Datei ist die
Beobachtbarkeits-Oberflaeche fuer Admin und preflight.py (Spec, offene
Frage 4). Beide werden atomar geschrieben (temp + rename), damit ein
Absturz keinen halben JSON-Inhalt hinterlaesst.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class WatcherState:
    processed: dict[str, dict] = field(default_factory=dict)
    quarantined: dict[str, dict] = field(default_factory=dict)
    id_cache: dict[str, str] = field(default_factory=dict)
    mismatches: dict[str, int] = field(default_factory=dict)
    pending: dict[str, dict] = field(default_factory=dict)


def load_state(path: Path) -> WatcherState:
    if not path.exists():
        return WatcherState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return WatcherState(
        processed=data.get("processed", {}),
        quarantined=data.get("quarantined", {}),
        id_cache=data.get("id_cache", {}),
        mismatches=data.get("mismatches", {}),
        pending=data.get("pending", {}),
    )


def _write_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def save_state(state: WatcherState, path: Path) -> None:
    _write_atomic(asdict(state), path)


def write_status(status: dict, path: Path) -> None:
    payload = dict(status)
    payload["written_at"] = time.time()
    _write_atomic(payload, path)


def status_is_stale(status: dict, now: float, poll_interval: float, factor: float) -> bool:
    last = status.get("last_successful_poll")
    if last is None:
        return True
    return (now - float(last)) > (factor * poll_interval)
