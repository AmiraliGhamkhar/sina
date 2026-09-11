"""Data-driven model catalog for the model hub (spec: download + auto-configure).

Every entry pins its upstream source (HuggingFace repo + file), expected size
and — for LFS files — the exact sha256 from the LFS pointer, so downloads are
verified byte-exact before a model is marked installed. The catalog is data,
not code: adding a model = adding a ``ModelSpec`` (surfaced automatically in
``GET /api/v1/models`` and the WPF Models screen — no UI hardcoding, mirroring
the template/terminology philosophy).

Roles map to integrations:

- ``stt-shenava``     → in-process ``shenava`` STT provider (sherpa-onnx);
                        auto-configures the moment files land (path resolution
                        convention in ``ai/stt/shenava.py``).
- ``stt-whisper``     → external whisper.cpp server; the API cannot restart
                        external services, so download + manifest is recorded
                        and the exact server arguments are surfaced to the
                        operator (see ``operator_note``).
- ``llm-llama``       → external llama-server; same external-restart semantics.
- ``pii-redaction``   → in-process NER redactor (onnxruntime); auto-configures
                        via path resolution in ``services/pii_ner.py``.

Licenses were verified against each repo's model card (2026-09-11). Model
weights are NEVER committed to git (see .gitignore ``models/``) — the catalog
ships metadata only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelRole(str, Enum):
    STT_SHENAVA = "stt-shenava"
    STT_WHISPER = "stt-whisper"
    LLM_LLAMA = "llm-llama"
    PII_REDACTION = "pii-redaction"


@dataclass(frozen=True)
class ModelFileSpec:
    """One downloadable file inside a model bundle."""

    #: remote path on huggingface.co ({repo}/resolve/main/{filename})
    repo: str
    filename: str
    #: relative name under models/{model_id}/
    local_name: str
    size_bytes: int
    #: sha256 for LFS-hosted files (from the LFS pointer); None → size-verified
    sha256: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    role: ModelRole
    description: str
    license: str
    license_url: str | None
    source_repo: str
    files: tuple[ModelFileSpec, ...]
    #: human-facing hint about what runs the model
    runtime: str
    #: True when the backend picks the model up without any restart (in-process
    #: providers); False when an external service must be (re)started with the
    #: downloaded file — the exact arguments are surfaced in ``operator_note``.
    auto_configured: bool
    operator_note: str | None = None
    #: registry provider names this model enables (display + docs only)
    providers: tuple[str, ...] = ()

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="whisper-large-v3-turbo",
        name="Whisper large-v3-turbo (Q4_0)",
        role=ModelRole.STT_WHISPER,
        description=(
            "OpenAI Whisper large-v3-turbo (809M) quantized to GGUF Q4_0 for "
            "whisper.cpp. Multilingual STT with strong mixed Persian/English "
            "behavior; served by the external whisper.cpp server."
        ),
        license="MIT",
        license_url="https://huggingface.co/Xviers/whisper-large-v3-turbo-GGUF/blob/main/LICENSE",
        source_repo="Xviers/whisper-large-v3-turbo-GGUF",
        files=(
            ModelFileSpec(
                repo="Xviers/whisper-large-v3-turbo-GGUF",
                filename="whisper.cpp/whisper-large-v3-turbo-q4_0.gguf",
                local_name="whisper-large-v3-turbo-q4_0.gguf",
                size_bytes=473_992_235,
                sha256="ca304b2c868356ebb87b22fd4361b2fbdfa6d450970c9d86ebbbd8ae22ecbed3",
            ),
        ),
        runtime="whisper.cpp server (external process)",
        auto_configured=False,
        operator_note=(
            "Start whisper-server with -m models/whisper-large-v3-turbo/"
            "whisper-large-v3-turbo-q4_0.gguf --port 9000, then set "
            "MS_STT__WHISPER_SERVER__URL=http://127.0.0.1:9000 (see "
            "docs/MODELS.md; docker compose --profile stt does this for you)."
        ),
        providers=("whisper-local",),
    ),
    ModelSpec(
        id="shenava-koochik",
        name="Shenava Koochik v1.0 (INT8, streaming)",
        role=ModelRole.STT_SHENAVA,
        description=(
            "Persian FastConformer cache-aware CTC ASR (114M params, "
            "FLEURS-fa WER ≈ 11%). Runs in-process via sherpa-onnx with true "
            "incremental decoding — no external service, becomes selectable "
            "the moment the download completes."
        ),
        license="Apache-2.0",
        license_url="https://huggingface.co/Reza2kn/Shenava-Koochik-v1.0-tract-streaming/blob/main/LICENSE",
        source_repo="Reza2kn/Shenava-Koochik-v1.0-tract-streaming",
        files=(
            ModelFileSpec(
                repo="Reza2kn/Shenava-Koochik-v1.0-tract-streaming",
                filename="model.int8.onnx",
                local_name="model.int8.onnx",
                size_bytes=174_269_452,
                sha256="f902c2a1027930fbc00c565af24365ab998e86fdbf90a29d18a1915cae789787",
            ),
            ModelFileSpec(
                repo="Reza2kn/Shenava-Koochik-v1.0-tract-streaming",
                filename="tokens.txt",
                local_name="tokens.txt",
                size_bytes=12_236,
            ),
        ),
        runtime="in-process (sherpa-onnx; requires the 'local-ai' extra)",
        auto_configured=True,
        providers=("shenava",),
    ),
    ModelSpec(
        id="minicpm5-2b",
        name="MiniCPM5 2B (Q4_K_M)",
        role=ModelRole.LLM_LLAMA,
        description=(
            "OpenBMB MiniCPM5 2B instruct LLM (128k context) for grounded "
            "note drafting. Served by the external llama-server (llama.cpp)."
        ),
        license="Apache-2.0",
        license_url="https://huggingface.co/openbmb/MiniCPM5-2B-GGUF/blob/main/README.md",
        source_repo="openbmb/MiniCPM5-2B-GGUF",
        files=(
            ModelFileSpec(
                repo="openbmb/MiniCPM5-2B-GGUF",
                filename="MiniCPM5-2B-Q4_K_M.gguf",
                local_name="MiniCPM5-2B-Q4_K_M.gguf",
                size_bytes=1_561_318_368,
                sha256="ec2d5801640099e97d8d7e8003ad4d81f336e757811f03a26173dddf386602fd",
            ),
        ),
        runtime="llama-server (external process)",
        auto_configured=False,
        operator_note=(
            "Restart llama-server with -m models/minicpm5-2b/MiniCPM5-2B-Q4_K_M.gguf "
            "(docker compose --profile llm picks MS_LLM_GGUF automatically)."
        ),
        providers=("llama-server",),
    ),
    ModelSpec(
        id="jibay-2",
        name="Jibay 2 (Q4_K_M)",
        role=ModelRole.LLM_LLAMA,
        description=(
            "JibayAi Jibay 2 — lightweight Persian/English LLM (Qwen3-based, "
            "2B params) for grounded note drafting. Served by the external "
            "llama-server (llama.cpp)."
        ),
        license="Apache-2.0",
        license_url="https://huggingface.co/JibayAi/Jibay_2_GGUF_Q4-K-M/blob/main/LICENSE",
        source_repo="JibayAi/Jibay_2_GGUF_Q4-K-M",
        files=(
            ModelFileSpec(
                repo="JibayAi/Jibay_2_GGUF_Q4-K-M",
                filename="Jibay2_Q4_K_M.gguf",
                local_name="Jibay2_Q4_K_M.gguf",
                size_bytes=1_282_439_840,
                sha256="ff6f107457d084f3ed3ccdfdf5e123a1c3a5f7376db6fb935bf195ece87e01e0",
            ),
        ),
        runtime="llama-server (external process)",
        auto_configured=False,
        operator_note=(
            "Restart llama-server with -m models/jibay-2/Jibay2_Q4_K_M.gguf "
            "(docker compose --profile llm picks MS_LLM_GGUF automatically)."
        ),
        providers=("llama-server",),
    ),
    ModelSpec(
        id="persian-pii-tookabert",
        name="OpenMed Persian PII (TookaBERT-Large INT4)",
        role=ModelRole.PII_REDACTION,
        description=(
            "Persian medical PII token-classification model (TookaBERT-Large, "
            "INT4 ONNX, held-out F1 ≈ 0.98). Upgrades pre-cloud PHI redaction "
            "from regex-only to NER-based (names, phones, national IDs, "
            "addresses, cards) with sliding-window inference. Runs in-process; "
            "active the moment the download completes."
        ),
        license="CC-BY-4.0",
        license_url="https://huggingface.co/Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4/blob/main/README.md",
        source_repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
        files=(
            ModelFileSpec(
                repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
                filename="model.onnx",
                local_name="model.onnx",
                size_bytes=360_670_490,
                sha256="88e8bcf61c0212196437997599d43f157718112516473143b2a5d897613a6c94",
            ),
            ModelFileSpec(
                repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
                filename="config.json",
                local_name="config.json",
                size_bytes=2_421,
            ),
            ModelFileSpec(
                repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
                filename="tokenizer.json",
                local_name="tokenizer.json",
                size_bytes=3_974_537,
            ),
            ModelFileSpec(
                repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
                filename="tokenizer_config.json",
                local_name="tokenizer_config.json",
                size_bytes=1_357,
            ),
            ModelFileSpec(
                repo="Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4",
                filename="special_tokens_map.json",
                local_name="special_tokens_map.json",
                size_bytes=828,
            ),
        ),
        runtime="in-process (onnxruntime + tokenizers; 'local-ai' extra)",
        auto_configured=True,
        providers=("redaction",),
    ),
)


def get_spec(model_id: str) -> ModelSpec | None:
    for spec in CATALOG:
        if spec.id == model_id:
            return spec
    return None
