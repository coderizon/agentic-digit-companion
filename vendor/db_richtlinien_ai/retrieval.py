"""Hybrider, quellenbasierter Retrieval-Kern fuer DB-Richtlinien.

Pipeline pro Anfrage:
  1. Query-Expansion (Domaenen-Synonyme/Abkuerzungen)
  2. Vektorsuche (auf Tail TopK MaxSim) + BM25-Retrieval (lokaler Index)
  3. Reciprocal-Rank-Fusion der beiden Kandidatenlisten
  4. Cross-Encoder-Reranking (bge-reranker-v2-m3)
  5. Relevanz-Schwellwert
  6. Nachbarseiten-Anreicherung (Seite +/- n im selben Dokument)

Der Kern ist bewusst frei von CLI/LLM-Logik, damit er von ask.py und vom
Evaluations-Skript identisch genutzt werden kann.
"""

from __future__ import annotations

import gzip
import os
import json
import re
import subprocess
import threading
from collections import OrderedDict, defaultdict
from copy import copy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_richtlinien_ai.pdf_pages import read_jsonl
from db_richtlinien_ai.doctypes import DocTypeIndex
from db_richtlinien_ai.versions import VersionIndex
from db_richtlinien_ai.text import clean_extracted_text, tokenize
from db_richtlinien_ai.rerank_excerpt import rerank_excerpt
from db_richtlinien_ai.document_structure import StructureIndex
from db_richtlinien_ai.comparison_evidence import has_subject_heading
from db_richtlinien_ai.query_intent import (requested_rules,record_matches_rules,
                                           rule_question,linguistic_hints,quantity_compounds,semantic_topic,comparison_subjects)

ROOT = Path(__file__).resolve().parents[2]

ENV_OVERRIDES = {
    "DBR_QDRANT_URL": "qdrant_url",
    "DBR_EMBEDDING_BASE_URL": "embedding_base_url",
    "DBR_EMBEDDING_API_KEY": "embedding_api_key",
    "DBR_RERANKER_BASE_URL": "reranker_base_url",
    "DBR_RERANKER_API_KEY": "reranker_api_key",
    "DBR_LLM_BASE_URL": "llm_base_url",
    "DBR_LLM_MODEL": "llm_model",
    "DBR_RAG_API_MODEL": "api_model",
    "DBR_RAG_API_KEY": "api_key",
    # Prompt-Varianten fuer A/B-Messungen ohne Aenderung an rag.json
    "DBR_SYSTEM_PROMPT": "system_prompt",
    "DBR_RAG_TEMPLATE": "rag_template",
}


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or _resolve(os.environ.get("DBR_CONFIG", "config/rag.json"))
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    for env_key, config_key in ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value:
            config[config_key] = value
    return config


@dataclass
class Passage:
    key: str
    source_file: str
    page: int
    text: str
    locator: str
    score: float = 0.0
    kind: str = "hit"  # "hit" oder "neighbor"
    signals: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Query-Expansion
# --------------------------------------------------------------------------- #
class QueryExpander:
    def __init__(self, expansion_path: Path | None):
        self.groups: list[dict[str, Any]] = []
        if expansion_path and expansion_path.exists():
            data = json.loads(expansion_path.read_text(encoding="utf-8"))
            self.groups = data.get("groups", [])

    @staticmethod
    def _fires(tokens: set[str], trigger: str) -> bool:
        """Trigger zuendet bei exaktem Token oder als Praefix eines Kompositums.

        Bindestrich-Komposita ("ist-kabelschrank") werden in ihre Teile zerlegt.
        Bewusst kein Teilstring-Vergleich: "erden" wuerde sonst in "werden"
        zuenden und jede Frage mit "werden" um Erdungsbegriffe erweitern.
        """
        parts: set[str] = set()
        for tok in tokens:
            parts.add(tok)
            parts.update(p for p in tok.split("-") if p)
        if trigger in parts:
            return True
        if len(trigger) >= 4:
            return any(part.startswith(trigger) for part in parts)
        return False

    def expand(self, query: str) -> tuple[str, list[str]]:
        """Gibt (erweiterte_query, hinzugefuegte_begriffe) zurueck."""
        tokens = set(tokenize(query))
        added: list[str] = []
        for group in self.groups:
            triggers = [t.casefold() for t in group.get("triggers", [])]
            if any(self._fires(tokens, t) for t in triggers):
                for term in group.get("expansions", []):
                    if term.casefold() not in {a.casefold() for a in added}:
                        added.append(term)
        if not added:
            return query, []
        expanded = query + " " + " ".join(added)
        return expanded, added

    def related_parent_queries(self,query):
        """One alternative search view for component selection by a parameter.

        It is not a synonym substitution in the user's question or evidence.
        The original dense search, reranker question and factual checks stay.
        """
        if not re.search(r'\bwelch\w*\b',query,re.I) or not re.search(r'\d',query):return []
        for group in self.groups:
            if group.get('relation')!='component_parent' or not group.get('expansions'):continue
            for trigger in group.get('triggers',[]):
                pattern=r'\b'+re.escape(trigger)+r'(?:e|en|n|s|es)?\b'
                if re.search(pattern,query,re.I):
                    selection=re.split(r'[?.]\s+(?=(?:Aus\s+was|Woraus)\b)',query,flags=re.I)[0]
                    alternative=re.sub(pattern,str(group['expansions'][0]),selection,flags=re.I)
                    alternative=semantic_topic(alternative)
                    # A short parent/parameter view finds the system's own
                    # selection rules without making the component its synonym.
                    # All situational qualifiers remain in the original query.
                    parameters=re.findall(r'\d+(?:[,.]\d+)?\s*(?:km\s*/\s*h|mm|cm|m|kV|V|N)?\b',alternative,re.I)
                    if parameters:alternative=str(group['expansions'][0])+' '+' '.join(parameters)
                    if alternative!=query:return [alternative]
        return []


# --------------------------------------------------------------------------- #
# BM25 (lokaler Index aus build_bm25_index.py)
# --------------------------------------------------------------------------- #
class Bm25Index:
    def __init__(self, index_path: Path):
        with gzip.open(index_path, "rt", encoding="utf-8") as handle:
            self.index = json.load(handle)
        self.documents = self.index["documents"]
        self.avg_doc_length = self.index["avg_doc_length"]

    def search(self, query: str, top_k: int, k1: float = 1.5, b: float = 0.75, *, allowed_ids=None) -> list[tuple[str, float]]:
        scores: dict[int, float] = defaultdict(float)
        query_terms = set(tokenize(query))
        postings = self.index["postings"]
        # Technical identifiers retain German plural spellings in the index
        # (BTSen, MSCs). Nominate those existing forms without changing the
        # original question or admitting an unverified source.
        for identifier in re.findall(r'(?<!\w)[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*(?!\w)',query)[:12]:
            if not 2<=len(identifier)<=16:continue
            for suffix in ('en','s','n'):
                variant=identifier.casefold()+suffix
                if 0<len(postings.get(variant,[]))<=128:query_terms.add(variant)
        # Keep the full object and also search its alphabetic components. The
        # original query and the semantic reranker still decide relevance.
        for term in list(query_terms):
            parts=term.split('-')
            if len(parts)>1 and all(part.isalpha() and len(part)>=2 for part in parts):
                query_terms.update(parts)
        # Long whole technical nouns often differ only by German inflection.
        # Nominate existing finite word forms; never strip the object to a
        # prefix or force these candidates past semantic reranking.
        for term in sorted(query_terms)[:64]:
            if not term.isalpha() or len(term)<8:continue
            stem=term
            for ending in ('ern','en','em','er','es','e','n','s'):
                if stem.endswith(ending) and len(stem)-len(ending)>=6:
                    stem=stem[:-len(ending)];break
            query_terms.update(variant for ending in ('','e','en','em','er','es','n','s')
                               if (variant:=stem+ending) in postings)
        idf = self.index["idf"]
        for term in query_terms:
            term_postings = postings.get(term)
            if not term_postings:
                continue
            term_idf = idf[term]
            for doc_id, term_frequency in term_postings:
                if allowed_ids is not None and self.documents[doc_id]['id'] not in allowed_ids:continue
                doc_length = self.documents[doc_id]["token_count"] or 1
                denom = term_frequency + k1 * (1 - b + b * doc_length / self.avg_doc_length)
                scores[doc_id] += term_idf * (term_frequency * (k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self.documents[doc_id]["id"], score) for doc_id, score in ranked]


def decompose_query(query: str, max_subs: int = 5) -> list[str]:
    """Zerlegt eine mehrteilige Verbundfrage in Teilfragen.

    Rein regelbasiert (kein LLM): Split an Frage-/Zeilengrenzen, kurze
    Fragmente verworfen. Die volle Frage bleibt immer erste Teilfrage.
    """
    subs = [query]
    # A comparison needs evidence for each named subject even when no page
    # contains the literal phrase "Unterschied zwischen ...". These are
    # search views only; generation and every source check keep the full query.
    subs.extend('Was ist '+value+'?' for value in comparison_subjects(query))
    for part in re.split(r"[?\n]+|\.\s+(?=(?:Aus\s+was|Woraus|Welche\w*|Wie|Was|Wofür)\b)", query):
        part = part.strip(" \t-•:;,")
        if len(part.split()) >= 3 and all(part.strip(' .?!').casefold()!=old.strip(' .?!').casefold() for old in subs):
            subs.append(part)
    return subs[:max_subs]


def comparison_coverage(scored,query,views,records,keep,threshold,minimum):
    """Reserve eligible explicit subject definitions, never bypass relevance."""
    eligible=[(key,score) for key,score in scored if score>=threshold]
    protected=[]
    for subject in comparison_subjects(query):
        scores=views.get('Was ist '+subject+'?',{})
        candidates=[(key,score) for key,score in eligible if scores.get(key,0)>=minimum
                    and has_subject_heading(records[key]['text'],subject)]
        if candidates:
            best=max(candidates,key=lambda row:(scores[row[0]],row[1]))
            if best[0] not in {k for k,_ in protected}:protected.append(best)
    return list(dict(protected+eligible).items())[:keep]


# --------------------------------------------------------------------------- #
# Retriever
# --------------------------------------------------------------------------- #
class Retriever:
    topk = None
    structure=None
    def __init__(self, config: dict[str, Any] | None = None, *, use_reranker: bool = True):
        self.config = config or load_config()
        self.use_reranker = use_reranker
        if self.config.get("text_retrieval_backend") == "topk":
            from db_richtlinien_ai.topk import TopKClient
            self.topk = TopKClient(self.config)
            self.topk.verify_source(_resolve(self.config["source_jsonl"]))

        # Seiten laden + Lookups bauen (Extraktions-Steuerzeichen bereinigen)
        pages = read_jsonl(_resolve(self.config["source_jsonl"]))
        for p in pages:
            p["text"] = clean_extracted_text(p.get("text", ""))
        self.by_id: dict[str, dict] = {p["id"]: p for p in pages}
        self.by_file_page: dict[tuple[str, int], dict] = {
            (p["source_file"], int(p["page"])): p for p in pages
        }

        # Versionslogik (Dubletten / aktuelle Fassung); ohne Artefakt neutral.
        groups_path = self.config.get("page_groups")
        self.versions = VersionIndex.load(_resolve(groups_path) if groups_path else None)
        self.by_locator: dict[str, dict] = {f"{p['source_file']}, Seite {int(p['page'])}": p for p in pages}
        # Dokumenttypen (Richtlinie vor Fachbuch vor Fragenkatalog); ohne Artefakt neutral.
        types_path = self.config.get("doc_types")
        self.doctypes = DocTypeIndex.load(
            _resolve(types_path) if types_path else None, self.config.get("doc_type_weights")
        )

        self.expander = QueryExpander(_resolve(self.config.get("query_expansion", "")) if self.config.get("query_expansion") else None)
        self.bm25 = Bm25Index(_resolve(self.config["bm25_index"]))
        self.structure=StructureIndex.from_config(self.config,ROOT)
        self.reference_graph = None
        if self.config.get("reference_graph"):
            from db_richtlinien_ai.reference_graph import ReferenceGraph

            self.reference_graph = ReferenceGraph.load(_resolve(self.config["reference_graph"]), self.by_id)

        # Modelle lazy laden; Lock schuetzt die Initialisierung, weil das
        # Fan-out in retrieve() mehrere Threads gleichzeitig hierher fuehrt.
        self._init_lock = threading.Lock()
        self._embedder = None
        self._reranker = None
        self._qdrant = None
        self._pool: ThreadPoolExecutor | None = None

        # Kleiner LRU-Cache fuer Query-Embeddings (wiederkehrende Fragen,
        # identische Subqueries) — spart den Roundtrip zum Embedding-Dienst.
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embed_cache_lock = threading.Lock()

        # Persistenter Sparse-Worker (nur bei enable_sparse relevant).
        self._version_signals: dict[str, dict[str, Any]] = {}
        self._sparse_proc: subprocess.Popen | None = None
        self._sparse_lock = threading.Lock()
        self._symbol_cache: OrderedDict[str, list[str]] = OrderedDict()
        self._symbol_lock = threading.Lock()

    def _symbolic_candidates(self, query: str, limit: int = 12) -> list[tuple[str,float]]:
        """Bounded literal nominations for notation lost by word tokenization.

        Only letters-plus-integer identifiers, at most two per question. These
        are proposals, never source admission; normal reranking/thresholds apply.
        The small cache stores index keys, not answers or generated knowledge.
        """
        atoms=list(dict.fromkeys(re.findall(r'(?<!\w)([A-Za-z]{1,3})\s*\+\s*(\d{1,3})(?!\w)',query)))[:2]
        if not atoms:return []
        keys=set()
        for prefix,number in atoms:
            atom=prefix.casefold()+'+'+number
            with self._symbol_lock:
                cached=self._symbol_cache.get(atom)
            if cached is None:
                pattern=re.compile(r'(?<!\w)'+re.escape(prefix)+r'\s*\+\s*'+re.escape(number)+r'(?!\w)',re.I)
                cached=[key for key,rec in self.by_id.items() if pattern.search(rec.get('text',''))]
                # Common formula fragments do not form a useful rare channel.
                if len(cached)>128:cached=[]
                with self._symbol_lock:
                    self._symbol_cache[atom]=cached
                    while len(self._symbol_cache)>32:self._symbol_cache.popitem(last=False)
            keys.update(cached)
        terms={term for term in tokenize(query) if len(term)>=4}
        ranked=sorted(keys,key=lambda key:(-len(terms&set(tokenize(self.by_id[key]['text']))),key))[:limit]
        return [(key,1.0/(index+1)) for index,key in enumerate(ranked)]

    def _acronym_plural_candidates(self,query,limit=8):
        """Keep rare complete identifier forms from being lost in broad BM25.

        A source with the sought acronym can otherwise rank below hundreds of
        pages matching a common measurement word. These are only nominations;
        the original query, semantic thresholds and evidence checks stay intact.
        """
        index=self.bm25.index;keys=set()
        for identifier in re.findall(r'(?<!\w)[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*(?!\w)',query)[:12]:
            if not 2<=len(identifier)<=16:continue
            for suffix in ('en','s','n'):
                values=index['postings'].get(identifier.casefold()+suffix,[])
                if not 0<len(values)<=128:continue
                for number,_ in values:
                    if 0<=number<len(index['documents']):
                        key=index['documents'][number]['id']
                        if key in self.by_id:keys.add(key)
        ranked=sorted(keys)[:limit]
        return [(key,1.0/(i+1)) for i,key in enumerate(ranked)]

    # ----- lazy components -------------------------------------------------- #
    @property
    def embedder(self):
        if self._embedder is None:
            with self._init_lock:
                if self._embedder is None:
                    from db_richtlinien_ai.embeddings import load_embedder

                    # local (sentence-transformers) oder geteilter vllm-embedding-
                    # Dienst, je nach config["embedding_engine"]. Identischer Pfad
                    # wie der Ingest.
                    self._embedder = load_embedder(self.config)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            with self._init_lock:
                if self._reranker is None:
                    from db_richtlinien_ai.reranking import load_reranker

                    # local (CrossEncoder im Prozess) oder geteilter vllm-reranker-
                    # Dienst, je nach config["reranker_engine"].
                    self._reranker = load_reranker(self.config)
        return self._reranker

    @property
    def qdrant(self):
        if self._qdrant is None:
            with self._init_lock:
                if self._qdrant is None:
                    from qdrant_client import QdrantClient

                    self._qdrant = QdrantClient(url=self.config["qdrant_url"], timeout=120)
        return self._qdrant

    @property
    def pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            with self._init_lock:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(
                        max_workers=int(self.config.get("retrieval_parallelism", 8)),
                        thread_name_prefix="retrieval",
                    )
        return self._pool

    # ----- retrieval steps -------------------------------------------------- #
    def _embed_query(self, query: str) -> list[float]:
        with self._embed_cache_lock:
            cached = self._embed_cache.get(query)
            if cached is not None:
                self._embed_cache.move_to_end(query)
                return cached
        vector = self.embedder.encode_one(query)
        max_size = int(self.config.get("embed_cache_size", 256))
        with self._embed_cache_lock:
            self._embed_cache[query] = vector
            self._embed_cache.move_to_end(query)
            while len(self._embed_cache) > max_size:
                self._embed_cache.popitem(last=False)
        return vector

    def _scope_filter(self):
        sources = getattr(self, 'search_sources', None)
        if sources is None:
            return {}
        from qdrant_client.models import FieldCondition, Filter, MatchAny
        return {'query_filter': Filter(must=[
            FieldCondition(key='source_file', match=MatchAny(any=sorted(sources)))])}

    def _dense(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if self.topk is not None:
            return self.topk.search(self, query, "pages", top_k)
        if getattr(self, 'search_sources', None) == frozenset():
            return []
        vector = self._embed_query(query)
        out={}
        collections=[self.config["collection_name"],*getattr(self,'additional_collections',())]
        for collection in collections:
            points=self.qdrant.query_points(collection_name=collection,query=vector,
                limit=top_k,with_payload=True,**self._scope_filter()).points
            for point in points:
                payload=point.payload or {}
                key=payload.get('record_id') or payload.get('id')
                if key:out[str(key)]=max(float(point.score),out.get(str(key),float('-inf')))
        return sorted(out.items(),key=lambda pair:pair[1],reverse=True)[:top_k]

    def _structure_candidates(self,query,limit=12):
        if self.structure is None:return []
        out=[];seen=set()
        for unit,score in self.structure.ranked_units(query,limit*4):
            rec=self.by_file_page.get((unit['source_file'],unit['page']))
            if rec and rec['id'] not in seen:out.append((rec['id'],score));seen.add(rec['id'])
            if len(out)>=limit:break
        return out

    def _structure_dense(self,query,limit=12):
        if getattr(self, 'search_sources', None) == frozenset():return []
        if self.structure is None or not self.config.get('structure_collection'):return []
        if self.topk is not None:
            return self.topk.search(self,query,"structure",limit)
        from db_richtlinien_ai.incremental import embedding_signature
        vector=self._embed_query(query);signature=embedding_signature(self.config,len(vector))
        points=self.qdrant.query_points(collection_name=self.config['structure_collection'],query=vector,
            limit=limit*3,with_payload=True,**self._scope_filter()).points
        out=[];seen=set()
        for point in points:
            data=point.payload or {}
            if data.get('structure_sha256')!=self.structure.sha256 or data.get('embedding_signature')!=signature:
                raise ValueError('Structured vector index identity mismatch')
            refs=data.get('unit_ids')
            if not isinstance(refs,list) or not refs or any(uid not in self.structure.by_id for uid in refs):raise ValueError('Unknown structured vector unit')
            unit=self.structure.by_id[refs[0]]
            if any(data.get(k)!=unit[k] for k in ['source_file','source_sha256','page']):raise ValueError('Structured vector changed original source')
            rec=self.by_file_page.get((unit['source_file'],unit['page']))
            if rec and rec['id'] not in seen:out.append((rec['id'],float(point.score)));seen.add(rec['id'])
            if len(out)>=limit:break
        return out

    def _sparse_worker(self) -> subprocess.Popen:
        """Persistenter Sparse-Worker-Prozess (``sparse_bgem3.py --serve``).

        FlagEmbedding bleibt aus dem Produktions-venv heraus; frueher lief pro
        Query ein frischer Subprocess, der das Modell jedes Mal neu geladen
        hat (Sekunden Latenz). Der Worker haelt das Modell geladen und
        beantwortet Requests zeilenweise (JSON rein, JSON raus).
        Konfigurierbar ueber ``sparse_python`` / ``sparse_helper`` in rag.json.
        """
        if self._sparse_proc is None or self._sparse_proc.poll() is not None:
            py = _resolve(self.config.get("sparse_python", ".venv_sparse/bin/python"))
            helper = _resolve(self.config.get("sparse_helper", "scripts/sparse_bgem3.py"))
            self._sparse_proc = subprocess.Popen(
                [str(py), str(helper), "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        return self._sparse_proc

    def _encode_query_sparse(self, query: str) -> dict[str, list]:
        """Query-Sparse-Vektor (bge-m3 lexical weights) via Worker-Prozess."""
        with self._sparse_lock:
            for attempt in (1, 2):  # bei totem Worker einmal neu starten
                proc = self._sparse_worker()
                try:
                    proc.stdin.write(json.dumps({"texts": [query]}) + "\n")
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                except (BrokenPipeError, OSError):
                    line = ""
                if line:
                    break
                proc.kill()
                self._sparse_proc = None
                if attempt == 2:
                    raise RuntimeError("Sparse-Encoder-Worker liefert keine Antwort.")
        data = json.loads(line)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Sparse-Encoder fehlgeschlagen: {data['error']}")
        return data[0]

    def _sparse(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if getattr(self, 'search_sources', None) == frozenset():
            return []
        from qdrant_client.models import SparseVector

        vec = self._encode_query_sparse(query)
        if not vec.get("indices"):
            return []
        points = self.qdrant.query_points(
            collection_name=self.config.get(
                "sparse_collection", self.config["collection_name"] + "_sparse"
            ),
            query=SparseVector(indices=vec["indices"], values=vec["values"]),
            using="sparse",
            **self._scope_filter(),
            limit=top_k,
            with_payload=True,
        ).points
        out: list[tuple[str, float]] = []
        for p in points:
            payload = p.payload or {}
            key = payload.get("record_id") or payload.get("id")
            if key:
                out.append((str(key), float(p.score)))
        return out

    def decompose(self, query: str) -> list[str]:
        """Teilfragen fuer die Kandidatengewinnung (Recall); das Reranking
        laeuft danach weiter auf der vollen Frage (Praezision)."""
        return decompose_query(query, int(self.config.get("max_subqueries", 5)))

    @staticmethod
    def _rrf(rankings: list[list[tuple[str, float]]], rrf_k: int) -> dict[str, float]:
        fused: dict[str, float] = defaultdict(float)
        for ranking in rankings:
            for rank, (key, _score) in enumerate(ranking, start=1):
                fused[key] += 1.0 / (rrf_k + rank)
        return fused

    def _apply_versions(self, scored: list[tuple[str, float]], allowed_ids=None) -> list[tuple[str, float]]:
        """Dubletten zusammenfuehren und ueberholte Fassungen abwerten.

        - Seiten mit identischem Inhalt in mehreren Fassungen belegen nur einen
          Platz; zitiert wird die kanonische (aktuellste) Fassung mit dem
          besten Score der Gruppe.
        - Seiten, die nur in einer ueberholten Fassung existieren, bekommen
          einen Abschlag (``superseded_penalty``), verschwinden aber nicht.
        """
        self._version_signals: dict[str, dict[str, Any]] = {}
        vi = self.versions
        if not vi.enabled or not bool(self.config.get("dedup_versions", True)):
            return self._apply_doc_types(scored)
        penalty = float(self.config.get("superseded_penalty", 0.15))
        best: dict[str, tuple[str, float, str]] = {}  # group -> (key, score, original_locator)
        out: list[tuple[str, float]] = []
        for key, score in scored:
            rec = self.by_id[key]
            locator = f"{rec['source_file']}, Seite {int(rec['page'])}"
            gid = vi.group_of(locator)
            if gid is None:
                if vi.is_superseded(rec["source_file"]):
                    score = score * (1.0 - penalty)
                    self._version_signals[key] = {"superseded": True}
                out.append((key, score))
                continue
            canon = vi.canonical_of(locator)
            canon_rec = self.by_locator.get(canon)
            if canon_rec and allowed_ids is not None and canon_rec['id'] not in allowed_ids:canon_rec=None
            use_key = canon_rec["id"] if canon_rec else key
            prev = best.get(gid)
            if prev is None or score > prev[1]:
                best[gid] = (use_key, score, locator)
        for gid, (use_key, score, locator) in best.items():
            n = len(vi.groups.get(gid, []))
            sig = {"versions_merged": n}
            # Gruppe nur aus ueberholten Fassungen (Inhalt in der aktuellen Fassung
            # geaendert): ebenfalls abwerten, sonst schlaegt die alte Seite die neue.
            if vi.is_superseded(self.by_id[use_key]["source_file"]):
                score = score * (1.0 - penalty)
                sig["superseded"] = True
            out.append((use_key, score))
            if self.by_id[use_key]["source_file"] != locator.rsplit(", Seite", 1)[0]:
                sig["merged_from"] = locator
            self._version_signals[use_key] = sig
        return self._apply_doc_types(out)

    def _apply_doc_types(self, scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Dokumenttyp-Gewichtung: Richtlinie/Gesetz voll, Lehrgang/Fachbuch/
        Fragenkatalog abgestuft (``doc_type_weights``)."""
        dt = self.doctypes
        if not dt.enabled or not bool(self.config.get("weight_doc_types", True)):
            return scored
        out: list[tuple[str, float]] = []
        for key, score in scored:
            sf = self.by_id[key]["source_file"]
            w = dt.weight_of(sf)
            sig = self._version_signals.setdefault(key, {})
            sig["doc_type"] = dt.type_of(sf)
            if w != 1.0:
                sig["doc_type_weight"] = w
            out.append((key, score * w))
        return out

    def retrieve(self, query: str, *, use_graph: bool | None = None) -> dict[str, Any]:
        cfg = self.config
        graph_enabled = bool(cfg.get("enable_graph", False)) if use_graph is None else use_graph
        if graph_enabled and self.reference_graph is None:
            raise RuntimeError("Querverweisgraph ist fuer diesen Bestand nicht geladen.")
        if graph_enabled and not self.use_reranker:
            raise RuntimeError("Querverweissuche benoetigt das Reranking der zusaetzlichen Kandidaten.")
        expanded_query, added_terms = self.expander.expand(query)
        references=requested_rules(query) if cfg.get('enable_regulation_scope',False) else ()
        allowed_ids=getattr(self,'scope_ids',None)
        if references:
            rule_ids={key for key,record in self.by_id.items() if record_matches_rules(
                record['source_file'],record['text'],references,self.doctypes.type_of(record['source_file']))}
            allowed_ids=rule_ids if allowed_ids is None else rule_ids & allowed_ids
        if allowed_ids is not None and not allowed_ids:
            return {'query':query,'expanded_query':expanded_query,'added_terms':added_terms,
                    'search_queries':[query],'passages':[],'hits':[],
                    'graph':{'enabled':graph_enabled,'candidate_count':0}}
        searcher=self
        if references:
            searcher=copy(self)
            searcher.search_sources=frozenset(self.by_id[k]['source_file'] for k in allowed_ids)
            searcher._embed_query=self._embed_query
            searcher._encode_query_sparse=self._encode_query_sparse
        if cfg.get('enable_intent_hints',False):
            hints=list(linguistic_hints(query))+list(quantity_compounds(query,self.bm25.index.get('postings',{})))
            added_terms=list(dict.fromkeys(added_terms+hints));expanded_query+=' '+' '.join(hints)
        n_dense = int(cfg.get("dense_candidates", 30))
        n_bm25 = int(cfg.get("bm25_candidates", 30))

        # Teilfragen fuer breitere Kandidatengewinnung (Multi-Hop-Recall).
        subqueries = self.decompose(query) if cfg.get("enable_decomposition", True) else [query]
        if not cfg.get('enable_decomposition',True) and cfg.get('enable_multi_question_search',False):
            subqueries=decompose_query(query,max_subs=3)
        related_queries=self.expander.related_parent_queries(query) if cfg.get('enable_related_parent_search',False) else []
        if related_queries:
            maximum=max(2,int(cfg.get('max_subqueries',5)));room=max(0,maximum-1-len(related_queries))
            independent=subqueries[1:][-room:] if room else []
            subqueries=[query]+independent+related_queries

        enable_sparse = bool(cfg.get("enable_sparse", False))
        n_sparse = int(cfg.get("sparse_candidates", n_bm25))

        # Dense nutzt die Originalfrage(n) (Embeddings erfassen Semantik bereits);
        # BM25 nutzt die expandierte Frage (lexikalische Recall-Hilfe).
        # Volle Frage zuerst -> ihre Raenge dienen als Signal-Anzeige.
        # Alle Rankings laufen parallel: Dense/Sparse sind Netzwerk-Roundtrips
        # (Embedding-Dienst + Qdrant), die nicht aufeinander warten muessen.
        futures: list[Future] = [
            self.pool.submit(searcher._dense, query, n_dense),
            self.pool.submit(self.bm25.search, expanded_query, n_bm25,**({'allowed_ids':allowed_ids} if allowed_ids is not None else {})),
        ]
        if enable_sparse:
            futures.append(self.pool.submit(searcher._sparse, query, n_sparse))
        for sub in subqueries[1:]:
            sub_exp, _ = self.expander.expand(sub)
            futures.append(self.pool.submit(searcher._dense, sub, n_dense))
            futures.append(self.pool.submit(self.bm25.search, sub_exp, n_bm25,**({'allowed_ids':allowed_ids} if allowed_ids is not None else {})))
            if enable_sparse:
                futures.append(self.pool.submit(searcher._sparse, sub, n_sparse))

        structure_futures=[]
        if self.structure is not None:
            structure_futures.append(self.pool.submit(self._structure_candidates,expanded_query))
            if cfg.get('structure_collection'):
                def dense_structure_after_primary():
                    # Reuse the already encoded original query. Two concurrent
                    # cache misses must not issue duplicate embedding requests.
                    futures[0].result()
                    return searcher._structure_dense(query)
                structure_futures.append(self.pool.submit(dense_structure_after_primary))
        rankings = [f.result() for f in futures]
        structured_rankings=[f.result() for f in structure_futures]
        rankings.extend(structured_rankings)
        if allowed_ids is not None:rankings=[[(key,score) for key,score in ranking if key in allowed_ids] for ranking in rankings]
        dense = rankings[0]
        bm25 = rankings[1]
        sparse: list[tuple[str, float]] = rankings[2] if enable_sparse else []
        symbolic=self._symbolic_candidates(query) if self.use_reranker else []
        acronym_plural=self._acronym_plural_candidates(query) if self.use_reranker else []
        if allowed_ids is not None:
            symbolic=[(key,score) for key,score in symbolic if key in allowed_ids]
            acronym_plural=[(key,score) for key,score in acronym_plural if key in allowed_ids]
        if symbolic:rankings.append(symbolic)
        if acronym_plural:rankings.append(acronym_plural)

        fused = self._rrf(rankings, int(cfg.get("rrf_k", 60)))
        # nur Kandidaten behalten, die wir als Volltext kennen
        fused_keys = [k for k in fused if k in self.by_id]
        fused_keys.sort(key=lambda k: fused[k], reverse=True)

        dense_rank = {k: i + 1 for i, (k, _) in enumerate(dense)}
        bm25_rank = {k: i + 1 for i, (k, _) in enumerate(bm25)}
        sparse_rank = {k: i + 1 for i, (k, _) in enumerate(sparse)}

        rerank_keep = int(cfg.get("rerank_keep", 8))
        threshold = float(cfg.get("relevance_threshold", 0.0))
        # Anteil des Cross-Encoders an der Endwertung; Rest = normierte RRF-Fusion.
        # Reiner Reranker degradiert bei langen, mehrteiligen Domaenenfragen
        # BM25-starke Exakttreffer -> Blend beider Signale ist robuster.
        blend = float(cfg.get("rerank_blend", 0.5))
        graph_candidates = {}

        if self.use_reranker and fused_keys:
            rerank_pool = int(cfg.get("rerank_pool", 60))
            candidate_keys = fused_keys[:rerank_pool]
            if cfg.get('enable_multi_question_search',False) and len(subqueries)>1:
                # Reserve bounded space for independently asked aspects. RRF
                # consensus on one aspect must not exclude all others.
                stride=3 if enable_sparse else 2;diverse=[]
                for offset in range(stride,len(rankings[:stride*len(subqueries)]),stride):
                    for ranking in rankings[offset:offset+stride]:
                        for key,_ in ranking[:2]:
                            if key in self.by_id and key not in diverse:diverse.append(key)
                reserve=min(len(diverse),max(1,rerank_pool//4))
                candidate_keys=list(dict.fromkeys(candidate_keys[:rerank_pool-reserve]+diverse[:reserve]+candidate_keys))[:rerank_pool]
            base_candidate_keys = list(candidate_keys)
            if cfg.get('rerank_neighbors',False):
                neighbours=[];limit=min(12,rerank_pool//4)
                protected_base=set(base_candidate_keys[:max(0,rerank_pool-limit)])
                for seed in base_candidate_keys[:8]:
                    record=self.by_id[seed]
                    for delta in (-1,1):
                        neighbour=self.by_file_page.get((record['source_file'],int(record['page'])+delta))
                        if not neighbour:continue
                        key=neighbour['id']
                        if key in protected_base or key in neighbours:continue
                        if allowed_ids is not None and key not in allowed_ids:continue
                        neighbours.append(key)
                        if len(neighbours)>=limit:break
                    if len(neighbours)>=limit:break
                if limit and neighbours:
                    ordered=base_candidate_keys[:max(0,rerank_pool-len(neighbours))]+neighbours+base_candidate_keys
                    candidate_keys=list(dict.fromkeys(ordered))[:rerank_pool]
            candidate_keys.extend(key for key,_ in symbolic if key not in candidate_keys)
            candidate_keys.extend(key for key,_ in acronym_plural if key not in candidate_keys)
            if graph_enabled:
                graph_candidates = self.reference_graph.expand(
                    candidate_keys[:int(cfg.get("graph_seed_count", 8))], query,
                    limit=int(cfg.get("graph_max_candidates", 12)),
                    pages_per_reference=int(cfg.get("graph_pages_per_reference", 2)),
                )
                if allowed_ids is not None:graph_candidates={key:value for key,value in graph_candidates.items() if key in allowed_ids}
                candidate_keys.extend(k for k in graph_candidates if k not in candidate_keys)
            # Score a bounded query-relevant view, including late rules. The
            # full source text below remains unchanged for generation/checks.
            max_chars = int(cfg.get("rerank_max_chars", 0))
            texts = [
                rerank_excerpt(self.by_id[k]["text"], query, max_chars)
                for k in candidate_keys
            ]
            scores = self.reranker.score(query, texts)
            original_rr = dict(zip(candidate_keys, scores));rr=dict(original_rr)
            # Independent parts must not compete only under the full compound
            # wording. Use the same bounded candidates and keep the original
            # relevance floor, even for a strong score on an alternative view.
            scoring_views=list(dict.fromkeys(subqueries[1:]))[:3] if cfg.get('enable_multi_question_search',False) else related_queries[:1]
            view_scores={}
            for related in scoring_views:
                related_scores=self.reranker.score(related,[rerank_excerpt(self.by_id[k]['text'],related,max_chars) for k in candidate_keys])
                view_scores[related]=dict(zip(candidate_keys,related_scores))
                rr={key:max(rr[key],score) for key,score in zip(candidate_keys,related_scores)}
            # Die Graph-Erweiterung veraendert die Normalisierung der Basistreffer
            # nicht. Neue Kandidaten muessen sich durch das Reranking bewaehren.
            fmin = min(fused[k] for k in base_candidate_keys)
            fmax = max(fused[k] for k in base_candidate_keys)
            span = (fmax - fmin) or 1.0
            # Relative RRF rank cannot establish that any source is relevant.
            min_reranker = float(cfg.get("min_reranker_score", 0.001))
            scored = [(k, blend * rr[k] + (1.0 - blend) * max(0.0, (fused.get(k, fmin) - fmin) / span))
                      for k in candidate_keys if original_rr[k] >= min_reranker]
            scored = self._apply_versions(scored,allowed_ids)
            preferred=set()
            if cfg.get('prefer_primary_for_rules',False) and rule_question(query):
                floor=max(.5,max(rr.values(),default=0)*.7)
                for key,_ in scored:
                    if self.doctypes.type_of(self.by_id[key]['source_file']) in {'ril','gesetz','mitteilung'} and rr.get(key,0)>=floor:preferred.add(key)
            scored.sort(key=lambda kv:(kv[0] in preferred,rr.get(kv[0],0) if kv[0] in preferred else kv[1],kv[1]),reverse=True)
            kept = [(k, s) for k, s in scored if s >= threshold][:rerank_keep]
            if cfg.get('enable_multi_question_search',False) and comparison_subjects(query):
                kept=comparison_coverage(scored,query,view_scores,self.by_id,rerank_keep,threshold,min_reranker)
            score_label = "blend"
        else:
            scored = self._apply_versions([(k, fused[k]) for k in fused_keys[: max(rerank_keep * 4, 32)]],allowed_ids)
            scored.sort(key=lambda kv: kv[1], reverse=True)
            kept = scored[:rerank_keep]
            score_label = "rrf"

        passages: list[Passage] = []
        seen: set[tuple[str, int]] = set()
        for retrieval_rank, (key, score) in enumerate(kept, start=1):
            rec = self.by_id[key]
            sf, pg = rec["source_file"], int(rec["page"])
            seen.add((sf, pg))
            passages.append(
                Passage(
                    key=key,
                    source_file=sf,
                    page=pg,
                    text=rec["text"],
                    locator=f"{sf}, Seite {pg}",
                    score=round(float(score), 4),
                    kind="hit",
                    signals={
                        "retrieval_rank": retrieval_rank,
                        "score_type": score_label,
                        "reranker_score": round(rr.get(key, 0.0), 5) if self.use_reranker else None,
                        **({'original_reranker_score':round(original_rr.get(key,0),5)} if self.use_reranker and related_queries else {}),
                        "dense_rank": dense_rank.get(key),
                        "bm25_rank": bm25_rank.get(key),
                        "sparse_rank": sparse_rank.get(key),
                        "rrf": round(fused.get(key, 0.0), 5),
                        **({'symbolic_candidate':True} if key in dict(symbolic) else {}),
                        **({'acronym_plural_candidate':True} if key in dict(acronym_plural) else {}),
                        **({"graph_reference": graph_candidates[key]} if key in graph_candidates else {}),
                        **self._version_signals.get(key, {}),
                    },
                )
            )

        # Nachbarseiten anreichern
        neighbor_pages = int(cfg.get("neighbor_pages", 0))
        if neighbor_pages > 0:
            for hit in list(passages):
                for delta in range(-neighbor_pages, neighbor_pages + 1):
                    if delta == 0:
                        continue
                    npg = hit.page + delta
                    rec = self.by_file_page.get((hit.source_file, npg))
                    if not rec or (hit.source_file, npg) in seen:
                        continue
                    seen.add((hit.source_file, npg))
                    passages.append(
                        Passage(
                            key=rec["id"],
                            source_file=hit.source_file,
                            page=npg,
                            text=rec["text"],
                            locator=f"{hit.source_file}, Seite {npg}",
                            score=0.0,
                            kind="neighbor",
                            signals={
                                "neighbor_of": hit.locator,
                                "neighbor_of_rank": hit.signals.get("retrieval_rank"),
                            },
                        )
                    )

        # Optional bounded companion context for rare exact entity spellings.
        # The hit threshold stays unchanged; like physical neighbours, these
        # are context candidates and must still pass the full answer checks.
        if passages and cfg.get('enable_identifier_context',False) and self.use_reranker:
            companion_keys=[key for key,_ in acronym_plural if rr.get(key,0)>=min_reranker]
            companion_keys.sort(key=lambda key:(-rr[key],key))
            added=0
            for key in companion_keys:
                rec=self.by_id[key];identity=(rec['source_file'],int(rec['page']))
                if identity in seen:continue
                seen.add(identity);added+=1
                passages.append(Passage(key,identity[0],identity[1],rec['text'],f'{identity[0]}, Seite {identity[1]}',kind='neighbor',
                    signals={'identifier_context':True,'neighbor_of_rank':1,'reranker_score':round(rr[key],5)}))
                if added==2:break

        if self.structure is not None:
            # Keep original structure owners available even if a duplicate
            # page was canonicalized to another document. These are context
            # candidates, never forced accepted answers or replacement quotes.
            owners=list(dict.fromkeys(key for ranking in structured_rankings for key,_ in ranking))
            added=0
            for key in owners:
                if added>=2:break
                rec=self.by_id.get(key)
                if not rec or (rec['source_file'],int(rec['page'])) in seen:continue
                if allowed_ids is not None and key not in allowed_ids:continue
                if self.use_reranker and original_rr.get(key,0)<.5:continue
                seen.add((rec['source_file'],int(rec['page'])));added+=1
                passages.append(Passage(key,rec['source_file'],int(rec['page']),rec['text'],
                    f"{rec['source_file']}, Seite {int(rec['page'])}",kind='neighbor',signals={'structure_owner':True,'neighbor_of_rank':1}))
            parents=[]
            for passage in list(passages):
                hints=self.structure.page_hints(query,passage.source_file,passage.page)
                if hints:passage.signals={**passage.signals,'structure':hints}
                for pg in self.structure.parent_pages(query,passage.source_file,passage.page):
                    rec=self.by_file_page.get((passage.source_file,pg))
                    if not rec or (rec['source_file'],pg) in seen:continue
                    if allowed_ids is not None and rec['id'] not in allowed_ids:continue
                    seen.add((rec['source_file'],pg))
                    parents.append(Passage(rec['id'],rec['source_file'],pg,rec['text'],f"{rec['source_file']}, Seite {pg}",
                        kind='neighbor',signals={'structure_parent':True,'neighbor_of_rank':passage.signals.get('retrieval_rank',1)}))
                    if len(parents)>=3:break
                if len(parents)>=3:break
            passages.extend(parents)

        # Echte Treffer muessen vor Nachbarseiten in den Kontext, damit
        # relevante Hits nicht durch lange Kontextseiten aus dem Budget fallen.
        if references:
            passages=[p for p in passages if record_matches_rules(p.source_file,p.text,references,self.doctypes.type_of(p.source_file))]
        passages.sort(
            key=lambda p: (
                0 if p.kind == "hit" else 1,
                p.signals.get("retrieval_rank") if p.kind == "hit" else p.signals.get("neighbor_of_rank", 9999),
                p.source_file,
                p.page,
            )
        )

        return {
            "query": query,
            "expanded_query": expanded_query,
            "added_terms": added_terms,
            "search_queries":subqueries,
            "passages": passages,
            "hits": [p for p in passages if p.kind == "hit"],
            "graph": {"enabled": graph_enabled, "candidate_count": len(graph_candidates)},
        }
