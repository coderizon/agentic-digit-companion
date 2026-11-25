"""Client for Tail's authenticated, pinned TopK multi-vector index."""
from hashlib import file_digest
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


MODEL = "topk-io/topk-embed-v1-small"
REVISION = "e54485ebab921f2c18c4d092b3f4c40dcca26781"


class TopKClient:
    def __init__(self, config):
        self.url = "http://127.0.0.1:" + str(config.get("topk_port", 8083))
        self.corpus = config["topk_source_sha256"]
        self.auth = "Bearer " + Path(config["topk_key_file"]).read_text().strip()
        self.http = build_opener(ProxyHandler({}))

    def verify_source(self, path):
        with Path(path).open("rb") as handle:
            if file_digest(handle,"sha256").hexdigest()!=self.corpus:
                raise ValueError("Original page corpus changed; publish a matching TopK index")

    def call(self, path, data=None, *, timeout=60):
        raw=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        request=Request(self.url+path,data=raw,headers={"Content-Type":"application/json","Authorization":self.auth})
        try:
            with self.http.open(request,timeout=timeout) as response:
                result=json.load(response)
        except HTTPError as error:
            if error.code==400:
                raise ValueError("TopK rejected the native records or index identity") from error
            raise
        if result.get("revision")!=REVISION:
            raise ValueError("TopK encoder revision changed")
        return result

    def health(self):
        result=self.call("/health",timeout=2)
        return (result.get("ready") is True and result.get("model")==MODEL and
                result.get("dimension")==256 and result.get("corpus_sha256")==self.corpus
                and result.get("counts",{}).get("pages",0)>0)

    def index_upload(self, filename, digest):
        return self.call("/native/index",{"file":filename,"digest":digest},timeout=3600)["pages"]

    def drop_upload(self,file_id):
        return self.call("/native/drop",{"file_id":file_id})["removed"]

    def search(self, retriever, query, kind, limit):
        scope=getattr(retriever,"search_sources",None)
        if scope is not None and not scope:
            return []
        uploads=[item for item in getattr(retriever,"topk_uploads",())
                 if kind=="pages" and scope is not None and item["source"] in scope]
        result=self.call("/search",{"kind":kind,"query":query,"limit":limit if kind=="pages" else limit*3,
                         "sources":sorted(scope) if scope is not None else None,"uploads":uploads,
                         "corpus_sha256":self.corpus})
        if result.get("dimension")!=256 or result.get("corpus_sha256")!=self.corpus:
            raise ValueError("TopK returned an incompatible corpus")
        hits=[]
        seen=set()
        for row in result["hits"]:
            source,page=row["source_file"],row["page"]
            if scope is not None and source not in scope:
                raise ValueError("TopK returned a source outside the current selection")
            if kind=="structure":
                if row["structure_sha256"]!=retriever.structure.sha256 or not row["unit_ids"]:
                    raise ValueError("TopK structure does not match the active artifact")
                for uid in row["unit_ids"]:
                    unit=retriever.structure.by_id[uid]
                    if any(row[k]!=unit[k] for k in ("source_file","source_sha256","page")):
                        raise ValueError("TopK structured evidence identity mismatch")
                record=retriever.by_file_page[source,page]
            else:
                record=retriever.by_id[row["record_id"]]
                if (record["source_file"],record["page"])!=(source,page):
                    raise ValueError("TopK page identity mismatch")
                if record.get("source_sha256") and row.get("source_sha256")!=record["source_sha256"]:
                    raise ValueError("TopK original bytes do not match the admitted source")
            if record["id"] not in seen:
                seen.add(record["id"])
                hits.append((record["id"],float(row["score"])))
            if len(hits)==limit:break
        return hits
