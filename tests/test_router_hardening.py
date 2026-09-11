"""Phase 5 router hardening units: EWMA-ranked latency, budget soft-stop,
privacy-pure fallback chains, tracker absorb semantics. Pure functions — no
app, no network (that's what makes these safety properties cheap to test)."""
from __future__ import annotations

from ai.base import PrivacyClass, ProviderKind
from ai.router import ProviderCandidate, RouteRequest, RoutingMode, TaskKind, route
from ai.router.health import HealthTracker


def _cand(name: str, *, privacy=PrivacyClass.LOCAL, latency_ms=None, hint=500) -> ProviderCandidate:
    return ProviderCandidate(
        name=name,
        kind=ProviderKind.STT,
        privacy=privacy,
        latency_ms=latency_ms,
        latency_hint_ms=hint,
        supports_batch=True,
    )


_REQ = RouteRequest(task=TaskKind.TRANSCRIBE_STREAM, kind=ProviderKind.STT)


def test_observed_ewma_latency_outranks_static_hint():
    # "slowhint" lies with a small hint but has bad observed latency; the
    # router must trust measurement over marketing.
    decision = route(
        _REQ,
        [
            _cand("liar", hint=10, latency_ms=4000),
            _cand("truthy", hint=300, latency_ms=300),
        ],
    )
    assert decision.provider == "truthy"
    assert decision.fallbacks == ("liar",)


def test_no_observed_latency_falls_back_to_hints():
    decision = route(_REQ, [_cand("a", hint=700), _cand("b", hint=200)])
    assert decision.provider == "b"


def test_budget_soft_stop_excludes_cloud_and_says_so():
    req = RouteRequest(
        task=TaskKind.GENERATE_NOTE,
        kind=ProviderKind.LLM,
        mode=RoutingMode.AUTO,
        cloud_excluded=True,
        preferred="cloudllm",
    )
    decision = route(
        req,
        [
            ProviderCandidate(name="cloudllm", kind=ProviderKind.LLM, privacy=PrivacyClass.CLOUD),
            ProviderCandidate(name="localllm", kind=ProviderKind.LLM, privacy=PrivacyClass.LOCAL),
        ],
    )
    # explicit preference for a cloud provider must NOT defeat the guard
    assert decision.provider == "localllm"
    assert decision.budget_soft_stop is True
    assert "soft stop" in decision.reason


def test_budget_exhausted_with_no_local_is_a_loud_refusal():
    req = RouteRequest(
        task=TaskKind.GENERATE_NOTE,
        kind=ProviderKind.LLM,
        cloud_excluded=True,
    )
    from ai.router import NoEligibleProviderError

    try:
        route(req, [ProviderCandidate(name="c", kind=ProviderKind.LLM, privacy=PrivacyClass.CLOUD)])
    except NoEligibleProviderError as exc:
        assert "budget_soft_stop" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected refusal")


def test_fallback_chain_never_crosses_the_privacy_wall():
    candidates = [
        _cand("local_a", hint=100),
        _cand("cloud_b", privacy=PrivacyClass.CLOUD, hint=10),
        _cand("cloud_c", privacy=PrivacyClass.CLOUD, hint=20),
    ]
    req = RouteRequest(
        task=TaskKind.TRANSCRIBE_STREAM,
        kind=ProviderKind.STT,
        privacy_required=True,
        mode=RoutingMode.AUTO,
    )
    decision = route(req, candidates)
    assert decision.provider == "local_a"
    # the runtime fallback chain is built from decision.fallbacks — so it is
    # privacy-safe BY CONSTRUCTION, not by a later filter
    assert "cloud_b" not in decision.fallbacks
    assert "cloud_c" not in decision.fallbacks
    assert decision.considered == ("local_a",)


def test_health_demotion_changes_ranking():
    tracker = HealthTracker(failure_threshold=2, cooldown_s=1000)
    tracker.record_failure("stt:fast")
    tracker.record_failure("stt:fast")  # demoted
    tracker.record_success("stt:slow", latency_ms=900)
    candidates = [
        ProviderCandidate(
            name="fast",
            kind=ProviderKind.STT,
            privacy=PrivacyClass.LOCAL,
            healthy=tracker.is_healthy("stt:fast"),
            latency_ms=tracker.latency_map().get("stt:fast"),
            latency_hint_ms=50,
        ),
        ProviderCandidate(
            name="slow",
            kind=ProviderKind.STT,
            privacy=PrivacyClass.LOCAL,
            healthy=tracker.is_healthy("stt:slow"),
            latency_ms=tracker.latency_map().get("stt:slow"),
            latency_hint_ms=5000,
        ),
    ]
    decision = route(_REQ, candidates)
    assert decision.provider == "slow"
    assert decision.fallbacks == ("fast",)  # still eligible as a last resort


def test_ewma_smoothing_and_absorb():
    tracker = HealthTracker()
    tracker.record_success("stt:x", latency_ms=1000)
    tracker.record_success("stt:x", latency_ms=100)
    ewma = tracker.snapshot()["stt:x"]["latency_ewma_ms"]
    # one bad sample must not dominate: 0.3*100 + 0.7*1000 = 730
    assert abs(ewma - 730) < 1
    # a peer worker that saw failures must demote us even if we are pristine
    peer = {
        "healthy": False,
        "consecutive_failures": 3,
        "total_failures": 5,
        "total_successes": 1,
        "latency_ewma_ms": 800,
    }
    tracker2 = HealthTracker(failure_threshold=99)  # never demotes on its own
    tracker2.record_success("stt:x", latency_ms=200)
    tracker2.absorb("stt:x", peer)
    snap = tracker2.snapshot()["stt:x"]
    assert snap["healthy"] is False  # ANDed demotion
    assert snap["consecutive_failures"] == 3
    assert snap["total_failures"] == 5
    assert snap["latency_ewma_ms"] == 500  # (200 + 800) / 2
