#!/usr/bin/env bash
# setup.sh — llm-gateway one-shot setup (Linux / macOS / WSL / git-bash)
#
# Checks Docker, generates .env from .env.example with real secrets, probes for
# local AI servers, and optionally starts the whole stack and waits for health.
# Idempotent: an existing .env is never touched.
#
# Usage:
#   ./setup.sh                     # interactive: prompts + starts the stack
#   ./setup.sh --skip-start        # prepare .env only, do not start
#   ./setup.sh --non-interactive   # no prompts (CI/automation), starts the stack

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"

SKIP_START=0
NON_INTERACTIVE=0
for arg in "$@"; do
    case "$arg" in
        --skip-start) SKIP_START=1 ;;
        --non-interactive) NON_INTERACTIVE=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

# ----------------------------------------------------------------- helpers ----
step() { printf '\n==> %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
ok()   { printf '  %s\n' "$*"; }
fail() { printf '  %s\n' "$*" >&2; }

# sed replacement-string escape for user-entered values: backslash, slash,
# ampersand, and pipe (hex/base64 generated values have none of these).
sed_escape() { printf '%s' "$1" | sed -e 's/[\\/&|]/\\&/g'; }

# Read a line, strip a trailing CR (piped input / Windows text files add one),
# and store it in REPLY. Returns read's status (0 on a line, non-zero on EOF).
get_input() {
    REPLY=""
    if read -r -p "$1" REPLY; then
        REPLY="${REPLY%$'\r'}"
        return 0
    fi
    return 1
}

# $1 = desired output length in chars (even; hex output)
rand_hex() {
    local len="$1"
    local bytes=$(( len / 2 ))
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        head -c "$len" /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$len"
    fi
}

# Replace (or append) a KEY=value line in .env. GNU/BSD-sed compatible.
set_env() {
    local key="$1" value; value="$(sed_escape "$2")"
    if grep -q "^${key}=" "$ENV_FILE"; then
        "${SED_I[@]}" "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$2" >> "$ENV_FILE"
    fi
}

# ------------------------------------------------------- Step 1: prerequisites
step "Step 1/5 - Checking prerequisites"
if ! command -v docker >/dev/null 2>&1 || ! docker --version >/dev/null 2>&1; then
    fail "Docker not found. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) or Docker Engine on Linux, then re-run."
    exit 1
fi
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1 && docker-compose --version >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "Docker Compose not found. Install Docker Desktop (includes Compose) or the compose plugin, then re-run."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker is installed but the daemon is not running. Start Docker (Desktop) and re-run."
    exit 1
fi
ok "Docker + Compose OK."

# macOS BSD sed needs a backup-suffix argument; GNU sed doesn't.
if [ "$(uname -s)" = "Darwin" ]; then SED_I=(sed -i ''); else SED_I=(sed -i); fi

# ------------------------------------------------------------- Step 2: .env
step "Step 2/5 - Environment (.env)"

# Compose mounts a Docker secret file even when the key itself is optional (S1).
# Make sure a fresh clone has the file; never touch an existing one.
if [ ! -f "$REPO_ROOT/secrets/openrouter_api_key.txt" ]; then
    mkdir -p "$REPO_ROOT/secrets"
    : > "$REPO_ROOT/secrets/openrouter_api_key.txt"
    info "Created secrets/openrouter_api_key.txt (empty = no OpenRouter key; add one later if you like)."
fi

if [ -f "$ENV_FILE" ]; then
    # Idempotency: never clobber an existing .env.
    info "Using existing .env (leaving unchanged)."
else
    if [ ! -f "$ENV_EXAMPLE" ]; then
        fail ".env.example not found next to setup.sh - is this the llm-gateway repo root?"
        exit 1
    fi
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    GRAFANA_PW=""

    # --- generated secrets ---
    SECRET_KEY="$(rand_hex 64)"
    PG_PASSWORD="$(rand_hex 32)"
    GRAFANA_PW="$(rand_hex 32)"
    set_env "SECRET_KEY" "$SECRET_KEY"
    set_env "POSTGRES_PASSWORD" "$PG_PASSWORD"
    # User/db must match the POSTGRES_USER / POSTGRES_DB defaults the compose
    # file now reads from .env (ishaab / llmgateway — added below if missing):
    set_env "DATABASE_URL" "postgresql://ishaab:${PG_PASSWORD}@postgres/llmgateway"
    set_env "GRAFANA_ADMIN_PASSWORD" "$GRAFANA_PW"

    # --- prompted values (defaults in non-interactive mode) ---
    LM_URL="http://host.docker.internal:1234"
    COMFY_URL="http://host.docker.internal:8188"
    LM_DEFAULT_MODEL=""   # empty = keep the example placeholder
    LM_CHAT_MODEL=""      # empty = fall back to LM_DEFAULT_MODEL
    OR_API_KEY=""         # empty = no OpenRouter key (app boots fine, S1)
    if [ "$NON_INTERACTIVE" -eq 0 ]; then
        if get_input "LM Studio URL (default: $LM_URL): "; then [ -n "$REPLY" ] && LM_URL="$REPLY"; fi
        if get_input "ComfyUI URL (default: $COMFY_URL): "; then [ -n "$REPLY" ] && COMFY_URL="$REPLY"; fi
        if get_input "LM Studio default model id (used for prompt rewriting; Enter keeps placeholder): "; then [ -n "$REPLY" ] && LM_DEFAULT_MODEL="$REPLY"; fi
        if get_input "LM Studio chat model id (Enter for empty = falls back to default model): "; then [ -n "$REPLY" ] && LM_CHAT_MODEL="$REPLY"; fi
        echo "OpenRouter API key is OPTIONAL - the app now boots without it."
        if get_input "OpenRouter API key (Enter to leave empty): "; then [ -n "$REPLY" ] && OR_API_KEY="$REPLY"; fi
    fi
    set_env "LM_URL" "$LM_URL"
    set_env "COMFY_URL" "$COMFY_URL"
    set_env "LM_CHAT_MODEL" "$LM_CHAT_MODEL"
    set_env "OPENROUTER_API_KEY" "$OR_API_KEY"
    [ -n "$LM_DEFAULT_MODEL" ] && set_env "LM_DEFAULT_MODEL" "$LM_DEFAULT_MODEL"

    # --- port probes (interactive only; never fatal) ---
    if [ "$NON_INTERACTIVE" -eq 0 ]; then
        info "Probing for local servers (report-only)..."
        if (exec 3<>/dev/tcp/host.docker.internal/1234) 2>/dev/null; then ok "LM Studio detected on 1234"; else info "LM Studio not detected (defaulting to 1234)"; fi
        if (exec 3<>/dev/tcp/host.docker.internal/8188) 2>/dev/null; then ok "ComfyUI detected on 8188"; else info "ComfyUI not detected (defaulting to 8188)"; fi
        if (exec 3<>/dev/tcp/host.docker.internal/11434) 2>/dev/null; then ok "Ollama detected on 11434"; else info "Ollama not detected (11434 optional)"; fi
    fi

    # --- masked summary ---
    ok ".env written."
    info "  LM_URL: $LM_URL"
    if [ -n "$LM_CHAT_MODEL" ]; then info "  LM_CHAT_MODEL: $LM_CHAT_MODEL"; else info "  LM_CHAT_MODEL: not set (falls back to LM_DEFAULT_MODEL)"; fi
    info "  COMFY_URL: $COMFY_URL"
    if [ -n "$OR_API_KEY" ]; then info "  OPENROUTER_API_KEY: set"; else info "  OPENROUTER_API_KEY: not set (app boots without it)"; fi
fi

# ------------------------------------------------- metrics token (S2)
# METRICS_TOKEN gates GET /metrics (empty disables the endpoint). Generate one
# when .env lacks it (idempotent) and mirror it into monitoring/metrics_token so
# Prometheus can authenticate. The file must contain EXACTLY the token — the
# backend compares the header string, so no trailing whitespace/newline.
METRICS_TOKEN_FILE="$REPO_ROOT/monitoring/metrics_token"
mkdir -p "$REPO_ROOT/monitoring"
if grep -q '^METRICS_TOKEN=' "$ENV_FILE"; then
    METRICS_TOKEN="$(grep '^METRICS_TOKEN=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
    # Trim surrounding whitespace (incl. a trailing CR from Windows line
    # endings) and write the trimmed value back so .env and
    # monitoring/metrics_token can never drift — the backend compares the
    # header string byte-for-byte. Idempotent: when the value already
    # matches, set_env's rewrite leaves every line byte-identical.
    METRICS_TOKEN="$(printf '%s' "$METRICS_TOKEN" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    set_env "METRICS_TOKEN" "$METRICS_TOKEN"
    info "Syncing monitoring/metrics_token from existing METRICS_TOKEN in .env."
else
    METRICS_TOKEN="$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)"
    set_env "METRICS_TOKEN" "$METRICS_TOKEN"
    info "Generated METRICS_TOKEN in .env."
fi
printf '%s' "$METRICS_TOKEN" > "$METRICS_TOKEN_FILE"
ok "Wrote monitoring/metrics_token (matches METRICS_TOKEN)."

# --------------------------------------- security defaults (S5+S7)
# Add TRUSTED_PROXIES + REGISTRATION_ENABLED only when missing; never
# overwrite a value the user set deliberately.
for kv in "TRUSTED_PROXIES=172.16.0.0/12" "REGISTRATION_ENABLED=true"; do
    key="${kv%%=*}"
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        set_env "$key" "${kv#*=}"
        info "Added $kv to .env."
    else
        info "$key already present in .env (left unchanged)."
    fi
done

# ------------------------------------- postgres credentials (M3)
# docker-compose requires POSTGRES_USER + POSTGRES_DB in .env; add the defaults
# only when missing, never overwrite a value the operator set deliberately.
for kv in "POSTGRES_USER=ishaab" "POSTGRES_DB=llmgateway"; do
    key="${kv%%=*}"
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        set_env "$key" "${kv#*=}"
        info "Added $kv to .env."
    else
        info "$key already present in .env (left unchanged)."
    fi
done

# ------------------------------------------------ Step 3: SkipStart escape hatch
if [ "$SKIP_START" -eq 1 ]; then
    info "Skipping stack start (--skip-start)."
    info "Run 'docker compose up -d --build' yourself when ready (or re-run setup.sh without --skip-start)."
    exit 0
fi

# ------------------------------------------------------------- Step 4: start
step "Step 4/5 - Starting the stack (docker compose up -d --build)"
if [ "$NON_INTERACTIVE" -eq 0 ]; then
    if get_input "Start the full stack now? [Y/n] "; then
        case "$REPLY" in
            n|N|no|NO|No) info "OK - not starting. Run 'docker compose up -d --build' when ready. Docs: http://localhost:2727/docs"; exit 0 ;;
        esac
    fi
fi
if ! "${COMPOSE[@]}" up -d --build; then
    fail "Stack failed to start. Tail of backend logs:"
    "${COMPOSE[@]}" logs --tail=50 backend 2>&1 || true
    exit 1
fi

# ----------------------------------------------------------- Step 5: health
step "Step 5/5 - Waiting for backend health (up to 60s)"
healthy=0
start_ts="$(date +%s)"
while [ $(( $(date +%s) - start_ts )) -lt 60 ]; do
    if curl -sf --max-time 3 http://localhost:2727/health 2>/dev/null | grep -q '"ok"'; then
        healthy=1
        break
    fi
    elapsed=$(( $(date +%s) - start_ts ))
    if [ $((elapsed % 5)) -eq 0 ] && [ "$elapsed" -gt 0 ]; then info "Waiting for backend... (${elapsed}s)"; fi
    sleep 1
done
if [ "$healthy" -ne 1 ]; then
    fail "Backend not healthy after 60s (curl required for the health check). Check: docker compose logs backend"
    exit 1
fi

# ------------------------------------------------------------------ summary ----
ok ""
ok "llm-gateway is up!"
info "  App:             http://localhost:2727"
info "  Docs (Swagger):  http://localhost:2727/docs"
info "  Prometheus:      http://localhost:9090"
if [ -n "${GRAFANA_PW:-}" ]; then
    info "  Grafana:         http://localhost:3000  (admin / $GRAFANA_PW)"
else
    info "  Grafana:         http://localhost:3000  (admin / see GRAFANA_ADMIN_PASSWORD in .env)"
fi
info ""
info "  Next steps:"
info "    1) Open /docs and register an account."
info "    2) Add your providers (LM Studio local, or your own API keys) - see 'Adding providers' in the README."
info "    3) Start chatting."
info ""
info "  Tailscale note: keep port 2727 internal; reach the app via Caddy on :80 over your tailnet."
exit 0
