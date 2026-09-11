"""WebSocket client transport seam for streaming cloud STT adapters.

The seam exists for two reasons:
1. unit tests drive provider protocols with scripted fake transports — no
   network, no recorded servers needed in CI (acceptance: "recorded
   fixtures/mocks");
2. swapping the websockets library (or adding proxy/TLS options) touches one
   file, not every adapter.

``recv`` yields ``str`` for text frames and ``bytes`` for binary frames, and
raises :class:`ConnectionClosed` on orderly or error close.
"""
from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class WsTransportError(RuntimeError):
    """Transport-level failure with provider-safe message."""

    def __init__(self, message: str, *, close_code: int | None = None) -> None:
        super().__init__(message)
        self.close_code = close_code


class ConnectionClosed(WsTransportError):
    pass


class WsTransport(abc.ABC):
    @abc.abstractmethod
    async def send_text(self, payload: str) -> None: ...

    @abc.abstractmethod
    async def send_bytes(self, payload: bytes) -> None: ...

    @abc.abstractmethod
    async def recv(self) -> str | bytes: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


#: (url, headers) -> connected transport. Adapters receive this so tests can
#: hand them a scripted fake.
WsConnector = Callable[[str, Mapping[str, str]], Awaitable[WsTransport]]


class WebsocketsTransport(WsTransport):
    """Production transport on the ``websockets`` asyncio client (>=13)."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    @classmethod
    async def connect(
        cls,
        url: str,
        headers: Mapping[str, str],
        *,
        subprotocols: list[str] | None = None,
        max_size: int = 8 * 1024 * 1024,
        ping_interval: float | None = 20.0,
        open_timeout: float = 10.0,
    ) -> WebsocketsTransport:
        from websockets.asyncio.client import connect

        ws = await connect(
            url,
            additional_headers=dict(headers),
            subprotocols=subprotocols,
            max_size=max_size,
            ping_interval=ping_interval,
            open_timeout=open_timeout,
        )
        return cls(ws)

    async def send_text(self, payload: str) -> None:
        await self._ws.send(payload)

    async def send_bytes(self, payload: bytes) -> None:
        await self._ws.send(payload)

    async def recv(self) -> str | bytes:
        from websockets.exceptions import ConnectionClosed as _Closed

        try:
            return await self._ws.recv()
        except _Closed as exc:
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            raise ConnectionClosed(f"connection closed ({code})", close_code=code) from exc
        except OSError as exc:
            raise ConnectionClosed(f"socket error: {exc}") from exc

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:  # pragma: no cover — teardown must not raise
            pass


async def default_connector(url: str, headers: Mapping[str, str]) -> WsTransport:
    return await WebsocketsTransport.connect(url, headers)
