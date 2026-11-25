"""Exact FR.ITZ table lookups grounded in a row of the downloaded original CSV."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


TITLES = {
    "einwohner":"Grunddaten zur Indikatorenbildung auf Ebene der Gesamtstadt - Bevölkerungsbestand",
    "armut":"Grunddaten zur Indikatorenbildung auf Ebene der Stadtbezirke - Soziales (Armutsgefährdungsquote)",
}


def build(gen):
    from import_sources import fetch
    docs = json.loads((gen / "sources.json").read_bytes())
    path = gen / f"fritz-{os.getpid()}.tmp"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE quellen (art TEXT PRIMARY KEY, titel TEXT, url TEXT, original TEXT, sha256 TEXT);
            CREATE TABLE werte (art TEXT, zeile INTEGER, jahr INTEGER, gebiet TEXT, felder TEXT,
                                PRIMARY KEY (art,zeile), UNIQUE(art,jahr,gebiet));
        """)
        for art, title in TITLES.items():
            catalogue = next(d for d in docs.values() if d["kind"] == "Datenkatalog" and d["title"] == title)
            record = json.loads(Path(catalogue["original"]).read_bytes())
            url = next(u for d in record["distributions"] for u in d.get("download_url", [])
                       if urlparse(u).scheme == "https" and urlparse(u).hostname == "fritz.freiburg.de"
                       and urlparse(u).path.endswith("content.csv"))
            raw = fetch(url).content
            digest = hashlib.sha256(raw).hexdigest()
            original = gen.parent / "originale" / (digest+".csv")
            if not original.exists():
                original.write_bytes(raw)
            if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                raise ValueError("Die Originaldatei weicht vom Abruf ab")
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = raw.decode("cp1252")
            db.execute("INSERT INTO quellen VALUES (?,?,?,?,?)", (art,title,url,str(original),digest))
            for n,row in enumerate(csv.DictReader(io.StringIO(decoded),delimiter=";"),2):
                db.execute("INSERT INTO werte VALUES (?,?,?,?,?)",
                           (art,n,int(row["Jahr"]),row.get("Stadtbezirk","Freiburg"),json.dumps(row,ensure_ascii=False)))
        print(db.execute("SELECT art, COUNT(*), MIN(jahr), MAX(jahr) FROM werte GROUP BY art").fetchall())
    path.replace(gen / "fritz.sqlite")


def table_answer(question, gen):
    q = question.casefold()
    if re.search(r"armutsgefährdung|armutsgefaehrdung",q):
        kind = "armut"
    elif re.search(r"einwohner|einwohnerinnen|bevölkerungszahl|wie viele (?:menschen|personen) (?:leben|wohn|lebt|wohnt)",q):
        kind = "einwohner"
    else:
        return None
    if not re.search(r"\bwie (?:viele|viel|hoch|groß)|\bwelche (?:einwohnerzahl|armutsgefährdungsquote)|\bwie hoch",q):
        return None
    if kind == "einwohner":
        simple = re.sub(r"\b(?:19|20)\d{2}\b|\bunter\s+\d+\s+jahre[n]?\b", " ", q)
        allowed = set(("wie viele viel hoch groß ist war hatte hat beträgt betrug zählte leben lebten wohnen wohnten "
                        "menschen personen einwohner einwohnerinnen und einwohner_innen einwohnerzahl bevölkerungszahl "
                        "welche der die das in im zum für von an mit stadt stadtgebiet freiburg breisgau "
                        "gesamtstadt jahr jahre gesamt insgesamt laut fritz fr es gab gibt").split())
        if any(t not in allowed for t in re.findall(r"[\wäöüß]+",simple)):
            return None
    if not (gen / "fritz.sqlite").is_file() or re.search(r"warum|ursache|prognose|voraussichtlich|anteil|\bwächst\b|durchschnitt|höchste|niedrigste|dichte|ausländer|geschlecht", q):
        return None
    years = list(dict.fromkeys(re.findall(r"\b(?:19|20)\d{2}\b",q)))
    if len(years)>1 or re.search(r"\bbis\b|\bseit\b|zunahme|veränder|vergleich",q):
        return None
    with sqlite3.connect(f"file:{gen / 'fritz.sqlite'}?mode=ro", uri=True) as db:
        title, url = db.execute("SELECT titel,url FROM quellen WHERE art=?", (kind,)).fetchone()
        districts = [x[0] for x in db.execute("SELECT DISTINCT gebiet FROM werte WHERE art='armut'")]
        matched = [d for d in districts if re.search(r"(?<!\w)"+re.escape(d.casefold())+r"(?!\w)",q)]
        if kind == "armut" and len(matched) != 1:
            return None
        if kind == "einwohner" and (matched or "freiburg" not in q or re.search(r"stadtteil|stadtbezirk|postleitzahl|\bplz\b",q)):
            return None
        area = matched[0] if kind=="armut" else "Freiburg"
        last = db.execute("SELECT MAX(jahr) FROM werte WHERE art=? AND gebiet=?", (kind,area)).fetchone()[0]
        year = int(years[0]) if years else last
        row = db.execute("SELECT zeile,felder FROM werte WHERE art=? AND gebiet=? AND jahr=?", (kind,area,year)).fetchone()
    if not row:
        return f"Für {area} im Jahr {year} enthält diese FR.ITZ-Tabelle keine Zeile. Ihr jüngster Eintrag für {area} ist von {last}. [{title}]({url})"
    n, fields = row[0], json.loads(row[1])
    if kind == "einwohner":
        under = re.search(r"unter\s+(\d+)\s*(?:j[aä]hr|jahr)", q)
        column = f"Anzahl Einwohner_innen unter {under[1]} Jahre" if under else "Anzahl Einwohner_innen"
        if column not in fields or not fields[column].isdigit() or re.search(r"über|älter|männ|weibli|weiblich|nichtdeutsch|neu zugezog|haushalt", q):
            return None
        pop = format(int(fields[column]),",").replace(",",".")
        answer = f"Für das Jahr {year} weist FR.ITZ für Freiburg {pop} Einwohnerinnen und Einwohner" + (f" unter {under[1]} Jahren" if under else "") + " aus."
    else:
        column = next(c for c in fields if c.startswith("Armutsgefährdungsquote"))
        answer = f"Für {area} weist die Tabelle für {year} eine Armutsgefährdungsquote von {fields[column]} Prozent aus, gemessen am städtischen Median."
    return answer + f" [{title} · CSV-Zeile {n}, Spalte „{column}“]({url})"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("generation",type=Path)
    build(parser.parse_args().generation.resolve(strict=True))
