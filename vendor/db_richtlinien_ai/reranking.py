"""Gemeinsame Reranker-Abstraktion fuer Retrieval und Eval.

Analog zu ``embeddings.py``: ZWEI Backends, per Config ``reranker_engine``
umschaltbar, identische Score-Semantik (Relevanz-Wahrscheinlichkeit in [0, 1]):

* ``local``  – ``sentence-transformers``-CrossEncoder im Prozess. Auf dem
  CPU-only rag-api-Container ist das der dominante Latenzposten pro Anfrage.
* ``openai`` – OpenAI-/Jina-kompatibler ``/rerank``-Endpoint (der
  ``vllm-reranker``-Dienst aus ``docker-compose.yml``). Damit laeuft das
  Cross-Encoder-Scoring auf der GPU und wird zwischen Prozessen geteilt.

Bewusst ohne neue Abhaengigkeit: der Remote-Pfad nutzt nur ``urllib``.
"""

from __future__ import annotations

import json
import math
import urllib.request
from typing import Any, Sequence


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _normalize_scores(scores: Sequence[float]) -> list[float]:
    """Rohlogits -> Wahrscheinlichkeiten; bereits normierte Scores unveraendert.

    Lokale CrossEncoder liefern Logits, vLLM-Rerank liefert je nach Version
    Logits oder Sigmoid-Werte. Sobald ein Score ausserhalb [0, 1] liegt,
    interpretieren wir die ganze Liste als Logits.
    """
    values = [float(s) for s in scores]
    if values and (min(values) < 0.0 or max(values) > 1.0):
        return [_sigmoid(v) for v in values]
    return values


class LocalReranker:
    """CrossEncoder im Prozess (CPU/GPU je nach Host)."""

    def __init__(
        self,
        model_name: str,
        revision: str | None = None,
        trust_remote_code: bool = False,
        batch_size: int = 16,
        device: str | None = None,
    ):
        from sentence_transformers import CrossEncoder

        import torch
        model_kwargs = {"dtype": torch.float16} if device and device.startswith("cuda") else {}
        self._model = CrossEncoder(
            model_name,
            revision=revision,
            trust_remote_code=trust_remote_code,
            device=device, model_kwargs=model_kwargs,
        )
        self.batch_size = batch_size

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        pairs = [[query, text] for text in texts]
        raw = self._model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return _normalize_scores([float(r) for r in raw])


class RemoteReranker:
    """Jina-kompatibler ``/rerank``-Endpoint (z. B. vLLM ``--runner pooling``)."""

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

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": list(texts),
            "top_n": len(texts),
        }
        request = urllib.request.Request(
            f"{self.base_url}/rerank",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        # Ergebnisse kommen relevanz-sortiert zurueck -> auf Eingabereihenfolge
        # zurueckmappen, damit der Aufrufer per zip() zuordnen kann.
        by_index = {int(item["index"]): float(item["relevance_score"]) for item in data["results"]}
        return _normalize_scores([by_index[i] for i in range(len(texts))])


def load_reranker(config: dict[str, Any]):
    """Baut den passenden Reranker aus der RAG-Config.

    ``reranker_engine``: ``local`` (Default) oder ``openai``.
    """
    engine = str(config.get("reranker_engine", "local")).lower()
    model_name = config["reranker_model"]
    if engine == "openai":
        return RemoteReranker(
            model_name,
            config.get("reranker_base_url", "http://localhost:8082/v1"),
            config.get("reranker_api_key", "sk-noauth"),
            timeout=int(config.get("reranker_timeout", 120)),
        )
    return LocalReranker(
        model_name,
        revision=config.get("reranker_revision") or None,
        trust_remote_code=bool(config.get("reranker_trust_remote_code", False)),
        batch_size=int(config.get("reranker_batch_size", 16)),
        device=config.get("reranker_device"),
    )
