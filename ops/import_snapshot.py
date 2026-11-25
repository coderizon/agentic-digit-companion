"""Validate and atomically publish a city snapshot exported with export_snapshot.py."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def restore(root, archive, expected):
    if digest(archive) != expected:
        raise ValueError("Wrong snapshot SHA-256")
    base = root.resolve() / "data"
    base.mkdir(parents=True, exist_ok=True)
    with (base / ".import.lock").open("a") as lock, tempfile.TemporaryDirectory(dir=base) as folder:
        fcntl.flock(lock, fcntl.LOCK_EX)
        staging = Path(folder)
        with tarfile.open(archive, "r|gz") as stream:
            for member in stream:
                path = PurePosixPath(member.name)
                if (not member.isfile() or len(path.parts) < 2 or path.is_absolute() or ".." in path.parts
                        or not (path.parts[0] == "originale" or re.fullmatch(r"\d{8}T\d{6}Z", path.parts[0]))):
                    raise ValueError(f"Unexpected snapshot entry {member.name}")
                stream.extract(member, path=staging, filter="data")
        gen, = [path for path in staging.iterdir() if path.name != "originale"]
        docs = json.loads((gen / "sources.json").read_bytes())
        plan = json.loads((gen / "textindex/plan.json").read_bytes())
        if digest(gen / "pages.jsonl") != plan["corpus_sha256"] or digest(gen / "textindex/pages.jsonl") != plan["pages"]["source_sha256"]:
            raise ValueError("Text index differs from its page snapshot")
        original_dir = base / "originale"
        original_dir.mkdir(exist_ok=True)
        def original_path(old, checksum):
            filename = Path(old).name
            if (not re.fullmatch(r"[0-9a-f]{64}", checksum) or Path(filename).stem != checksum
                    or not (staging / "originale" / filename).is_file()):
                raise ValueError("Missing content-addressed original")
            return str(original_dir / filename)
        for item in docs.values():
            item["original"] = original_path(item["original"], item["digest"])
        (gen / "sources.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
        with sqlite3.connect(gen / "fritz.sqlite") as db:
            for art, old, checksum in db.execute("SELECT art,original,sha256 FROM quellen").fetchall():
                db.execute("UPDATE quellen SET original=? WHERE art=?", (original_path(old,checksum),art))
        for item in (staging / "originale").iterdir():
            if digest(item) != item.stem:
                raise ValueError(f"Source checksum mismatch {item.name}")
            dest = original_dir / item.name
            if dest.exists():
                if digest(dest) != item.stem:
                    raise ValueError(f"Damaged existing original {item.name}")
            else:
                item.replace(dest)
        marker = ".snapshot-sha256"
        destination = base / gen.name
        if destination.exists():
            if not (destination / marker).exists() or (destination / marker).read_text().strip() != expected:
                raise ValueError("A different snapshot already has this date")
        else:
            (gen / marker).write_text(expected + "\n")
            gen.replace(destination)
        link = staging / "current"
        link.symlink_to(destination.name)
        os.replace(link, base / "import")
        print(f"Activated {destination.name}, {len(docs)} public sources, corpus {plan['corpus_sha256']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--root", type=Path, default=Path(os.environ.get("FREIBURG_ROOT","/srv/freiburg-openrag")))
    a = p.parse_args()
    restore(a.root, a.archive, a.sha256.lower())
