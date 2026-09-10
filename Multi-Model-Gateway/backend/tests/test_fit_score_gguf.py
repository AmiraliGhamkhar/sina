"""Unit tests for the GGUF binary header walker (services/fit_score_gguf.py).

Stdlib unittest only — no pytest dependency. Tests are offline (no network): the
walker is pure over bytes. It needs only `struct` + `gguf`; if `gguf` isn't
installed in this environment the whole suite skips cleanly. This is the binary
-format submodule split out of fit_score.py (the F1 GGUF header reader).
"""
import unittest
import struct

try:
    from app.services import fit_score_gguf as ggufmod
    from app.services.fit_score_gguf import (
        _read_gguf_string,
        _read_gguf_value,
        parse_gguf_header,
        _GGUF_VALUE_FMT,
    )
except Exception as exc:  # noqa: BLE001 — env may lack gguf
    ggufmod = None
    _read_gguf_string = None
    _read_gguf_value = None
    parse_gguf_header = None
    _GGUF_VALUE_FMT = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _build_header(kv: list[tuple[str, object]]) -> bytes:
    """Minimal GGUF layout: magic, v3, counts, then the KV section."""
    payload = bytearray()

    def push_string(s: str):
        raw = s.encode("utf-8")
        payload.extend(struct.pack("<Q", len(raw)))
        payload.extend(raw)

    def push_u32(v: int):
        payload.extend(struct.pack("<I", v))

    def push_u64(v: int):
        payload.extend(struct.pack("<Q", v))

    payload.extend(b"GGUF")
    push_u32(3)
    push_u64(0)  # tensor_count (unused by walker)
    push_u64(len(kv))
    for key, value in kv:
        push_string(key)
        if isinstance(value, str):
            push_u32(8)  # GGUFValueType.STRING
            push_string(value)
        else:
            push_u32(4)  # GGUFValueType.UINT32
            push_u32(value)
    return bytes(payload)


class ReadGgufStringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _read_gguf_string is None:
            raise unittest.SkipTest(
                f"app.services.fit_score_gguf import failed in this env: {_IMPORT_ERROR}"
            )

    def test_reads_length_prefixed_string(self):
        data = struct.pack("<Q", 3) + b"abc"
        value, next_off = _read_gguf_string(data, 0)
        self.assertEqual(value, "abc")
        self.assertEqual(next_off, 11)

    def test_out_of_bounds_returns_none(self):
        self.assertEqual(_read_gguf_string(b"\x00\x00", 0), (None, 0))

    def test_truncated_payload_returns_none(self):
        # length says 100, but only 5 bytes follow. The walker advances past the
        # length prefix before the bounds check, so it reports the advanced offset.
        data = struct.pack("<Q", 100) + b"abcde"
        self.assertEqual(_read_gguf_string(data, 0), (None, 8))


class ReadGgufValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _read_gguf_value is None:
            raise unittest.SkipTest(
                f"app.services.fit_score_gguf import failed in this env: {_IMPORT_ERROR}"
            )

    def test_scalar_uint32(self):
        from gguf import GGUFValueType
        data = struct.pack("<I", 4096)
        value, next_off = _read_gguf_value(data, 0, GGUFValueType.UINT32)
        self.assertEqual(value, 4096)
        self.assertEqual(next_off, 4)

    def test_string_value(self):
        from gguf import GGUFValueType
        data = struct.pack("<Q", 5) + b"llama"
        value, next_off = _read_gguf_value(data, 0, GGUFValueType.STRING)
        self.assertEqual(value, "llama")
        self.assertEqual(next_off, 13)

    def test_skips_array(self):
        from gguf import GGUFValueType
        # u32 elem_type (UINT32=4), u64 count (3), then 3 × 4 bytes
        data = struct.pack("<I", 4) + struct.pack("<Q", 3) + struct.pack("<III", 1, 2, 3)
        value, next_off = _read_gguf_value(data, 0, GGUFValueType.ARRAY)
        self.assertEqual(value, ggufmod._ARRAY_SKIPPED)
        self.assertEqual(next_off, 12 + 12)


class ParseGgufHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if parse_gguf_header is None:
            raise unittest.SkipTest(
                f"app.services.fit_score_gguf import failed in this env: {_IMPORT_ERROR}"
            )

    def test_parses_architecture_fields(self):
        data = _build_header([
            ("general.architecture", "llama"),
            ("llama.block_count", 32),
            ("llama.embedding_length", 4096),
            ("llama.attention.head_count", 32),
            ("llama.attention.head_count_kv", 8),
            ("llama.context_length", 8192),
        ])
        meta = parse_gguf_header(data)
        self.assertEqual(meta["n_layer"], 32)
        self.assertEqual(meta["n_kv_head"], 8)
        self.assertEqual(meta["architecture"], "llama")

    def test_defaults_kv_head_to_head(self):
        data = _build_header([
            ("general.architecture", "llama"),
            ("llama.block_count", 32),
            ("llama.embedding_length", 4096),
            ("llama.attention.head_count", 16),
        ])
        meta = parse_gguf_header(data)
        self.assertEqual(meta["n_kv_head"], 16)

    def test_bad_magic_returns_none(self):
        self.assertIsNone(parse_gguf_header(b"\x00" * 24))

    def test_too_short_returns_none(self):
        self.assertIsNone(parse_gguf_header(b"GGUF"))

    def test_missing_required_fields_returns_none(self):
        data = _build_header([("general.architecture", "llama")])
        self.assertIsNone(parse_gguf_header(data))


class ValueFormatTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _GGUF_VALUE_FMT is None:
            raise unittest.SkipTest(
                f"app.services.fit_score_gguf import failed in this env: {_IMPORT_ERROR}"
            )

    def test_covers_all_gguf_scalar_types(self):
        from gguf import GGUFValueType
        for t in ("UINT8", "INT8", "UINT16", "INT16", "UINT32", "INT32",
                  "FLOAT32", "BOOL", "UINT64", "INT64", "FLOAT64"):
            self.assertIn(getattr(GGUFValueType, t), _GGUF_VALUE_FMT, t)


if __name__ == "__main__":
    unittest.main()
