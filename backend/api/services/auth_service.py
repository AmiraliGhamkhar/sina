"""Authentication service (Phase 7, spec §14).

Pieces:
- ``PasswordHasher`` — argon2id (argon2-cffi) with configurable cost; the
  hash string never leaves this module except into the users table.
- ``LoginGuard`` — per-username failure counting + temporary lockout
  (in-process; small state, per-worker is the documented deployment unit).
- ``RefreshStore`` — one-time refresh tokens: DB-backed when persistence is
  on (hashed at rest), in-process fallback otherwise. Rotation detects
  reuse → revokes the whole user's sessions (token-theft posture).
- ``AuthService`` — login / refresh / logout orchestration.

The dev-token path (deps.py) stays for dev builds; production requires real
credentials and a JWT secret.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from api.auth.tokens import Principal, TokenError, create_token, decode_token

logger = logging.getLogger(__name__)


class AuthError(Exception):
    def __init__(self, reason: str, status_code: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


# -- password hashing ---------------------------------------------------------------


class PasswordHasher:
    """argon2id via argon2-cffi (lazy import: the `security` extra)."""

    def __init__(self, *, time_cost: int = 3, memory_cost: int = 65536, parallelism: int = 4):
        self._params = dict(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)

    def _hasher(self):
        try:
            from argon2 import PasswordHasher
        except ImportError as exc:  # pragma: no cover - extras missing
            raise AuthError("password auth requires the `security` extra (argon2-cffi)", 500) from exc
        return PasswordHasher(**self._params)

    def hash(self, password: str) -> str:
        return self._hasher().hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

        try:
            return self._hasher().verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


# -- lockout ---------------------------------------------------------------------------


@dataclass
class _LockState:
    failures: int = 0
    locked_until: float = 0.0


class LoginGuard:
    """In-process per-username lockout (spec §14 lockout policy)."""

    def __init__(self, *, max_failures: int = 5, lockout_seconds: int = 300) -> None:
        self._max = max(1, max_failures)
        self._lockout_s = lockout_seconds
        self._states: dict[str, _LockState] = {}

    def check_locked(self, username: str) -> float:
        """Return remaining lock seconds (0 = not locked)."""
        state = self._states.get(username.lower())
        if state and state.locked_until > time.time():
            return state.locked_until - time.time()
        return 0.0

    def record_failure(self, username: str) -> float:
        key = username.lower()
        state = self._states.setdefault(key, _LockState())
        state.failures += 1
        if state.failures >= self._max:
            state.locked_until = time.time() + self._lockout_s
            state.failures = 0
            logger.warning("account locked temporarily user=%s", key)
            return float(self._lockout_s)
        return 0.0

    def record_success(self, username: str) -> None:
        self._states.pop(username.lower(), None)


# -- refresh token store -----------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RefreshStore:
    """One-time refresh tokens. DB-backed (hashed) or in-process fallback."""

    def __init__(self, repo=None) -> None:
        self._repo = repo  # api.repositories.users.RefreshTokenRepository | None
        self._memory: dict[str, dict] = {}  # jti → {hash, user_id, expires_ts, revoked}

    async def issue(
        self, *, token: str, jti: str, user_id: str, ttl_minutes: int, device: str = ""
    ) -> None:
        expires = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        if self._repo is not None:
            await self._repo.issue(
                jti=jti,
                token_hash=_hash_token(token),
                user_id=user_id,
                expires_at=expires,
                device=device,
            )
            return
        self._memory[jti] = {
            "hash": _hash_token(token),
            "user_id": user_id,
            "expires_ts": expires.timestamp(),
            "revoked": False,
        }

    async def consume(self, *, token: str, jti: str) -> str | None:
        """Return the owning user_id when the token is live; None otherwise.
        Consuming is one-shot: a second call for the same jti fails."""
        if self._repo is not None:
            row = await self._repo.consume(jti)
            if row is None or not hmac.compare_digest(row.token_hash, _hash_token(token)):
                return None
            return row.user_id
        entry = self._memory.get(jti)
        if entry is None or entry["revoked"] or entry["expires_ts"] <= time.time():
            return None
        if not hmac.compare_digest(entry["hash"], _hash_token(token)):
            return None
        entry["revoked"] = True
        return entry["user_id"]

    async def revoke_all_for_user(self, user_id: str) -> int:
        if self._repo is not None:
            return await self._repo.revoke_all_for_user(user_id)
        count = 0
        for entry in self._memory.values():
            if entry["user_id"] == user_id and not entry["revoked"]:
                entry["revoked"] = True
                count += 1
        return count


# -- service ---------------------------------------------------------------------------


@dataclass
class IssuedPair:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str = ""
    role: str = ""


class UserLookup(Protocol):
    async def get_by_username(self, username: str): ...
    async def update_login(self, user_id: str, *, success: bool) -> None: ...


class AuthService:
    def __init__(
        self,
        *,
        settings,
        users: UserLookup | None = None,
        refresh_store: RefreshStore | None = None,
        hasher: PasswordHasher | None = None,
        guard: LoginGuard | None = None,
    ) -> None:
        self._settings = settings
        self._users = users
        self._refresh = refresh_store or RefreshStore()
        self._hasher = hasher or PasswordHasher()
        self._guard = guard or LoginGuard(
            max_failures=settings.auth.lockout_max_failures,
            lockout_seconds=settings.auth.lockout_seconds,
        )

    @property
    def refresh_store(self) -> RefreshStore:
        return self._refresh

    def _secret(self) -> str:
        jwt_secret = self._settings.auth.jwt_secret
        if jwt_secret is None:
            raise AuthError("MS_AUTH__JWT_SECRET is required for credential auth", 500)
        return jwt_secret.get_secret_value()

    async def login(self, username: str, password: str, *, device: str = "") -> IssuedPair:
        if self._users is None:
            raise AuthError(
                "credential login requires a configured database (MS_DATABASE__URL)", 501
            )
        remaining = self._guard.check_locked(username)
        if remaining > 0:
            raise AuthError(
                f"account locked — try again in {int(remaining) + 1}s", 429
            )
        user = await self._users.get_by_username(username)
        if user is None or user.password_hash is None:
            lock = self._guard.record_failure(username)
            # constant-ish time: still burn a hash when the user is unknown
            self._hasher.verify(_DUMMY_HASH, password)
            if lock:
                raise AuthError(f"account locked — try again in {int(lock) + 1}s", 429)
            raise AuthError("invalid credentials")
        if not user.is_active:
            raise AuthError("account disabled", 403)
        if not self._hasher.verify(user.password_hash, password):
            lock = self._guard.record_failure(username)
            await self._users.update_login(user.id, success=False)
            if lock:
                raise AuthError(f"account locked — try again in {int(lock) + 1}s", 429)
            raise AuthError("invalid credentials")
        self._guard.record_success(username)
        await self._users.update_login(user.id, success=True)
        pair = await self._issue_pair(user.id, user.role_id, device)
        return IssuedPair(pair.access_token, pair.refresh_token, pair.expires_in,
                          user_id=user.id, role=user.role_id)

    async def refresh(self, refresh_token: str, *, device: str = "") -> IssuedPair:
        try:
            principal: Principal = decode_token(
                self._secret(), refresh_token, expected_typ="refresh"
            )
        except TokenError as exc:
            raise AuthError(str(exc)) from exc
        user_id = await self._refresh.consume(token=refresh_token, jti=_jti_of(principal))
        if user_id is None:
            # consumed/unknown refresh token: assume theft → revoke the user's sessions
            revoked = await self._refresh.revoke_all_for_user(principal.user_id)
            logger.warning(
                "refresh token reuse detected user=%s revoked=%d", principal.user_id, revoked
            )
            raise AuthError("refresh token invalid or already used")
        # role from the token is trusted for the rotation (no DB round-trip);
        # a demoted user is cut off when the access TTL expires
        return await self._issue_pair(user_id, principal.role, device)

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        try:
            principal = decode_token(self._secret(), refresh_token, expected_typ="refresh")
        except TokenError:
            return  # logging out with a dead token is fine
        await self._refresh.consume(token=refresh_token, jti=_jti_of(principal))

    async def issue_pair(self, user_id: str, role: str, device: str = "") -> IssuedPair:
        """Public pair issuance (dev-token login, admin-created sessions)."""
        return await self._issue_pair(user_id, role, device)

    async def _issue_pair(self, user_id: str, role: str, device: str) -> IssuedPair:
        auth = self._settings.auth
        access, _ = create_token(
            self._secret(), sub=user_id, role=role, ttl_minutes=auth.access_ttl_minutes
        )
        refresh, jti = create_token(
            self._secret(),
            sub=user_id,
            role=role,
            ttl_minutes=auth.refresh_ttl_days * 24 * 60,
            typ="refresh",
        )
        await self._refresh.issue(
            token=refresh,
            jti=jti,
            user_id=user_id,
            ttl_minutes=auth.refresh_ttl_days * 24 * 60,
            device=device,
        )
        return IssuedPair(
            access_token=access, refresh_token=refresh, expires_in=auth.access_ttl_minutes * 60
        )


def _jti_of(principal: Principal) -> str:
    return principal.jti or ""


#: burned when the username is unknown so login timing does not leak account
#: existence (a syntactically valid argon2id hash of an unguessable string)
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdGVkc2FsdGVkc2FsdGVkc2FsdGVk"
    "$RdescudvJCsgt3ub+b+dWRWJTmaaJObG/q2JvpC3RVZMi1D3bLao0A"
)
