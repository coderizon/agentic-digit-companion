"""Derive distinct, independently verifiable load-test cells from the municipal CSV."""
import csv
import hashlib
import io
import random
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path


def cases(documents):
    doc, = [d for d in documents.values() if d["kind"]=="Haushalt" and d["title"].endswith("2025/2026")]
    raw=Path(doc["original"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==doc["digest"]
    try:
        text=raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text=raw.decode("cp1252")
    table=csv.DictReader(io.StringIO(text,newline=""),dialect=csv.Sniffer().sniff(text.splitlines()[0],delimiters=";,"))
    rows=list(table)
    def key(r):
        return r["Produkt"].strip(),r["Amt / Dienststelle"].strip()
    counts=Counter(key(r) for r in rows)
    result=[]
    for line,r in enumerate(rows,2):
        product,agency=key(r)
        if counts[(product,agency)]!=1 or not re.fullmatch(r"\d{6}",product) or not re.fullmatch(r"AMT-\d{3}",agency):
            continue
        for y in (2025,2026):
            for metric in ("Erträge","Aufwendungen"):
                column=f"Ansatz {y} ({metric})"
                amount=Decimal(r[column].strip().replace(",","."))
                result.append({"id":f"zelle-{line}-{y}-{metric}",
                    "question":f"Wie hoch ist der Ansatz {y} für {metric} im Produkt {product}, Amt {agency[4:]}, im Freiburger Ergebnishaushalt?",
                    "want":{"betrag":str(amount),"zeile":line,"spalte":column,"quelle":doc["url"],"pruefsumme_csv":doc["digest"]},
                    "mentions":[],"link":None})
    random.Random(20261125).shuffle(result)
    return result
