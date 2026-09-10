"""AI-layer unit tests that do not need the FastAPI app."""
from __future__ import annotations

import pytest

from ai.base import PrivacyClass, ProviderCapabilities, ProviderKind
from ai.registry import (
    ProviderAlreadyRegistered,
    ProviderDescriptor,
    ProviderNotFound,
    ProviderRegistry,
    build_default_registry,
)
from ai.router import (
    NoEligibleProviderError,
    ProviderCandidate,
    RouteRequest,
    RoutingError,
    RoutingMode,
    TaskKind,
    route,
)


def cand(name, kind=ProviderKind.STT, privacy=PrivacyClass.LOCAL, healthy=True, **kw):
    return ProviderCandidate(
        name=name,
        kind=kind,
        privacy=privacy,
        healthy=healthy,
        supports_streaming=kw.pop("streaming", True),
        languages=kw.pop("languages", ("fa", "en")),
        latency_hint_ms=kw.pop("latency", 100),
        cost_hint_per_unit=kw.pop("cost", 0.0),
    )


# -- registry ----------------------------------------------------------------


def test_registry_rejects_duplicates_and_missing():
    reg = ProviderRegistry()
    desc = ProviderDescriptor(
        name="a",
        kind=ProviderKind.STT,
        capabilities=ProviderCapabilities(privacy=PrivacyClass.LOCAL),
        factory=lambda cfg: object(),
    )
    reg.register(desc)
    with pytest.raises(ProviderAlreadyRegistered):
        reg.register(desc)
    with pytest.raises(ProviderNotFound):
        reg.get_descriptor(ProviderKind.STT, "missing")


def test_registry_caches_instances_and_configured_predicate():
    reg = ProviderRegistry()
    calls = []

    reg.register(
        ProviderDescriptor(
            name="needs-url",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(privacy=PrivacyClass.CLOUD),
            factory=lambda cfg: calls.append(1) or object(),
            configured=lambda cfg: bool(cfg.get("api_key")),
        )
    )
    assert reg.is_configured(ProviderKind.LLM, "needs-url", {}) is False
    assert reg.is_configured(ProviderKind.LLM, "needs-url", {"api_key": "k"}) is True
    first = reg.create(ProviderKind.LLM, "needs-url")
    second = reg.create(ProviderKind.LLM, "needs-url")
    assert first is second and len(calls) == 1


def test_default_registry_has_phase1_providers():
    reg = build_default_registry()
    names = {(d.kind.value, d.name) for d in reg.descriptors()}
    assert ("stt", "mock") in names
    assert ("llm", "mock") in names
    assert ("llm", "llama-server") in names


# -- router: privacy is a wall, not a preference -------------------------------


def test_privacy_required_forces_local_even_in_cloud_mode():
    decision = route(
        RouteRequest(
            task=TaskKind.TRANSCRIBE_STREAM,
            kind=ProviderKind.STT,
            mode=RoutingMode.CLOUD,
            privacy_required=True,
        ),
        [cand("mock"), cand("deepgram", privacy=PrivacyClass.CLOUD)],
    )
    assert decision.provider == "mock"
    assert decision.privacy_override_applied is True
    assert "deepgram" not in decision.considered  # never even eligible


def test_preferred_provider_wins_when_eligible_and_falls_back_otherwise():
    cands = [cand("mock"), cand("deepgram", privacy=PrivacyClass.CLOUD, latency=50)]
    explicit = route(
        RouteRequest(
            task=TaskKind.TRANSCRIBE_STREAM,
            kind=ProviderKind.STT,
            preferred="deepgram",
        ),
        cands,
    )
    assert explicit.provider == "deepgram"
    assert explicit.fallbacks == ("mock",)

    # preferred violates privacy → ignored (non-strict), local wins
    privacy = route(
        RouteRequest(
            task=TaskKind.TRANSCRIBE_STREAM,
            kind=ProviderKind.STT,
            privacy_required=True,
            preferred="deepgram",
        ),
        cands,
    )
    assert privacy.provider == "mock"
    assert "preferred 'deepgram' unavailable" in privacy.reason


def test_strict_preference_that_violates_privacy_errors_loudly():
    with pytest.raises(RoutingError):
        route(
            RouteRequest(
                task=TaskKind.TRANSCRIBE_STREAM,
                kind=ProviderKind.STT,
                privacy_required=True,
                preferred="deepgram",
                strict_preference=True,
            ),
            [cand("mock"), cand("deepgram", privacy=PrivacyClass.CLOUD)],
        )


def test_health_demotes_to_fallback():
    decision = route(
        RouteRequest(task=TaskKind.TRANSCRIBE_STREAM, kind=ProviderKind.STT, mode=RoutingMode.AUTO),
        [
            cand("fast-but-down", healthy=False),
            cand("slower-but-up", latency=900),
        ],
    )
    assert decision.provider == "slower-but-up"
    assert decision.fallbacks == ("fast-but-down",)


def test_auto_prefers_lower_latency_then_cost_then_name():
    decision = route(
        RouteRequest(task=TaskKind.TRANSCRIBE_STREAM, kind=ProviderKind.STT),
        [
            cand("b", latency=100),
            cand("a", latency=100),
            cand("cheap", latency=200, cost=0.5),
        ],
    )
    # a/b tie on latency; name tie-break → a
    assert decision.provider == "a"


def test_streaming_requirement_filters_non_streaming_providers():
    with pytest.raises(NoEligibleProviderError):
        route(
            RouteRequest(task=TaskKind.TRANSCRIBE_STREAM, kind=ProviderKind.STT),
            [cand("batch-only", streaming=False)],
        )
    ok = route(
        RouteRequest(task=TaskKind.TRANSCRIBE_BATCH, kind=ProviderKind.STT),
        [cand("batch-only", streaming=False)],
    )
    assert ok.provider == "batch-only"


def test_empty_registry_errors():
    with pytest.raises(NoEligibleProviderError):
        route(RouteRequest(task=TaskKind.GENERATE_NOTE, kind=ProviderKind.LLM), [])


def test_llm_task_routes_llm_candidates_only():
    decision = route(
        RouteRequest(task=TaskKind.GENERATE_NOTE, kind=ProviderKind.LLM),
        [
            cand("mock", kind=ProviderKind.STT),
            cand("llama-server", kind=ProviderKind.LLM, latency=1500),
        ],
    )
    assert decision.provider == "llama-server"
    assert decision.kind is ProviderKind.LLM
