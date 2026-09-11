"""Auth wire shapes — frozen now so the WPF client codes against the final
contract while the implementation lands in Phase 7.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)
    device_name: str | None = Field(default=None, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="access token TTL, seconds")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    """Optional body: pass the refresh token to revoke it server-side."""
    refresh_token: str | None = Field(default=None, max_length=4096)


class PrincipalInfo(BaseModel):
    user_id: str
    role: str
    is_dev: bool = False

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        allowed = {"admin", "physician", "clinician", "scribe", "auditor"}
        if v not in allowed:
            raise ValueError(f"role must be one of {sorted(allowed)}")
        return v
