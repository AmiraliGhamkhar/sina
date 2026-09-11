# models/

Local model weights referenced by *server-side* providers (GGUF for
llama-server/whisper-server, ONNX for in-process STT/NER). **Never committed**
— `.gitignore` excludes `*.gguf*`, `*.bin`, `*.onnx`, `*.safetensors`, …

Downloads are managed by the model hub (admin action from the WPF client's
**AI Models** screen or `POST /api/v1/models/{id}/download`): streamed from
pinned HuggingFace sources, sha256-verified, auto-configured where possible.
Catalog, licenses, and service wiring: `docs/MODELS.md`.

Local convention: model choice is a deployment decision documented per site;
the application only stores *paths* in configuration.
