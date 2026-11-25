"""Bounded local observation of RAG drafts; never an answer-release decision."""
from __future__ import annotations
import hashlib
import json
import logging
from pathlib import Path
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

logger=logging.getLogger(__name__)
PROTOCOL='nli-shadow-observation-v1'


def _sha(text):return hashlib.sha256(text.encode()).hexdigest()


def validate_endpoint(value):
    url=urllib.parse.urlsplit(value)
    if (url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost','::1'} or url.username or
        url.password or url.path!='/score' or url.query or url.fragment):
        raise ValueError('NLI shadow endpoint must be the local /score worker')
    return value


class NliShadowObserver:
    def __init__(self,endpoint,key_file,directory,*,timeout=3.,max_pending=8):
        self.endpoint=validate_endpoint(endpoint);self.key_file=Path(key_file);self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.timeout=min(10.,max(.1,float(timeout)));self.pending=queue.Queue(maxsize=max(1,min(32,int(max_pending))))
        self.thread=threading.Thread(target=self._run,daemon=True,name='local-nli-shadow');self.thread.start()

    @classmethod
    def from_config(cls,root,config):
        if not config.get('enable_nli_shadow',False):return None
        root=Path(root)
        return cls(config.get('nli_shadow_url','http://127.0.0.1:8197/score'),
            config.get('nli_shadow_key_file',root/'nli-shadow/api.key'),
            config.get('nli_shadow_directory',root/'nli-shadow/events'),
            timeout=config.get('nli_shadow_timeout',3),max_pending=config.get('nli_shadow_max_pending',8))

    def _write(self,job,stage,value):
        if not re.fullmatch(r'[a-f0-9]{32}',job):raise ValueError('Invalid observation ID')
        path=self.directory/(job+'-'+stage+'.json');temp=path.with_suffix('.pending')
        temp.write_text(json.dumps({'protocol':PROTOCOL,'shadow_only':True,'affects_answer_release':False,**value},ensure_ascii=False,indent=2))
        temp.chmod(0o600);temp.replace(path)

    def start(self,draft,question,sources):
        from db_richtlinien_ai.evidence import answer_blocks,cited_sources
        job=uuid.uuid4().hex;pairs=[];audit=[]
        for claim_id,text in list(answer_blocks(draft).items())[:6]:
            try:locators=sorted(cited_sources(text,sources))[:2]
            except ValueError:continue
            if not locators or len(text)>2000:continue
            premise='\n\n'.join(sources[loc] for loc in locators)[:20000]
            hypothesis=text
            for loc in locators:hypothesis=hypothesis.replace('['+loc+']','')
            pairs.append({'id':str(claim_id),'premise':premise,'hypothesis':hypothesis.strip()})
            audit.append({'claim_id':claim_id,'claim_sha256':_sha(hypothesis.strip()),'locators':locators,
                          'source_sha256':{loc:_sha(sources[loc]) for loc in locators}})
        if not pairs:return None
        record={'job':job,'question_sha256':_sha(question),'draft_sha256':_sha(draft),'claims':audit}
        try:self.pending.put_nowait((job,pairs,record,time.monotonic()))
        except queue.Full:
            logger.info('NLI shadow queue full; checked answer remains unaffected');return None
        return job

    def finish(self,job,answer=None,error=None):
        if job is None:return
        retained=[]
        for claim in getattr(answer,'claims',()):
            text=claim.text
            for locator in getattr(answer,'source_views',{}):text=text.replace('['+locator+']','')
            retained.append(_sha(text.strip()))
        self._write(job,'final',{'job':job,'final_answer_sha256':_sha(str(answer)) if answer is not None else None,
            'retained_checked_claim_hashes':retained,'main_error_type':type(error).__name__ if error else None,
            'comparison_is_agreement_not_gold_accuracy':True})

    def _run(self):
        while True:
            job,pairs,record,queued=self.pending.get()
            try:
                start=time.monotonic();key=self.key_file.read_text().strip()
                if not key:raise ValueError('No worker credential')
                request=urllib.request.Request(self.endpoint,data=json.dumps({'pairs':pairs}).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
                with urllib.request.urlopen(request,timeout=self.timeout) as response:
                    data=response.read(128000)
                scores=json.loads(data)
                if (scores.get('shadow_only') is not True or scores.get('protocol')!=PROTOCOL or
                    not isinstance(scores.get('scores'),list) or len(scores['scores'])!=len(pairs)):
                    raise ValueError('Invalid shadow response')
                self._write(job,'scores',{**record,'queue_seconds':start-queued,'roundtrip_seconds':time.monotonic()-start,'worker':scores})
            except Exception as exc:
                try:self._write(job,'scores',{**record,'error_type':type(exc).__name__})
                except OSError:logger.warning('NLI shadow diagnostic unavailable; answer unaffected')
            finally:self.pending.task_done()


def observe_start(observer,draft,question,sources):
    if observer is None:return None
    try:return observer.start(draft,question,sources)
    except Exception:
        logger.warning('NLI shadow observation failed; mandatory factual checks continue');return None


def observe_finish(observer,job,answer=None,error=None):
    if observer is None or job is None:return
    try:observer.finish(job,answer,error)
    except Exception:logger.warning('NLI shadow final diagnostic failed; answer unchanged')
