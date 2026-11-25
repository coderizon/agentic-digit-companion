"""Content-bound native PDF vectors. A document is published in one SQLite transaction."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

import numpy as np


UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
VERSION_FILE = re.compile(rf"({UUID})-([0-9a-f]{{64}})\.jsonl")
DIMENSION = 256


class UploadIndex:
    def __init__(self, database, records):
        self.database = Path(database)
        self.records = Path(records)
        self.writer = threading.Lock()
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS versions (
                    version_key TEXT PRIMARY KEY, file_id TEXT NOT NULL, filename TEXT NOT NULL,
                    source TEXT NOT NULL, source_hash TEXT NOT NULL, pages INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_by_file ON versions(file_id);
                CREATE TABLE IF NOT EXISTS vectors (
                    version_key TEXT REFERENCES versions(version_key) ON DELETE CASCADE,
                    page INTEGER, record_id TEXT NOT NULL, tokens INTEGER NOT NULL, data BLOB NOT NULL,
                    PRIMARY KEY(version_key, page));
            """)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=60)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def descriptor(item):
        name, digest = item["file"], item["digest"]
        match = VERSION_FILE.fullmatch(name)
        if not match or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid native record identity")
        return name, digest, match[1]

    def index(self, item, encode):
        name, digest, file_id = self.descriptor(item)
        version_key = name + ":" + digest
        with self.writer, closing(self.connect()) as db, db:
            existing = db.execute("SELECT filename, pages FROM versions WHERE version_key=?", (version_key,)).fetchone()
            if existing:
                if existing[0] != name:
                    raise ValueError("Conflicting native version")
                return existing[1]
            path = self.records / name
            if path.is_symlink() or path.resolve().parent != self.records.resolve():
                raise ValueError("Invalid native record location")
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise ValueError("Native records changed")
                handle.seek(0)
                count = 0
                source = source_hash = None
                for line in handle:
                    row = json.loads(line)
                    page, text = row["page"], row["text"]
                    if type(page) is not int or page < 1 or not isinstance(text, str):
                        raise ValueError("Invalid native page")
                    if source is None:
                        source, source_hash = row["source_file"], row["source_sha256"]
                        if not source or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
                            raise ValueError("Invalid native original")
                        db.execute("INSERT INTO versions VALUES (?,?,?,?,?,0)", (version_key,file_id,name,source,source_hash))
                    rid = f"kb_{file_id}_{source_hash[:12]}:p{page}"
                    if (row["id"],row["source_file"],row["source_sha256"]) != (rid,source,source_hash):
                        raise ValueError("Native page and original disagree")
                    if len(text.strip()) < 8:
                        continue
                    values = np.asarray(encode(text), dtype=np.float16, order="C")
                    if values.ndim != 2 or values.shape[1] != DIMENSION or not len(values) or not np.isfinite(values).all():
                        raise ValueError("Invalid native page embedding")
                    db.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", (version_key,page,rid,len(values),values.tobytes()))
                    count += 1
                if not count:
                    raise ValueError("Document has no indexable page")
                db.execute("UPDATE versions SET pages=? WHERE version_key=?", (count,version_key))
                return count

    def candidates(self, items, sources):
        if not items:
            return
        if sources is None:
            raise ValueError("Private documents require a knowledge selection")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            for item in items:
                name,digest,_ = self.descriptor(item)
                version_key = name + ":" + digest
                row = db.execute("SELECT filename,source,source_hash FROM versions WHERE version_key=?", (version_key,)).fetchone()
                if row is None:
                    raise RuntimeError("Selected native version is not indexed")
                if row[0] != name or row[1] != item["source"] or row[1] not in sources:
                    raise ValueError("Native source outside selected knowledge")
                for page,rid,tokens,data in db.execute(
                        "SELECT page, record_id, tokens, data FROM vectors WHERE version_key=? ORDER BY page",(version_key,)):
                    vectors = np.frombuffer(data,dtype=np.float16).reshape(tokens,DIMENSION)
                    yield {"record_id":rid,"source_file":row[1],"source_sha256":row[2],"page":page}, vectors

    def drop(self, file_id):
        if not re.fullmatch(UUID,file_id):
            raise ValueError("Invalid native file ID")
        with self.writer, closing(self.connect()) as db, db:
            return db.execute("DELETE FROM versions WHERE file_id=?",(file_id,)).rowcount
