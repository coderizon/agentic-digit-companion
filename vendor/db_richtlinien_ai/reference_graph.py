"""Belegter Querverweisgraph fuer einen begrenzten Richtlinien-Piloten.

Eine Kante sagt nur aus, dass eine Seite ein Modul ausdruecklich nennt.
Sie behauptet keine fachliche Geltung, Ersetzung oder Ausnahme. Mehrere
Dokumentfassungen bleiben getrennt und werden nicht automatisch aufgeloest.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from db_richtlinien_ai.text import clean_extracted_text, tokenize

REFERENCE = re.compile(
    r"\b(?:Ril|Richtlinie|Modul|DB-Ril)\.?\s*(\d{3}\.\d{4}(?:[A-Z]\d{2})?)(?![\dA-Z])",
    re.IGNORECASE,
)
FILENAME_MODULE = re.compile(r"^(?:Ril[ _-]*)?(\d{3})[._ -](\d{4})([A-Z]\d{2})?(?=[ _.\-]|$)", re.I)
HEADER_MODULE = re.compile(r"^\s*(\d{3}\.\d{4}(?:[A-Z]\d{2})?)\s*$", re.M)
STOP_WORDS = set("der die das den dem des ein eine einer einem einen und oder ist sind wird werden wie was welche welchen welcher wo zu zur zum in im an am von mit fuer für auf bei nach unter es als sich man sollte muss müssen".split())


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def corpus_digest(pages: Iterable[dict]) -> str:
    digest = hashlib.sha256()
    for page in sorted(pages, key=lambda p: p['id']):
        row = [page['id'], page['source_file'], int(page['page']), clean_extracted_text(page.get('text', ''))]
        digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def modules_on_page(page: dict) -> set[str]:
    """Modulzuordnung nur aus Dateianfang oder freistehender Kopf-/Fusszeile."""
    found = set()
    match = FILENAME_MODULE.match(page['source_file'])
    if match:
        found.add(f'{match[1]}.{match[2]}{match[3] or ""}'.upper())
    text = page['text']
    for region in (text[:500], text[-200:]):
        found.update(m.group(1).upper() for m in HEADER_MODULE.finditer(region))
    return found


def build_graph(pages: list[dict], *, max_documents: int = 40,
                families: tuple[str, ...] = (), document_hashes: dict[str, str] | None = None) -> dict:
    """Erstellt nachvollziehbare Ein-Schritt-Verweise mit unveraendertem Beleg."""
    if max_documents < 1:
        raise ValueError('Der Pilot muss mindestens ein Quelldokument erlauben.')
    pages = [{**p, 'text': clean_extracted_text(p.get('text', ''))} for p in pages]
    targets: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    own_modules: dict[str, set[str]] = {}
    mentions: dict[str, list[tuple[dict, Any]]] = defaultdict(list)
    for page in pages:
        own = modules_on_page(page)
        own_modules[page['id']] = own
        for module in own:
            targets[module][page['source_file']].append(page['id'])
        if families and not page['source_file'].startswith(families):
            continue
        for match in REFERENCE.finditer(page['text']):
            if match[1].upper() not in own:
                mentions[page['source_file']].append((page, match))
    # Dokumentauswahl allein anhand expliziter, im Korpus vorhandener Verweise.
    # Keine Auswahl anhand von Eval-Antworten oder erwarteten Zielseiten.
    def connected_count(name: str) -> int:
        return sum(len(targets.get(m[1].upper(), {})) == 1 for _, m in mentions[name])
    selected = sorted(mentions, key=lambda name: (-connected_count(name), name))[:max_documents]
    edges = []
    seen = set()
    for name in selected:
        for page, match in mentions[name]:
            module = match[1].upper()
            key = (page['id'], module)
            if key in seen:
                continue
            seen.add(key)
            candidates = targets.get(module, {})
            # Eine Referenz auf einen anderen Teil derselben Sammeldatei ist erlaubt.
            target_ids = [k for ids in candidates.values() for k in ids if k != page['id']]
            status = 'eindeutig' if len(candidates) == 1 and target_ids else ('mehrdeutig' if len(candidates) > 1 else 'nicht_im_korpus')
            edges.append({
                'source_id': page['id'], 'relation': 'nennt_modul', 'target_module': module,
                'target_files': sorted(candidates), 'target_ids': sorted(target_ids), 'status': status,
                'evidence': {'source_file': name, 'page': int(page['page']),
                             'start': match.start(), 'end': match.end(), 'quote': match[0],
                             'page_sha256': text_digest(page['text'])},
                'validity': 'nicht_geprueft',
            })
    used_files = set(selected)
    used_files.update(name for edge in edges for name in edge['target_files'])
    return {
        'schema_version': 1, 'extractor': 'explizite-modulverweise-v1',
        'corpus_sha256': corpus_digest(pages), 'source_documents': selected,
        'families': list(families), 'edges': edges,
        'documents': {name: {'pdf_sha256': (document_hashes or {}).get(name),
                             'valid_from': None, 'valid_to': None} for name in sorted(used_files)},
        'stats': {'source_documents': len(selected), 'edges': len(edges),
                  'status': dict(Counter(edge['status'] for edge in edges))},
    }


class ReferenceGraph:
    def __init__(self, data: dict, pages: dict[str, dict]):
        if data.get('schema_version') != 1:
            raise ValueError('Unbekanntes Querverweisgraph-Format.')
        if data.get('corpus_sha256') != corpus_digest(pages.values()):
            raise ValueError('Querverweisgraph und Dokumentbestand stimmen nicht ueberein.')
        self.stats = data['stats']
        self.by_source: dict[str, list[dict]] = defaultdict(list)
        self.pages = pages
        self.tokens: dict[str, set[str]] = {}
        for edge in data['edges']:
            source = pages.get(edge['source_id'])
            evidence = edge['evidence']
            if not source or source['source_file'] != evidence['source_file'] or int(source['page']) != evidence['page']:
                raise ValueError('Querverweis ohne passende Originalseite.')
            text = clean_extracted_text(source['text'])
            if text_digest(text) != evidence['page_sha256'] or text[evidence['start']:evidence['end']] != evidence['quote']:
                raise ValueError('Querverweisbeleg wurde veraendert.')
            if edge['status'] != 'eindeutig':
                continue
            if any(key not in pages for key in edge['target_ids']):
                raise ValueError('Querverweisziel fehlt im Dokumentbestand.')
            self.by_source[edge['source_id']].append(edge)
            for key in edge['target_ids']:
                if key not in self.tokens:
                    self.tokens[key] = set(tokenize(pages[key]['text'])) - STOP_WORDS

    @classmethod
    def load(cls, path: Path, pages: dict[str, dict]) -> 'ReferenceGraph':
        return cls(json.loads(path.read_text(encoding='utf-8')), pages)

    def expand(self, seeds: list[str], query: str, *, limit: int = 12,
               pages_per_reference: int = 2, max_edges: int = 32) -> dict[str, dict]:
        """Begrenzte, gerichtete Expansion; jeder Kandidat behaelt seinen Beleg."""
        if limit <= 0 or pages_per_reference <= 0 or max_edges <= 0:
            return {}
        words = set(tokenize(query)) - STOP_WORDS
        result: dict[str, dict] = {}
        visited_edges = 0
        for seed in seeds:
            for edge in self.by_source.get(seed, []):
                visited_edges += 1
                if visited_edges > max_edges:
                    return result
                ranked = sorted(edge['target_ids'], key=lambda key: (
                    -len(words & self.tokens[key]), int(self.pages[key]['page']), key))
                for key in ranked[:pages_per_reference]:
                    if key in seeds or key in result:
                        continue
                    result[key] = {'source_id': seed, 'target_module': edge['target_module'],
                                   'relation': edge['relation'], 'evidence': edge['evidence'],
                                   'validity': edge['validity']}
                    if len(result) >= limit:
                        return result
        return result
