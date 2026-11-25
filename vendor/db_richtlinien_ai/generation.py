"""Gemeinsame Kontext- und LLM-Generierung fuer CLI, Eval und RAG-API."""

from __future__ import annotations

import json
import logging
import hashlib
import ipaddress
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Iterable
from db_richtlinien_ai.reading_mode import final_only

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class ModelResponseError(RuntimeError):
    """Transport/model failures are not evidence that a source is missing."""


class TruncatedGenerationError(ModelResponseError):
    """A complete model response explicitly exhausted its output-token budget."""


class ContextWindowError(ModelResponseError):
    """An exact local token count exceeded the configured context window."""


class ReaderUnavailableError(ModelResponseError):
    """Neither configured local reader is ready for this request."""


_tokenize_lock = RLock()
_token_counts: OrderedDict[tuple[str, str], tuple[int, int | None, float]] = OrderedDict()
_tokenize_unavailable: dict[str, float] = {}
_reader_ready_cache: OrderedDict[tuple[str, str, str], tuple[bool, float]] = OrderedDict()


def _reader_ready(config):
    base = str(config.get("llm_base_url") or "").rstrip("/")
    model = str(config.get("llm_model") or "")
    api_key = str(config.get("llm_api_key", "local"))
    cache_key = (base, model, hashlib.sha256(api_key.encode()).hexdigest())
    with _tokenize_lock:
        cached = _reader_ready_cache.get(cache_key)
        if cached and time.monotonic() - cached[1] < 1:
            return cached[0]
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(request, timeout=min(2.0, max(.1, float(config.get("llm_reader_probe_timeout", 1))))) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
        ready = isinstance(data, dict) and model in {item.get("id") for item in data.get("data", []) if isinstance(item, dict)}
    except (OSError, ValueError, TypeError):
        ready = False
    with _tokenize_lock:
        _reader_ready_cache[cache_key] = (ready, time.monotonic())
        while len(_reader_ready_cache) > 16:
            _reader_ready_cache.popitem(last=False)
    return ready


def select_reader(config):
    """Optional single reader selection before any generation/verification.

    Readiness checks are bounded and cached for one second. A valid unsupported
    answer never triggers another model. Once selected, one config stays with
    the whole request so generation and evidence checking cannot mix readers.
    """
    if not config.get("fallback_llm_base_url"):
        return config
    if not _private_endpoint(str(config["fallback_llm_base_url"])):
        raise ValueError("Reader fallback requires a local or private endpoint")
    if _reader_ready(config):
        logger.info("Reader selected: primary model=%s", config.get("llm_model"))
        return dict(config, selected_reader="primary")
    fallback = dict(config,
        llm_base_url=config["fallback_llm_base_url"],
        llm_model=config.get("fallback_llm_model"),
        llm_revision=config.get("fallback_llm_revision"),
        llm_api_key=config.get("fallback_llm_api_key", config.get("llm_api_key", "local")),
        llm_tokenize_url=config.get("fallback_llm_tokenize_url"),
        llm_context_window=config.get("fallback_llm_context_window", config.get("llm_context_window")),
        selected_reader="fallback")
    if fallback["llm_model"] and _reader_ready(fallback):
        logger.warning("Reader selected: fallback model=%s; primary unavailable", fallback["llm_model"])
        return fallback
    raise ReaderUnavailableError("Die lokalen Sprachmodelle sind gerade nicht bereit. Bitte versuche es in Kürze erneut.")


def _messages(system_prompt, user_prompt):
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _private_endpoint(url):
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or ""
    try:
        address = ipaddress.ip_address(hostname)
        local = address.is_private or address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        local = hostname == "localhost" or hostname.endswith(".ts.net")
    return parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password and local


def _local_tokenizer_url(config):
    url = str(config.get("llm_tokenize_url") or "").strip()
    if not url or not config.get("llm_context_window"):
        return None
    if not _private_endpoint(url):
        raise ValueError("Token preflight requires a local or private Tail/Head endpoint")
    return url


def count_prompt_tokens(config, system_prompt, user_prompt, model=None):
    """Count using the served chat template; unsupported endpoints are optional.

    Only counts/digests are cached. Original prompts are sent to the explicitly
    configured private endpoint and are never stored in this cache or logs.
    """
    url = _local_tokenizer_url(config)
    if not url:
        return None
    payload = dict(model=model or config.get("llm_model"), messages=_messages(system_prompt, user_prompt),
                   add_generation_prompt=True, add_special_tokens=False,
                   return_token_strs=False, chat_template_kwargs={"enable_thinking": config.get("llm_thinking") is True})
    data = json.dumps(payload).encode("utf-8")
    cache_key = (url, hashlib.sha256(data).hexdigest())
    with _tokenize_lock:
        if _tokenize_unavailable.get(url, 0) > time.monotonic():
            return None
        if cache_key in _token_counts:
            cached = _token_counts[cache_key]
            if time.monotonic() - cached[2] < 5:
                _token_counts.move_to_end(cache_key)
                return cached[:2]
            del _token_counts[cache_key]
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {config.get('llm_api_key', 'local')}"})
    try:
        with urllib.request.urlopen(request, timeout=min(5.0, max(.1, float(config.get("llm_tokenize_timeout", 2))))) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid tokenizer response")
        count, maximum = result.get("count"), result.get("max_model_len")
        if type(count) is not int or count < 0:
            raise ValueError("invalid token count")
        if maximum is not None and (type(maximum) is not int or maximum <= 0):
            raise ValueError("invalid context window")
    except urllib.error.HTTPError as exc:
        if exc.code not in {404, 405, 501, 422}:
            raise _http_failure(exc) from None
        with _tokenize_lock:
            _tokenize_unavailable[url] = time.monotonic() + 60
        logger.info("Token preflight unavailable: http_status=%s; using character budget", exc.code)
        return None
    except (OSError, ValueError, TypeError):
        logger.info("Token preflight unavailable; using character budget")
        return None
    with _tokenize_lock:
        _token_counts[cache_key] = (count, maximum, time.monotonic())
        while len(_token_counts) > 128:
            _token_counts.popitem(last=False)
    return count, maximum


def _token_limit(config, measured):
    configured = int(config["llm_context_window"])
    reported = measured[1]
    limit = min(configured, reported) if reported else configured
    reserve = int(config.get("llm_max_tokens", 1200)) + max(0, int(config.get("llm_context_margin_tokens", 64)))
    return limit - reserve


def ensure_prompt_fits(config, system_prompt, user_prompt, model=None):
    measured = count_prompt_tokens(config, system_prompt, user_prompt, model)
    if measured is not None and measured[0] > _token_limit(config, measured):
        raise ContextWindowError("Die Anfrage überschreitet das Kontextfenster. Bitte formuliere die Frage kürzer.")
    return measured


def _http_failure(exc: urllib.error.HTTPError) -> ModelResponseError:
    # Model servers sometimes echo the prompt in their error body. Read it only
    # to classify the failure; do not put the body, prompt, or credentials in logs.
    detail = exc.read(16384).decode("utf-8", errors="replace").casefold()
    if any(term in detail for term in ("context length", "context_length", "maximum context", "max_model_len")):
        category = "context_length"
    elif "model" in detail and any(term in detail for term in ("not found", "does not exist", "unknown model")):
        category = "unknown_model"
    elif any(term in detail for term in ("max_tokens", "max_completion_tokens", "temperature", "response_format")):
        category = "invalid_parameters"
    else:
        category = "request_rejected"
    logger.warning("LLM request rejected: status=%s category=%s", exc.code, category)
    return ModelResponseError(f"Modellanfrage fehlgeschlagen (HTTP {exc.code}, {category}).")


def resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def build_context(
    passages: Iterable[Any],
    max_hit_chars: int,
    max_neighbor_chars: int,
    budget: int,
) -> str:
    return assemble_context(passages, max_hit_chars, max_neighbor_chars, budget)[0]


def assemble_context(passages, max_hit_chars, max_neighbor_chars, budget):
    """Return both the prompt context and exactly the excerpts actually sent."""
    blocks: list[str] = []
    sources: dict[str, str] = {}
    used = 0
    for passage in passages:
        limit = max_hit_chars if passage.kind == "hit" else max_neighbor_chars
        text = passage.text[:limit].strip()
        if not text:
            continue
        tag = "" if passage.kind == "hit" else " (Nachbarseite/Kontext)"
        metadata=''
        hints=deepcopy(getattr(passage,'signals',{}).get('structure') or {})
        if hints:
            while len(json.dumps(hints,ensure_ascii=False))>1800 and hints.get('table_hints'):hints['table_hints'].pop()
            raw=json.dumps(hints,ensure_ascii=False)
            if len(raw)<=1800:metadata='Suchmetadaten (keine eigenstaendigen Belege): '+raw+'\n'
        block = f"### {passage.locator}{tag}\n{metadata}Zitierfaehige Fundstelle: {passage.locator}\n{text}"
        if used + len(block) > budget and blocks:
            break
        blocks.append(block)
        sources[passage.locator] = text
        used += len(block)
    return "\n\n".join(blocks), sources


def context_sources_from_config(passages, config):
    return assemble_context(passages, int(config.get("max_hit_chars", 3500)),
                            int(config.get("max_neighbor_chars", 1200)),
                            int(config.get("context_budget_chars", 24000)))[1]


def build_context_from_config(passages: Iterable[Any], config: dict[str, Any]) -> str:
    return build_context(
        passages,
        int(config.get("max_hit_chars", 3500)),
        int(config.get("max_neighbor_chars", 1200)),
        int(config.get("context_budget_chars", 24000)),
    )


def prepare_rag_prompt(passages, config, system_prompt, template, query):
    """Fit only source excerpts, retaining the exact question and system text.

    Context and admissible verifier sources come from one assembly each time.
    A bounded exact-count loop trims excerpt lengths/budget, never source IDs
    independently of their text. The default remains the existing char budget.
    """
    passages = list(passages)
    if config.get('enable_definition_scoping',False):
        from db_richtlinien_ai.entity_scope import scope_definition_passages
        passages=scope_definition_passages(passages,query)
    budget = int(config.get("context_budget_chars", 24000))
    hit_limit = int(config.get("max_hit_chars", 3500))
    neighbor_limit = int(config.get("max_neighbor_chars", 1200))
    for attempt in range(4):
        context, sources = assemble_context(passages, hit_limit, neighbor_limit, budget)
        prompt = render_user_prompt(template, query, context)
        if not sources:
            return prompt, sources, {"context_chars": len(context), "prompt_tokens": None, "context_reductions": attempt}
        measured = count_prompt_tokens(config, system_prompt, prompt)
        if measured is None or measured[0] <= _token_limit(config, measured):
            return prompt, sources, {"context_chars": len(context), "prompt_tokens": measured[0] if measured else None, "context_reductions": attempt}
        allowed = _token_limit(config, measured)
        # Use the actually assembled length: a nominal 24k character budget may
        # hold much less text, and reducing only that nominal value can do nothing.
        budget = int(min(budget, len(context)) * max(.1, min(.75, allowed / max(1, measured[0]) * .85)))
        if budget < 128:
            break
        per_passage = max(64, budget // max(1, min(4, len(passages))))
        hit_limit = min(hit_limit, per_passage)
        neighbor_limit = min(neighbor_limit, per_passage)
    raise ContextWindowError("Die Frage und ihre Quellen passen nicht in das Kontextfenster. Bitte grenze die Frage etwas ein.")


def load_prompts(config: dict[str, Any]) -> tuple[str, str]:
    concise = config.get("concise_answers", False)
    system_path = config.get("concise_system_prompt", "config/system_prompt_richtlinien_concise.md") if concise else config["system_prompt"]
    template_path = config.get("concise_rag_template", "config/rag_template_concise.md") if concise else config["rag_template"]
    system_prompt = resolve(system_path).read_text(encoding="utf-8")
    template = resolve(template_path).read_text(encoding="utf-8")
    return system_prompt, template


def render_user_prompt(template: str, query: str, context: str) -> str:
    return template.replace("{{QUERY}}", query).replace("{{CONTEXT}}", context)


def _llm_request(
    config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    base_url: str | None,
    model: str | None,
    stream: bool,
) -> urllib.request.Request:
    resolved_base_url = (base_url or config.get("llm_base_url", "http://localhost:8000/v1")).rstrip("/")
    resolved_model = model or config.get("llm_model")
    payload = {
        "model": resolved_model,
        "messages": _messages(system_prompt, user_prompt),
        "temperature": config.get("llm_temperature", 0.1),
        "top_p": config.get("llm_top_p", 0.8),
        "max_tokens": config.get("llm_max_tokens", 1200),
        "stream": stream,
        "chat_template_kwargs": {"enable_thinking": config.get("llm_thinking") is True},
    }
    if config.get("llm_response_format"):
        payload["response_format"] = config["llm_response_format"]
    return urllib.request.Request(
        f"{resolved_base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.get('llm_api_key', 'local')}",
        },
        method="POST",
    )


def call_llm(
    config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    ensure_prompt_fits(config, system_prompt, user_prompt, model)
    request = _llm_request(config, system_prompt, user_prompt, base_url, model, stream=False)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=float(config.get("llm_timeout", 600))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ModelResponseError("Der Modellserver hat eine ungültige Antwort geliefert. Bitte versuche es erneut.") from None
    if not isinstance(data, dict):
        raise ModelResponseError("Der Modellserver hat eine ungültige Antwort geliefert. Bitte versuche es erneut.")
    choices = data.get("choices") or []
    if not isinstance(choices, list) or (choices and not isinstance(choices[0], dict)):
        raise ModelResponseError("Der Modellserver hat eine ungültige Antwort geliefert. Bitte versuche es erneut.")
    if choices and choices[0].get('finish_reason')=='length':
        raise TruncatedGenerationError('Die Modellantwort wurde vorzeitig beendet. Bitte versuche es erneut.')
    if choices and choices[0].get("finish_reason") not in (None, "stop"):
        raise ModelResponseError("Die Modellantwort wurde vorzeitig beendet. Bitte versuche es erneut.")
    message = (choices[0].get("message") or {}) if choices else {}
    if config.get("llm_thinking") is True:
        content = final_only(message)
        logger.info("Qwen text thinking: model=%s tokens=%s final=%s",
                    model or config.get("llm_model"), data.get("usage",{}).get("completion_tokens"), bool(content))
    else:
        content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelResponseError("Das Modell hat keine Antwort geliefert. Bitte versuche es erneut.")
    logger.info("LLM stage completed: seconds=%.3f output_chars=%s", time.perf_counter() - started, len(content))
    return content


def iter_sse_content(lines: Iterable[bytes | str]) -> Iterable[str]:
    """Extrahiert Content-Deltas aus einem OpenAI-SSE-Strom (``data:``-Zeilen)."""
    complete = False
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            complete = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            raise ModelResponseError("Der Modellserver hat einen ungültigen Datenstrom geliefert. Bitte versuche es erneut.") from None
        if not isinstance(chunk, dict) or chunk.get("error"):
            raise ModelResponseError("Die Modellübertragung wurde abgebrochen. Bitte versuche es erneut.")
        choices = chunk.get("choices") or []
        if not isinstance(choices, list) or (choices and not isinstance(choices[0], dict)):
            raise ModelResponseError("Der Modellserver hat einen ungültigen Datenstrom geliefert. Bitte versuche es erneut.")
        if not choices:
            continue  # z. B. abschliessender Usage-Chunk
        if choices[0].get('finish_reason')=='length':
            raise TruncatedGenerationError('Die Modellantwort wurde vorzeitig beendet. Bitte versuche es erneut.')
        if choices[0].get("finish_reason") not in (None, "stop"):
            raise ModelResponseError("Die Modellantwort wurde vorzeitig beendet. Bitte versuche es erneut.")
        content = (choices[0].get("delta") or {}).get("content")
        if content:
            yield content
    if not complete:
        raise ModelResponseError("Die Modellübertragung wurde unvollständig beendet. Bitte versuche es erneut.")


def stream_llm(
    config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    base_url: str | None = None,
    model: str | None = None,
) -> Iterable[str]:
    """Streamt die LLM-Antwort als Folge von Text-Deltas (Token-Chunks).

    Gegenstueck zu ``call_llm``: gleiche Anfrage, aber die vLLM-SSE-Chunks
    werden durchgereicht, sobald sie eintreffen — die Time-to-first-token
    haengt damit nur noch am Retrieval + Prefill, nicht an der kompletten
    Generierung.
    """
    if config.get("llm_thinking") is True:
        # The caller maintains browser SSE heartbeats. Buffer the private
        # reasoning until the model returns a final answer to verify.
        yield call_llm(config, system_prompt, user_prompt, base_url, model)
        return
    ensure_prompt_fits(config, system_prompt, user_prompt, model)
    request = _llm_request(config, system_prompt, user_prompt, base_url, model, stream=True)
    started = time.perf_counter()
    size = 0
    has_content = False
    try:
        with urllib.request.urlopen(request, timeout=float(config.get("llm_timeout", 600))) as response:
            for content in iter_sse_content(response):
                size += len(content)
                has_content = has_content or bool(content.strip())
                yield content
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc) from None
    if not has_content:
        raise ModelResponseError("Das Modell hat keine Antwort geliefert. Bitte versuche es erneut.")
    logger.info("LLM stream completed: seconds=%.3f output_chars=%s", time.perf_counter() - started, size)
