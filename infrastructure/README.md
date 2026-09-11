# Infrastructure

| Path | Purpose |
|---|---|
| `docker/Dockerfile.api` | FastAPI image (python:3.12-slim, non-root, healthcheck) |
| `nginx/nginx.conf` | TLS termination + WS upgrade gateway for `/api/`, `/health`, `/ws/`; blocks `/docs`, `/openapi.json` (404) and does not proxy `/metrics` |
| `prometheus/prometheus.yml` | scrape config (`api:8000/metrics`, 15 s) |
| `prometheus/grafana-dashboard.json` | `medicalscribe-api` dashboard |
| `load/ws_loadtest.py` | WS load-test harness (see docs/RELEASE.md) |

Compose lives at the repository root (`docker-compose.yml`) to keep build
contexts simple:

| Compose invocation | Services |
|---|---|
| `docker compose up` | `api`, `postgres`, `redis` |
| `--profile gateway` | + nginx edge (127.0.0.1:8443) |
| `--profile stt` | + whisper.cpp server (127.0.0.1:9000; external STT service) |
| `--profile llm` | + llama-server (127.0.0.1:8080; external LLM service) |
| `--profile monitoring` | + Prometheus (127.0.0.1:9090) + Grafana (127.0.0.1:3000) |

## Production notes (expanded in docs/DEPLOYMENT.md)

1. The WPF client must point at the gateway origin only (single HTTPS port
   for REST + WebSocket). api/llama/whisper ports stay on internal networks.
2. `POSTGRES_PASSWORD` and all `MS_*` secrets come from the deployment env —
   never from this repo.
3. WS sessions and in-memory caches are process-local: run one `api` replica
   (or sticky WS routing) until sessions are fully Redis-coordinated.
4. TLS terminates at nginx; the compose network is isolated and the api binds
   loopback-mapped ports only in dev.
