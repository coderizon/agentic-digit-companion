"""Exact public register and timetable answers, backed by the original bytes."""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


REF = re.compile(r"\b[A-Z]{1,3}-\d{2}/\d+\b", re.I)
LINE = re.compile(r"\b(?:vag[- ]linie|linie|buslinie|straßenbahnlinie)\s*(\d+[a-z]?)\b", re.I)
AMBIGUOUS = re.compile(r"\b(warum|weshalb|bewert\w*|kosten|wer|stimm\w*|frist\w*|zuständig\w*|wann fährt|wie oft|wann hält|takt\w*|verspät\w*|beschlossen|genehmigt|abstimmung\w*|verbindlich\w*|rechtskräftig\w*|begründ\w*)\b", re.I)


def original(doc):
    data = Path(doc["original"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != doc["digest"]:
        raise ValueError("Municipal source checksum differs")
    return data


class Registers:
    def __init__(self, docs):
        self.papers = {ref.group().upper(): doc for doc in docs.values()
                       if doc["kind"] == "Ratsinfo" and (ref := REF.match(doc["title"]))}
        self.timetable = next((doc for doc in docs.values() if doc["kind"] == "VAG-Fahrplan"), None)

    @lru_cache(maxsize=1)
    def routes(self):
        groups = defaultdict(list)
        if self.timetable:
            with zipfile.ZipFile(io.BytesIO(original(self.timetable))) as archive:
                table = io.StringIO(archive.read("routes.txt").decode("utf-8-sig"))
                for n, row in enumerate(csv.DictReader(table), 2):
                    label = row["route_long_name"].strip()
                    if label:
                        groups[row["route_short_name"].strip().casefold()].append((n, label))
        return groups

    def answer(self, question):
        if AMBIGUOUS.search(question):
            return None
        refs = REF.findall(question)
        if len(refs) == 1 and re.search(r"Datum|Betreff|Thema|Art von Vorlage|Beschlussvorlage|wie lautet.*Titel", question, re.I):
            ref = refs[0].upper()
            doc = self.papers.get(ref)
            if doc is not None:
                item = json.loads(original(doc))
                if not item.get("name") or not item.get("date"):
                    return None
                date = item["date"].split("T", 1)[0]
                fields = date.split("-")
                display = ".".join(reversed(fields)) if len(fields) == 3 else date
                kind = f" Art im Ratsinfo: {item['paperType']}." if item.get("paperType") else ""
                return "ratsinfo", f"{ref} vom {display}: {item['name']}.{kind} [{ref} · OParl-Drucksache]({doc['landing']})"
        lines = LINE.findall(question)
        if len(lines) == 1 and re.search(r"Endpunkte|Linienweg|von wo bis wo|Streckenverlauf|zwischen welchen", question, re.I):
            matches = self.routes().get(lines[0].casefold(), ())
            if matches:
                variants = {}
                for n, label in matches:
                    variants.setdefault(label, n)
                parts = [f"{label} [VAG · routes.txt · Zeile {n}]({self.timetable['url']})"
                         for label, n in variants.items()]
                return "fahrplan", f"Linie {lines[0]} im veröffentlichten Sollfahrplan: " + " / ".join(parts)
        return None
