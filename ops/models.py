from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from huggingface_hub import snapshot_download
root = Path(os.environ.get('FREIBURG_ROOT', '/srv/freiburg-openrag')) / 'models'
items = [
    ('topk-io/topk-embed-v1-small','e54485ebab921f2c18c4d092b3f4c40dcca26781','topk'),
    ('BAAI/bge-reranker-v2-m3','953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e','rerank'),
    ('tencent/EVIE-8B','b3cbfc6cf6bf18fe7da480eacb0f70017d666fe9','evie'),
    ('deutsche-telekom/electra-base-de-squad2','ed28bfc62eb5b38599633b65de3adfc8dfe3f5e5','antwortspanne'),
    ('intfloat/multilingual-e5-small','614241f622f53c4eeff9890bdc4f31cfecc418b3','webui-embedding'),
]
def get(item):
    repo,rev,name=item
    folder=root/name
    snapshot_download(repo, revision=rev, local_dir=str(folder), allow_patterns=['*.json','*.py','*.safetensors','*.txt','*.model','*.jinja','*.tiktoken'],ignore_patterns=['onnx/*','openvino/*','*/onnx/*'],max_workers=3)
    (folder/'revision.txt').write_text(rev)
    if name == 'evie':
        (folder/'model-lock.json').write_text(json.dumps({"repo":repo,"revision":rev})+"\n")
    print(name,'done',flush=True)
with ThreadPoolExecutor(3) as pool:
    list(pool.map(get,items))
