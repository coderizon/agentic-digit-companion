"""Immutable request views over the shared retriever and admitted uploaded pages."""
from collections import OrderedDict
from copy import copy
import hashlib
import json
import threading

from db_richtlinien_ai.lexical_index import build_bm25
from db_richtlinien_ai.retrieval import Bm25Index


class KnowledgeViews:
    def __init__(self, maximum=6):
        self.maximum = maximum
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def view(self, base, sources, uploaded_pages=(), version='', topk_uploads=()):
        names = frozenset(sources)
        key = (id(base), hashlib.sha256(json.dumps(sorted(names)).encode()).hexdigest(), version)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            records = {key: row for key, row in base.by_id.items() if row['source_file'] in names}
            for row in uploaded_pages:
                if row['source_file'] not in names:
                    continue
                if row['id'] in records and records[row['id']] != row:
                    raise ValueError('Knowledge page identifier collision')
                records[row['id']] = row
            view = copy(base)
            view._pool = base.pool
            if getattr(base,"topk",None) is None or base.config.get("enable_sparse"):
                view._qdrant = base.qdrant
            view.topk_uploads = tuple(topk_uploads)
            view._encode_query_sparse = base._encode_query_sparse
            view._symbol_cache = OrderedDict()
            view._symbol_lock = threading.Lock()
            view.by_id = records
            view.by_file_page = {(r['source_file'], int(r['page'])): r for r in records.values()}
            view.by_locator = {f"{r['source_file']}, Seite {int(r['page'])}": r for r in records.values()}
            view.search_sources = names
            view.scope_ids = frozenset(records)
            new_ids=set(records)-set(base.by_id)
            view.additional_collections=(
                base.config.get('knowledge_upload_collection','db_richtlinien_knowledge_pages_bge_m3'),
            ) if new_ids else ()
            if set(records) != set(base.by_id):
                bm25 = object.__new__(Bm25Index)
                bm25.index = build_bm25(records.values())
                bm25.documents = bm25.index['documents']
                bm25.avg_doc_length = bm25.index['avg_doc_length']
                view.bm25 = bm25
            self.cache[key] = view
            while len(self.cache) > self.maximum:
                self.cache.popitem(last=False)
            return view
