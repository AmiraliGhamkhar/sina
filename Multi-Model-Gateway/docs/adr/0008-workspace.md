# 0008 — Workspace: one deep module for path security and edit/undo

`services/workspace/store.py` is the only code that touches the workspaces volume.
It owns the git-backed per-user-per-agent folder, the per-workspace `asyncio.Lock`,
and every path-security decision. Three domain-named public methods
(`write_file`, `apply_patch`, `edit_lines`) share one private pipeline
(`_resolveInside → _checkQuota → fs → git commit → _finalize_edit`), and undo is
deterministic by `commit_sha`.

## The two seams it owns

1. **Path security** — `_resolveInside(workspace_root, rel)` is the *single*
   helper that can return 422. It rejects absolute paths, `..`/`.` segments,
   empty segments, control characters, and symlink escapes (the resolved path and
   the resolved parent must stay under the resolved root). Pure (no FS mutation),
   so it's unit-testable without a workspace. `._assertNotDirectory` covers the
   "can't write to `.`" case.
2. **Write pipeline + undo** — every mutating operation is serialized by the
   per-workspace lock (so `bash` shares the same lock via
   `with_workspace_lock`), then does `fs → git commit → capture HEAD sha → insert
   `file_edits` row (with `commit_sha`), and `git reset --hard HEAD~1` on a DB
   failure`. Undo looks up the `file_edits` row, resolves `commit_sha`, and
   `git revert --no-edit <sha>` (with a `git log --grep <edit_id>` fallback for
   pre-`commit_sha` rows), then inserts a new audit row.

## Why one module (vs. scattering path checks)

The deep-module position: **much behavior, small interface.** The public surface is
`read_file`, `list_files`, `du_mb`, `write_file`, `apply_patch`, `edit_lines`,
`undo`, and `with_workspace_lock`; everything about *how* (the guard order, the
hash-conflict rules, the git ordering, the quota) is hidden. Two independent
consumers — the `file_*` tools and the `bash` tool — justify the seam, and they
must agree on path rules and the lock.

The deletion test is the tell: deleting this module scatters the escape/`..`
guard back into every file and bash tool, and the `file_edits` audit + git
history lose their single owner (so concurrent tools could interleave commits and
make undo non-linear).

## Alternatives considered

- **Per-tool path validation** — rejected; `..`/symlink-escape rules are a single
  invariant that must hold identically for `write_file`, `apply_patch`,
  `edit_lines`, and `read_file`. Duplicating it invites a bypass.
- **No git / pure-DB edits** — rejected; git gives a trustworthy mechanical revert
  without reimplementing patch logic (see ADR-0003), and the `git reset --hard
  HEAD~1` on DB failure keeps the audit row and the working tree consistent.

Consequence (fixed in a follow-up): the store raised `AppError(status_code=422,
...)` in 27 places but `AppError.__init__` only accepted `detail`, so every
rejection mapped to a 500. `AppError` now accepts an optional `status_code`
(backward compatible), and `backend/tests/test_workspace_store.py` locks down the
`_resolveInside` 422 seam, the hashes, and the write→commit→audit→undo pipeline.

See ADR-0002 (named volume + sandbox session), ADR-0003 (patch/hashline + audit +
git undo), and `services/workspace/store.py`.
