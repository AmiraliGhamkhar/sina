"""VRAM-aware fit scoring for the local model catalog and Hugging Face.

Local metadata comes from LM Studio (/api/v0/models: quant, max context);
Hugging Face metadata comes from the HF Hub /api/models (safetensors
parameters/size, downloads, likes). VRAM need is a documented heuristic, not
a promise:

  weights_gb ≈ size on disk          (or params_b × bytes/param for the quant)
  kv_gb      ≈ ctx_tokens × 128 KB × (params_b / 7)   # GQA-era ballpark
  need_gb    ≈ (weights + kv) × 1.1                   # runtime overhead

Verdicts are scored against TOTAL VRAM (not free), and factor RAM offload:
fits_fully (need ≤ total VRAM × (1-margin)), partial_offload (overflows total
VRAM but the weights/need fit in VRAM + RAM), wont_fit (too large even with
offload), cpu_only (no GPU detected).

The HF model browser (F1) is exact where it can be: for GGUF repos it reads
the quant file's own header (llama.block_count / embedding_length /
head_count[_kv]) and sizes the KV cache from the real architecture instead of
the ballpark above — see estimate_gguf_fit / read_gguf_metadata. Verdicts:
fits_fully (with FIT_SAFETY_MARGIN headroom), fits_cpu_offload, likely_too_large,
cpu_only, unknown.
"""
import logging
import re
import time

import httpx

from app.core.config import settings
from app.services.hardware import probe_hardware
from app.services.fit_score_gguf import (
    _GGUF_FILE_RE,
    _GGUF_RANGE_BYTES,
    _QUANT_RE,
    _SHARD_RE,
    parse_gguf_header,
)

# Backwards-compatible alias: the walker is named `parse_gguf_header` in the
# gguf submodule; the rest of the app (and tests) imported it as `_parse_gguf_header`.
_parse_gguf_header = parse_gguf_header

logger = logging.getLogger(__name__)

# effective bytes per parameter for common quantizations (GGUF-style ballpark)
QUANT_BYTES_PER_PARAM = {
    "q2": 0.40, "q3": 0.48, "q4": 0.60, "q5": 0.70, "q6": 0.85, "q8": 1.10,
    "f16": 2.0, "bf16": 2.0, "fp16": 2.0, "f32": 4.0, "fp32": 4.0,
}
DEFAULT_BYTES_PER_PARAM = 0.60  # assume Q4-ish when the quant is unknown

# shared sort order for every cookbook: best-fit first, then score within a rank
_VERDICT_RANK = {"fits_fully": 0, "partial_offload": 1, "cpu_only": 2, "wont_fit": 3, "unknown": 4}

_PARAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB]\b")

# in-process TTL cache for Hugging Face catalog fetches (keyed by search+limit)
_HF_CACHE_TTL = 600  # seconds
_hf_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


def _cache_get(search: str, limit: int) -> list[dict] | None:
    item = _hf_cache.get((search, limit))
    if item is None:
        return None
    ts, models = item
    if time.monotonic() - ts > _HF_CACHE_TTL:
        _hf_cache.pop((search, limit), None)
        return None
    return models


def _cache_set(search: str, limit: int, models: list[dict]) -> None:
    _hf_cache[(search, limit)] = (time.monotonic(), models)


# ---- GGUF quant files (HF model browser, F1) ----

# header read is a Range request: 4 MB covers the magic/version/counts, the
# whole KV metadata section, and the tensor-info table for large models

# in-process TTL cache for GGUF header reads (keyed by resolve URL)
_GGUF_META_CACHE_TTL = 600  # seconds
_gguf_meta_cache: dict[str, tuple[float, dict | None]] = {}

# known architecture substrings used to infer `arch` when the model has no
# safetensors block to name it
_KNOWN_ARCHS = (
    "llama", "mistral", "mixtral", "qwen", "gemma", "phi", "falcon",
    "deepseek", "gpt", "mpt", "yi", "baichuan", "olmo", "stablelm",
    "internlm", "starcoder", "codestral", "granite", "nemotron", "solar",
    "exaone", "smollm", "glm", "minicpm", "t5", "bert", "roberta",
)

# tag scrape for the browser's capability pills (best-effort; often empty)
_VISION_TAGS = {"image-text-to-text", "visual-question-answering", "image-to-text",
                "any-to-text", "multimodal", "vision"}
_TOOL_TAGS = {"function-calling", "tool-calling", "tools"}

# shared sort order for the browser quant list: smallest first so cheap quants
# are scored first and the header-read cap hits the big ones last
_QUANT_READ_CAP = 12


def _parse_params_b(text: str) -> float | None:
    """'qwen2.5-7b-instruct' → 7.0 ; 'llama-3.1-8B-Q4' → 8.0"""
    match = _PARAMS_RE.search(text or "")
    return float(match.group(1)) if match else None


def _bytes_per_param(quant: str | None) -> float:
    q = (quant or "").lower()
    for key, bpp in QUANT_BYTES_PER_PARAM.items():
        if key in q:
            return bpp
    return DEFAULT_BYTES_PER_PARAM


async def get_local_models() -> list[dict]:
    """Catalog from LM Studio (/api/v0/models); if it's down the catalog is empty."""
    models: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{settings.LM_URL}/api/v0/models")
            response.raise_for_status()
        for m in response.json().get("data", []):
            if m.get("type") not in (None, "llm", "vlm"):
                continue  # skip embedding models etc.
            models.append({
                "id": m.get("id", ""),
                "source": "lmstudio",
                "params_b": _parse_params_b(m.get("id", "")),
                "quant": m.get("quantization"),
                "size_bytes": None,  # LM Studio doesn't report file size here
                "max_context": m.get("max_context_length"),
            })
    except Exception as e:
        logger.warning("LM Studio catalog unavailable: %r", e)

    return models


def estimate_fit(model: dict, vram_total_mb: int | None, ram_total_mb: int | None, context_tokens: int) -> dict:
    params_b = model.get("params_b")

    if model.get("size_bytes"):
        weights_gb = model["size_bytes"] / 1e9
    elif params_b:
        weights_gb = params_b * _bytes_per_param(model.get("quant"))
    else:
        weights_gb = None

    if weights_gb is None:
        return {
            "verdict": "unknown",
            "score": 0,
            "need_gb": None,
            "rationale": "could not estimate model size (no file size or parameter count)",
        }

    kv_gb = context_tokens * 128e3 * ((params_b or 7) / 7) / 1e9
    need_gb = (weights_gb + kv_gb) * 1.1

    if vram_total_mb is None:
        return {
            "verdict": "cpu_only",
            "score": 0,
            "need_gb": round(need_gb, 1),
            "rationale": f"no GPU detected; ~{need_gb:.1f} GB would be needed at {context_tokens} ctx (CPU inference will be slow)",
        }

    vram_gb = vram_total_mb / 1024
    score = min(100, round(100 * vram_gb / need_gb))

    if need_gb <= vram_gb * (1 - settings.FIT_SAFETY_MARGIN):
        verdict = "fits_fully"
        rationale = (f"~{weights_gb:.1f} GB weights + ~{kv_gb:.1f} GB KV cache "
                     f"(@{context_tokens} ctx) fits in {vram_gb:.1f} GB VRAM")
    else:
        if ram_total_mb is not None:
            ram_gb = ram_total_mb / 1024
            offloadable = (weights_gb <= ram_gb) and (need_gb <= vram_gb + ram_gb)
        else:
            offloadable = need_gb <= vram_gb * 2.0
        if offloadable:
            verdict = "partial_offload"
            if weights_gb > vram_gb:
                rationale = (f"~{weights_gb:.1f} GB weights exceed {vram_gb:.1f} GB VRAM — "
                             f"partial GPU offload to RAM, expect reduced speed")
            else:
                rationale = (f"~{weights_gb:.1f} GB weights fit in VRAM but the "
                             f"~{need_gb:.1f} GB working set exceeds it — partial GPU "
                             f"offload (KV cache overflow), expect reduced speed")
        else:
            verdict = "wont_fit"
            rationale = f"needs ~{need_gb:.1f} GB — too large for {vram_gb:.1f} GB VRAM even with RAM offload"

    return {
        "verdict": verdict,
        "score": score,
        "need_gb": round(need_gb, 1),
        "rationale": rationale,
    }


async def build_cookbook(hardware: dict, context_tokens: int | None = None) -> dict:
    context_tokens = context_tokens or settings.COOKBOOK_CONTEXT_TOKENS
    vram_total_mb = None
    if hardware["gpu_available"]:
        # single-GPU assumption: score against the card with the most total VRAM
        vram_total_mb = max(g["vram_total_mb"] for g in hardware["gpus"])
    ram_total_mb = hardware.get("ram_total_mb")

    entries = []
    for model in await get_local_models():
        fit = estimate_fit(model, vram_total_mb, ram_total_mb, context_tokens)
        entries.append({**{k: v for k, v in model.items() if k != "size_bytes"}, **fit})

    entries.sort(key=lambda e: (_VERDICT_RANK.get(e["verdict"], 5), -e["score"]))

    recommendation = None
    if entries and entries[0]["verdict"] in ("fits_fully", "partial_offload"):
        recommendation = entries[0]["id"]

    return {
        "hardware": hardware,
        "context_tokens": context_tokens,
        "models": entries,
        "recommendation": recommendation,
    }


async def get_hf_models(search: str = "", limit: int = 10) -> list[dict]:
    """Top Hugging Face models from the Hub API, sorted by downloads.

    Returns a lightweight catalog entry per model (params from safetensors,
    size in bytes, popularity stats). Never raises — on any failure it logs a
    warning and returns [] so the cookbook page degrades to an empty table.
    Results are cached in-process for _HF_CACHE_TTL seconds.
    """
    cached = _cache_get(search, limit)
    if cached is not None:
        return cached

    params: dict = {"limit": min(limit, 50), "sort": "downloads"}
    if search:
        params["search"] = search

    models: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get("https://huggingface.co/api/models", params=params)
            response.raise_for_status()
        for m in response.json():
            weights = m.get("safetensors") or m.get("pytorch") or {}
            params_b = weights.get("parameters") if isinstance(weights, dict) else None
            if params_b is None:
                params_b = _parse_params_b(m.get("id", ""))
            models.append({
                "id": m.get("id", ""),
                "source": "hf",
                "params_b": params_b,
                "size_bytes": weights.get("total") if isinstance(weights, dict) else None,
                "quant": None,
                "max_context": None,
                "downloads": m.get("downloads"),
                "likes": m.get("likes"),
                "lastModified": m.get("lastModified"),
                "pipeline_tag": m.get("pipeline_tag"),
                "library_name": m.get("library_name"),
            })
    except Exception as e:
        logger.warning("Hugging Face catalog unavailable: %r", e)
        return []

    _cache_set(search, limit, models)
    return models


async def build_hf_cookbook(hardware: dict, context_tokens: int, search: str = "", limit: int = 10) -> dict:
    """Fit-score the Hugging Face catalog against the probed hardware.

    Shares estimate_fit and the verdict sort order with build_cookbook, so the
    two tables rank apples-to-apples. Response mirrors the local cookbook but
    with a `search` echo and `count` instead of a recommendation.
    """
    vram_total_mb = None
    if hardware["gpu_available"]:
        # single-GPU assumption: score against the card with the most total VRAM
        vram_total_mb = max(g["vram_total_mb"] for g in hardware["gpus"])
    ram_total_mb = hardware.get("ram_total_mb")

    entries = []
    for model in await get_hf_models(search, limit):
        fit = estimate_fit(model, vram_total_mb, ram_total_mb, context_tokens)
        entries.append({**{k: v for k, v in model.items() if k != "size_bytes"}, **fit})

    entries.sort(key=lambda e: (_VERDICT_RANK.get(e["verdict"], 5), -e["score"]))

    return {
        "hardware": hardware,
        "context_tokens": context_tokens,
        "search": search,
        "models": entries,
        "count": len(entries),
    }


# ---- GGUF quant grouping (HF model browser) ----


def _extract_quant(filename: str) -> str | None:
    """Pull the quant token from a GGUF filename.

    The shard suffix and the extension are stripped first so the LAST quant
    match wins ("qwen2.5-7b-instruct-Q4_K_M.gguf" → "Q4_K_M", never a stray
    "Q2" from the model id). Returns None when the name has no Q-token
    (f16/bf16/f32 checkpoints).
    """
    stem = _SHARD_RE.sub("", filename)
    stem = _GGUF_FILE_RE.sub("", stem)
    matches = list(_QUANT_RE.finditer(stem))
    if not matches:
        return None
    return matches[-1].group(0).upper()


def group_gguf_quants(files: list[dict]) -> list[dict]:
    """Collapse a repo's file list into one entry per logical quant option.

    Sharded checkpoints ("-00001-of-00003.gguf") group under their shared
    quant token with the parts' sizes summed; every non-sharded .gguf file is
    its own entry. Non-GGUF files are ignored. Entries are sorted by size
    ascending so the cheapest quant is scored first. Returns [] for a repo
    with no GGUF files.
    """
    options: dict[str, dict] = {}

    def _add(filename: str, size: int | None, is_shard: bool) -> None:
        quant = _extract_quant(filename) or filename
        entry = options.setdefault(
            quant,
            {"quant": quant, "filenames": [], "size_bytes": 0, "is_sharded": False},
        )
        entry["filenames"].append(filename)
        entry["size_bytes"] += size or 0
        entry["is_sharded"] = entry["is_sharded"] or is_shard

    for f in files:
        filename = f.get("filename") or ""
        if not _GGUF_FILE_RE.search(filename):
            continue
        _add(filename, f.get("size"), is_shard=bool(_SHARD_RE.search(filename)))

    return sorted(options.values(), key=lambda o: o["size_bytes"])


async def read_gguf_metadata(resolve_url: str) -> dict | None:
    """Fetch and parse a GGUF file's header metadata.

    Range-requests the first _GGUF_RANGE_BYTES of a resolve URL, then parses
    the header. Any failure (network, non-2xx, bad magic, truncated KV) logs
    a warning and returns None — never raises. Results are cached in-process
    per URL for _GGUF_META_CACHE_TTL seconds so a repo's quants are not
    re-read on every browser refresh.
    """
    cached = _gguf_meta_cache.get(resolve_url)
    if cached is not None and time.monotonic() - cached[0] < _GGUF_META_CACHE_TTL:
        return cached[1]

    headers = {"Range": f"bytes=0-{_GGUF_RANGE_BYTES - 1}"}
    if settings.HF_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HF_TOKEN}"

    meta = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(resolve_url, headers=headers)
            response.raise_for_status()
        meta = _parse_gguf_header(response.content)
    except Exception as e:  # noqa: BLE001 — a header read failure is never fatal
        logger.warning("GGUF header read failed for %s: %r", resolve_url, e)
        meta = None

    _gguf_meta_cache[resolve_url] = (time.monotonic(), meta)
    return meta


# ---- GGUF-accurate fit scoring ----


def estimate_gguf_fit(size_bytes: int, meta: dict | None, vram_total_mb: int | None, ram_total_mb: int | None, context_tokens: int) -> dict:
    """Fit verdict for one GGUF quant against the probed VRAM (+ RAM offload).

    Exact where the file allows: weights come from the file size, KV from the
    file's own architecture numbers (llama.block_count etc.):

      kv_bytes = 2 × n_layer × ctx × n_embd × KV_CACHE_BYTES_PER_ELEMENT
                 × (n_kv_head / n_head)   # GQA ratio; 1 for MHA

    No extra overhead multiplier — weights and KV are exact, so the 10%
    FIT_SAFETY_MARGIN lives in the fits_fully threshold only. Verdicts:
    fits_fully | fits_cpu_offload | likely_too_large | cpu_only | unknown.
    """
    weights_gb = size_bytes / 1e9

    if meta is None:
        return {
            "verdict": "unknown",
            "score": 0,
            "need_gb": None,
            "rationale": "couldn't read model metadata (GGUF header parse failed)",
        }

    gqa_ratio = (meta["n_kv_head"] / meta["n_head"]) if meta["n_head"] else 1.0
    kv_bytes = (
        2 * meta["n_layer"] * context_tokens * meta["n_embd"]
        * settings.KV_CACHE_BYTES_PER_ELEMENT * gqa_ratio
    )
    kv_gb = kv_bytes / 1e9
    need_gb = weights_gb + kv_gb

    if vram_total_mb is None:
        return {
            "verdict": "cpu_only",
            "score": 0,
            "need_gb": round(need_gb, 1),
            "rationale": (f"no GPU detected; ~{weights_gb:.1f} GB weights + ~{kv_gb:.1f} GB "
                          f"KV cache (@{context_tokens} ctx) would be needed (CPU inference will be slow)"),
        }

    vram_gb = vram_total_mb / 1024
    score = min(100, round(100 * vram_gb / need_gb))

    if need_gb <= vram_gb * (1 - settings.FIT_SAFETY_MARGIN):
        verdict = "fits_fully"
        rationale = (f"~{weights_gb:.1f} GB weights + ~{kv_gb:.1f} GB KV cache "
                     f"(@{context_tokens} ctx) fits in {vram_gb:.1f} GB VRAM")
    else:
        if ram_total_mb is not None:
            ram_gb = ram_total_mb / 1024
            offloadable = (weights_gb <= ram_gb) and (need_gb <= vram_gb + ram_gb)
        else:
            offloadable = need_gb <= vram_gb * 2.0
        if offloadable:
            # weights/need overflow VRAM but the runtime can offload the overflow
            # to RAM (llama.cpp / LM Studio partial offload); expect reduced speed
            verdict = "fits_cpu_offload"
            if weights_gb > vram_gb:
                rationale = (f"~{weights_gb:.1f} GB weights exceed {vram_gb:.1f} GB VRAM — "
                             f"partial GPU offload to RAM, expect reduced speed")
            else:
                rationale = (f"~{weights_gb:.1f} GB weights fit in VRAM but the "
                             f"~{need_gb:.1f} GB working set exceeds it — partial GPU "
                             f"offload (KV cache overflow), expect reduced speed")
        else:
            verdict = "likely_too_large"
            rationale = f"needs ~{need_gb:.1f} GB — too large for {vram_gb:.1f} GB VRAM even with RAM offload"

    return {
        "verdict": verdict,
        "score": score,
        "need_gb": round(need_gb, 1),
        "rationale": rationale,
    }


# ---- HF model detail (HF model browser) ----


def _hf_auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.HF_TOKEN}"} if settings.HF_TOKEN else {}


def _parse_arch(repo_id: str) -> str | None:
    rid = (repo_id or "").lower()
    for arch in _KNOWN_ARCHS:
        if arch in rid:
            return arch
    return None


def _derive_capabilities(model: dict, description: str) -> list[str]:
    """Best-effort capability pills scraped from tags/description/id."""
    tags = {t.lower() for t in model.get("tags") or []}
    capabilities = []
    if tags & _VISION_TAGS or "vl" in (model.get("id") or "").lower():
        capabilities.append("Vision")
    if tags & _TOOL_TAGS:
        capabilities.append("Tool Use")
    if "reasoning" in tags or "reasoning" in (description or "").lower() or "reason" in (model.get("id") or "").lower():
        capabilities.append("Reasoning")
    return capabilities


async def get_hf_model_detail(repo_id: str) -> dict | None:
    """Full metadata for one HF repo from the Hub API.

    Returns the fields the browser needs (stats, params, file list, capability
    pills, format pills) or None on ANY failure — never raises. Files come
    from the API's `siblings` array; a repo without siblings is unusable for
    quant grouping and returns None.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://huggingface.co/api/models/{repo_id}",
                headers=_hf_auth_headers(),
            )
            response.raise_for_status()
        model = response.json()
    except Exception as e:  # noqa: BLE001 — a detail failure is a 404, never a 500
        logger.warning("Hugging Face model detail unavailable for %s: %r", repo_id, e)
        return None

    siblings = model.get("siblings")
    if not isinstance(siblings, list) or not siblings:
        return None

    weights = model.get("safetensors") or {}
    params_b = weights.get("parameters") if isinstance(weights, dict) else None
    if params_b is None:
        params_b = _parse_params_b(model.get("id", ""))
    arch = weights.get("arch") if isinstance(weights, dict) else None
    if not arch:
        arch = _parse_arch(model.get("id", ""))

    description = ""
    card_data = model.get("cardData") or {}
    if isinstance(card_data, dict):
        description = card_data.get("text") or card_data.get("description") or ""

    files = [{"filename": s.get("rfilename", ""), "size": s.get("size")} for s in siblings]

    return {
        "id": model.get("id", repo_id),
        "downloads": model.get("downloads"),
        "likes": model.get("likes"),
        "lastModified": model.get("lastModified"),
        "pipeline_tag": model.get("pipeline_tag"),
        "library_name": model.get("library_name"),
        "tags": model.get("tags") or [],
        "description": description,
        "params_b": params_b,
        "arch": arch,
        "files": files,
        "capabilities": _derive_capabilities(model, description),
        "formats": _derive_formats(files),
    }


def _derive_formats(files: list[dict]) -> list[str]:
    """Format pills from the file list: GGUF quants, MLX weights."""
    formats = []
    if any(_GGUF_FILE_RE.search(f.get("filename", "")) for f in files):
        formats.append("GGUF")
    if any((".mlx" in f.get("filename", "") or "-mlx" in f.get("filename", "")) for f in files):
        formats.append("MLX")
    return formats


async def build_hf_model_detail(repo_id: str, context_tokens: int) -> dict | None:
    """Browser payload for one HF repo: stats + per-quant GGUF fit.

    Probes hardware once, groups the repo's GGUF files into quant options,
    and — for the first _QUANT_READ_CAP of them, smallest first — reads the
    quant's header and computes a fit. Returns None when the repo detail
    itself is unavailable (caller turns that into a 404).
    """
    detail = await get_hf_model_detail(repo_id)
    if detail is None:
        return None

    hw = await probe_hardware()
    vram_total_mb = None
    if hw["gpu_available"]:
        # single-GPU assumption: score against the card with the most total VRAM
        vram_total_mb = max(g["vram_total_mb"] for g in hw["gpus"])
    ram_total_mb = hw.get("ram_total_mb")

    quants = group_gguf_quants(detail["files"])
    scored = quants[:_QUANT_READ_CAP]  # bound header reads; cheapest first
    for quant in scored:
        first = quant["filenames"][0]
        resolve_url = f"https://huggingface.co/{repo_id}/resolve/main/{first}"
        meta = await read_gguf_metadata(resolve_url)
        quant["fit"] = estimate_gguf_fit(quant["size_bytes"], meta, vram_total_mb, ram_total_mb, context_tokens)

    return {
        "repo_id": repo_id,
        "downloads": detail["downloads"],
        "likes": detail["likes"],
        "last_modified": detail["lastModified"],
        "description": detail["description"],
        "params_b": detail["params_b"],
        "arch": detail["arch"],
        "pipeline_tag": detail["pipeline_tag"],
        "library_name": detail["library_name"],
        "capabilities": detail["capabilities"],
        "formats": detail["formats"],
        "quants": scored,
        "has_gguf": bool(quants),
        "context_tokens": context_tokens,
        "hardware": hw,
    }
