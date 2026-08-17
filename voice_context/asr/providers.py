from __future__ import annotations

from typing import Any


DEFAULT_REMOTE_ASR_PROVIDER = "openai_compatible"
REMOTE_ASR_PROVIDERS = frozenset(
    {"aliyun_realtime", "openai_compatible", "custom_api"}
)
OBSOLETE_LOCAL_ASR_KEYS = ("model_path", "device")


def normalize_remote_asr_config(config: dict[str, Any]) -> dict[str, Any]:
    """Remove local-ASR settings and migrate unsupported providers to a remote API."""
    provider = str(config.get("provider", DEFAULT_REMOTE_ASR_PROVIDER))
    if provider not in REMOTE_ASR_PROVIDERS:
        config.update(
            {
                "provider": DEFAULT_REMOTE_ASR_PROVIDER,
                "model": "whisper-1",
                "base_url": "https://api.openai.com/v1",
            }
        )
    for key in OBSOLETE_LOCAL_ASR_KEYS:
        config.pop(key, None)
    return config
