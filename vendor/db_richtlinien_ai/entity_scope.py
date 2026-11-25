"""Select explicit entity sections for short definition/function questions.

Only existing exact text slices are admitted. Regulatory, conditional, numeric
and comparison questions retain their original full passage selection.
"""
import copy
import re


def definition_target(query):
    if re.search(r'\b(?:darf|dürfen|muss|müssen|soll|ausnahm\w*|maxim\w*|höchst\w*|mindest\w*|grenz\w*|anforder\w*|wenn|falls|vergleich|zwischen|und)\b',query,re.I):return None
    if not re.search(r'^\s*(?:was\s+(?:ist|sind|macht|bezeichnet|bedeutet)|wofür|wozu)|\b(?:aufgabe|funktion|informationen|daten)\b',query,re.I):return None
    matches=re.findall(r'(?<!\w)[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*(?!\w)',query)
    values=[re.sub(r'\W','',value) for value in matches if len(re.sub(r'\W','',value))>=3]
    return values[0] if values else None


def sections(text,target):
    headings=[];offset=0
    for line in text.splitlines(keepends=True):
        plain=line.strip();ids=[];depth=None
        numbered=re.match(r'^(\d+(?:\.\d+)+)\s+(.{3,180})$',plain)
        titled=re.match(r'^(.{4,110}?)\s+[–—-]\s+([A-Za-z][A-Za-z0-9/-]{1,12})(?=\s|$)',plain)
        if numbered:
            depth=len(numbered[1].split('.'))
            ids=[re.sub(r'\W','',value).upper() for value in re.findall(r'\b[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*\b',numbered[2])]
        elif titled:
            ids=[re.sub(r'\W','',titled[2]).upper()]
        else:
            offset+=len(line);continue
        headings.append((offset,depth,ids));offset+=len(line)
    found=[]
    for index,(start,depth,ids) in enumerate(headings):
        if target not in ids:continue
        end=len(text)
        for stop,next_depth,_ in headings[index+1:]:
            if depth is None or next_depth is None or next_depth<=depth:
                end=stop;break
        found.append(text[start:end])
    return found


def scope_definition_passages(passages,query):
    target=definition_target(query)
    if not target:return passages
    selected=[]
    for passage in passages:
        spans=sections(passage.text,target)
        if not spans:continue
        scoped=copy.copy(passage);scoped.text='\n\n'.join(spans)
        scoped.signals={**getattr(passage,'signals',{}),'definition_scope':target}
        selected.append(scoped)
    return selected or passages
