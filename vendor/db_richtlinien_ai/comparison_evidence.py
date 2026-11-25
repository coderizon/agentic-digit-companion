"""Extractive comparison proposals from admitted, independently checked originals.

No concept-to-answer mapping, extra retrieval, numeric inference or approval.
Ambiguous layout and incomplete definition blocks retain the generative path.
"""
import re
import unicodedata
from db_richtlinien_ai.query_intent import comparison_subjects
from db_richtlinien_ai.qualifications import fold

FINITE=re.compile(r'\b(?:ist|sind|wird|werden|stellt|stellen|nimmt|nehmen|dient|dienen|überwacht|überwachen|kontrolliert|kontrollieren|enthält|enthalten|speichert|speichern|verwaltet|verwalten|entspricht|besteht|bestehen|bildet|bilden|entsteht|entstehen|zeichnet|zeichnen|bietet|bieten|erfordert|erfordern|verbindet|verbinden|ermöglicht|ermöglichen|umfasst|umfassen)\b',re.I)
MARK=re.compile(r'^\s*\(\d{1,3}\)\s*')
HEADING=re.compile(r'^\s*(\d+(?:\.\d+)*)(?:\s+|$)')
CAPTION=re.compile(r'^\s*(?:Abbildung|Abb\.?|Bild|Quelle)\s*(?:\d|:)',re.I)
BULLET=re.compile(r'^\s*(?:[-•▪\uf07a]\s*)?Ril\s+\d{3}[.]\d{4}\b',re.I)


def _quote_fold(text):
    # Match the registered-original resolver's case-sensitive line-wrap rule.
    # Acronym/numeric hyphens remain; ordinary lower-case word wraps disappear.
    text=re.sub(r'(?<=\w)-[ \t]*\n\s*(?=[a-zäöüß])','',text)
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().split())


def tokens(text):
    parts=re.findall(r'[\wäöüß]+',fold(text))
    result=set()
    for part in parts:
        if part in {'von','der','die','das','dem','den','des','einem','einer','und','mit'}:continue
        if part.isalpha() and len(part)>5:
            for suffix in ['ern','en','er','es','e','n']:
                if part.endswith(suffix) and len(part)-len(suffix)>=4:part=part[:-len(suffix)];break
        result.add(part)
    return result


def has_subject_heading(text,subject):
    """Search-only heading witness; not proof of any statement about a subject."""
    wanted=tokens(subject)
    if not wanted or len(wanted)>7:return False
    for line in text.splitlines():
        label=line.strip()
        if not 2<=len(label)<=160 or MARK.match(label) or CAPTION.match(label) or FINITE.search(label):continue
        if not wanted<=tokens(label):continue
        if HEADING.match(label):return True
        if len(tokens(label)-wanted)<=3 and not re.match(r'^(?:Zwischen|An|Bei|Als|Wenn|Falls|Sofern|Mit|Von|Vom|Für|Fuer|Durch|Wer|Die|Der|Das)\b',label,re.I):return True
    return False


def definition_candidates(text,subject):
    wanted=tokens(subject)
    if not wanted or len(wanted)>7:return []
    lines=text.splitlines(keepends=True);starts=[];offset=0
    for line in lines:starts.append(offset);offset+=len(line)
    out=[]
    for i,line in enumerate(lines):
        label=line.strip();heading=HEADING.match(label);short=2<=len(label)<=160
        if BULLET.match(label) and any(w.isdigit() for w in wanted):
            end=i+1
            while end<len(lines):
                value=lines[end].strip()
                if re.match(r'^[-•▪\uf07a]\s*',value) or HEADING.match(value):break
                end+=1
            body=text[starts[i]:starts[end] if end<len(lines) else len(text)].strip()
            if 30<=len(body)<=500 and wanted<=tokens(body) and re.search(r'\bbedienen\s*$',re.sub(r'-\s*\n\s*','',body),re.I):out.append(body)
            continue
        if CAPTION.match(label) and wanted<=tokens(label):
            # A caption can precede or follow its explanatory paragraph. Bind
            # it only to a complete adjacent paragraph naming its own topic,
            # never to the next unrelated numbered section.
            topic={w for w in wanted if len(w)>3 and not w.isdigit()}
            if len(topic)>=2:
                lower=i
                while lower>0 and i-lower<24:
                    previous=lines[lower-1].strip()
                    if CAPTION.match(previous) or HEADING.match(previous):break
                    lower-=1
                upper=i+1
                while upper<len(lines) and upper-i<24:
                    following=lines[upper].strip()
                    if CAPTION.match(following) or HEADING.match(following):break
                    upper+=1
                for lo,hi in [(lower,i),(i+1,upper)]:
                    for begin in range(lo,hi):
                        opening=' '.join(lines[begin:min(begin+2,hi)])
                        if not topic<=tokens(lines[begin]) or not FINITE.search(opening):continue
                        body=text[starts[begin]:starts[hi] if hi<len(lines) else len(text)].strip()
                        if 30<=len(body)<=1600 and re.search(r'[.!?][”\")]*\s*$',body):out.append(body)
                        break
            continue
        # Only explicit labels/headings can nominate a definition. Mere prose
        # mentioning another component cannot become its own definition.
        if not short or MARK.match(label) or not wanted<=tokens(label) or FINITE.search(label) or CAPTION.search(label):continue
        if heading is None and (len(tokens(label)-wanted)>3 or re.match(r'^(?:Zwischen|An|Bei|Als|Wenn|Falls|Sofern|Mit|Von|Vom|Für|Fuer|Durch|Wer|Die|Der|Das)\b',label,re.I)):continue
        if re.search(r'[.!?]\s+[A-ZÄÖÜ]',label):continue
        first=i+1
        while first<len(lines) and not lines[first].strip():first+=1
        if first==len(lines):continue
        numbered=bool(MARK.match(lines[first]));last=first
        if not numbered and heading is None and not FINITE.search(' '.join(lines[first:first+2])):continue
        depth=len(heading[1].split('.')) if heading else None
        while last<len(lines):
            value=lines[last].strip()
            if last>first:
                new_heading=HEADING.match(value)
                if numbered and MARK.match(value):break
                if CAPTION.match(value):break
                if new_heading and (depth is None or len(new_heading[1].split('.'))<=depth):break
                if not value and last>first+1:break
            last+=1
        body=text[starts[first]:starts[last] if last<len(lines) else len(text)].strip()
        if not 30<=len(body)<=1600 or not FINITE.search(body) or not re.search(r'[.!?][”\")]*\s*$',body):continue
        out.append(body)
    return list(dict.fromkeys(out))


def literal_comparison(query,sources,quote_resolver):
    subjects=comparison_subjects(query)
    if len(subjects)!=2 or quote_resolver is None:return None
    rest=query
    for subject in subjects:rest=re.sub(re.escape(subject),' ',rest,count=1,flags=re.I)
    grammar=set('was ist sind der die das den dem des welche welcher welches unterschied unterschiede bestehen besteht zwischen worin unterscheiden unterscheidet sich von und sowie vergleiche bitte mit ein eine einen einem einer eines versus vs mir beiden aufgaben grundsätzlich grundsaetzlich'.split())
    if set(re.findall(r'\w+',rest.casefold()))-grammar:return None
    values=[]
    for subject in subjects:
        chosen=None
        for locator,text in sources.items():
            candidates=definition_candidates(text,subject)
            if len(candidates)!=1:continue
            candidate=candidates[0]
            # Proposals must occur in the exact admitted excerpt AND the
            # hash-checked registered original. A resolver cannot add evidence.
            original=quote_resolver(locator,candidate)
            if not original or _quote_fold(original) not in _quote_fold(text):continue
            chosen=(locator,original);break
        if chosen is None:return None
        values.append(chosen)
    if fold(values[0][1])==fold(values[1][1]):return None
    return '\n\n'.join('> **'+subject+'** – laut der zitierten Unterlage: „'+original+'“ ['+locator+']'
        for subject,(locator,original) in zip(subjects,values))
