#!/usr/bin/env python3
"""WebSocket load test (Phase 8, spec §18 acceptance).

Spawns N concurrent dictation sessions against a running MedicalScribe API
(default provider: mock — no external AI services), streams PCM audio
chunks, and records:

- time to session.started
- per-final-segment latency (audio sent → transcript.final received)
- session throughput and dropouts (error frames / closes)

Usage:
    python infrastructure/load/ws_loadtest.py --url ws://localhost:8000/ws/v1/transcribe \
        --token <dev-or-jwt> --sessions 20 --duration 60

The roadmap acceptance target ("20 concurrent WS sessions on a 4 GB VM with
local llama-server") uses the same script with a real provider — the mock
run validates the harness and the server's WS/async layer only; its numbers
are a LOWER BOUND baseline, not STT performance.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import time

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pip install websockets (or `pip install -e .[dev]`)") from exc


class SessionStats:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.started_ms: float | None = None
        self.final_latencies: list[float] = []
        self.errors = 0
        self.closed = False


async def run_session(
    url: str, token: str, duration_s: float, stats: SessionStats, chunk_hz: float = 20.0
) -> None:
    """One dictation session: start → stream silent-ish audio → stop."""
    started = time.perf_counter()
    async with websockets.connect(
        f"{url}?token={token}", max_size=2**20, ping_interval=None
    ) as ws:
        await ws.send(json.dumps({"v": 1, "type": "session.start"}))
        # quiet 16 kHz mono PCM: small nonzero frames keep the pipeline honest
        chunk = base64.b64encode(b"\x00\x01" * 160).decode()

        async def sender() -> None:
            interval = 1.0 / chunk_hz
            deadline = time.perf_counter() + duration_s
            seq = 0
            t0 = time.monotonic()
            while time.perf_counter() < deadline:
                await ws.send(json.dumps({
                    "v": 1, "type": "audio.chunk", "seq": seq,
                    "ts_ms": int((time.monotonic() - t0) * 1000),
                    "pcm16_b64": chunk,
                }))
                seq += 1
                await asyncio.sleep(interval)
            await ws.send(json.dumps({"v": 1, "type": "session.stop"}))

        async def receiver() -> None:
            async for raw in ws:
                frame = json.loads(raw)
                ftype = frame.get("type")
                if ftype == "session.started":
                    stats.started_ms = (time.perf_counter() - started) * 1000
                elif ftype == "transcript.final":
                    stats.final_latencies.append(
                        (time.perf_counter() - started) * 1000
                    )
                elif ftype == "error":
                    stats.errors += 1
                elif ftype == "session.completed":
                    stats.closed = True
                    return

        await asyncio.gather(sender(), receiver())

    stats.closed = stats.closed or True


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(q * len(ordered))))]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:8000/ws/v1/transcribe")
    parser.add_argument("--token", required=True, help="access JWT or dev token")
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--rampup-ms", type=float, default=2000.0)
    #: 20 concurrent sessions as ONE principal would trip the per-user cap
    #: (MS_RATE_LIMIT__WS_SESSIONS_PER_USER, default 5 — spec §14); with the
    #: server's JWT secret the harness mints one synthetic user per session.
    parser.add_argument(
        "--jwt-secret",
        default=None,
        help="mint a distinct principal per session (recommended for >cap runs)",
    )
    args = parser.parse_args()

    all_stats: list[SessionStats] = []
    wall_start = time.perf_counter()

    def token_for(i: int) -> str:
        if not args.jwt_secret:
            return args.token
        import sys

        sys.path.insert(0, "backend")
        from api.auth.tokens import create_token

        tok, _ = create_token(args.jwt_secret, sub=f"loadtest-{i}", role="physician",
                              ttl_minutes=30)
        return tok

    async def spawn(i: int) -> None:
        await asyncio.sleep(args.rampup_ms / 1000 * i / max(1, args.sessions - 1))
        stats = SessionStats(f"ws-{i}")
        all_stats.append(stats)
        try:
            await run_session(args.url, token_for(i), args.duration, stats)
        except Exception as exc:  # noqa: BLE001 — record, never abort the run
            stats.errors += 1
            print(f"session {i} failed: {exc}")

    await asyncio.gather(*(spawn(i) for i in range(args.sessions)))
    wall = time.perf_counter() - wall_start

    starteds = [s.started_ms for s in all_stats if s.started_ms is not None]
    finals = [ms for s in all_stats for ms in s.final_latencies]
    errors = sum(s.errors for s in all_stats)
    completed = sum(1 for s in all_stats if s.closed)

    print("\n=== MedicalScribe WS load test ===")
    print(f"sessions: {args.sessions}  duration/session: {args.duration}s  wall: {wall:.1f}s")
    print(f"handshake completed: {len(starteds)}/{args.sessions}")
    if starteds:
        print(
            f"session.started latency ms: p50={_pct(starteds, .5):.0f} "
            f"p95={_pct(starteds, .95):.0f} max={max(starteds):.0f}"
        )
    print(f"final segments received: {len(finals)}  errors: {errors}  completed: {completed}")
    if finals:
        print(
            "final-arrival time (since session start) ms: "
            f"p50={_pct(finals, .5):.0f} p95={_pct(finals, .95):.0f} "
            f"mean={statistics.mean(finals):.0f}"
        )
    per_session = [len(s.final_latencies) for s in all_stats]
    if per_session:
        print(f"finals per session: min={min(per_session)} max={max(per_session)}")


if __name__ == "__main__":
    asyncio.run(main())
