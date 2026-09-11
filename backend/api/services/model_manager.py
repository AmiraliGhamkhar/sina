"""Model hub download manager (spec: download section + auto-configure).

Responsibilities:
- stream model files from HuggingFace into ``MS_MODELS__DIR/{model_id}/``,
  verifying exact size and (for LFS files) sha256 before anything is marked
  installed — a failed/partial download never looks ready;
- track per-model progress for the Models screen (bytes done/total, state);
- persist a small ``manifest.json`` bookkeeping file next to the artifacts;
- auto-configure in-process providers (shenava STT, PII NER redaction) —
  those resolve paths by convention the moment files land, so "download
  finished" IS "configured". External services (whisper.cpp, llama-server)
  get their exact restart arguments surfaced instead (see operator_note).

Invariants:
- model weights are never committed, never served raw, never logged;
- a download error never crashes the app — it lands in the model's status;
- only ONE download task per model at a time (409 on duplicate start).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from api.config import ModelsConfig
from api.services.model_catalog import CATALOG, ModelFileSpec, ModelSpec

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
_PARTIAL_DIR = ".partial"
_BLOCK = 1024 * 1024  # 1 MiB transfer blocks


class ModelDownloadError(RuntimeError):
    pass


@dataclass
class _FileProgress:
    local_name: str
    received: int = 0
    total: int = 0


@dataclass
class _ModelState:
    state: str = "not_installed"  # not_installed | downloading | installed | error
    files: dict[str, _FileProgress] = field(default_factory=dict)
    error: str | None = None
    started_at: float | None = None


class ModelManager:
    """Owns downloads + installed-state for the catalog. One instance per app.

    ``catalog`` is injectable for tests (tiny synthetic specs); production
    uses :data:`api.services.model_catalog.CATALOG`.
    """

    def __init__(
        self,
        config: ModelsConfig,
        *,
        catalog: tuple[ModelSpec, ...] = CATALOG,
        metrics: Any = None,
        audit: Any = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._catalog = tuple(catalog)
        self._root = Path(config.dir)
        self._metrics = metrics
        self._audit = audit
        self._tasks: dict[str, asyncio.Task] = {}
        self._live: dict[str, _ModelState] = {}
        self._client = client  # test seam: transport-mocked client
        self._owns_client = client is None

    # -- catalog ----------------------------------------------------------------

    def specs(self) -> tuple[ModelSpec, ...]:
        return self._catalog

    def spec(self, model_id: str) -> ModelSpec | None:
        for spec in self._catalog:
            if spec.id == model_id:
                return spec
        return None

    # -- status --------------------------------------------------------------

    def model_dir(self, spec: ModelSpec) -> Path:
        return self._root / spec.id

    def _manifest_path(self) -> Path:
        return self._root / MANIFEST_NAME

    def _read_manifest(self) -> dict:
        try:
            import json

            data = json.loads(self._manifest_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_manifest(self, data: dict) -> None:
        import json

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._manifest_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._manifest_path())

    def _files_installed(self, spec: ModelSpec) -> bool:
        """All files present with the exact cataloged size (cheap check — the
        sha256 was verified at download time; manual file drops are accepted
        when sizes match)."""
        for f in spec.files:
            p = self.model_dir(spec) / f.local_name
            try:
                if p.stat().st_size != f.size_bytes:
                    return False
            except OSError:
                return False
        return True

    def status(self, spec: ModelSpec) -> dict:
        live = self._live.get(spec.id)
        if live is not None:
            received = sum(fp.received for fp in live.files.values())
            total = sum(fp.total for fp in live.files.values())
            return {
                "state": live.state,
                "received_bytes": received,
                "total_bytes": total or spec.total_bytes,
                "progress": (received / total) if total else None,
                "error": live.error,
                "current_file": next(
                    (fp.local_name for fp in live.files.values() if fp.received < fp.total),
                    None,
                ),
                "file_progress": {fp.local_name: fp.received for fp in live.files.values()},
            }
        if self._files_installed(spec):
            manifest = self._read_manifest().get("models", {}).get(spec.id, {})
            return {
                "state": "installed",
                "received_bytes": spec.total_bytes,
                "total_bytes": spec.total_bytes,
                "progress": 1.0,
                "error": None,
                "current_file": None,
                "installed_at": manifest.get("installed_at"),
                "file_progress": {f.local_name: f.size_bytes for f in spec.files},
            }
        return {
            "state": "not_installed",
            "received_bytes": 0,
            "total_bytes": spec.total_bytes,
            "progress": 0.0,
            "error": None,
            "current_file": None,
            "installed_at": None,
            "file_progress": {f.local_name: 0 for f in spec.files},
        }

    def list_statuses(self) -> list[dict]:
        return [self.status(spec) for spec in self._catalog]

    # -- download ------------------------------------------------------------

    def _url(self, f: ModelFileSpec) -> str:
        base = self._config.base_url.rstrip("/")
        return f"{base}/{f.repo}/resolve/main/{quote(f.filename)}"

    async def start_download(self, model_id: str) -> bool:
        """Start (or resume-wait on) a download. True if this call started it."""
        spec = self.spec(model_id)
        if spec is None:
            raise ModelDownloadError(f"unknown model id '{model_id}'")
        existing = self._tasks.get(spec.id)
        if existing is not None and not existing.done():
            return False  # already running
        if self._files_installed(spec):
            return False  # nothing to do

        # disk-space guard (best effort on exotic filesystems)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self._root).free
            if free < spec.total_bytes + self._config.min_free_disk_bytes:
                raise ModelDownloadError(
                    f"not enough disk space: need {spec.total_bytes} + "
                    f"{self._config.min_free_disk_bytes} free, have {free}"
                )
        except OSError:
            logger.debug("disk space check failed", exc_info=True)

        state = _ModelState(
            state="downloading",
            files={f.local_name: _FileProgress(f.local_name, 0, f.size_bytes) for f in spec.files},
            started_at=time.monotonic(),
        )
        self._live[spec.id] = state
        self._count(spec.id, "started")
        task = asyncio.create_task(self._download(spec, state), name=f"model-download:{spec.id}")
        self._tasks[spec.id] = task
        return True

    async def _download(self, spec: ModelSpec, state: _ModelState) -> None:
        try:
            client = self._client or httpx.AsyncClient(
                timeout=self._config.download_timeout_s, follow_redirects=True
            )
            try:
                target_dir = self.model_dir(spec)
                target_dir.mkdir(parents=True, exist_ok=True)
                partial_dir = target_dir / _PARTIAL_DIR
                partial_dir.mkdir(parents=True, exist_ok=True)
                for f in spec.files:
                    await self._download_file(spec, f, client, state, partial_dir, target_dir)
            finally:
                if self._owns_client:
                    await client.aclose()
            self._mark_installed(spec)
            # success → drop live state so status (incl. installed_at) comes
            # from the manifest; error/cancelled live state is kept for display
            self._live.pop(spec.id, None)
            self._count(spec.id, "completed")
            logger.info("model downloaded: %s (%d bytes)", spec.id, spec.total_bytes)
            await self._audit_event(spec, "MODEL_DOWNLOADED")
        except asyncio.CancelledError:
            self._cleanup_partial(spec)
            state.state = "not_installed"
            state.error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced via status, never fatal
            self._cleanup_partial(spec)
            state.state = "error"
            state.error = str(exc)
            self._count(spec.id, "failed")
            logger.warning("model download failed: %s (%s)", spec.id, exc)

    async def _download_file(
        self,
        spec: ModelSpec,
        f: ModelFileSpec,
        client: httpx.AsyncClient,
        state: _ModelState,
        partial_dir: Path,
        target_dir: Path,
    ) -> None:
        progress = state.files[f.local_name]
        url = self._url(f)
        tmp = partial_dir / f.local_name
        digest = hashlib.sha256()
        received = 0
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise ModelDownloadError(
                    f"HTTP {resp.status_code} fetching {f.filename} from {f.repo}"
                )
            fh = await asyncio.to_thread(open, tmp, "wb")
            try:
                async for block in resp.aiter_bytes(_BLOCK):
                    await asyncio.to_thread(fh.write, block)
                    digest.update(block)
                    received += len(block)
                    progress.received = received
            finally:
                await asyncio.to_thread(fh.close)
        if received != f.size_bytes:
            raise ModelDownloadError(
                f"size mismatch for {f.filename}: expected {f.size_bytes}, got {received}"
            )
        if f.sha256 and self._config.verify_sha256:
            actual = digest.hexdigest()
            if actual != f.sha256:
                raise ModelDownloadError(f"sha256 mismatch for {f.filename}: {actual}")
        final = target_dir / f.local_name
        os.replace(tmp, final)

    def _mark_installed(self, spec: ModelSpec) -> None:
        data = self._read_manifest()
        models = data.setdefault("models", {})
        models[spec.id] = {
            "installed_at": datetime.now(UTC).isoformat(),
            "source_repo": spec.source_repo,
            "files": {
                f.local_name: {"size": f.size_bytes, "sha256": f.sha256} for f in spec.files
            },
        }
        self._write_manifest(data)

    def _cleanup_partial(self, spec: ModelSpec) -> None:
        shutil.rmtree(self.model_dir(spec) / _PARTIAL_DIR, ignore_errors=True)

    # -- delete ----------------------------------------------------------------

    async def delete(self, model_id: str) -> bool:
        """Remove artifacts (cancels an in-flight download first).

        Returns True when something was removed; False when nothing was there.
        Raises ModelDownloadError for unknown ids or in-flight cancel failure.
        """
        spec = self.spec(model_id)
        if spec is None:
            raise ModelDownloadError(f"unknown model id '{model_id}'")
        task = self._tasks.get(spec.id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best effort
                pass
        removed = False
        d = self.model_dir(spec)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed = True
        data = self._read_manifest()
        if spec.id in data.get("models", {}):
            del data["models"][spec.id]
            self._write_manifest(data)
            removed = True
        self._live.pop(spec.id, None)
        if removed:
            self._count(spec.id, "deleted")
            logger.info("model deleted: %s", spec.id)
            await self._audit_event(spec, "MODEL_DELETED")
        return removed

    # -- helpers ---------------------------------------------------------------

    def _count(self, model_id: str, status: str) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.incr(f"model_downloads:{model_id}:{status}")
        except Exception:  # pragma: no cover — metrics must never break flows
            logger.debug("model metric update failed", exc_info=True)

    async def _audit_event(self, spec: ModelSpec, event: str) -> None:
        if self._audit is None:
            return
        try:
            # metadata only — model ids/repos, never file contents
            self._audit.emit(event, model_id=spec.id, repo=spec.source_repo)
        except Exception:  # pragma: no cover — audit is best-effort here
            logger.debug("model audit event failed", exc_info=True)

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._owns_client and self._client is not None:
            await self._client.aclose()
