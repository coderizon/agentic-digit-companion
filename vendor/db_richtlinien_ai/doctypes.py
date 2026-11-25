"""Dokumenttypen fuer den Korpus: Richtlinie vor Fachbuch vor Fragenkatalog.

Nach der Versionslogik bleibt ein zweiter Verdraengungseffekt: Fragenkataloge,
Lehrgangsunterlagen, private Zusammenfassungen und Fachbuecher formulieren
Sachverhalte oft frageaehnlich und schlagen im Reranking die eigentliche
Richtlinienseite. Dieses Modul ordnet jedem Dokument einen Typ zu; der
Retriever gewichtet damit (``doc_type_weights``), und ``citable`` markiert,
ob ein Typ als Beleg taugt.

Zuordnung: Regeln ueber den Dateinamen, ergaenzt um Textmerkmale der ersten
Seiten (Richtlinien-Kopfzeile). Manuelle Korrekturen in
``config/doc_type_overrides.json`` (``{"Dateiname.pdf": "typ"}``) haben Vorrang.
Artefakt: ``data/indexes/doc_types.json`` (lesbar, pruefbar).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# Typ -> zitierfaehig als Beleg?
DOC_TYPES: dict[str, bool] = {
    "ril": True,  # DB-Richtlinie / Modul
    "gesetz": True,  # Gesetz, Verordnung, TSI, VOB
    "mitteilung": True,  # Technische Mitteilung (TM), Anschreiben, Weisung
    "zeichnung": True,  # Ebs/Dlk-Zeichnungen, Zeichnungsverzeichnisse, Schaltbilder
    "hersteller": True,  # Datenblaetter, Kataloge, Zulassungen
    "lehrgang": True,  # Lehrgangs-/Schulungsunterlagen (TN, BUeW, Bue0230, FIT)
    "fachbuch": True,  # Fachbuecher, Handbuecher, Leitfaeden, Fachbeitraege
    "fragenkatalog": False,  # Fragenkataloge, Pruefungsfragen, Loesungen
    "notizen": False,  # private Zusammenfassungen, Webseiten-Ausdrucke
    "sonstiges": True,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "ril": 1.0,
    "gesetz": 1.0,
    "mitteilung": 0.95,
    "zeichnung": 0.9,
    "hersteller": 0.85,
    "sonstiges": 0.9,
    "lehrgang": 0.85,
    "fachbuch": 0.8,
    "fragenkatalog": 0.7,
    "notizen": 0.6,
}

# Reihenfolge = Prioritaet: die erste passende Regel gewinnt.
_NAME_RULES: list[tuple[str, str]] = [
    # Nummerierte DB-Richtlinienmodule (954_9101, 81304_Planungshandbuch, 046_2301A01 ...)
    # sind Richtlinien, auch wenn "Handbuch", "Einweisung" o. ae. im Namen steht.
    ("ril", r"^\(?\d{3}[_ .]?\d{0,4}"),
    ("fragenkatalog", r"fragenkatalog|pr[üu]fungsfragen|\bl[öo]sung(en)?\b|\+ *l[öo]sungen"),
    ("notizen", r"zusammenfassung|notizen|^www\.|^anpassungen\.pdf$"),
    ("lehrgang", r"^tn |tn modul|^tnp\d|^b[üu]0230|^b[üu]w\b|vdef-tn|^fit[ _]|funktionsausbildung|^kabeltechnik_\d|unterlage modul|geschichte des arbeiten|^0_gsm-r"),
    ("fachbuch", r"^buch-|handbuch|praxis|leitfaden|fachbeitrag|^bahnpraxis|kabeltechnik.*grundlagen"),
    ("gesetz", r"^(ebo|eso|aeg|eigv|teiv|tsi[ _]|vv-ibg|vob|bundeseisenbahn|deutsche bahn gr[üu]ndungsgesetz|tgl-)"),
    ("mitteilung", r"^tm[ _]|^tm_nr|anschreiben|einf[üu]hrungsschreiben|weisung|handlungsanweisung|arbeitsanw"),
    ("zeichnung", r"^ebs |^dlk[ _]|^erg zu dlk|zeichnungsverzeichnis|prinzipschaltbild|^seite \d+_"),
    ("hersteller", r"prospekt|katalog|ebook|hilti|prysmian|bauartgenehmigung|^z-19|^z_19|zulassung|pz-abz|infomappe|betaflam|rockwool|z\.i\.s\.|plastetrog|bayka|kabelrohre|lieferanten|^universal multiplex|pole master|smart navigator"),
    ("ril", r"^(modul|gesamt\d*modul|signalbuch|anhang|zusatz|vordruck)|\bril\b|^ds_820|^dv[ _]\d|^dv_\d|betriebsplanung.*406|^st092|^blp_\d|^ril-"),
]
_NAME_RULES_COMPILED = [(t, re.compile(rx, re.IGNORECASE)) for t, rx in _NAME_RULES]

# Richtlinien-Kopfzeile: Modulnummer wie "954.9101" plus "Gueltig ab" auf den ersten Seiten.
_RIL_HEADER = re.compile(r"\b\d{3}\.\d{4}\b")
_GUELTIG_AB = re.compile(r"g[üu]ltig\s+ab", re.IGNORECASE)
_FRAGEN_TEXT = re.compile(r"\bfrage\s*\d+|\bfragenkatalog\b|\bl[öo]sung\s*\d+", re.IGNORECASE)


def classify(name: str, first_pages: Iterable[str] = ()) -> str:
    for doc_type, rx in _NAME_RULES_COMPILED:
        if rx.search(name):
            return doc_type
    text = " ".join(list(first_pages)[:3])
    if _FRAGEN_TEXT.search(text) and len(_FRAGEN_TEXT.findall(text)) >= 3:
        return "fragenkatalog"
    if _RIL_HEADER.search(text) and _GUELTIG_AB.search(text):
        return "ril"
    return "sonstiges"


def build_doc_types(pages: Iterable[dict[str, Any]], overrides: dict[str, str] | None = None) -> dict[str, Any]:
    first_pages: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        if int(p.get("page", 1)) <= 3:
            first_pages[p["source_file"]].append(p.get("text", "")[:3000])
        else:
            first_pages.setdefault(p["source_file"], [])
    overrides = overrides or {}
    docs: dict[str, dict[str, Any]] = {}
    for name, texts in first_pages.items():
        doc_type = overrides.get(name) or classify(name, texts)
        if doc_type not in DOC_TYPES:
            raise ValueError(f"Unbekannter Dokumenttyp {doc_type!r} fuer {name}")
        docs[name] = {
            "type": doc_type,
            "citable": DOC_TYPES[doc_type],
            "source": "override" if name in overrides else "rule",
        }
    counts = Counter(d["type"] for d in docs.values())
    return {"version": 1, "types": DOC_TYPES, "counts": dict(counts), "docs": docs}


def load_overrides(path: Path | None) -> dict[str, str]:
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def save_doc_types(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class DocTypeIndex:
    """Lesezugriff; ohne Artefakt ist alles Typ 'sonstiges' mit Gewicht 1.0."""

    def __init__(self, data: dict[str, Any] | None = None, weights: dict[str, float] | None = None):
        self.docs: dict[str, dict[str, Any]] = (data or {}).get("docs", {})
        self.weights: dict[str, float] = {**DEFAULT_WEIGHTS, **(weights or {})}

    @classmethod
    def load(cls, path: Path | None, weights: dict[str, float] | None = None) -> "DocTypeIndex":
        if not path or not Path(path).exists():
            return cls(None, weights)
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), weights)

    @property
    def enabled(self) -> bool:
        return bool(self.docs)

    def type_of(self, source_file: str) -> str:
        return self.docs.get(source_file, {}).get("type", "sonstiges")

    def weight_of(self, source_file: str) -> float:
        if not self.docs:
            return 1.0
        return float(self.weights.get(self.type_of(source_file), 1.0))

    def citable(self, source_file: str) -> bool:
        return bool(self.docs.get(source_file, {}).get("citable", True))
