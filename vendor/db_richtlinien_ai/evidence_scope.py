"""Additional negative checks on the verifier's exact source-line selections.

These rules do not establish entailment. They reject absent formal identifiers
and evidence borrowed from a neighbouring named section; the semantic check is
still required. Only the already admitted original excerpts are inspected.
"""
from __future__ import annotations

import re
from db_richtlinien_ai.qualifications import RELATIVE_STATUS, SOURCE_STATUS_ANCHOR,fold


def identifiers(text: str) -> set[str]:
    text = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', text)
    values = re.findall(r'(?<!\w)[A-ZÄÖÜ][A-ZÄÖÜ0-9]*(?:[-/][A-ZÄÖÜ0-9]+)*(?!\w)', text)
    return {re.sub(r'\W', '', value) for value in values if len(re.sub(r'\W', '', value)) >= 3}


def literal_tokens(text: str) -> set[str]:
    # Keep technical compound boundaries (BSC-Controller, SIM-Karte) while
    # joining ordinary line-wrapped words. Both the whole compound and its
    # explicit components remain available below; no ID is invented.
    text = re.sub(r'(\b\w+)-\s*\n\s*(?=\w)',lambda m:m[1]+('-' if len(m[1])>=2 and m[1].isupper() else ''),text)
    # German plural suffixes keep the original technical identifier, e.g.
    # BTSen/MSCs. Preserve case here: an all-uppercase BTSEN is a distinct ID.
    plurals=re.findall(r'(?<!\w)([A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*)(?:en|s|n)(?!\w)',text)
    values = re.findall(r'\w+(?:[-/]\w+)*', text.upper())
    return {part for value in values+plurals for part in [re.sub(r'\W', '', value), *re.split(r'[-/]', value)]}


def named_sections(lines: dict[int, str], query: str) -> list[dict]:
    targets = identifiers(query)
    if len(targets) != 1:
        return []  # multi-entity questions need the semantic comparison
    target = next(iter(targets))
    headings = []
    for number, line in lines.items():
        match = re.match(r'^\s*(\d+(?:\.\d+){1,5})\s+(.{3,110})$', line.strip())
        if match:
            headings.append((number, tuple(map(int, match[1].split('.'))), match[2]))
    sections = []
    for index, (start, level, title) in enumerate(headings):
        if target not in identifiers(title):
            continue
        end = max(lines)
        for next_start, next_level, _ in headings[index+1:]:
            if len(next_level) <= len(level):
                end = next_start-1
                break
        sections.append(dict(target=target, heading=title, line_start=start, line_end=end))
    return sections


def assignment_mismatches(block: str, source_text: str) -> list[dict]:
    """Veto an explicit reassignment to another named actor; never prove a fact.

    Restrict this to active assignment verbs and an explicit source subject.
    Absence of such a pattern establishes nothing and leaves semantic checks
    responsible. Multiple explicitly documented actors remain admissible.
    """
    text = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', source_text)
    text = ' '.join(text.split())
    roles = {};quotes={}
    for match in re.finditer(r'\b(?:Das|Die|Der)\s+([A-Z][A-Z0-9]{1,9})\s+'
            r'(?:steuert|übernimmt|regelt)\b.{0,65}?\bZuordnung\b([^.!?]{1,180})', text):
        for obj in identifiers(match[2]):
            roles.setdefault(obj, set()).add(match[1])
            quotes[(obj,match[1])]=match[0].strip()
    failures = []
    for match in re.finditer(r'\b([A-Z][A-Z0-9]{1,9})\b'
            r'(?:(?![.!?]\s+[A-Z]).){0,180}?\b(?:ordnet|weist)\b([^.!?]{1,180})', block):
        actor = match[1]
        prefix=match[0][:match.start(2)-match.start()]
        if len(identifiers(prefix))!=1:continue  # a second subject/object is ambiguous
        for obj in identifiers(match[2]):
            if obj in roles and actor not in roles[obj]:
                failures.append(dict(reason='assignment_actor_differs_from_original',
                    actor=actor, object=obj, source_actors=sorted(roles[obj]),
                    source_quote=quotes[(obj,next(iter(roles[obj])))] if len(roles[obj])==1 else None))
    return failures


def comparison_storage_mismatches(block, sources, query):
    """Veto fields borrowed from the other explicitly headed comparison entity.

    Consecutive admitted pages of the same document may continue a section.
    Missing pages, missing headings and ambiguous sentence subjects establish
    no ownership. This negative check neither supplies nor approves evidence.
    """
    from db_richtlinien_ai.query_intent import comparison_subjects
    from db_richtlinien_ai.entity_scope import sections
    subjects=comparison_subjects(query)
    if len(subjects)!=2 or not all(re.fullmatch(r'[A-Z][A-Z0-9]{2,11}',s) for s in subjects):return []
    storage=r'(?:speichert|enthält|umfasst|verwaltet)'
    match=re.match(r'^\s*(?:>\s*)?(?:\*\*)?(?:(?:Das|Die|Der)\s+)?([A-Z][A-Z0-9]{2,11})(?:\*\*)?\s+'+storage+r'\b',block)
    if not match or match[1] not in subjects:return []
    actor=match[1];documents={}
    if any(subject!=actor and re.search(r'\b(?:(?:Das|Die|Der)\s+)?'+re.escape(subject)+r'\s+'+storage+r'\b',block)
           for subject in subjects):return []  # several explicit owners in one block
    for source in sources:
        name,separator,page=source['locator'].rpartition(', Seite ')
        if separator and page.isdigit():documents.setdefault(name,{})[int(page)]='\n'.join(source['lines'].values())
    owned={subject:set() for subject in subjects};bounded={subject:False for subject in subjects}
    for pages in documents.values():
        runs=[];current=[];previous=None
        for page,text in sorted(pages.items()):
            if previous is not None and page!=previous+1:runs.append(current);current=[]
            current.append(text);previous=page
        if current:runs.append(current)
        for run in runs:
            # A repeated running title at the next page boundary is not a
            # new component section. Ignore it only in this negative scope
            # view, never in the admitted original or the cited line ranges.
            prefixes=[set([line.strip() for line in page.splitlines() if line.strip()][:6]) for page in run]
            repeated={line for prefix in prefixes for line in prefix
                if sum(line in other for other in prefixes)>1
                and not re.match(r'^\d+(?:\.\d+)+\s',line)
                and not (set(subjects)&identifiers(line))}
            scoped=[]
            for page in run:
                lines=[];nonempty=0
                for line in page.splitlines():
                    if line.strip():nonempty+=1
                    if nonempty<=6 and line.strip() in repeated:continue
                    lines.append(line)
                scoped.append('\n'.join(lines))
            text='\n\n'.join(scoped)
            for subject in subjects:
                spans=sections(text,subject)
                if spans:bounded[subject]=True
                for span in spans:owned[subject].update(literal_tokens(span))
                # Explicit positive storage prose can also name a shared
                # field; another section cannot establish exclusivity then.
                for sentence in re.findall(r'\b(?:Das|Die|Der)\s+'+re.escape(subject)+r'\s+'+storage+r'\b[^.!?]{0,500}',text):
                    if not re.search(r'\b(?:nicht|kein\w*)\b',sentence,re.I):owned[subject].update(literal_tokens(sentence))
    if not bounded[actor]:return []
    fields=identifiers(block)-{actor}
    for compound in re.findall(r'\b[A-ZÄÖÜ][A-ZÄÖÜ0-9]{1,11}-[A-ZÄÖÜ][a-zäöüß]{2,30}\b',block):
        fields.add(re.sub(r'\W','',compound.upper()))
    failures=[]
    for field in sorted(fields-owned[actor]):
        others=[subject for subject in subjects if subject!=actor and bounded[subject] and field in owned[subject]]
        if others:failures.append(dict(reason='stored_field_belongs_to_other_compared_section',actor=actor,field=field,source_actors=others))
    return failures


_MODULE = re.compile(r'(?<!\d)(\d{3})[._-]?(\d{4})(?:[ \t]?([A-Z]\d{2}))?(?![\dA-Z])',re.I)
_EDITION_REQUEST = re.compile(r'\b(?:von|aus(?:\s+dem\s+Jahr)?|Stand|Fassung|Ausgabe)\s*[:(]?\s*'
                             r'(?:(?:\d{1,2}[./-]){2})?((?:19|20)\d{2})\b',re.I)
_PRINTED_EDITION = re.compile(r'\b(?:gültig\s+(?:ab|vom)|Stand|Ausgabe|Fassung|Version(?:sstand)?)\s*[:(]?\s*'
                             r'(?:(?:\d{1,2}[./-]){2})?((?:19|20)\d{2})\b',re.I)


def module_identifiers(text):
    return {''.join(m.groups(default='')).upper() for m in _MODULE.finditer(text)}


def requested_module_edition(query):
    modules=module_identifiers(query)
    years=set(_EDITION_REQUEST.findall(query))
    if len(modules)==len(years)==1:
        return next(iter(modules)),next(iter(years))
    return None


def table_edition_mismatches(query, references):
    """A claim about a named table or expressly requested edition needs the same module and year."""
    table=bool(re.search(r'(?:tabelle\w*|matrix)\b',query,re.I))
    edition=requested_module_edition(query)
    if not edition or not table and (not re.search(r'\b(?:fassung|ausgabe|aktualisierung|stand)\b',query,re.I)
            or re.search(r'\b(?:vergleiche|vergleich|unterschied\w*)\b',query,re.I)):
        return []
    module,year=edition
    seen=set()
    unproven=[]
    for source,_,_ in references:
        locator=source['locator']
        if locator in seen:
            continue
        seen.add(locator)
        name=locator.rpartition(', Seite ')[0]
        lines=list(source['lines'].values())
        heading=' '.join(lines[:8])
        owners={''.join(m.groups(default='')).upper() for m in _MODULE.finditer(name+' '+heading)}
        # A tranche of another rule mentioning the requested module in body text
        # or a date in a table cell does not identify this PDF page's edition.
        printed=set(_PRINTED_EDITION.findall(' '.join(lines[:8]+lines[-8:])))
        file_years=set(re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)',_MODULE.sub('',name)))
        if module not in owners or (printed or file_years)!={year}:
            unproven.append(locator)
    if seen and not unproven:
        return []
    return [dict(reason='requested_table_edition_not_proven' if table else 'requested_edition_not_proven',requested_module=module,requested_year=year,
                 checked_locators=sorted(unproven))]


def evidence_mismatches(block: str, references: list[tuple[dict, int, int]], query: str, *, context_sources=None) -> list[dict]:
    plain = re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]', '', block, flags=re.I)
    selected = '\n'.join(source['lines'][line] for source, start, end in references
                         for line in range(start, end+1))
    # The requested entity may be named in the page heading rather than in
    # every selected detail line. This exception applies only to identifiers
    # actually asked AND present in that same admitted original excerpt.
    context='\n'.join(line for source,_,_ in references for line in source['lines'].values())
    heading_entities=identifiers(query)&literal_tokens(context)
    missing = identifiers(plain) - literal_tokens(selected) - heading_entities
    failures = [dict(reason='formal_identifier_absent_in_checked_lines', identifiers=sorted(missing))] if missing else []
    failures.extend(table_edition_mismatches(query,references))
    for quote in re.findall(r'„([^“]{25,1600})“',plain):
        if fold(quote) not in fold(context):
            failures.append(dict(reason='quoted_text_absent_in_admitted_original'))
    failures.extend(assignment_mismatches(plain,context))
    failures.extend(comparison_storage_mismatches(plain,
        context_sources if context_sources is not None else [source for source,_,_ in references],query))
    scope_context=re.sub(r'(?<=\w)-\s*\n\s*(?=\w)','',context)
    if (re.search(r'\d',plain) and re.search(r'\bToleranzangaben\b',scope_context,re.I)
            and not re.search(r'\b(?:Toleranz\w*|Nenn\w*)\b',plain,re.I)):
        failures.append(dict(reason='tolerance_or_nominal_scope_omitted',required_scope='Toleranzangaben bzw. Nennwert, nicht allgemeine Höchstgrenze'))
    # "currently registered participants" describes dynamic data membership,
    # not a claim that a dated technology is in operation today. Remove only
    # that syntactic use from this extra status veto, never from the answer.
    status_plain=re.sub(r'\b(?:aktuell|derzeit|gegenwärtig)\s+(?:(?!(?:verwendet|genutzt|eingesetzt|betrieben|installiert|implementiert)\b)\w+\s+){0,6}(?:angemeldet\w*|registriert\w*|befind\w*)\b','',plain,flags=re.I)
    if RELATIVE_STATUS.search(status_plain) and not SOURCE_STATUS_ANCHOR.search(plain):
        failures.append(dict(reason='relative_source_status_presented_as_current_operation'))
    for source, start, end in references:
        sections = named_sections(source['lines'], query)
        if sections and not any(section['line_start'] <= start <= end <= section['line_end'] for section in sections):
            failures.append(dict(reason='evidence_outside_named_section', locator=source['locator'],
                                 requested_sections=sections, line_start=start, line_end=end))
    # An explicit self-definition of a different entity does not establish a
    # storage/containment relationship with the requested entity. This matters
    # when a flat PDF extraction places independent definitions inside a list.
    storage=r'(?:speichert|speichern|gespeichert|enthält|enthalten|umfasst)'
    targets=identifiers(query)
    if len(targets)==1 and re.search(r'\b'+storage+r'\b',plain,re.I):
        target=next(iter(targets));claimed=identifiers(plain)
        for source,_,_ in references:
            text=' '.join(source['lines'].values())
            for match in re.finditer(r'\b([A-Z][A-Z0-9]{1,9})\s*\([^)]{3,100}\)\s*:\s*(?:Ein(?:e)?\s+)?\1\s+ist\b',text):
                entity=match[1]
                if entity==target or entity not in claimed:continue
                linked=False
                for sentence in re.split(r'(?<=[.!?])\s+',text):
                    if (re.search(r'\b'+storage+r'\b',sentence,re.I) and entity in identifiers(sentence)
                        and target in identifiers(sentence) and not re.search(r'\b(?:nicht|kein\w*)\b',sentence,re.I)):
                        linked=True;break
                if not linked:
                    failures.append(dict(reason='entity_definition_is_not_a_stored_field',locator=source['locator'],entity=entity,target=target))
    return failures
