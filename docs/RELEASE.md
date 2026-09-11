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

## 4. Windows client packaging

**Default path — self-contained exe via CI (no Windows machine needed).** Push a
`v*` tag; `.github/workflows/release.yml` rebuilds the client from the tagged
commit, produces `MedicalScribe-win-x64.zip` (single-file, self-contained —
runs on Windows 10+ x64 with no .NET install) plus `sha256.txt`, and attaches
both to the GitHub Release. Every push also builds the same artifact as the
`client-publish` CI job (artifact `MedicalScribe-win-x64`).

1. [ ] CI green on the tagged commit (backend + client-windows + docker).
2. [ ] `git tag vX.Y.Z && git push origin vX.Y.Z` — release workflow runs.
3. [ ] Verify the Release page lists `MedicalScribe-win-x64.zip` +
      `sha256.txt`; copy the checksum to the clinic install medium.
4. [ ] Smoke on one workstation: unzip, run `MedicalScribe.WPF.exe`,
      first-run wizard (server URL + mic), one dictation session, one report
      draft → finalize → approve against the staging server; open the
      **AI Models** screen and pull one model end-to-end.

**Opt-in path — signed MSIX (auto-update).** MSIX packaging and signing cannot
be produced from Linux CI (makeappx / SignTool are Windows tools); this stays
the manual step for clinics that want silent auto-updates:

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
