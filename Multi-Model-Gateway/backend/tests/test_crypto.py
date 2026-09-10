"""Unit tests for app.core.crypto (Fernet secret encryption) and the
provider_registry.mask_key helper.

Stdlib unittest only — no pytest dependency. All tests run offline:
encryption is pure local cryptography, no network or database involved.
"""
import unittest

from app.core import crypto


class EncryptDecryptTests(unittest.TestCase):
    def test_round_trip(self):
        original = "sk-test-provider-key-123456"
        token = crypto.encrypt_secret(original)
        self.assertEqual(crypto.decrypt_secret(token), original)

    def test_token_differs_from_plaintext(self):
        original = "sk-test-provider-key-123456"
        token = crypto.encrypt_secret(original)
        self.assertNotEqual(token, original)

    def test_encrypt_empty_raises(self):
        with self.assertRaises(ValueError):
            crypto.encrypt_secret("")

    def test_decrypt_garbage_raises(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_secret("not-a-fern-token")

    def test_decrypt_non_string_raises(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_secret(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            crypto.decrypt_secret(12345)  # type: ignore[arg-type]

    def test_wrong_key_raises(self):
        """A token encrypted under one derived key must refuse to decrypt under
        a different one (simulates KEY_ENCRYPTION_KEY/SECRET_KEY changing)."""
        token = crypto.encrypt_secret("super-secret")
        original_key = crypto.settings.SECRET_KEY
        crypto._fernet_instance = None
        try:
            crypto.settings.SECRET_KEY = "a-completely-different-secret-key"
            with self.assertRaises(ValueError):
                crypto.decrypt_secret(token)
        finally:
            crypto.settings.SECRET_KEY = original_key
            crypto._fernet_instance = None


class MaskKeyTests(unittest.TestCase):
    def test_mask_key(self):
        try:
            from app.services.provider_registry import mask_key
        except ImportError as exc:  # env may lack DB/secret config; skip cleanly
            self.skipTest(f"provider_registry import failed in this env: {exc}")
        self.assertEqual(mask_key("abcd"), "****")
        self.assertEqual(mask_key("abcdefgh"), "****efgh")
        self.assertEqual(mask_key(""), "****")


if __name__ == "__main__":
    unittest.main()
