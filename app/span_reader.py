"""CPU extractive reader for single, typed facts in complete public legal clauses."""
import re
import threading
from functools import lru_cache
from pathlib import Path

import torch
from transformers import AutoModelForQuestionAnswering, AutoTokenizer


REVISION="ed28bfc62eb5b38599633b65de3adfc8dfe3f5e5"
TYPES=(
    (r"was kostet|wie (?:viel|hoch).{0,55}(?:gebühr|kosten|steuer|preis)|welche gebühr",r"\d[\d.,]*\s*(?:Euro|€)"),
    (r"wie (?:lange|früh|spät)|bis wann|welche frist|innerhalb welcher|wann.{0,25}frühest",r"\b(?:tag|woche|monat|jahr|stunde|frist)\w*\b"),
    (r"in welcher höhe|wie hoch.{0,35}(?:messen|gemessen)|welcher abstand",r"\b\d+[.,]?\d*\s*(?:cm|m|mm|Zentimeter|Meter)\b"),
)
MULTI=re.compile(r"\bund\b|\boder\b|warum|begründe|vergleiche|unter welchen|falls|ausnahme|zusätzlich",re.I)
QUALIFIER=re.compile(r"\b(?i:für|mit|bei|auf|in|von)\s+(?i:mein|unser|ein)(?:e[nmsr]?)?\s+"
                     r"(?:(?:[a-zäöüß]+)\s+){0,2}([A-ZÄÖÜ][\w-]{3,})\b")


def fact_type(question):
    if MULTI.search(question) or question.count("?")>1:
        return None
    return next((answer for form,answer in TYPES if re.search(form,question,re.I)),None)


def absent_condition(question,clauses,docs):
    subjects=QUALIFIER.findall(question)
    if not subjects:
        return False
    context=" ".join(docs[row["doc"]]["title"]+" "+row["text"] for row,_ in clauses).casefold()
    for subject in subjects:
        stem=subject.casefold()
        if len(stem)>6:
            stem=re.sub(r"(?:en|er|em|es|e|n|s)$","",stem)
        if stem not in context:
            return True
    return False


class SpanReader:
    def __init__(self,folder):
        folder=Path(folder)
        if (folder/"revision.txt").read_text().strip()!=REVISION:
            raise ValueError("Unexpected extractive reader revision")
        self.tokenizer=AutoTokenizer.from_pretrained(str(folder),local_files_only=True)
        self.model=AutoModelForQuestionAnswering.from_pretrained(str(folder),local_files_only=True).eval()
        self.lock=threading.Lock()

    @torch.inference_mode()
    def score(self,question,clauses,docs):
        contexts=[docs[r["doc"]]["title"]+"\n"+text for r,text in clauses]
        with self.lock:
            batch=self.tokenizer([question]*len(contexts),contexts,truncation="only_second",max_length=320,
                                 padding=True,return_tensors="pt",return_offsets_mapping=True)
            offsets=batch.pop("offset_mapping")
            out=self.model(**batch)
        scores=[]
        for i,(_,clause) in enumerate(clauses):
            tokens=[j for j,seg in enumerate(batch.sequence_ids(i)) if seg==1 and
                    offsets[i,j,0].item()>=len(contexts[i])-len(clause)]
            if not tokens:
                continue
            start=out.start_logits[i,tokens]
            end=out.end_logits[i,tokens]
            combined=start[:,None]+end[None,:]
            width=torch.arange(len(tokens))[None,:]-torch.arange(len(tokens))[:,None]
            combined.masked_fill_((width<0)|(width>24),-torch.inf)
            ind=int(combined.argmax())
            a,b=divmod(ind,len(tokens))
            span=contexts[i][int(offsets[i,tokens[a],0]):int(offsets[i,tokens[b],1])]
            margin=float(combined[a,b]-out.start_logits[i,0]-out.end_logits[i,0])
            scores.append((margin,i,span))
        return sorted(scores,reverse=True)

    def decision(self,question,clauses,docs):
        pattern=fact_type(question)
        if not clauses:
            return None,False
        unsupported=absent_condition(question,clauses,docs)
        if not pattern and not unsupported:
            return None,False
        ranked=self.score(question,clauses,docs)
        if not ranked:
            return None,False
        score,idx,span=ranked[0]
        # A general fee or deadline cannot establish applicability to an
        # unmentioned specific use. Require both missing textual coverage
        # and the independent reader preferring an unanswerable result.
        if unsupported and score<=-1:
            return None,True
        if not pattern or score<7 or not re.search(pattern,span,re.I):
            return None,False
        if any(score-margin<2.5 and other.strip().casefold()!=span.strip().casefold()
               for margin,_,other in ranked[1:]):
            return None,False
        return idx,False

    def select(self,question,clauses,docs):
        return self.decision(question,clauses,docs)[0]


@lru_cache(maxsize=1)
def load_reader(folder):
    return SpanReader(folder)
