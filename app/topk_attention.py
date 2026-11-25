"""Packed document attention using native PyTorch on GPUs with small shared memory."""
import sys

import torch
import torch.nn.functional as F


def document_segments(document_index, *, causal):
    _, counts = torch.unique_consecutive(document_index, return_counts=True)
    return counts.tolist(), causal


def packed_sdpa(query, key, value, *, block_mask, enable_gqa, kernel_options):
    # Every packed document has its own attention boundary. The pretrained
    # encoder's projections, rotary positions, gating and weights stay in place.
    lengths, causal = block_mask
    if enable_gqa and key.shape[1] != query.shape[1]:
        groups = query.shape[1] // key.shape[1]
        key = key.repeat_interleave(groups, dim=1)
        value = value.repeat_interleave(groups, dim=1)
    outputs = []
    start = 0
    for length in lengths:
        end = start + length
        k, v = key[:, :, start:end], value[:, :, start:end]
        for offset in range(0, length, 256):
            q = query[:, :, start+offset:min(start+offset+256,end)]
            mask = None
            if causal:
                positions = torch.arange(offset,offset+q.shape[2],device=q.device)
                mask = torch.arange(length,device=q.device)[None,:] <= positions[:,None]
            outputs.append(F.scaled_dot_product_attention(q,k,v,attn_mask=mask))
        start = end
    return torch.cat(outputs,dim=2)


def configure_packed_attention(encoder, device):
    if device.type == "cuda" and torch.cuda.get_device_capability(device)[0] >= 8:
        return
    layer = next(child for child in encoder.modules() if type(child).__name__ == "PackedQwen3_5Attention")
    backbone = sys.modules[type(layer).__module__]
    backbone.document_mask = document_segments
    backbone.compiled_flex_attention = packed_sdpa
    if device.type == "cpu":
        from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5RMSNormGated, torch_recurrent_gated_delta_rule
        def conv(x, weight, bias, activation, cu_seqlens):
            pieces = []
            edges = cu_seqlens.tolist()
            for a,b in zip(edges,edges[1:]):
                part = F.conv1d(x[:,a:b].transpose(1,2),weight[:,None],bias,
                                padding=weight.shape[1]-1, groups=weight.shape[0])[:,:,:b-a]
                pieces.append(F.silu(part).transpose(1,2))
            return torch.cat(pieces,1), None
        def delta(q,k,v,g,beta,cu_seqlens,**options):
            edges = cu_seqlens.tolist()
            segments = [torch_recurrent_gated_delta_rule(q[:,a:b],k[:,a:b],v[:,a:b],g[:,a:b],beta[:,a:b],**options)[0]
                        for a,b in zip(edges,edges[1:])]
            return torch.cat(segments,1), None
        backbone.fla_causal_conv1d = conv
        for child in encoder.modules():
            if type(child).__name__ == "PackedGatedDeltaNet":
                child.chunk_gated_delta_rule = delta
                norm=Qwen3_5RMSNormGated(child.head_v_dim,eps=child.layer_norm_epsilon)
                norm.weight=child.norm.weight
                child.norm=norm.eval()
