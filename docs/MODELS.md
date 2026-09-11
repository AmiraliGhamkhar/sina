# Local AI models — model hub (download + auto-configure)

MedicalScribe keeps the client thin: **model weights are downloaded and
verified server-side**, never shipped with the app and never served raw to
clients. The WPF client's **AI Models** screen (and `GET /api/v1/models`)
drives everything:

1. an **admin** presses *Download* for a catalog model,
2. the API streams the files from HuggingFace into
   `MS_MODELS__DIR/{model-id}/`, verifying **sha256** (LFS files) / exact size
   (small files) against the pinned catalog, with live per-file progress,
3. on success the model is recorded in a manifest (`models/manifest.json`)
   and — where possible — **auto-configured with no restart**.

Downloads are idempotent, resumable per model (re-POST returns `202` while
one is in flight), cancellable (DELETE removes artifacts and partial files),
and guarded by a minimum free-disk check
(`MS_MODELS__MIN_FREE_DISK_BYTES`, default 2 GB).

## Catalog

| Model | Id | Role | Size | License | Runtime | Auto-configured |
|---|---|---|---|---|---|---|
| Whisper large-v3-turbo (Q4_0) | `whisper-large-v3-turbo` | Multilingual STT | ~474 MB | MIT | whisper.cpp server (**external**) | no — see note |
| Shenava Koochik v1.0 (INT8) | `shenava-koochik` | Persian STT, streaming | ~174 MB | Apache-2.0 | in-process (sherpa-onnx) | **yes** |
| MiniCPM5 2B (Q4_K_M) | `minicpm5-2b` | Note-drafting LLM | ~1.6 GB | Apache-2.0 | llama-server (**external**) | no — see note |
| Jibay 2 (Q4_K_M) | `jibay-2` | Persian/English LLM | ~1.3 GB | Apache-2.0 | llama-server (**external**) | no — see note |
| OpenMed Persian PII (TookaBERT-Large INT4) | `persian-pii-tookabert` | PHI redaction (pre-cloud) | ~365 MB | CC-BY-4.0 | in-process (onnxruntime) | **yes** |

Sources are pinned in `backend/api/services/model_catalog.py` (repo +
filename + size + sha256). To move to a mirror, set `MS_MODELS__BASE_URL` —
everything else stays identical.

## What "auto-configure" does (and does not) do

**In-process models — true auto-configure.** `shenava-koochik` and
`persian-pii-tookabert` run *inside* the API process (the `local-ai` pip
extra: `sherpa-onnx`, `onnxruntime`, `tokenizers`). Their file paths
auto-resolve to `MS_MODELS__DIR/{id}/…` the moment the download completes —
no restart:

- the `shenava` STT provider becomes immediately selectable
  (`MS_STT__DEFAULT_PROVIDER=shenava` or per-encounter),
- NER-based PHI redaction layers onto the regex scrubber before any cloud LLM
  call (`MS_LLM__CLOUD__PII_NER_ENABLED=true`; label set configurable — see
  `.env.example`).

**External models — honest operator notes.** `whisper-large-v3-turbo`
(whisper.cpp server) and `minicpm5-2b` / `jibay-2` (llama-server) run as
separate processes per the external-service rule (spec §12) — the API never
spawns them. After downloading, the UI shows the exact restart command. With
docker compose this is one command each:

```bash
docker compose --profile stt up -d      # whisper-server on :9000
docker compose --profile llm up -d      # llama-server   on :8080
```

The compose profiles mount `./models` and pick the downloaded file
automatically:

```bash
# .env — which downloaded GGUF llama-server serves:
MS_LLM_GGUF=minicpm5-2b/MiniCPM5-2B-Q4_K_M.gguf     # or jibay-2/Jibay2_Q4_K_M.gguf
```

Then point the API at them (`.env`):

```bash
MS_STT__WHISPER_SERVER__URL=http://127.0.0.1:9000
MS_LLM__LLAMA_SERVER__BASE_URL=http://127.0.0.1:8080
```

For a native (non-Docker) server, the same files are served by:

```bash
# whisper.cpp server (external process)
whisper-server --host 127.0.0.1 --port 9000 \
  -m models/whisper-large-v3-turbo/whisper-large-v3-turbo-q4_0.gguf

# llama.cpp llama-server (external process)
llama-server --host 127.0.0.1 --port 8080 \
  -m models/minicpm5-2b/MiniCPM5-2B-Q4_K_M.gguf --ctx-size 8192
```

## API

Any authenticated principal can **read**; starting a download or deleting
artifacts requires the **admin** role. Actions are audited metadata-only
(`MODEL_DOWNLOADED` / `MODEL_DELETED` events — never model bytes); metrics
counters `model_downloads:{id}:started|completed|failed` appear on
`/metrics`.

```text
GET    /api/v1/models                      → catalog + live install status/progress
GET    /api/v1/models/{model_id}           → one model
POST   /api/v1/models/{model_id}/download  → 202 Accepted (admin); idempotent
DELETE /api/v1/models/{model_id}           → remove artifacts (admin)
```

Example:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -X POST http://127.0.0.1:8000/api/v1/models/shenava-koochik/download
```

Schemas: `docs/API.md` (§Models).

## Operations

- **Disk:** artifacts live under `MS_MODELS__DIR` (default `models/`, a
  sibling of `backend/` — git-ignored). Deleting a model from the UI removes
  the directory and manifest entry; restart external services that still
  point at the removed file.
- **Docker:** the API container mounts `./models:/app/models` (read-write —
  downloads land on the host). Ensure the host directory is writable by the
  container user (uid 10001), e.g. `sudo chown -R 10001 ./models` on Linux.
- **Verification:** LFS files (`.onnx`/`.gguf` weights) are sha256-checked
  against the pinned catalog; small text files (tokens, configs) are checked
  by exact size. A mismatch fails the install, surfaces the error in the UI,
  and cleans partial files.
- **Re-downloads:** a failed download leaves the model in `error` state;
  pressing *Download* again retries from scratch (verified, so safe).

## Licensing notes (read before clinical deployment)

- **Shenava Koochik** — the catalog downloads the original
  `Reza2kn/Shenava-Koochik-v1.0-tract-streaming` (**Apache-2.0**). A widely
  mirrored repackaging
  (`mah92/sherpa-onnx-nemo-ctc-fa-shenava-koochik-v1.0-streaming-int8`) is
  **CC-BY-NC-4.0** (non-commercial) — deliberately **not** used, so the
  default stack stays commercial-use compatible.
- **MiniCPM5-2B / Jibay 2 / Whisper GGUF** — Apache-2.0 / Apache-2.0 / MIT per
  their HF repos. Quantized third-party conversions; sha256 pinning means you
  get exactly the reviewed bytes.
- **openmed-persian-pii-tookabert** — **CC-BY-4.0** (attribution required).
- These models assist documentation workflows; they are **not certified
  medical devices**. A clinician must review every generated report (the
  client enforces review-before-finalize regardless of model choice).
