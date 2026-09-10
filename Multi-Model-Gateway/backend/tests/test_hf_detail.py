"""Unit tests for the HF model browser + GGUF-accurate fit (services/fit_score.py).

Stdlib unittest only — no pytest dependency. Tests are offline: the Hub API
calls are mocked at httpx.AsyncClient, GGUF header reads are mocked at
read_gguf_metadata, and hardware probing is patched. If the package can't be
imported in this environment (missing settings/secret deps), the whole suite
skips cleanly.
"""
import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, patch

try:
    from app.services import fit_score
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    fit_score = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class _FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
        return None


class _FakeClient:
    """httpx.AsyncClient stand-in: returns the configured response per URL."""

    def __init__(self, responses=None, default=None):
        self._responses = responses or {}
        self._default = default
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        for prefix, response in self._responses.items():
            if url.startswith(prefix):
                return response
        if self._default is not None:
            return self._default
        return _FakeResponse(payload=None, status_code=404)


def _gguf_bytes() -> bytes:
    """A minimal real-GGUF-layout header: magic, v3, counts, then the KV
    fields the parser needs. Only the KV section is required for metadata
    extraction — tensor info is never read by the header walker."""
    payload = bytearray()

    def push_string(s: str):
        raw = s.encode("utf-8")
        payload.extend(struct.pack("<Q", len(raw)))
        payload.extend(raw)

    def push_u32(v: int):
        payload.extend(struct.pack("<I", v))

    def push_u64(v: int):
        payload.extend(struct.pack("<Q", v))

    # magic "GGUF", version 3, tensor_count 0 (unused), kv_count 6
    payload.extend(b"GGUF")
    push_u32(3)
    push_u64(0)
    push_u64(6)

    kv = [
        ("general.architecture", "llama", "STRING", "llama"),
        ("llama.block_count", 32, "UINT32", None),
        ("llama.embedding_length", 4096, "UINT32", None),
        ("llama.attention.head_count", 32, "UINT32", None),
        ("llama.attention.head_count_kv", 8, "UINT32", None),
        ("llama.context_length", 8192, "UINT32", None),
    ]
    for key, value, kind, _ in kv:
        push_string(key)
        if kind == "STRING":
            push_u32(8)  # GGUFValueType.STRING
            push_string(value)
        else:
            push_u32(4)  # GGUFValueType.UINT32
            push_u32(value)

    return bytes(payload)


class GroupGgufQuantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def test_single_files_each_become_one_entry(self):
        files = [
            {"filename": "model-Q4_K_M.gguf", "size": 4_000_000_000},
            {"filename": "model-Q8_0.gguf", "size": 7_000_000_000},
        ]
        quants = fit_score.group_gguf_quants(files)
        self.assertEqual(len(quants), 2)
        self.assertEqual([q["quant"] for q in quants], ["Q4_K_M", "Q8_0"])
        self.assertFalse(quants[0]["is_sharded"])
        self.assertEqual(quants[0]["filenames"], ["model-Q4_K_M.gguf"])

    def test_shards_group_with_summed_size(self):
        files = [
            {"filename": "model-Q4_K_M-00001-of-00003.gguf", "size": 1_500_000_000},
            {"filename": "model-Q4_K_M-00003-of-00003.gguf", "size": 1_500_000_000},
            {"filename": "model-Q4_K_M-00002-of-00003.gguf", "size": 1_500_000_000},
        ]
        quants = fit_score.group_gguf_quants(files)
        self.assertEqual(len(quants), 1)
        entry = quants[0]
        self.assertEqual(entry["quant"], "Q4_K_M")
        self.assertTrue(entry["is_sharded"])
        self.assertEqual(len(entry["filenames"]), 3)
        self.assertEqual(entry["size_bytes"], 4_500_000_000)

    def test_non_gguf_ignored(self):
        files = [
            {"filename": "config.json", "size": 1000},
            {"filename": "model-Q4_K_M.gguf", "size": 4_000_000_000},
            {"filename": "model.safetensors", "size": 9_000_000_000},
        ]
        quants = fit_score.group_gguf_quants(files)
        self.assertEqual(len(quants), 1)
        self.assertEqual(quants[0]["quant"], "Q4_K_M")

    def test_sorted_by_size_ascending(self):
        files = [
            {"filename": "model-Q8_0.gguf", "size": 7_000_000_000},
            {"filename": "model-Q4_K_M.gguf", "size": 4_000_000_000},
            {"filename": "model-Q2_K.gguf", "size": 2_000_000_000},
        ]
        quants = fit_score.group_gguf_quants(files)
        self.assertEqual([q["quant"] for q in quants], ["Q2_K", "Q4_K_M", "Q8_0"])

    def test_quant_regex_extraction(self):
        self.assertEqual(fit_score._extract_quant("foo.Q4_K_M.gguf"), "Q4_K_M")
        self.assertEqual(fit_score._extract_quant("bar-Q8_0.gguf"), "Q8_0")
        self.assertEqual(fit_score._extract_quant("model-Q1_0-00001-of-00002.gguf"), "Q1_0")
        self.assertIsNone(fit_score._extract_quant("model-f16.gguf"))


class EstimateGgufFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    META = {"n_layer": 32, "n_embd": 4096, "n_head": 32, "n_kv_head": 8, "context_length": 8192, "architecture": "llama"}

    def test_missing_meta_is_unknown(self):
        fit = fit_score.estimate_gguf_fit(4_700_000_000, None, 6144, None, 8192)
        self.assertEqual(fit["verdict"], "unknown")
        self.assertIsNone(fit["need_gb"])

    def test_no_gpu_is_cpu_only(self):
        fit = fit_score.estimate_gguf_fit(4_700_000_000, self.META, None, None, 8192)
        self.assertEqual(fit["verdict"], "cpu_only")
        self.assertIsInstance(fit["need_gb"], float)

    def test_29gb_on_6gb_is_likely_too_large(self):
        fit = fit_score.estimate_gguf_fit(29_000_000_000, self.META, 6144, None, 8192)
        self.assertEqual(fit["verdict"], "likely_too_large")

    def test_exact_kv_arithmetic_and_verdict(self):
        """4.7 GB weights, GQA 32/8, 8k ctx on 6 GB total VRAM: KV is exact (not
        the old 128KB-per-7B ballpark), so the verdict falls to fits_cpu_offload."""
        kv_bytes = (
            2 * 32 * 8192 * 4096 * fit_score.settings.KV_CACHE_BYTES_PER_ELEMENT * (8 / 32)
        )
        kv_gb = kv_bytes / 1e9
        need_gb = 4.7 + kv_gb
        fit = fit_score.estimate_gguf_fit(4_700_000_000, self.META, 6144, None, 8192)
        self.assertEqual(fit["need_gb"], round(need_gb, 1))
        # 6 GB total VRAM * 0.9 margin = 5.4 < need (~5.77); offload need <= 6*2
        self.assertEqual(fit["verdict"], "fits_cpu_offload")
        self.assertEqual(fit["score"], min(100, round(100 * 6 / need_gb)))

    def test_fits_fully_with_headroom(self):
        """Small quant + big VRAM clears the 10% margin → fits_fully."""
        fit = fit_score.estimate_gguf_fit(2_000_000_000, self.META, 20480, None, 8192)
        self.assertEqual(fit["verdict"], "fits_fully")

    def test_10gb_offloads_to_32gb_ram(self):
        """10 GB weights (≤ 32 GB RAM) and need ≤ 8 GB VRAM + 32 GB RAM →
        fits_cpu_offload."""
        fit = fit_score.estimate_gguf_fit(10_000_000_000, self.META, 8192, 32768, 8192)
        self.assertEqual(fit["verdict"], "fits_cpu_offload")
        # weights (10 GB) > VRAM (8 GB) → the "weights exceed" branch, not KV overflow
        self.assertIn("weights exceed", fit["rationale"])

    def test_50gb_weights_exceed_ram_and_combined(self):
        """50 GB weights exceed 32 GB RAM and the 40 GB combined budget →
        likely_too_large."""
        fit = fit_score.estimate_gguf_fit(50_000_000_000, self.META, 8192, 32768, 8192)
        self.assertEqual(fit["verdict"], "likely_too_large")


class GgufHeaderParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def test_header_walker_extracts_metadata(self):
        meta = fit_score._parse_gguf_header(_gguf_bytes())
        self.assertIsNotNone(meta)
        self.assertEqual(meta["n_layer"], 32)
        self.assertEqual(meta["n_embd"], 4096)
        self.assertEqual(meta["n_head"], 32)
        self.assertEqual(meta["n_kv_head"], 8)
        self.assertEqual(meta["context_length"], 8192)
        self.assertEqual(meta["architecture"], "llama")

    def test_bad_magic_returns_none(self):
        data = bytearray(_gguf_bytes())
        data[0:4] = b"NOPE"
        self.assertIsNone(fit_score._parse_gguf_header(bytes(data)))

    def test_truncated_body_returns_none(self):
        self.assertIsNone(fit_score._parse_gguf_header(_gguf_bytes()[:30]))

    def test_large_scalar_array_skipped_without_offset_drift(self):
        """An ARRAY KV with >4096 elements must be skipped in FULL — the old
        walker truncated the element loop at 4096 and then misread every key
        that followed. The big array payload sits BEFORE the scalar key we
        need, so a correct walker must land exactly on it."""
        data = bytearray()

        def push_string(s: str):
            raw = s.encode("utf-8")
            data.extend(struct.pack("<Q", len(raw)))
            data.extend(raw)

        def push_u32(v: int):
            data.extend(struct.pack("<I", v))

        def push_u64(v: int):
            data.extend(struct.pack("<Q", v))

        data.extend(b"GGUF")
        push_u32(3)          # version
        push_u64(0)          # tensor_count (unused)
        push_u64(5)          # kv_count

        # KV 1: general.architecture (STRING)
        push_string("general.architecture")
        push_u32(8)          # STRING
        push_string("qwen2")

        # KV 2: a 5000-element UINT32 array (5000 * 4 = 20000 payload bytes)
        push_string("some.large_array")
        push_u32(9)          # ARRAY
        push_u32(4)          # element type UINT32
        push_u64(5000)       # count > 4096, the old truncation cap
        data.extend(struct.pack("<I", 7) * 5000)

        # KVs 3-5: the scalar fields the parser needs — must all still parse
        # correctly after the skipped array
        push_string("llama.block_count")
        push_u32(4)          # UINT32
        push_u32(28)
        push_string("llama.embedding_length")
        push_u32(4)
        push_u32(3584)
        push_string("llama.attention.head_count")
        push_u32(4)
        push_u32(28)

        meta = fit_score._parse_gguf_header(bytes(data))
        self.assertIsNotNone(meta)
        self.assertEqual(meta["architecture"], "qwen2")
        self.assertEqual(meta["n_layer"], 28)
        self.assertEqual(meta["n_embd"], 3584)
        self.assertEqual(meta["n_head"], 28)

    def test_string_array_skipped_without_offset_drift(self):
        """STRING-array elements are variable-length (u64 prefix + bytes), so
        the skip must walk each element instead of jumping a fixed size."""
        data = bytearray()

        def push_string(s: str):
            raw = s.encode("utf-8")
            data.extend(struct.pack("<Q", len(raw)))
            data.extend(raw)

        def push_u32(v: int):
            data.extend(struct.pack("<I", v))

        def push_u64(v: int):
            data.extend(struct.pack("<Q", v))

        data.extend(b"GGUF")
        push_u32(3)
        push_u64(0)
        push_u64(5)

        push_string("general.architecture")
        push_u32(8)          # STRING
        push_string("llama")

        # KV 2: a 3-element STRING array with uneven lengths
        push_string("tokenizer.ggml.tokens")
        push_u32(9)          # ARRAY
        push_u32(8)          # element type STRING
        push_u64(3)
        for token in ("a", "bbbb", "cc"):
            push_string(token)

        # KVs 3-5: the scalar fields the parser needs — must all still parse
        # correctly after the skipped string array
        push_string("llama.block_count")
        push_u32(4)          # UINT32
        push_u32(28)
        push_string("llama.embedding_length")
        push_u32(4)
        push_u32(3584)
        push_string("llama.attention.head_count")
        push_u32(4)
        push_u32(28)

        meta = fit_score._parse_gguf_header(bytes(data))
        self.assertIsNotNone(meta)
        self.assertEqual(meta["architecture"], "llama")
        self.assertEqual(meta["n_layer"], 28)
        self.assertEqual(meta["n_embd"], 3584)
        self.assertEqual(meta["n_head"], 28)

    def test_truncated_array_payload_returns_none(self):
        """The array skip must fail the parse (never silently truncate) when
        the declared payload runs past the buffer."""
        data = bytearray()

        def push_string(s: str):
            raw = s.encode("utf-8")
            data.extend(struct.pack("<Q", len(raw)))
            data.extend(raw)

        def push_u32(v: int):
            data.extend(struct.pack("<I", v))

        def push_u64(v: int):
            data.extend(struct.pack("<Q", v))

        data.extend(b"GGUF")
        push_u32(3)
        push_u64(0)
        push_u64(1)

        push_string("some.large_array")
        push_u32(9)          # ARRAY
        push_u32(4)          # element type UINT32
        push_u64(5000)       # count claims 20000 bytes, buffer has none
        # no payload follows

        self.assertIsNone(fit_score._parse_gguf_header(bytes(data)))

    def test_missing_kv_head_defaults_to_head(self):
        data = bytearray()

        def push_string(s: str):
            raw = s.encode("utf-8")
            data.extend(struct.pack("<Q", len(raw)))
            data.extend(raw)

        def push_u32(v: int):
            data.extend(struct.pack("<I", v))

        def push_u64(v: int):
            data.extend(struct.pack("<Q", v))

        data.extend(b"GGUF")
        push_u32(3)
        push_u64(0)
        push_u64(4)
        for key, value in [
            ("general.architecture", "llama"),
            ("llama.block_count", 32),
            ("llama.embedding_length", 4096),
            ("llama.attention.head_count", 32),
        ]:
            push_string(key)
            if key == "general.architecture":
                push_u32(8)
                push_string(value)
            else:
                push_u32(4)
                push_u32(value)

        meta = fit_score._parse_gguf_header(bytes(data))
        self.assertIsNotNone(meta)
        self.assertEqual(meta["n_head"], 32)
        self.assertEqual(meta["n_kv_head"], 32)  # defaults to n_head


class BuildHfModelDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        fit_score._gguf_meta_cache.clear()
        fit_score._hf_cache.clear()

    def test_build_hf_model_detail(self):
        detail_payload = {
            "id": "org/qwen2-vl-7b-reason",
            "downloads": 1234,
            "likes": 42,
            "lastModified": "2026-01-01T00:00:00.000Z",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "tags": ["text-generation", "function-calling", "reasoning", "vision"],
            "cardData": {"text": "A capable model"},
            "safetensors": {"parameters": 7.2, "total": 14_000_000_000},
            "siblings": [
                {"rfilename": "model-Q4_K_M-00001-of-00003.gguf", "size": 1_400_000_000},
                {"rfilename": "model-Q4_K_M-00002-of-00003.gguf", "size": 1_400_000_000},
                {"rfilename": "model-Q4_K_M-00003-of-00003.gguf", "size": 1_400_000_000},
                {"rfilename": "model-Q8_0.gguf", "size": 7_000_000_000},
                {"rfilename": "model-mlx-q4.mlx", "size": 4_000_000_000},
                {"rfilename": "config.json", "size": 1000},
            ],
        }
        client = _FakeClient({
            "https://huggingface.co/api/models/": _FakeResponse(payload=detail_payload),
        })
        hw = {"gpu_available": True, "gpus": [
            {"index": 0, "name": "Test GPU", "vram_total_mb": 8192, "vram_free_mb": 6144},
        ], "ram_total_mb": 32768}
        meta_mock = AsyncMock(return_value=BuildHfModelDetailTests.META)

        async def run():
            with (
                patch("app.services.fit_score.httpx.AsyncClient", return_value=client),
                patch.object(fit_score, "probe_hardware", new=AsyncMock(return_value=hw)),
                patch.object(fit_score, "read_gguf_metadata", new=meta_mock),
            ):
                return await fit_score.build_hf_model_detail("org/qwen2-vl-7b-reason", 8192)

        result = asyncio.run(run())
        self.assertEqual(result["repo_id"], "org/qwen2-vl-7b-reason")
        self.assertEqual(result["downloads"], 1234)
        self.assertEqual(result["params_b"], 7.2)
        self.assertEqual(result["arch"], "qwen")
        self.assertEqual(result["context_tokens"], 8192)
        self.assertEqual(result["hardware"], hw)
        self.assertTrue(result["has_gguf"])
        # GGUF + MLX format pills; capability pills from the tag scrape
        self.assertEqual(result["formats"], ["GGUF", "MLX"])
        self.assertEqual(result["capabilities"], ["Vision", "Tool Use", "Reasoning"])
        # two quant options: the sharded Q4 group (summed) and the single Q8
        self.assertEqual(len(result["quants"]), 2)
        q4 = result["quants"][0]
        self.assertEqual(q4["quant"], "Q4_K_M")
        self.assertEqual(q4["size_bytes"], 4_200_000_000)
        self.assertTrue(q4["is_sharded"])
        self.assertEqual(len(q4["filenames"]), 3)
        # 4.2 GB weights + 1.07 GB KV = 5.27 GB <= 8.0 GB total VRAM * 0.9 margin
        self.assertEqual(q4["fit"]["verdict"], "fits_fully")
        self.assertEqual(result["quants"][1]["quant"], "Q8_0")
        self.assertFalse(result["quants"][1]["is_sharded"])
        self.assertIn("fit", result["quants"][1])
        # header reads happened for both quants, one per quant option
        self.assertEqual(meta_mock.call_count, 2)

    META = {"n_layer": 32, "n_embd": 4096, "n_head": 32, "n_kv_head": 8, "context_length": 8192, "architecture": "llama"}

    def test_missing_siblings_returns_none(self):
        client = _FakeClient({
            "https://huggingface.co/api/models/": _FakeResponse(payload={"id": "org/x"}),
        })

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                return await fit_score.get_hf_model_detail("org/x")

        self.assertIsNone(asyncio.run(run()))


class ReadGgufMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        fit_score._gguf_meta_cache.clear()

    def test_read_gguf_metadata_parses_and_sends_range(self):
        client = _FakeClient({
            "https://huggingface.co/": _FakeResponse(content=_gguf_bytes()),
        })

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                return await fit_score.read_gguf_metadata("https://huggingface.co/org/m/resolve/main/m.gguf")

        meta = asyncio.run(run())
        self.assertEqual(meta["n_layer"], 32)
        self.assertEqual(meta["n_kv_head"], 8)
        headers = client.calls[0]["headers"]
        self.assertIn("Range", headers)
        self.assertEqual(headers["Range"], f"bytes=0-{fit_score._GGUF_RANGE_BYTES - 1}")

    def test_read_gguf_metadata_failure_returns_none_and_caches(self):
        client = _FakeClient(default=_FakeResponse(status_code=500))

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                first = await fit_score.read_gguf_metadata("https://huggingface.co/org/m/resolve/main/m.gguf")
                second = await fit_score.read_gguf_metadata("https://huggingface.co/org/m/resolve/main/m.gguf")
                return first, second

        first, second = asyncio.run(run())
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(client.calls), 1)  # the failure was cached


if __name__ == "__main__":
    unittest.main()
