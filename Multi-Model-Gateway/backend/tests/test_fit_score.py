"""Unit tests for the model-fit scoring services (services/fit_score.py).

Stdlib unittest only — no pytest dependency. Tests are offline: the Hugging
Face catalog calls are mocked at httpx.AsyncClient, and get_hf_models is
patched inside the build_hf_cookbook tests. If the package can't be imported
in this environment (missing settings/secret deps), the whole suite skips
cleanly.
"""
import asyncio
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
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeHfClient:
    """Minimal httpx.AsyncClient stand-in: records the request, returns payload."""

    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_params = None
        self.get_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.last_url = url
        self.last_params = params
        self.get_count += 1
        return _FakeResponse(self._payload)


class GetHfModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        fit_score._hf_cache.clear()

    def test_safetensors_metadata_is_parsed(self):
        """safetensors.parameters → params_b, safetensors.total → size_bytes."""
        client = _FakeHfClient([{
            "id": "org/params-model",
            "safetensors": {"parameters": 7.2, "total": 5_000_000_000},
            "downloads": 1234,
            "likes": 42,
            "lastModified": "2026-01-01T00:00:00.000Z",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
        }])

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                return await fit_score.get_hf_models()

        models = asyncio.run(run())
        self.assertEqual(len(models), 1)
        entry = models[0]
        self.assertEqual(entry["id"], "org/params-model")
        self.assertEqual(entry["source"], "hf")
        self.assertEqual(entry["params_b"], 7.2)
        self.assertEqual(entry["size_bytes"], 5_000_000_000)
        self.assertEqual(entry["quant"], None)
        self.assertEqual(entry["downloads"], 1234)
        self.assertEqual(entry["likes"], 42)
        self.assertEqual(entry["pipeline_tag"], "text-generation")
        self.assertEqual(entry["library_name"], "transformers")
        # the request asks for popular models first
        self.assertEqual(client.last_params["sort"], "downloads")

    def test_missing_safetensors_falls_back_to_model_id(self):
        """No safetensors block → params parsed from the id, size_bytes None."""
        client = _FakeHfClient([{"id": "qwen2.5-7b-instruct", "downloads": 10, "likes": 1}])

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                return await fit_score.get_hf_models()

        models = asyncio.run(run())
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["params_b"], 7.0)
        self.assertIsNone(models[0]["size_bytes"])
        self.assertIsNone(models[0]["pipeline_tag"])

    def test_fetch_failure_returns_empty_list(self):
        """A failed Hub request must never raise — the cookbook degrades to []."""
        async def run():
            with patch(
                "app.services.fit_score.httpx.AsyncClient",
                side_effect=RuntimeError("network down"),
            ):
                return await fit_score.get_hf_models("qwen", 25)

        self.assertEqual(asyncio.run(run()), [])

    def test_cache_hit_skips_second_fetch(self):
        """Within the TTL the same (search, limit) key is served from cache."""
        client = _FakeHfClient([{"id": "org/a", "downloads": 1, "likes": 0}])

        async def run():
            with patch("app.services.fit_score.httpx.AsyncClient", return_value=client):
                await fit_score.get_hf_models("qwen", 10)
                return await fit_score.get_hf_models("qwen", 10)

        self.assertEqual(len(asyncio.run(run())), 1)
        self.assertEqual(client.get_count, 1)  # the second call hit the cache


class BuildHfCookbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        fit_score._hf_cache.clear()

    def test_build_hf_cookbook_scores_entries(self):
        """An 8B model on 6 GB total VRAM gets a real verdict and a need_gb."""
        hw = {"gpu_available": True, "gpus": [
            {"index": 0, "name": "Test GPU", "vram_total_mb": 8192, "vram_free_mb": 6144},
        ], "ram_total_mb": 32768}
        models = [{
            "id": "org/8b-model", "source": "hf", "params_b": 8.0,
            "size_bytes": None, "quant": None, "max_context": None,
        }]

        async def run():
            with patch.object(fit_score, "get_hf_models", new=AsyncMock(return_value=models)):
                return await fit_score.build_hf_cookbook(hw, 8192, "8b", 10)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["search"], "8b")
        entry = result["models"][0]
        self.assertEqual(entry["source"], "hf")
        self.assertIn(entry["verdict"], ("fits_fully", "partial_offload", "wont_fit", "cpu_only", "unknown"))
        self.assertIsInstance(entry["need_gb"], (int, float))
        # size_bytes is stripped from the entry (same as the local cookbook)
        self.assertNotIn("size_bytes", entry)

    def test_build_hf_cookbook_sorts_best_fit_first(self):
        """Entries are ranked with the same verdict order as the local cookbook."""
        hw = {"gpu_available": True, "gpus": [
            {"index": 0, "name": "Test GPU", "vram_total_mb": 8192, "vram_free_mb": 6144},
        ], "ram_total_mb": 32768}
        models = [
            {"id": "org/big", "source": "hf", "params_b": 70.0, "size_bytes": None, "quant": None, "max_context": None},
            {"id": "org/small", "source": "hf", "params_b": 1.0, "size_bytes": None, "quant": None, "max_context": None},
        ]

        async def run():
            with patch.object(fit_score, "get_hf_models", new=AsyncMock(return_value=models)):
                return await fit_score.build_hf_cookbook(hw, 8192)

        result = asyncio.run(run())
        self.assertEqual(result["models"][0]["id"], "org/small")  # fits before wont_fit


class EstimateFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if fit_score is None:
            raise unittest.SkipTest(
                f"app.services.fit_score import failed in this env: {_IMPORT_ERROR}"
            )

    def test_estimate_fit_uses_default_bytes_per_param(self):
        """params_b with no quant is weighted at DEFAULT_BYTES_PER_PARAM."""
        model = {"params_b": 8, "quant": None, "size_bytes": None}
        fit = fit_score.estimate_fit(model, None, None, 8192)
        self.assertEqual(fit["verdict"], "cpu_only")
        kv_gb = 8192 * 128e3 * (8 / 7) / 1e9
        expected_need = round((8 * fit_score.DEFAULT_BYTES_PER_PARAM + kv_gb) * 1.1, 1)
        self.assertEqual(fit["need_gb"], expected_need)

    def test_ram_offload_when_weights_exceed_vram(self):
        """~12 GB weights exceed 8 GB VRAM but fit in 32 GB RAM and the working
        set fits in VRAM + RAM → partial_offload (NOT fits_fully, NOT wont_fit)."""
        model = {"params_b": None, "quant": None, "size_bytes": 12_000_000_000}
        fit = fit_score.estimate_fit(model, 8192, 32768, 8192)
        self.assertEqual(fit["verdict"], "partial_offload")
        # weights (12 GB) > VRAM (8 GB) → the "weights exceed" branch, not KV overflow
        self.assertIn("weights exceed", fit["rationale"])

    def test_wont_fit_when_weights_exceed_combined_vram_and_ram(self):
        """~100 GB weights exceed 8 GB VRAM + 32 GB RAM combined → wont_fit."""
        model = {"params_b": None, "quant": None, "size_bytes": 100_000_000_000}
        fit = fit_score.estimate_fit(model, 8192, 32768, 8192)
        self.assertEqual(fit["verdict"], "wont_fit")


if __name__ == "__main__":
    unittest.main()
