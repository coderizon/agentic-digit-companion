"""Authenticated local multi-vector search for original and explicitly admitted native PDFs."""
from collections import OrderedDict
from hashlib import sha256
import heapq
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import threading
import time

import numpy as np
from sentence_transformers import MultiVectorEncoder
import torch
from transformers import AutoTokenizer

from topk_index import Collection
from topk_uploads import DIMENSION, UploadIndex


MODEL = "topk-io/topk-embed-v1-small"
REVISION = "e54485ebab921f2c18c4d092b3f4c40dcca26781"
INDEX = Path("/index")
MODEL_DIR = Path("/model")
LOG = logging.getLogger("topk")


class Search:
    def __init__(self):
        plan = json.loads((INDEX/"topk-plan.json").read_text())
        if (plan["model"],plan["revision"],plan["dimension"]) != (MODEL, REVISION, 2048):
            raise ValueError("Original index was built with another encoder")
        self.corpus = json.loads((INDEX/"corpus-manifest.json").read_text())["source_jsonl_sha256"]
        self.collections = {k:Collection(INDEX,k,plan["collections"][k]) for k in ("pages","structure")}
        originals = json.loads((INDEX/"source-hashes.json").read_text())
        if originals["corpus_sha256"] != self.corpus:
            raise ValueError("Original document registry belongs to a different corpus")
        for row in self.collections["pages"].rows:
            row["source_sha256"] = originals["documents"][row["source_file"]]
        self.source_names = {k:frozenset(c.sources) for k,c in self.collections.items()}
        self.encoder = MultiVectorEncoder(str(MODEL_DIR),trust_remote_code=True,device="cuda",
            model_kwargs={"dtype":torch.bfloat16},config_kwargs={"output_dim":2048})
        self.encoder.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR),local_files_only=True,trust_remote_code=True)
        self.prompt = json.loads((MODEL_DIR/"config.json").read_text())["document_prompt"]
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.uploads = UploadIndex("/state/native-v1.sqlite", "/records")
        self.requests = 0

    def info(self):
        return {"ready":True,"model":MODEL,"revision":REVISION,"dimension":DIMENSION,
                "corpus_sha256":self.corpus,"counts":{k:len(v.rows) for k,v in self.collections.items()},
                "search_requests":self.requests}

    @staticmethod
    def project(value):
        return torch.nn.functional.normalize(value[:, :DIMENSION].to("cuda",torch.float32),dim=-1).half()

    def segments(self, text):
        # Preserve whole-page encoding under the model's token limit. Overlong
        # uploaded pages retain every segment under the same original page ID.
        if len(text) > 8192 or len(text.encode())+len(self.prompt)+8 > 8192:
            count = len(self.tokenizer((self.prompt+text).strip(),truncation=False)["input_ids"])
            if count > 8192:
                mid = len(text)//2
                boundary = text.rfind(" ",mid//2,mid+1)
                cut = boundary+1 if boundary>=0 else mid
                yield from self.segments(text[:cut])
                yield from self.segments(text[cut:])
                return
        if text.strip():
            yield text

    @torch.inference_mode()
    def encode_document(self,text):
        chunks = []
        for segment in self.segments(text):
            with self.lock:
                encoded = self.encoder.encode_document([segment],batch_size=1,show_progress_bar=False)[0]
                chunks.append(self.project(encoded).cpu().numpy())
        if not chunks:
            raise ValueError("Empty document text")
        return np.concatenate(chunks,axis=0)

    @torch.inference_mode()
    def search(self, body):
        query,kind,limit = body["query"],body["kind"],body["limit"]
        if not isinstance(query,str) or not query.strip() or len(query)>131072 or kind not in self.collections:
            raise ValueError("Invalid query or collection")
        if type(limit) is not int or not 1<=limit<=1024 or body["corpus_sha256"]!=self.corpus:
            raise ValueError("Invalid index identity or search depth")
        allowed = body["sources"]
        if allowed is not None and (not isinstance(allowed,list) or not all(isinstance(s,str) for s in allowed)):
            raise ValueError("Invalid source selection")
        scope = frozenset(allowed) if allowed is not None else None
        items = body.get("uploads",[])
        if not isinstance(items,list) or (items and (scope is None or kind!="pages")):
            raise ValueError("Native sources require an explicit page selection")
        started = time.monotonic()
        with self.lock:
            self.requests += 1
            if query not in self.cache:
                encoded=self.encoder.encode_query([query],batch_size=1,show_progress_bar=False)[0]
                self.cache[query]=(self.project(encoded),{})
                if len(self.cache)>8:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(query)
            projected,scores=self.cache[query]
            hits=[]
            if scope is None or not scope.isdisjoint(self.source_names[kind]):
                collection=self.collections[kind]
                if kind not in scores:
                    scores[kind]=collection.scores(projected)
                hits=collection.hits(scores[kind],scope,limit)
            def native():
                for row,values in self.uploads.candidates(items,scope):
                    gpu=torch.from_numpy(values.copy()).to("cuda")
                    dots=torch.mm(gpu,projected.T.contiguous(),out_dtype=torch.float32)
                    yield {**row,"score":float(dots.amax(dim=0).sum().item())}
            if items:
                hits.extend(heapq.nlargest(limit,native(),key=lambda r:r["score"]))
                hits=sorted(hits,key=lambda r:r["score"],reverse=True)[:limit]
        LOG.info("search kind=%s query_sha256=%s hits=%d native_versions=%d seconds=%.3f",
                 kind,sha256(query.encode()).hexdigest(),len(hits),len(items),time.monotonic()-started)
        return {"revision":REVISION,"dimension":DIMENSION,"corpus_sha256":self.corpus,"hits":hits}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def reply(self,status,value):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path=="/health":
            self.reply(200,self.server.searcher.info())
        else:
            self.reply(404,{})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get("Authorization",""), self.server.auth):
            return self.reply(401,{"error":"Authentication required"})
        engine=self.server.searcher
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=8*1024**2:
                return self.reply(413,{"error":"Invalid request size"})
            body=json.loads(self.rfile.read(size))
            if self.path=="/search":
                data=engine.search(body)
            elif self.path=="/native/index":
                data={"pages":engine.uploads.index(body,engine.encode_document),"revision":REVISION}
            elif self.path=="/native/drop":
                data={"removed":engine.uploads.drop(body["file_id"]),"revision":REVISION}
            else:
                return self.reply(404,{})
        except (ValueError,KeyError,TypeError) as error:
            LOG.warning("Invalid request on %s: %s",self.path,type(error).__name__)
            return self.reply(400,{"error":"Invalid input or document identity"})
        except Exception:
            LOG.exception("Failed local operation %s",self.path)
            return self.reply(503,{"error":"Local multi-vector index unavailable"})
        self.reply(200,data)


if __name__=="__main__":
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format="%(message)s")
    service=Search()
    server=ThreadingHTTPServer(("0.0.0.0",8083),Handler)
    server.searcher=service
    token=Path("/run/topk.key").read_text().strip()
    if len(token)<32:raise ValueError("Missing service key")
    server.auth="Bearer "+token
    LOG.info("TopK ready: %s",service.info())
    server.serve_forever()
