"""Stream only the public Freiburg import and its reproducible indexes to stdout."""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tarfile
from pathlib import Path


def export(root):
    base = (root / "data").resolve()
    gen = (base / "import").resolve(strict=True)
    if gen.parent != base or not re.fullmatch(r"\d{8}T\d{6}Z", gen.name):
        raise ValueError("Unexpected import directory")
    sources = json.loads((gen / "sources.json").read_bytes())
    kinds = {"Haushalt", "Ortsrecht", "Ratsinfo", "Ratsvorlage", "Datenkatalog", "VAG-Fahrplan", "MobiData BW"}
    originals = {}
    for item in sources.values():
        if item["kind"] not in kinds:
            raise ValueError(f"Unexpected source family: {item['kind']}")
        originals[item["original"]] = item["digest"]
    with sqlite3.connect(f"file:{gen / 'fritz.sqlite'}?mode=ro", uri=True) as db:
        originals.update(db.execute("SELECT original, sha256 FROM quellen"))
    files = [gen / name for name in ("sources.json","pages.jsonl","stand.json","visual.json","fritz.sqlite")]
    files += [gen / "textindex" / name for name in (
        "plan.json","bm25.json.gz","pages.jsonl","topk2-pages-inverse.npy",
        "topk2-pages-offsets.npy","topk2_mrl256-pages.npy")]
    files += sorted(p for p in (gen / "bildindex").rglob("*") if p.is_file()
                    and p.suffix in {".json", ".pt", ".png", ".npy"})
    for path, digest in sorted(originals.items()):
        original = Path(path).resolve(strict=True)
        if (original.parent != (base / "originale") or original.stem != digest
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Original outside its content-addressed directory")
        with original.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"Source checksum mismatch {original.name}")
        files.append(original)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz", compresslevel=1) as archive:
        for path in files:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):
                raise ValueError(f"Unexpected archive member {path.name}")
            archive.add(path, arcname=str(path.relative_to(base)), recursive=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("/srv/freiburg-openrag"))
    export(parser.parse_args().root)
