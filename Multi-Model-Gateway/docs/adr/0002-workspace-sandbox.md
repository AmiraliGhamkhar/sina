# 0002 — Named-volume workspace + persistent sandbox session

Workspaces live on a named volume `workspaces:/workspaces` at
`workspaces/{user_id}/{agent_id}`, git-init on creation, shared by the `backend`
and the new `sandbox` service (`python:3.11-slim` + node, git, curl — multi-runtime).
The sandbox is a persistent per-workspace session with a 30-minute idle TTL; one bash
at a time per workspace via a per-workspace `asyncio.Lock` (concurrency = 1). Egress is
allowlisted (pypi, github, npm + the gateway itself). Quotas are 1 GiB soft disk per
workspace, 30 s bash timeout, 512 MiB / 0.5 CPU per session — enforced in the backend
as `AppError → SSE error` so the model sees "workspace quota exceeded" and can adapt.

Why a named volume (not a host bind): survives container recreate, no host path
assumptions, matches existing `pgdata`/`training_data` volumes. Why persistent session:
repo tasks need stateful work (`pip install` once, run many); per-call ephemeral would
be unbearably slow. Why one-at-a-time: prevents git interleaving and keeps the `file_edits`
audit + git history linear and revertible. See ADR-0003 for the edit/undo shape and the
capability-gate design (master switch + per-user grant).

## Confinement — now real (F1/C2, not just a promise)

The isolation that the "persistent session" phrasing implied is now enforced by the
sandbox (`sandbox/uid_alloc.py` + `sandbox/app.py`): every `(user_id, agent_id)` runs
`bash` as a distinct OS **UID** in a workspace directory `chmod 700`'d to that UID.
Allocation is **owner-as-registry** — the workspace directory's `st_uid` IS the record
(no side table to keep in sync, collision-free, self-healing). The controller (root)
chowns the tree and spawns bash via `Popen(user=<uid>, group=<uid>, start_new_session)`;
the kernel clears all of the child's capabilities when it drops to a nonzero UID, so
`bash` is fully unprivileged. A different tenant's workspace is owned by a different UID
with 700 (no group/other bits), so an unprivileged `bash` cannot traverse into it even
though it shares the volume. The backend is the trust anchor and runs as root so it keeps
full access; only `bash` is confined.

Consequence of enforcement (deliberate): the sandbox controller uses a **minimal cap
allow-list** (`SETUID`, `SETGID`, `CHOWN`, `FOWNER`, `DAC_OVERRIDE`, `KILL`) instead of
`cap_drop: ALL`, because `chown` and `setuid` require those capabilities even as root.
Everything else stays dropped, `no-new-privileges` and `read_only` remain — the bash
child still ends up capability-less. The backend prod Dockerfile no longer runs as
`appuser`; the trusted backend must be root to write any tenant's 700 workspace.

## Egress — confined by network, not by string filter

The sandbox sits on its own user-defined network (`sandboxnet`) with the backend as its
only compose peer, so a model-driven shell cannot reach postgres/redis/backend/worker/
searxng. It has outbound NAT (pip/npm/git work) but no route to the internal services or
the host (`host.docker.internal` is not present on the sandbox). `SANDBOX_ALLOWLIST`
remains aspirational dead config — a domain-level allowlist would need an egress proxy.

