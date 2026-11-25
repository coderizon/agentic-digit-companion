"""Select complete, contiguous excerpts from the retrieved public original pages."""
import re

from db_richtlinien_ai.rerank_excerpt import query_terms


def windows(text, normalize, width=780):
    paragraphs = [normalize(p) for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    for p in paragraphs:
        if len(p) <= width:
            chunks.append(p)
        else:
            # Long unbroken paragraphs are cut at word boundaries with overlap.
            words = p.split()
            start = 0
            while start < len(words):
                i, size = start, 0
                while i < len(words) and (size+len(words[i]) <= width or i==start):
                    size += len(words[i])+1
                    i += 1
                chunks.append(" ".join(words[start:i]))
                if i == len(words):
                    break
                start = max(start+1,i-15)
    result = []
    i = 0
    while i < len(chunks):
        j,s = i+1,chunks[i]
        while j < len(chunks) and len(s)+len(chunks[j])+1 <= width:
            s += " "+chunks[j]
            j += 1
        result.append(s)
        i = max(i+1,j-1) if j < len(chunks) else j
    # Adjacent sliding word windows must themselves match one stretch of the
    # original after whitespace/line-break normalization.
    full = normalize(text)
    return [s for s in dict.fromkeys(result) if len(s)>=25 and s in full]


def selected(question, rows, docs, reranker, normalize, budget=3400):
    keywords=query_terms(question)
    candidates=[]
    for rank, row in enumerate(rows[:8]):
        pieces=windows(row["text"],normalize)
        if not pieces:
            pieces=[normalize(row["text"])[:780]]
        def overlap(piece):
            return len(keywords & query_terms(piece))
        for piece in sorted(pieces,key=overlap,reverse=True)[:4]:
            candidates.append((rank,row,piece))
    if not candidates:
        return []
    labels=[docs[row["doc"]]["title"]+"\n"+piece for _,row,piece in candidates]
    scores=reranker.score(question,labels)
    ordered=sorted(zip(scores,candidates),key=lambda pair:(-pair[0],pair[1][0]))
    chosen=[]
    for _,(_,row,piece) in ordered:
        if any(row["id"]==p["id"] and piece in seen for p,seen in chosen):
            continue
        if len(piece)>budget:
            continue
        chosen.append((row,piece))
        budget-=len(piece)
        if len(chosen)>=5 or budget<100:
            break
    return chosen


def rule_clauses(pieces,docs,normalize):
    """Return legally numbered clauses that fit entirely in the cited page."""
    out=[]
    seen=set()
    for row,piece in pieces:
        if docs[row["doc"]]["kind"]!="Ortsrecht":
            continue
        paragraphs=[normalize(p) for p in re.split(r"\n\s*\n",row["text"]) if p.strip()]
        for i,paragraph in enumerate(paragraphs):
            if not re.match(r"^\(\d+[a-z]?\)\s",paragraph):
                continue
            parts=[paragraph]
            # Keep a subordinate lettered/numbered list with its introductory
            # rule, including lists printed after a blank line. A list item on
            # its own would lose its legal subject or qualifying condition.
            for following in paragraphs[i+1:]:
                if re.match(r"^(?:\d+\.|[a-z]\))\s",following):
                    parts.append(following)
                else:
                    break
            value=" ".join(parts)
            overlap=any(len(part)>=20 and part in piece for part in parts)
            if (not 40<=len(value)<=900 or value[-1] not in ".!?" or value in seen or
                    not overlap or value not in normalize(row["text"])):
                continue
            seen.add(value)
            out.append((row,value))
    return out
