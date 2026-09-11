"""Prometheus `/metrics` endpoint (Phase 8, spec §18 observability).

Dependency-free text exposition (format 0.0.4) over the in-process
``Metrics`` registry — no prometheus_client dependency needed for the
scrape path (engineering rule §19: avoid unnecessary dependencies).

Mapping rules (stable, documented for dashboards):
- counter ``http.status.<code>``      → ``medicalscribe_http_responses_total{status}``
- counter ``llm_requests:<provider>`` → ``medicalscribe_llm_requests_total{provider}``
- counter ``llm_errors:<attempt>``    → ``medicalscribe_llm_errors_total{attempt}``
- counter ``llm_tokens:<p>:<field>``  → ``medicalscribe_llm_tokens_total{provider,field}``
- counter ``voice_command:<id>``      → ``medicalscribe_voice_commands_total{command}``
- counter ``model_downloads:<id>:<status>`` → ``medicalscribe_model_downloads_total{model,status}``
- other counters                      → ``medicalscribe_<sanitized>_total``
- latency ``http.<route template>``   → ``medicalscribe_http_request_duration_seconds``
                                        summary{route} (quantiles from the
                                        bounded ring; count/sum exact, in s)
- other latency series                → ``medicalscribe_<sanitized>_seconds`` summary
- gauges                              → ``medicalscribe_<sanitized>``

App-state extras: build_info, db_mode, cost ledger, provider health.

The endpoint is unauthenticated BY DESIGN: it is scraped on the internal
network and the nginx edge deliberately does not proxy `/metrics`
(infrastructure/nginx/nginx.conf). It never contains transcripts, user
names, or secrets — counters and route templates only.
"""
from __future__ import annotations

import re
import sys

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from api.version import API_VERSION, PHASE

router = APIRouter(tags=["metrics"])

_INVALID = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize(name: str) -> str:
    cleaned = _INVALID.sub("_", name.strip("_"))
    return cleaned or "unknown"


def _counter_series(name: str) -> tuple[str, dict[str, str]] | None:
    """Split known structured counters into metric + labels (None = generic)."""
    if name.startswith("http.status."):
        return "medicalscribe_http_responses_total", {"status": name.split(".", 2)[2]}
    if name.startswith("llm_requests:"):
        return "medicalscribe_llm_requests_total", {"provider": name.split(":", 1)[1]}
    if name.startswith("llm_errors:"):
        return "medicalscribe_llm_errors_total", {"attempt": name.split(":", 1)[1]}
    if name.startswith("llm_tokens:"):
        parts = name.split(":")
        if len(parts) == 3:
            return "medicalscribe_llm_tokens_total", {
                "provider": parts[1], "field": parts[2]
            }
        return None
    if name.startswith("voice_command:"):
        return "medicalscribe_voice_commands_total", {"command": name.split(":", 1)[1]}
    if name.startswith("model_downloads:"):
        parts = name.split(":")
        if len(parts) == 3:
            return "medicalscribe_model_downloads_total", {
                "model": parts[1],
                "status": parts[2],
            }
        return None
    return None


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def _summary(name: str, labels: dict[str, str], stats: dict) -> list[str]:
    """Prometheus summary: quantiles + exact count/sum (converted to seconds)."""
    lines = [
        f"# TYPE {name} summary",
        f"{name}{_labels(labels)} Sum={stats.get('sum_ms', 0) / 1000:.4f} Count={stats.get('count', 0)}",
    ]
    return lines


def _quantile_lines(name: str, labels: dict[str, str], stats: dict) -> list[str]:
    """Prometheus summary exposition: quantiles (from the bounded ring) +
    exact cumulative _sum/_count, milliseconds converted to seconds.
    The quantile label merges into the series label set (single braces)."""
    out = [f"# TYPE {name} summary"]
    for q, key in (("0.5", "p50_ms"), ("0.95", "p95_ms"), ("0.99", "p99_ms")):
        merged = _labels({**labels, "quantile": q})
        out.append(f"{name}{merged} {stats.get(key, 0) / 1000:.4f}")
    out.append(f"{name}_sum{_labels(labels)} {stats.get('sum_ms', 0) / 1000:.4f}")
    out.append(f"{name}_count{_labels(labels)} {stats.get('count', 0)}")
    return out


def render_metrics(app) -> str:
    metrics = app.state.metrics
    snap = metrics.snapshot()
    lines: list[str] = []

    # -- build info + uptime ------------------------------------------------
    lines += [
        "# HELP medicalscribe_build_info Build metadata (value is always 1).",
        "# TYPE medicalscribe_build_info gauge",
        f'medicalscribe_build_info{{api_version="{API_VERSION}",phase="{PHASE}",'
        f'python="{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"}} 1',
        "# HELP medicalscribe_uptime_seconds Process uptime in seconds.",
        "# TYPE medicalscribe_uptime_seconds gauge",
        f"medicalscribe_uptime_seconds {snap['uptime_seconds']}",
    ]

    # -- counters ------------------------------------------------------------
    generic: dict[str, float] = {}
    structured: dict[tuple[str, str], float] = {}
    for name, value in sorted(snap["counters"].items()):
        series = _counter_series(name)
        if series is not None:
            metric, labels = series
            structured[(metric, _labels(labels))] = (
                structured.get((metric, _labels(labels)), 0) + value
            )
        else:
            generic[_sanitize(name)] = generic.get(_sanitize(name), 0) + value

    emitted: set[str] = set()
    for (metric, label_str), value in sorted(structured.items()):
        if metric not in emitted:
            lines.append(f"# TYPE {metric} counter")
            emitted.add(metric)
        lines.append(f"{metric}{label_str} {value}")
    for name, value in sorted(generic.items()):
        lines.append(f"# TYPE medicalscribe_{name} counter")
        lines.append(f"medicalscribe_{name} {value}")

    # -- gauges --------------------------------------------------------------
    for name, value in sorted(snap["gauges"].items()):
        gname = _sanitize(name)
        lines.append(f"# TYPE medicalscribe_{gname} gauge")
        lines.append(f"medicalscribe_{gname} {value}")

    # -- latency summaries ---------------------------------------------------
    for name, stats in sorted(snap["latency"].items()):
        if name.startswith("http."):
            route = name[len("http."):] or "unmatched"
            lines += _quantile_lines(
                "medicalscribe_http_request_duration_seconds", {"route": route}, stats
            )
        else:
            lines += _quantile_lines(
                f"medicalscribe_{_sanitize(name)}_seconds", {}, stats
            )

    # -- app-state extras ------------------------------------------------------
    db = getattr(app.state, "db", None)
    lines += [
        "# HELP medicalscribe_db_mode Durable database mode (1=durable, 0=in-memory).",
        "# TYPE medicalscribe_db_mode gauge",
        f"medicalscribe_db_mode {1 if db is not None else 0}",
    ]
    ledger = getattr(app.state, "cost_ledger", None)
    if ledger is not None:
        cost = ledger.snapshot()
        lines += [
            "# HELP medicalscribe_cost_tokens_today LLM tokens consumed today (UTC).",
            "# TYPE medicalscribe_cost_tokens_today gauge",
            f"medicalscribe_cost_tokens_today {cost['tokens_today']}",
            "# HELP medicalscribe_cost_budget_tokens_per_day Configured daily budget (0=disabled).",
            "# TYPE medicalscribe_cost_budget_tokens_per_day gauge",
            f"medicalscribe_cost_budget_tokens_per_day {cost['budget_tokens_per_day'] or 0}",
            "# HELP medicalscribe_cost_budget_exhausted Soft-stop active (cloud excluded).",
            "# TYPE medicalscribe_cost_budget_exhausted gauge",
            f"medicalscribe_cost_budget_exhausted {1 if cost['exhausted'] else 0}",
        ]
    tracker = getattr(app.state, "provider_health", None)
    if tracker is not None:
        health = tracker.snapshot()
        if health:
            lines += [
                "# HELP medicalscribe_provider_healthy Router health state per provider.",
                "# TYPE medicalscribe_provider_healthy gauge",
            ]
            for provider, state in health.items():
                lines.append(
                    f'medicalscribe_provider_healthy{{provider="{provider}"}} '
                    f"{1 if state['healthy'] else 0}"
                )
            lines += [
                "# HELP medicalscribe_provider_latency_ewma_ms EWMA request latency per provider.",
                "# TYPE medicalscribe_provider_latency_ewma_ms gauge",
            ]
            for provider, state in health.items():
                if state.get("latency_ewma_ms") is not None:
                    lines.append(
                        f'medicalscribe_provider_latency_ewma_ms{{provider="{provider}"}} '
                        f"{state['latency_ewma_ms']:.1f}"
                    )

    return "\n".join(lines) + "\n"


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        render_metrics(request.app), media_type="text/plain; version=0.0.4; charset=utf-8"
    )
