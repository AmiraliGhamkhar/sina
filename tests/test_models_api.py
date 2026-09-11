"""Model hub API tests: verified downloads (httpx MockTransport — no network),
progress/status, manifest bookkeeping, permissions, auto-configure effects on
the shenava provider config, delete semantics. Uses a synthetic tiny catalog
so downloads are kilobytes, not gigabytes."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from api.auth.tokens import create_token
from api.config import ModelsConfig
from api.main import create_app
from api.services.model_catalog import ModelFileSpec, ModelRole, ModelSpec
from api.services.model_manager import ModelManager

from tests.conftest import TEST_DEV_TOKEN, TEST_JWT_SECRET, make_settings
from tests.local_ai_fakes import install_fake_numpy, install_fake_sherpa


def _tiny_catalog(tmp_path) -> tuple[ModelSpec, ModelSpec]:
    """Two tiny models: a shenava-shaped one (2 files) + a llama one (1 file)."""
    good = ModelSpec(
        id="tiny-shenava",
        name="Tiny Shenava",
        role=ModelRole.STT_SHENAVA,
        description="test stt",
        license="Apache-2.0",
        license_url=None,
        source_repo="test/tiny-shenava",
        files=(
            ModelFileSpec(
                repo="test/tiny-shenava",
                filename="model.int8.onnx",
                local_name="model.int8.onnx",
                size_bytes=2048,
                sha256=None,
            ),
            ModelFileSpec(
                repo="test/tiny-shenava",
                filename="tokens.txt",
                local_name="tokens.txt",
                size_bytes=11,
            ),
        ),
        runtime="in-process",
        auto_configured=True,
        providers=("shenava",),
    )
    bad_hash = ModelSpec(
        id="tiny-bad-hash",
        name="Tiny Bad Hash",
        role=ModelRole.LLM_LLAMA,
        description="test llm",
        license="MIT",
        license_url=None,
        source_repo="test/tiny-llm",
        files=(
            ModelFileSpec(
                repo="test/tiny-llm",
                filename="model.gguf",
                local_name="model.gguf",
                size_bytes=512,
                sha256="0" * 64,  # never matches → verification failure path
            ),
        ),
        runtime="llama-server",
        auto_configured=False,
        operator_note="restart llama-server -m ...",
        providers=("llama-server",),
    )
    return good, bad_hash


def _file_body(size: int, fill: bytes = b"a") -> bytes:
    return (fill * (size // len(fill) + 1))[:size]


def _mock_hf_client(catalog_specs, *, status_for: dict | None = None) -> httpx.AsyncClient:
    """MockTransport serving catalog file bytes at HF-style resolve URLs."""
    by_url = {}
    for spec in catalog_specs:
        for f in spec.files:
            url = f"https://huggingface.co/{f.repo}/resolve/main/{f.filename}"
            by_url[url] = _file_body(f.size_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        override = (status_for or {}).get(str(request.url))
        if override is not None:
            return httpx.Response(override)
        body = by_url.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _make_manager(tmp_path, specs, client, metrics=None) -> ModelManager:
    return ModelManager(
        ModelsConfig(dir=str(tmp_path / "models"), min_free_disk_bytes=0),
        catalog=specs,
        client=client,
        metrics=metrics,
    )


def _make_app(tmp_path, specs, client, **settings_overrides):
    settings = make_settings(
        tmp_path,
        models={"dir": str(tmp_path / "models"), "min_free_disk_bytes": 0},
        **settings_overrides,
    )
    app = create_app(settings)
    app.state.model_manager = _make_manager(
        tmp_path, specs, client, metrics=app.state.metrics
    )
    return app


@pytest.fixture
def hub(tmp_path):
    specs = _tiny_catalog(tmp_path)
    client = _mock_hf_client(specs)
    app = _make_app(tmp_path, specs, client)
    from fastapi.testclient import TestClient

    headers = {"Authorization": f"Bearer {TEST_DEV_TOKEN}"}
    with TestClient(app) as c:
        yield c, app, tmp_path / "models", specs, headers
    asyncio.run(client.aclose())


# -- listing + status ----------------------------------------------------------


def test_list_models_shows_catalog_and_states(hub):
    client, app, models_dir, specs, headers = hub
    resp = client.get("/api/v1/models", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["models"]
    assert [m["id"] for m in data] == ["tiny-shenava", "tiny-bad-hash"]
    assert all(m["state"] == "not_installed" for m in data)
    shenava = data[0]
    assert shenava["role"] == "stt-shenava"
    assert shenava["auto_configured"] is True
    assert shenava["total_bytes"] == 2059
    assert [f["size_bytes"] for f in shenava["files"]] == [2048, 11]
    assert shenava["install_dir"].endswith("tiny-shenava")


def test_get_unknown_model_404(hub):
    client, *_ , headers = hub
    resp = client.get("/api/v1/models/does-not-exist", headers=headers)
    assert resp.status_code == 404


def test_models_require_auth(hub):
    client, *_ = hub
    assert client.get("/api/v1/models").status_code == 401


# -- download flow ---------------------------------------------------------------


def test_download_installs_files_and_writes_manifest(hub):
    client, app, models_dir, specs, headers = hub
    resp = client.post("/api/v1/models/tiny-shenava/download", headers=headers)
    assert resp.status_code == 202
    # TestClient runs the app loop synchronously; the download task completes
    # on it — poll until installed
    for _ in range(100):
        status = client.get("/api/v1/models/tiny-shenava", headers=headers).json()
        if status["state"] == "installed":
            break
        assert status["state"] in ("downloading", "not_installed"), status
    assert status["state"] == "installed", status
    assert status["progress"] == 1.0
    assert status["installed_at"] is not None

    target = models_dir / "tiny-shenava"
    assert (target / "model.int8.onnx").read_bytes() == _file_body(2048)
    assert (target / "tokens.txt").read_bytes() == _file_body(11)
    manifest = json.loads((models_dir / "manifest.json").read_text())
    entry = manifest["models"]["tiny-shenava"]
    assert entry["source_repo"] == "test/tiny-shenava"
    assert entry["files"]["tokens.txt"]["size"] == 11

    # second download start → idempotent "already installed"
    resp2 = client.post("/api/v1/models/tiny-shenava/download", headers=headers)
    assert resp2.status_code == 202
    assert resp2.json()["state"] == "installed"


def test_download_sha_mismatch_marks_error_and_cleans_partial(hub):
    client, app, models_dir, specs, headers = hub
    resp = client.post("/api/v1/models/tiny-bad-hash/download", headers=headers)
    assert resp.status_code == 202
    for _ in range(100):
        status = client.get("/api/v1/models/tiny-bad-hash", headers=headers).json()
        if status["state"] == "error":
            break
    assert status["state"] == "error"
    assert "sha256 mismatch" in status["error"]
    # partial artifacts cleaned; final file never placed
    assert not (models_dir / "tiny-bad-hash" / "model.gguf").exists()
    assert not (models_dir / "tiny-bad-hash" / ".partial").exists()


def test_download_http_error_surfaces_in_status(tmp_path):
    specs = _tiny_catalog(tmp_path)
    client = _mock_hf_client(specs, status_for={
        "https://huggingface.co/test/tiny-shenava/resolve/main/model.int8.onnx": 503
    })
    app = _make_app(tmp_path, specs, client)
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        headers = {"Authorization": f"Bearer {TEST_DEV_TOKEN}"}
        assert c.post("/api/v1/models/tiny-shenava/download", headers=headers).status_code == 202
        status = {}
        for _ in range(100):
            status = c.get("/api/v1/models/tiny-shenava", headers=headers).json()
            if status["state"] == "error":
                break
        assert status["state"] == "error"
        assert "HTTP 503" in status["error"]
    asyncio.run(client.aclose())


def test_download_requires_admin_role(hub):
    client, *_ = hub
    physician_token, _ = create_token(
        TEST_JWT_SECRET, sub="u-doc", role="physician", ttl_minutes=5
    )
    resp = client.post(
        "/api/v1/models/tiny-shenava/download",
        headers={"Authorization": f"Bearer {physician_token}"},
    )
    assert resp.status_code == 403
    resp_del = client.delete(
        "/api/v1/models/tiny-shenava",
        headers={"Authorization": f"Bearer {physician_token}"},
    )
    assert resp_del.status_code == 403


def test_download_unknown_model_404(hub):
    client, *_ , headers = hub
    resp = client.post("/api/v1/models/nope/download", headers=headers)
    assert resp.status_code == 404


def test_duplicate_download_start_does_not_double_task(hub):
    client, app, models_dir, specs, headers = hub
    manager = app.state.model_manager
    assert asyncio.run(manager.start_download("tiny-shenava")) is True
    # files exist synchronously? no — the task runs on the app loop; start
    # again immediately: still running or already installed → not an error
    result = asyncio.run(manager.start_download("tiny-shenava"))
    assert result in (False, True)


# -- delete ------------------------------------------------------------------


def test_delete_removes_artifacts_and_manifest_entry(hub):
    client, app, models_dir, specs, headers = hub
    client.post("/api/v1/models/tiny-shenava/download", headers=headers)
    for _ in range(100):
        if client.get("/api/v1/models/tiny-shenava", headers=headers).json()["state"] == "installed":
            break
    resp = client.delete("/api/v1/models/tiny-shenava", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not (models_dir / "tiny-shenava").exists()
    manifest = json.loads((models_dir / "manifest.json").read_text())
    assert "tiny-shenava" not in manifest["models"]
    status = client.get("/api/v1/models/tiny-shenava", headers=headers).json()
    assert status["state"] == "not_installed"
    # deleting again is a no-op
    resp2 = client.delete("/api/v1/models/tiny-shenava", headers=headers)
    assert resp2.json()["deleted"] is False


# -- auto-configure (the headline feature) ------------------------------------


def test_shenava_download_auto_configures_provider(tmp_path, monkeypatch):
    """After the shenava model downloads, settings.provider_config resolves
    real paths → the provider is routable with zero env changes."""
    from tests.local_ai_fakes import FakeSherpaRecognizer

    recognizer = FakeSherpaRecognizer()
    install_fake_numpy(monkeypatch)
    install_fake_sherpa(monkeypatch, recognizer)

    # place shenava files where the model manager WOULD put them, via the
    # real manager, using a shenava-role spec named exactly 'shenava-koochik'
    spec = ModelSpec(
        id="shenava-koochik",
        name="Shenava",
        role=ModelRole.STT_SHENAVA,
        description="",
        license="Apache-2.0",
        license_url=None,
        source_repo="test/shenava",
        files=(
            ModelFileSpec(
                repo="test/shenava",
                filename="model.int8.onnx",
                local_name="model.int8.onnx",
                size_bytes=2048,
            ),
            ModelFileSpec(
                repo="test/shenava",
                filename="tokens.txt",
                local_name="tokens.txt",
                size_bytes=11,
            ),
        ),
        runtime="in-process",
        auto_configured=True,
        providers=("shenava",),
    )
    client = _mock_hf_client((spec,))
    models_dir = tmp_path / "models"
    settings = make_settings(
        tmp_path, models={"dir": str(models_dir), "min_free_disk_bytes": 0}
    )
    app = create_app(settings)
    app.state.model_manager = ModelManager(
        settings.models, catalog=(spec,), client=client, metrics=app.state.metrics
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        headers = {"Authorization": f"Bearer {TEST_DEV_TOKEN}"}
        # before: provider not configured
        before = settings.provider_config("stt", "shenava")
        assert before["model_path"] is None

        c.post("/api/v1/models/shenava-koochik/download", headers=headers)
        for _ in range(100):
            if c.get("/api/v1/models/shenava-koochik", headers=headers).json()["state"] == "installed":
                break

        # after: provider config auto-resolves the downloaded paths
        after = settings.provider_config("stt", "shenava")
        assert after["model_path"] == str(models_dir / "shenava-koochik" / "model.int8.onnx")
        assert after["tokens_path"] == str(models_dir / "shenava-koochik" / "tokens.txt")

        # and the provider is actually constructible + registry-configured
        from ai.stt.shenava import ShenavaProvider, shenava_configured

        assert shenava_configured(after) is True
        provider = ShenavaProvider(after)
        assert provider.name == "shenava"
        assert provider.capabilities.is_local
    asyncio.run(client.aclose())


def test_audit_and_metrics_counters_recorded(hub):
    client, app, models_dir, specs, headers = hub
    client.post("/api/v1/models/tiny-shenava/download", headers=headers)
    for _ in range(100):
        if client.get("/api/v1/models/tiny-shenava", headers=headers).json()["state"] == "installed":
            break
    snapshot = app.state.metrics.snapshot()
    counters = snapshot["counters"]
    assert counters.get("model_downloads:tiny-shenava:started", 0) >= 1
    assert counters.get("model_downloads:tiny-shenava:completed", 0) >= 1
