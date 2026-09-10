"""GGUF binary header walker — pure, no I/O, no config.

This is the hand-rolled parser for the GGUF file header used by the HF model
browser (F1). It walks the spec layout (magic, version, tensor/kv counts, then
the KV section) without materializing tensor payloads, so a partial body read
over Range requests is enough to pull the architecture numbers (block_count,
embedding_length, head_count[_kv], context_length) that size the KV cache.

Kept OUT of `fit_score.py` because it is an unrelated domain (binary format
parsing vs. VRAM fit scoring). It is pure — only `struct` and `gguf` are needed —
so it unit-tests in isolation, and `fit_score.py` re-exports it for backwards
compatibility. See ADR N/A: this is a locality split, not a new seam.
"""

from __future__ import annotations

import re
import struct

import gguf

_GGUF_RANGE_BYTES = 4_000_000
_GGUF_FILE_RE = re.compile(r"\.gguf$", re.IGNORECASE)
_QUANT_RE = re.compile(r"Q(\d[0-9]?)_?([A-Za-z0-9_]*)?", re.IGNORECASE)
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
_GGUF_SUPPORTED_VERSIONS = (2, 3)
_GGUF_VALUE_FMT = {
    gguf.GGUFValueType.UINT8: ("<B", 1),
    gguf.GGUFValueType.INT8: ("<b", 1),
    gguf.GGUFValueType.UINT16: ("<H", 2),
    gguf.GGUFValueType.INT16: ("<h", 2),
    gguf.GGUFValueType.UINT32: ("<I", 4),
    gguf.GGUFValueType.INT32: ("<i", 4),
    gguf.GGUFValueType.FLOAT32: ("<f", 4),
    gguf.GGUFValueType.BOOL: ("<?", 1),
    gguf.GGUFValueType.UINT64: ("<Q", 8),
    gguf.GGUFValueType.INT64: ("<q", 8),
    gguf.GGUFValueType.FLOAT64: ("<d", 8),
}
_ARRAY_SKIPPED = object()


def _read_gguf_string(data: bytes, off: int) -> tuple[str | None, int]:
    """GGUF strings are u64 length + bytes; returns (string, next_offset)."""
    if off + 8 > len(data):
        return None, off
    n = struct.unpack_from("<Q", data, off)[0]
    off += 8
    end = off + n
    if end > len(data):
        return None, off
    return data[off:end].decode("utf-8", errors="replace"), end


def _read_gguf_value(data: bytes, off: int, vtype: gguf.GGUFValueType) -> tuple[object | None, int]:
    """One GGUF KV value starting at `off`; returns (value, next_offset).

    ARRAY values are bounds-checked and skipped, never materialized: the fit
    formula only reads scalars/strings, so building the payload would be
    wasted work — but the walker MUST advance past the WHOLE array or every
    later key offset drifts.
    """
    if vtype == gguf.GGUFValueType.STRING:
        return _read_gguf_string(data, off)
    if vtype == gguf.GGUFValueType.ARRAY:
        # GGUF array layout (matching gguf's own reader): u32 element type,
        # then u64 count, then the elements
        if off + 12 > len(data):
            return None, off
        elem_type = gguf.GGUFValueType(struct.unpack_from("<I", data, off)[0])
        count = struct.unpack_from("<Q", data, off + 4)[0]
        off += 12
        if elem_type == gguf.GGUFValueType.STRING:
            # variable-length elements: each carries a u64 length prefix
            for _ in range(count):
                if off + 8 > len(data):
                    return None, off
                n = struct.unpack_from("<Q", data, off)[0]
                off += 8
                if n > len(data) - off:
                    return None, off
                off += n
        else:
            # fixed-size scalars: skip count × elem_size in one jump (a nested
            # array or unknown type fails the parse; count may be huge, so the
            # bounds check happens before the offset moves)
            elem = _GGUF_VALUE_FMT.get(elem_type)
            if elem is None or count > (len(data) - off) // elem[1]:
                return None, off
            off += count * elem[1]
        return _ARRAY_SKIPPED, off
    fmt = _GGUF_VALUE_FMT.get(vtype)
    if fmt is None or off + fmt[1] > len(data):
        return None, off
    return struct.unpack_from(fmt[0], data, off)[0], off + fmt[1]


def parse_gguf_header(data: bytes) -> dict | None:
    """Walk the GGUF header and pull the fields the fit formula needs.

    This is a manual walker over the spec layout (magic, version, tensor/kv
    counts, then the KV section) instead of gguf.GGUFReader, because the
    reader requires a real file path AND eagerly memmaps every tensor's data —
    on a partial 4 MB body it raises in _build_tensors before the caller can
    touch `fields` (verified against gguf 0.19.0). The KV section we care
    about lives entirely in the first few KB, well inside the range read.
    """
    try:
        if len(data) < 24:
            return None
        if struct.unpack_from("<I", data, 0)[0] != gguf.GGUF_MAGIC:
            return None
        version = struct.unpack_from("<I", data, 4)[0]
        if version not in _GGUF_SUPPORTED_VERSIONS:
            return None
        kv_count = struct.unpack_from("<Q", data, 16)[0]
    except struct.error:
        return None

    fields: dict[str, object] = {}
    off = 24
    try:
        for _ in range(min(kv_count, 4096)):
            key, off = _read_gguf_string(data, off)
            if key is None:
                return None
            if off + 4 > len(data):
                return None
            vtype = gguf.GGUFValueType(struct.unpack_from("<I", data, off)[0])
            off += 4
            value, off = _read_gguf_value(data, off, vtype)
            if value is None:
                return None
            if value is not _ARRAY_SKIPPED:
                fields[key] = value
    except (struct.error, ValueError):
        return None

    arch = fields.get("general.architecture")
    if not isinstance(arch, str):
        arch = None
    prefix = arch or "llama"

    def _int_key(*names: str) -> int | None:
        for name in names:
            value = fields.get(name)
            if isinstance(value, int) and value > 0:
                return value
        return None

    n_layer = _int_key(f"{prefix}.block_count", "llama.block_count")
    n_embd = _int_key(f"{prefix}.embedding_length", "llama.embedding_length")
    n_head = _int_key(f"{prefix}.attention.head_count", "llama.attention.head_count")
    n_kv_head = _int_key(f"{prefix}.attention.head_count_kv", "llama.attention.head_count_kv")
    context_length = _int_key(f"{prefix}.context_length", "llama.context_length")

    if n_layer is None or n_embd is None or n_head is None:
        return None  # no reliable KV estimate without the architecture numbers

    return {
        "n_layer": n_layer,
        "n_embd": n_embd,
        "n_head": n_head,
        "n_kv_head": n_kv_head if n_kv_head is not None else n_head,
        "context_length": context_length,
        "architecture": arch,
    }
