"""Workspace package — re-exports the store singleton factory."""

from app.services.workspace.store import WorkspaceStore, get_workspace_store

__all__ = ["WorkspaceStore", "get_workspace_store"]
