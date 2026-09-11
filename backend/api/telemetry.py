"""Structured logging + lightweight in-process metrics (Phase 1 form).

Data hygiene rules (docs/ARCHITECTURE.md):
- Log route templates, not full URLs with query strings (WS tokens live in
  query params).
- Never log transcript payloads, provider request bodies, or secrets.
- Phase 8: `/metrics` (routes/metrics.py) maps this registry to the
  Prometheus text format; optional OTel traces via `maybe_init_otel`.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import defaultdict
from copy import deepcopy
from threading import Lock


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        for key in ("route", "status", "duration_ms", "user", "session", "provider", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_logs: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Metrics:
    """Thread-safe counters + gauges, exported as a plain dict snapshot.

    Names are stable strings; Phase 8 maps them 1:1 to Prometheus metrics
    (ws_connections_active gauge, llm_latency_ms summary, etc.).
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)
        self._latency: dict[str, list[float]] = defaultdict(list)
        #: cumulative (count, sum_ms) per latency series — exact, unbounded
        #: cheap; the ring above only bounds the quantile approximation.
        self._latency_totals: dict[str, tuple[int, float]] = defaultdict(
            lambda: (0, 0.0)
        )
        self.started_at = time.time()

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe_ms(self, name: str, ms: float) -> None:
        with self._lock:
            series = self._latency[name]
            series.append(ms)
            if len(series) > 1000:  # bounded ring: quantiles over recent samples
                del series[: len(series) - 500]
            count, total = self._latency_totals[name]
            self._latency_totals[name] = (count + 1, total + ms)

    def snapshot(self) -> dict:
        with self._lock:
            counters = deepcopy(dict(self._counters))
            gauges = deepcopy(dict(self._gauges))
            latency = {
                name: {
                    # exact, cumulative
                    "count": self._latency_totals[name][0],
                    "sum_ms": round(self._latency_totals[name][1], 1),
                    "avg_ms": round(
                        self._latency_totals[name][1] / max(1, self._latency_totals[name][0]), 1
                    ),
                    # approximations from the bounded ring (last ≤1000 samples)
                    "ring_count": len(series),
                    "max_ms": round(max(series), 1) if series else 0,
                    "p50_ms": _quantile(series, 0.50),
                    "p95_ms": _quantile(series, 0.95),
                    "p99_ms": _quantile(series, 0.99),
                }
                for name, series in self._latency.items()
            }
        return {
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "counters": counters,
            "gauges": gauges,
            "latency": latency,
        }


def _quantile(samples: list[float], q: float) -> float:
    """Nearest-rank quantile over the (bounded, recent) sample ring."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, min(len(ordered), int(round(q * len(ordered)))))
    return round(ordered[rank - 1], 1)


def maybe_init_otel(settings, app) -> bool:
    """Optional OTel tracing (Phase 8, spec §18 observability).

    Enabled only when MS_OBSERVABILITY__OTEL_ENABLED=true AND the optional
    `observability` extra is installed (opentelemetry-sdk +
    -instrumentation-fastapi + OTLP exporter). Never a hard dependency: a
    missing SDK logs a warning and the server runs metrics-only. The exporter
    follows the standard OTel env contract (OTEL_EXPORTER_OTLP_ENDPOINT etc.).
    Call once from create_app (sync, before startup) — single-process by design.
    """
    if not settings.observability.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning(
            "MS_OBSERVABILITY__OTEL_ENABLED=true but the `observability` extra is "
            "not installed (pip install '.[observability]') — running metrics-only"
        )
        return False

    resource = Resource.create(
        {"service.name": "medicalscribe-api", "service.version": settings.server.env}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logging.getLogger(__name__).info("OTel tracing enabled (OTLP gRPC exporter)")
    return True
