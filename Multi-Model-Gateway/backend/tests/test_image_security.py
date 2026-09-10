"""Unit tests for app.services.image_security.validate_image_ref.

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): validate_image_ref is a pure function over strings. The module is
stdlib-only so the import should always succeed, but the try/except skip
pattern is kept so the suite degrades cleanly if `app` isn't importable.
"""
import unittest

try:
    from app.services.image_security import ALLOWED_IMAGE_TYPES, validate_image_ref
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    ALLOWED_IMAGE_TYPES = None
    validate_image_ref = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class ValidateImageRefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if validate_image_ref is None:
            raise unittest.SkipTest(
                f"app.services.image_security import failed in this env: {_IMPORT_ERROR}"
            )

    def test_valid_filename_passes(self):
        filename, subfolder, type = validate_image_ref("image_123.png")
        self.assertEqual((filename, subfolder, type), ("image_123.png", "", "output"))

    def test_valid_subfolder_and_whitelisted_type_passes(self):
        filename, subfolder, type = validate_image_ref("image.png", "batch42", "temp")
        self.assertEqual((filename, subfolder, type), ("image.png", "batch42", "temp"))

    def test_defaults_round_trip(self):
        # "" subfolder and "output" type are the defaults and must pass
        filename, subfolder, type = validate_image_ref("a.png", "", "output")
        self.assertEqual((filename, subfolder, type), ("a.png", "", "output"))

    def test_empty_filename_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("")

    def test_dotdot_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("..")
        with self.assertRaises(ValueError):
            validate_image_ref("a..png")

    def test_slash_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a/b.png")

    def test_backslash_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a\\b.png")

    def test_overlong_filename_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a" * 256)

    def test_bad_characters_rejected(self):
        for bad in ("a:b.png", "a*b.png", "a?b.png", "a<b>.png", ".hidden.png"):
            with self.assertRaises(ValueError):
                validate_image_ref(bad)

    def test_nested_subfolder_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a.png", "sub/nested")

    def test_dotdot_subfolder_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a.png", "..")

    def test_overlong_subfolder_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a.png", "s" * 201)

    def test_each_allowed_type_accepted(self):
        for t in ALLOWED_IMAGE_TYPES:
            filename, subfolder, type = validate_image_ref("a.png", "", t)
            self.assertEqual((filename, subfolder, type), ("a.png", "", t))

    def test_empty_type_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a.png", type="")

    def test_type_not_whitelisted_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_ref("a.png", type="system")


if __name__ == "__main__":
    unittest.main()
