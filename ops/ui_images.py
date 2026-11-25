"""Expose only checksum-verified public Freiburg previews to the chat frontend."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag"))
target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
folder = root / "data/import/bildindex"
for row in json.loads((folder / "katalog.json").read_bytes()):
    src = folder / (row["key"] + ".png")
    if src.parent != folder or src.is_symlink():
        raise ValueError("Unknown preview path")
    with src.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != row["image_sha256"]:
        raise ValueError("Preview hash mismatch")
    dest = target / (digest + ".png")
    with tempfile.NamedTemporaryFile(dir=target, delete=False) as new, src.open("rb") as handle:
        shutil.copyfileobj(handle,new)
        os.fchmod(new.fileno(),0o644)
    os.replace(new.name,dest)
