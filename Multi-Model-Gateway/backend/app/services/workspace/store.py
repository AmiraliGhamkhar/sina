"""WorkspaceStore — the only code that touches the workspaces volume.

Per-user-per-agent git-backed folders on the named volume `workspaces:/workspaces`
at `workspaces/{user_id}/{agent_id}`. Every mutating operation is serialized by a
per-workspace asyncio.Lock (one bash at a time, ADR-0002 Q12) and leaves both a git
commit and a file_edits audit row (ADR-0003). Refactored to a deep module (#2):
three domain-named methods share one private pipeline (_resolveInside → _check...
→ fs → git → DB) and undo is deterministic via commit_sha.

External seam:
  read_file(user_id, agent_id, path) → {content, lines:[{n, hash, text}]}
  list_files(user_id, agent_id, path=".") → [rel paths]
  write_file / apply_patch / edit_lines  (domain-named, shared internals)
  undo(user_id, agent_id, edit_id, db) — commit_sha-driven, no grep
  with_workspace_lock(user_id, agent_id) — so Sandbox shares the same lock

Internal seams (private): _resolveInside, _checkQuota, _audit, git helpers.
"""

import asyncio
import hashlib
import os
import pathlib
import subprocess
from typing import Optional
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.db import AsyncSessionLocal
from app.models.file_edits import FileEdit
import uuid as _uuid


def _workspace_root() -> pathlib.Path:
    return pathlib.Path(settings.WORKSPACE_ROOT)


def _workspace_path(user_id: str, agent_id: str) -> pathlib.Path:
    return _workspace_root() / str(user_id) / str(agent_id)


# ── O_NOFOLLOW text I/O (F8): a model-controlled `bash` can race the file tools
# by swapping a workspace file for a symlink between _resolveInside's check and the
# actual open()/read. O_NOFOLLOW refuses a final-component symlink at open time.
#   - POSIX: guaranteed. The resolve in _resolveInside already rejects symlink
#     *escape* (a link pointing outside the workspace) and O_NOFOLLOW closes the
#     final-component TOCTOU window.
#   - Windows: os.O_NOFOLLOW is absent (symlinks are rare in workspace tasks and
#     the compose volume is Linux); we degrade to a plain open there.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _read_text_fp(fp: pathlib.Path) -> str:
    """Read a text file refusing a final-component symlink (O_NOFOLLOW)."""
    if _NOFOLLOW:
        fd = os.open(str(fp), os.O_RDONLY | _NOFOLLOW)
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="surrogateescape") as f:
                return f.read()
        except Exception:
            os.close(fd)
            raise
    return fp.read_text(encoding="utf-8", errors="surrogateescape")


def _write_text_fp(fp: pathlib.Path, content: str) -> None:
    """Write a text file refusing a final-component symlink (O_NOFOLLOW)."""
    if _NOFOLLOW:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW
        fd = os.open(str(fp), flags, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            os.close(fd)
            raise
    else:
        fp.write_text(content, encoding="utf-8")


def _line_hash(line: str) -> str:
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]


def _file_hashes(content: str) -> list[str]:
    return [_line_hash(l) for l in content.splitlines()]


# ── Single path-security helper (Q4): one place that can return 422 ─────────

def _resolveInside(workspace_root: pathlib.Path, rel: str) -> pathlib.Path:
    """Resolve `rel` inside `workspace_root` and return the absolute Path.

    Validates: empty → ".", absolute → 422, segments containing '..' or '.' or
    empty or control chars → 422, control chars anywhere → 422, and the final
    resolved path must stay under workspace_root (symlink escape) → 422.
    Pure (no FS mutation), testable without a workspace.
    """
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        raise AppError(status_code=422, detail="invalid path characters")
    if not rel or rel in (".", "./"):
        rel = "."
    if rel == ".":
        return workspace_root
    p = pathlib.PurePosixPath(rel)
    if p.is_absolute():
        raise AppError(status_code=422, detail="path must be relative")
    for part in p.parts:
        if part in ("..", ".") or not part:
            raise AppError(status_code=422, detail="path may not contain '..' or '.' segments")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise AppError(status_code=422, detail="invalid path characters")
    abs_path = workspace_root / rel
    # Symlink/escape guard: resolved path must stay under resolved root
    try:
        abs_path.resolve().relative_to(workspace_root.resolve())
    except (ValueError, OSError):
        raise AppError(status_code=422, detail="path escapes workspace")
    # Also guard the parent for create paths that don't exist yet
    try:
        abs_path.resolve().parent.relative_to(workspace_root.resolve())
    except (ValueError, OSError):
        raise AppError(status_code=422, detail="path escapes workspace")
    return abs_path


# ── Patch-body validation: the diff's own header paths are a second trust boundary ──

def _patch_header_paths(patch: str) -> list[str]:
    """Extract the target path from every ---/+++ line in a unified diff.

    Handles the optional leading `--- a/...` (git-style) and timestamp suffixes
    (`--- a/f.txt\t2026-01-01 ...`). Returns paths verbatim (with the a//b/
    prefix still on) — stripping is the caller's job.
    """
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw and raw != "/dev/null":
                paths.append(raw)
            elif raw == "/dev/null":
                continue  # file-addition / removal hunks target only one side
    return paths


def _validate_patch_targets(workspace_root: pathlib.Path, patch: str) -> None:
    """Run every diff-header target through the same rules as `_resolveInside`
    (after stripping the git a//b/ prefix and the -p1 strip level).

    Unified-diff headers take the form [ab]/path — the same shape `-p1` strips.
    /dev/null (add/remove hunks) is skipped. Raises 422 on any target that
    would land outside the workspace: absolute paths, `..` segments, or a
    symlink planted inside the workspace pointing out.
    """
    for raw in _patch_header_paths(patch):
        target = raw
        if target.startswith(("a/", "b/")):
            target = target[2:]
        # -p1 also strips a leading ./ if present (a/./f.txt → f.txt)
        if target.startswith("./"):
            target = target[2:]
        if not target:
            # e.g. header is `--- a/` — degenerate; let the -p1 semantics decide
            continue
        _resolveInside(workspace_root, target)


def _assertNotDirectory(rel: str) -> None:
    if rel == "." or rel in (".", "./"):
        raise AppError(status_code=422, detail="path is a directory")


class WorkspaceStore:
    """Singleton-ish store — per-workspace locks live here. The only holder of the workspaces volume."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, user_id: str, agent_id: str) -> asyncio.Lock:
        key = (str(user_id), str(agent_id))
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    @asynccontextmanager
    async def with_workspace_lock(self, user_id: str, agent_id: str) -> AsyncIterator[None]:
        """Public lock handle so Sandbox.exec can share the workspace lock (Q3)."""
        async with self._lock(user_id, agent_id):
            yield

    def ensure_workspace(self, user_id: str, agent_id: str) -> pathlib.Path:
        wp = _workspace_path(user_id, agent_id)
        wp.mkdir(parents=True, exist_ok=True)
        git_dir = wp / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init", "-q", str(wp)], check=False)
            subprocess.run(["git", "-C", str(wp), "config", "user.name", "llm-gateway"], check=False)
            subprocess.run(["git", "-C", str(wp), "config", "user.email", "agent@llm-gateway"], check=False)
            subprocess.run(["git", "-C", str(wp), "commit", "--allow-empty", "-m", "init"], check=False)
        return wp

    # Model-controlled bash can write .git/hooks/* (or set core.fsmonitor /
    # credential.helper) in ANY workspace on the shared volume (sweep H4).
    # These flags neutralize that: hooksPath points at an empty dir (verified:
    # with it set, a planted pre-commit hook does NOT run), fsmonitor and
    # credential helpers are disabled, and the repo's own .git/config cannot
    # override command-line -c settings (they always win in git).
    _GIT_HARDEN = [
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "credential.helper=",
    ]

    def _git(self, wp: pathlib.Path, *args: str, **kw) -> subprocess.CompletedProcess:
        """Run a hardened git command inside the workspace."""
        return subprocess.run(["git", *self._GIT_HARDEN, "-C", str(wp), *args], **kw)

    def _commit(self, wp: pathlib.Path, message: str) -> str | None:
        """Commit and return the new HEAD sha (or None on failure)."""
        self._git(wp, "add", "-A", check=False)
        self._git(wp, "commit", "--allow-empty", "-m", message, check=False)
        proc = self._git(wp, "rev-parse", "HEAD", capture_output=True, text=True)
        sha = proc.stdout.strip() if proc.returncode == 0 else None
        return sha if sha and len(sha) >= 7 else None

    def _revert_commit(self, wp: pathlib.Path, sha: str) -> bool:
        proc = self._git(wp, "revert", "--no-edit", sha, capture_output=True, text=True)
        return proc.returncode == 0

    # ── Reads ────────────────────────────────────────────────────────────

    def read_file(self, user_id: str, agent_id: str, path: str) -> dict:
        wp = self.ensure_workspace(user_id, agent_id)
        rel = path if path not in (None, "") else "."
        # Normalize via helper; "." is a directory for reads
        if not rel or rel in (".", "./"):
            raise AppError(status_code=422, detail="path is a directory")
        # Validate + escape in one place
        fp = _resolveInside(wp, rel)
        if not fp.is_file():
            raise AppError(status_code=404, detail="file not found")
        content = _read_text_fp(fp)
        lines = content.splitlines()
        return {
            "content": content,
            "lines": [{"n": i + 1, "hash": _line_hash(l), "text": l} for i, l in enumerate(lines)],
        }

    def list_files(self, user_id: str, agent_id: str, path: str = ".") -> list[str]:
        wp = self.ensure_workspace(user_id, agent_id)
        rel = path if path not in (None, "") else "."
        if not rel or rel in (".", "./"):
            rel = "."
        # Listing "." is allowed; otherwise validate
        if rel == ".":
            base = wp
        else:
            base = _resolveInside(wp, rel)
        if not base.exists():
            return []
        if base.is_file():
            return [rel]
        out: list[str] = []
        for p in base.rglob("*"):
            if ".git" in p.parts:
                continue
            if p.is_file():
                try:
                    rel_p = p.relative_to(wp).as_posix()
                    out.append(rel_p)
                except ValueError:
                    continue
        return sorted(out)

    def du_mb(self, user_id: str, agent_id: str) -> float:
        wp = self.ensure_workspace(user_id, agent_id)
        total = 0
        # Count EVERY regular file under the workspace, including .git (F9): git
        # objects from `git clone`/`git add` were previously excluded, letting
        # bash grow the workspace far past the quota. Symlinks are not followed
        # (rglob + is_file skips them), so a symlink can't inflate the count.
        for p in wp.rglob("*"):
            if not p.is_file():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return total / (1024 * 1024)

    def _check_quota(self, user_id: str, agent_id: str) -> None:
        if self.du_mb(user_id, agent_id) > self.quota_mb():
            raise AppError(status_code=413, detail="workspace quota exceeded")

    def quota_mb(self) -> float:
        """The workspace disk quota in MiB (public — the bash tool reads it)."""
        return float(settings.SANDBOX_DISK_QUOTA_MB)

    # ── Mutating helpers (the hidden pipeline) ───────────────────────────

    async def _audit(
        self,
        user_id: str,
        agent_id: str | None,
        store: str,
        path: str,
        patch: str,
        before_hash: str | None,
        after_hash: str | None,
        tool_call_id: str | None,
        db: AsyncSession | None,
        commit_sha: str | None = None,
    ) -> str:
        edit_id = str(_uuid.uuid4())
        session = db
        owned = False
        if session is None:
            session = AsyncSessionLocal()
            owned = True
        try:
            row = FileEdit(
                id=edit_id,
                user_id=user_id,
                agent_id=agent_id,
                store=store,
                path=path,
                patch=patch[:20000],
                before_hash=before_hash,
                after_hash=after_hash,
                tool_call_id=tool_call_id,
                commit_sha=commit_sha,
            )
            session.add(row)
            await session.commit()
            # Fill commit_sha on the already-inserted row when called post-commit
            if commit_sha and getattr(row, "commit_sha", None) is None:
                pass  # already set above
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
        finally:
            if owned:
                try:
                    await session.close()
                except Exception:
                    pass
        return edit_id

    async def _finalize_edit(
        self,
        wp: pathlib.Path,
        rel: str,
        kind_word: str,
        user_id: str,
        agent_id: str,
        patch: str,
        before_hash: str | None,
        after_hash: str | None,
        tool_call_id: str | None,
        db: AsyncSession | None,
    ) -> dict:
        """Git commit first (Q5 A: fs→git→DB), then DB insert with sha. On DB failure, reset git.

        The commit message's trailing token is the edit_id, so git log --grep and
        commit_sha stay consistent. We generate edit_id first, commit with it,
        then insert the row with the resulting sha — git sha never changes after.
        """
        edit_id = str(_uuid.uuid4())
        commit_msg = f"{kind_word} {rel} {edit_id}"
        sha = self._commit(wp, commit_msg)
        session = db
        owned = False
        if session is None:
            session = AsyncSessionLocal()
            owned = True
        try:
            row = FileEdit(
                id=edit_id,
                user_id=user_id,
                agent_id=agent_id,
                store="workspace",
                path=rel,
                patch=patch[:20000],
                before_hash=before_hash,
                after_hash=after_hash,
                tool_call_id=tool_call_id,
                commit_sha=sha,
            )
            session.add(row)
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            if sha:
                self._git(wp, "reset", "--hard", "HEAD~1", check=False)
            raise
        finally:
            if owned:
                try:
                    await session.close()
                except Exception:
                    pass
        return {"edit_id": edit_id, "path": rel, "commit_sha": sha}

    # ── Public mutating methods (domain-named; share the pipeline) ───────

    async def write_file(
        self,
        user_id: str,
        agent_id: str,
        path: str,
        content: str,
        expected_hashes: Optional[list[str]] = None,
        tool_call_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        if not path or path in (".", "./"):
            raise AppError(status_code=422, detail="path is a directory")
        async with self._lock(user_id, agent_id):
            wp = self.ensure_workspace(user_id, agent_id)
            fp = _resolveInside(wp, path)
            rel = path
            if expected_hashes is not None and fp.is_file():
                cur = _read_text_fp(fp)
                cur_hashes = _file_hashes(cur)
                # Conflict when the caller's expected prefix doesn't match the
                # current file. The old `A and B` let stale hashes slip through
                # whenever the hashes happened to exist elsewhere in the file
                # (set-difference empty) even though the prefix didn't match —
                # e.g. all-identical lines. Exact-prefix comparison only.
                if expected_hashes != cur_hashes[: len(expected_hashes)]:
                    raise AppError(status_code=409, detail="file changed, re-read")
            self._check_quota(user_id, agent_id)
            fp.parent.mkdir(parents=True, exist_ok=True)
            before = _read_text_fp(fp) if fp.is_file() else ""
            _write_text_fp(fp, content)
            after = content
            before_hash = hashlib.sha1(before.encode()).hexdigest()[:8] if before else None
            after_hash = hashlib.sha1(after.encode()).hexdigest()[:8]
            patch = f"write {rel}"
            return await self._finalize_edit(wp, rel, "write", user_id, agent_id, patch, before_hash, after_hash, tool_call_id, db)

    async def apply_patch(
        self,
        user_id: str,
        agent_id: str,
        path: str,
        patch: str,
        expected_hashes: Optional[list[str]] = None,
        tool_call_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        if not path or path in (".", "./"):
            raise AppError(status_code=422, detail="path is a directory")
        async with self._lock(user_id, agent_id):
            wp = self.ensure_workspace(user_id, agent_id)
            if not patch or ("---" not in patch and "@@" not in patch) or ("+++" not in patch and "@@" not in patch):
                if "@@" not in patch:
                    raise AppError(status_code=422, detail="patch must be a unified diff")
            fp = _resolveInside(wp, path)
            # The `path` argument above is not the only trust boundary: the diff
            # body's own ---/+++ header paths are what `patch -p1` actually
            # opens, with cwd=wp. Every one of them must land inside the
            # workspace too, or a crafted diff writes outside the "one 422
            # seam" (either directly on builds whose patch doesn't refuse `..`,
            # or via a symlink planted inside the workspace by another tool).
            _validate_patch_targets(wp, patch)
            rel = path
            if expected_hashes is not None and fp.is_file():
                cur = _read_text_fp(fp)
                cur_hashes = _file_hashes(cur)
                if set(expected_hashes) - set(cur_hashes):
                    raise AppError(status_code=409, detail="file changed, re-read")
            self._check_quota(user_id, agent_id)
            before = _read_text_fp(fp) if fp.is_file() else ""
            try:
                proc = subprocess.run(
                    ["patch", "-p1", "--forward", "--batch"],
                    input=patch.encode(),
                    cwd=str(wp),
                    capture_output=True,
                    timeout=10,
                )
                if proc.returncode not in (0,):
                    raise AppError(status_code=422, detail=f"patch failed: {proc.stderr.decode(errors='ignore')[:400]}")
            except FileNotFoundError:
                raise AppError(status_code=422, detail="patch tool unavailable in this environment")
            after = _read_text_fp(fp) if fp.is_file() else ""
            before_hash = hashlib.sha1(before.encode()).hexdigest()[:8] if before else None
            after_hash = hashlib.sha1(after.encode()).hexdigest()[:8] if after else None
            return await self._finalize_edit(wp, rel, "patch", user_id, agent_id, patch, before_hash, after_hash, tool_call_id, db)

    async def edit_lines(
        self,
        user_id: str,
        agent_id: str,
        path: str,
        old_hashes: list[str],
        new_content: str,
        tool_call_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        if not path or path in (".", "./"):
            raise AppError(status_code=422, detail="path is a directory")
        if not old_hashes:
            raise AppError(status_code=422, detail="old_hashes required")
        async with self._lock(user_id, agent_id):
            wp = self.ensure_workspace(user_id, agent_id)
            fp = _resolveInside(wp, path)
            rel = path
            if not fp.is_file():
                raise AppError(status_code=404, detail="file not found")
            cur = _read_text_fp(fp)
            lines = cur.splitlines()
            cur_hashes = [_line_hash(l) for l in lines]
            if not set(old_hashes).issubset(set(cur_hashes)):
                raise AppError(status_code=409, detail="file changed, re-read")
            new_lines = new_content.splitlines()
            hash_to_idx: dict[str, int] = {}
            for i, h in enumerate(cur_hashes):
                if h not in hash_to_idx:
                    hash_to_idx[h] = i
            idxs = sorted(hash_to_idx[h] for h in old_hashes if h in hash_to_idx)
            if not idxs:
                raise AppError(status_code=404, detail="hashed lines not found")
            start, end = idxs[0], idxs[-1] + 1
            before = cur
            after_lines = lines[:start] + new_lines + lines[end:]
            after = "\n".join(after_lines)
            if cur.endswith("\n"):
                after += "\n"
            self._check_quota(user_id, agent_id)
            _write_text_fp(fp, after)
            before_hash = hashlib.sha1(before.encode()).hexdigest()[:8]
            after_hash = hashlib.sha1(after.encode()).hexdigest()[:8]
            patch = f"edit_lines {rel} {','.join(old_hashes[:4])}"
            return await self._finalize_edit(wp, rel, "edit_lines", user_id, agent_id, patch, before_hash, after_hash, tool_call_id, db)

    async def _record_edit(
        self,
        user_id: str,
        agent_id: str | None,
        store: str,
        path: str,
        patch: str,
        before_hash: str | None,
        after_hash: str | None,
        tool_call_id: str | None,
        db: AsyncSession | None,
    ) -> str:
        # Kept for backward compat (memory_files etc.); new workspace writes use _finalize_edit.
        return await self._audit(user_id, agent_id, store, path, patch, before_hash, after_hash, tool_call_id, db)

    async def undo(self, user_id: str, agent_id: str, edit_id: str, db: AsyncSession) -> dict:
        async with self._lock(user_id, agent_id):
            result = await db.execute(select(FileEdit).where(FileEdit.id == edit_id))
            row = result.scalar_one_or_none()
            if row is None:
                raise AppError(status_code=404, detail="edit not found")
            if str(row.user_id) != str(user_id):
                raise AppError(status_code=403, detail="unauthorised")
            wp = self.ensure_workspace(user_id, agent_id)
            sha = getattr(row, "commit_sha", None)
            commit = None
            if sha:
                # Verify the sha exists in this repo before reverting
                proc = self._git(wp, "cat-file", "-e", sha, capture_output=True)
                if proc.returncode == 0:
                    commit = sha
            if not commit:
                # Fallback for old rows (commit_sha NULL): grep by message (one release)
                proc = subprocess.run(
                    ["git", "-C", str(wp), "log", "--all", "--oneline", "--grep", edit_id],
                    capture_output=True,
                    text=True,
                )
                commit = (proc.stdout.strip().split("\n")[0].split()[0] if proc.stdout.strip() else None)
            if commit:
                ok = self._revert_commit(wp, commit)
                if not ok:
                    raise AppError(status_code=422, detail="undo conflict — workspace has later edits that block revert")
                # Record undo as new audit row (git → DB, no reset needed here — revert is the fs change)
                sha2 = None
                # The revert already created a commit; capture its sha
                proc2 = self._git(wp, "rev-parse", "HEAD", capture_output=True, text=True)
                sha2 = proc2.stdout.strip() if proc2.returncode == 0 else None
                new_id = str(_uuid.uuid4())
                try:
                    undo_row = FileEdit(
                        id=new_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        store=row.store,
                        path=row.path,
                        patch=f"undo {edit_id}",
                        before_hash=row.after_hash,
                        after_hash=row.before_hash,
                        tool_call_id=None,
                        commit_sha=sha2,
                    )
                    db.add(undo_row)
                    await db.commit()
                except Exception:
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    raise AppError(status_code=500, detail="undo audit failed")
                return {"edit_id": new_id, "undone": edit_id, "commit_sha": sha2}
            raise AppError(status_code=422, detail="cannot locate commit for edit")


_workspace_store: Optional[WorkspaceStore] = None


def get_workspace_store() -> WorkspaceStore:
    global _workspace_store
    if _workspace_store is None:
        _workspace_store = WorkspaceStore()
    return _workspace_store
