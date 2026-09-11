"""Structured logging + lightweight in-process metrics (Phase 1 form).

Data hygiene rules (docs/ARCHITECTURE.md):
- Log route templates, not full URLs with query strings (WS tokens live in
  query params).
- Never log transcript payloads, provider request bodies, or secrets.
- Prometheus text exposition and OTel spans land in Phase 8; the counter
  registry below is shaped so they can sit on top of it unchanged.
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
            if len(series) > 1000:  # bounded ring for Phase 1 process-local stats
                del series[: len(series) - 500]

    def snapshot(self) -> dict:
        with self._lock:
            counters = deepcopy(dict(self._counters))
            gauges = deepcopy(dict(self._gauges))
            latency = {
                name: {
                    "count": len(series),
                    "avg_ms": round(sum(series) / len(series), 1) if series else 0,
                    "max_ms": round(max(series), 1) if series else 0,
                }
                for name, series in self._latency.items()
            }
        return {
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "counters": counters,
            "gauges": gauges,
            "latency": latency,
        }
