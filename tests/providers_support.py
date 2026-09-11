"""Shared helpers for Phase 3 provider tests: scripted WS transport,
recorded-fixture loading, synthetic speech builders. No network anywhere.
"""
from __future__ import annotations

import io
import json
import wave
from collections import deque
from pathlib import Path

from ai.stt.ws_transport import ConnectionClosed, WsTransport

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ScriptedTransport(WsTransport):
    """Server stand-in: replays queued messages, records client traffic."""

    def __init__(self, messages: list) -> None:
        self._q: deque = deque(
            m if isinstance(m, (str, bytes)) else json.dumps(m) for m in messages
        )
        self.sent: list = []  # ("text"|"bytes", payload)
        self.closed = False

    async def send_text(self, payload: str) -> None:
        self.sent.append(("text", payload))

    async def send_bytes(self, payload: bytes) -> None:
        self.sent.append(("bytes", payload))

    async def recv(self) -> str | bytes:
        if not self._q:
            raise ConnectionClosed("script exhausted")
        item = self._q.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def scripted_connector(transport: ScriptedTransport):
    """Capture connect args so tests can assert URL/headers."""

    captured: dict = {}

    async def connector(url: str, headers) -> ScriptedTransport:
        captured["url"] = url
        captured["headers"] = dict(headers)
        return transport

    connector.captured = captured  # type: ignore[attr-defined]
    return connector


def pcm_speech(ms: int, sample_rate: int = 16000, amplitude: int = 6000) -> bytes:
    """Alternating-sign square wave — plenty loud for the energy VAD."""
    n = sample_rate * ms // 1000
    return b"".join(
        int(amplitude if i % 2 == 0 else -amplitude).to_bytes(2, "little", signed=True)
        for i in range(n)
    )


def pcm_silence(ms: int, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * (sample_rate * ms // 1000)


def make_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def agen(items: list):
    """Async generator over a list (audio chunk stream)."""
    for it in items:
        yield it


async def collect(aiter) -> list:
    return [x async for x in aiter]
