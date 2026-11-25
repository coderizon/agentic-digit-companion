"""Locate explicit source exceptions that a broad answer must not silently omit.

This is a conservative extra guard, not a semantic entailment replacement. Only
the exact supplied excerpts are inspected. Linking uses the local sentence
context and shared subject words/numbers; it never supplies a new answer fact.
"""
from __future__ import annotations

import re
import unicodedata

WAIVER_PATTERN = (r'\b(?:brauchen|benötigen|benoetigen|erfordern)\s+keine?\b'
                  r'|\bkeine?\s+(?:[\w-]+\s+){1,4}(?:brauchen|benötigen|benoetigen|erfordern)\b')
EXCEPTION = re.compile(
    r'\b(?:ausnahm\w*|ausgenommen|abweichend\w*|abweichen|abweicht|abgewich\w*|sonderregel\w*)\b'
    r'|\bAbweichung(?:en)?\b(?=\s*:|[^.!?]{0,160}\b(?:zulässig\w*|zulaessig\w*|erlaubt\w*|gestattet\w*|möglich\w*|moeglich\w*|genehmig\w*|bedarf|bedürfen|beduerfen|darf|dürfen|duerfen|kann|können|koennen)\b)'
    r'|\b(?:gilt|gelten)\s+(?:\w+\s+){0,3}nicht\b'
    r'|\b(?:z\.\s*zt\.|zurzeit|derzeit|aktuell|gegenwärtig)\s*(?:noch\s+)?nicht\s+(?:verwendet|genutzt|eingesetzt|implementiert|betrieben)\b'
    r'|'+WAIVER_PATTERN, re.I)
PERMISSION = re.compile(r'\b(?:kann|können|koennen|darf|dürfen|duerfen|möglich|moeglich)\b', re.I)
NONUSE = re.compile(r'\b(?:z\.\s*zt\.|zurzeit|derzeit|aktuell|gegenwärtig)\s*(?:noch\s+)?nicht\s+(?:verwendet|genutzt|eingesetzt|implementiert|betrieben)\b',re.I)
RELATIVE_STATUS = re.compile(r'\b(?:z\.\s*zt\.|zurzeit|derzeit|aktuell|gegenwärtig)(?=\s|[,;:)])',re.I)
SOURCE_STATUS_ANCHOR = re.compile(
    r'\b(?:laut|gemäß|gemaess)\s+(?:\w+\s+){0,3}(?:Quelle|Unterlage|Dokument)\b'
    r'|\b(?:Quelle|Unterlage|Dokument)\s+(?:nennt|bezeichnet|beschreibt|gibt|vermerkt)\b',re.I)
WAIVER = re.compile(WAIVER_PATTERN,re.I)
OBLIGATION = re.compile(r'\b(?:muss|müssen|muessen|ist\s+\w+\s+zu|sind\s+\w+\s+zu)\b',re.I)
DISCOURSE = re.compile(r'\b(?:Aus|Aufgrund|Wegen)\s+(?:diesen|dieser|diesem|den\s+genannten)\s+Gr[uü]nd\w*\b',re.I)
GENERIC = {'abstand','höhe','hoehe','länge','laenge','breite','dauer','wert','regel','grenz'}
STOP = set(('der die das den dem des ein eine einem einen einer eines von vom zu zum zur '
            'in im am an auf aus mit für fuer bei und oder aber als ist sind sei wird werden dies '
            'wurde wurden kann können koennen darf dürfen duerfen muss müssen muessen soll '
            'sollen nicht keine kein dieser diese dieses diesen dabei davon dazu jedoch '
            'grundsätzlich grundsaetzlich vorsehen vorgesehen gelten gilt betragen beträgt '
            'ausnahme ausnahmen ausgenommen abweichend abgewichen abweichungen sonderregel '
            'durch bis mehr weniger mindestens höchstens hoechstens maximal minimal etwa '
            'einerseits andererseits sowie weiterhin beispielsweise zumbeispiel '
            'brauchen benötigen benoetigen erfordern angewandt anwenden eingesetzt einsetzen').split())


def fold(text: str) -> str:
    text=unicodedata.normalize('NFKC',text).casefold()
    text=re.sub(r'(?<=\w)-\s*\n\s*(?=\w)','',text)
    return ' '.join(text.replace('ß','ss').split())


def words(text: str) -> set[str]:
    result=set()
    for word in re.findall(r'[\w]+(?:-[\w]+)*',fold(text)):
        if any(c.isdigit() for c in word):
            # Keep the substantive part of e.g. "19-Zoll-Volleinschübe".
            result.update(words(' '.join(p for p in word.split('-') if p.isalpha())))
            continue
        if word in STOP or len(word)<4:continue
        word=word.replace('-','')
        for ending in ('ern','en','em','er','es','e','s','n'):
            if word.endswith(ending) and len(word)-len(ending)>=5:
                word=word[:-len(ending)];break
        result.add(word)
    return result


def numbers(text: str) -> set[str]:
    # PDF text often joins units to decimals ("6,5m"). A trailing word
    # boundary would backtrack to "6" and falsely reject the same "6,5 m".
    # Preserve the decimal and all digits; spacing is not a value conversion.
    return {n.replace(',','.') for n in re.findall(r'\b\d+(?:[,.]\d+)?(?=\b|[A-Za-zµΩ%])',text)}


_REFERENCE_LINE = re.compile(
    r'^\s*(?:(?:Ril|Richtlinie|Modul|Kapitel|Abschnitt)\s+)?'
    r'\d+(?:\.\d+)+(?:[A-Z]\d+)?(?:\s+[\d.]+)?(?:\s*\(\d+[a-z]?\))?'
    r'(?:\s*[a-z]\))?(?:\s*/\s*(?:\d+(?:\.\d+)+(?:[A-Z]\d+)?)?)?\s*$',
    re.I)
_BARE_INDEX_LABEL = re.compile(
    r'(?:^|\s*[-–—:]\s+|\s*\()'
    r'(?:abweichende\s+(?:Regeln|Regelungen|Bestimmungen)|Ausnahmen?|Sonderregelungen?)'
    r'\s*\)?\s*\.?\s*$', re.I)
_OPERATIVE_LANGUAGE = re.compile(
    r'\b(?:bei|für|fuer|wenn|falls|sofern|soweit|sobald|außer|ausser|nur|ohne|'
    r'gilt|gelten|bleibt|bleiben|ist|sind|hat|haben|muss|müssen|soll|sollen|darf|dürfen|'
    r'kann|können|bedarf|bedürfen|zulässig|gestattet|erforderlich)\b', re.I)


def _source_entries(text):
    """In reference directories, each printed locator starts a separate entry."""
    for paragraph in re.split(r'\n\s*\n', text):
        lines = paragraph.splitlines()
        starts = [i for i,line in enumerate(lines) if _REFERENCE_LINE.fullmatch(line)]
        if len(starts) < 2:
            yield paragraph
            continue
        yield '\n'.join(lines[:starts[0]])
        for first,last in zip(starts, starts[1:]+[len(lines)]):
            entry = '\n'.join(lines[first+1:last])
            joined = ' '.join(re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', entry).split())
            label = _BARE_INDEX_LABEL.search(joined)
            if label and not _OPERATIVE_LANGUAGE.search(joined[:label.start()]):
                # The locator points to the regulation. Its nominal index label
                # does not state what the exception is. Keep any other clauses.
                yield joined[:label.start()]
            else:
                yield entry


def condition_lines(source: str, condition: str) -> list[tuple[int,int]]:
    """Locate an actual clause in the verifier's non-empty PDF lines, across wraps."""
    def letters(text):
        return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())
    key=letters(condition)
    if not key:return []
    chunks=[];line_ids=[]
    for i,line in enumerate((l for l in source.splitlines() if l.strip()),1):
        part=letters(line)
        chunks.append(part);line_ids.extend([i]*len(part))
    page=''.join(chunks)
    return [(line_ids[m.start()],line_ids[m.end()-1]) for m in re.finditer(re.escape(key),page)]


def exception_windows(text: str):
    """Keep a qualifying sentence together with its immediate antecedent.

    PDF line wraps are joined, paragraph boundaries stay significant. A following
    unrelated section cannot qualify every assertion on that PDF page.
    """
    for paragraph in _source_entries(text):
        paragraph=re.sub(r'(?<=\w)-\s*\n\s*(?=\w)','',paragraph)
        lines=[line.strip() for line in paragraph.splitlines() if line.strip()]
        # A short exception list item without sentence punctuation belongs to
        # its immediately preceding statement, not the entire PDF text block.
        list_items=set()
        for index,line in enumerate(lines):
            if (index and re.match(r'^(?:[-•]\s*)?Ausnahme(?:n)?\s+(?:bei|für|fuer)\b',line,re.I)
                and len(line)<=180 and not re.search(r'[.!?]',line)):
                anchor=' '.join(lines[max(0,index-1):index])
                yield anchor+' '+line,line
                list_items.add(index)
        paragraph=' '.join(line for index,line in enumerate(lines) if index not in list_items)
        paragraph=' '.join(paragraph.split())
        sentences=[];begin=0
        for boundary in re.finditer(r'(?<=[.!?])\s+',paragraph):
            last=paragraph[:boundary.start()].split()[-1].casefold().lstrip('(')
            if last in {'z.','b.','u.','a.','z.b.','z.zt.','bzw.','ggf.','ca.','nr.','abs.','art.'}:continue
            sentences.append(paragraph[begin:boundary.start()]);begin=boundary.end()
        if paragraph[begin:]:sentences.append(paragraph[begin:])
        for index,sentence in enumerate(sentences):
            marker=EXCEPTION.search(sentence)
            if not marker:continue
            if re.search(r'\bkeine?\s+Ausnahme\w*\b',sentence,re.I):continue
            # Marginal column headings can precede a real conditional sentence
            # after PDF extraction. Keep the complete actual clause, without
            # treating an interleaved heading as part of the exception.
            starts=list(re.finditer(r'\b(?:Wenn|Falls|Sofern|Soweit|Bei|In|Für|Fuer|Wird|Werden)\b',sentence[:marker.start()]))
            start=starts[0].start() if starts else 0
            if not sentence[:start].strip(' ('):start=0
            condition=sentence[start:]
            # Introductory standard references describe where an exception is
            # mentioned. Its actual conditional clause supplies the scope.
            after=sentence[marker.end():]
            introduction=re.match(r'\s+(?:erwähnt|erwaehnt|genannt|beschrieben)\b',after,re.I)
            if introduction:
                clause=re.search(r'\b(?:wenn|falls|sofern)\b',after,re.I)
                if clause:condition=after[clause.start():]
            # A waiver can have an immediate mandatory follow-up. It is part
            # of the scope only inside the same paragraph and with a shared
            # concrete subject; an unrelated following rule is not appended.
            if WAIVER.search(condition) and index+1<len(sentences):
                following=sentences[index+1]
                if OBLIGATION.search(following) and (words(condition)&words(following))-GENERIC:
                    condition+=' '+following
            antecedent=' '.join(sentences[max(0,index-1):index])+ ' '+(sentence if introduction else condition)
            inactive=NONUSE.search(condition)
            if inactive:
                # Keep the complete owner as context, but the qualification
                # itself is the literal dated status, not a repeated definition.
                yield antecedent,inactive.group()
                continue
            yield antecedent,condition


def required_exceptions(blocks: dict[int,str], sources: dict[str,str],query: str='') -> list[dict]:
    requirements=[];seen=set()
    from db_richtlinien_ai.query_intent import comparison_subjects
    subjects=comparison_subjects(query)
    def owner_words(value):
        return words(value)|{v.casefold() for v in re.findall(r'\b[A-Z][A-Z0-9]{1,9}\b',value)}
    def clause_owner(value):
        # Distinguish the grammatical subject from an object mentioned later:
        # a ring may "entspricht einer linearen Kette" without inheriting the
        # exception for an open chain. Unknown predicates keep broad matching.
        result=set()
        for clause in re.split(r';|\b(?:und|während|hingegen|dagegen|aber)\b',value,flags=re.I):
            prefix=re.split(r'\b(?:ist|sind|wird|werden|hat|haben|entspricht|bietet|bieten|erfordert|erfordern|entsteht|entstehen|nimmt|nehmen|überwacht|überwachen|kontrolliert|kontrollieren|speichert|speichern|verwaltet|verwalten|verbindet|verbinden)\b',clause,maxsplit=1,flags=re.I)[0]
            result|=owner_words(prefix)
        return result
    owners=[owner_words(subject) for subject in subjects]
    distinctive=[owners[0]-owners[1],owners[1]-owners[0]] if len(owners)==2 else []
    common_heads={w for w in owners[0]&owners[1] if len(w)>3} if len(owners)==2 else set()
    all_owner_words=set().union(*owners) if owners else set()
    def parameter_numbers(value):
        # Numbered paragraphs and references such as "Satz 1" are locators,
        # not shared technical parameter values. Keep all real quantities.
        value=re.sub(r'(^|[„\n])\s*\(\d{1,3}\)(?=\s+(?:Die|Der|Das|Bei|An|Im|In|Es|Wenn|Falls|Sofern|Hier|Ein|Eine)\b)\s*',r'\1',value)
        value=re.sub(r'\b(?:Satz|Absatz|Abschnitt|Punkt|Anhang|Anlage|Ziffer|Nr\.)\s+\d+(?:[.]\d+)*','',value,flags=re.I)
        return numbers(value)
    for claim_id,block in blocks.items():
        # Source filenames and page numbers are not assertion subjects/values.
        plain=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',block,flags=re.I)
        block_words=words(plain);block_numbers=parameter_numbers(plain)
        cited_here={locator for locator in sources if fold(locator) in fold(block)}
        for locator,source in sources.items():
            labelled_owners=[]
            if common_heads:
                for line in source.splitlines():
                    if len(line)>160 or not re.match(r'^\s*(?:(?:Abbildung|Abb\.?|Bild)\s*\d+|\d+(?:[.]\d+)*\s+[A-ZÄÖÜ])',line):continue
                    label_words=owner_words(line.replace('-',' '))
                    if common_heads&label_words:labelled_owners.append(label_words)
            for anchor,condition in exception_windows(source):
                aggregate=bool(re.search(r'\b(?:beide\w*|sämtliche\w*|alle\w*)\s+(?:Varianten|Verfahren|Anbindungen|Bauarten|Systeme|Ausführungen)\b',condition,re.I))
                if distinctive and all(distinctive) and not aggregate:
                    # An explicitly named other comparison subject cannot
                    # donate its exception merely through shared context
                    # words. Ambiguous or joint ownership keeps the guard.
                    claim_owner=clause_owner(plain);source_owner=clause_owner(anchor)
                    claim_matches=[bool(d&claim_owner) for d in distinctive]
                    source_matches=[bool(d&source_owner) for d in distinctive]
                    if (claim_matches.count(True)==source_matches.count(True)==1
                        and claim_matches!=source_matches):continue
                    if (claim_matches.count(True)==1 and not any(source_matches) and common_heads&source_owner
                        and any((label&source_owner)-all_owner_words for label in labelled_owners)):
                        continue  # an explicitly labelled third variant owns this exception
                # A dated deployment status belongs to that cited context;
                # unlike a normative exception, it is not a universal rule for
                # definitions in other documents or implementations.
                if NONUSE.search(condition) and cited_here and locator not in cited_here:continue
                common=block_words & words(anchor)
                anchor_numbers=parameter_numbers(anchor)
                numeric=bool(block_numbers & anchor_numbers)
                # A common number alone, or a shared generic word alone, is not
                # sufficient evidence that an exception belongs to this claim.
                if block_numbers and anchor_numbers and not numeric:continue
                if not (numeric and common-GENERIC or len(common)>=2):continue
                # Shared words inside an exception do not establish that its
                # antecedent is the asserted rule. In another source/page,
                # require a substantial antecedent match if that antecedent
                # exists. Short exact/numeric rules and unanchored conditions
                # retain the conservative guard, including uncited originals.
                antecedent=anchor[:-len(condition)].strip() if anchor.endswith(condition) else ''
                subject=words(antecedent)-GENERIC
                if (not numeric and cited_here and locator not in cited_here and len(subject)>=3
                    and len(subject&block_words)/len(subject)<.60):continue
                key=(claim_id,locator,fold(condition))
                if key in seen:continue
                seen.add(key)
                requirements.append({'claim_id':claim_id,'locator':locator,'text':condition,'context':anchor.strip()})
    return requirements


def scope_words(text: str) -> set[str]:
    text=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',text,flags=re.I)
    # Remove marker words only. A verb-final waiver matcher also spans the
    # actual object ("keine Abdeckungen benötigen"); that object must survive.
    text=re.sub(r'\b(?:ausnahm\w*|ausgenommen|abweich\w*|abgewich\w*|sonderregel\w*)\b',' ',text,flags=re.I)
    return words(DISCOURSE.sub(' ',text))


def qualifying_neighbor(extra: str,rule: str,condition_context: str,condition: str='') -> bool:
    """Do not let an intervening different subject carry a later exception."""
    candidate=scope_words(extra)-GENERIC
    if not candidate:return bool(EXCEPTION.search(extra))
    relevant=(scope_words(rule)|scope_words(condition_context))-GENERIC
    explicit_subject=scope_words(condition)-GENERIC
    return (bool(candidate&relevant) and len(candidate&relevant)/len(candidate)>=.60
            or len(candidate&explicit_subject)>=2)


def exception_preserved(block: str, condition: str) -> bool:
    """Check explicit scope words/values; the LLM must still verify semantics.

    Exact quotation is accepted. Paraphrases must retain the affected entity,
    an exception indicator and any numeric/modal restriction. A bare mention of
    the entity elsewhere does not make a universal rule safe.
    """
    if NONUSE.search(condition) and not SOURCE_STATUS_ANCHOR.search(block):
        return False  # a dated source status is not today's operational state
    if fold(condition).strip(' ()') in fold(block):return True
    if not EXCEPTION.search(block):return False
    if PERMISSION.search(condition) and not PERMISSION.search(block):return False
    for sentence in re.split(r'(?<=[.!?])\s+',condition):
        if WAIVER.search(sentence):
            # An unrelated exception/negation cannot turn "requires doors"
            # into "requires no doors" for the actual affected modules.
            wanted=scope_words(sentence)
            candidates=[part for part in re.split(r'(?<=[.!?])\s+',block) if WAIVER.search(part)]
            if not any(wanted.issubset(scope_words(part)) for part in candidates):return False
    # Discourse pointers contain no extra requirement (e.g. "for these
    # reasons"). Exception morphology is checked separately above.
    scope=scope_words(condition);retained=scope_words(block)
    # The affected group/conditional premise stays exact. A long explanatory
    # tail may contain one OCR artefact; this does not waive the independent
    # entailment check of the full original source and all numeric conditions.
    premise=scope_words(condition.split(',',1)[0]) if ',' in condition else scope
    if not premise.issubset(retained):return False
    if scope and (len(scope-retained)>1 or len(scope & retained)/len(scope)<.85):return False
    return numbers(condition).issubset(numbers(block))
