"""Application configuration — pydantic-settings.

All secrets stay server-side; the WPF client receives a *manifest*, never
these values (docs/ARCHITECTURE.md). Env style: ``MS_`` prefix, nested via
``__``:  ``MS_LLM__LLAMA_SERVER__BASE_URL=http://127.0.0.1:8080``.

.env is loaded from the process CWD (repo root or container /app).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    #: "dev" | "production" — production disables API docs and dev tokens
    env: Literal["dev", "production"] = "dev"
    public_base_url: str | None = None
    log_level: str = "INFO"
    log_json: bool = False


class CorsConfig(BaseModel):
    #: WPF does not need CORS (non-browser client); this exists for future
    #: browser-based admin tooling. Keep it locked down.
    allow_origins: list[str] = Field(default_factory=list)
    allow_credentials: bool = False


class AuthConfig(BaseModel):
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    access_ttl_minutes: int = 30
    refresh_ttl_days: int = 14
    #: dev-only static bearer token accepted as the "dev operator" principal.
    #: Never enabled when env == "production".
    dev_token: SecretStr | None = None
    # Phase 7 — credential auth
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # KiB
    argon2_parallelism: int = 4
    lockout_max_failures: int = 5
    lockout_seconds: int = 300
    #: bootstrap the admin user when a password is provided (never defaulted)
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: SecretStr | None = None


class DatabaseConfig(BaseModel):
    url: str | None = None  # postgresql+asyncpg://user:pw@host:5432/medicalscribe
    echo: bool = False
    pool_size: int = 10
    #: create_all on boot (dev convenience). Production uses alembic migrations.
    auto_create: bool = True


class SecurityConfig(BaseModel):
    """Phase 7 — provider-secret encryption key (Fernet). Server-side only."""

    secret_encryption_key: SecretStr | None = None


class ObservabilityConfig(BaseModel):
    """Phase 8 — /metrics is always on; OTel tracing is opt-in (needs the
    `observability` extra) and follows the standard OTLP env contract."""

    otel_enabled: bool = False


class RedisConfig(BaseModel):
    url: str | None = None  # redis://host:6379/0


class RateLimitConfig(BaseModel):
    enabled: bool = True
    requests_per_minute: int = 120
    auth_per_minute: int = 10
    window_seconds: int = 60
    #: max simultaneous WS transcription sessions per user (spec §14)
    ws_sessions_per_user: int = 5


class LlamaServerConfig(BaseModel):
    """External llama.cpp server (OpenAI-compatible). Never exposed to WPF."""

    base_url: str | None = None
    model: str = "local"
    api_key: SecretStr | None = None
    timeout_s: float = 120.0
    max_retries: int = 2
    streaming: bool = True


class CloudLlmConfig(BaseModel):
    """Phase 4 providers. Keys are read server-side only."""

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None  # egress proxy / Azure-style gateway
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_base_url: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str | None = None
    timeout_s: float = 120.0
    max_retries: int = 2
    #: best-effort PHI scrub of transcript/context before ANY cloud provider
    #: sees it (privacy_required encounters never route cloud anyway)
    redact_phi_for_cloud: bool = True


class LlmConfig(BaseModel):
    llama_server: LlamaServerConfig = Field(default_factory=LlamaServerConfig)
    cloud: CloudLlmConfig = Field(default_factory=CloudLlmConfig)
    default_provider: str | None = None  # registry name; None → router decides
    #: note-drafting knobs (POST /api/v1/reports/{encounter}/draft)
    report_temperature: float = 0.2
    report_max_tokens: int = 1600
    #: single strict-JSON repair round-trip on unparseable model output
    repair_enabled: bool = True


class WhisperServerConfig(BaseModel):
    """whisper.cpp whisper-server (external process; never spawned here)."""

    url: str | None = None  # e.g. http://127.0.0.1:8001 — routes only if set
    model: str = "large-v3"
    timeout_s: float = 300.0
    api_key: SecretStr | None = None  # optional reverse-proxy bearer auth
    #: medical hotwords → whisper initial_prompt (embedded-English terms)
    hotwords: list[str] = []
    # VAD-windowed pseudo-streaming (ai/stt/_common.VadSegmenter)
    vad_silence_ms: int = 600
    vad_interim_ms: int = 1500
    vad_pre_roll_ms: int = 300
    vad_threshold: float = 0.02
    max_segment_ms: int = 30000


class QwenAsrConfig(BaseModel):
    """Qwen2-Audio served behind an OpenAI-audio-compatible HTTP endpoint."""

    url: str | None = None
    model: str = "qwen2-audio-instruct"
    timeout_s: float = 180.0
    api_key: SecretStr | None = None
    vad_silence_ms: int = 600
    vad_interim_ms: int = 2000
    vad_pre_roll_ms: int = 300
    vad_threshold: float = 0.02
    max_segment_ms: int = 20000


class SpeechmaticsConfig(BaseModel):
    api_key: SecretStr | None = None
    region: str = "eu2"  # eu2 | us
    batch_url: str | None = None  # override (defaults from region)
    rt_url: str | None = None
    language: str = "fa"  # batch default; per-request language wins
    operating_domain: str = "general"  # speechmatics operating domain
    timeout_s: float = 60.0
    job_poll_interval_s: float = 1.0
    job_timeout_s: float = 300.0
    max_retries: int = 2


class DeepgramConfig(BaseModel):
    api_key: SecretStr | None = None
    model: str = "nova-2-general"
    base_url: str | None = None  # https proxy override for self-hosted/egress
    ws_url: str | None = None
    endpointing_ms: int = 300
    timeout_s: float = 60.0
    #: baseline keyword boosting (drug names, laterality terms); per-request
    #: context hints are appended to this
    keywords: list[str] = []


class SttConfig(BaseModel):
    default_provider: str = "mock"
    #: per-interim pacing for the mock provider (dev demo realism; tests use 0)
    mock_interim_delay_s: float = 0.05
    whisper_server: WhisperServerConfig = Field(default_factory=WhisperServerConfig)
    qwen_asr: QwenAsrConfig = Field(default_factory=QwenAsrConfig)
    speechmatics: SpeechmaticsConfig = Field(default_factory=SpeechmaticsConfig)
    deepgram: DeepgramConfig = Field(default_factory=DeepgramConfig)
    #: batch transcribe upload cap in bytes (413 beyond this)
    batch_max_bytes: int = 64 * 1024 * 1024


class RoutingConfig(BaseModel):
    """AI router policy defaults (clients may override per request)."""

    mode: Literal["local", "cloud", "hybrid", "auto"] = "auto"
    #: when True every encounter is treated as privacy_required → local only
    privacy_default: bool = True
    #: provider failure count before temporary demotion (ai.router.health)
    failure_threshold: int = 3
    health_cooldown_s: float = 30.0
    #: Phase 5 — transparent runtime fallback over RouteDecision.fallbacks
    fallback_enabled: bool = True
    #: Phase 5 — daily LLM token budget; 0 disables the guard. Exhaustion
    #: soft-stops cloud providers (local keeps working), see services/cost.py
    budget_tokens_per_day: int = 0
    #: Phase 5 — Redis health-mirror publish interval (0 disables mirroring)
    health_mirror_interval_s: float = 0.0


class WebsocketConfig(BaseModel):
    path: str = "/ws/v1/transcribe"
    max_message_bytes: int = 1_048_576  # 1 MiB control or audio frame
    heartbeat_seconds: int = 30
    max_session_minutes: int = 120
    # Phase 2 — audio pipeline
    pause_buffer_ms: int = 2000
    provider_flush_timeout_s: float = 10.0
    audio_queue_maxsize: int = 512


class AuditConfig(BaseModel):
    #: JSONL file for compliance events (no transcripts; see services/audit)
    log_file: str = "logs/audit.jsonl"
    enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    websocket: WebsocketConfig = Field(default_factory=WebsocketConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @property
    def is_production(self) -> bool:
        return self.server.env == "production"

    @model_validator(mode="after")
    def _production_fail_closed(self) -> Settings:
        """Production must carry real auth material — enforced on the MODEL so
        direct Settings(...) construction cannot bypass it (the loader's check
        stays as a redundant belt-and-braces)."""
        if self.is_production:
            secret = self.auth.jwt_secret
            if secret is None or len(secret.get_secret_value()) < 32:
                raise ValueError(
                    "production requires MS_AUTH__JWT_SECRET with >= 32 chars "
                    "(openssl rand -base64 48)"
                )
        return self

    def provider_config(self, kind: str, name: str) -> dict:
        """Project the settings slice for a provider into the factory dict.

        Centralizing this keeps secrets out of ad-hoc dict-building and gives
        the registry a stable contract. SecretStr values are unwrapped here —
        the returned dict may contain secrets and must never be logged.
        """
        if kind == "llm" and name == "llama-server":
            ls = self.llm.llama_server
            return {
                "base_url": ls.base_url or "",
                "model": ls.model,
                "api_key": ls.api_key.get_secret_value() if ls.api_key else None,
                "timeout_s": ls.timeout_s,
                "max_retries": ls.max_retries,
                "streaming": ls.streaming,
            }
        if kind == "llm" and name == "mock":
            return {}
        cl = self.llm.cloud
        if kind == "llm" and name == "openai":
            return {
                "base_url": cl.openai_base_url or "https://api.openai.com",
                "model": cl.openai_model,
                "api_key": cl.openai_api_key.get_secret_value() if cl.openai_api_key else None,
                "timeout_s": cl.timeout_s,
                "max_retries": cl.max_retries,
            }
        if kind == "llm" and name == "anthropic":
            return {
                "base_url": cl.anthropic_base_url or "https://api.anthropic.com",
                "model": cl.anthropic_model,
                "api_key": cl.anthropic_api_key.get_secret_value() if cl.anthropic_api_key else None,
                "timeout_s": cl.timeout_s,
                "max_retries": cl.max_retries,
            }
        if kind == "llm" and name == "gemini":
            return {
                "base_url": cl.gemini_base_url or "https://generativelanguage.googleapis.com",
                "model": cl.gemini_model,
                "api_key": cl.gemini_api_key.get_secret_value() if cl.gemini_api_key else None,
                "timeout_s": cl.timeout_s,
                "max_retries": cl.max_retries,
            }
        if kind == "stt" and name == "mock":
            return {"interim_delay_s": self.stt.mock_interim_delay_s}
        if kind == "stt" and name == "whisper-local":
            w = self.stt.whisper_server
            return {
                "url": w.url or "",
                "model": w.model,
                "timeout_s": w.timeout_s,
                "api_key": w.api_key.get_secret_value() if w.api_key else None,
                "hotwords": list(w.hotwords),
                "vad_silence_ms": w.vad_silence_ms,
                "vad_interim_ms": w.vad_interim_ms,
                "vad_pre_roll_ms": w.vad_pre_roll_ms,
                "vad_threshold": w.vad_threshold,
                "max_segment_ms": w.max_segment_ms,
            }
        if kind == "stt" and name == "qwen-asr":
            q = self.stt.qwen_asr
            return {
                "url": q.url or "",
                "model": q.model,
                "timeout_s": q.timeout_s,
                "api_key": q.api_key.get_secret_value() if q.api_key else None,
                "vad_silence_ms": q.vad_silence_ms,
                "vad_interim_ms": q.vad_interim_ms,
                "vad_pre_roll_ms": q.vad_pre_roll_ms,
                "vad_threshold": q.vad_threshold,
                "max_segment_ms": q.max_segment_ms,
            }
        if kind == "stt" and name == "speechmatics":
            sm = self.stt.speechmatics
            return {
                "api_key": sm.api_key.get_secret_value() if sm.api_key else None,
                "region": sm.region,
                "batch_url": sm.batch_url,
                "rt_url": sm.rt_url,
                "language": sm.language,
                "operating_domain": sm.operating_domain,
                "timeout_s": sm.timeout_s,
                "job_poll_interval_s": sm.job_poll_interval_s,
                "job_timeout_s": sm.job_timeout_s,
                "max_retries": sm.max_retries,
            }
        if kind == "stt" and name == "deepgram":
            dg = self.stt.deepgram
            return {
                "api_key": dg.api_key.get_secret_value() if dg.api_key else None,
                "model": dg.model,
                "base_url": dg.base_url,
                "ws_url": dg.ws_url,
                "endpointing_ms": dg.endpointing_ms,
                "timeout_s": dg.timeout_s,
                "keywords": list(dg.keywords),
            }
        return {}


@lru_cache
def get_settings() -> Settings:
    # production's fail-closed secret check is a model validator — it fires
    # here and on every direct Settings(...) construction alike
    return Settings()
