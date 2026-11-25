"""Check the real chat endpoint against independently selected original pages and CSV cells."""
import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests


HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("FREIBURG_ROOT", "/srv/freiburg-openrag"))
URL = os.environ.get("FREIBURG_CHAT_URL", "http://127.0.0.1:8090/v1/chat/completions")
MODEL = "freiburg-stadtdaten"
AMOUNT = re.compile(r"\*\*([−+\-\d.]+,\d{2}) €\*\*")


def percentile(values, fraction):
    return round(sorted(values)[math.ceil(len(values)*fraction)-1], 3) if values else None


def grade(case, reply, docs, route):
    if "want" in case:
        amounts = [Decimal(a.replace("−","-").replace(".","").replace(",",".")) for a in AMOUNT.findall(reply)]
        want = case["want"]
        if want:
            return (amounts == [Decimal(want["betrag"])] and f'CSV-Zeile {want["zeile"]} · Spalte „{want["spalte"]}“' in reply
                    and f']({want["quelle"]})' in reply)
        return (not amounts and not re.search(r"\d[\d.,]*\s*(?:€|Euro)", reply) and
                all(s.casefold() in reply.casefold() for s in case["mentions"]) and (not case["link"] or f']({case["link"]})' in reply))
    if "path" in case and case["path"] != route:
        return False
    if "doc" in case:
        link = docs[case["doc"]]["url"] + "#page="
        if "page" in case and f']({link}{case["page"]})' not in reply:
            return False
        if "page" not in case and f']({link}' not in reply:
            return False
    if case.get("url_contains") and not any(case["url_contains"] in url for url in re.findall(r"\]\((https?://[^)]+)\)", reply)):
        return False
    return (all(re.search(pattern,reply,re.I) for pattern in case["patterns"]) and
            not any(re.search(pattern,reply,re.I) for pattern in case.get("forbidden",[])))


def run(args):
    docs = json.loads((ROOT / "data/import/sources.json").read_bytes())
    corpus = json.loads((ROOT / "data/import/textindex/plan.json").read_bytes())["corpus_sha256"]
    budget = json.loads((HERE / "budget.json").read_bytes())
    documents = json.loads((HERE / "documents.json").read_bytes())
    if args.suite == "budget":
        cases = budget
    elif args.suite == "exact":
        cases = [*budget, *(c for c in documents if c.get("path"))]
    elif args.suite == "distinct":
        from table_cases import cases as independent_cells
        cases=independent_cells(docs)
        if args.count and args.count>len(cases):
            raise ValueError("Not enough distinct original cells")
    elif args.suite=="docburst":
        from document_capacity import cases as paragraph_questions
        cases=paragraph_questions(ROOT/"data/import",docs)
        if args.count and args.count>len(cases):
            raise ValueError("Not enough distinct legal document questions")
    elif args.suite=="mixed-distinct":
        from table_cases import cases as independent_cells
        from document_capacity import cases as paragraph_questions
        cells=iter(independent_cells(docs))
        legal=iter(paragraph_questions(ROOT/"data/import",docs))
        cases=[next(legal) if (i+1)%5==0 else next(cells) for i in range(args.count or 200)]
    elif args.suite == "text":
        cases = [c for c in documents if not c.get("image") and not c.get("path")]
    elif args.suite == "holdout":
        cases=json.loads((HERE/"holdout.json").read_bytes())
    elif args.suite == "mixed":
        cases = [*budget[:4], documents[0], *budget[4:8], documents[1], *budget[8:12], documents[2],
                 *budget[12:16], documents[3], *budget[16:20], documents[4]]
    else:
        cases = documents
    if args.only:
        selected = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in selected]
        if selected != {c["id"] for c in cases}:
            raise ValueError("Unknown case ID")
    if args.count:
        cases = [cases[i%len(cases)] for i in range(args.count)]
    key = (args.auth_file or ROOT / "app/chat.key").read_text().strip()
    barrier = threading.Event()
    rows = []
    def request(question, history=()):
        headers={"Authorization":"Bearer "+key}
        if args.uncached:
            headers["Cache-Control"]="no-cache"
        res = requests.post(URL, headers=headers, json={
            "model":MODEL, "messages":[*history, {"role":"user","content":question}]}, timeout=(5, args.timeout))
        res.raise_for_status()
        return (res.json()["choices"][0]["message"]["content"], res.headers.get("X-Freiburg-Pfad"),
                int(res.headers.get("X-Freiburg-Modellaufrufe","0")),res.status_code,res.headers.get("X-Freiburg-Cache"))
    def task(index, case):
        barrier.wait()
        start = time.monotonic()
        row = {"n":index,"id":case["id"],"frage":case["question"]}
        try:
            history = []
            if case.get("previous"):
                prev, *_ = request(case["previous"])
                history = [{"role":"user","content":case["previous"]},{"role":"assistant","content":prev}]
            reply, route, calls, status, cached = request(case["question"],history)
            ok = bool(grade(case,reply,docs,route))
            if case.get("image") and ok:
                match = re.search(r"\]\((/static/freiburg/([0-9a-f]{64})\.png)\)",reply)
                if match:
                    image = requests.get(URL.split("/v1/")[0]+match[1], timeout=10)
                    ok = image.ok and hashlib.sha256(image.content).hexdigest() == match[2]
                else:
                    ok = False
            row.update(http=status, korrekt=ok, pfad=route, cache=cached, modellaufrufe=calls, antwort=reply)
        except (requests.RequestException, KeyError, ValueError) as exc:
            response = getattr(exc, "response", None)
            row.update(http=response.status_code if response is not None else 0, fehler=type(exc).__name__, korrekt=False)
        row["sekunden"] = round(time.monotonic()-start,3)
        return row
    with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        jobs = [pool.submit(task,i+1,case) for i,case in enumerate(cases)]
        started = time.monotonic()
        barrier.set()
        for job in cf.as_completed(jobs):
            row = job.result()
            rows.append(row)
            print(row["id"],row["http"],row["korrekt"],row["sekunden"],flush=True)
    duration = time.monotonic()-started
    success = [r["sekunden"] for r in rows if r["http"]==200]
    from collections import Counter
    summary = dict(gesamt=len(rows),korrekt=sum(r["korrekt"] for r in rows),http=dict(Counter(r["http"] for r in rows)),
                   gesamt_s=round(duration,3),p50_erfolg_s=percentile(success,.5),p95_erfolg_s=percentile(success,.95))
    report = dict(zeit_utc=datetime.now(timezone.utc).isoformat(),korpus=corpus,suite=args.suite,parallel=args.parallel,antwortcache=not args.uncached,
                  ergebnis=summary,anfragen=sorted(rows,key=lambda r:r["n"]))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(args.output)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["budget","documents","exact","mixed","mixed-distinct","text","holdout","distinct","docburst"], default="documents")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--count", type=int)
    parser.add_argument("--only")
    parser.add_argument("--timeout", type=int, default=500)
    parser.add_argument("--uncached",action="store_true")
    parser.add_argument("--auth-file",type=Path,help="Session token file for a test via Open WebUI's /api/chat/completions")
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
