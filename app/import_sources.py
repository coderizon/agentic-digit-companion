"""Erfasst öffentliche Freiburger Originalquellen in einer neuen, abgeschlossenen Generation."""
import csv
import hashlib
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import fitz
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag")) / "data"
ORTSRECHT = "https://www.freiburg.de/pb/206280.html"
PORTAL = "https://search.piveau.datenraum.freiburg.de/search"
RIS = "https://ris.freiburg.de/oparl/body/FR/paper"
GTFS = "https://www.vag-freiburg.de/fileadmin/gtfs/VAGFR.zip"
MOBIL = "https://mobidata-bw.de/api/3/action/package_search"
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Coderizon-Freiburg-OpenData-Pilot/1.0"})
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=(429, 502, 503, 504))))
LAST_CALL = {}


def fetch(url, **params):
    host = urlparse(url).hostname
    delay = 20.2 if host == "www.freiburg.de" else 0.4
    time.sleep(max(0, delay - (time.monotonic() - LAST_CALL.get(host, 0))))
    LAST_CALL[host] = time.monotonic()
    r = HTTP.get(url, params=params, timeout=(12, 95))
    r.raise_for_status()
    return r


def plain(value):
    return " ".join(BeautifulSoup(value, "html.parser").stripped_strings) if "<" in value and ">" in value else " ".join(value.split())


def normalized(value):
    return " ".join(value.split())


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Capture:
    def __init__(self):
        self.stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = ROOT / self.stamp
        self.path.mkdir(parents=True)
        self.originals = ROOT / "originale"
        self.originals.mkdir(exist_ok=True)
        self.docs = {}
        self.rows = []
        self.image_candidates = []
        self.notes = []

    def document(self, kind, title, url, raw, ext, license_info="", attribution="Stadt Freiburg im Breisgau", landing=None):
        digest = sha(raw)
        ident = sha(url.encode())[:22]
        out = self.originals / (digest + "." + ext)
        if not out.exists():
            tmp = out.with_suffix(".part")
            tmp.write_bytes(raw)
            tmp.replace(out)
        elif sha(out.read_bytes()) != digest:
            raise ValueError("Abweichendes Original: " + url)
        entry = dict(id=ident, kind=kind, title=normalized(title), url=url, landing=landing or url,
                     digest=digest, original=str(out), license=license_info, attribution=attribution)
        self.docs[ident] = entry
        return entry

    def passage(self, doc, page, text, locator, label=None):
        if not text.strip():
            return
        body = f"{doc['title']}\n{label or locator}\n{text.strip()}"
        self.rows.append(dict(id=f"{doc['id']}-{page}", source_path=doc["original"],
                              source_file=f"{doc['title']} ({doc['id']})", page=page, doc=doc["id"],
                              source_sha256=doc["digest"], locator=locator, text=body, char_count=len(body)))

    def pdf(self, kind, title, url, raw, landing=None):
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass:
                self.notes.append(f"Verschlüsseltes PDF ausgelassen: {url}")
                return
            doc = self.document(kind, title, url, raw, "pdf", landing=landing)
            for i, page in enumerate(pdf, 1):
                text = page.get_text("text", sort=True).replace("\u00ad", "")
                locator = f"PDF-Seite {i}"
                self.passage(doc, i, text, locator)
                # Visuelle Kandidaten stammen aus Originalgrafiken oder dichten Vektorplänen.
                if i <= 30:
                    images = page.get_image_info()
                    surface = max((fitz.Rect(im["bbox"]).get_area() for im in images), default=0) / max(page.rect.get_area(), 1)
                    map_page = any(w in (title + " " + text[:300]).casefold() for w in ("karte", "lageplan", "grenze", "anlage", "zonierung"))
                    if surface > .12 or map_page and len(page.get_drawings()) > 35:
                        self.image_candidates.append((min(surface, 1) + .35 * map_page, doc["id"], i))

    def law(self):
        soup = BeautifulSoup(fetch(ORTSRECHT).content, "html.parser")
        links = {}
        archive = None
        for a in soup.select("a[href]"):
            url = urljoin(ORTSRECHT, a["href"])
            path = unquote(urlparse(url).path)
            if urlparse(url).hostname != "www.freiburg.de" or "/ortsrecht/" not in path.lower():
                continue
            if path.lower().endswith(".zip"):
                archive = url
            elif path.lower().endswith(".pdf"):
                name = Path(path).name.casefold()
                title = re.sub(r"\s*\(\s*[\d.,]+\s*(?:KB|MB)\s*\)\s*$", "", a.get_text(" ", strip=True), flags=re.I)
                links[name] = (normalized(title), url)
        if not archive or not links:
            raise ValueError("Das Ortsrechtsverzeichnis liefert kein Archiv oder keine Einzelbelege")
        seen = set()
        with zipfile.ZipFile(io.BytesIO(fetch(archive).content)) as z:
            for entry in z.infolist():
                key = Path(entry.filename.replace("\\", "/")).name.casefold()
                if key in links and key not in seen and entry.file_size < 45_000_000:
                    title, url = links[key]
                    self.pdf("Ortsrecht", title, url, z.read(entry), landing=ORTSRECHT)
                    seen.add(key)
        if not seen:
            raise ValueError("Keine ZIP-Dateinamen stimmen mit dem öffentlichen Verzeichnis überein")
        self.notes.append(f"Ortsrecht: {len(seen)} von {len(links)} verlinkten PDF-Dateien aus dem Sammelarchiv zugeordnet.")
        print(self.notes[-1], flush=True)

    def portal(self):
        datasets = {}
        page = 0
        while True:
            data = fetch(PORTAL, filter="dataset", limit=100, page=page).json()["result"]
            batch = data["results"]
            if not batch:
                break
            for d in batch:
                datasets[d["id"]] = d
            page += 1
            if page * 100 >= data["count"]:
                break
        self.notes.append(f"Open-Data-Portal: {len(datasets)} eindeutige Katalogeinträge, API meldet {data['count']}.")
        for d in datasets.values():
            title = d.get("title", {}).get("de") or next(iter(d.get("title", {}).values()), "")
            if not title:
                continue
            descr = d.get("description", {}).get("de", "")
            dist = d.get("distributions", [])
            license_info = sorted({v.get("license", {}).get("resource", "") for v in dist} - {""})
            urls = [x["resource"] for x in d.get("landing_page", []) if x.get("resource","").startswith("https://")]
            landing = next(iter(urls), "https://ui.piveau.datenraum.freiburg.de/datasets/" + d["id"])
            doc = self.document("Datenkatalog", title, d["resource"], canonical(d), "json", ", ".join(license_info), landing=landing)
            keys = ", ".join(k["label"] for k in d.get("keywords", []) if k.get("label"))
            formats = ", ".join(sorted({v.get("format", {}).get("id", "") for v in dist} - {""}))
            self.passage(doc, 1, "\n".join(s for s in [plain(descr), "Schlagwörter: " + keys if keys else "", "Datenformate: " + formats if formats else ""] if s),
                         "Katalogeintrag")
            if "ergebnishaushalt" not in title.lower():
                continue
            for entry in dist:
                url = next((v for v in entry.get("download_url", []) if v.startswith("https://fritz.freiburg.de/") and ".csv" in v), None)
                if not url:
                    continue
                raw = fetch(url).content
                budget = self.document("Haushalt", title, url, raw, "csv", entry.get("license", {}).get("resource",""), landing=landing)
                try:
                    table = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    table = raw.decode("cp1252")
                sample = csv.reader(io.StringIO(table), delimiter=";")
                headers = next(sample)
                for n, row in enumerate(sample, 2):
                    cells = [f"{h.strip()}: {v.strip()}" for h,v in zip(headers,row) if h.strip() and v.strip()]
                    self.passage(budget, n, "\n".join(cells), f"CSV-Zeile {n} (inkl. Kopfzeile)")
                break
        print(self.notes[-1], flush=True)

    def papers(self):
        first = fetch(RIS).json()
        high = first["pagination"]["totalPages"]
        unique = {}
        for page in range(max(1,high-2),high+1):
            for p in fetch(f"{RIS}/page/{page}").json()["data"]:
                if not p.get("deleted") and p.get("name"):
                    unique[p["id"]] = p
        ordered = sorted(unique.values(), key=lambda p:(p.get("date",""),p["id"]), reverse=True)
        full = 0
        for p in ordered:
            title = f"{p.get('reference','')} · {normalized(p['name'])}"
            main = p.get("mainFile") or {}
            # Nur die Ratsvorlage selbst. Gutachten Dritter werden nicht mit importiert.
            attrs = {k:p[k] for k in ("id","name","reference","date","paperType","web") if k in p}
            doc = self.document("Ratsinfo", title, p["id"], canonical(attrs), "json", landing=p.get("web") or p["id"])
            desc = f"Betreff: {normalized(p['name'])}\nDrucksache: {p.get('reference','')}\nDatum: {p.get('date','')}\nArt: {p.get('paperType','')}"
            self.passage(doc, 1, desc, "OParl-Drucksache")
            url = main.get("accessUrl") or main.get("downloadUrl")
            if full < 40 and url and url.startswith("https://ris.freiburg.de/") and main.get("size",0) < 8_000_000:
                try:
                    raw = fetch(url).content
                    if not raw.startswith(b"%PDF"):
                        raise ValueError("kein PDF")
                    self.pdf("Ratsvorlage",title,url,raw,landing=p.get("web") or p["id"])
                    full += 1
                except (requests.RequestException, fitz.FileDataError, ValueError) as e:
                    self.notes.append(f"Vorlage ausgelassen: {p['id']} ({type(e).__name__})")
        self.notes.append(f"Ratsinfo: {len(unique)} Drucksachen aus den letzten drei API-Seiten, {full} Hauptdokumente als PDF. Gesamtanzeige: {first['pagination']['totalElements']}.")
        print(self.notes[-1], flush=True)

    def mobility(self):
        raw = fetch(GTFS).content
        doc = self.document("VAG-Fahrplan", "VAG Freiburg · Linien im veröffentlichten GTFS-Fahrplan", GTFS, raw, "zip",
                            "DL-DE BY 2.0", "Datensatz der VAG Freiburg")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            rows = csv.DictReader(io.StringIO(z.read("routes.txt").decode("utf-8-sig")))
            for n,row in enumerate(rows,2):
                self.passage(doc,n,"\n".join(f"{k}: {v}" for k,v in row.items() if v),f"routes.txt · Zeile {n}",
                             f"Fahrplanlinie {row.get('route_short_name','')} (Sollfahrplan)")
        try:
            found = fetch(MOBIL,q="Freiburg",rows=100).json()["result"]["results"]
            for d in found:
                url="https://mobidata-bw.de/dataset/"+d["name"]
                owner=(d.get("organization") or {}).get("title") or "MobiData BW"
                doc=self.document("MobiData BW",d.get("title") or d["name"],url,canonical(d),"json",d.get("license_title",""),owner)
                self.passage(doc,1,plain(d.get("notes") or "") + "\n" + ", ".join(v.get("name","") for v in d.get("resources",[])), "Datensatzbeschreibung")
        except (requests.RequestException, KeyError, ValueError) as e:
            self.notes.append(f"MobiData BW nicht importiert: {type(e).__name__}")

    def publish(self):
        if len(set(r["id"] for r in self.rows)) != len(self.rows):
            raise ValueError("Mehrdeutige Fundstellen")
        (self.path/"pages.jsonl").write_bytes(b"".join(canonical(p)+b"\n" for p in self.rows))
        (self.path/"sources.json").write_bytes(canonical(self.docs))
        counts = Counter()
        selected = []
        for score,d,n in sorted(self.image_candidates,reverse=True):
            if counts[d] < 2:
                counts[d] += 1
                selected.append({"doc":d,"page":n})
            if len(selected)>=48:
                break
        (self.path/"visual.json").write_bytes(canonical(selected))
        stats = dict(stand=self.stamp,quellen=Counter(d["kind"] for d in self.docs.values()),fundstellen=len(self.rows),
                     bildseiten=len(selected),notizen=self.notes)
        (self.path/"stand.json").write_bytes(canonical(stats))
        # A fresh capture has no text, image or table index yet. Live services stay
        # on their verified generation while a new one is being built and tested.
        print(json.dumps(stats,ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run = Capture()
    run.law()
    run.portal()
    run.papers()
    run.mobility()
    run.publish()
