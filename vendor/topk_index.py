"""Resident, source-filtered MaxSim index for the original page and structure snapshots."""
from hashlib import file_digest
import json

import numpy as np
import torch


class Collection:
    def __init__(self, root, name, plan, device="cuda"):
        metadata = root / (name + ".jsonl")
        with metadata.open("rb") as handle:
            if file_digest(handle, "sha256").hexdigest() != plan["source_sha256"]:
                raise ValueError(f"{name} metadata changed")
        fields = ("point_id", "record_id", "source_file", "page", "source_sha256", "structure_sha256", "unit_ids")
        with metadata.open() as handle:
            self.rows = [{k: row[k] for k in fields if k in row} for row in map(json.loads, handle)]
        self.sources = np.array([row["source_file"] for row in self.rows], dtype=object)
        self.inverse = np.load(root / f"topk2-{name}-inverse.npy")
        offsets = np.load(root / f"topk2-{name}-offsets.npy")
        disk = np.load(root / f"topk2_mrl256-{name}.npy", mmap_mode="c")
        if (disk.shape != (plan["tokens"], 256) or disk.dtype != np.float16
                or len(self.rows) != len(self.inverse) or len(self.rows) != plan["records"]
                or offsets[0] != 0 or offsets[-1] != len(disk) or len(offsets) != plan["unique"] + 1
                or np.any(np.diff(offsets) <= 0) or self.inverse.min() < 0 or self.inverse.max() >= plan["unique"]):
            raise ValueError(f"{name} index layout mismatch")
        self.device = torch.device(device)
        self.vectors = torch.from_numpy(disk).to(self.device)
        if self.device.type == "cpu":
            self.vectors = self.vectors.float()
        self.blocks = []
        start = 0
        while start < len(offsets) - 1:
            end = int(np.searchsorted(offsets, offsets[start] + 131072, side="right") - 1)
            end = max(start + 1, end)
            lengths = torch.as_tensor(np.diff(offsets[start:end + 1]), device=self.device)
            self.blocks.append((int(offsets[start]), int(offsets[end]), lengths))
            start = end

    def scores(self, question):
        transposed = question.T.contiguous()
        blocks = []
        for lo, hi, lengths in self.blocks:
            # FP16 stored operands, FP32 accumulation and segment reductions.
            if self.device.type == "cpu":
                dots = torch.mm(self.vectors[lo:hi], transposed.float())
            else:
                dots = torch.mm(self.vectors[lo:hi], transposed, out_dtype=torch.float32)
            blocks.append(torch.segment_reduce(dots, "max", lengths=lengths).sum(dim=1))
        unique = torch.cat(blocks).cpu().numpy()
        if not np.isfinite(unique).all():
            raise ValueError("Nonfinite similarity")
        return unique[self.inverse]

    def hits(self, scores, sources, limit):
        ids = (np.arange(len(scores)) if sources is None else
               np.flatnonzero(np.fromiter((s in sources for s in self.sources), bool, count=len(scores))))
        order = ids[np.argsort(-scores[ids], kind="stable")[:limit]]
        return [{**self.rows[i], "score": float(scores[i])} for i in order]
