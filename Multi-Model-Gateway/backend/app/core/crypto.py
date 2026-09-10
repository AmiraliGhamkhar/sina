"""Fernet-based encryption for secrets at rest (user-provided provider API keys).

Key material comes from settings.KEY_ENCRYPTION_KEY when set (an explicit 32-byte
urlsafe base64 Fernet key), otherwise it is derived from settings.SECRET_KEY via
SHA-256 so existing deployments work without adding an env var. Deriving from
SECRET_KEY is weaker than a dedicated key, hence the explicit override.

Never log plaintext keys; callers must not either.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    """Build (and cache) the module's Fernet instance from settings."""
    global _fernet_instance
    if _fernet_instance is None:
        if settings.KEY_ENCRYPTION_KEY:
            raw = settings.KEY_ENCRYPTION_KEY
            try:
                decoded = base64.urlsafe_b64decode(raw)
            except Exception as exc:
                raise ValueError(
                    "KEY_ENCRYPTION_KEY must be a urlsafe base64-encoded 32-byte Fernet key"
                ) from exc
            if len(decoded) != 32:
                raise ValueError(
                    "KEY_ENCRYPTION_KEY must decode to exactly 32 bytes (a Fernet key)"
                )
            _fernet_instance = Fernet(raw)
        else:
            derived = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
            _fernet_instance = Fernet(derived)
    return _fernet_instance


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret and return the Fernet token as a str."""
    if not plaintext:
        raise ValueError("cannot encrypt an empty secret")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet token back to the plaintext secret.

    Any failure — wrong key, garbage token, malformed input — surfaces as a
    ValueError so callers can treat "could not decrypt" uniformly.
    """
    if not isinstance(token, str):
        raise ValueError("cannot decrypt a non-string token")
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (ValueError, AttributeError, TypeError, UnicodeDecodeError, InvalidToken) as exc:
        raise ValueError(
            "could not decrypt provider key — KEY_ENCRYPTION_KEY/SECRET_KEY changed?"
        ) from exc
