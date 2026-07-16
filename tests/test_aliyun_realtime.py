from __future__ import annotations

import pytest

from voice_context.asr.aliyun_realtime import AliyunRealtimeASR, normalize_websocket_url
from voice_context.asr.base import ASRError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "llm-workspace.cn-beijing.maas.aliyuncs.com",
            "wss://llm-workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        ),
        (
            "https://llm-workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            "wss://llm-workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        ),
        (
            "https://llm-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "wss://llm-workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        ),
        (
            "wss://llm-workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
            "wss://llm-workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        ),
    ],
)
def test_workspace_urls_map_to_realtime_websocket(value: str, expected: str) -> None:
    assert normalize_websocket_url(value) == expected


def test_realtime_events_replace_partial_and_accumulate_finals(qtbot) -> None:
    class Result:
        def __init__(self, sentence):
            self.sentence = sentence

        def get_sentence(self):
            return self.sentence

    engine = AliyunRealtimeASR({"base_url": "https://workspace.example/api/v1"}, "key")
    observed = []
    engine.transcript_updated.connect(observed.append)
    engine._handle_event(Result({"text": "帮我", "begin_time": 0}))
    engine._handle_event(Result({"text": "帮我修复", "begin_time": 0}))
    engine._handle_event(Result({"text": "帮我修复。", "begin_time": 0, "end_time": 800}))
    engine._handle_event(Result({"text": "谢谢", "begin_time": 900}))
    assert observed == ["帮我", "帮我修复", "帮我修复。", "帮我修复。 谢谢"]
    result = engine.stop()
    assert result.text == "帮我修复。 谢谢"
    assert result.segments[0] == {"text": "帮我修复。", "start_ms": 0, "end_ms": 800}


def test_realtime_requires_api_key() -> None:
    with pytest.raises(ASRError, match="API key is empty"):
        AliyunRealtimeASR({}, "").start()
