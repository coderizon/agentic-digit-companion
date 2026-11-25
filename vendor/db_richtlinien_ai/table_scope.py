"""Negative checks from spatially confirmed cells already present in excerpts."""
import re
from decimal import Decimal


class TableConstraintBundle(list):
    """Request-local negative cells and their original conditional footnotes."""
    def __init__(self,cells=(),qualifications=()):
        super().__init__(dict(c) for c in cells)
        self.qualifications=tuple(dict(q) for q in qualifications)


def filter_constraints(cells,locators):
    return TableConstraintBundle([c for c in cells if c['locator'] in locators],
        [q for q in getattr(cells,'qualifications',()) if q['locator'] in locators])


def normalized(text):return ' '.join(text.casefold().split())


def header_pattern(value):
    pieces=value.split();pattern=''
    for i,part in enumerate(pieces):
        if i:pattern+=r'\s*' if part[:1].isdigit() else r'\s+'
        pattern+=re.escape(part)
    return pattern+(r'\b' if re.search(r'\d',value) else r'(?:n|en|s)?\b')


def role_pattern(label):
    for pattern in [r'(?:maxim\w*|höchst\w*|hoechst\w*)',r'(?:minim\w*|mindest\w*)',r'nenn(?!t\b|en\b|e\b)\w*',r'regel\w*',r'toleranz\w*']:
        if re.search(r'\b'+pattern,label,re.I):return pattern
    return None


def validate_cells(cells,sources):
    if not isinstance(cells,list) or len(cells)>16:raise ValueError('Invalid table constraints')
    for cell in cells:
        if not isinstance(cell,dict) or set(cell)!={'locator','header','label','value'}:raise ValueError('Invalid table cell')
        if not all(isinstance(v,str) and 1<=len(v)<=180 for v in cell.values()):raise ValueError('Invalid table text')
        if cell['locator'] not in sources:raise ValueError('Table cell outside admitted sources')
        source=normalized(sources[cell['locator']])
        if any(normalized(cell[key]) not in source for key in ('header','label','value')):
            raise ValueError('Table cell not present in admitted original excerpt')
    for item in getattr(cells,'qualifications',()):
        if not isinstance(item,dict) or set(item)!={'locator','header','label','value','text'}:raise ValueError('Invalid original table qualification')
        if not all(isinstance(v,str) and 1<=len(v)<=2000 for v in item.values()):raise ValueError('Invalid table qualification text')
        if {k:item[k] for k in ['locator','header','label','value']} not in cells:raise ValueError('Qualification has no matching original cell')
        if normalized(item['text']) not in normalized(sources[item['locator']]):raise ValueError('Qualification absent in admitted original')
    return cells


def qualification_requirements(blocks,query,approved,cells):
    out=[]
    for claim_id,block in blocks.items():
        for item in getattr(cells,'qualifications',()):
            if item['locator'] not in approved.get(claim_id,set()):continue
            plain=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',block,flags=re.I)
            header=header_pattern(item['header'])
            if not re.search(header,query+' '+plain,re.I):continue
            value=re.fullmatch(r'\s*(\d+(?:[,.]\d+)?)\s*([A-Za-zµΩ%]+)\s*',item['value'])
            if not value or not re.search(r'(?<![\d,.])'+re.escape(value[1])+r'\s*'+re.escape(value[2])+r'(?![A-Za-zµΩ%])',plain,re.I):continue
            # Only the same numeric role can activate this marked row; a
            # different column or an explanatory aside cannot borrow it.
            role=role_pattern(item['label'])
            if not role or not re.search(r'\b'+role,plain,re.I):continue
            out.append({'claim_id':claim_id,'locator':item['locator'],'text':item['text'],
                        'context':item['header']+' '+item['label']+' '+item['value']+' '+item['text'],'kind':'table_footnote'})
    return out


def footnote_preserved(block,condition):
    from db_richtlinien_ai.qualifications import fold,scope_words,numbers,PERMISSION
    wanted=condition.strip(' *†‡')
    if fold(wanted) in fold(block):return True
    if PERMISSION.search(wanted) and not PERMISSION.search(block):return False
    terms=scope_words(wanted);retained=scope_words(block)
    return bool(terms and terms.issubset(retained) and numbers(wanted).issubset(numbers(block)))


def decimal(text):return Decimal(text.replace(',','.').replace('−','-'))


def table_mismatches(block,query,locators,cells):
    failures=[]
    plain=re.sub(r'\[[^\]\n]+\.pdf[^\]\n]*\]','',block,flags=re.I)
    for cell in cells:
        if cell['locator'] not in locators:continue
        if any(all(q[k]==cell[k] for k in ['locator','header','label','value']) for q in getattr(cells,'qualifications',())):
            continue  # the original exception may legitimately change this value
        # Header ownership is mandatory; two different data columns cannot
        # silently share a numeric constraint merely because units coincide.
        header=header_pattern(cell['header'])
        if not re.search(header,query+' '+plain,re.I):continue
        role=role_pattern(cell['label'])
        if not role:continue
        original=re.fullmatch(r'\s*(\d+(?:[,.]\d+)?)\s*([A-Za-zµΩ%]+)\s*',cell['value'])
        if not original:continue
        other_headers={c['header'] for c in cells if c['locator']==cell['locator'] and c['header']!=cell['header']}
        for clause in re.split(r';|\n|(?<=[.!?])\s+(?=[A-ZÄÖÜ])',plain):
            own=bool(re.search(header,clause,re.I))
            others=any(re.search(header_pattern(h),clause,re.I) for h in other_headers)
            if others:continue  # never borrow another column's value in a comparison
            if not own and any(re.search(header_pattern(h),query,re.I) for h in other_headers):continue
            scoped=re.sub(header,'',clause,flags=re.I)
            for match in re.finditer(r'\b'+role+r'[^\d\n;.!?]{0,65}?([+−-]?\d+(?:[,.]\d+)?)\s*([A-Za-zµΩ%]+)',scoped,re.I):
                if match[2].casefold()!=original[2].casefold():continue  # no unproved unit conversions
                if decimal(match[1])!=decimal(original[1]):
                    failures.append(dict(reason='table_role_value_mismatch',locator=cell['locator'],
                        header=cell['header'],label=cell['label'],original_value=cell['value'],claimed_value=match[1]+' '+match[2]))
    return failures
