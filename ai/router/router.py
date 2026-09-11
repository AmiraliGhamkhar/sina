"""AI Router — provider selection with privacy-first rules.

The router is a pure decision function over a candidate list; the backend
supplies candidates from :mod:`ai.registry` plus live health from
:class:`ai.router.health.HealthTracker`. Keeping the policy pure (no DB, no
network) mirrors Multi-Model-Gateway's ``resolve_role`` seam and makes the
safety-critical privacy rules trivially testable.

Routing precedence (fixed — do not reorder):
1. ``privacy_required=True`` → LOCAL candidates only (hard constraint).
2. An explicit client-requested provider (validated against the registry).
3. Mode policy (LOCAL | CLOUD | HYBRID | AUTO).
4. Health (unhealthy demoted), then latency, then cost, then name.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ai.base import PrivacyClass, ProviderKind


class RoutingMode(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"
    AUTO = "auto"


class TaskKind(str, Enum):
    TRANSCRIBE_STREAM = "transcribe_stream"
    TRANSCRIBE_BATCH = "transcribe_batch"
    GENERATE_NOTE = "generate_note"
    NORMALIZE_TERMS = "normalize_terms"


@dataclass(frozen=True)
class RouteRequest:
    task: TaskKind
    kind: ProviderKind
    mode: RoutingMode = RoutingMode.AUTO
    #: True for encounters marked private (or global privacy policy). This is
    #: an override, not a hint: it forces PrivacyClass.LOCAL regardless of
    #: ``mode`` or ``preferred``.
    privacy_required: bool = False
    #: provider name explicitly requested by the user/API; must exist in the
    #: candidate set or ``strict_preference`` decides whether we error out.
    preferred: str | None = None
    strict_preference: bool = False
    language: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ProviderCandidate:
    """One routable option, flattened for pure scoring."""

    name: str
    kind: ProviderKind
    privacy: PrivacyClass
    healthy: bool = True
    supports_streaming: bool = True
    supports_batch: bool = True
    languages: tuple[str, ...] = ("*",)
    latency_hint_ms: int = 500
    cost_hint_per_unit: float = 0.0


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    kind: ProviderKind
    #: ordered provider names to try if the primary fails at runtime
    fallbacks: tuple[str, ...] = ()
    reason: str = ""
    privacy_override_applied: bool = False
    #: full ordered candidate names, recorded for auditing
    considered: tuple[str, ...] = ()


class RoutingError(Exception):
    code = "ROUTING_ERROR"


class NoEligibleProviderError(RoutingError):
    def __init__(self, request: RouteRequest, considered: Sequence[ProviderCandidate]) -> None:
        names = ", ".join(c.name for c in considered) or "<none registered>"
        super().__init__(
            f"no eligible provider for task '{request.task.value}' "
            f"(mode={request.mode.value}, privacy={request.privacy_required}); "
            f"candidates considered: {names}"
        )


def _capable(candidate: ProviderCandidate, request: RouteRequest) -> bool:
    if candidate.kind is not request.kind:
        return False
    if request.task is TaskKind.TRANSCRIBE_STREAM and not candidate.supports_streaming:
        return False
    if request.task is TaskKind.TRANSCRIBE_BATCH and not candidate.supports_batch:
        return False
    if request.language and "*" not in candidate.languages:
        # "fa-en" mixed is satisfied by any provider claiming fa or en
        if request.language not in candidate.languages and not (
            request.language in ("fa-en", "fa-en-mixed")
            and {"fa", "en"} & set(candidate.languages)
        ):
            return False
    return True


def _privacy_allowed(candidate: ProviderCandidate, request: RouteRequest) -> bool:
    if request.privacy_required:
        return candidate.privacy is PrivacyClass.LOCAL
    return True


def _mode_allowed(candidate: ProviderCandidate, request: RouteRequest) -> bool:
    if request.mode is RoutingMode.LOCAL:
        return candidate.privacy is PrivacyClass.LOCAL
    if request.mode is RoutingMode.CLOUD:
        return candidate.privacy is PrivacyClass.CLOUD
    return True  # HYBRID / AUTO allow both; ordering encodes preference


def _rank_key(candidate: ProviderCandidate, request: RouteRequest) -> tuple:
    privacy_pref = 0 if candidate.privacy is PrivacyClass.LOCAL else 1
    if request.mode is RoutingMode.CLOUD:
        privacy_pref = 0 if candidate.privacy is PrivacyClass.CLOUD else 1
    return (
        0 if candidate.healthy else 1,  # health dominates
        privacy_pref,  # local-first by default; privacy also forces this
        candidate.latency_hint_ms,
        candidate.cost_hint_per_unit,
        candidate.name,  # stable tie-break
    )


def route(
    request: RouteRequest, candidates: Sequence[ProviderCandidate]
) -> RouteDecision:
    """Pure selection. Raises :class:`NoEligibleProviderError` when nothing is
    routable — callers surface this as HTTP 503 with code ``NO_PROVIDER`` and
    must never fall back to a privacy-violating provider.
    """
    # Privacy overrides convenience (spec §11): when privacy is required the
    # mode filter is skipped entirely — LOCAL is the only admissible class.
    def _mode_ok(c: ProviderCandidate) -> bool:
        if request.privacy_required:
            return True
        return _mode_allowed(c, request)

    eligible = [
        c
        for c in candidates
        if _capable(c, request) and _privacy_allowed(c, request) and _mode_ok(c)
    ]
    privacy_override = request.privacy_required and request.mode not in (
        RoutingMode.LOCAL,
    )

    # 2. explicit preference wins if it survives the eligibility filters
    if request.preferred:
        match = next((c for c in eligible if c.name == request.preferred), None)
        if match is not None:
            rest = sorted(
                (c for c in eligible if c.name != match.name),
                key=lambda c: _rank_key(c, request),
            )
            return RouteDecision(
                provider=match.name,
                kind=request.kind,
                fallbacks=tuple(c.name for c in rest),
                reason=f"explicit preference '{match.name}'",
                privacy_override_applied=privacy_override,
                considered=tuple(c.name for c in eligible),
            )
        if request.strict_preference:
            raise RoutingError(
                f"requested provider '{request.preferred}' is not eligible "
                f"(mode={request.mode.value}, privacy={request.privacy_required})"
            )
        # non-strict: fall through with a note in the reason

    if not eligible:
        raise NoEligibleProviderError(request, candidates)

    ordered = sorted(eligible, key=lambda c: _rank_key(c, request))
    primary, *rest = ordered
    reason_bits = [f"mode={request.mode.value}"]
    if privacy_override:
        reason_bits.append("privacy override → local only")
    if request.preferred:
        reason_bits.append(f"preferred '{request.preferred}' unavailable")
    return RouteDecision(
        provider=primary.name,
        kind=request.kind,
        fallbacks=tuple(c.name for c in rest),
        reason="; ".join(reason_bits),
        privacy_override_applied=privacy_override,
        considered=tuple(c.name for c in ordered),
    )
