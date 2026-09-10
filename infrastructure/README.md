# Infrastructure

| Path | Purpose | Phase |
|---|---|---|
| `docker/Dockerfile.api` | FastAPI image (python:3.12-slim, non-root, healthcheck) | 1 (hardened in 8) |
| `nginx/nginx.conf` | TLS termination + WS upgrade gateway for `/api/`, `/ws/` | 1 |
| `prometheus/prometheus.yml` | metrics scrape config | 8 (endpoint), 1 (file) |

Compose lives at the repository root (`docker-compose.yml`) to keep build
contexts simple:

- default profile: `api`, `postgres`, `redis`
- `--profile gateway`: nginx edge
- `--profile llm`: llama-server example (external AI service; model files
  mounted read-only from `./models`, never copied into images)
- `--profile monitoring`: prometheus

Production notes (expanded in Phase 8 / docs/DEPLOYMENT.md):

1. The WPF client must point at the gateway origin only (single HTTPS port
   for REST + WebSocket). api/llama ports stay on internal networks.
2. `POSTGRES_PASSWORD` and all `MS_*` secrets come from the deployment env —
   never from this repo.
3. WebSocket sessions are process-local until Redis coordination (Phase 7);
   run one api worker or use sticky routing meanwhile.
4. TLS here terminates at nginx; reencrypt to the api container (no plaintext
   beyond localhost inside the compose network is required, but the network is
   isolated and the api binds loopback-mapped ports only in dev).
