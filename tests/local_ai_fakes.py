"""Fakes for the optional local-AI runtimes (sherpa-onnx / onnxruntime /
tokenizers / numpy) so adapter tests run everywhere — no wheels, no network,
no model files. Mirrors the ScriptedTransport approach of Phase 3: the fake
implements exactly the API surface the adapters touch.
"""
from __future__ import annotations

import sys
import types

# --------------------------------------------------------------------- numpy


class FakeArray:
    def __init__(self, data: bytes, itemsize: int = 2) -> None:
        self.data = data
        self.itemsize = itemsize

    def astype(self, _dtype) -> FakeArray:
        return self

    def __truediv__(self, _v) -> FakeArray:
        return self

    def __len__(self) -> int:
        return len(self.data) // self.itemsize

    def __iter__(self):
        return iter(self.data)


class FakeNumpyModule(types.ModuleType):
    float32 = "float32"
    int64 = "int64"

    @staticmethod
    def frombuffer(data, dtype=None):
        itemsize = 2 if dtype in ("<i2", "int16") else 4
        return FakeArray(data, itemsize)

    @staticmethod
    def zeros_like(x):
        return x

    @staticmethod
    def asarray(x, dtype=None):
        return x


def install_fake_numpy(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "numpy", FakeNumpyModule("numpy"))


# ------------------------------------------------------------- sherpa-onnx


class FakeSherpaStream:
    def __init__(self) -> None:
        self.waveform_chunks: list[int] = []  # sample counts per accept
        self.finished = False

    def accept_waveform(self, sample_rate, samples) -> None:
        self.waveform_chunks.append(len(samples))

    def input_finished(self) -> None:
        self.finished = True


class FakeSherpaRecognizer:
    """Scripted OnlineRecognizer: ``result_script`` is popped per get_result
    call; resets/decodes are recorded for assertions."""

    def __init__(self) -> None:
        self.result_script: list[str] = []
        self.resets: int = 0
        self.decode_calls: int = 0
        self.created_streams: list[FakeSherpaStream] = []
        self.ctor_kwargs: dict = {}

    @classmethod
    def from_nemo_ctc(cls, **kwargs) -> FakeSherpaRecognizer:
        inst = cls()
        inst.ctor_kwargs = kwargs
        return inst

    def create_stream(self, hotwords=None) -> FakeSherpaStream:
        stream = FakeSherpaStream()
        self.created_streams.append(stream)
        return stream

    def is_ready(self, stream) -> bool:
        return False  # decode loop drains immediately

    def decode_stream(self, stream) -> None:
        self.decode_calls += 1

    def get_result(self, stream) -> str:
        if self.result_script:
            return self.result_script.pop(0)
        return ""

    def reset(self, stream) -> None:
        self.resets += 1


def install_fake_sherpa(monkeypatch, recognizer: FakeSherpaRecognizer) -> None:
    module = types.ModuleType("sherpa_onnx")

    def _from_nemo_ctc(**kwargs):
        recognizer.ctor_kwargs = kwargs  # recorded for assertions
        return recognizer

    module.OnlineRecognizer = types.SimpleNamespace(from_nemo_ctc=_from_nemo_ctc)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)


def install_missing_local_ai(monkeypatch) -> None:
    """Simulate neither optional package being installed."""
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "tokenizers", None)


# ------------------------------------------------------------- onnxruntime


class FakeOrtLogits:
    """[seq, labels] logits stand-in with numpy-free argmax(axis=-1)."""

    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def __getitem__(self, idx):
        return self.rows[idx]

    def argmax(self, axis: int = -1):
        if axis == -1:
            return [max(range(len(r)), key=r.__getitem__) for r in self.rows]
        raise NotImplementedError(axis)


class FakeOrtSession:
    def __init__(self, model_path: str, providers=None) -> None:
        self.model_path = model_path
        self.providers = providers
        self.inputs = [
            types.SimpleNamespace(name=n)
            for n in ("input_ids", "attention_mask", "token_type_ids")
        ]
        self.runs: list[dict] = []

    def get_inputs(self):
        return self.inputs

    def run(self, _output_names, feeds):
        self.runs.append(feeds)
        raise NotImplementedError("script per-test via subclass or `logits`")


class FakeOrtModule(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("onnxruntime")
        self.sessions: list[FakeOrtSession] = []
        self.session_cls = FakeOrtSession

    def InferenceSession(self, model_path, providers=None):
        session = self.session_cls(model_path, providers)
        self.sessions.append(session)
        return session


def install_fake_ort(monkeypatch, session_cls=None) -> FakeOrtModule:
    module = FakeOrtModule()
    if session_cls is not None:
        module.session_cls = session_cls
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    return module


# --------------------------------------------------------------- tokenizers


class FakeEncoding:
    def __init__(
        self,
        ids: list[int],
        offsets: list[tuple[int, int]],
        attention_mask: list[int] | None = None,
        overflowing: list[FakeEncoding] | None = None,
    ) -> None:
        self.ids = ids
        self.offsets = offsets
        self.attention_mask = attention_mask or [1] * len(ids)
        self.overflowing = overflowing or []


class FakeTokenizer:
    """Maps text → encodings from a per-test script keyed by exact text."""

    def __init__(self, encodings_by_text: dict[str, list[FakeEncoding]] | None = None) -> None:
        self.encodings_by_text = encodings_by_text or {}
        self.truncation: dict | None = None
        self.padding: dict | None = None
        self.last_text: str | None = None

    def enable_truncation(self, **kwargs) -> None:
        self.truncation = kwargs

    def enable_padding(self, **kwargs) -> None:
        self.padding = kwargs

    def encode(self, text: str) -> FakeEncoding:
        self.last_text = text
        encs = self.encodings_by_text.get(text)
        if not encs:
            return FakeEncoding([1, 2], [(0, 0), (0, 0)])
        main = encs[0]
        main.overflowing = encs[1:]
        return main


class FakeTokenizersModule(types.ModuleType):
    def __init__(self, tokenizer: FakeTokenizer) -> None:
        super().__init__("tokenizers")
        self._tokenizer = tokenizer
        self.Tokenizer = types.SimpleNamespace(from_file=lambda _p: tokenizer)


def install_fake_tokenizers(monkeypatch, tokenizer: FakeTokenizer) -> None:
    monkeypatch.setitem(
        sys.modules, "tokenizers", FakeTokenizersModule(tokenizer)
    )
