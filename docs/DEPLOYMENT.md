# Deployment notes (progressive — finalized in Phase 8)

## Topology

- **Clinic LAN**: Windows workstations (WPF, MSIX-installed) → clinic servers:
  nginx gateway (TLS), FastAPI api (1 worker until Phase 7/Redis; then N behind
  the gateway with sticky WS or Redis-coordinated sessions), Postgres 16,
  Redis 7, llama.cpp server (GPU host) and/or whisper-server.
- **Hybrid/cloud opt-in**: only after the org accepts data-exit policy; every
  encounter defaults `privacy_required=true`, which *cannot* be overridden by
  user preference (tested invariant).

## Secrets

1. `.env` on the server host only; owner `mscribe`, mode `600`. Never shipped
   with the WPF client. `MS_AUTH__JWT_SECRET` ≥ 32 bytes (`openssl rand -base64 48`),
   `MS_SERVER__ENV=production` makes the API *refuse to boot* without it.
2. Provider API keys (Phase 7): stored per-provider row, Fernet-encrypted with
   `MS_SECURITY__KEY_ENCRYPTION_KEY` (distinct from JWT secret). Client-visible
   representation is `****last4` or the configured boolean.
3. `MS_AUTH__DEV_TOKEN` must be unset in production; it is inert when
   `ENV=production` by code, not convention.

## TLS / edge

- Nginx terminates TLS 1.2+/1.3 and proxies `/api/`, `/health`, `/ws/`
  (upgrade headers set, buffering off for WS). `/docs` and `/openapi.json` are
  closed at the edge in production.
- WPF trusts the deployment CA (pin by thumbprint in settings, Phase 8) —
  no global TLS-bypass flags exist in the client.

## Ops

- Health gates containers: `/health` liveness, `/health/ready` checks TCP
  reachability of Postgres/Redis when configured.
- Logs: JSON to stdout → collector; **transcripts are never logged** (app-level
  enforcement: log route templates only; audit sink deny-list).
- Backups: `pg_dump` + WAL-G (Phase 8) for Postgres; Redis needs no backup
  (never source of truth); `logs/audit.jsonl` ships to the SIEM daily until the
  audit table exists.
- Scaling rule until Phase 7: exactly one `api` replica — WS sessions and
  provider health are in-process. Post-Redis: N replicas + shared state.

## Update policy

WPF updates via signed MSIX; API via image tags. The client refuses to connect
when `ws_protocol`/`ws_protocol_min` is incompatible (`/api/v1/version`) —
protocol bump policy in docs/WEBSOCKET_PROTOCOL.md.
