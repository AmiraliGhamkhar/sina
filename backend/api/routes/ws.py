"""Real-time transcription WebSocket (protocol v1, Phase 2: audio pipeline live).

Flow: authenticate → ``session.start`` (strict v1 schema + protocol gate) →
AI-router provider selection → ``session.started`` → audio (binary frames or
JSON ``audio.chunk`` base64) fed to the TranscriptionHub → provider
``stream()`` segments relayed as ``transcript.interim`` / ``transcript.final``
→ pause/resume/stop with hub semantics → ``session.completed``; finals land
in the in-memory TranscriptStore (Phase 7 moves it to Postgres).

docs/WEBSOCKET_PROTOCOL.md is the normative document for this module.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ai.base import ProviderKind
from ai.router import NoEligibleProviderError
from api.auth.deps import websocket_principal
from api.errors import ErrorCode, ws_error_frame
from api.schemas.ws import AudioChunk, SessionCompleted, SessionStart, SessionStarted, _SessionCtl
from api.services.ai_bridge import select_stt_provider
from api.services.session_registry import WsSession
from api.services.transcription_hub import TranscriptionHub
from api.version import WS_PROTOCOL_MIN

logger = logging.getLogger(__name__)
router = APIRouter()

WS_CLOSE_POLICY_VIOLATION = 4400
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_TIMEOUT = 4408
WS_CLOSE_PROTOCOL_VERSION = 4409
WS_CLOSE_UNAVAILABLE = 4503


@router.websocket("/ws/v1/transcribe")
async def transcribe_socket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    try:
        principal = await websocket_principal(websocket, settings)
    except Exception:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    await websocket.accept()
    metrics = websocket.app.state.metrics
    metrics.incr("ws_accepted")
    session: WsSession | None = None
    hub: TranscriptionHub | None = None
    send_lock = asyncio.Lock()

    async def send_frame(frame: dict) -> None:
        # Sends racing a client disconnect are a normal teardown interleaving
        # (the loop below notices the disconnect via _recv and unwinds).
        # Starlette surfaces that race as ClosedResourceError/RuntimeError —
        # swallowing here keeps it out of both the hub pump task and TestClient
        # portals, which would otherwise re-raise it as an app crash.
        async with send_lock:
            try:
                await websocket.send_json(frame)
            except (WebSocketDisconnect, anyio.ClosedResourceError, RuntimeError):
                metrics.incr("ws_send_after_close")
                logger.debug("dropped frame for closed socket: %s", frame.get("type"))

    try:
        session, hub = await _handshake(websocket, principal.user_id, send_frame)
        if session is None:
            return
        await _media_loop(websocket, session, hub, send_frame)
    except WebSocketDisconnect:
        logger.debug(
            "websocket disconnected",
            extra={"session": session.session_id if session else None},
        )
    finally:
        if hub is not None:
            try:
                await hub.stop(timeout_s=settings.websocket.provider_flush_timeout_s)
            except Exception:  # pragma: no cover - shutdown path, never raise
                logger.debug("hub stop failed during cleanup", exc_info=True)
        if session is not None:
            websocket.app.state.sessions.remove(session.session_id)
            metrics.set_gauge("ws_connections_active", websocket.app.state.sessions.count())


async def _recv(websocket: WebSocket, timeout: float) -> tuple[str, object] | None:
    """Raw receive: ('text', str) | ('bytes', bytes) | ('bad-json', None).
    Returns ``None`` on timeout. Raises WebSocketDisconnect when the client
    goes away."""
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout)
    except TimeoutError:
        return None
    mtype = message.get("type")
    if mtype == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    if message.get("bytes") is not None:
        return ("bytes", message["bytes"])
    text = message.get("text") or ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return ("json", parsed)
    except json.JSONDecodeError:
        pass
    return ("bad-json", None)


async def _handshake(
    websocket: WebSocket, user_id: str, send_frame
) -> tuple[WsSession | None, TranscriptionHub | None]:
    settings = websocket.app.state.settings
    received = await _recv(websocket, timeout=15.0)
    if received is None:
        await send_frame(
            ws_error_frame(ErrorCode.PROTOCOL_FRAME, "expected session.start", recoverable=False)
        )
        await websocket.close(code=WS_CLOSE_TIMEOUT)
        return None, None
    kind, payload = received
    if kind != "json" or not payload:
        await send_frame(
            ws_error_frame(
                ErrorCode.PROTOCOL_FRAME,
                "first frame must be a JSON session.start",
                recoverable=False,
            )
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return None, None

    if payload.get("type") != "session.start":
        await send_frame(
            ws_error_frame(
                ErrorCode.SESSION_STATE, "first frame must be session.start", recoverable=False
            )
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return None, None

    version = payload.get("v")
    if not isinstance(version, int) or version < WS_PROTOCOL_MIN or version > 1:
        await send_frame(
            ws_error_frame(
                ErrorCode.PROTOCOL_VERSION,
                f"client protocol v{version!r} unsupported (server speaks v1)",
                recoverable=False,
            )
        )
        await websocket.close(code=WS_CLOSE_PROTOCOL_VERSION)
        return None, None

    # strict schema validation — unknown fields are protocol drift, reject loudly
    try:
        start = SessionStart.model_validate(payload)
    except ValidationError as exc:
        await send_frame(
            ws_error_frame(
                ErrorCode.PROTOCOL_FRAME,
                "session.start rejected",
                recoverable=False,
                details=[
                    {"loc": list(e.get("loc", [])), "type": e.get("type")} for e in exc.errors()
                ],
            )
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return None, None

    # provider selection goes through the AI router — never a direct pick
    try:
        decision, provider = await select_stt_provider(websocket, start)
    except NoEligibleProviderError as exc:
        await send_frame(ws_error_frame(ErrorCode.NO_PROVIDER, str(exc), recoverable=False))
        await websocket.close(code=WS_CLOSE_UNAVAILABLE)
        return None, None
    except Exception as exc:
        logger.exception("provider selection failed")
        await send_frame(ws_error_frame(ErrorCode.PROVIDER_UNAVAILABLE, str(exc), recoverable=False))
        await websocket.close(code=WS_CLOSE_UNAVAILABLE)
        return None, None

    session = websocket.app.state.sessions.create(user_id=user_id, provider=decision.provider)
    session.state = "recording"
    metrics = websocket.app.state.metrics
    metrics.incr("ws_sessions_started")

    audio = start.audio if isinstance(start.audio, dict) else {}
    try:
        sample_rate = int(audio.get("sample_rate", 16000))
        channels = int(audio.get("channels", 1))
    except (TypeError, ValueError):
        sample_rate, channels = 16000, 1

    websocket.app.state.transcript_store.open(
        session.session_id,
        user_id=user_id,
        provider=decision.provider,
        language=start.language,
    )
    # Phase 5 runtime fallback: the hub may transparently switch to the
    # router's fallback names (already privacy-filtered by route()) if the
    # primary stream dies with a retryable error mid-session.
    hub = TranscriptionHub(
        provider=provider,
        session_id=session.session_id,
        store=websocket.app.state.transcript_store,
        send_frame=send_frame,
        metrics=metrics,
        health_tracker=websocket.app.state.provider_health,
        audio_q_maxsize=settings.websocket.audio_queue_maxsize,
        pause_buffer_ms=settings.websocket.pause_buffer_ms,
        sample_rate=sample_rate,
        channels=channels,
        fallback_chain=list(decision.fallbacks) if settings.routing.fallback_enabled else [],
        provider_factory=lambda name: websocket.app.state.ai_registry.create(
            ProviderKind.STT, name, settings.provider_config("stt", name)
        ),
        command_service=getattr(websocket.app.state, "voice_commands", None),
    )
    # started frame first, pump second: deterministic ordering of the first
    # control frame vs provider transcript frames (no race on session.started)
    await send_frame(
        SessionStarted(
            session_id=session.session_id,
            protocol=1,
            provider=decision.provider,
            mode=str(start.mode or settings.routing.mode),
            language=start.language,
            server_time_ms=int(time.time() * 1000),
        ).model_dump(mode="json")
    )
    hub.start()
    metrics.incr("stt_streams_started")

    websocket.app.state.audit.emit(
        "session_started",
        session_id=session.session_id,
        user_id=user_id,
        provider=decision.provider,
        routing_reason=decision.reason,
        privacy_override=decision.privacy_override_applied,
        language=start.language,
    )
    metrics.set_gauge("ws_connections_active", websocket.app.state.sessions.count())
    return session, hub


async def _media_loop(
    websocket: WebSocket, session: WsSession, hub: TranscriptionHub, send_frame
) -> None:
    settings = websocket.app.state.settings
    metrics = websocket.app.state.metrics
    max_s = settings.websocket.max_session_minutes * 60
    recv_timeout = float(settings.websocket.heartbeat_seconds * 3)
    last_audio_at = time.time()

    while True:
        received = await _recv(websocket, timeout=recv_timeout)
        if received is None:
            await send_frame(
                ws_error_frame(ErrorCode.SESSION_STATE, "idle timeout", recoverable=False)
            )
            await websocket.close(code=WS_CLOSE_TIMEOUT)
            return

        kind, payload = received
        if kind == "bytes":
            session.audio_frames += 1
            last_audio_at = time.time()
            await hub.feed(payload)  # type: ignore[arg-type]
            continue
        if kind == "bad-json":
            await send_frame(ws_error_frame(ErrorCode.PROTOCOL_FRAME, "frame is not valid JSON"))
            continue

        mtype = payload.get("type")

        if mtype == "audio.chunk":
            try:
                chunk = AudioChunk.model_validate(payload)
                data = base64.b64decode(chunk.pcm16_b64, validate=True)
            except (ValidationError, ValueError) as exc:
                await send_frame(
                    ws_error_frame(
                        ErrorCode.PROTOCOL_FRAME,
                        "audio.chunk rejected",
                        details=(
                            [{"loc": list(e.get("loc", [])), "type": e.get("type")} for e in exc.errors()]
                            if isinstance(exc, ValidationError)
                            else ["invalid base64 payload"]
                        ),
                    )
                )
                continue
            session.audio_frames += 1
            last_audio_at = time.time()
            metrics.incr("ws_audio_frames")
            await hub.feed(data)
            continue

        if mtype in ("session.pause", "session.resume", "session.stop"):
            try:
                ctl = _SessionCtl.model_validate(payload)
            except ValidationError as exc:
                await send_frame(
                    ws_error_frame(
                        ErrorCode.PROTOCOL_FRAME, f"{mtype} rejected", details=exc.errors()[:3]
                    )
                )
                continue
            if ctl.type == "session.stop":
                stats = await hub.stop(timeout_s=settings.websocket.provider_flush_timeout_s)
                session.segments_final = stats["final_segments"]
                duration_ms = int((last_audio_at - session.created_at) * 1000)
                websocket.app.state.transcript_store.close(session.session_id)
                metrics.incr("stt_streams_stopped")
                # audit BEFORE the completed frame: once the client sees it, it
                # may drop the socket — and an audit trail that races with
                # transport teardown is not an audit trail
                websocket.app.state.audit.emit(
                    "session_stopped",
                    session_id=session.session_id,
                    segments_final=session.segments_final,
                    audio_seconds=stats["audio_seconds"],
                    dropped_audio_seconds=stats["dropped_audio_seconds"],
                    stream_failed=stats["stream_failed"],
                    duration_ms=duration_ms,
                )
                await send_frame(
                    SessionCompleted(
                        session_id=session.session_id,
                        segment_count=session.segments_final,
                        duration_ms=duration_ms,
                        provider=session.provider,
                    ).model_dump(mode="json")
                )
                return
            new_state = "paused" if ctl.type == "session.pause" else "recording"
            if (session.state, new_state) not in {("recording", "paused"), ("paused", "recording")}:
                await send_frame(
                    ws_error_frame(
                        ErrorCode.SESSION_STATE, f"cannot go {session.state} → {new_state}"
                    )
                )
                continue
            session.state = new_state  # type: ignore[assignment]
            if new_state == "paused":
                hub.pause()
            else:
                hub.resume()
            await send_frame(
                {"v": 1, "type": f"session.{new_state}", "session_id": session.session_id}
            )
            continue

        if mtype == "pong":
            continue  # heartbeat reply (reserved; server pings land with Phase 8)

        await send_frame(
            ws_error_frame(ErrorCode.PROTOCOL_FRAME, f"unknown message type '{mtype}'")
        )
        if time.time() - session.created_at > max_s:
            await send_frame(
                ws_error_frame(
                    ErrorCode.SESSION_STATE, "max session duration exceeded", recoverable=False
                )
            )
            return
