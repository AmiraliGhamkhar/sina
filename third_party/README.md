# third_party/

Reserved for vendored code that must ship with the product (license-mandated
or offline-build needs). Policy:

1. Default to package references (NuGet/PyPI), not vendoring.
2. Anything vendored goes in `third_party/<name>/` with the upstream LICENSE
   copied verbatim and an `UPSTREAM.md` (repo, commit hash, modifications).
3. Adapted (non-vendored) patterns stay in first-party files with header
   notes; all attribution is indexed in `docs/THIRD_PARTY_NOTICES.md`.

Currently empty by design.
