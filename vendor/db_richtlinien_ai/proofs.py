"""Display only provenance that survived the final factual checks.

No model call, retrieval, inferred quotation or additional factual statement is
performed here. The native-original adapter must independently match a quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re


@dataclass(frozen=True)
class EvidenceSpan:
    locator: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class CheckedClaim:
    text: str
    spans: tuple[EvidenceSpan, ...]


class CheckedAnswer(str):
    """An ordinary API string with request-local, non-serialized provenance."""
    def __new__(cls, text, claims=(), source_views=None):
        value = super().__new__(cls, text)
        value.claims = tuple(claims)
        value.source_views = MappingProxyType(dict(source_views or {}))
        return value


def _cell(text):
    # WebUI intentionally renders generated HTML entities as literal text.
    # Markdown escapes keep ordinary punctuation and units human-readable.
    return re.sub(r'([\\`*_{}#!~])',r'\\\1',' '.join(text.split()))


def _safe_quote(text):
    # Do not let original text become an image/link, table row, HTML or a
    # secondary source locator. Unsupported markup gets the original-page
    # placeholder rather than a visibly altered or unsafe "original quote".
    return not (re.search(r'[\[\]|]|</?[a-zA-Z]|\b[a-z][a-z0-9+.-]*://|\bwww\.|&(?:#\w+|\w+);',text,re.I))


def _safe_locator(locator):
    return (isinstance(locator, str) and
            not re.search(r'[\x00-\x1f<>|\[\]`\\/:]', locator) and
            bool(re.search(r'\.pdf, Seite [1-9]\d*$', locator, re.I)))


def render_evidence_table(answer, config, quote_resolver=None):
    """Add a bounded table after an already checked answer, never to a draft.

    The complete qualified answer remains intact. Long/unavailable native
    quotations are explicitly left to the original page instead of being cut.
    """
    if (not config.get('show_evidence_table', False) or
            not isinstance(answer, CheckedAnswer) or not answer.claims):
        return answer
    rows = []
    for claim in answer.claims:
        if claim.text not in answer or not claim.spans:
            continue
        spans = tuple(dict.fromkeys(claim.spans))
        if len(spans)>4 or any(not _safe_locator(s.locator) for s in spans):
            continue
        refs=[];quotes=[];valid=True;native_matches=0
        for span in spans:
            source=answer.source_views.get(span.locator)
            if not isinstance(source,str):valid=False;break
            lines=[line for line in source.splitlines() if line.strip()]
            if (type(span.line_start) is not int or type(span.line_end) is not int or
                    not 1<=span.line_start<=span.line_end<=len(lines) or
                    span.line_end-span.line_start>=20):
                valid=False;break
            if span.locator not in refs:refs.append(span.locator)
            excerpt='\n'.join(lines[span.line_start-1:span.line_end])
            native=quote_resolver(span.locator,excerpt) if quote_resolver else None
            if native:
                native_matches+=1
                if native not in quotes:quotes.append(native)
        if not valid:continue
        # No shortened quote may silently discard a source condition. Keep
        # only whole native matches; the answer itself is never abbreviated.
        if native_matches==len(spans) and sum(map(len,quotes))<=1000 and all(_safe_quote(q) for q in quotes):
            quote=' <br><br> '.join('„'+_cell(q)+'“' for q in quotes)
        else:
            quote='Beleg siehe Originalseite.'
        row='| '+quote+' | '+' <br> '.join('['+r+']' for r in refs)+' |'
        if row in rows:continue  # one original can support several answer sentences
        rows.append(row)
        if len(rows)>=3:break
    if not rows:return answer
    return str(answer)+'\n\n**Belege**\n\n| Originalzitat | Richtlinie / Dokument und Seite |\n| --- | --- |\n'+'\n'.join(rows)
