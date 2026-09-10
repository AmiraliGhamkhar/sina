# models/

Local model weights referenced by *server-side* providers (GGUF for
llama-server, faster-whisper/ONNX for local STT). **Never committed** —
`.gitignore` excludes `*.gguf*`, `*.bin`, `*.onnx`, `*.safetensors`, …

| Model | Where it's used | License note |
|---|---|---|
| Llama/Qwen GGUF (e.g. qwen2.5-14b-instruct-q5) | llama-server container (`--profile llm`) | per-model license (check before redistribution) |
| Whisper large-v3 / distil | whisper-server (Phase 3) | MIT (whisper weights) |

Download scripts land in Phase 3/8 (`scripts/fetch-models.ps1`, checksum
verified). Model choice is a deployment decision documented per site; the
application only stores *paths* in configuration.
