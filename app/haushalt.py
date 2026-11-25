"""Exact lookups in the original, versioned FR.ITZ municipal budget CSVs."""
import csv
import hashlib
import io
import json
import logging
import re
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path


YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
PRODUCT = re.compile(r"\b(?:produkt(?:nummer|nr\.?)?|haushaltsposition)\s*[:#-]?\s*(\d{6})\b", re.I)
OFFICE = re.compile(r"\b(?:amt(?:snummer)?|dienststelle)\s*[-:#%]?\s*(\d{1,3})\b", re.I)
COLUMN = re.compile(r"(Ansatz|Ergebnis)\s+(\d{4})\s+\((.+)\)")
PLAN = re.compile(r"\b(?:plan(?:en|t|ung|wert\w*)?|geplant\w*|haushaltsplan\w*|ansatz|angesetzt\w*|veranschlag\w*|vorgesehen\w*|eingeplant\w*|rechnet|erwartet\w*|soll\w*|will)\b", re.I)
ACTUAL = re.compile(r"tatsächlich|wirklich|ist[- ]?(?:wert|betrag|zahlen)|\bergebnis\b|\bhat .*?(?:ausgegeben|eingenommen)|\bgab .*? aus\b", re.I)
MONEY = re.compile(r"haushalt|budget|etat|finanzplan|einnahm|eingenommen|einnimmt|einnehmen|ertr[äa]g|erlös|ausgab|ausg[ei]b|ausgegeb|gab .*? aus\b|aufwand|aufwend|saldo|zuschussbedarf|veranschlag|eingeplant|wie viel (?:geld|plant)|wie teuer", re.I)
OUT_OF_SCOPE = re.compile(r"\b(?:warum|weshalb|wieso|prozent|durchschnitt|pro kopf|durchschnittlich|teuerste\w*|höchste\w*|niedrigste\w*|vergleiche?|differenz|unterschied|insgesamt|zusammengezählt|investitionshaushalt|finanzhaushalt|istvergleich)\b|eintritt|ticketpreis|eintrittskarte", re.I)
MEASURES = {
    "Erträge": re.compile(r"ertr[äa]g|einnahm|eingenommen|einnimmt|einnehmen|erlös", re.I),
    "Aufwendungen": re.compile(r"aufwend|aufwand|ausgab|ausg[ei]b|ausgegeb|gab .*? aus\b|kostet .*?die stadt|kostet es die stadt", re.I),
    "Saldo": re.compile(r"saldo|zuschussbedarf|unterdeckung", re.I),
}
GENERIC = set("stadt stadtverwaltung freiburg freiburger amt dienststelle ortsverw ortsverwaltung eigenbetrieb allgemeine allgemeiner allgemeinen kommunale stadtische staedtische oeffentliche produkt ergebnis ergebnishaushalt ertraege aufwendungen haushalt einnahmen ausgaben weitere sonstige sonstigen bereich mit und der die das des den dem fuer von zur zum ohne einer eines auf nach".split())
ASK = "Für die Haushaltsauskunft brauche ich"


def words(text):
    text = text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return re.findall(r"[a-z0-9]+(?:\?[a-z0-9]+)*", text)


def display(text):
    for bad, good in (("Stra?en", "Straßen"), ("Stadtk?mmerei", "Stadtkämmerei"),
                      ("B?rger", "Bürger"), ("B?ro", "Büro"), ("f?r", "für")):
        text = text.replace(bad, good)
    return text


def named_parts(label):
    tokens = words(label)
    informative = {t for t in tokens if len(t) >= 4 and t not in GENERIC and not t.isdigit()}
    if informative:
        yield " ".join(tokens)
    yield from informative
    if "volkshochschule" in tokens:
        yield "vhs"
    if "musikschulen" in tokens:
        yield "musikschule"


class Edition:
    def __init__(self, doc):
        self.title = doc["title"]
        self.url = doc["url"]
        self.years = set(YEAR.findall(self.title))
        raw = Path(doc["original"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc["digest"]:
            raise ValueError("Prüfsumme der Haushaltsdatei stimmt nicht")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        header = text.splitlines()[0]
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";" if ";" in header else ",")
        self.columns = {}
        for column in reader.fieldnames or ():
            match = COLUMN.fullmatch(column.strip())
            if match:
                mode, year, measure = match.groups()
                self.columns[mode, year, measure.split("/")[0]] = column
        if not self.columns or "Produkt" not in reader.fieldnames or "Amt / Dienststelle" not in reader.fieldnames:
            raise ValueError("Unbekannte Spalten in der Haushaltsdatei")

        self.rows = {}
        names = defaultdict(set)
        for line, values in enumerate(reader, 2):
            row = {k: v.strip() for k, v in values.items() if k and v is not None}
            self.rows[line] = row
            office = re.sub(r"^(?:%\s*)?(?:THH\s*)?\d+\s*", "", row["Amt / Dienststelle Bezeichnung"])
            for label in (office, row["Produktbezeichnung"]):
                for part in named_parts(label):
                    names[part].add(line)
        aliases = sorted(names, key=lambda key: (-len(key), key))
        self.alias_rows = [names[key] for key in aliases]
        # Historic FR.ITZ exports sometimes contain a literal '?' in place of an umlaut or ß.
        patterns = [re.escape(key).replace(r"\?", r"(?:ae|oe|ue|ss|\?)") for key in aliases]
        self.matcher = re.compile(r"(?<![a-z0-9?])(?:" + "|".join(
            f"(?P<a{i}>{pattern}s?)" for i, pattern in enumerate(patterns)) + r")(?![a-z0-9?])")

    def matches(self, question, products, offices):
        candidates = set(self.rows)
        named = list(self.matcher.finditer(" ".join(words(question))))
        if not (products or offices or named):
            return None
        for product in products:
            candidates = {n for n in candidates if self.rows[n]["Produkt"] == product}
        for office in offices:
            candidates = {n for n in candidates if self.rows[n]["Amt / Dienststelle"] == office}
        for match in named:
            candidates &= self.alias_rows[int(match.lastgroup[1:])]
        # Never discard an explicitly named, unrecognised office in favour of a unique product.
        if not offices and re.search(r"\b(?:amt|dienststelle)\s+(?:für\s+)?[a-zäöüß]", question, re.I):
            if not any(set(words(m[0])) & set(words(self.rows[n]["Amt / Dienststelle Bezeichnung"]))
                       for m in named for n in candidates):
                return set()
        return candidates


@lru_cache(maxsize=2)
def editions(generation):
    docs = json.loads((generation / "sources.json").read_bytes())
    return tuple(Edition(doc) for doc in docs.values() if doc["kind"] == "Haushalt")


def ask_position(edition, candidates):
    rows = [edition.rows[n] for n in sorted(candidates)]
    offices = {row["Amt / Dienststelle"] for row in rows}
    products = {row["Produkt"] for row in rows}
    if len(products) > 1:
        options = dict.fromkeys(display(r["Produktbezeichnung"]) + " (" + r["Produkt"] + ")" for r in rows)
        dimension = "das Produkt"
    elif len(offices) > 1:
        options = dict.fromkeys(display(r["Amt / Dienststelle Bezeichnung"]) for r in rows)
        dimension = "das Amt oder den Stadtteil"
    else:
        return ASK + " eine genauere Bezeichnung der Haushaltsposition."
    choices = list(options)[:4]
    return f"{ASK} {dimension}. Meinst du " + " oder ".join(choices) + (" oder eine weitere Position?" if len(options)>4 else "?")


def amount_answer(edition, line, column, year, mode, measure):
    row = edition.rows[line]
    literal = row[column]
    if not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", literal):
        return "Für diese Haushaltsposition ist kein lesbarer Betrag veröffentlicht."
    value = Decimal(literal.replace(",", "."))
    amount = f"{value:,.2f}".translate(str.maketrans({",":".", ".":",", "-":"−"}))
    kind = "Planwert" if mode == "Ansatz" else "Ist-Wert"
    subject = display(row["Produktbezeichnung"])
    office = display(row["Amt / Dienststelle Bezeichnung"])
    return (f"Für {subject} (Produkt {row['Produkt']}, {office}) beträgt der {kind} für {measure} "
            f"im Jahr {year} **{amount} €**.\n\n"
            f"[{edition.title} · CSV-Zeile {line} · Spalte „{column}“]({edition.url})")


def budget_answer(question, generation):
    if not MONEY.search(question) or OUT_OF_SCOPE.search(question):
        return None
    if re.search(r"§\s*\d", question) and not PRODUCT.search(question):
        # A statutory expense allowance is a legal rule, even though its
        # wording also contains the municipal accounting term "Aufwand".
        return None
    measures = [name for name, pattern in MEASURES.items() if pattern.search(question)]
    years = list(dict.fromkeys(YEAR.findall(question)))
    products = set(PRODUCT.findall(question))
    offices = {"AMT-" + n.zfill(3) for n in OFFICE.findall(question)}
    offices.update("THH_" + n for n in re.findall(r"\bTHH[ _-]*(\d+)\b", question, re.I))
    explicit = bool(products or offices or re.search(r"haushalts(?:plan|ansatz|produkt)|ergebnishaushalt|doppelhaushalt|\betat\b", question, re.I))
    if not explicit and not (measures or PLAN.search(question)):
        return None
    try:
        archive = editions(generation)
    except (OSError, ValueError, KeyError):
        logging.getLogger(__name__).exception("Haushaltstabelle konnte nicht verifiziert werden")
        return "Die Haushaltsdatei lässt sich derzeit nicht prüfen. Eine belegte Betragsauskunft ist gerade nicht möglich."
    if not archive:
        return None
    mode = "Ergebnis" if ACTUAL.search(question) and not PLAN.search(question) else "Ansatz"
    if len(years) > 1 or len(products) > 1 or len(offices) > 1 or len(measures) > 1:
        return None
    year = years[0] if years else ""
    possible = [e for e in archive if any(k[:2] == (mode,year) for k in e.columns)]
    primary = [e for e in possible if year in e.years]
    selected = primary or possible
    edition = max(selected or archive, key=lambda e: max(e.years, default=""))
    candidates = edition.matches(question, products, offices)
    if candidates is None and not explicit:
        return None
    if not year:
        return ASK + " das Jahr. Für welches Haushaltsjahr möchtest du den Betrag wissen?"
    if not selected:
        link = f"[{edition.title}]({edition.url})"
        if mode == "Ergebnis":
            return f"Für {year} ist in den eingelesenen Ergebnishaushalten kein Ist-Betrag veröffentlicht. {link}"
        return f"Für {year} ist in den eingelesenen Ergebnishaushalten kein Planwert veröffentlicht. {link}"
    if not candidates:
        if candidates is None:
            return ASK + " das Produkt oder die Dienststelle. Welche Haushaltsposition meinst du?"
        return (f"Für diese Kombination finde ich im Ergebnishaushalt {year} keine eindeutige Zeile. "
                f"[{edition.title}]({edition.url})")
    if len(candidates) > 1:
        return ask_position(edition, candidates)
    if not measures:
        return ASK + " die gewünschte Angabe. Geht es um Erträge, Aufwendungen oder den Saldo?"
    column = edition.columns.get((mode, year, measures[0]))
    if not column:
        return f"Diese Spalte ist im Ergebnishaushalt {year} nicht veröffentlicht. [{edition.title}]({edition.url})"
    return amount_answer(edition, next(iter(candidates)), column, year, mode, measures[0])


def clarification_question(question, messages):
    """Carry short answers to a budget clarification across consecutive chat turns."""
    if len(question) > 130 or re.search(r"^(?:wie|was|wann|weshalb|warum|wer|zeig|welch\w*)\b", question.strip(), re.I):
        return None
    parts = [question]
    i = len(messages) - 2
    while i >= 1 and len(parts) < 5:
        reply, previous = messages[i], messages[i-1]
        if reply.get("role") != "assistant" or not str(reply.get("content","")).startswith(ASK):
            break
        if previous.get("role") != "user" or not isinstance(previous.get("content"), str):
            break
        parts.append(previous["content"][:1800])
        i -= 2
    return " ".join(reversed(parts)) if len(parts)>1 else None
