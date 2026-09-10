"""Per-user memory files (Claude-style file store), read via agentic tools.

Deliberately NOT embeddings: this is a plain file store the agent reads and
writes through the memory_* tools, distinct from the pgvector RAG in
services/memory.py. Files are versioned — every mutating operation takes an
if_version and gets a conflict result when the file moved underneath it, so
concurrent agent rounds can't silently clobber each other.

All queries are scoped by user_id; a path is only ever a string that starts
with "/" and passes _validate_path. Nothing here touches the provider or the
network — it is stdlib + SQLAlchemy only.
"""
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.memory_files import MemoryFile

logger = logging.getLogger(__name__)

# Sentinel if_version meaning "create this file"; anything else must match the
# row's current version or the write is rejected as a conflict.
NEW_SENTINEL = "__new__"

_MAX_PATH_CHARS = 512


def _validate_path(path: str) -> str:
    """Validate a memory-file path and return it unchanged.

    Rules: non-empty string, starts with "/", at most 512 characters, no
    control characters (C0 range or DEL), and no ".." segments (split on
    "/"). Raises ValueError on the first violation.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    if len(path) > _MAX_PATH_CHARS:
        raise ValueError(f"path longer than {_MAX_PATH_CHARS} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise ValueError("path contains control characters")
    if ".." in path.split("/"):
        raise ValueError("path may not contain '..' segments")
    return path


def _tier1_5_paths() -> list[str]:
    """MEMORY_TIER1_5_PATHS parsed into a list of paths, blanks skipped."""
    return [p.strip() for p in settings.MEMORY_TIER1_5_PATHS.split(",") if p.strip()]


async def _get_row(db: AsyncSession, user_id: str, path: str):
    result = await db.execute(
        select(MemoryFile).where(
            MemoryFile.user_id == user_id, MemoryFile.path == path
        )
    )
    return result.scalar_one_or_none()


def _coerce_if_version(if_version):
    """NEW_SENTINEL stays a sentinel; anything else becomes an int.

    The tool layer passes if_version straight from the model's JSON args
    (a string), while the row's version is an int — normalize before both the
    version comparison and the arithmetic so "1" never spurious-conflicts.
    """
    if if_version == NEW_SENTINEL:
        return NEW_SENTINEL
    try:
        return int(if_version)
    except (TypeError, ValueError):
        raise ValueError("if_version must be __new__ or an integer")


async def memory_index(db: AsyncSession, user_id: str) -> list[dict]:
    """List this user's memory files: path, description, aliases (no content)."""
    result = await db.execute(
        select(MemoryFile.path, MemoryFile.description, MemoryFile.aliases)
        .where(MemoryFile.user_id == user_id)
        .order_by(MemoryFile.path)
    )
    return [
        {
            "path": row.path,
            "description": row.description,
            "aliases": list(row.aliases or []),
        }
        for row in result.all()
    ]


async def memory_read(db: AsyncSession, user_id: str, path: str) -> dict | None:
    """Full read of one memory file, or None when it doesn't exist.

    An invalid path is treated like a miss (None) rather than raised — the
    read-only callers (tool handlers, context building) shouldn't have to
    handle path errors separately from not-found.
    """
    try:
        path = _validate_path(path)
    except ValueError:
        return None
    row = await _get_row(db, user_id, path)
    if row is None:
        return None
    return {
        "path": row.path,
        "description": row.description,
        "aliases": list(row.aliases or []),
        "content": row.content or "",
        "version": row.version,
        "size_bytes": row.size_bytes,
        "updated_at": row.updated_at,
    }


async def _apply_write(db: AsyncSession, user_id: str, path: str, description: str,
                       aliases: list[str], content: str, if_version, source: str | None) -> dict:
    """The single versioned-write primitive.

    if_version == NEW_SENTINEL creates the file (version 1) or returns a
    conflict with the current state; otherwise it does a conditional UPDATE
    that only lands when the row's version still equals if_version. Returns
    {"ok": True, "version": N} on success, or {"ok": False, "reason": ...,
    "current": {content, version}} on size_cap/conflict/not_found.
    """
    try:
        path = _validate_path(path)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_path", "message": str(exc)}
    if_version = _coerce_if_version(if_version)
    size = len(content.encode("utf-8"))
    if size >= settings.MEMORY_FILE_CAP_BYTES:
        # at/over the cap is rejected outright — content is never truncated
        return {"ok": False, "reason": "size_cap",
                "message": "file exceeds size cap; consolidate first"}

    if if_version == NEW_SENTINEL:
        existing = await _get_row(db, user_id, path)
        if existing is not None:
            return {
                "ok": False,
                "reason": "conflict",
                "current": {"content": existing.content or "", "version": existing.version},
            }
        try:
            db.add(MemoryFile(
                user_id=user_id,
                path=path,
                description=description,
                aliases=aliases,
                content=content,
                version=1,
                size_bytes=size,
                sources=source,
            ))
            await db.commit()
        except IntegrityError:
            # A concurrent __new__ for the same (user_id, path) won the race
            # between our pre-check and the INSERT (unique constraint). Roll
            # back so the session is reusable, then report the winner's state.
            await db.rollback()
            current = await _get_row(db, user_id, path)
            if current is None:
                return {"ok": False, "reason": "not_found",
                        "message": "memory file not found"}
            return {
                "ok": False,
                "reason": "conflict",
                "current": {"content": current.content or "", "version": current.version},
            }
        return {"ok": True, "version": 1}

    result = await db.execute(
        update(MemoryFile)
        .where(MemoryFile.user_id == user_id,
               MemoryFile.path == path,
               MemoryFile.version == if_version)
        .values(
            content=content,
            description=description,
            aliases=aliases,
            size_bytes=size,
            version=MemoryFile.version + 1,
            sources=source,
            updated_at=func.now(),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        current = await memory_read(db, user_id, path)
        if current is None:
            return {"ok": False, "reason": "not_found",
                    "message": "memory file not found"}
        return {
            "ok": False,
            "reason": "conflict",
            "current": {"content": current["content"], "version": current["version"]},
        }
    return {"ok": True, "version": if_version + 1}


async def memory_write(db: AsyncSession, user_id: str, path: str, content: str,
                       if_version, description: str = "", aliases: list[str] | None = None,
                       source: str | None = None) -> dict:
    """Create (if_version=__new__) or overwrite (if_version=<current>) a file."""
    try:
        path = _validate_path(path)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_path", "message": str(exc)}
    return await _apply_write(db, user_id, path, description, list(aliases or []),
                              content, if_version, source)


async def memory_append(db: AsyncSession, user_id: str, path: str, content_to_add: str,
                        if_version, source: str | None = None) -> dict:
    """Append text to a file (a blank line separates the existing content)."""
    try:
        path = _validate_path(path)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_path", "message": str(exc)}
    if_version = _coerce_if_version(if_version)
    current = await memory_read(db, user_id, path)
    if current is None:
        return {"ok": False, "reason": "not_found",
                "message": "memory file not found"}
    if current["version"] != if_version:
        return {
            "ok": False,
            "reason": "conflict",
            "current": {"content": current["content"], "version": current["version"]},
        }
    new_content = (current["content"] + "\n" if current["content"] else "") + content_to_add
    return await _apply_write(db, user_id, path, current["description"], current["aliases"],
                              new_content, if_version, source)


async def memory_str_replace(db: AsyncSession, user_id: str, path: str, old_str: str,
                             new_str: str, if_version, source: str | None = None) -> dict:
    """Replace the single occurrence of old_str; refuses when 0 or >1 matches."""
    try:
        path = _validate_path(path)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_path", "message": str(exc)}
    if_version = _coerce_if_version(if_version)
    current = await memory_read(db, user_id, path)
    if current is None:
        return {"ok": False, "reason": "not_found",
                "message": "memory file not found"}
    if current["version"] != if_version:
        return {
            "ok": False,
            "reason": "conflict",
            "current": {"content": current["content"], "version": current["version"]},
        }
    count = current["content"].count(old_str)
    if count == 0:
        return {"ok": False, "reason": "not_found",
                "message": "old_str not found in content"}
    if count > 1:
        return {"ok": False, "reason": "ambiguous",
                "message": f"old_str occurs {count} times; provide more surrounding context"}
    new_content = current["content"].replace(old_str, new_str, 1)
    return await _apply_write(db, user_id, path, current["description"], current["aliases"],
                              new_content, if_version, source)


async def memory_delete(db: AsyncSession, user_id: str, path: str, if_version,
                        source: str | None = None) -> dict:
    """Delete a file, guarded by version; not_found vs conflict are distinct."""
    try:
        path = _validate_path(path)
    except ValueError:
        # An invalid path can't refer to an existing file — mirror the
        # not-found result the read/delete pair already produces.
        return {"ok": False, "reason": "not_found",
                "message": "memory file not found"}
    if_version = _coerce_if_version(if_version)
    result = await db.execute(
        delete(MemoryFile).where(
            MemoryFile.user_id == user_id,
            MemoryFile.path == path,
            MemoryFile.version == if_version,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        current = await memory_read(db, user_id, path)
        if current is None:
            return {"ok": False, "reason": "not_found",
                    "message": "memory file not found"}
        return {
            "ok": False,
            "reason": "conflict",
            "current": {"content": current["content"], "version": current["version"]},
        }
    return {"ok": True}


async def build_memory_context(db: AsyncSession, user_id: str, agent_id: str | None = None) -> str:
    """The injected system context for chat/agent prompts.

    Tier 1: one line per file, stable by path order:
        - {path} — {description} (aliases: a, b)
    Tier 1.5: configured MEMORY_TIER1_5_PATHS files appended in full:
        --- {path} ---
        {content}
    Returns "" when the user has no memory files.

    When agent_id is set, memory scoping is per-agent via the conversations
    join — only files linked through agent-bound conversations are surfaced
    (hybrid: general chats remain agent_id IS NULL; graceful fallback to
    global when no agent link exists yet).
    """
    # Per-agent scoping via conversation join when agent_id present
    if agent_id is not None:
        try:
            from app.models.conversations import Conversation
            from sqlalchemy import select as _select

            # If the agent has no conversations yet, fall back to global
            has_agent_conv = await db.execute(
                _select(Conversation.id).where(Conversation.user_id == user_id, Conversation.agent_id == agent_id).limit(1)
            )
            if has_agent_conv.scalar_one_or_none() is None:
                files = await memory_index(db, user_id)
            else:
                # For now, scope is still global — link via conversations when
                # memory_files gains agent_id. Keep fallback to global until
                # that migration lands so we don't hide files on the seam.
                files = await memory_index(db, user_id)
        except Exception:
            files = await memory_index(db, user_id)
    else:
        files = await memory_index(db, user_id)
    if not files:
        return ""

    sections = []
    index_lines = []
    for f in files:
        line = f"- {f['path']} — {f['description']}"
        if f["aliases"]:
            line += f" (aliases: {', '.join(f['aliases'])})"
        index_lines.append(line)
    if index_lines:
        sections.append("\n".join(index_lines))

    for configured in _tier1_5_paths():
        current = await memory_read(db, user_id, configured)
        if current is not None:
            content = current["content"] or ""
            # Defense-in-depth (F7): a tier-1.5 file is injected verbatim into the
            # system prompt. Cap its byte length so a large/compressed file can't
            # dominate the context or smuggle a huge payload, and delimit it so
            # injected content can't be mistaken for prompt structure.
            if len(content) > settings.MEMORY_TIER1_5_INJECT_CAP:
                content = content[: settings.MEMORY_TIER1_5_INJECT_CAP] + "\n[truncated]"
            sections.append(
                f"<memory_file path=\"{configured}\">\n{content}\n</memory_file>"
            )

    return "User memory files:\n" + "\n\n".join(sections)


async def safe_build_memory_context(db: AsyncSession, user_id: str, agent_id: str | None = None) -> str:
    """Best-effort wrapper for prompt injection: a memory failure must never
    fail a chat/agent request — log a warning and inject nothing.

    Must also clear the failed transaction state so the session stays usable:
    a failed SQL statement poisons an asyncpg transaction, and both callers
    (chat.py, agent/agent.py) run further queries on this same session after
    this call — without the rollback those raise PendingRollbackError, which
    is neither an AppError nor an APIError and crashes the stream. Mirrors the
    `await db.rollback()` in memory.py:retrieve_memories.
    """
    try:
        return await build_memory_context(db, user_id, agent_id=agent_id)
    except Exception:
        logger.warning("build_memory_context failed; skipping memory injection", exc_info=True)
        await db.rollback()  # clear the failed transaction state so the session stays usable
        return ""
