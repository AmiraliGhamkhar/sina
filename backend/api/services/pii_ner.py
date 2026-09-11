"""NER-based Persian PII redaction (openmed-persian-pii-tookabert INT4).

Upgrades the pre-cloud PHI scrub (regex-only :func:`note_prompt.redact_phi`)
with a token-classification model running in-process on onnxruntime:

- sliding-window inference (max_length 256 / stride 96 — the model card
  explicitly forbids a single truncated pass over long documents),
- span cleanup: trim whitespace, merge adjacent same-label spans, de-dupe
  overlapping window predictions by character offsets,
- regex/rule assists: the high-precision regex layer always runs too (union
  of both — the model card's recommended layering),
- a clinical label policy: identifiers (names, phones, IDs, addresses, cards)
  are masked; clinically central labels (AGE, DATE, SEX/GENDER, TITLE, CITY)
  are kept by default and are configurable via MS_LLM__CLOUD__PII_REDACT_LABELS.

Safety semantics (unchanged from the regex layer): this is a *pre-cloud
courtesy* layer, NOT a de-identification guarantee. The hard guarantee stays
the privacy wall — privacy_required encounters route LOCAL only, tested in
the routing suite. Any NER failure degrades to regex-only, never raises, and
never blocks note drafting.

Optional dependencies (``pip install ".[local-ai]"``): onnxruntime +
tokenizers. Without them (or before the model is downloaded) the regex layer
works exactly as before.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.config import CloudLlmConfig, ModelsConfig
from api.services.model_catalog import get_spec

logger = logging.getLogger(__name__)

#: catalog id of the PII model (model manager download folder)
PII_MODEL_ID = "persian-pii-tookabert"

#: labels masked by default (identifiers). Clinically central labels are
#: deliberately absent — see module docstring + CloudLlmConfig.pii_redact_labels.
DEFAULT_REDACT_LABELS = frozenset(CloudLlmConfig().pii_redact_labels)

#: label → redaction tag (stable wire format, mirrors note_prompt._REDACTIONS)
_LABEL_TAGS: dict[str, str] = {
    "GIVENNAME": "name",
    "SURNAME": "name",
    "TITLE": "title",
    "GENDER": "gender",
    "SEX": "sex",
    "AGE": "age",
    "DATE": "date",
    "EMAIL": "email",
    "TELEPHONENUM": "phone",
    "CITY": "city",
    "STREET": "street",
    "BUILDINGNUM": "address",
    "ZIPCODE": "postal-code",
    "IDCARDNUM": "national-id",
    "SOCIALNUM": "social-id",
    "TAXNUM": "tax-id",
    "PASSPORTNUM": "passport",
    "DRIVERLICENSENUM": "driver-license",
    "CREDITCARDNUMBER": "card-number",
}


@dataclass(frozen=True)
class PiiSpan:
    start: int
    end: int
    label: str

    @property
    def tag(self) -> str:
        return _LABEL_TAGS.get(self.label, self.label.lower())


class _NerEngine:
    """Lazy onnxruntime engine. Construction is heavy (~1 s); built once."""

    def __init__(self, model_dir: Path, *, max_length: int, stride: int) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        config_path = model_dir / "config.json"
        config: dict[str, Any] = {}
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        id2label = {int(k): str(v) for k, v in (config.get("id2label") or {}).items()}
        if not id2label:
            raise ValueError("PII config.json has no id2label mapping")
        self._id2label = id2label
        self._outside = {"O"}

        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=max_length, stride=stride)
        tokenizer.enable_padding(length=max_length)
        self._tokenizer = tokenizer

        self._session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}

    def _window_encodings(self, text: str) -> list[Any]:
        enc = self._tokenizer.encode(text)
        return [enc, *enc.overflowing]

    def _run_window(self, enc: Any) -> list[PiiSpan]:
        import numpy as np

        ids = np.asarray([enc.ids], dtype=np.int64)
        mask = np.asarray([enc.attention_mask], dtype=np.int64)
        feeds: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        logits = self._session.run(None, feeds)[0][0]  # [seq, labels]
        spans: list[PiiSpan] = []
        current: PiiSpan | None = None
        for idx, (token_label_id, offset) in enumerate(
            zip(logits.argmax(axis=-1), enc.offsets, strict=False)
        ):
            if not enc.attention_mask[idx]:
                break
            start, end = offset
            label = self._id2label.get(int(token_label_id), "O")
            if start == end or label in self._outside or not label:
                # special / outside tokens CLOSE an open entity span
                if current is not None:
                    spans.append(current)
                    current = None
                continue
            if label.startswith("B-") or current is None or current.label != label[2:]:
                if current is not None:
                    spans.append(current)
                current = PiiSpan(start, end, label[2:])
            else:  # I- continuation
                current = PiiSpan(current.start, end, current.label)
        if current is not None:
            spans.append(current)
        return spans

    def predict(self, text: str) -> list[PiiSpan]:
        """All PII spans across sliding windows, merged + whitespace-trimmed."""
        merged: list[PiiSpan] = []
        for enc in self._window_encodings(text):
            for span in self._run_window(enc):
                merged.append(span)
        return _merge_spans(merged, text)


def _merge_spans(spans: list[PiiSpan], text: str) -> list[PiiSpan]:
    """Model-card postprocessing: trim whitespace edges, drop empties, merge
    overlaps (same label first, then any overlap keeps the wider span)."""
    trimmed: list[PiiSpan] = []
    for s in spans:
        while s.start < s.end and s.start < len(text) and text[s.start].isspace():
            s = PiiSpan(s.start + 1, s.end, s.label)
        while s.end > s.start and text[s.end - 1].isspace():
            s = PiiSpan(s.start, s.end - 1, s.label)
        if s.end > s.start:
            trimmed.append(s)
    # merge same-label overlaps AND same-label spans separated only by
    # whitespace — BERT token offsets include the leading space, so the
    # tokens of one entity arrive as gapped spans
    trimmed.sort(key=lambda s: (s.start, -s.end))
    out: list[PiiSpan] = []
    for s in trimmed:
        if out and s.label == out[-1].label:
            gap = text[out[-1].end : s.start]
            if s.start <= out[-1].end or (s.start > out[-1].end and gap.isspace()):
                out[-1] = PiiSpan(out[-1].start, max(out[-1].end, s.end), s.label)
                continue
        out.append(s)
    # drop spans fully contained in a wider kept span (any label); equal spans
    # deduplicate instead of eliminating each other
    final: list[PiiSpan] = []
    for s in sorted(out, key=lambda s: (s.start, -(s.end - s.start))):
        if any(f.start <= s.start and s.end <= f.end for f in final):
            continue
        final.append(s)
    final.sort(key=lambda s: s.start)
    return final


def _apply_spans(text: str, spans: list[PiiSpan]) -> str:
    if not spans:
        return text
    out = text
    for s in sorted(spans, key=lambda s: s.start, reverse=True):
        out = out[: s.start] + f"[REDACTED:{s.tag}]" + out[s.end :]
    return out


class RedactionService:
    """Regex-first, NER-when-available PHI scrub for cloud LLM calls.

    Bound once per app (app.state.redaction); the note-draft and template-
    extraction routes call :meth:`redact` instead of the raw regex helper.
    """

    def __init__(
        self,
        cloud: CloudLlmConfig,
        models: ModelsConfig,
        *,
        metrics: Any = None,
    ) -> None:
        self._cloud = cloud
        self._models = models
        self._metrics = metrics
        self._labels = {str(x).upper() for x in cloud.pii_redact_labels}
        self._engine: _NerEngine | None = None
        self._engine_failed_at: float | None = None
        self._engine_lock = asyncio.Lock()

    # -- model resolution -----------------------------------------------------

    @property
    def model_dir(self) -> Path | None:
        """Explicit config wins; else the model-manager download folder."""
        if self._cloud.pii_model_dir:
            p = Path(self._cloud.pii_model_dir)
        else:
            spec = get_spec(PII_MODEL_ID)
            p = Path(self._models.dir) / spec.id if spec else None
        if p is None:
            return None
        if (p / "model.onnx").is_file() and (p / "tokenizer.json").is_file():
            return p
        return None

    @property
    def ner_available(self) -> bool:
        return self._cloud.pii_ner_enabled and self.model_dir is not None

    def _get_engine(self) -> _NerEngine | None:
        """Lazily build the ONNX session; retry with 60 s cooldown on failure."""
        if self._engine is not None:
            return self._engine
        if self._engine_failed_at is not None and time.monotonic() - self._engine_failed_at < 60:
            return None
        model_dir = self.model_dir
        if model_dir is None or not self._cloud.pii_ner_enabled:
            return None
        try:
            self._engine = _NerEngine(
                model_dir,
                max_length=self._cloud.pii_max_length,
                stride=self._cloud.pii_stride,
            )
            logger.info("PII NER engine ready dir=%s", model_dir)
            return self._engine
        except Exception:  # noqa: BLE001 — degrade to regex, never block drafting
            self._engine_failed_at = time.monotonic()
            logger.warning("PII NER engine init failed — regex-only redaction", exc_info=True)
            return None

    # -- redaction --------------------------------------------------------------

    def redact_sync(self, text: str) -> str:
        """Regex layer always; NER spans unioned on top. Never raises."""
        from api.services.note_prompt import redact_phi

        out = redact_phi(text)
        engine = self._get_engine()
        if engine is None:
            return out
        try:
            spans = [s for s in engine.predict(out) if s.label in self._labels]
            self._incr("pii_ner_redacted")
            return _apply_spans(out, spans)
        except Exception:  # noqa: BLE001 — model failure must not block drafts
            self._engine = None
            self._engine_failed_at = time.monotonic()
            self._incr("pii_ner_fallback")
            logger.warning("PII NER inference failed — regex-only redaction", exc_info=True)
            return out

    async def redact(self, text: str) -> str:
        """Async wrapper (ONNX inference runs off the event loop)."""
        if not text:
            return text
        if not self.ner_available:
            from api.services.note_prompt import redact_phi

            return redact_phi(text)
        async with self._engine_lock:
            return await asyncio.to_thread(self.redact_sync, text)

    def _incr(self, name: str) -> None:
        if self._metrics is not None:
            try:
                self._metrics.incr(name)
            except Exception:  # pragma: no cover
                logger.debug("pii metric update failed", exc_info=True)
