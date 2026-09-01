from __future__ import annotations

import json
import ssl
import httpx
import pytest

from voice_context.asr.api_engine import APIASREngine
from voice_context.asr.base import ASRError
from voice_context.asr.factory import create_asr_engine
from voice_context.core import tls
from voice_context.core.events import ReferenceEvent
from voice_context.llm.client import LLMComposerClient


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("failed", request=request, response=response)


class FakeStreamResponse:
    def __init__(self, events, status_code: int = 200) -> None:
        self.events = events
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.events).encode()

    def iter_lines(self):
        for event in self.events:
            if event == "[DONE]":
                yield "data: [DONE]"
            else:
                yield f"data: {json.dumps(event)}"


@pytest.mark.parametrize("provider", ["funasr", "faster_whisper"])
def test_local_asr_providers_are_not_supported(provider: str) -> None:
    with pytest.raises(ASRError, match="Unsupported ASR provider"):
        create_asr_engine({"provider": provider})


def test_asr_openai_request_and_nested_response(monkeypatch, tmp_path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF" + b"0" * 100)
    observed = {}

    def post(url, **kwargs):
        observed["url"] = url
        observed["headers"] = kwargs["headers"]
        return FakeResponse({"data": {"transcript": "recognized"}})

    monkeypatch.setattr("voice_context.asr.api_engine.httpx.post", post)
    engine = APIASREngine({"base_url": "https://host/v1", "model": "whisper", "timeout_s": 5}, "secret")
    assert engine.transcribe(path).text == "recognized"
    assert observed["url"] == "https://host/v1/audio/transcriptions"
    assert observed["headers"]["Authorization"] == "Bearer secret"


def test_asr_reports_http_detail(monkeypatch, tmp_path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF" + b"0" * 100)
    monkeypatch.setattr(
        "voice_context.asr.api_engine.httpx.post", lambda *args, **kwargs: FakeResponse({"error": "bad"}, 401)
    )
    with pytest.raises(ASRError, match="HTTP 401"):
        APIASREngine({"base_url": "https://host/v1"}).transcribe(path)


def test_llm_openai_response_and_immutable_reference_context(monkeypatch) -> None:
    observed = {}

    def stream(method, url, **kwargs):
        observed["method"] = method
        observed["url"] = url
        observed["json"] = kwargs["json"]
        return FakeStreamResponse(
            [
                {
                    "id": "request-123",
                    "model": "coder-v2",
                    "choices": [{"delta": {"role": "assistant", "content": ""}}],
                },
                {
                    "id": "request-123",
                    "model": "coder-v2",
                    "choices": [{"delta": {"content": "Do [ref_1]"}}],
                },
                {
                    "id": "request-123",
                    "model": "coder-v2",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                    "choices": [],
                },
                "[DONE]",
            ]
        )

    monkeypatch.setattr("voice_context.llm.client.httpx.stream", stream)
    client = LLMComposerClient(
        {"provider": "openai_compatible", "base_url": "https://host/v1", "model": "coder"}, "key"
    )
    ref = ReferenceEvent("ref_1", 0, "private full value", preview="safe preview")
    assert client.rewrite("speech", "timeline", [ref]) == "Do [ref_1]"
    assert observed["method"] == "POST"
    assert observed["url"] == "https://host/v1/chat/completions"
    assert observed["json"]["stream"] is True
    assert observed["json"]["stream_options"] == {"include_usage": True}
    user_message = observed["json"]["messages"][1]["content"]
    assert "private full value" in user_message
    assert "never rewrite" in user_message
    assert client.last_response_text == "Do [ref_1]"
    assert client.last_metadata == {
        "request_id": "request-123",
        "response_model": "coder-v2",
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "streamed": True,
        "stream_event_count": 3,
    }


def test_openai_root_urls_gain_v1_prefix(monkeypatch, tmp_path) -> None:
    observed = []

    def post(url, **kwargs):
        observed.append(url)
        return FakeResponse({"text": "ok"})

    def stream(method, url, **kwargs):
        observed.append(url)
        return FakeStreamResponse(
            [
                {"id": "request-1", "model": "model", "choices": [{"delta": {"content": "ok"}}]},
                "[DONE]",
            ]
        )

    monkeypatch.setattr("voice_context.asr.api_engine.httpx.post", post)
    monkeypatch.setattr("voice_context.llm.client.httpx.stream", stream)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    APIASREngine({"base_url": "https://host"}).transcribe(audio)
    LLMComposerClient({"provider": "openai_compatible", "base_url": "https://host"}).rewrite("a", "b", [])
    assert observed == [
        "https://host/v1/audio/transcriptions",
        "https://host/v1/chat/completions",
    ]


def test_httpx_calls_reuse_the_shared_tls_context(monkeypatch, tmp_path) -> None:
    observed = []

    def post(url, **kwargs):
        observed.append(("post", kwargs.get("verify")))
        return FakeResponse({"text": "ok"})

    def stream(method, url, **kwargs):
        observed.append(("stream", kwargs.get("verify")))
        return FakeStreamResponse([{"choices": [{"delta": {"content": "ok"}}]}, "[DONE]"])

    monkeypatch.setattr("voice_context.asr.api_engine.httpx.post", post)
    monkeypatch.setattr("voice_context.llm.client.httpx.stream", stream)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    APIASREngine({"base_url": "https://host/v1"}).transcribe(audio)
    LLMComposerClient({"provider": "openai_compatible", "base_url": "https://host/v1"}).rewrite("a", "b", [])
    assert observed == [
        ("post", tls.SSL_CONTEXT),
        ("stream", tls.SSL_CONTEXT),
    ]


def test_tls_context_falls_back_to_system_trust_when_bundle_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "certifi":
            raise FileNotFoundError("cacert.pem was deleted from the temp dir")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    context = tls._build_ssl_context()
    assert isinstance(context, ssl.SSLContext)
