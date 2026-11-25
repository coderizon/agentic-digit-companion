"""OpenAI-kompatibler Produkt-RAG-Service fuer OpenWebUI.

OpenWebUI bleibt damit die Chat-Oberflaeche. Retrieval, Reranking,
Kontext-Assembly und Strict-Prompting laufen zentral ueber denselben
``Retriever``, den auch die Eval-Skripte verwenden.

Performance-Pfad:
    * ``stream: true`` nutzt SSE. Bei aktiver Belegpruefung wird fachlicher
      Text erst nach Generierung und Pruefung ausgegeben.
* Blockierende Arbeit (Retrieval, LLM-Aufrufe) laeuft im Threadpool, damit
  der Event-Loop parallele Anfragen weiter annehmen kann.
* Folgefragen werden ueber den Gespraechsverlauf zu eigenstaendigen Fragen
  umformuliert (``enable_query_rewrite``), damit das Retrieval trifft.
* Optionale Authentifizierung ueber ``api_key`` / ``DBR_RAG_API_KEY``.
"""

from __future__ import annotations

import json
import time
import re
import uuid
import urllib.request
from contextvars import copy_context
from db_richtlinien_ai.reading_mode import text_settings, visual_enabled
from functools import lru_cache
from threading import RLock
from typing import Any, Iterator

import logging

from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from db_richtlinien_ai.generation import (
    build_context_from_config,
    context_sources_from_config,
    call_llm,
    load_prompts,
    render_user_prompt,
    stream_llm,
    ModelResponseError,
    ContextWindowError,
    prepare_rag_prompt,
    ReaderUnavailableError,
    TruncatedGenerationError,
    select_reader,
)
from db_richtlinien_ai.retrieval import Retriever, load_config
from db_richtlinien_ai.evidence import ABSTENTION, check_answer
from db_richtlinien_ai.context_retry import prepare_context_retry
from db_richtlinien_ai.proofs import render_evidence_table
from db_richtlinien_ai.nli_shadow import observe_start,observe_finish
from db_richtlinien_ai.comparison_evidence import literal_comparison

logger = logging.getLogger("db_richtlinien_ai.rag_api")
if not logging.getLogger().handlers:
    # uvicorn konfiguriert nur seine eigenen Logger; Rewrite-/Retrieval-Infos sollen im Container-Log sichtbar sein.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger.setLevel(logging.INFO)

app = FastAPI(title="DB-Richtlinien RAG API", version="1.1")
# Optional local deployment adapter; the core API never enables cloud OCR.
source_enricher = None
source_text_resolver = None
source_quote_resolver = None
request_retriever_resolver = None
answer_observer = None  # optional local diagnostics, never an approval authority

REWRITE_SYSTEM_PROMPT = (
    "Du formulierst die letzte Nutzerfrage eines Gespraechs als eigenstaendige, "
    "vollstaendig verstaendliche Frage um. Loese Rueckbezuege wie 'dort', 'dann', "
    "'davon' oder 'und bei Nebel?' anhand des Verlaufs auf. Erfinde nichts dazu. "
    "Kurze Reaktionen wie 'Kenne ich nicht' oder 'Verstehe ich nicht' sind eine "
    "Bitte, das zuletzt besprochene Thema einfacher zu erklaeren. Formuliere "
    "daraus eine konkrete eigenstaendige Erklaerungsfrage zum Thema der "
    "letzten fachlichen Nutzerfrage; wiederhole nicht bloss die Reaktion. "
    "Ist die Frage bereits eigenstaendig, gib sie unveraendert zurueck. "
    "Gib ausschliesslich die umformulierte Frage aus, ohne Erklaerung."
)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(p for p in parts if p).strip()
    return str(content or "").strip()


def _latest_user_query(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            query = _text_content(message.get("content"))
            if query:
                return query
    raise HTTPException(status_code=400, detail="No user message found.")


def _chat_turns(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _text_content(message.get("content"))
        if text:
            turns.append((role, text))
    return turns


def _needs_query_rewrite(query: str) -> bool:
    """Recognize references/ellipses; a new explicit topic needs no LLM hop."""
    text = query.casefold().strip()
    if _nonunderstanding_reply(query):
        return True
    if re.search(r"\b(?:er|es|sie|ihn|ihm|ihnen|dies(?:e|er|es|en|em)?|dazu|dabei|davon|damit|dafuer|dafür|dort|dann|solche[rsnm]?)\b", text):
        return True
    if re.match(r"(?:und|oder|aber)\b", text):
        return True
    if re.search(r"\b(?:das|die|der)\s*(?:denn|genau|eigentlich|auch|bitte)?\s*[?.!]*$", text):
        return True
    if re.search(r"\b(?:das|die|den|der)\s+(?:(?:aber|doch|ja|schon)\s+)?(?:kennen|wissen|meinen|zeigen|beschreiben|erklaeren|erklären|verstehen)\b", text):
        return True
    if re.match(r"(?:kenne|verstehe|versteh)\s+ich\s+nicht\b", text):
        return True
    return bool(re.fullmatch(r"(?:warum|wieso|weshalb|wann|wo|wie(?:\s+(?:lange|oft|viel|genau))?)\s*[?.!]*", text))


def _nonunderstanding_reply(query):
    text = query.casefold().strip().strip(" .!?")
    return bool(re.fullmatch(
        r"(?:(?:das|es)\s+)?(?:kenne|kenn|verstehe|versteh)\s+ich\s+(?:(?:leider|noch|gar|überhaupt)\s+)?nicht|"
        r"ich\s+(?:kenne|kenn|verstehe|versteh)\s+(?:(?:das|es)\s+)?(?:(?:leider|noch|gar|überhaupt)\s+)?nicht", text))


def _rewrite_fallback(turns, query):
    """Resolve an unchanged non-understanding reply from actual user history."""
    if not _nonunderstanding_reply(query):
        return query
    for role, previous in reversed(turns):
        if role != "user" or _needs_query_rewrite(previous):
            continue
        if re.match(r"\s*(?:was|wie|wann|wo|warum|wieso|weshalb|welch\w*|wer|wieviel\w*|erklär\w*|erklaer\w*|beschreib\w*)\b", previous, re.I):
            return "Erkläre die Antwort auf diese Frage einfacher: " + previous.strip()
    return query


def _rewrite_query(config: dict[str, Any], messages: list[dict[str, Any]], query: str) -> str:
    """Folgefragen anhand des Verlaufs zu eigenstaendigen Fragen umformulieren.

    Nur aktiv, wenn es ueberhaupt Verlauf gibt (erste Frage eines Chats geht
    ohne LLM-Roundtrip direkt ins Retrieval). Bei Fehlern oder unplausiblem
    Ergebnis wird auf die Originalfrage zurueckgefallen.
    """
    if not config.get("enable_query_rewrite", True):
        return query
    turns = _chat_turns(messages)
    if sum(1 for role, _ in turns if role == "user") < 2:
        return query
    if config.get("rewrite_dependent_only", True) and not _needs_query_rewrite(query):
        return query

    # Verlauf ohne die aktuelle (letzte) Nutzerfrage.
    for i in range(len(turns) - 1, -1, -1):
        if turns[i][0] == "user":
            del turns[i:]
            break
    limit = int(config.get("rewrite_max_history", 6))
    lines = [
        f"{'Nutzer' if role == 'user' else 'Assistent'}: {text[:500]}"
        for role, text in turns[-limit:]
    ]
    user_prompt = (
        "Gespraechsverlauf:\n" + "\n".join(lines) + f"\n\nLetzte Frage: {query}\n\nEigenstaendige Frage:"
    )
    rewrite_config = dict(config)
    rewrite_config["llm_thinking"] = False
    rewrite_config["llm_max_tokens"] = int(config.get("rewrite_max_tokens", 120))
    rewrite_config["llm_temperature"] = 0.0
    rewrite_config["llm_timeout"] = float(config.get("rewrite_timeout", 20))
    # Answer/verifier schemas do not belong to the plain-text rewrite call.
    rewrite_config.pop("llm_response_format", None)
    started = time.perf_counter()
    try:
        raw = call_llm(rewrite_config, REWRITE_SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001 — Rewrite ist optional, Original bleibt nutzbar
        logger.warning("Query-Rewrite fehlgeschlagen, Originalfrage wird genutzt: %s", exc)
        return _rewrite_fallback(turns, query)
    rewritten = _clean_rewrite(raw)
    if not rewritten or len(rewritten) > 500:
        logger.info("Query-Rewrite verworfen (roh=%r), Originalfrage wird genutzt", raw[:200])
        return _rewrite_fallback(turns, query)
    if rewritten.casefold().strip(" .!?") == query.casefold().strip(" .!?"):
        rewritten = _rewrite_fallback(turns, query)
    logger.info("Query-Rewrite: seconds=%.3f %r -> %r", time.perf_counter() - started, query, rewritten)
    return rewritten


def _clean_rewrite(raw: str) -> str:
    """Erste brauchbare Zeile der Modellausgabe als eigenstaendige Frage.

    Das Modell haengt gelegentlich Erlaeuterungen oder ein Label an; solche
    Ausgaben fuehrten frueher zum stillen Rueckfall auf die Originalfrage.
    """
    for line in raw.splitlines():
        line = line.strip().strip('"').strip("'").strip()
        if not line:
            continue
        line = re.sub(r"^(eigenst(ae|ä)ndige\s+frage|frage)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip().strip('"')
        if line:
            return line
    return ""


def _check_auth(config: dict[str, Any], authorization: str | None) -> None:
    """Optionaler Bearer-Schutz: aktiv, sobald ``api_key`` gesetzt ist."""
    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        return
    if authorization != f"Bearer {api_key}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


_state_lock = RLock()


@lru_cache(maxsize=1)
def _cached_state() -> tuple[dict[str, Any], Retriever, str, str]:
    config = load_config()
    retriever = Retriever(config, use_reranker=True)
    system_prompt, template = load_prompts(config)
    return config, retriever, system_prompt, template


def _state() -> tuple[dict[str, Any], Retriever, str, str]:
    # lru_cache schuetzt seine Tabelle, aber nicht gleichzeitige Cache-Misses.
    # Bereitschaftsabfragen duerfen den grossen Suchbestand nur einmal laden.
    with _state_lock:
        return _cached_state()


def _rebuild_state() -> None:
    """Cache leeren und sofort neu aufbauen (eager, nicht lazy).

    Der Watcher wertet erst den 2xx dieser Route als "Refresh bestaetigt";
    ein blosses cache_clear() wuerde den mehrsekuendigen Reload in den
    naechsten Chat-Request verschieben und Fehler dort statt beim Watcher
    sichtbar machen (Spec 6.10 A).
    """
    with _state_lock:
        _cached_state.cache_clear()
        _cached_state()


def _model_id(config: dict[str, Any]) -> str:
    return str(config.get("api_model") or "db-richtlinien-rag")


def _comparison_models(config: dict[str, Any]) -> dict[str, bool]:
    """Nur konfigurierte Vergleichsvarianten duerfen den Graphen umschalten."""
    return {str(name): bool(enabled) for name, enabled in config.get("comparison_models", {}).items()}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    config, retriever, _, _ = _state()
    collection = config["collection_name"]
    exists = retriever.qdrant.collection_exists(collection)
    if not exists:
        raise HTTPException(status_code=503, detail=f"Qdrant collection missing: {collection}")
    if _comparison_models(config):
        # Der grosse Head-Kaltstart darf nicht schon wegen vorhandener Vektoren
        # als betriebsbereit erscheinen. Nur das konfigurierte Modell gilt.
        try:
            request = urllib.request.Request(
                config["llm_base_url"].rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {config.get('llm_api_key', 'local')}"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                available = json.load(response)
            if config["llm_model"] not in {model["id"] for model in available["data"]}:
                raise ValueError("Konfiguriertes Modell fehlt.")
        except Exception:
            raise HTTPException(status_code=503, detail="Vergleichsmodell noch nicht betriebsbereit.") from None
    return {
        "status": "ready",
        "model": _model_id(config),
        "collection": collection,
        "product_path": config.get("product_path", "rag_api"),
    }


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    config, _, _, _ = _state()
    _check_auth(config, authorization)
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": now,
                "owned_by": "db-richtlinien-ki",
            } for model_id in (_comparison_models(config) or {_model_id(config): False})
        ],
    }


def _completion_payload(answer: str, request_model: str, completion_id: str) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _generate_checked(config,system_prompt,user_prompt,query,sources,generate,retry_context=None):
    clipped_retry_used=False
    def draft_for(prompt):
        nonlocal clipped_retry_used
        try:return generate(config,system_prompt,prompt)
        except TruncatedGenerationError:
            if clipped_retry_used:raise
            clipped_retry_used=True
            logger.warning('Output-token limit reached; one concise retry on the same admitted evidence')
            shorter=dict(config,llm_thinking=False,llm_max_tokens=min(600,int(config.get('llm_max_tokens',1200))))
            instruction='\nAntworte jetzt in höchstens drei kurzen Sätzen. Keine Tabelle. Nenne den gefragten Aspekt und seine wesentlichen Bedingungen. Belege jeden Satz mit dem exakten Quellenlocator.'
            return generate(shorter,system_prompt+instruction,prompt)
    def checked(draft,current_sources,allow_repair=True):
        observer=answer_observer if config.get('enable_nli_shadow',False) else None
        job=observe_start(observer,draft,query,current_sources)
        checks = dict(config,llm_thinking=False) if config.get("llm_thinking") else config
        try:value=check_answer(draft,query,current_sources,checks,llm=call_llm,allow_repair=allow_repair)
        except Exception as exc:
            observe_finish(observer,job,error=exc);raise
        observe_finish(observer,job,answer=value)
        return value
    literal=literal_comparison(query,sources,source_quote_resolver)
    if literal is not None:
        logger.info('Comparing independently resolved literal original definitions; mandatory checks follow')
        proposal=checked(literal,sources,allow_repair=False)
        if proposal!=ABSTENTION and not str(proposal).strip().endswith('Weitere Aussagen sind im bereitgestellten Kontext nicht ausreichend belegt.'):
            return render_evidence_table(proposal,config,source_quote_resolver)
    draft=draft_for(user_prompt)
    answer=checked(draft,sources)
    if answer!=ABSTENTION or retry_context is None or not config.get('enable_evidence_check',True):return render_evidence_table(answer,config,source_quote_resolver)
    logger.info('Native abstention; checking for additional original raster evidence')
    retry=retry_context()
    if retry is None:return render_evidence_table(answer,config,source_quote_resolver)
    prompt2,sources2=retry
    draft2=draft_for(prompt2)
    # Exactly one new draft after additional evidence; no repair/OCR loop.
    return render_evidence_table(checked(draft2,sources2,allow_repair=False),config,source_quote_resolver)


def _stream_payload(
    config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    request_model: str,
    completion_id: str,
    query: str = "",
    sources: dict[str, str] | None = None,
    no_evidence: bool = False,
    retry_context=None,
    request_context=None,
) -> Iterator[str]:
    """SSE output; with evidence checking, never expose unchecked token deltas."""
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request_model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield chunk({"role": "assistant"})
    try:
        if no_evidence:
            yield chunk({"content": ABSTENTION})
        elif config.get("enable_evidence_check", True):
            # Never stream unverified claims. Keep SSE alive while generation
            # and the evidence pass run in a worker; release only the checked text.
            from concurrent.futures import ThreadPoolExecutor, TimeoutError
            def generate_checked():
                return _generate_checked(config,system_prompt,user_prompt,query,sources or {},
                    lambda c,s,p: ''.join(stream_llm(c,s,p)),retry_context)
            with ThreadPoolExecutor(max_workers=1) as worker:
                future = worker.submit(request_context.run, generate_checked) if request_context is not None else worker.submit(generate_checked)
                while True:
                    try:
                        answer = future.result(timeout=10)
                        break
                    except TimeoutError:
                        if future.done():
                            raise
                        yield ": evidence-check pending\n\n"
                yield chunk({"content": answer})
        else:
            for delta_text in stream_llm(config, system_prompt, user_prompt):
                yield chunk({"content": delta_text})
    except Exception as exc:  # Abbruch sichtbar machen statt stumm zu enden
        yield chunk({"content": f"\n\n[Generierung abgebrochen: {exc}]"})
    yield chunk({}, "stop")
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any], authorization: str | None = Header(default=None)):
    config, retriever, system_prompt, template = _state()
    _check_auth(config, authorization)
    if request_retriever_resolver is not None:
        retriever = request_retriever_resolver(payload, retriever)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list.")

    query = _latest_user_query(messages)
    request_model = str(payload.get("model") or _model_id(config))
    variants = _comparison_models(config)
    if variants and request_model not in variants:
        raise HTTPException(status_code=400, detail="Unbekannte Vergleichsvariante.")

    def _prepare():
        started = time.perf_counter()
        # Only the authenticated Tail wrapper opens this request scope.
        request_config = text_settings(select_reader(config), visual_enabled())
        used_query = _rewrite_query(request_config, messages, query)
        rewritten_at = time.perf_counter()
        result = retriever.retrieve(used_query, use_graph=variants[request_model]) if variants else retriever.retrieve(used_query)
        passages=result['passages']
        if source_text_resolver is not None:
            identity=lambda rows:[(p.source_file,p.page,p.locator) for p in rows]
            original_identity=identity(passages)
            passages=source_text_resolver(passages,used_query,request_config)
            if identity(passages)!=original_identity:raise ValueError('Original text adapter changed source identity')
        retrieved_at = time.perf_counter()
        user_prompt, sources, context_info = prepare_rag_prompt(passages, request_config, system_prompt, template, used_query)
        retry=prepare_context_retry(passages,sources,used_query,request_config,system_prompt,template,source_enricher)
        logger.info("RAG prepared: rewrite_seconds=%.3f retrieval_seconds=%.3f context_chars=%s prompt_tokens=%s context_reductions=%s source_count=%s hits=%s", rewritten_at - started, retrieved_at - rewritten_at, context_info["context_chars"], context_info["prompt_tokens"], context_info["context_reductions"], len(sources), len(result["hits"]))
        return user_prompt, used_query, sources, not result["hits"] or not sources, request_config,retry

    # Retrieval/Rewrite blockieren (Netzwerk + CPU) -> raus aus dem Event-Loop.
    try:
        user_prompt, used_query, sources, no_evidence, request_config,retry_context = await run_in_threadpool(_prepare)
    except ReaderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ContextWindowError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except ModelResponseError:
        raise HTTPException(status_code=502, detail="Die Modellvorbereitung konnte nicht abgeschlossen werden. Bitte versuche es erneut.") from None

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if payload.get("stream"):
        return StreamingResponse(
            _stream_payload(request_config, system_prompt, user_prompt, request_model, completion_id,
                            used_query, sources, no_evidence,retry_context,copy_context()),
            media_type="text/event-stream",
        )
    def generate_checked():
        if no_evidence:
            return ABSTENTION
        return _generate_checked(request_config,system_prompt,user_prompt,used_query,sources,call_llm,retry_context)
    try:
        answer = await run_in_threadpool(generate_checked)
    except ContextWindowError as exc:
        raise HTTPException(status_code=413,detail=str(exc)) from None
    except ModelResponseError:
        raise HTTPException(status_code=502, detail="Das Sprachmodell konnte die Anfrage nicht beantworten. Bitte versuche es erneut.") from None
    return JSONResponse(_completion_payload(answer, request_model, completion_id))


@app.get("/vergleich/status")
def comparison_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    config, retriever, _, _ = _state()
    _check_auth(config, authorization)
    return {"model": config.get("llm_model"), "variants": _comparison_models(config),
            "pages": len(retriever.by_id),
            "graph": retriever.reference_graph.stats if retriever.reference_graph else None}


@app.post("/admin/reload")
async def admin_reload(authorization: str | None = Header(default=None)):
    config = load_config()
    _check_auth(config, authorization)
    try:
        await run_in_threadpool(_rebuild_state)
    except Exception as exc:  # noqa: BLE001 — Fehlerdetail gehoert zum Watcher-Vertrag
        raise HTTPException(status_code=500, detail=f"Reload fehlgeschlagen: {exc}")
    return JSONResponse({"status": "reloaded"})
