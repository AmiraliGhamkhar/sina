"""Shenava STT adapter tests — all against the fake sherpa-onnx runtime
(tests/local_ai_fakes.py): streaming segmentation, batch decode, config
auto-resolution, registry wiring, error paths. No network, no model files,
no optional wheels required (CI installs neither)."""
from __future__ import annotations

import asyncio

import pytest

from ai.base import PrivacyClass, ProviderError, STTRequest
from ai.registry import build_default_registry
from ai.stt.shenava import (
    ShenavaProvider,
    resolve_shenava_paths,
    shenava_configured,
)
from tests.local_ai_fakes import (
    FakeSherpaRecognizer,
    install_fake_numpy,
    install_fake_sherpa,
    install_missing_local_ai,
)


def _loud_pcm(ms: int = 100, amp: int = 20000) -> bytes:
    n = 16 * ms  # samples at 16 kHz
    return b"".join(
        (amp if i % 2 == 0 else -amp).to_bytes(2, "little", signed=True) for i in range(n)
    )


def _silence_pcm(ms: int = 100) -> bytes:
    return b"\x00\x00" * (16 * ms)


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    recognizer = FakeSherpaRecognizer()
    install_fake_numpy(monkeypatch)
    install_fake_sherpa(monkeypatch, recognizer)
    model = tmp_path / "model.int8.onnx"
    model.write_bytes(b"fake-onnx")
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("token-id-lines\n")
    cfg = {
        "model_path": str(model),
        "tokens_path": str(tokens),
        "vad_silence_ms": 300,
        "vad_interim_ms": 300,
        "vad_pre_roll_ms": 100,
        "vad_threshold": 0.02,
        "max_segment_ms": 30000,
    }
    return recognizer, cfg


# -- construction / registry ---------------------------------------------------


def test_registry_registers_shenava_as_local_streaming():
    registry = build_default_registry()
    names = [x.name for x in registry.descriptors()]
    assert "shenava" in names
    from ai.base import ProviderKind

    desc = registry.get_descriptor(ProviderKind.STT, "shenava")
    assert desc.capabilities.privacy is PrivacyClass.LOCAL
    assert desc.capabilities.supports_streaming is True
    assert desc.capabilities.languages == ("fa",)


def test_configured_predicate_requires_both_paths():
    assert shenava_configured({}) is False
    assert shenava_configured({"model_path": "m.onnx"}) is False
    assert shenava_configured({"model_path": "m.onnx", "tokens_path": "t.txt"}) is True


def test_missing_extra_raises_provider_unavailable(monkeypatch, fake_runtime, tmp_path):
    install_missing_local_ai(monkeypatch)
    _, cfg = fake_runtime
    with pytest.raises(ProviderError, match="local-ai"):
        ShenavaProvider(cfg)


def test_missing_files_raise_config_error(fake_runtime, tmp_path):
    _, cfg = fake_runtime
    bad = dict(cfg, model_path=str(tmp_path / "nope.onnx"))
    with pytest.raises(ProviderError, match="not found"):
        ShenavaProvider(bad)


def test_ctor_passes_neMo_loader_args(fake_runtime):
    recognizer, cfg = fake_runtime
    provider = ShenavaProvider(cfg)
    assert recognizer.ctor_kwargs["model"].endswith("model.int8.onnx")
    assert recognizer.ctor_kwargs["tokens"].endswith("tokens.txt")
    assert recognizer.ctor_kwargs["feature_dim"] == 80
    assert recognizer.ctor_kwargs["sample_rate"] == 16000
    assert provider.capabilities.extra["engine"] == "sherpa-onnx"


# -- path auto-resolution (model manager convention) --------------------------


def test_resolve_paths_explicit_wins(tmp_path):
    m, t = tmp_path / "m.onnx", tmp_path / "t.txt"
    m.write_bytes(b"x")
    t.write_bytes(b"x")
    assert resolve_shenava_paths(str(m), str(t), str(tmp_path)) == (str(m), str(t))


def test_resolve_paths_auto_from_models_dir(tmp_path):
    base = tmp_path / "shenava-koochik"
    base.mkdir()
    (base / "model.int8.onnx").write_bytes(b"x")
    (base / "tokens.txt").write_bytes(b"x")
    model, tokens = resolve_shenava_paths(None, None, str(tmp_path))
    assert model == str(base / "model.int8.onnx")
    assert tokens == str(base / "tokens.txt")


def test_resolve_paths_missing_model_dir_returns_none(tmp_path):
    assert resolve_shenava_paths(None, None, str(tmp_path)) == (None, None)


def test_provider_config_projection_auto_resolves(tmp_path):
    from api.config import Settings

    base = tmp_path / "shenava-koochik"
    base.mkdir()
    (base / "model.int8.onnx").write_bytes(b"x")
    (base / "tokens.txt").write_bytes(b"x")
    settings = Settings(
        server={"env": "dev"},
        audit={"enabled": False},
        models={"dir": str(tmp_path)},
    )
    cfg = settings.provider_config("stt", "shenava")
    assert cfg["model_path"] == str(base / "model.int8.onnx")
    assert cfg["tokens_path"] == str(base / "tokens.txt")
    assert shenava_configured(cfg) is True


def test_provider_config_projection_unset_when_not_downloaded(tmp_path):
    from api.config import Settings

    settings = Settings(server={"env": "dev"}, audit={"enabled": False}, models={"dir": str(tmp_path)})
    cfg = settings.provider_config("stt", "shenava")
    assert cfg["model_path"] is None
    assert shenava_configured(cfg) is False


# -- streaming ----------------------------------------------------------------


def test_stream_interim_then_final_with_reset(fake_runtime):
    asyncio.run(_test_stream_interim_then_final_with_reset_async(fake_runtime))


async def _test_stream_interim_then_final_with_reset_async(fake_runtime):
    recognizer, cfg = fake_runtime
    recognizer.result_script = ["سلام", "سلام دنیا", "سلام دنیا"]  # interim, interim, final
    provider = ShenavaProvider(cfg)

    async def chunks():
        for _ in range(4):  # 400 ms speech
            yield _loud_pcm(100)
        for _ in range(6):  # 600 ms silence ≥ silence_ms 300 → final
            yield _silence_pcm(100)

    segments = await _collect(provider.stream(chunks()))

    finals = [s for s in segments if s.is_final]
    interims = [s for s in segments if not s.is_final]
    assert interims, "expected interim hypotheses during ongoing speech"
    assert all(s.language == "fa" for s in segments)
    assert len(finals) == 1
    assert finals[0].text == "سلام دنیا"
    assert finals[0].is_final is True
    # decoder state reset exactly once for the single utterance
    assert recognizer.resets == 1
    # audio reached the sherpa stream in sample-sized pieces
    stream = recognizer.created_streams[0]
    assert len(stream.waveform_chunks) == 10
    assert all(n == 1600 for n in stream.waveform_chunks)


def test_stream_second_utterance_after_reset(fake_runtime):
    asyncio.run(_test_stream_second_utterance_after_reset_async(fake_runtime))


async def _test_stream_second_utterance_after_reset_async(fake_runtime):
    recognizer, cfg = fake_runtime
    recognizer.result_script = ["یک", "یک", "دو", "دو"]
    provider = ShenavaProvider(cfg)

    async def chunks():
        for _ in range(4):
            yield _loud_pcm(100)
        for _ in range(6):
            yield _silence_pcm(100)
        for _ in range(4):
            yield _loud_pcm(100)
        for _ in range(6):
            yield _silence_pcm(100)

    segments = await _collect(provider.stream(chunks()))
    finals = [s for s in segments if s.is_final]
    assert len(finals) == 2
    assert [f.text for f in finals] == ["یک", "دو"]
    assert recognizer.resets == 2


def test_stream_silence_only_emits_nothing(fake_runtime):
    asyncio.run(_test_stream_silence_only_emits_nothing_async(fake_runtime))


async def _test_stream_silence_only_emits_nothing_async(fake_runtime):
    recognizer, cfg = fake_runtime
    provider = ShenavaProvider(cfg)

    async def chunks():
        for _ in range(10):
            yield _silence_pcm(100)

    segments = await _collect(provider.stream(chunks()))
    assert segments == []
    assert recognizer.resets == 0


def test_stream_max_segment_force_cut(fake_runtime):
    asyncio.run(_test_stream_max_segment_force_cut_async(fake_runtime))


async def _test_stream_max_segment_force_cut_async(fake_runtime):
    """Runaway speech force-cuts at max_segment_ms (VAD contract): 1 s of
    continuous speech with a 300 ms cap yields deterministic 300 ms segments."""
    recognizer, cfg = fake_runtime
    cfg = dict(cfg, max_segment_ms=300, vad_interim_ms=10_000)
    recognizer.result_script = ["بلند"] * 40
    provider = ShenavaProvider(cfg)

    async def chunks():
        for _ in range(10):  # 1 s of continuous speech
            yield _loud_pcm(100)

    segments = await _collect(provider.stream(chunks()))
    finals = [s for s in segments if s.is_final]
    # cuts at 300/600/900 ms (force) + flush of the open tail at 1000 ms
    assert [f.end_ms for f in finals] == [300, 600, 900, 1000]
    assert recognizer.resets == len(finals)


# -- batch ------------------------------------------------------------------


def test_transcribe_batch_single_final_segment(fake_runtime):
    asyncio.run(_test_transcribe_batch_single_final_segment_async(fake_runtime))


async def _test_transcribe_batch_single_final_segment_async(fake_runtime):
    recognizer, cfg = fake_runtime
    recognizer.result_script = ["سلام این یک تست است"]
    provider = ShenavaProvider(cfg)
    segments = await provider.transcribe(
        STTRequest(audio=_loud_pcm(500), encoding="pcm_s16le")
    )
    assert len(segments) == 1
    seg = segments[0]
    assert seg.text == "سلام این یک تست است"
    assert seg.is_final is True
    assert seg.end_ms == 500
    stream = recognizer.created_streams[0]
    assert stream.finished is True  # input_finished() flushed the stream


def test_transcribe_empty_audio_returns_empty(fake_runtime):
    asyncio.run(_test_transcribe_empty_audio_returns_empty_async(fake_runtime))


async def _test_transcribe_empty_audio_returns_empty_async(fake_runtime):
    _, cfg = fake_runtime
    provider = ShenavaProvider(cfg)
    assert await provider.transcribe(STTRequest(audio=b"")) == []
