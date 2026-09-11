# Release checklist (Phase 8)

This is the operational runbook for cutting a MedicalScribe release. Every
step is either executable from this repo or explicitly calls out what needs
a Windows machine / production credentials.

## 1. Code gates (automated)

- [ ] CI green on the release commit: backend (pytest on **real PostgreSQL +
      Redis services** + ruff, both Python 3.11/3.12), client-linux,
      client-windows (build + xunit), docker, secret scan (gitleaks, full
      history), pip-audit with no unreviewed findings.
- [ ] `python -m pytest` locally: full suite green (sqlite mode) — the CI run
      additionally exercises PostgreSQL + Redis.
- [ ] Migration round-trip on a production-shape database:
      `cd backend && MS_DATABASE__URL=… alembic upgrade head` then
      `alembic downgrade -1 && alembic upgrade head` on a **copy** of
      production data.

## 2. Security pass

- [ ] `pip-audit --skip-editable` clean (or every finding triaged + accepted
      in writing).
- [ ] gitleaks clean on full history (config: `.gitleaks.toml`).
- [ ] Production env review (docs/SECURITY.md checklist): `MS_SERVER__ENV=
      production`, JWT secret ≥ 32 bytes rotated for the release,
      `MS_AUTH__DEV_TOKEN` unset, `MS_SECURITY__SECRET_ENCRYPTION_KEY` set and
      backed up (provider secrets are unrecoverable without it),
      `MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD` unset after first boot.
- [ ] Gateway pen-test checklist walked (docs/SECURITY.md §Pen-test).

## 3. Server release

- [ ] Build image: `docker build -f infrastructure/docker/Dockerfile.api -t
      medicalscribe-api:{{VERSION}} .`
- [ ] Apply migrations **before** starting the new image
      (`MS_DATABASE__AUTO_CREATE=false` in production).
- [ ] Roll out behind nginx; verify `/health` → 200, `/health/ready` → 200
      with `postgres: ok`, one WS dictation session end-to-end (mock or a
      real provider), `/metrics` scraped by Prometheus (monitoring profile:
      `docker compose --profile monitoring up`).
- [ ] Watch the dashboard for 24 h: `medicalscribe_transcript_db_write_
      failures_total`, `medicalscribe_http_rate_limited_total`, provider
      health panels (docs/DEPLOYMENT.md §Ops).

## 4. Windows client packaging (needs a Windows machine)

MSIX packaging and signing cannot be produced from Linux CI (makeappx /
SignTool are Windows tools); this is the manual step of the release.

1. On a Windows 10/11 machine with the .NET 10 SDK:
   `dotnet publish client/MedicalScribe.WPF -c Release -r win-x64`
2. Create a Windows Application Packaging Project in
   `client/MedicalScribe.WPF.sln` (or use the manifest template in
   `client/packaging/AppxManifest.template.xml`), referencing the published
   WPF app; fill the placeholders (identity name, publisher CN = the code
   signing certificate subject).
3. Sign with the clinic's code-signing certificate (timestamp server
   included). The publisher in the manifest MUST equal the certificate
   subject or updates will be refused.
4. Publish the `.msix` + the filled `MedicalScribe.appinstaller.template.xml`
   (auto-update policy: hourly checks, silent install) to the clinic HTTPS
   update host.
5. Smoke on one workstation: install, first-run wizard (server URL + mic),
   one dictation session, one report draft → finalize → approve against the
   staging server.

Signing + MSIX on the CI Windows runner remains open (see honesty ledger in
README) — it needs one iteration with the real certificate artifacts.

## 5. Regulatory-boundary language (every release)

- [ ] Client and docs describe the system as **assistive**: AI-drafted text
      requires clinician review and sign-off; nothing auto-finalizes
      (verified by tests: approved reports are immutable, `[[MISSING]]`
      sections are never invented).
- [ ] No claim of clinical validation/certification anywhere in the product
      surface.
- [ ] Privacy defaults intact: `privacy_required=true` default; cloud
      providers never receive PHI-scrubbed-but-identifiable data from
      privacy-required encounters (tested invariant).

## 6. After the release

- [ ] Tag the commit; note the tag in the deployment log with the image
      digest.
- [ ] Archive the release audit log slice (`audit_log` table + JSONL) per
      retention policy.
- [ ] File follow-ups for anything skipped in this run.
