"""Search-only linguistic hints and explicit primary-document constraints.

No answer value, technical system choice or document/page answer mapping lives
here. Original questions and original evidence are never rewritten by hints.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class RuleReference:
    family: str
    module: str = ''


_CODE=r'(\d{3})(?:[._/ ]*(\d{4}(?:[A-Z]\d{1,2})?|\d{2}))?(?!\d)'
_NAMED=re.compile(r'\b(?:ril|richtlinie(?:n)?|modul)\s*'+_CODE,re.I)
_NEXT=re.compile(r'\s*(?:,|und|oder|sowie|/)\s*(?:ril\s*)?'+_CODE,re.I)
_UPPER=re.compile(r'\b(?:höchst\w*|hoechst\w*|größt\w*|groesst\w*|maximal\w*|maximum|obere?\s+grenz\w*)\b',re.I)
_LOWER=re.compile(r'\b(?:mindest\w*|kleinst\w*|geringst\w*|minimal\w*|minimum|untere?\s+grenz\w*)\b',re.I)
_RULE_QUESTION=re.compile(r'\b(?:darf|dürfen|duerfen|muss|müssen|muessen|benötig\w*|benoetig\w*|erforder\w*|vorgeschrieb\w*|zulässig\w*|zulaessig\w*|gefordert\w*|grenz\w*)\b',re.I)


def requested_rules(query):
    values=[]
    for match in _NAMED.finditer(query):
        values.append(RuleReference(match[1],(match[2] or '').upper()));end=match.end()
        for _ in range(3):
            following=_NEXT.match(query,end)
            if not following:break
            values.append(RuleReference(following[1],(following[2] or '').upper()));end=following.end()
    return tuple(dict.fromkeys(values))[:4]


def semantic_topic(query):
    """Separate explicit document scope/polite requests from a search topic."""
    chunks=[];begin=0
    for match in _NAMED.finditer(query):
        if match.start()<begin:continue
        chunks.append(query[begin:match.start()]);end=match.end()
        for _ in range(3):
            following=_NEXT.match(query,end)
            if not following:break
            end=following.end()
        begin=end
    chunks.append(query[begin:]);text=' '.join(chunks)
    filler=set('welche welcher welches welchen wird werden ist sind bei in im am an auf aus für fuer von vom nach laut gemäß gemaess benötigt benoetigt braucht sag sage mir bitte detailliert erklärt erklaert beschreibe beschrieben nenne nennt so ein eine einen einer einem eines der die das man'.split())
    return ' '.join(word.strip(' .?!,:;()') for word in text.split() if word.strip(' .?!,:;()').casefold() not in filler)


def record_matches_rules(source_file,text,references,doc_type='sonstiges'):
    if not references:return True
    name=Path(source_file).name
    match=re.match(r'^\(?(?:ril[-_ ]*)?(\d{3})(?:[._ -]*(\d{2,4}(?:[A-Z]\d{1,2})?))?(?=$|[._ -])',name,re.I)
    if match:
        family,module=match[1],(match[2] or '').upper()
        for ref in references:
            if ref.family!=family:continue
            if not ref.module:return True
            if len(ref.module)==2 and module.startswith(ref.module):return True
            if module==ref.module:return True
            if len(ref.module)==4 and module.startswith(ref.module) and module[len(ref.module):].startswith(('A','V','Z')):return True
    if doc_type!='ril':return False
    # Aggregate primary documents can contain the requested module. A mere
    # in-prose cross-reference in a training page is never enough.
    for ref in references:
        module=re.escape(ref.module) if ref.module else r'\d{2,4}(?:[AVZ]\d{1,2})?'
        pattern=r'^\s*'+ref.family+r'[._ ]'+module+r'(?=\s|$)'
        if re.search(pattern,text[:800],re.M|re.I):return True
    return False


def numeric_intent(query):
    if _UPPER.search(query):return 'maximum'
    if _LOWER.search(query):return 'minimum'
    return None


def comparison_subjects(query):
    """Explicit two-subject comparison, used only to select source material."""
    if not isinstance(query,str) or len(query)>1500:return ()
    patterns=(r'\bUnterschied\w*\b[^?!.]{0,50}?\bzwischen\s+(.+?)\s+(?:und|sowie)\s+(.+?)[?!.]*$',
              r'\bunterscheiden\s+sich\s+(.+?)\s+(?:und|sowie)\s+(.+?)[?!.]*$',
              r'\bunterscheidet\s+sich\s+(.+?)\s+von\s+(.+?)[?!.]*$',
              r'\bvergleiche\s+(.+?)\s+mit\s+(.+?)[?!.]*$',
              r'^\s*(.+?)\s+(?:versus|vs\.?)\s+(.+?)[?!.]*$')
    match=next((m for pattern in patterns if (m:=re.search(pattern,query,re.I))),None)
    if not match:return ()
    subjects=tuple(re.sub(r'^(?:einem|einer|einen|eines|eine|ein|der|die|das|dem|den|des)\s+','',
                         value.strip(' \t?.!'),flags=re.I) for value in match.groups())
    # A trailing comparison criterion is not part of the second entity's name.
    # Keep the complete original query for generation and every proof check.
    criterion=re.search(r'\s+(?:(?:hinsichtlich|bezüglich|bezueglich|betreffend)\b|(?:in\s+Bezug\s+auf|im\s+Hinblick\s+auf)\b|bei\s+(?:der|den|dem)\s+(?:\w+\s+){0,2}(?:Anzahl|Informationen|Daten|Aufgaben|Funktionen|Grenzen|Grenzwerte|Länge|Laenge|Höhe|Hoehe|Breite|Einsatzbedingungen)\b)',subjects[1],re.I)
    if criterion:subjects=(subjects[0],subjects[1][:criterion.start()].strip())
    if (all(3<=len(value)<=100 and 1<=len(value.split())<=8 and not re.search(r'\b(?:und|oder|sowie)\b|[\n;?]',value,re.I)
            for value in subjects) and subjects[0].casefold()!=subjects[1].casefold()):return subjects
    return ()


def quantity_question(query):
    if _UPPER.search(query) and _LOWER.search(query):return False
    if numeric_intent(query) and comparison_subjects(query) and re.search(r'\bAnzahl\b',query,re.I):return True
    return bool(numeric_intent(query) and re.search(
        r'^\s*(?:wie\s+(?:hoch|lang\w*|breit|weit|groß|gross|schwer|viel)|welch\w*.*(?:höhe|laenge|länge|abstand|breite|grenzwert|maximalwert|mindestwert))',query,re.I))


def value_kind_present(text,kind):
    if kind=='maximum':
        return bool(_UPPER.search(text) or re.search(r'\b(?:obergrenze|nicht\s+mehr\s+als|bis\s+zu)\b|\bnicht\b.{0,40}\b(?:überschreit\w*|ueberschreit\w*|übersteig\w*)',text,re.I))
    return bool(_LOWER.search(text) or re.search(r'\b(?:untergrenze|nicht\s+weniger\s+als)\b|\bnicht\b.{0,40}\bunterschreit\w*',text,re.I))


def count_resource_scope_failures(query,approved_blocks):
    """A count of one formal resource cannot answer a count of another.

    Resolve case-insensitive query spellings only against actual formal codes
    in the checked originals. Unknown aliases and multiple requested resources
    keep semantic checking; no answer value is inferred here.
    """
    if not re.search(r'\bAnzahl\b',query,re.I):return []
    context='\n'.join(line for _,_,ranges in approved_blocks for source,_,_ in ranges for line in source['lines'].values())
    known=set(re.findall(r'(?<!\w)([A-Z][A-Z0-9]{1,11})(?:en|s|n)?(?!\w)',context))
    atom=r'([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9]{1,30})(?:-[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß]{1,40})?'
    count=re.compile(r'\bAnzahl\s+(?:(?:der|aller|von|an)\s+)?'+atom,re.I)
    def named(text):
        values=set()
        for match in count.finditer(text):
            if match[1].upper() in known:values.add(match[1].upper())
            following=re.match(r'\s+(?:und|oder|sowie)\s+'+atom,text[match.end():],re.I)
            if following and following[1].upper() in known:values.add(following[1].upper())
        return values
    requested=named(query)
    if len(requested)!=1:return []
    resource=next(iter(requested));failures=[]
    numeral=r'(?:\d+(?:[,.]\d+)?|ein(?:e|en|er|es|s)?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwoelf)'
    counted=re.compile(r'\b'+numeral+r'\s+'+atom,re.I)
    for claim_id,block,_ in approved_blocks:
        plain=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',block,flags=re.I)
        values=named(plain)|{m[1].upper() for m in counted.finditer(plain) if m[1].upper() in known}
        if values and resource not in values:
            failures.append(dict(claim_id=claim_id,reason='counted_resource_differs_from_question',
                requested_counted_resource=resource,answered_counted_resources=sorted(values)))
    return failures


def quantity_scope_failures(query,approved_blocks):
    """A normal value is not an answer to an explicit maximum/minimum query.

    This is a negative scope check, not entailment. Semantics, values, units
    and all qualifications still require the existing complete proof checks.
    """
    approved_blocks=list(approved_blocks)
    count_failures=count_resource_scope_failures(query,approved_blocks)
    if not quantity_question(query):return count_failures
    kind=numeric_intent(query);numeric=[]
    for claim_id,block,ranges in approved_blocks:
        plain=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',block,flags=re.I)
        if not re.search(r'\d',plain):continue
        numeric.append(claim_id)
        other='minimum' if kind=='maximum' else 'maximum'
        if (re.search(r'\b(?:regel\w*|normal\w*|typisch\w*)\b',plain,re.I) or value_kind_present(plain,other)) and not value_kind_present(plain,kind):continue
        for source,start,end in ranges:
            context='\n'.join(source['lines'][line] for line in range(max(1,start-2),end+1))
            if value_kind_present(context,kind):return count_failures
    return count_failures+[{'claim_id':i,'reason':'requested_quantity_kind_not_answered','requested_kind':kind} for i in numeric]


def rule_question(query):
    return bool(requested_rules(query) or numeric_intent(query) or _RULE_QUESTION.search(query))


def linguistic_hints(query):
    hints=[]
    intent=numeric_intent(query)
    if intent=='maximum':hints.extend(['maximal','maximale','Maximum','Höchstwert'])
    elif intent=='minimum':hints.extend(['minimal','minimale','Minimum','Mindestwert'])
    for words,nouns in [(r'\bhoch\b',('Höhe',)),(r'\blang\b',('Länge',)),
                        (r'\bbreit\b',('Breite',)),(r'\bschwer\b',('Gewicht','Masse'))]:
        if re.search(r'^\s*wie\b',query,re.I) and re.search(words,query,re.I):hints.extend(nouns)
    for number,unit in re.findall(r'(?<!\w)(\d+(?:[,.]\d+)?)\s*(km\s*/\s*h|mm|cm|m|kv|db)\b',query,re.I):
        hints.extend([number,re.sub(r'\s+','',unit)])
    return tuple(dict.fromkeys(hints))


def quantity_compounds(query,postings):
    nouns=[h.casefold() for h in linguistic_hints(query) if h in {'Höhe','Länge','Breite','Gewicht','Masse'}]
    result=[]
    for word in re.findall(r'\b[\wäöüß]{5,}\b',query.casefold()):
        for noun in nouns:
            for joint in ('','n','s','en'):
                candidate=word+joint+noun
                if candidate in postings and candidate not in result:result.append(candidate)
    return tuple(result[:4])
