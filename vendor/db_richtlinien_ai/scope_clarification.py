"""Narrow clarification of underdetermined system/component selection.

This asks for missing information; it never selects a technical system. Clear
value, source, comparison and multi-aspect questions keep the factual RAG path.
"""
import re

DIMENSION=re.compile(r'\b(?:material\w*|querschnitt\w*|höhe\w*|hoehe\w*|länge\w*|laenge\w*|breite\w*|abstand\w*|kraft\w*|spannung\w*|strom\w*|leistung\w*|zeichnung\w*|regelzeichnung\w*|unterschied\w*|vergleich\w*|grenzwert\w*)\b',re.I)
CONTEXT=re.compile(r'\b(?:Re\s*\d+\w*|S-Bahn|Gleichstrom\w*|Wechselstrom\w*|Tunnel\w*|Neubau\w*|Bestand\w*|bestehend\w*)\b',re.I)
SELECTION=re.compile(r'^\s*welch\w*\s+(?:\w+\s+){0,2}(?:oberleitung\w*|bauart\w*|fahrdr(?:aht|äht|aeht)\w*|tragseil\w*)\b',re.I)
SECOND_ASPECT=re.compile(r'[?.;]\s*(?:Aus\s+was|Woraus|Wie|Was|Welche\w*|Wofür)|\bund\s+(?:welch\w*|wie|was)\b',re.I)


def clarification_kind(question):
    if (not isinstance(question,str) or len(question)>1500 or not SELECTION.search(question)
        or not re.search(r'\d+\s*km\s*/\s*h',question,re.I) or DIMENSION.search(question)
        or CONTEXT.search(question) or SECOND_ASPECT.search(question)):
        return None
    if re.search(r'\b(?:fahrdr(?:aht|äht|aeht)\w*|tragseil\w*)\b',question,re.I):return 'component_or_system'
    if re.search(r'\b(?:nennt|aufgeführt\w*|aufgefuehrt\w*|beschreibt|erklärt|erklaert|listet|laut)\b',question,re.I):return None
    return 'installation_context'


def clarification_answer(question,retriever,config):
    if not config.get('enable_scope_clarification',False):return None
    kind=clarification_kind(question)
    if kind is None:return None
    if kind=='component_or_system':
        text='Meinst du die Bauart der gesamten Oberleitungsanlage oder die Ausführung des genannten Bauteils, etwa Material und Querschnitt?'
        calls=0
    else:
        # Only an observed clash among relevant source proposals activates
        # this route. Multiple documents alone do not establish ambiguity.
        result=retriever.retrieve(question);identifiers=set()
        for passage in result.get('hits',[])[:5]:
            if passage.signals.get('reranker_score',0)<.5:continue
            identifiers.update(''.join(x.casefold().split()) for x in re.findall(r'\bRe\s*\d+(?:\s*S-Bahn|\s*mod)?\b',passage.text,re.I))
        if len(identifiers)<2:return None
        text='Für welche Anlagenart suchst du die Oberleitungsbauart, und geht es um einen Neubau oder eine bestehende Anlage?'
        calls=1
    return {'answer':text,'route':'scope_clarification','clarification_kind':kind,
            'model_calls':0,'retrieval_calls':calls,'cloud_calls':0,'factual_text_generated':False}
