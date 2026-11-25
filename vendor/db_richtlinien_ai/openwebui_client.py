"""Schmaler OpenWebUI-REST-Client fuer den Ingestion-Watcher.

Nur Stdlib (wie scripts/add_openwebui_kb_file.py). Die Listing-Route ist in
v0.10.2 paginiert (Standard-Seitengroesse 30, groesseres limit nur fuer
Admins) — daher wird immer die komplette Pagination gelaufen, statt sich
auf eine Seite zu verlassen. Zugriffsverweigerung auf die KB-Liste kommt
in v0.10.2 als HTTP 400, nicht nur 401/403.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class OpenWebUIError(RuntimeError):
    def __init__(self,message,status_code=None):
        super().__init__(message)
        self.status_code=status_code


class OpenWebUIAuthError(OpenWebUIError):
    pass


_AUTH_ERROR_CODES = (400, 401, 403)
_MAX_PAGES = 1000  # Schutz gegen Endlosschleifen bei inkonsistentem total


def _default_opener(request: urllib.request.Request, timeout: int) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class OpenWebUIClient:
    def __init__(self, base_url: str, token: str, opener=None, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = opener or _default_opener
        self.timeout = timeout

    def _get(self, path: str) -> bytes:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            return self.opener(request, self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in _AUTH_ERROR_CODES:
                raise OpenWebUIAuthError(f"GET {path}: HTTP {exc.code}",exc.code) from exc
            raise OpenWebUIError(f"GET {path}: HTTP {exc.code}",exc.code) from exc
        except urllib.error.URLError as exc:
            raise OpenWebUIError(f"GET {path}: {exc.reason}") from exc

    def list_knowledge_files(self, knowledge_id: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        total = None
        while page <= _MAX_PAGES:
            raw = self._get(f"/api/v1/knowledge/{knowledge_id}/files?page={page}")
            data = json.loads(raw.decode("utf-8"))
            page_items = data.get("items") or []
            total = int(data.get("total") or 0)
            items.extend(page_items)
            if not page_items or len(items) >= total:
                break
            page += 1
        return items

    def download_file(self, file_id: str, dest_dir: Path) -> tuple[Path, str]:
        payload = self._get(f"/api/v1/files/{file_id}/content")
        digest = hashlib.sha256(payload).hexdigest()
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=dest_dir, prefix=".download-", suffix=".part", delete=False
        ) as handle:
            handle.write(payload)
            tmp_path = Path(handle.name)
        return tmp_path, digest
