FROM python:3.11-slim AS base

# Prefer IPv4 when resolving host.docker.internal. Docker Desktop also publishes
# an IPv6 host-gateway that isn't routable from the container, and glibc otherwise
# tries it first — causing "Network is unreachable" on host calls to LM Studio /
# ComfyUI. This precedence rule makes IPv4(-mapped) addresses win.
RUN echo 'precedence ::ffff:0:0/96 100' >> /etc/gai.conf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Development: hot-reload. Running as root is fine on a local / tailnet box.
FROM base AS dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Production: unprivileged user, no reload.
#
# F1/C2 note: the workspace volume is confined via per-tenant OS UIDs (the sandbox
# chowns each workspace to a tenant UID and chmod 700's it). The backend is the
# trust anchor and must keep write access to EVERY workspace — so it must run as
# root (a non-root backend user would be a *different* UID and could not traverse a
# tenant's 700 dir). This is intentional: only bash is untrusted; the backend is
# verified code that already holds all users' data and secrets. If you harden this
# further, it must be via a shared group + ACL, not by making the backend a
# non-root user on the chown-700 volume.
FROM base AS prod
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
