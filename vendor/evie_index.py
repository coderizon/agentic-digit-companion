"""Pinned EVIE encoding and hash-verified page and region indexes."""
from pathlib import Path
import hashlib
import json
import math
import os
import time

MODEL = 'tencent/EVIE-8B'
REVISION = 'b3cbfc6cf6bf18fe7da480eacb0f70017d666fe9'
DIMENSION = 4096
ENCODING = 'sentence-transformers-6.2.0-sdpa-bf16-native'


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def identity(catalog, region_sha=None):
    return dict(model=MODEL, revision=REVISION, encoding=ENCODING,
                pool_sha256=catalog.pool_sha256, region_catalog_sha256=region_sha)


def verify_vector(value):
    import torch
    if (value.ndim != 2 or value.shape[1] != DIMENSION
            or not 1 <= value.shape[0] <= 32768
            or value.dtype != torch.bfloat16 or not torch.isfinite(value).all()):
        raise ValueError('Invalid EVIE vector')


class EvieEncoder:
    def __init__(self, model_path):
        from sentence_transformers import MultiVectorEncoder
        import importlib.metadata
        import torch
        model_path = Path(model_path)
        lock = read_json(model_path / 'model-lock.json')
        if lock['repo'] != MODEL or lock['revision'] != REVISION:
            raise ValueError('Unexpected EVIE model revision')
        if importlib.metadata.version('sentence-transformers') != '6.2.0':
            raise ValueError('Unexpected EVIE encoder runtime')
        torch.set_num_threads(4)
        torch.backends.cuda.matmul.allow_tf32 = False
        self.device = os.environ.get("FREIBURG_VISION_DEVICE", "cpu")
        self.model = MultiVectorEncoder(str(model_path), device=self.device,
            model_kwargs={'dtype': torch.float32 if self.device == "cpu" else torch.float16,
                          'attn_implementation': 'sdpa'},
            local_files_only=True, trust_remote_code=False).eval()
        if self.device == "cpu":
            # The installed GPU FLA package is shared with TopK. Transformers
            # otherwise chooses its Triton kernels even for EVIE's CPU tensors.
            from transformers.models.qwen3_5 import modeling_qwen3_5 as hf
            for layer in self.model.modules():
                if isinstance(layer,hf.Qwen3_5GatedDeltaNet):
                    layer.chunk_gated_delta_rule=hf.torch_chunk_gated_delta_rule
                    layer.recurrent_gated_delta_rule=hf.torch_recurrent_gated_delta_rule
                    layer.causal_conv1d_fn=None
                    layer.causal_conv1d_update=hf.torch_causal_conv1d_update
                    norm=hf.Qwen3_5RMSNormGated(layer.head_v_dim,eps=layer.layer_norm_epsilon)
                    norm.weight=layer.norm.weight
                    layer.norm=norm.eval()

    def document(self, path):
        import torch
        from PIL import Image
        with torch.inference_mode(), Image.open(path) as image:
            value = self.model.encode_document([image.convert('RGB')], batch_size=1,
                                               show_progress_bar=False)[0].detach().cpu()
        value = value.to(torch.bfloat16)
        verify_vector(value)
        return value

    def question(self, text):
        import torch
        with torch.inference_mode():
            value = self.model.encode_query([text], batch_size=1, show_progress_bar=False)[0]
        value = value.to(torch.bfloat16)
        verify_vector(value)
        return value.to(self.device, dtype=torch.float32)


def verify_entry(catalog, folder, entry, expected_identity):
    page_id = entry['page_id']
    page = catalog.pages.get(page_id)
    if page is None or entry['identity'] != expected_identity:
        raise ValueError('Index entry has the wrong model or corpus identity')
    if entry['image_sha256'] != page.image_sha256:
        raise ValueError('Index source image changed')
    catalog.material(page_id)
    vector = Path(folder) / (page_id + '.pt')
    if digest(vector) != entry['vector_sha256']:
        raise ValueError('Index vector changed')
    return vector


def build_index(catalog, folder, encoder, region_sha=None, cached=None):
    """Resume individually verified entries and commit the complete index atomically."""
    import torch
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    expected = identity(catalog, region_sha)
    entries = []
    for page_id, page in catalog.pages.items():
        item_path = folder / (page_id + '.json')
        if item_path.exists():
            item = read_json(item_path)
            verify_entry(catalog, folder, item, expected)
        else:
            material = catalog.material(page_id)
            start = time.monotonic()
            value = cached(page_id, page.image_sha256) if cached else None
            imported = value is not None
            if value is None:
                value = encoder.document(material.path)
            verify_vector(value)
            target = folder / (page_id + '.pt')
            temporary = target.with_suffix('.tmp')
            torch.save(value.contiguous(), temporary)
            temporary.replace(target)
            item = dict(page_id=page_id, image_sha256=page.image_sha256, identity=expected,
                        vector_sha256=digest(target), shape=list(value.shape),
                        seconds=time.monotonic()-start, imported=imported)
            write_json(item_path, item)
        entries.append(item)
        print('EVIE_INDEXED', page_id, flush=True)
    manifest = dict(identity=expected, entries=entries)
    write_json(folder / 'manifest.json', manifest)
    return manifest


class EvieIndex:
    def __init__(self, catalog, folder, region_sha=None):
        import torch
        self.catalog = catalog
        self.folder = Path(folder)
        self.identity = identity(catalog, region_sha)
        self.revision = REVISION
        manifest = read_json(self.folder / 'manifest.json')
        if manifest['identity'] != self.identity:
            raise ValueError('EVIE model/index/catalog identity differs')
        self.page_ids = [row['page_id'] for row in manifest['entries']]
        if (len(self.page_ids) != len(set(self.page_ids))
                or set(self.page_ids) != set(catalog.pages)):
            raise ValueError('EVIE index is incomplete or has duplicate entries')
        self.vectors = []
        for row in manifest['entries']:
            path = verify_entry(catalog, self.folder, row, self.identity)
            value = torch.load(path, map_location='cpu', weights_only=True)
            verify_vector(value)
            if list(value.shape) != row['shape']:
                raise ValueError('EVIE vector dimensions differ')
            self.vectors.append(value.to(os.environ.get("FREIBURG_VISION_DEVICE", "cpu"), dtype=torch.float32))

    def candidates(self,sources=None,source_hashes=None):
        if sources is None and source_hashes is None:
            return list(range(len(self.page_ids)))
        import unicodedata
        canonical=lambda s:unicodedata.normalize('NFKC',s).strip().casefold()
        names={canonical(n) for n in sources or []}
        hashes=set(source_hashes or [])
        if source_hashes is not None:
            return [i for i,key in enumerate(self.page_ids) if self.catalog.pages[key].source_sha256 in hashes]
        return [i for i,key in enumerate(self.page_ids)
                if canonical(Path(self.catalog.pages[key].source).name) in names]

    def rank(self, vector, limit, candidates=None):
        import torch
        indices=list(range(len(self.page_ids))) if candidates is None else candidates
        if not indices:return []
        with torch.inference_mode():
            scores = torch.stack([(vector @ page.T).amax(dim=-1).sum()
                                  for page in (self.vectors[i] for i in indices)]).cpu().tolist()
        if len(scores) != len(indices) or not all(math.isfinite(s) for s in scores):
            raise ValueError('Invalid EVIE scores')
        order = sorted(zip((self.page_ids[i] for i in indices), scores), key=lambda item: (-item[1], item[0]))[:limit]
        return [{'page_id': page_id, 'score': score} for page_id, score in order]
