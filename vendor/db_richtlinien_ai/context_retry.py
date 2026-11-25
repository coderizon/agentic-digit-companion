"""One additive, source-bound OCR retry after a checked native abstention."""
from dataclasses import replace
import logging

from db_richtlinien_ai.generation import ModelResponseError, prepare_rag_prompt

logger=logging.getLogger(__name__)
HEADER='Bildbeschriftung (geprüft):'


def normalized_line(value):
    # Layout whitespace only: relations, decimal marks and digit groups remain.
    return ' '.join(value.split())


def prepare_context_retry(passages,sources,query,config,system,template,enricher):
    """Capture the admitted native excerpts, not an unrestricted raw corpus."""
    if enricher is None or not sources or not config.get('enable_evidence_check',True):return None
    retained=[replace(p,text=sources[p.locator]) for p in passages if p.locator in sources]
    if len({p.locator for p in retained})!=len(sources):
        raise ModelResponseError('Die Quellenzuordnung für die Zusatzprüfung ist ungültig.')
    original=dict(sources);used=False

    def retry():
        nonlocal used
        if used:return None
        used=True
        additions=enricher(retained,query,config)
        if not isinstance(additions,dict) or any(k not in original for k in additions):
            raise ModelResponseError('Die Zusatzprüfung hat eine unbekannte Quelle geliefert.')
        cleaned={};count=0
        for locator,lines in additions.items():
            if not isinstance(lines,(list,tuple)) or any(not isinstance(line,str) or len(line)>300 or '\n' in line for line in lines):
                raise ModelResponseError('Die Zusatzprüfung hat ungültige Belegzeilen geliefert.')
            old_lines={normalized_line(line) for line in original[locator].splitlines()}
            values=list(dict.fromkeys(line.strip() for line in lines if line.strip()
                                      and normalized_line(line) not in old_lines and line.strip()!=HEADER))
            if values:cleaned[locator]=values;count+=len(values)
        if count>2:raise ModelResponseError('Die Zusatzprüfung überschreitet ihr Belegbudget.')
        if not cleaned:
            logger.info('Raster retry skipped: no_new_verified_lines');return None
        enriched=[replace(p,text=p.text+'\n\n'+HEADER+'\n'+'\n'.join(cleaned[p.locator])) if p.locator in cleaned else p for p in retained]
        prompt,new_sources,info=prepare_rag_prompt(enriched,config,system,template,query)
        if any(not new_sources.get(locator,'').startswith(text) for locator,text in original.items()):
            logger.info('Raster retry skipped: native_evidence_would_be_lost');return None
        visible=any(normalized_line(line) in {normalized_line(x) for x in new_sources.get(locator,'').splitlines()}
                    for locator,lines in cleaned.items() for line in lines)
        if not visible:
            logger.info('Raster retry skipped: no_new_visible_evidence');return None
        logger.info('Raster retry prepared: sources=%s added_lines=%s context_chars=%s',len(new_sources),count,info['context_chars'])
        return prompt,new_sources
    return retry
