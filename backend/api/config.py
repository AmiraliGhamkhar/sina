"""Application configuration — pydantic-settings.

All secrets stay server-side; the WPF client receives a *manifest*, never
these values (docs/ARCHITECTURE.md). Env style: ``MS_`` prefix, nested via
``__``:  ``MS_LLM__LLAMA_SERVER__BASE_URL=http://127.0.0.1:8080``.

.env is loaded from the process CWD (repo root or container /app).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
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


class DatabaseConfig(BaseModel):
    url: str | None = None  # postgresql+asyncpg://user:pw@host:5432/medicalscribe
    echo: bool = False
    pool_size: int = 10


class RedisConfig(BaseModel):
    url: str | None = None  # redis://host:6379/0


class RateLimitConfig(BaseModel):
    enabled: bool = True
    requests_per_minute: int = 120
    auth_per_minute: int = 10
    window_seconds: int = 60


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
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.0-flash"


class LlmConfig(BaseModel):
    llama_server: LlamaServerConfig = Field(default_factory=LlamaServerConfig)
    cloud: CloudLlmConfig = Field(default_factory=CloudLlmConfig)
    default_provider: str | None = None  # registry name; None → router decides


class WhisperServerConfig(BaseModel):
    url: str | None = None  # whisper.cpp/whisper-server HTTP endpoint (Phase 3)
    model: str = "large-v3"
    timeout_s: float = 300.0


class QwenAsrConfig(BaseModel):
    url: str | None = None  # Phase 3


class SpeechmaticsConfig(BaseModel):
    api_key: SecretStr | None = None  # Phase 3
    region: str = "eu2"


class DeepgramConfig(BaseModel):
    api_key: SecretStr | None = None  # Phase 3
    model: str = "nova-2-general"


class SttConfig(BaseModel):
    default_provider: str = "mock"
    whisper_server: WhisperServerConfig = Field(default_factory=WhisperServerConfig)
    qwen_asr: QwenAsrConfig = Field(default_factory=QwenAsrConfig)
    speechmatics: SpeechmaticsConfig = Field(default_factory=SpeechmaticsConfig)
    deepgram: DeepgramConfig = Field(default_factory=DeepgramConfig)


class RoutingConfig(BaseModel):
    """AI router policy defaults (clients may override per request)."""

    mode: Literal["local", "cloud", "hybrid", "auto"] = "auto"
    #: when True every encounter is treated as privacy_required → local only
    privacy_default: bool = True
    #: provider failure count before temporary demotion (ai.router.health)
    failure_threshold: int = 3
    health_cooldown_s: float = 30.0


class WebsocketConfig(BaseModel):
    path: str = "/ws/v1/transcribe"
    max_message_bytes: int = 1_048_576  # 1 MiB control or audio frame
    heartbeat_seconds: int = 30
    max_session_minutes: int = 120


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

    @property
    def is_production(self) -> bool:
        return self.server.env == "production"

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
        if kind == "stt" and name == "mock":
            return {}
        return {}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # fail-closed: production must have real auth material
    if settings.is_production:
        if not settings.auth.jwt_secret or len(settings.auth.jwt_secret.get_secret_value()) < 32:
            raise RuntimeError(
                "production requires MS_AUTH__JWT_SECRET with >= 32 chars (openssl rand -base64 48)"
            )
    return settings
