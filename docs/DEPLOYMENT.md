# Deployment notes (progressive — finalized in Phase 8)

## Topology

- **Clinic LAN**: Windows workstations (WPF — self-contained exe from the
  GitHub Release, or MSIX for auto-update shops) → clinic servers:
  nginx gateway (TLS), FastAPI api (1 worker until Phase 8 load tests; then N
  behind the gateway with sticky WS or Redis-coordinated sessions), Postgres 16,
  Redis 7, llama.cpp server (GPU host) and/or whisper-server.
- **Local AI models** (optional, `docker compose --profile stt` /
  `--profile llm`): downloaded + sha256-verified from the client's AI Models
  screen into `./models` (see `docs/MODELS.md`). In-process models (Shenava
  STT, PII NER) need the `local-ai` extra — included in the Dockerfile by
  default; native installs: `pip install ".[local-ai]"`.
- **Hybrid/cloud opt-in**: only after the org accepts data-exit policy; every
  encounter defaults `privacy_required=true`, which *cannot* be overridden by
  user preference (tested invariant).

## Secrets

1. `.env` on the server host only; owner `mscribe`, mode `600`. Never shipped
   with the WPF client. `MS_AUTH__JWT_SECRET` ≥ 32 bytes (`openssl rand -base64 48`),
   `MS_SERVER__ENV=production` makes the API *refuse to boot* without it.
2. Provider API keys: stored per-provider row (`ai_providers.secret_ciphertext`),
   Fernet-encrypted with `MS_SECURITY__SECRET_ENCRYPTION_KEY` (distinct from the
   JWT secret; generate with `python -c "from cryptography.fernet import Fernet;
   print(Fernet.generate_key().decode())"`). Managed via the admin API
   (`PUT /api/v1/admin/providers/{kind}/{name}/secret`), decrypted in exactly
   one code path and never returned by any endpoint.
3. `MS_AUTH__DEV_TOKEN` must be unset in production; it is inert when
   `ENV=production` by code, not convention.

## TLS / edge

- Nginx terminates TLS 1.2+/1.3 and proxies `/api/`, `/health`, `/ws/`
  (upgrade headers set, buffering off for WS). `/docs` and `/openapi.json` are
  closed at the edge in production.
- WPF trusts the deployment CA (pin by thumbprint in settings, Phase 8) —
  no global TLS-bypass flags exist in the client.

## Ops

- Health gates containers: `/health` liveness; `/health/ready` pings the
  database engine directly (Phase 7) and TCP-probes Redis when configured.
- Monitoring (Phase 8): `docker compose --profile monitoring up` adds
  Prometheus (scrapes `api:8000/metrics`, 15 s) and Grafana with the
  `medicalscribe-api` dashboard (`infrastructure/prometheus/
  grafana-dashboard.json`). The edge does NOT proxy `/metrics` — scrape
  internally only. Optional OTel traces: install the `observability` extra,
  set `MS_OBSERVABILITY__OTEL_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Load testing (Phase 8): `infrastructure/load/ws_loadtest.py --url
  ws://host:8000/ws/v1/transcribe --jwt-secret <secret> --sessions 20` —
  mints one principal per session so the per-user cap (5) isn't the thing
  being measured.
- Schema management (Phase 7): production boots with
  `MS_DATABASE__AUTO_CREATE=false` and applies `cd backend &&
  MS_DATABASE__URL=... alembic upgrade head` as a release step; `create_all`
  stays a dev convenience. First boot with
  `MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD` set creates the admin user (never a
  default credential); subsequent boots skip it.
- Logs: JSON to stdout → collector; **transcripts are never logged** (app-level
  enforcement: log route templates only; audit sink deny-list). Audit events
  dual-write to `logs/audit.jsonl` **and** the `audit_log` table (bounded
  background writer; drops to file-only if the DB sinks fall behind).
- Backups: `pg_dump` + WAL-G (Phase 8) for Postgres — the audit table, reports,
  transcripts and revision history all live there now; Redis needs no backup
  (never source of truth; rate limiting degrades open when it is down).
- Scaling rule until Phase 8 load tests: exactly one `api` replica — WS
  sessions and in-memory write-through caches are per-process. With
  `MS_REDIS__URL` set, rate-limit buckets and provider health already share
  across workers; durable reads work from any replica.

## Update policy

WPF updates via signed MSIX; API via image tags. The client refuses to connect
when `ws_protocol`/`ws_protocol_min` is incompatible (`/api/v1/version`) —
protocol bump policy in docs/WEBSOCKET_PROTOCOL.md.
