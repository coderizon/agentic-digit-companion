"""Permission-scoped service matching and draft preparation for a municipal demand interview."""
import re
import unicodedata
from uuid import UUID

from ui_knowledge import label


MODEL = "freiburg-digitom-bedarf"
NAME = "DIGITOM · Bedarfsassistent"
START = "Unser Außendienst möchte kaputte Spielgeräte per Handy mit Foto und Standort melden. Gibt es dafür schon einen Service?"
FIELDS = ("Anliegen", "Zielgruppe", "Empfänger", "Erfolgskriterium")
QUESTIONS = {
    "Anliegen": "Welches konkrete Problem möchtet ihr lösen?",
    "Zielgruppe": "Wer soll den Dienst nutzen?",
    "Empfänger": "Welche Stelle soll die Meldungen oder Anfragen bearbeiten?",
    "Erfolgskriterium": "Woran würdet ihr erkennen, dass sich der Ablauf verbessert hat?",
}
SOURCE_FIELDS = ("Typ", "Kennung", "Titel", "Leistung", "Zuständig", "Suchbegriffe", "Bezugsservice",
                 "Erfahrung", "Pflichtangaben", "Hinweis", "Stand", "Erläuterung")
SOURCE_FIELD = re.compile(r"(?:^|\s)(" + "|".join(SOURCE_FIELDS) + r")\s*:\s*", re.I)
GENERIC = {"digital", "mobile", "online", "service", "erfassen", "melden", "buchen", "fur", "mit"}


def words(text):
    value = unicodedata.normalize("NFKD", text.casefold())
    return set(re.findall(r"[a-z0-9]{3,}", "".join(ch for ch in value if not unicodedata.combining(ch))))


def plain(text):
    """Keep text from submissions and source records separate from our Markdown links."""
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", text)


def source_rows(scope):
    """Read each record and its citation from the same search hit. Never join across files."""
    rows, seen = [], set()
    for kb in scope["collections"]:
        for hit in kb["hits"]:
            fid = str(UUID(hit["file"]))
            filename = label(hit["name"]) or "Dokument"
            page = hit.get("page")
            target = f"/api/v1/files/{fid}/content"
            if page and filename.lower().endswith(".pdf"):
                target += f"#page={page}"
            link = f"[{filename}" + (f" · S. {page}" if page else "") + f"]({target})"
            text = hit["text"]
            for record in re.split(r"(?=\bTyp\s*:)", text, flags=re.I):
                found = list(SOURCE_FIELD.finditer(record))
                parts = {}
                for i, match in enumerate(found):
                    end = found[i + 1].start() if i + 1 < len(found) else len(record)
                    value = re.split(r"\n\s*\n", record[match.end():end].strip(), maxsplit=1)[0]
                    field = match[1].casefold()
                    if field in {"pflichtangaben", "suchbegriffe"}:
                        # The WebUI splitter can flatten paragraphs to one line.
                        value = re.split(r"[.!?]|\s+(?=(?:Das|Die|Der|Ein|Eine|Für|Zur|Im)\s)", value, maxsplit=1)[0]
                    parts[field] = " ".join(value.split())[:600]
                key = (fid, tuple(parts.items()))
                if parts.get("typ") and parts.get("titel") and key not in seen:
                    rows.append(dict(parts, link=link))
                    seen.add(key)
    return rows


def match_services(question, services):
    asked = words(question) - GENERIC
    scored = []
    for row in services:
        vocabulary = words(row.get("suchbegriffe", "") + " " + row["titel"]) - GENERIC
        matches = {word for word in asked if any(
            word == key or (min(len(word), len(key)) >= 5 and word[:5] == key[:5])
            or (len(key) >= 6 and word.endswith(key)) for key in vocabulary)}
        score = sum(2 if len(word) >= 7 else 1 for word in matches)
        if score >= 2:
            scored.append((score, row))
    scored.sort(key=lambda item: -item[0])
    return [row for score, row in scored if score >= scored[0][0] - 1] if scored else []


FIELD_PATTERNS = {
    "Zielgruppe": (r"(?:Zielgruppe|Nutzerkreis|Meldende)\s*:\s*",
                   r"(?:melden|nutzen|buchen)\s+sollen\s+", r"(?:gedacht|bestimmt)\s+ist\s+(?:es|das|der Dienst)\s+für\s+"),
    "Empfänger": (r"(?:Empf[aä]nger|Bearbeitung|Zielstelle)\s*:\s*",
                  r"(?:Meldungen?|Anfragen?|Buchungen?|Terminanfragen?)\s+(?:soll(?:en)?\s+(?:direkt\s+)?(?:an|zu[mr]?|bei[m]?)|gehen\s+an)\s+",
                  r"(?:bearbeiten|empfangen)\s+soll\s+"),
    "Erfolgskriterium": (r"(?:Erfolgskriterium|Erfolg|Ziel|Nutzen)\s*:\s*",
                        r"(?:unser\s+)?Ziel\s+(?:ist|wäre)(?:\s+es)?\s*,?\s*",
                        r"(?:erfolgreich|gelungen)\s+(?:ist|wäre)\s+es,?\s+wenn\s+"),
}
BRIEF = re.compile(r"steckbrief|entwurf|zusammenfass", re.I)
END_INSTRUCTION = re.compile(r"(?:^|[.?!;]\s*|,\s*)(?:bitte\s+)?(?:erstell\w*|schreib\w*|mach\w*|formulier\w*)\b.{0,20}(?:steckbrief|entwurf).*", re.I)


def interview(turns, required, extract):
    facts = {}
    first = re.sub(r"^(?:neuer Bedarf|anderes Anliegen|neues Thema)\s*[:.,]?\s*", "", turns[0], flags=re.I)
    first = re.split(r"(?<=[.!?])\s+", first.strip())[0]
    first = re.sub(r"^(?:Anliegen|Problem)\s*:\s*", "", first, flags=re.I)
    if first and not (BRIEF.search(first) and len(first.split()) < 7):
        facts["Anliegen"] = first[:480].rstrip(" .?")
    actor = re.search(r"\b((?:unser|der) Außendienst|(?:Bürgerinnen und Bürger|Mitarbeitende des Außendienstes))\s+(?:möchte\w*|will|wollen|soll\w*)\b", first, re.I)
    if actor:
        facts["Zielgruppe"] = actor[1]
    for turn in turns:
        statement = END_INSTRUCTION.sub("", turn)
        for field, patterns in FIELD_PATTERNS.items():
            for pattern in patterns:
                found = re.search(r"\b(?:" + pattern + r")([^.;!?\n]+)", statement, re.I)
                if found:
                    value = re.split(r",?\s+(?:und\s+)?(?:Zielgruppe|Empfänger|Erfolgskriterium|Ziel)\s*:", found[1], flags=re.I)[0]
                    value = re.sub(r"\s+(?:landen|ankommen|gehen)$", "", value.strip(" ,"), flags=re.I)
                    if value:
                        facts[field] = value[:320]
                    break
        for field in required:
            if field not in FIELDS:
                stated = re.search(r"(?:^|[\s;])" + re.escape(field) + r"\s*:\s*([^.;!?\n]+)", statement, re.I)
                if stated:
                    facts[field] = stated[1].strip()[:320]
    # Free-form replies are accepted as well. The model may only copy spans
    # from users' statements, never resolve a missing field from the catalogue.
    missing = [field for field in required if field != "Anliegen" and field not in facts]
    clarification = [s for s in turns[1:] if len(s.split()) >= 5 and not re.match(
        r"(?:bitte\s+)?(?:prüf\w*|zeig\w*|erstell\w*|welch\w*|gibt es)\b", s, re.I)]
    if missing and extract and clarification:
        copied = extract(turns, missing)
        for field in missing:
            value = copied.get(field)
            if isinstance(value, str) and 2 <= len(value) <= 320 and any(value in turn for turn in turns):
                facts[field] = value
    return facts


def respond(scope, *, extract=None, unstructured=None):
    if not scope:
        return "Wähle im Chat über **#** eine freigegebene Wissenssammlung mit Servicekatalog oder Bedarfsunterlagen aus."
    turns = scope.get("turns") or [scope["question"]]
    rows = source_rows(scope)
    catalog = [r for r in rows if r["typ"].casefold() == "it-service"]
    matches = match_services(turns[0], catalog)
    if len(matches) > 1 and len(turns) > 1:
        selected = match_services(turns[-1], matches)
        if len(selected) == 1:
            matches = selected
    elif len(turns) > 1:
        selected = match_services(turns[-1], catalog)
        if len(selected) == 1 and selected != matches and not BRIEF.search(turns[-1]):
            matches = selected
            turns = turns[-1:]
    brief_requested = bool(BRIEF.search(turns[-1]))
    parts = ["*Übungsfall mit fiktiven Quellen.*"] if scope.get("demo") else []
    service = matches[0] if len(matches) == 1 else None
    if service:
        description = plain(service.get("leistung", "")) if not brief_requested else ""
        owner = ("Zuständig ist " + plain(service["zuständig"]) + ".") if service.get("zuständig") else "Die Serviceverantwortung ist offen."
        parts.append(" ".join(x for x in ("**Passender Service:**", plain(service["titel"]) + ".", description, owner, service["link"]) if x))
        similar = [r for r in rows if r["typ"].casefold() == "vergleichsbedarf" and service.get("kennung")
                   and r.get("bezugsservice", "").casefold() == service["kennung"].casefold()]
        if similar:
            case = similar[0]
            lesson = plain(case.get("erfahrung", "")) if not brief_requested else ""
            parts.append(" ".join(x for x in ("**Vergleichsfall:**", plain(case["titel"]) + ".", lesson, case["link"]) if x))
    elif matches:
        parts.append("**Mehrere passende Katalogeinträge.** Welche dieser Leistungen kommt eurem Anliegen am nächsten?\n\n"
                     + "\n".join("- " + plain(r["titel"]) + " " + r["link"] for r in matches[:4]))
        if not brief_requested:
            return "\n\n".join(parts)
    elif not catalog and any(kb["hits"] for kb in scope["collections"]) and unstructured:
        grounded = unstructured(turns[0])
        parts.append(grounded or "In der ausgewählten Sammlung ist für dieses Anliegen kein passender Service belegt.")
    else:
        parts.append("**Serviceprüfung:** In den gefundenen Katalogeinträgen ist für dieses Anliegen kein passender Service belegt. Die Zuständigkeit bleibt offen.")
    template = next((r for r in rows if r["typ"].casefold() == "steckbriefvorlage" and r.get("pflichtangaben")), None)
    required = [v.strip() for v in re.split(r"[,;]", template["pflichtangaben"]) if 0 < len(v.strip()) <= 50] if template else list(FIELDS)
    required = list(dict.fromkeys(["Anliegen"] + required))
    facts = interview(turns, required, extract)
    missing = [field for field in required if not facts.get(field)]
    if brief_requested or not missing:
        parts.insert(int(bool(scope.get("demo"))), "**Bedarfssteckbrief · Entwurf zur fachlichen Prüfung**\n\n" +
                     "\n".join("- **" + label(field) + ":** " + plain(facts.get(field, "offen")) for field in required))
        if template:
            parts.append("Vorlage: " + template["link"])
    if missing:
        parts.append("**Noch zu klären:** " + " ".join(QUESTIONS.get(f, f"Welche Angabe gibt es für „{label(f)}“?") for f in missing))
    elif brief_requested:
        parts.append("Das Fachamt kann den Entwurf prüfen und anschließend in seine Vorlage übernehmen.")
    return "\n\n".join(parts)
