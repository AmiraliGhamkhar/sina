# models/

Local model weights referenced by *server-side* providers (GGUF for
llama-server, faster-whisper/ONNX for local STT). **Never committed** —
`.gitignore` excludes `*.gguf*`, `*.bin`, `*.onnx`, `*.safetensors`, …

| Model | Where it's used | License note |
|---|---|---|
| Llama/Qwen GGUF (e.g. qwen2.5-14b-instruct-q5) | llama-server container (`--profile llm`) | per-model license (check before redistribution) |
| Whisper large-v3 / distil | whisper-server (Phase 3) | MIT (whisper weights) |

Downloads go through the **model hub**: the client's *AI Models* screen (or
`POST /api/v1/models/{id}/download`) fetches catalog entries with
sha256-verified integrity checks and auto-configures the matching server-side
providers (`MS_MODELS__DIR` points at this folder) — see docs/MODELS.md.
Model choice is a deployment decision documented per site; the application
only stores *paths* in configuration.
