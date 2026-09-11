"""User/Role repository (Phase 7)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import RefreshToken, Role, User


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; Postgres keeps tz. Normalize to UTC
    before comparing (production engine is Postgres; sqlite is the test one)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class UserRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def get_by_username(self, username: str) -> User | None:
        async with self._sm() as session:
            result = await session.execute(select(User).where(User.username == username))
            return result.scalar_one_or_none()

    async def get(self, user_id: str) -> User | None:
        async with self._sm() as session:
            return await session.get(User, user_id)

    async def create(
        self,
        *,
        username: str,
        role_id: str = "clinician",
        password_hash: str | None = None,
        display_name: str = "",
        email: str | None = None,
    ) -> User:
        async with self._sm() as session:
            user = User(
                username=username,
                role_id=role_id,
                password_hash=password_hash,
                display_name=display_name or username,
                email=email,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def update_login(self, user_id: str, *, success: bool) -> None:
        async with self._sm() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            if success:
                user.last_login_at = datetime.now(UTC)
                user.failed_login_count = 0
                user.locked_until = None
            else:
                user.failed_login_count = (user.failed_login_count or 0) + 1
            await session.commit()

    async def set_lock(self, user_id: str, until: datetime | None) -> None:
        async with self._sm() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            user.locked_until = until
            if until is None:
                user.failed_login_count = 0
            await session.commit()

    async def list(self) -> list[User]:
        async with self._sm() as session:
            result = await session.execute(select(User).order_by(User.created_at))
            return list(result.scalars())

    async def set_active(self, user_id: str, active: bool) -> User | None:
        async with self._sm() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            user.is_active = active
            await session.commit()
            await session.refresh(user)
            return user


class RoleRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def ensure(self, role_id: str, description: str = "", permissions: list | None = None):
        async with self._sm() as session:
            existing = await session.get(Role, role_id)
            if existing is None:
                session.add(
                    Role(role_id=role_id, description=description, permissions=permissions or [])
                )
                await session.commit()

    async def list(self) -> list[Role]:
        async with self._sm() as session:
            result = await session.execute(select(Role))
            return list(result.scalars())


class RefreshTokenRepository:
    """One-time refresh tokens: store hash only, consume atomically."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def issue(
        self, *, jti: str, token_hash: str, user_id: str, expires_at: datetime, device: str = ""
    ) -> None:
        async with self._sm() as session:
            session.add(
                RefreshToken(
                    jti=jti, token_hash=token_hash, user_id=user_id,
                    expires_at=expires_at, device=device,
                )
            )
            await session.commit()

    async def consume(self, jti: str) -> RefreshToken | None:
        """Return the live token row and mark it used, or None when
        missing/expired/already revoked (rotation is one-shot)."""
        async with self._sm() as session:
            row = await session.get(RefreshToken, jti)
            if row is None or row.revoked_at is not None:
                return None
            if _aware(row.expires_at) <= datetime.now(UTC):
                return None
            row.revoked_at = datetime.now(UTC)
            await session.commit()
            return row

    async def mark_replaced(self, jti: str, replaced_by: str) -> None:
        async with self._sm() as session:
            row = await session.get(RefreshToken, jti)
            if row is not None:
                row.replaced_by = replaced_by
                await session.commit()

    async def revoke_all_for_user(self, user_id: str) -> int:
        from sqlalchemy import update

        async with self._sm() as session:
            result = await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
            await session.commit()
            return result.rowcount or 0

    async def purge_expired(self, now: datetime | None = None) -> int:
        from sqlalchemy import delete

        async with self._sm() as session:
            result = await session.execute(
                delete(RefreshToken).where(
                    RefreshToken.expires_at <= (now or datetime.now(UTC))
                )
            )
            await session.commit()
            return result.rowcount or 0
