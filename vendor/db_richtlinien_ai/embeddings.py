"""Gemeinsame Embedding-Abstraktion fuer Ingest und Retrieval.

Ziel (Plan A2/A3): EIN Embedding-Pfad fuer beide Seiten der Anwendung, damit
die Vektoren in Qdrant konsistent sind, egal ob sie vom Ingest-Skript, von
``ask.py`` oder spaeter vom geteilten vLLM-Embedding-Dienst erzeugt werden.

Zwei Backends, per Config ``embedding_engine`` umschaltbar:

* ``local``  – ``sentence-transformers`` im Prozess (Default, unveraendertes
  Verhalten; gut fuer reproduzierbare Eval ohne laufenden GPU-Dienst).
* ``openai`` – OpenAI-kompatibler Embedding-Endpoint (der ``vllm-embedding``-
  Dienst aus ``docker-compose.yml``). Damit nutzen Skripte und OpenWebUI
  dasselbe Modell ueber denselben Dienst.

Bewusst ohne neue Abhaengigkeit: der Remote-Pfad nutzt nur ``urllib``.
"""

from __future__ import annotations

import json
import math
import urllib.request
from typing import Any, Sequence


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class LocalEmbedder:
    """sentence-transformers im Prozess (normalisierte Vektoren)."""

    def __init__(self, model_name: str, revision: str | None = None):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, revision=revision)
        self.dimension = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str], batch_size: int = 8) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


class OpenAIEmbedder:
    """OpenAI-kompatibler Embedding-Endpoint (z. B. vLLM ``--runner pooling``).

    Vektoren werden client-seitig L2-normiert, damit Cosine-Distanz in Qdrant
    unabhaengig davon stimmt, ob der Server normalisiert ausliefert.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "sk-noauth",
        timeout: int = 120,
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Dimension einmalig vom Dienst erfragen (kein Hardcoding).
        self.dimension = len(self._request(["dimension-probe"])[0])

    def _request(self, texts: Sequence[str]) -> list[list[float]]:
        payload = {"model": self.model_name, "input": list(texts)}
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [_l2_normalize(item["embedding"]) for item in items]

    def encode(self, texts: Sequence[str], batch_size: int = 8) -> list[list[float]]:
        texts = list(texts)
        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            out.extend(self._request(texts[start : start + batch_size]))
        return out

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


def load_embedder(config: dict[str, Any]):
    """Baut den passenden Embedder aus der RAG-Config.

    ``embedding_engine``: ``local`` (Default) oder ``openai``.
    """
    engine = str(config.get("embedding_engine", "local")).lower()
    model_name = config["embedding_model"]
    if engine == "openai":
        return OpenAIEmbedder(
            model_name,
            config.get("embedding_base_url", "http://localhost:8081/v1"),
            config.get("embedding_api_key", "sk-noauth"),
        )
    return LocalEmbedder(model_name, revision=config.get("embedding_revision") or None)
