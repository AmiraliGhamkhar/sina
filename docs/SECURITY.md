# Security notes & pen-test checklist (Phase 8)

Threat model in one paragraph: the WPF client runs on clinic workstations
and is treated as **untrusted for secrets** — it holds only a short-lived
access token (memory) and talks to the API over TLS. The API server holds
all credentials (JWT secret, provider API keys, Fernet key) and is the only
party that ever contacts AI providers. Postgres holds clinical data; Redis
holds only rate-limit buckets and shared health state (never source of
truth). The edge (nginx) terminates TLS and blocks `/docs`, `/openapi.json`
and `/metrics` from outside.

## Production environment checklist

- `MS_SERVER__ENV=production` (refuses to boot without a ≥32-byte JWT secret;
  dev-token login inert by code, not convention).
- `MS_AUTH__DEV_TOKEN` unset. `MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD` set for the
  FIRST boot only, then unset (bootstrap is skipped once the admin exists).
- `MS_SECURITY__SECRET_ENCRYPTION_KEY` set (Fernet) and backed up — provider
  secrets in `ai_providers.secret_ciphertext` are unrecoverable without it.
- `MS_DATABASE__AUTO_CREATE=false` + Alembic migrations as the only schema
  path.
- TLS: nginx terminates 1.2/1.3; WPF trusts the deployment CA (thumbprint
  pinning is a client-side setting; no global TLS-bypass flags exist).
- Rate limits sized: `MS_RATE_LIMIT__AUTH_PER_MINUTE` tight (default 10);
  Redis backend (`MS_REDIS__URL`) when running >1 worker so buckets are
  shared — the limiter degrades **open** if Redis dies, and the login
  lockout (argon2 + 5-failure lock) remains the second layer.

## Data hygiene invariants (enforced in code + tests)

- Transcripts are never logged (route templates only; audit payload
  deny-list; no query strings in access logs — WS tokens ride in them).
- Provider API keys decrypt in exactly one function
  (`api_bridge.provider_config_with_secrets`) and never appear in listings,
  audit rows, or logs. Secrets are stored Fernet-encrypted at rest.
- Refresh tokens are stored as SHA-256 hashes, single-use; reuse revokes all
  of the user's sessions.
- Passwords: argon2id (OWASP baseline params), dummy-hash equalizer so
  unknown-user logins burn the same time as wrong passwords.
- Audit rows are append-only; usernames/passwords never recorded.
- `privacy_required` encounters never route to cloud providers (tested).
- PHI scrubbing before ANY cloud LLM call (`redact_phi_for_cloud`).

## Pen-test checklist (gateway + API)

Walk this against a staging deployment before each release.

1. **Edge**: `/docs`, `/openapi.json`, `/metrics` unreachable from outside;
   TLS versions/ciphers as configured; no HTTP→HTTPS downgrade path that
   carries tokens.
2. **Authn**: login lockout fires (5 bad passwords → 429 + lock window);
   refresh rotation is single-use (replay a consumed token → 401 AND the
   legit successor token also dies — verify with a second client); expired
   access tokens rejected; dev token inert in production env.
3. **AuthZ**: non-admin JWT against `/api/v1/admin/*` → 403; patient list
   without a token → 401.
4. **Injection**: SQL via patient search `q`, template keys, report ids
   (parameterized everywhere — verify no raw string interpolation).
5. **Rate limits**: burst `/auth/login` from one IP → 429 with Retry-After;
   general bucket; WS per-user cap (6th concurrent session → error frame +
   close 4400).
6. **WS protocol**: unauthenticated socket → 4401; bad first frame → 4400;
   protocol version drift → 4409; oversize frame → 1009; idle → pings then
   4408.
7. **Secrets**: `GET /api/v1/providers` (any auth) contains no key material;
   stored `secret_ciphertext` differs from the plaintext; admin secret PUT
   with a non-admin token → 403; vault key missing → 501 (not a fallback to
   plaintext).
8. **Audit**: every state change above produced an audit row with no
   usernames/passwords in payloads.
9. **Dependency supply chain**: pip-audit + gitleaks green in CI; image
   built from the repo Dockerfile only.

## Known accepted risks (tracked, not ignored)

- The rate limiter degrades open when its backend is unreachable
  (availability over strictness for a clinical tool); the lockout guard and
  nginx limits are the compensating controls.
- Access JWTs trust their role claim for 30 min after a role demotion
  (`MS_AUTH__ACCESS_TTL_MINUTES`); revocation lands with the next token
  refresh. Shorten the TTL for stricter deployments.
- Load-test numbers on the mock STT path only so far (docs/RELEASE.md); the
  4 GB VM + llama-server soak is still to be executed on real hardware.
