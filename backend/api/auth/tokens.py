"""JWT access-token codec (HS256).

Standards, not novel crypto. Deliberately tiny until Phase 7 wires it to the
User table: claims contract is defined now so refresh handling, role
authorization and WS auth share one shape later.

Claims: sub (user id or "dev"), role, sid (session id), jti (token id,
revocation hook), iat, exp, typ ("access" | "refresh" — refresh validation is
Phase 7).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

import jwt as pyjwt


class TokenError(Exception):
    """Invalid/expired/foreign token. Surfaces as UNAUTHENTICATED."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    session_id: str | None = None
    is_dev: bool = False


def create_token(
    secret: str,
    *,
    sub: str,
    role: str,
    ttl_minutes: int,
    session_id: str | None = None,
    typ: str = "access",
) -> tuple[str, str]:
    """Return (token, jti). jti is returned so callers can store a revocation
    record (Phase 7 refresh rotation)."""
    jti = secrets.token_urlsafe(12)
    now = int(time.time())
    payload = {
        "sub": sub,
        "role": role,
        "sid": session_id,
        "jti": jti,
        "typ": typ,
        "iat": now,
        "exp": now + ttl_minutes * 60,
    }
    return pyjwt.encode(payload, secret, algorithm="HS256"), jti


def decode_token(secret: str, token: str, *, expected_typ: str = "access") -> Principal:
    try:
        claims = pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except pyjwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ", "access") != expected_typ:
        raise TokenError("wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise TokenError("missing subject")
    return Principal(user_id=str(sub), role=str(claims.get("role") or "clinician"), session_id=claims.get("sid"))
