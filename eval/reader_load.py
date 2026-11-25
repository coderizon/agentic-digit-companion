"""Time streamed generation from Strata alone, using one official passage per question."""
import argparse
import concurrent.futures as cf
import json
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

import requests


def main(args):
    root = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag"))
    gen = root / "data/import"
    passages = {(r["doc"],r["page"]):r["text"] for r in map(json.loads,(gen/"pages.jsonl").read_bytes().splitlines())}
    test = json.loads(Path(__file__).with_name("documents.json").read_bytes())
    test = [r for r in test if r.get("page")]
    url = os.environ.get("FREIBURG_READER_URL","http://127.0.0.1:18189/v1").rstrip("/")
    headers = {"Authorization":"Bearer "+(root/"app/reader.key").read_text().strip()}
    model = requests.get(url+"/models", headers=headers, timeout=8).json()["data"][0]["id"]
    gate = threading.Event()
    def one(i):
        case = test[i%len(test)]
        text = passages[case["doc"],case["page"]]
        text = " ".join(re.sub(r"(?<=\w)-[ \t]*\n\s*(?=[a-zäöüß])","",text).split())
        messages = [{"role":"system","content":"Antworte auf Deutsch in einem kurzen Satz allein aus dem Auszug."},
                    {"role":"user","content":case["question"]+"\n\nAuszug:\n"+text}]
        gate.wait()
        now = time.monotonic()
        row = {"id":case["id"],"n":i+1,"ttft_s":None}
        parts = []
        try:
            with requests.post(url+"/chat/completions",headers=headers,stream=True,timeout=(5,args.timeout),json={
                "model":model,"messages":messages,"stream":True,"temperature":0,"max_tokens":args.tokens,
                "reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":False}}) as res:
                row["http"] = res.status_code
                res.raise_for_status()
                res.encoding = "utf-8"
                for line in res.iter_lines(chunk_size=1,decode_unicode=True):
                    if not line.startswith("data:") or line[5:].strip()=="[DONE]":
                        continue
                    chunk = json.loads(line[5:])
                    for choice in chunk.get("choices",[]):
                        piece = choice.get("delta",{}).get("content") or ""
                        if piece and row["ttft_s"] is None:
                            row["ttft_s"] = round(time.monotonic()-now,3)
                        parts.append(piece)
                        if choice.get("finish_reason"):
                            row["ende"] = choice["finish_reason"]
        except (requests.RequestException,ValueError) as exc:
            row["fehler"] = type(exc).__name__
            row.setdefault("http",0)
        answer = "".join(parts)
        row.update(gesamt_s=round(time.monotonic()-now,3),antwort=answer,
                   korrekt=bool(answer) and all(re.search(p,answer,re.I) for p in case["patterns"]))
        return row
    with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        tasks=[pool.submit(one,i) for i in range(args.parallel)]
        start=time.monotonic()
        gate.set()
        rows=[]
        for task in cf.as_completed(tasks):
            row=task.result()
            print(row["id"],row["http"],row["ttft_s"],row["gesamt_s"],flush=True)
            rows.append(row)
    def p95(key):
        values=sorted(row[key] for row in rows if row.get(key) is not None and "fehler" not in row)
        return values[math.ceil(len(values)*.95)-1] if values else None
    stats={"parallel":args.parallel,"http":dict(Counter(r["http"] for r in rows)),"korrekt":sum(r["korrekt"] for r in rows),
           "ttft_p95_s":p95("ttft_s"),"gesamt_p95_s":p95("gesamt_s"),"lauf_s":round(time.monotonic()-start,3)}
    args.output.write_text(json.dumps({"model":model,"ergebnis":stats,"anfragen":sorted(rows,key=lambda r:r["n"])},
                                      ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(stats),flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parallel",type=int,default=4)
    ap.add_argument("--timeout",type=int,default=360)
    ap.add_argument("--tokens",type=int,default=96)
    ap.add_argument("--output",type=Path,required=True)
    main(ap.parse_args())
