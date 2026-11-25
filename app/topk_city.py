"""Build/serve a Freiburg generation using the original 256D MaxSim implementation."""
import argparse
from collections import OrderedDict
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from http.server import ThreadingHTTPServer

import numpy as np
import torch
from sentence_transformers import MultiVectorEncoder
from transformers import AutoTokenizer

from db_richtlinien_ai.lexical_index import build_bm25
from topk_index import Collection
from topk_service import Handler, MODEL, REVISION, Search
from topk_attention import configure_packed_attention


ROOT = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag"))
MODEL_DIR = ROOT / "models/topk"
KEY = ROOT / "app/topk.key"


class CitySearch(Search):
    def search(self, body):
        if body.get("uploads"):
            raise ValueError("Der Stadtindex verwendet die importierten öffentlichen Quellen.")
        return super().search(body)

    def __init__(self, generation, building=False):
        if (MODEL_DIR / "revision.txt").read_text().strip() != REVISION:
            raise ValueError("Unerwartete TopK-Modellrevision")
        device = os.environ.get("FREIBURG_SEARCH_DEVICE", "cuda:0")
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self.device = torch.device(device)
        self.encoder = MultiVectorEncoder(str(MODEL_DIR), device=device, local_files_only=True,
            trust_remote_code=True, model_kwargs={"dtype":dtype, "attn_implementation":"sdpa"},
            config_kwargs={"output_dim":2048}).eval()
        configure_packed_attention(self.encoder, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True, trust_remote_code=True)
        self.prompt = json.loads((MODEL_DIR / "config.json").read_text())["document_prompt"]
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.requests = 0
        if building:
            return
        index = generation / "textindex"
        plan = json.loads((index / "plan.json").read_text())
        self.corpus = hashlib.sha256((generation / "pages.jsonl").read_bytes()).hexdigest()
        if self.corpus != plan["corpus_sha256"] or plan["revision"] != REVISION:
            raise ValueError("Korpus und Index passen nicht zusammen")
        self.collections = {"pages": Collection(index,"pages",plan["pages"], device=self.device)}
        self.source_names = {"pages": frozenset(self.collections["pages"].sources)}

    def project(self, value):
        value = torch.nn.functional.normalize(value[:, :256].to(self.device, torch.float32), dim=-1)
        return value.half()


class CityHandler(Handler):
    def do_POST(self):
        if self.path != "/search":
            return self.reply(404,{})
        super().do_POST()


def build(generation):
    lock = (generation / ".topk.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (generation / "textindex/plan.json").exists():
        return
    base = generation / "textindex.aufbau"
    base.mkdir(exist_ok=True)
    raw = (generation / "pages.jsonl").read_bytes()
    rows = [json.loads(l) for l in raw.splitlines()]
    for doc in json.loads((generation / "sources.json").read_text()).values():
        if hashlib.sha256(Path(doc["original"]).read_bytes()).hexdigest() != doc["digest"]:
            raise ValueError("Quelle hat sich verändert: " + doc["url"])
    with gzip.open(base / "bm25.json.gz", "wt", encoding="utf-8") as out:
        json.dump(build_bm25(rows),out,ensure_ascii=False)
    engine = CitySearch(generation,building=True)
    offsets = [0]
    entries = []
    batch = 8
    with (base / "vectors.fp16").open("wb") as f, torch.inference_mode():
        for start in range(0,len(rows),batch):
            group = rows[start:start+batch]
            texts, owners = [], []
            for i,row in enumerate(group):
                pieces = list(engine.segments(row["text"]))
                texts.extend(pieces)
                owners.extend([i]*len(pieces))
            enc = engine.encoder.encode_document(texts,batch_size=batch,show_progress_bar=False)
            for i,row in enumerate(group):
                vectors = [engine.project(v).cpu().numpy() for j,v in zip(owners,enc) if j == i]
                value = np.concatenate(vectors)
                if value.shape[1]!=256 or not len(value) or not np.isfinite(value).all():
                    raise ValueError("Ungültige Einbettung: "+row["id"])
                f.write(value.tobytes())
                offsets.append(offsets[-1]+len(value))
                entries.append(dict(point_id=len(entries),record_id=row["id"],page=row["page"],
                                    source_file=row["source_file"],source_sha256=row["source_sha256"]))
            if start % 256 == 0:
                print(f"TopK {start+len(group)}/{len(rows)}",flush=True)
    count = offsets[-1]
    arr = np.lib.format.open_memmap(base / "topk2_mrl256-pages.npy", mode="w+", dtype=np.float16, shape=(count,256))
    source = np.memmap(base / "vectors.fp16",mode="r",dtype=np.float16,shape=arr.shape)
    for i in range(0,count,65536):
        arr[i:i+65536] = source[i:i+65536]
    arr.flush()
    del arr,source
    (base / "vectors.fp16").unlink()
    np.save(base/"topk2-pages-inverse.npy",np.arange(len(rows)))
    np.save(base/"topk2-pages-offsets.npy",np.array(offsets,dtype=np.int64))
    data = b"".join(json.dumps(row,ensure_ascii=False).encode()+b"\n" for row in entries)
    (base/"pages.jsonl").write_bytes(data)
    plan = dict(model=MODEL,revision=REVISION,corpus_sha256=hashlib.sha256(raw).hexdigest(),
                pages=dict(source_sha256=hashlib.sha256(data).hexdigest(),tokens=count,records=len(rows),unique=len(rows)))
    (base/"plan.json").write_text(json.dumps(plan))
    base.rename(generation/"textindex")
    print(f"{len(rows)} Fundstellen, {count} Tokenvektoren",flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["build","serve"])
    parser.add_argument("generation",type=Path)
    args=parser.parse_args()
    generation=args.generation.resolve(strict=True)
    if args.mode=="build":
        build(generation)
    else:
        engine=CitySearch(generation)
        # Kernel compilation on a cold T4 can exceed the HTTP client timeout.
        # Publish readiness after a real embedding and MaxSim search succeeded.
        warm = engine.search({"query":"Wo finde ich Informationen zu einer städtischen Satzung?",
                             "kind":"pages","limit":1,"sources":None,"uploads":[],"corpus_sha256":engine.corpus})
        if not warm["hits"]:
            raise RuntimeError("Active city index returned no result during startup")
        server=ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("FREIBURG_SEARCH_PORT", "8083"))), CityHandler)
        server.searcher=engine
        server.auth="Bearer "+KEY.read_text().strip()
        server.serve_forever()
