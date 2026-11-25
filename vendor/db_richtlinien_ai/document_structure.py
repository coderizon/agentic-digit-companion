"""Source-positioned document structure; parser observations are search hints.

This module has no Docling/model/network dependency. Native cell geometry can
support additional *negative* checks; neither a parser nor this index approves
an answer. Original locators and text remain the factual evidence boundary.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

PROTOCOL = 'registered-document-structure-v1'
MODULE = re.compile(r'(?<!\d)\d{3}[.]\d{4}(?:[AVZ]\d{1,2})?(?!\w)',re.I)
PRINTED_DATE = re.compile(r'\b(?:Gültig\s+ab|Stand)\s*:?\s*(\d{1,2}[.]\d{1,2}[.]\d{4})',re.I)
MEASURE = re.compile(r'^\s*[<>≤≥]?[+−±-]?\s*\d+(?:[,.]\d+)?(?:\s*[:/]\s*\d+(?:[,.]\d+)?)?\s*(?:[A-Za-zµΩ°%]+(?:/[A-Za-z]+)?)?\s*[*†‡]*\s*$')
UNIT = re.compile(r'(?<![A-Za-z])(?:mm|cm|km/h|km|m/s|m|kV|mV|V|mA|A|kW|W|kN|N|MHz|kHz|Hz|dB|ms|s|°C|°|%)(?![A-Za-z])')


def digest_json(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def text_key(value):
    value=unicodedata.normalize('NFKC',value).casefold()
    value=re.sub(r'(?<=[^\W\d_])-\s*(?=[^\W\d_])','',value)
    return ''.join(value.split())


def text_present(needle,haystack):
    wanted=text_key(needle);text=text_key(haystack)
    if not wanted:return False
    position=text.find(wanted)
    while position>=0:
        end=position+len(wanted)
        if not (position and wanted[0].isdigit() and text[position-1] in '+-−±<>≤≥' or
                position and wanted[0].isalnum() and text[position-1].isalnum() or
                end<len(text) and wanted[-1].isalnum() and text[end].isalnum()):return True
        position=text.find(wanted,position+1)
    return False


def bbox_top_left(value,width,height):
    if not isinstance(value,dict):raise ValueError('Missing source rectangle')
    numbers=[value.get(k) for k in ('l','t','r','b')]
    if any(type(n) not in (int,float) or not math.isfinite(n) for n in numbers):raise ValueError('Invalid source rectangle')
    left,top,right,bottom=numbers
    origin=value.get('coord_origin')
    if origin=='BOTTOMLEFT':top,bottom=height-top,height-bottom
    elif origin!='TOPLEFT':raise ValueError('Unknown coordinate origin')
    if not (-2<=left<right<=width+2 and -2<=top<bottom<=height+2):raise ValueError('Rectangle outside original page')
    return [round(max(0,left),4),round(max(0,top),4),round(min(width,right),4),round(min(height,bottom),4)]


def native_in_box(native,box):
    left,top,right,bottom=box;margin=2.0
    words=[w for w in native['words'] if left-margin<=(w[0]+w[2])/2<=right+margin
           and top-margin<=(w[1]+w[3])/2<=bottom+margin]
    return ' '.join(w[4] for w in words)


def _resolve(raw,ref):
    if not isinstance(ref,str) or not re.fullmatch(r'#/\w+/\d+',ref):return None
    group,index=ref[2:].split('/')
    try:return raw[group][int(index)]
    except (KeyError,IndexError,ValueError,TypeError):return None


def _refs(raw,values):
    return [item for value in values or [] if isinstance(value,dict)
            and (item:=_resolve(raw,value.get('cref'))) is not None]


def _walk(raw):
    seen=set()
    def visit(item):
        ref=item.get('self_ref')
        if ref in seen:return
        seen.add(ref)
        if item.get('prov'):yield item
        for child in _refs(raw,item.get('children',[])):yield from visit(child)
    yield from visit(raw.get('body',{}))
    yield from visit(raw.get('furniture',{}))
    for kind in ['texts','tables','pictures']:
        for item in raw.get(kind,[]):
            if item.get('self_ref') not in seen:yield from visit(item)


def _cell_records(table,page):
    data=table.get('data',{});rows=data.get('num_rows');cols=data.get('num_cols')
    if type(rows) is not int or type(cols) is not int or not 1<=rows<=1000 or not 1<=cols<=100:return [],False
    cells=[];occupied=set();unambiguous=True
    for n,cell in enumerate(data.get('table_cells',[])[:10000]):
        try:
            a,b,c,d=[cell[key] for key in ['start_row_offset_idx','end_row_offset_idx','start_col_offset_idx','end_col_offset_idx']]
            if any(type(v) is not int for v in (a,b,c,d)) or not 0<=a<b<=rows or not 0<=c<d<=cols:raise ValueError('Invalid table span')
            positions={(r,k) for r in range(a,b) for k in range(c,d)}
            if occupied&positions:unambiguous=False
            occupied.update(positions)
            text=cell.get('text','')
            if not isinstance(text,str) or len(text)>4000:raise ValueError('Invalid cell text')
            box=bbox_top_left(cell['bbox'],page['width'],page['height']) if cell.get('bbox') else None
            native=native_in_box(page,box) if box else ''
            cells.append({'id':str(n),'row_start':a,'row_end':b,'col_start':c,'col_end':d,
                'text':text,'bbox':box,'column_header':cell.get('column_header') is True,
                'row_header':cell.get('row_header') is True,'row_section':cell.get('row_section') is True,
                'native_verified':bool(box and text_present(text,native)),'native_text':native,
                'markers':list(dict.fromkeys(re.findall(r'[*†‡]+',text+' '+native)))})
        except (KeyError,ValueError,TypeError):unambiguous=False
    return cells,unambiguous


def _role(text):
    roles=[]
    for kind,pattern in [('maximum',r'\b(?:maxim\w*|höchst\w*|größt\w*)'),('minimum',r'\b(?:minim\w*|mindest\w*|kleinst\w*)'),
                         ('nominal',r'\b(?:nenn\w*|regel\w*)'),('tolerance',r'\btoleranz\w*')]:
        if re.search(pattern,text,re.I):roles.append(kind)
    return roles


def table_rows(cells,unambiguous):
    """Keep explicit multi-level headers/labels/footnotes, never fill blanks."""
    # A detector may mark the first *data* row as a header. In particular,
    # "< Nennstrom" must not become the owner of every later measurement.
    headers=[c for c in cells if c['column_header'] and c['text'].strip()
             and not re.match(r'^\s*[<>≤≥±+−-]',c['text']) and not MEASURE.fullmatch(c['text'])]
    footnotes=[c for c in cells if re.match(r'^\s*[*†‡]',c['text']) and len(c['text'])>35]
    max_col=max((c['col_end'] for c in cells),default=0)
    values=[c for c in cells if c not in headers and c not in footnotes and c['col_start']>0
            and c['text'].strip() and len(c['text'])<=1000]
    out=[]
    for value in values:
        upper=[c for c in headers if c['row_end']<=value['row_start']
               and c['col_start']<=value['col_start'] and value['col_end']<=c['col_end']]
        upper.sort(key=lambda c:(c['row_start'],c['col_start']))
        labels=[c for c in cells if c['col_end']<=value['col_start'] and c['text'].strip()
                and c['row_start']<=value['row_start']<c['row_end']
                and c not in headers and not MEASURE.fullmatch(c['text']) and c not in footnotes
                and re.search(r'[A-Za-zÄÖÜäöüß]',c['text'])]
        labels.sort(key=lambda c:c['col_start'])
        attached=[c for c in footnotes if set(c['markers'])&set(value['markers'])]
        units=list(dict.fromkeys(UNIT.findall(value['text'])));literal_units=bool(units)
        if not units:
            units=list(dict.fromkeys(u for c in upper+labels for u in UNIT.findall(c['text'])))
        leaf=upper[-1] if upper else None
        vertical=bool(value['bbox'] and labels and all(c['bbox'] and
            max(c['bbox'][1],value['bbox'][1])<=min(c['bbox'][3],value['bbox'][3])+3 for c in labels))
        horizontal=bool(value['bbox'] and leaf and leaf['bbox'] and
            abs((leaf['bbox'][0]+leaf['bbox'][2]-value['bbox'][0]-value['bbox'][2])/2)<max(12,(value['bbox'][2]-value['bbox'][0])*.75))
        property_value=bool(max_col==2 and value['col_start']==1 and labels and not upper
            and sum(len(c['text']) for c in labels)<=120)
        verified=bool(unambiguous and value['native_verified'] and labels and vertical and (upper and horizontal or property_value)
            and all(c['native_verified'] for c in labels+upper+attached) and (literal_units or len(units)<=1)
            and (not value['markers'] or attached))
        if not labels:continue
        sections=[c for c in cells if c['col_start']==0 and c['col_end']==max_col and c['row_end']<=value['row_start']
                  and c['text'].strip() and len(c['text'])<120 and not re.search(r'\d',c['text']) and c not in footnotes]
        section=max(sections,key=lambda c:c['row_start']) if sections else None
        out.append({'value_cell':value['id'],'row_label_cells':[c['id'] for c in labels],
            'column_header_cells':[c['id'] for c in upper],'footnote_cells':[c['id'] for c in attached],
            'value':value['text'],'row_labels':[c['text'] for c in labels],
            'column_headers':[c['text'] for c in upper],'header_unknown':not bool(upper),
            'row_group_headers':[section['text']] if section else [],
            'row_group_native_verified':bool(section is None or section['native_verified']),
            'unit_literals':units,'unit_inference_ambiguous':not literal_units and len(units)>1,
            'roles':_role(' '.join(c['text'] for c in labels)),
            'footnotes':[c['text'] for c in attached],'has_unresolved_marker':bool(value['markers'] and not attached),
            'native_relationship_verified':verified,
            'search_text':' | '.join([*(c['text'] for c in upper),*([section['text']] if section else []),*(c['text'] for c in labels),value['text']])})
    return out


def compile_document(raw,spec,native_pages,source_kind='sonstiges'):
    """Compile parser output while binding every observation to the original."""
    if not isinstance(raw,dict) or raw.get('schema_name')!='DoclingDocument':raise ValueError('Unknown parser document')
    pages={int(k):v for k,v in native_pages.items()};result=[];stack=[];previous_modules=None
    for item in _walk(raw):
        label=item.get('label','');provs=item.get('prov',[])
        for prov in provs[:8]:
            pg=prov.get('page_no')
            if type(pg) is not int or pg not in pages:continue
            native=pages[pg]
            modules=list(dict.fromkeys(MODULE.findall(native['text'][:1800])))
            owned=list(dict.fromkeys(w[4] for w in native['words'] if source_kind=='ril'
                and w[3]<=native['height']*.18 and MODULE.fullmatch(w[4].strip())))
            if owned and previous_modules is not None and set(owned)!=previous_modules:stack=[]
            if owned:previous_modules=set(owned)
            try:box=bbox_top_left(prov['bbox'],native['width'],native['height'])
            except (KeyError,ValueError,TypeError):continue
            text=item.get('text','')
            if not isinstance(text,str):text=''
            native_text=native_in_box(native,box);verified=text_present(text,native_text)
            unit={'id':digest_json([spec['source_sha256'],pg,item['self_ref'],box])[:32],
                'source_file':spec['source_file'],'source_sha256':spec['source_sha256'],'page':pg,
                'locator':f"{spec['source_file']}, Seite {pg}",'parser_ref':item['self_ref'],
                'kind':label,'bbox':box,'text':text,'native_verified':verified,'source_kind':source_kind,
                'module_refs':modules,
                'document_module_codes':owned,
                'printed_dates':list(dict.fromkeys(PRINTED_DATE.findall(native['text']))),
                'date_is_current_validity_assertion':False,
                'heading_path':[dict(h) for h in stack], 'parent_id':stack[-1]['id'] if stack else None,
                'captions':[],'footnotes':[],'references':[],
                'multiple_page_provenance':len(provs)>1}
            if label=='section_header':
                level=item.get('level',1)
                if type(level) is not int or not 1<=level<=12:level=1
                while stack and stack[-1]['level']>=level:stack.pop()
                unit['heading_path']=[dict(h) for h in stack];unit['parent_id']=stack[-1]['id'] if stack else None
                stack.append({'id':unit['id'],'text':text,'level':level,'page':pg,'native_verified':verified,'bbox':box})
            for field in ['captions','footnotes','references']:
                for linked in _refs(raw,item.get(field)):
                    for link_prov in linked.get('prov',[])[:4]:
                        link_pg=link_prov.get('page_no')
                        if link_pg not in pages:continue
                        try:link_box=bbox_top_left(link_prov['bbox'],pages[link_pg]['width'],pages[link_pg]['height'])
                        except (ValueError,KeyError,TypeError):continue
                        linked_text=linked.get('text','')
                        if isinstance(linked_text,str):
                            unit[field].append({'text':linked_text,'page':link_pg,'bbox':link_box,
                                'native_verified':text_present(linked_text,native_in_box(pages[link_pg],link_box))})
            if label=='table':
                cells,clear=_cell_records(item,native)
                rows=table_rows(cells,clear and len(provs)==1)
                # Repeated page furniture can be classified as a table by the
                # layout model. Keep it as metadata, not as a data-cell source.
                measures=sum(bool(MEASURE.fullmatch(c['text']) and UNIT.search(c['text'])) for c in cells)
                furniture=box[1]<native['height']*.20 and measures<2 and any(MODULE.search(c['text']) for c in cells)
                unit['kind']='page_header' if furniture else 'table'
                unit['table']={'rows':item.get('data',{}).get('num_rows'), 'cols':item.get('data',{}).get('num_cols'),
                    'unambiguous_grid':clear,'cells':cells,'records':[] if furniture else rows}
                unit['text']='\n'.join(c['text'] for c in cells if c['text'])
                unit['native_verified']=bool(cells and all(c['native_verified'] for c in cells if c['text']))
            scope_text=' '.join([unit['text'],*[h['text'] for h in unit['heading_path']],*[c['text'] for c in unit['captions']]])
            unit['scope_identifiers']=list(dict.fromkeys(re.findall(r'\bRe\s*\d+(?:\s*mod|\s*S-Bahn)?\b',scope_text,re.I)))
            unit['rule_roles']=_role(scope_text)
            hint_parts=[*unit['module_refs'],*(h['text'] for h in unit['heading_path']),*[c['text'] for c in unit['captions']],unit['text']]
            if unit.get('table'):hint_parts.extend(r['search_text'] for r in unit['table']['records'])
            unit['search_text']='\n'.join(hint_parts)[:30000]
            result.append(unit)
    return {'source_file':spec['source_file'],'source_sha256':spec['source_sha256'],'source_kind':source_kind,
        'pages':[{'page':pg,'width':p['width'],'height':p['height'],'rotation':p.get('rotation',0),
                  'native_text_sha256':hashlib.sha256(p['text'].encode()).hexdigest(),
                  'native_text_chars':len(p['text'])} for pg,p in pages.items()], 'units':result}


def validate_artifact(data,registry):
    if not isinstance(data,dict) or data.get('protocol')!=PROTOCOL:raise ValueError('Unknown structure artifact')
    docs=data.get('documents');seen=set()
    if not isinstance(docs,list) or not 1<=len(docs)<=5000:raise ValueError('Invalid structure document count')
    by_name={unicodedata.normalize('NFKC',v['name']).casefold():v for v in registry.values()}
    count=0
    for doc in docs:
        spec=by_name.get(unicodedata.normalize('NFKC',doc.get('source_file','')).casefold())
        if not spec or spec['sha256']!=doc.get('source_sha256'):raise ValueError('Structure source identity mismatch')
        pages={p['page']:p for p in doc.get('pages',[])}
        for pg,p in pages.items():
            if type(pg) is not int or pg not in spec['indexed_pages']:raise ValueError('Structure page outside original index')
            if not all(type(p.get(k)) in (float,int) and math.isfinite(p[k]) and p[k]>0 for k in ['width','height']):raise ValueError('Invalid page geometry')
        doc_units=doc.get('units',[]);doc_ids={u.get('id') for u in doc_units}
        for u in doc_units:
            if u.get('source_sha256')!=spec['sha256'] or u.get('source_file')!=doc['source_file'] or u.get('page') not in pages:raise ValueError('Unit changed original identity')
            if not re.fullmatch(r'[a-f0-9]{32}',u.get('id','')) or u['id'] in seen:raise ValueError('Invalid or repeated structure ID')
            seen.add(u['id']);count+=1
            pg=pages[u['page']];box=u.get('bbox')
            if not isinstance(box,list) or len(box)!=4:raise ValueError('Invalid unit rectangle')
            bbox_top_left(dict(zip(['l','t','r','b'],box),coord_origin='TOPLEFT'),pg['width'],pg['height'])
            if not isinstance(u.get('text'),str) or len(u['text'])>100000 or not isinstance(u.get('search_text'),str) or len(u['search_text'])>30000:raise ValueError('Invalid structure text size')
            if u.get('locator')!=f"{doc['source_file']}, Seite {u['page']}":raise ValueError('Structure locator mismatch')
            if u.get('parent_id') is not None and u['parent_id'] not in doc_ids:raise ValueError('Unknown original parent')
            for h in u.get('heading_path',[]):
                if h.get('id') not in doc_ids or h.get('page') not in pages:raise ValueError('Heading crossed original boundary')
            table=u.get('table')
            if table:
                cells=table.get('cells')
                if not isinstance(cells,list) or len(cells)>10000:raise ValueError('Invalid table-cell count')
                cell_ids={c.get('id') for c in cells}
                if len(cell_ids)!=len(cells):raise ValueError('Repeated table-cell ID')
                for c in cells:
                    if not isinstance(c.get('text'),str) or len(c['text'])>4000:raise ValueError('Invalid table-cell text')
                    if not (type(c.get('row_start')) is int and type(c.get('row_end')) is int
                        and 0<=c['row_start']<c['row_end']<=table['rows'] and type(c.get('col_start')) is int
                        and type(c.get('col_end')) is int and 0<=c['col_start']<c['col_end']<=table['cols']):raise ValueError('Table span outside original grid')
                    if c.get('bbox'):
                        bbox_top_left(dict(zip(['l','t','r','b'],c['bbox']),coord_origin='TOPLEFT'),pg['width'],pg['height'])
                expected=[] if u['kind']=='page_header' else table_rows(cells,table['unambiguous_grid'] and not u['multiple_page_provenance'])
                if expected!=table.get('records'):raise ValueError('Table ownership differs from source cells')
    if count>300000:raise ValueError('Structure index exceeds configured bound')
    return data


class StructureIndex:
    """A source-bound searchable sidecar; no answer text is synthesized here."""
    def __init__(self,path,expected_sha256,registry):
        path=Path(path)
        if path.stat().st_size>128*1024*1024:raise ValueError('Structure artifact too large')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Structure artifact changed')
        self.sha256=expected_sha256;self.data=validate_artifact(json.loads(raw),registry)
        self.by_page={};self.by_id={};self.pages={}
        for doc in self.data['documents']:
            for page in doc['pages']:self.pages[(doc['source_file'],page['page'])]=page
            for unit in doc['units']:
                self.by_id[unit['id']]=unit
                self.by_page.setdefault((doc['source_file'],unit['page']),[]).append(unit)
        self.term_counts={u['id']:Counter(self.terms(u['search_text'])) for u in self.by_id.values() if u['kind']!='page_header'}
        self.df=Counter(term for c in self.term_counts.values() for term in c)

    @classmethod
    def from_config(cls,config,root):
        if not config.get('enable_document_structure',False):return None
        def resolve(value):
            p=Path(value);return p if p.is_absolute() else Path(root)/p
        registry_path=resolve(config['structure_source_registry']);raw=registry_path.read_bytes()
        index=cls(resolve(config['structure_artifact']),config['structure_artifact_sha256'],json.loads(raw)['documents'])
        if index.data.get('source_registry_sha256')!=hashlib.sha256(raw).hexdigest():raise ValueError('Structure original registry changed')
        return index

    @staticmethod
    def terms(text):
        return [t for t in re.findall(r'[\wäöüß]+',text.casefold()) if len(t)>2 or t.isdigit()]

    def ranked_units(self,query,limit=20,source=None,page=None):
        terms=set(self.terms(query));out=[];total=len(self.term_counts)
        for uid,counts in self.term_counts.items():
            u=self.by_id[uid]
            if source is not None and (u['source_file']!=source or page is not None and u['page']!=page):continue
            score=sum(math.log(1+(total-self.df[t]+.5)/(self.df[t]+.5))*counts[t]/(counts[t]+1.2) for t in terms if t in counts)
            if score>0:out.append((u,score))
        return sorted(out,key=lambda x:(-x[1],x[0]['id']))[:limit]

    def page_hints(self,query,source,page):
        values=self.ranked_units(query,3,source,page)
        if not values:return None
        units=[u for u,_ in values]
        records=[r for u in units if u.get('table') for r in u['table']['records']]
        from db_richtlinien_ai.query_intent import numeric_intent,linguistic_hints,quantity_compounds
        intent=numeric_intent(query)
        terms=set(self.terms(query+' '+' '.join([*linguistic_hints(query),*quantity_compounds(query,self.df)])))
        def row_priority(row):
            owner=any(re.search(r'(?<!\w)'+r'\s*'.join(re.escape(w) for w in h.split())+r'(?!\w)',query,re.I) for h in row['column_headers'])
            return (owner,intent in row['roles'] if intent else False,len(terms&set(self.terms(row['search_text']))))
        records.sort(key=row_priority,reverse=True)
        return {'search_metadata_only':True,'module_refs':list(dict.fromkeys(m for u in units for m in u['module_refs']))[:4],
            'document_module_codes':list(dict.fromkeys(m for u in units for m in u.get('document_module_codes',[])))[:3],
            'source_kind':units[0]['source_kind'],
            'printed_dates':list(dict.fromkeys(d for u in units for d in u['printed_dates'])),
            'headings':list(dict.fromkeys(h['text'] for u in units for h in u['heading_path'])),
            'unit_ids':[u['id'] for u in units],
            'scope_identifiers':list(dict.fromkeys(s for u in units for s in u['scope_identifiers'])),
            'captions':[c['text'] for u in units for c in u['captions']][:3],
            'table_hints':[{k:r[k] for k in ['value','column_headers','row_labels','row_group_headers','header_unknown','unit_literals','roles','footnotes','native_relationship_verified']} for r in records[:3]]}

    def parent_pages(self,query,source,page):
        values=[]
        for unit,_ in self.ranked_units(query,2,source,page):
            values.extend(h['page'] for h in unit['heading_path'] if h['native_verified'])
            values.extend(f['page'] for f in unit['footnotes'] if f['native_verified'])
        return list(dict.fromkeys(pg for pg in values if pg!=page))[-2:]

    def search_points(self):
        """Self-contained paragraphs, table rows and captions for an extra index."""
        groups={}
        for unit in self.by_id.values():
            prefix='\n'.join([*unit['module_refs'],*(h['text'] for h in unit['heading_path'])])
            if unit['kind']=='page_header' or unit['kind']=='page_footer':continue
            if unit.get('table'):
                yield {'id':unit['id'],'unit_ids':[unit['id']],'source_file':unit['source_file'],
                    'source_sha256':unit['source_sha256'],'page':unit['page'],'text':unit['search_text'][:5000]}
                for n,row in enumerate(unit['table']['records']):
                    yield {'id':unit['id']+'-row-'+str(n),'unit_ids':[unit['id']],
                        'source_file':unit['source_file'],'source_sha256':unit['source_sha256'],'page':unit['page'],
                        'text':(prefix+'\n'+row['search_text']+'\n'+' '.join(row['footnotes']))[:5000]}
            elif unit['kind']=='picture':
                text=' '.join(c['text'] for c in unit['captions'])
                if text:yield {'id':unit['id'],'unit_ids':[unit['id']],'source_file':unit['source_file'],
                    'source_sha256':unit['source_sha256'],'page':unit['page'],'text':(prefix+'\n'+text)[:5000]}
            elif unit['kind']!='section_header' and unit['text'].strip():
                key=(unit['source_file'],unit['page'],unit['parent_id'])
                groups.setdefault(key,[]).append((unit,prefix))
        for (source,page,parent),items in groups.items():
            chunks=[];current=[];size=0
            for unit,prefix in items:
                if current and size+len(unit['text'])>1800:chunks.append(current);current=[];size=0
                current.append((unit,prefix));size+=len(unit['text'])
            if current:chunks.append(current)
            for chunk in chunks:
                unit,prefix=chunk[0];ids=[u['id'] for u,_ in chunk]
                text=prefix+'\n'+'\n'.join(u['text'] for u,_ in chunk)
                if len(text.strip())<30:continue
                yield {'id':digest_json(ids)[:32],'unit_ids':ids,'source_file':source,
                    'source_sha256':unit['source_sha256'],'page':page,'text':text[:5000]}
