"""PII NER redaction tests — fake onnxruntime/tokenizers runtimes
(tests/local_ai_fakes.py): span merging, label policy, sliding windows,
regex fallback, model-dir auto-resolution. No wheels, no model files."""

import asyncio
import json

from api.config import CloudLlmConfig, ModelsConfig
from api.services.pii_ner import (
    PII_MODEL_ID,
    PiiSpan,
    RedactionService,
    _apply_spans,
    _merge_spans,
)

from tests.local_ai_fakes import (
    FakeEncoding,
    FakeOrtSession,
    FakeTokenizer,
    install_fake_numpy,
    install_fake_ort,
    install_fake_tokenizers,
)


def _span(start, end, label):
    return PiiSpan(start, end, label)


# -- pure span postprocessing (model card contract) ----------------------------


def test_merge_trims_whitespace_edges():
    # "بیمار علی است": علی = chars 6..9 (single spaces)
    text = "بیمار علی است"
    assert text[6:9] == "علی"
    spans = _merge_spans([_span(5, 10, "GIVENNAME")], text)  # " علی "
    assert spans == [PiiSpan(6, 9, "GIVENNAME")]


def test_merge_adjacent_same_label_bridges_whitespace():
    # "محمد رضایی آمد": محمد=0..4, رضایی=5..10
    text = "محمد رضایی آمد"
    assert text[0:4] == "محمد" and text[5:10] == "رضایی"
    # token offsets include the leading space → gapped spans of one entity
    spans = _merge_spans([_span(0, 4, "GIVENNAME"), _span(5, 10, "GIVENNAME")], text)
    assert spans == [PiiSpan(0, 10, "GIVENNAME")]  # "محمد رضایی"
    # touching offsets merge the same way
    spans2 = _merge_spans([_span(0, 4, "GIVENNAME"), _span(4, 10, "GIVENNAME")], text)
    assert spans2 == [PiiSpan(0, 10, "GIVENNAME")]


def test_merge_does_not_bridge_non_whitespace_gaps():
    text = "علی;رضایی"
    spans = _merge_spans([_span(0, 3, "GIVENNAME"), _span(4, 9, "GIVENNAME")], text)
    assert len(spans) == 2  # ";" between them → separate spans


def test_merge_overlapping_windows_dedupes():
    text = "شماره ۰۹۱۲۳۴۵۶۷۸ اینجا"
    assert text[6:16] == "۰۹۱۲۳۴۵۶۷۸"
    spans = _merge_spans([_span(5, 16, "TELEPHONENUM"), _span(5, 16, "TELEPHONENUM")], text)
    assert spans == [_span(6, 16, "TELEPHONENUM")]


def test_merge_keeps_wider_span_over_contained():
    text = "یکدو سه"
    spans = _merge_spans([_span(0, 1, "GIVENNAME"), _span(0, 3, "GIVENNAME")], text)
    assert spans == [_span(0, 3, "GIVENNAME")]


def test_apply_spans_replaces_right_to_left():
    text = "آ ب پ"
    out = _apply_spans(text, [_span(0, 1, "GIVENNAME"), _span(4, 5, "EMAIL")])
    assert out == "[REDACTED:name] ب [REDACTED:email]"


def test_merge_drops_empty_and_whitespace_only_spans():
    spans = _merge_spans([_span(2, 2, "CITY"), _span(0, 2, "CITY")], "ab c")
    assert spans == [_span(0, 2, "CITY")]


# -- service: fallback + availability ------------------------------------------


def _service(tmp_path, **cloud_overrides) -> RedactionService:
    cloud = CloudLlmConfig(**cloud_overrides)
    return RedactionService(cloud, ModelsConfig(dir=str(tmp_path)))


def test_regex_only_when_model_absent(tmp_path):
    svc = _service(tmp_path)
    assert svc.ner_available is False
    text = "تماس بگیرید ۰۹۱۲۱۲۳۴۵۶۷ یا 09121234567"
    out = asyncio.run(svc.redact(text))
    assert "[REDACTED:phone]" in out
    assert "09121234567" not in out


def test_ner_disabled_falls_back_to_regex(tmp_path):
    model_dir = tmp_path / PII_MODEL_ID
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"x")
    (model_dir / "tokenizer.json").write_bytes(b"{}")
    svc = _service(tmp_path, pii_ner_enabled=False)
    assert svc.ner_available is False


def test_model_dir_explicit_config_wins(tmp_path):
    explicit = tmp_path / "custom-pii"
    explicit.mkdir()
    (explicit / "model.onnx").write_bytes(b"x")
    (explicit / "tokenizer.json").write_bytes(b"{}")
    svc = _service(tmp_path, pii_model_dir=str(explicit))
    assert svc.model_dir == explicit


def test_model_dir_auto_resolves_from_models_dir(tmp_path):
    model_dir = tmp_path / PII_MODEL_ID
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"x")
    (model_dir / "tokenizer.json").write_bytes(b"{}")
    svc = _service(tmp_path)
    assert svc.model_dir == model_dir
    assert svc.ner_available is True


# -- engine against fake ORT + tokenizer --------------------------------------


ID2LABEL = {"0": "O", "1": "B-GIVENNAME", "2": "I-GIVENNAME", "3": "B-DATE"}


class ScriptedSession(FakeOrtSession):
    """Returns the scripted logits rows (shape [1, seq, labels] stand-in)."""

    next_logits: list[list[float]] = None  # noqa: RUF012 - test scripting

    def run(self, _outputs, feeds):
        from tests.local_ai_fakes import FakeOrtLogits

        self.runs.append(feeds)
        # real ORT: run()[0] → [batch, seq, labels]; adapter takes [0][0]
        return [[FakeOrtLogits(list(type(self).next_logits))]]


def _install_engine(monkeypatch, tmp_path, *, labels=None, logits=None):
    model_dir = tmp_path / PII_MODEL_ID
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.onnx").write_bytes(b"x")
    (model_dir / "tokenizer.json").write_bytes(b"{}")
    (model_dir / "config.json").write_text(
        json.dumps({"id2label": labels or ID2LABEL}), encoding="utf-8"
    )

    install_fake_numpy(monkeypatch)
    install_fake_ort(monkeypatch, session_cls=ScriptedSession)
    ScriptedSession.next_logits = logits or []

    # fake tokenizer: whole text as 3 tokens, offsets covering it
    tokenizer = FakeTokenizer()
    install_fake_tokenizers(monkeypatch, tokenizer)
    return model_dir, tokenizer


def _tok_offsets_for(text: str) -> list[tuple[int, int]]:
    """Split text into word-ish chunks with real char offsets."""
    parts = text.split(" ")
    offsets, pos = [], 0
    for p in parts:
        offsets.append((pos, pos + len(p)))
        pos += len(p) + 1
    return offsets


def test_engine_redacts_name_span(monkeypatch, tmp_path):
    text = "بیمار علی رضایی مراجعه کرد"
    offs = _tok_offsets_for(text)  # [بیمار][علی][رضایی][مراجعه][کرد]
    # labels per token: O, B-GIVENNAME, I-GIVENNAME, O, O
    logits = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
    ]
    model_dir, tokenizer = _install_engine(monkeypatch, tmp_path, logits=logits)
    tokenizer.encodings_by_text = {
        text: [FakeEncoding(list(range(len(offs))), offs)],
    }

    svc = _service(tmp_path)
    out = asyncio.run(svc.redact(text))
    assert "علی رضایی" not in out
    assert "[REDACTED:name]" in out
    assert out.startswith("بیمار [REDACTED:name]")
    # truncation/padding configured per model card defaults
    assert tokenizer.truncation["max_length"] == 256
    assert tokenizer.truncation["stride"] == 96


def test_engine_label_policy_keeps_dates_by_default(monkeypatch, tmp_path):
    text = "در تاریخ ۱۴۰۳ وارد شد"
    offs = _tok_offsets_for(text)  # [در][تاریخ][۱۴۰۳][وارد][شد] — 5 tokens
    # DATE predicted on "۱۴۰۳" (token 2): rows are O, O, B-DATE, O, O
    logits = [
        [1.0, 0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
    ]
    model_dir, tokenizer = _install_engine(monkeypatch, tmp_path, logits=logits)
    tokenizer.encodings_by_text = {text: [FakeEncoding(list(range(len(offs))), offs)]}

    svc = _service(tmp_path)
    out = asyncio.run(svc.redact(text))
    assert "۱۴۰۳" in out  # clinically central: kept by default policy
    assert "[REDACTED" not in out

    # stricter deployment: add DATE to the label policy → redacted
    svc_strict = _service(tmp_path, pii_redact_labels=["DATE"])
    out_strict = asyncio.run(svc_strict.redact(text))
    assert "[REDACTED:date]" in out_strict


def test_engine_sliding_windows_merge_spans(monkeypatch, tmp_path):
    text = "علی از تهران آمد و مریم رفت"
    offs = _tok_offsets_for(text)
    # window 1 sees "علی ..." (B-GIVENNAME on token 0); window 2 (overflow)
    # also predicts علی — overlapping windows must not double-redact
    logits = [[0.0, 1.0, 0.0, 0.0]] + [[1.0, 0.0, 0.0, 0.0]] * (len(offs) - 1)
    model_dir, tokenizer = _install_engine(monkeypatch, tmp_path, logits=logits)
    tokenizer.encodings_by_text = {
        text: [
            FakeEncoding(list(range(len(offs))), offs),
            FakeEncoding(list(range(len(offs))), offs),  # overflowing window
        ],
    }

    svc = _service(tmp_path)
    out = asyncio.run(svc.redact(text))
    assert out.count("[REDACTED:name]") == 1


def test_engine_failure_degrades_to_regex(monkeypatch, tmp_path):
    text = "تماس ۰۹۱۲۱۲۳۴۵۶۷"
    model_dir, tokenizer = _install_engine(monkeypatch, tmp_path)

    class ExplodingSession(ScriptedSession):
        def run(self, _o, feeds):
            raise RuntimeError("onnx exploded")

    import sys

    ort_module = sys.modules["onnxruntime"]
    ort_module.session_cls = ExplodingSession

    tokenizer.encodings_by_text = {text: [FakeEncoding([1, 2], [(0, 0), (0, 0)])]}
    svc = _service(tmp_path)
    # engine init succeeds; inference raises → regex-only fallback, no crash
    out = asyncio.run(svc.redact(text))
    assert "[REDACTED:phone]" in out
    # second call also falls back (failed engine is dropped)
    assert "[REDACTED:phone]" in asyncio.run(svc.redact(text))


def test_engine_init_failure_degrades_to_regex(monkeypatch, tmp_path):
    text = "hello"
    _install_engine(monkeypatch, tmp_path)

    import sys

    class BadInit:
        def __init__(self, *a, **k):
            raise RuntimeError("cannot load onnx")

    ort_module = sys.modules["onnxruntime"]
    ort_module.session_cls = BadInit

    svc = _service(tmp_path)
    out = asyncio.run(svc.redact(text))
    assert out == text  # regex found nothing; NER silently unavailable
