from __future__ import annotations

from typing import Any

from .api_engine import APIASREngine
from .aliyun_realtime import AliyunRealtimeASR
from .base import ASREngine, ASRError
from .providers import DEFAULT_REMOTE_ASR_PROVIDER


def create_asr_engine(config: dict[str, Any], api_key: str = "") -> ASREngine:
    provider = str(config.get("provider", DEFAULT_REMOTE_ASR_PROVIDER))
    if provider == "openai_compatible":
        return APIASREngine(config, api_key=api_key)
    if provider == "custom_api":
        return APIASREngine(config, api_key=api_key, custom=True)
    if provider == "aliyun_realtime":
        return AliyunRealtimeASR(config, api_key)
    raise ASRError(f"Unsupported ASR provider: {provider}")
