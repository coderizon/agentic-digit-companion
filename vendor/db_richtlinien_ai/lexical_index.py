"""The same page BM25 representation for full ingestion and scoped collection views."""
from collections import Counter, defaultdict
import math
from db_richtlinien_ai.text import tokenize


def build_bm25(pages):
    documents = []
    postings = defaultdict(list)
    lengths = []
    for position, page in enumerate(pages):
        tokens = tokenize(page['text'])
        lengths.append(len(tokens))
        documents.append({**{key: page[key] for key in (
            'id', 'source_path', 'source_file', 'page', 'char_count')},
            'token_count': len(tokens), 'preview': page['text'][:800]})
        for token, frequency in Counter(tokens).items():
            postings[token].append([position, frequency])
    count = len(documents)
    return dict(version=1, document_count=count,
        avg_doc_length=(sum(lengths)/count or 1) if count else 1,
        documents=documents, postings=dict(postings),
        idf={term: math.log(1+(count-len(items)+.5)/(len(items)+.5))
             for term, items in postings.items()})
