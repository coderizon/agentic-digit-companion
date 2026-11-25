"""Versionslogik fuer den Korpus: Dubletten erkennen, neueste Fassung bevorzugen.

Der Korpus enthaelt viele Fassungen desselben Dokuments (Signalbuch V10/V11/
Aktualisierung 10/11, Ril 132.00/132.01, "... alt.pdf" usw.). Im Retrieval
konkurrieren identische Seiten dann um dieselben Plaetze und verdraengen andere
Treffer. Dieses Modul arbeitet **inhaltsbasiert**, nicht ueber Dateinamen:

1. Jede Seite bekommt einen Hash ihres normalisierten Textes. Seiten mit
   gleichem Hash in verschiedenen Dokumenten bilden eine *Gruppe*.
2. Dokumente, die viele Seiten teilen, bilden eine *Familie* (Union-Find).
3. Pro Dokument wird ein *Versionsdatum* bestimmt: haeufigstes "Gueltig ab /
   Stand"-Datum im Seitentext, sonst Datum aus dem Dateinamen.
4. In jeder Familie ist das Dokument mit dem juengsten Datum die *aktuelle*
   Fassung; alle anderen gelten als *ueberholt*. Pro Gruppe ist die Seite der
   aktuellsten Fassung die *kanonische* Fundstelle.

Das Artefakt (``data/indexes/page_groups.json.gz``) entsteht in
``scripts/build_page_groups.py`` und wird vom Retriever und den Eval-Skripten
geladen.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# Seiten mit weniger Zeichen sind meist Deckblaetter/Leerseiten: keine Dubletten-Logik.
MIN_HASH_CHARS = 200
# Mindestanzahl gemeinsamer Seiten, ab der zwei Dokumente als Fassungen desselben
# Dokuments gelten: absolut und relativ zum GROESSEREN Dokument. Der Bezug auf das
# groessere Dokument verhindert, dass ein Einzelmodul (7 Seiten), das komplett in
# einer Gesamtausgabe (1500 Seiten) enthalten ist, als "Fassung" der Gesamtausgabe
# gilt und diese verdraengt.
FAMILY_MIN_SHARED = 5
FAMILY_MIN_RATIO = 0.3

_DATE_IN_TEXT = re.compile(
    r"(?:G[üu]ltig\s+ab|Stand|Ausgabe|Fassung\s+vom)[:\s]*(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE
)
_DATE_IN_NAME = (
    re.compile(r"(\d{2})[-_.](\d{2})[-_.](\d{4})"),  # Stand_01-10-2018, 13_04_2010
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),  # 2018-10-01
)
_WS = re.compile(r"\s+")


def page_hash(text: str) -> str | None:
    normalized = _WS.sub(" ", text or "").strip().casefold()
    if len(normalized) < MIN_HASH_CHARS:
        return None
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _date_from_text(texts: Iterable[str]) -> str | None:
    counter: Counter[str] = Counter()
    for text in texts:
        for dd, mm, yy in _DATE_IN_TEXT.findall(text[:3000]):
            if 1990 <= int(yy) <= 2035 and 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
                counter[f"{yy}-{int(mm):02d}-{int(dd):02d}"] += 1
    if not counter:
        return None
    # Das juengste der haeufigen Daten: Ril-Seiten tragen das Datum ihrer letzten
    # Aenderung, das Dokument ist so neu wie seine juengste Seite mit Gewicht.
    top = max(counter.values())
    candidates = [d for d, n in counter.items() if n >= max(2, top * 0.2)]
    return max(candidates) if candidates else counter.most_common(1)[0][0]


def _date_from_name(name: str) -> str | None:
    m = _DATE_IN_NAME[0].search(name)
    if m:
        dd, mm, yy = m.groups()
        if 1990 <= int(yy) <= 2035 and 1 <= int(mm) <= 12:
            return f"{yy}-{mm}-{dd}"
    m = _DATE_IN_NAME[1].search(name)
    if m:
        return "-".join(m.groups())
    return None


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_page_groups(pages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Berechnet Gruppen, Familien, Versionsdaten und kanonische Fundstellen."""
    hashes: dict[str, list[str]] = defaultdict(list)  # hash -> [locator]
    doc_pages: Counter[str] = Counter()
    doc_hashed: Counter[str] = Counter()
    doc_texts: dict[str, list[str]] = defaultdict(list)
    locator_doc: dict[str, str] = {}

    for p in pages:
        doc = p["source_file"]
        locator = f"{doc}, Seite {int(p['page'])}"
        doc_pages[doc] += 1
        doc_texts[doc].append(p.get("text", ""))
        h = page_hash(p.get("text", ""))
        if h:
            doc_hashed[doc] += 1
            hashes[h].append(locator)
            locator_doc[locator] = doc

    # Versionsdatum je Dokument
    version_date: dict[str, str | None] = {}
    for doc, texts in doc_texts.items():
        # Textdatum (Gueltig ab/Stand auf den Seiten) und Dateinamensdatum: das
        # juengere zaehlt. Ein "Stand_01-04-2020" im Namen ist verlaesslicher als
        # der haeufigste Seitenstand, wenn nur wenige Seiten geaendert wurden.
        candidates = [d for d in (_date_from_text(texts), _date_from_name(doc)) if d]
        version_date[doc] = max(candidates) if candidates else None

    # Gemeinsame Seiten je Dokumentpaar -> Familien
    shared: Counter[tuple[str, str]] = Counter()
    for locs in hashes.values():
        docs = sorted({locator_doc[l] for l in locs})
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                shared[(docs[i], docs[j])] += 1
    uf = _UnionFind()
    for (a, b), n in shared.items():
        larger = max(1, doc_hashed[a], doc_hashed[b])
        if n >= FAMILY_MIN_SHARED and n / larger >= FAMILY_MIN_RATIO:
            uf.union(a, b)

    families: dict[str, list[str]] = defaultdict(list)
    for doc in doc_pages:
        families[uf.find(doc)].append(doc)

    def rank(doc: str) -> tuple:
        # juengstes Datum zuerst; "alt" im Namen verliert; dann mehr Seiten; dann Name
        is_alt = 1 if re.search(r"\balt\b", doc, re.IGNORECASE) else 0
        return (version_date.get(doc) or "0000-00-00", -is_alt, doc_pages[doc], doc)

    docs_out: dict[str, dict[str, Any]] = {}
    for fid, members in families.items():
        latest = max(members, key=rank)
        for doc in members:
            docs_out[doc] = {
                "pages": doc_pages[doc],
                "version_date": version_date.get(doc),
                "family": fid,
                "family_size": len(members),
                "latest": doc == latest,
            }

    groups: dict[str, list[str]] = {}
    canonical: dict[str, str] = {}
    page_group: dict[str, str] = {}
    for h, locs in hashes.items():
        if len({locator_doc[l] for l in locs}) < 2:
            continue
        gid = h[:12]
        groups[gid] = sorted(locs)
        canonical[gid] = max(locs, key=lambda l: rank(locator_doc[l]))
        for l in locs:
            page_group[l] = gid

    return {
        "version": 1,
        "docs": docs_out,
        "groups": groups,
        "canonical": canonical,
        "page_group": page_group,
    }


def save_page_groups(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    tmp.replace(path)


class VersionIndex:
    """Lesezugriff auf das Artefakt; ohne Artefakt verhaelt sich alles neutral."""

    def __init__(self, data: dict[str, Any] | None = None):
        data = data or {}
        self.docs: dict[str, dict[str, Any]] = data.get("docs", {})
        self.groups: dict[str, list[str]] = data.get("groups", {})
        self.canonical: dict[str, str] = data.get("canonical", {})
        self.page_group: dict[str, str] = data.get("page_group", {})

    @classmethod
    def load(cls, path: Path | None) -> "VersionIndex":
        if not path or not Path(path).exists():
            return cls()
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return cls(json.load(handle))

    @property
    def enabled(self) -> bool:
        return bool(self.page_group)

    def group_of(self, locator: str) -> str | None:
        return self.page_group.get(locator)

    def canonical_of(self, locator: str) -> str:
        gid = self.page_group.get(locator)
        return self.canonical.get(gid, locator) if gid else locator

    def equivalents(self, locator: str) -> list[str]:
        """Alle Fundstellen mit identischem Seiteninhalt (inkl. der eigenen)."""
        gid = self.page_group.get(locator)
        return list(self.groups.get(gid, [locator])) if gid else [locator]

    def is_superseded(self, source_file: str) -> bool:
        info = self.docs.get(source_file)
        return bool(info) and info.get("family_size", 1) > 1 and not info.get("latest", True)


def split_locator(locator: str) -> tuple[str, int] | None:
    m = re.match(r"^(.*), Seite (\d+)$", locator)
    return (m.group(1), int(m.group(2))) if m else None
