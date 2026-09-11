"""Provider secret vault (Phase 7, spec §14 "provider secret storage").

Fernet-authenticated encryption (cryptography) with a server-side key
(``MS_SECURITY__SECRET_ENCRYPTION_KEY``, a Fernet key — generate with
``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).

Secrets at rest: ``ai_providers.secret_ciphertext`` only. Secrets are
decrypted in exactly ONE place — ``ai_bridge.provider_config_with_secrets``
— right before a provider factory consumes them, and never logged, never
returned by an API.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SecretVaultError(RuntimeError):
    pass


class SecretVault:
    def __init__(self, key: str | None) -> None:
        self._fernet = None
        if key:
            try:
                from cryptography.fernet import Fernet

                self._fernet = Fernet(key.encode("utf-8"))
            except ImportError as exc:  # pragma: no cover - extras missing
                raise SecretVaultError(
                    "MS_SECURITY__SECRET_ENCRYPTION_KEY set but `cryptography` missing "
                    "(install the security extra)"
                ) from exc
            except Exception as exc:
                raise SecretVaultError(f"invalid Fernet key: {exc}") from exc

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise SecretVaultError("secret storage disabled (no encryption key configured)")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if self._fernet is None:
            raise SecretVaultError("secret storage disabled (no encryption key configured)")
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretVaultError("secret ciphertext failed authentication (wrong key?)") from exc
