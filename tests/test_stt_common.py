"""VAD segmenter + wav container unit tests (deterministic, audio-time driven)."""
from __future__ import annotations

import io
import wave

from providers_support import pcm_silence, pcm_speech

from ai.stt._common import VadSegmenter, pcm_duration_ms, pcm_rms, pcm_to_wav, wav_to_pcm

CHUNK = 3200  # 100 ms @ 16 kHz mono int16


def _chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def test_rms_and_duration_math():
    assert pcm_rms(b"") == 0.0
    loud = pcm_speech(100)
    assert pcm_rms(loud) > 0.1
    assert pcm_rms(pcm_silence(100)) == 0.0
    assert pcm_duration_ms(loud) == 100


def test_wav_roundtrip_and_rejections():
    pcm = pcm_speech(250)
    wav = pcm_to_wav(pcm, 16000, 1)
    out_pcm, rate, channels = wav_to_pcm(wav)
    assert out_pcm == pcm and rate == 16000 and channels == 1

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:  # 8-bit → rejected by the parser
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(16000)
        w.writeframes(pcm[:1000])
    assert "16-bit" in str(_expect_value_error(buf.getvalue()))
    assert "not a valid WAV" in str(_expect_value_error(b"AAAA"))


def _expect_value_error(data: bytes) -> Exception:
    try:
        wav_to_pcm(data)
    except ValueError as exc:
        return exc
    raise AssertionError("expected ValueError")


def test_vad_closes_utterance_on_silence():
    vad = VadSegmenter(threshold=0.02, silence_ms=500, interim_ms=10000, pre_roll_ms=0)
    events = []
    for c in _chunks(pcm_speech(1000)):
        events += vad.feed(c)
    for c in _chunks(pcm_silence(800)):
        events += vad.feed(c)
    events += vad.flush()
    finals = [e for e in events if e.kind == "final"]
    assert len(finals) == 1
    seg = finals[0]
    assert seg.start_ms == 0
    assert 1000 <= seg.end_ms <= 1800  # cut at last speech, trailing silence excluded
    assert len(seg.audio) >= 1600 * 2  # ~1 s of speech retained


def test_vad_interim_then_final_for_long_utterance():
    vad = VadSegmenter(threshold=0.02, silence_ms=600, interim_ms=1500, pre_roll_ms=0)
    events = []
    for c in _chunks(pcm_speech(5000)):
        events += vad.feed(c)
    for c in _chunks(pcm_silence(700)):
        events += vad.feed(c)
    kinds = [e.kind for e in events]
    assert "interim" in kinds
    assert kinds[-1] == "final"
    assert kinds.index("interim") < len(kinds) - 1
    # monotonic timeline
    times = [(e.start_ms, e.end_ms) for e in events]
    assert times == sorted(times)
    assert all(end > start for start, end in times)


def test_vad_max_segment_force_cut():
    vad = VadSegmenter(silence_ms=600, interim_ms=100000, max_segment_ms=3000, pre_roll_ms=0)
    events = []
    for c in _chunks(pcm_speech(10000)):
        events += vad.feed(c)
        if any(e.kind == "final" for e in events):
            break
    assert events and events[0].kind == "final"
    assert events[0].end_ms <= 3100


def test_vad_preroll_backfills_speech_onset():
    vad = VadSegmenter(silence_ms=400, interim_ms=100000, pre_roll_ms=300)
    events = []
    for c in _chunks(pcm_silence(500)):
        events += vad.feed(c)
    for c in _chunks(pcm_speech(600)):
        events += vad.feed(c)
    for c in _chunks(pcm_silence(500)):
        events += vad.feed(c)
    finals = [e for e in events if e.kind == "final"]
    assert len(finals) == 1
    assert finals[0].start_ms == 200  # 500 ms of silence seen, but 300 preroll kept
    assert finals[0].end_ms >= 800
