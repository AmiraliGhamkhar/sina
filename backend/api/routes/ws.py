"""Real-time WebSocket control plane (protocol v1).

Phase 1 implements the session lifecycle end-to-end: authenticate →
``session.start`` → protocol + schema validation → AI-router provider
selection → ``session.started`` → pause/resume/stop → ``session.completed``
with audit records. The audio pipeline (``audio.chunk`` → provider stream →
``transcript.*`` frames) is deliberately gated behind Phase 2 and answers
with a typed, recoverable error frame — the protocol shape is final already,
so clients built now will not need changes later.

docs/WEBSOCKET_PROTOCOL.md is the normative document for this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ai.router import NoEligibleProviderError
from api.auth.deps import websocket_principal
from api.errors import ErrorCode, ws_error_frame
from api.schemas.ws import AudioChunk, SessionCompleted, SessionStart, SessionStarted, _SessionCtl
from api.services.ai_bridge import select_stt_provider
from api.services.session_registry import WsSession
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
    try:
        session = await _handshake(websocket, principal.user_id)
        if session is None:
            return
        await _control_loop(websocket, session)
    except WebSocketDisconnect:
        logger.debug(
            "websocket disconnected",
            extra={"session": session.session_id if session else None},
        )
    finally:
        if session is not None:
            websocket.app.state.sessions.remove(session.session_id)
            metrics.set_gauge("ws_connections_active", websocket.app.state.sessions.count())


async def _recv_json(websocket: WebSocket, timeout: float) -> dict | None:
    """Return dict payload, ``{}`` for invalid JSON, ``None`` on timeout."""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout)
    except TimeoutError:
        return None
    except (WebSocketDisconnect, RuntimeError):
        raise
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _handshake(websocket: WebSocket, user_id: str) -> WsSession | None:
    settings = websocket.app.state.settings
    payload = await _recv_json(websocket, timeout=15.0)
    if payload is None:
        await websocket.send_json(
            ws_error_frame(ErrorCode.PROTOCOL_FRAME, "expected session.start", recoverable=False)
        )
        await websocket.close(code=WS_CLOSE_TIMEOUT)
        return None
    if not payload:
        await websocket.send_json(
            ws_error_frame(ErrorCode.PROTOCOL_FRAME, "frame is not valid JSON", recoverable=False)
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return None
    if payload.get("type") != "session.start":
        await websocket.send_json(
            ws_error_frame(
                ErrorCode.SESSION_STATE, "first frame must be session.start", recoverable=False
            )
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return None

    version = payload.get("v")
    if not isinstance(version, int) or version < WS_PROTOCOL_MIN or version > 1:
        await websocket.send_json(
            ws_error_frame(
                ErrorCode.PROTOCOL_VERSION,
                f"client protocol v{version!r} unsupported (server speaks v1)",
                recoverable=False,
            )
        )
        await websocket.close(code=WS_CLOSE_PROTOCOL_VERSION)
        return None

    # strict schema validation — unknown fields are protocol drift, reject loudly
    try:
        start = SessionStart.model_validate(payload)
    except ValidationError as exc:
        await websocket.send_json(
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
        return None

    # provider selection goes through the AI router — never a direct pick
    try:
        decision = await select_stt_provider(websocket, start)
    except NoEligibleProviderError as exc:
        await websocket.send_json(
            ws_error_frame(ErrorCode.NO_PROVIDER, str(exc), recoverable=False)
        )
        await websocket.close(code=WS_CLOSE_UNAVAILABLE)
        return None
    except Exception as exc:
        logger.exception("provider selection failed")
        await websocket.send_json(
            ws_error_frame(ErrorCode.PROVIDER_UNAVAILABLE, str(exc), recoverable=False)
        )
        await websocket.close(code=WS_CLOSE_UNAVAILABLE)
        return None

    session = websocket.app.state.sessions.create(user_id=user_id, provider=decision.provider)
    session.state = "recording"
    metrics = websocket.app.state.metrics
    metrics.incr("ws_sessions_started")
    websocket.app.state.audit.emit(
        "session_started",
        session_id=session.session_id,
        user_id=user_id,
        provider=decision.provider,
        routing_reason=decision.reason,
        privacy_override=decision.privacy_override_applied,
        language=start.language,
    )
    await websocket.send_json(
        SessionStarted(
            session_id=session.session_id,
            protocol=1,
            provider=decision.provider,
            mode=str(start.mode or settings.routing.mode),
            language=start.language,
            server_time_ms=int(time.time() * 1000),
        ).model_dump(mode="json")
    )
    metrics.set_gauge("ws_connections_active", websocket.app.state.sessions.count())
    return session


async def _control_loop(websocket: WebSocket, session: WsSession) -> None:
    settings = websocket.app.state.settings
    metrics = websocket.app.state.metrics
    max_s = settings.websocket.max_session_minutes * 60
    recv_timeout = float(settings.websocket.heartbeat_seconds * 3)

    while True:
        payload = await _recv_json(websocket, timeout=recv_timeout)
        if payload is None:
            await websocket.send_json(
                ws_error_frame(ErrorCode.SESSION_STATE, "idle timeout", recoverable=False)
            )
            await websocket.close(code=WS_CLOSE_TIMEOUT)
            return
        if not payload:
            await websocket.send_json(
                ws_error_frame(ErrorCode.PROTOCOL_FRAME, "frame is not valid JSON")
            )
            continue

        mtype = payload.get("type")

        if mtype == "audio.chunk":
            metrics.incr("ws_audio_frames_phase1")
            session.audio_frames += 1
            try:
                AudioChunk.model_validate(payload)  # validate now, pipe in Phase 2
            except ValidationError as exc:
                await websocket.send_json(
                    ws_error_frame(
                        ErrorCode.PROTOCOL_FRAME,
                        "audio.chunk rejected",
                        details=[{"loc": list(e.get("loc", [])), "type": e.get("type")} for e in exc.errors()],
                    )
                )
                continue
            await websocket.send_json(
                ws_error_frame(
                    ErrorCode.CAPABILITY_NOT_IMPLEMENTED,
                    "audio pipeline lands in Phase 2; protocol accepted",
                )
            )
            continue

        if mtype in ("session.pause", "session.resume", "session.stop"):
            try:
                ctl = _SessionCtl.model_validate(payload)
            except ValidationError as exc:
                await websocket.send_json(
                    ws_error_frame(
                        ErrorCode.PROTOCOL_FRAME, f"{mtype} rejected", details=exc.errors()[:3]
                    )
                )
                continue
            if ctl.type == "session.stop":
                duration_ms = int(time.time() * 1000 - session.created_at * 1000)
                await websocket.send_json(
                    SessionCompleted(
                        session_id=session.session_id,
                        segment_count=session.segments_final,
                        duration_ms=duration_ms,
                        provider=session.provider,
                    ).model_dump(mode="json")
                )
                websocket.app.state.audit.emit(
                    "session_stopped",
                    session_id=session.session_id,
                    segments_final=session.segments_final,
                    duration_ms=duration_ms,
                )
                return
            new_state = "paused" if ctl.type == "session.pause" else "recording"
            if (session.state, new_state) not in {("recording", "paused"), ("paused", "recording")}:
                await websocket.send_json(
                    ws_error_frame(
                        ErrorCode.SESSION_STATE, f"cannot go {session.state} → {new_state}"
                    )
                )
                continue
            session.state = new_state  # type: ignore[assignment]
            await websocket.send_json(
                {"v": 1, "type": f"session.{new_state}", "session_id": session.session_id}
            )
            continue

        await websocket.send_json(
            ws_error_frame(ErrorCode.PROTOCOL_FRAME, f"unknown message type '{mtype}'")
        )
        if time.time() - session.created_at > max_s:
            await websocket.send_json(
                ws_error_frame(
                    ErrorCode.SESSION_STATE, "max session duration exceeded", recoverable=False
                )
            )
            return
