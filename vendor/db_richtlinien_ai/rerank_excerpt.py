"""Bounded query-aware scoring views; never replace the source evidence itself."""
from __future__ import annotations

import math
import re
from db_richtlinien_ai.query_intent import linguistic_hints

_WORDS = re.compile(r"[0-9A-Za-zÄÖÜäöüß]+")
_SYMBOL = re.compile(r'(?<!\w)[A-Za-z]{1,3}\s*\+\s*\d{1,3}(?!\w)')
_STOP = set("der die das den dem des ein eine einer einem einen eines und oder für von mit zu zum zur im in am an auf aus bei ist sind wird werden wurde wurden wie was welche welcher welches welchen warum wozu kann können darf dürfen soll sollen nennt genannt bitte zeige zeigt mir es sie er man the a an of for to is are what which show".split())


def _term(word: str) -> str:
    word = word.casefold().translate(str.maketrans("äöü", "aou"))
    # Only a scoring hint, not a linguistic rewrite of evidence. Preserve short
    # acronyms/numbers and strip one common inflection from longer words.
    for suffix in ("ern", "en", "er", "es", "e", "n", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def query_terms(text: str) -> set[str]:
    terms={_term(m.group()) for m in _WORDS.finditer(text)
           if len(m.group()) >= 2 and m.group().casefold() not in _STOP}
    terms.update(re.sub(r'\s+','',m.group()).casefold() for m in _SYMBOL.finditer(text))
    terms.update(_term(word) for word in linguistic_hints(text))
    return terms


def rerank_excerpt(text: str, query: str, max_chars: int) -> str:
    """Keep the page header and the most useful contiguous scoring window.

    Prefix-only truncation loses late rules/exceptions. This retains the same
    request size and one score per candidate. The reranker still judges the
    original question; lexical overlap is only used to choose its input view.
    """
    # Restore lexical PDF line wraps for scoring, while preserving uppercase
    # identifiers and numeric separators. Evidence itself is not rewritten.
    text=re.sub(r'(?<=[a-zäöüß])-[ \t]*\n[ \t]*(?=[a-zäöüß])','',text)
    # Scoring-only normalization of explicitly capitalized acronym plurals.
    # The admitted source text in Retriever.by_id/Passage remains unchanged.
    text=re.sub(r'\b([A-Z][A-Z0-9]{1,10})(?:en|s|n)\b',r'\1',text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    prefix = text[:max_chars]
    if max_chars < 256:
        return prefix
    terms = query_terms(query)
    matches = [(m.start(), m.end(), _term(m.group())) for m in _WORDS.finditer(text)
               if _term(m.group()) in terms]
    matches.extend((m.start(),m.end(),re.sub(r'\s+','',m.group()).casefold())
                   for m in _SYMBOL.finditer(text) if re.sub(r'\s+','',m.group()).casefold() in terms)
    if not matches:
        return prefix
    counts = {term: sum(t == term for _, _, t in matches) for term in terms}
    weights = {term: 1.0 / (1.0 + math.log1p(n)) for term, n in counts.items()}
    header_end = min(250, max_chars // 4)
    separator = "\n[…]\n"
    width = max_chars - header_end - len(separator)
    header_terms = {t for _, end, t in matches if end <= header_end}

    def score(start: int, end: int, header: bool = False) -> float:
        found = {t for begin, finish, t in matches if begin >= start and finish <= end}
        value=sum(weights[t] for t in (found | header_terms if header else found))
        # Break lexical ties in favour of surrounding formula context. A view
        # ending immediately after "k+2" loses the actual following definition.
        value+=sum(.01*max(0,min(begin-start,end-finish,width//3))/(width//3)
                   for begin,finish,term in matches if '+' in term and term in found)
        return value

    best, best_score = prefix, score(0, max_chars)
    # Enough preceding context for negations/conditions; never assemble isolated
    # keyword snippets that would hide the surrounding rule from the reranker.
    starts = {max(header_end, min(begin - width // 3, len(text) - width))
              for begin, _, _ in matches}
    for start in sorted(starts):
        value = score(start, start + width, header=True)
        if value > best_score + 1e-9:
            best = text[:header_end] + separator + text[start:start + width]
            best_score = value
    return best
