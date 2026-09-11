"""Speechmatics adapter — realtime WebSocket streaming + batch job API.

Live:  ``wss://{region}.rt.speechmatics.com/v2/stream/ws`` — StartRecognition
handshake, binary PCM frames, ``RecognitionResult`` messages translated into
:class:`TranscriptSegment` events (partials → interims, finals → segment
commits).

Batch: job lifecycle ``POST /api/v2/jobs`` → ``PATCH …/send-audio`` →
``GET …/jobs/{id}`` poll → ``GET …/result``. Persian configured with
punctuation enabled; ``operating_params`` passthrough lets ops tune without a
code change.

    MS_STT__SPEECHMATICS__API_KEY  (required to route here)
    MS_STT__SPEECHMATICS__REGION   eu2 | us
    MS_STT__SPEECHMATICS__LANGUAGE fa (default)
    MS_STT__SPEECHMATICS__OPERATING_DOMAIN general

The WS client sits behind the same transport seam as the Deepgram adapter so
protocol tests are network-free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ai.base import (
    PrivacyClass,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    ProviderUnavailableError,
    STTProvider,
    STTRequest,
    TranscriptSegment,
)
from ai.stt._common import pcm_duration_ms
from ai.stt.ws_transport import ConnectionClosed, WsConnector, default_connector

logger = logging.getLogger(__name__)

_REGION_HOSTS = {
    "eu2": ("https://eu2.asr.speechmatics.com/v2", "wss://eu2.rt.speechmatics.com/v2/stream/ws"),
    "us": ("https://us.asr.speechmatics.com/v2", "wss://us.rt.speechmatics.com/v2/stream/ws"),
}


def speechmatics_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


class SpeechmaticsProvider(STTProvider):
    name = "speechmatics"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport_factory: WsConnector | None = None,
    ) -> None:
        cfg = dict(config or {})
        self._api_key = str(cfg.get("api_key") or "")
        if not self._api_key:
            raise ProviderError("speechmatics requires 'api_key'", provider=self.name)
        region = str(cfg.get("region") or "eu2").lower()
        batch_default, rt_default = _REGION_HOSTS.get(region, _REGION_HOSTS["eu2"])
        self._batch_url = str(cfg.get("batch_url") or batch_default).rstrip("/")
        self._rt_url = str(cfg.get("rt_url") or rt_default).rstrip("/")
        self._region = region
        self._language = str(cfg.get("language") or "en")
        self._domain = str(cfg.get("operating_domain") or "general")
        self._timeout_s = float(cfg.get("timeout_s", 60.0))
        self._poll_interval = float(cfg.get("job_poll_interval_s", 1.0))
        self._job_timeout = float(cfg.get("job_timeout_s", 300.0))
        self._max_retries = int(cfg.get("max_retries", 2))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._connector = transport_factory or default_connector
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            supports_batch=True,
            languages=("fa", "en", "*"),
            cost_hint_per_unit=float(cfg.get("cost_hint_per_minute", 0.006)),
            latency_hint_ms=900,
            extra={"region": self._region, "domain": self._domain},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _map_language(language: str | None, fallback: str) -> str:
        if not language:
            return fallback
        return {"fa-en": "fa", "fa-en-mixed": "fa"}.get(language, language)

    # -- batch job lifecycle ---------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        config = {
            "type": "transcription",
            "audio_format": "pcm_s16le" if request.encoding != "wav" else "wav",
            "sampling_rate": request.sample_rate,
            "max_delay": 120,
            "operating_params": {
                "language": self._map_language(request.language, self._language),
                "operating_domain": self._domain,
                "enable_punctuation": True,
                "enable_formatting": True,
            },
        }
        if request.context_hints:
            config["operating_params"]["custom_vocabulary"] = [str(h) for h in request.context_hints]
        headers = self._auth()
        try:
            start = await self._client.post(
                f"{self._batch_url}/jobs", headers=headers, json=config
            )
            self._raise_for(start, "job start")
            request_id = start.json()["request_id"]
            send = await self._client.patch(
                f"{self._batch_url}/jobs/{request_id}/send-audio",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=request.audio,
            )
            if send.status_code not in (200, 202):
                raise ProviderError(
                    f"speechmatics audio upload failed ({send.status_code})",
                    provider=self.name,
                    status_hint=send.status_code,
                )
            body = await self._await_result(request_id, headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"speechmatics unreachable: {exc}", provider=self.name) from exc
        return self._parse_batch_result(body, request)

    async def _await_result(self, request_id: str, headers: dict[str, str]) -> Mapping[str, Any]:
        deadline = time.perf_counter() + self._job_timeout
        delay = self._poll_interval
        while True:
            status = await self._client.get(f"{self._batch_url}/jobs/{request_id}", headers=headers)
            self._raise_for(status, "job status")
            state = status.json().get("job_status")
            if state == "done":
                break
            if state == "error":
                raise self._job_error(status.json())
            if time.perf_counter() > deadline:
                raise ProviderUnavailableError(
                    f"speechmatics job {request_id} timed out", provider=self.name
                )
            await asyncio.sleep(min(delay, max(0.05, deadline - time.perf_counter())))
            delay = min(delay * 2, 5.0)  # bounded poll backoff
        result = await self._client.get(
            f"{self._batch_url}/jobs/{request_id}/result",
            headers=headers,
            params={"scheme": "2", "srt": "false"},
        )
        self._raise_for(result, "job result")
        return result.json()

    @staticmethod
    def _job_error(body: Mapping[str, Any]) -> ProviderError:
        detail = str((body.get("status") or {}).get("reason") or "job failed")
        return ProviderError(f"speechmatics: {detail}", provider="speechmatics")

    @staticmethod
    def _raise_for(resp: httpx.Response, what: str) -> None:
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"speechmatics auth failed on {what}", provider="speechmatics", status_hint=resp.status_code
            )
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"speechmatics {what} error {resp.status_code}", provider="speechmatics"
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"speechmatics rejected {what} ({resp.status_code})",
                provider="speechmatics",
                status_hint=resp.status_code,
            )

    def _parse_batch_result(
        self, body: Mapping[str, Any], request: STTRequest
    ) -> list[TranscriptSegment]:
        results = body.get("results") or {}
        text = str(results.get("transcript", "")).strip()
        out: list[TranscriptSegment] = []
        elements = results.get("elements") or []
        if elements:
            # sentence-split on end-of-utterance marks; timestamps are seconds
            current: list[dict] = []
            for el in elements:
                current.append(el)
                if el.get("end_time") is not None and (el.get("is_end_of_transcript") or el.get("punctuation")):
                    out.append(self._segment_from_elements(current, request))
                    current = []
            if current:
                out.append(self._segment_from_elements(current, request))
        elif text:
            dur = pcm_duration_ms(request.audio, request.sample_rate, request.channels)
            out.append(
                TranscriptSegment(
                    text=text,
                    start_ms=0,
                    end_ms=max(dur, 1),
                    confidence=results.get("metadata", {}).get("confidence"),
                    language=request.language,
                )
            )
        return out

    @staticmethod
    def _segment_from_elements(elements: list[dict], request: STTRequest) -> TranscriptSegment:
        words = []
        confidences = []
        for el in elements:
            value = str(el.get("value", el.get("text", ""))).strip()
            if el.get("type") == "Punctuation" and value in {".", "!", "?", ","}:
                if value in {".", "!", "?"}:
                    words.append(value)
            elif value:
                words.append(value)
            if isinstance(el.get("confidence"), (int, float)):
                confidences.append(float(el["confidence"]))
        start_ms = int(float(elements[0].get("start_time", 0.0)) * 1000)
        end_ms = int(float(elements[-1].get("end_time", elements[0].get("start_time", 0.0))) * 1000)
        text = " ".join(words)
        for a, b in ((" ,", ","), (" .", "."), (" ?", "?"), (" !", "!")):
            text = text.replace(a, b)
        return TranscriptSegment(
            text=text.strip(),
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms + 1),
            confidence=sum(confidences) / len(confidences) if confidences else None,
            language=request.language,
        )

    # -- realtime streaming ---------------------------------------------------------

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        transport = await self._connector(
            self._rt_url, {**self._auth(), "Accept": "application/json"}
        )
        # wire contract: raw PCM16 mono @16k over the realtime socket
        start_msg = {
            "message_type": "StartRecognition",
            "audio_format": "pcm_s16le",
            "sampling_rate": 16000,
            "max_delay": 3.5,
            "enable_partials": True,
            "operating_params": {
                "language": self._language,
                "operating_domain": self._domain,
                "enable_punctuation": True,
            },
        }
        try:
            await transport.send_text(json.dumps(start_msg))
            ack = await self._await_message(transport, "RecognitionStart", timeout=10.0)
            if ack is None:
                raise ProviderUnavailableError(
                    "speechmatics did not acknowledge StartRecognition", provider=self.name
                )
            pump = asyncio.create_task(self._pump(transport, chunks))
            async for seg in self._read_results(transport):
                yield seg
            await pump
        except ProviderError:
            raise
        except ConnectionClosed as exc:
            raise ProviderUnavailableError(f"speechmatics stream closed: {exc}", provider=self.name) from exc
        finally:
            await transport.close()

    async def _pump(self, transport: Any, chunks: AsyncIterator[bytes]) -> None:
        async for chunk in chunks:
            await transport.send_bytes(chunk)
        await transport.send_text(json.dumps({"message_type": "EndOfStream"}))

    async def _await_message(
        self, transport: Any, message_type: str, *, timeout: float
    ) -> Mapping[str, Any] | None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                raw = await asyncio.wait_for(
                    transport.recv(), timeout=max(0.1, deadline - time.perf_counter())
                )
            except (ConnectionClosed, TimeoutError):
                return None
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("message_type") == message_type:
                return msg
            if msg.get("message_type") == "RecognitionFailed":
                raise ProviderUnavailableError(
                    f"speechmatics: {msg.get('reason', 'recognition failed')}", provider=self.name
                )
        return None

    async def _read_results(self, transport: Any) -> AsyncIterator[TranscriptSegment]:
        while True:
            try:
                raw = await asyncio.wait_for(transport.recv(), timeout=30.0)
            except ConnectionClosed:
                return
            except TimeoutError as exc:
                raise ProviderUnavailableError(
                    "speechmatics stream timeout", provider=self.name
                ) from exc
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            mtype = msg.get("message_type")
            if mtype in ("SetCustomVocabAck", "SetCustomVocabularyDataAck", "SetLanguagePackAck"):
                continue
            if mtype == "RecognitionFailed":
                raise ProviderUnavailableError(
                    f"speechmatics: {msg.get('reason', 'recognition failed')}", provider=self.name
                )
            if mtype in ("EndOfStream", "ResultEnd"):
                return
            if mtype != "RecognitionResult":
                continue
            async for seg in self._emit(msg):
                yield seg

    async def _emit(self, msg: Mapping[str, Any]) -> AsyncIterator[TranscriptSegment]:
        partial = bool(msg.get("is_partial"))
        results = msg.get("results") or []
        if not results:
            text = str(msg.get("transcript", "")).strip()
            if text:
                yield TranscriptSegment(
                    text=text, start_ms=0, end_ms=1, is_final=False
                )
            return
        for res in results:
            is_final = bool(res.get("is_final")) and not partial
            start_ms = int(float(res.get("start_time", 0.0)) * 1000)
            end_ms = int(float(res.get("end_time", res.get("start_time", 0.0))) * 1000)
            conf = res.get("confidence")
            text = self._text_from_response(res)
            if not text:
                continue
            yield TranscriptSegment(
                text=text,
                start_ms=start_ms,
                end_ms=max(end_ms, start_ms + 1),
                is_final=is_final,
                confidence=float(conf) if isinstance(conf, (int, float)) else None,
            )

    @staticmethod
    def _text_from_response(res: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for item in res.get("message_response") or []:
            itype = item.get("type")
            if itype in ("AddTranscript", "UpdateTranscript", "AppendPunctuation"):
                alts = item.get("alternatives") or [{}]
                value = str(alts[0].get("value", "")).strip()
                if value:
                    parts.append(value)
            elif itype == "ReplaceTranscript":
                alts = item.get("alternatives") or [{}]
                parts = [str(alts[0].get("value", "")).strip()]
        text = " ".join(p for p in parts if p)
        for a, b in ((" ,", ","), (" .", "."), (" ?", "?"), (" !", "!")):
            text = text.replace(a, b)
        return text.strip()

    # -- health -------------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(f"{self._batch_url}/account", headers=self._auth(), timeout=5.0)
            if resp.status_code == 200:
                return ProviderHealth(ok=True, latency_ms=(time.perf_counter() - started) * 1000)
            return ProviderHealth(
                ok=False, detail=f"HTTP {resp.status_code} (check MS_STT__SPEECHMATICS__API_KEY)"
            )
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
