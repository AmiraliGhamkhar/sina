"""AI router subpackage: privacy-aware provider selection + health tracking."""

from ai.router.models import (  # noqa: F401
    NoEligibleProviderError,
    ProviderCandidate,
    RouteDecision,
    RouteRequest,
    RoutingError,
    RoutingMode,
    TaskKind,
    route,
)
