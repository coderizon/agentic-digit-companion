"""Render municipal PDFs and reuse the original EVIE page/region indexes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pymupdf

from evie_index import EvieEncoder, EvieIndex, build_index, digest, write_json


ROOT = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag"))


class Catalog:
    def __init__(self, generation, mode):
        self.folder = generation / "bildindex"
        metadata = json.loads((self.folder / "katalog.json").read_bytes())
        rows = [item for item in metadata if item["type"] == mode]
        self.pages = {item["key"]: SimpleNamespace(**item) for item in rows}
        self.pool_sha256 = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()

    def material(self, key):
        page = self.pages[key]
        path = self.folder / (key + ".png")
        if digest(path) != page.image_sha256:
            raise ValueError("Bild und Original passen nicht zusammen")
        return SimpleNamespace(path=path)


def prepare(generation):
    folder = generation / "bildindex"
    folder.mkdir(exist_ok=True)
    docs = json.loads((generation / "sources.json").read_bytes())
    rows = []
    for item in json.loads((generation / "visual.json").read_bytes()):
        doc = docs[item["doc"]]
        if digest(doc["original"]) != doc["digest"]:
            raise ValueError("Abweichendes Original: " + doc["title"])
        with pymupdf.open(doc["original"]) as pdf:
            page = pdf[item["page"] - 1]
            rect = page.rect
            clips = [("seite", rect)]
            # Also index the largest substantial raster illustration as its own region.
            blocks = [pymupdf.Rect(img["bbox"]) & rect for img in page.get_image_info()]
            clips += [("ausschnitt", box) for box in sorted(blocks, key=lambda b: -b.get_area())
                      if 0.09 * rect.get_area() < box.get_area() < 0.88 * rect.get_area()][:1]
            for mode, box in clips:
                key = f"{doc['id']}-{item['page']}-{mode}"
                out = folder / (key + ".png")
                scale = 1000 / max(box.width, box.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=box, alpha=False)
                pix.save(str(out))
                rows.append(dict(key=key, type=mode, source=doc["title"], source_sha256=doc["digest"],
                    doc=doc["id"], page=item["page"], image_sha256=digest(out), box=list(box)))
    write_json(folder / "katalog.json", rows)


class VisualSearch:
    def __init__(self, generation):
        self.encoder = EvieEncoder(ROOT / "models/evie")
        self.indexes = []
        for mode in ("seite", "ausschnitt"):
            catalog = Catalog(generation, mode)
            if catalog.pages:
                self.indexes.append((catalog, EvieIndex(catalog, catalog.folder / mode)))

    def find(self, question, docids):
        vec = self.encoder.question(question)
        ranked = []
        for catalog, index in self.indexes:
            selection = [i for i, k in enumerate(index.page_ids) if catalog.pages[k].doc in docids]
            for match in index.rank(vec, 3, candidates=selection):
                k = match["page_id"]
                ranked.append((match["score"], catalog.pages[k], catalog.material(k).path))
        ranked.sort(key=lambda r: -r[0])
        return ranked


def read_labels(generation):
    """Read the printed labels independently of the vision model."""
    catalog = Catalog(generation, "seite")
    labels = {}
    for key in catalog.pages:
        path = catalog.material(key).path
        result = subprocess.run(["tesseract", str(path), "stdout", "-l", "deu", "--psm", "11"],
                                capture_output=True, text=True, timeout=90, check=True)
        labels[key] = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    write_json(catalog.folder / "beschriftungen.json", labels)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("generation", type=Path)
    parser.add_argument("--nur-beschriftungen", action="store_true")
    args = parser.parse_args()
    gen = args.generation.resolve(strict=True)
    if not args.nur_beschriftungen:
        prepare(gen)
        encoder = EvieEncoder(ROOT / "models/evie")
        for mode in ("seite", "ausschnitt"):
            catalog = Catalog(gen, mode)
            if catalog.pages:
                build_index(catalog, catalog.folder / mode, encoder)
    read_labels(gen)
