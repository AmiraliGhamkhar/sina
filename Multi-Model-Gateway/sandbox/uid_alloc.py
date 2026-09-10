"""Per-tenant UID allocation for the sandbox (F1/C2 confinement).

Confines `bash` to its own workspace on a shared volume the classic way:
each (user_id, agent_id) gets a distinct OS UID, its workspace directory is
`chmod 700`'d to that UID, and bash runs as that UID. A different tenant's
workspace is owned by a different UID with 700 (no group/other bits), so an
unprivileged bash cannot traverse into it — even though it's on the same volume
and even though the controller uid (root) sees everything.

Allocation is "owner-as-registry": the workspace directory's OWNER IS the record.
The controller stat()s the workspace, reads st_uid, and if it's already one of
ours (within the tenant range) it uses it; otherwise it allocates the next free
UID and chowns the tree. No side table to keep in sync, collision-free (each UID
is handed out once), and self-healing if the dir is recreated.

The kernel clears ALL of the child's capabilities the moment it drops to a
nonzero UID, so the bash child is fully unprivileged regardless of what the
controller holds.

This module is pure (no fastapi / no side effects) so it's unit-testable offline.
"""
from __future__ import annotations

import os
import pathlib

# Range of tenant UIDs. Must sit below nobody (65534) and above the system/range
# Linux uses for dynamic users (the cluster tools / systemd range 1000-9999 on
# DebOps, or the 999-9999 bin on slim). 10000-65000 is comfortably clear of both
# while staying inside the 16-bit UID space.
UID_MIN = 10000
UID_MAX = 65000


def is_tenant_uid(uid: int) -> bool:
    return UID_MIN <= uid <= UID_MAX


def next_free_uid(used: set[int]) -> int:
    """The smallest free tenant UID not in `used`. Raises if the range is full."""
    for candidate in range(UID_MIN, UID_MAX + 1):
        if candidate not in used:
            return candidate
    raise RuntimeError("tenant UID range exhausted")


def used_uids(workspace_root: pathlib.Path) -> set[int]:
    """All tenant UIDs currently in use, discoverable from the filesystem.

    Any workspace dir at <root>/<user>/<agent> whose owner is in the tenant range
    counts as used. Also includes system UIDs already in that range from
    /etc/passwd, so we never collide with a system account.
    """
    used: set[int] = set()
    # System accounts in the range (avoid colliding with a real passwd uid).
    try:
        import pwd

        for entry in pwd.getpwall():
            if is_tenant_uid(entry.pw_uid):
                used.add(entry.pw_uid)
    except (ImportError, Exception):  # noqa: BLE001 — host without pwd (Windows)
        pass
    # Tenants materialized in the volume.
    try:
        for user_dir in workspace_root.iterdir():
            if not user_dir.is_dir():
                continue
            for agent_dir in user_dir.iterdir():
                try:
                    uid = agent_dir.stat().st_uid
                except OSError:
                    continue
                if is_tenant_uid(uid):
                    used.add(uid)
    except (FileNotFoundError, OSError):
        pass
    return used


def tenant_ids(workspace: pathlib.Path, workspace_root: pathlib.Path) -> tuple[int, int]:
    """Return (uid, gid) for the tenant owning `workspace`, allocating as needed.

    Owner-as-registry: if the dir is already owned by a tenant UID, reuse it.
    Otherwise allocate the next free UID and chown the tree. Returns (uid, uid) —
    each tenant gets its own group too, so a shared group bit can't leak.
    """
    try:
        st = workspace.stat()
        if is_tenant_uid(st.st_uid):
            return st.st_uid, st.st_gid if is_tenant_uid(st.st_gid) else st.st_uid
    except FileNotFoundError:
        pass

    uid = next_free_uid(used_uids(workspace_root))
    chown_recursive(workspace, uid, uid)
    return uid, uid


def chown_recursive(workspace: pathlib.Path, uid: int, gid: int) -> None:
    """chown -R the workspace tree to (uid, gid) and chmod 700 the top dir.

    Requires CAP_CHOWN (the controller has it via the compose cap_add allow-list).
    Backend-as-root runs the file tools and ignores ownership, so this is safe to
    run even while the backend is mid-commit.
    """
    os.chown(workspace, uid, gid)
    for p in sorted(workspace.rglob("*"), reverse=True):  # children first
        try:
            os.chown(p, uid, gid)
        except OSError:
            continue
    os.chmod(workspace, 0o700)
