"""MiMo TTS API 客户端（OpenAI 兼容协议）。"""

import base64
import time
import os
from typing import Optional

from openai import OpenAI

from .config import (
    MIMO_BASE_URL,
    MIMO_API_KEY_ENV,
    MIMO_TOKEN_PLAN_URL,
    MIMO_TOKEN_PLAN_KEY_ENV,
    MODEL_TTS,
    AUDIO_FORMAT,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    REQUEST_TIMEOUT,
)


class MiMoTTSClient:
    """MiMo 语音合成客户端。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = MODEL_TTS,
        voice: str = "冰糖",
        style: str = "",
        use_token_plan: bool = False,
    ):
        key = api_key or os.environ.get(
            MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV, ""
        )
        if not key:
            env_name = MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV
            raise ValueError(f"请设置 {env_name} 环境变量或传入 api_key 参数")

        base_url = MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL
        self._client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self.voice = voice
        self.style = style

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        style: Optional[str] = None,
    ) -> tuple[bytes, dict]:
        """合成语音，返回 (WAV 音频字节, 用量信息)。

        voice/style 为 None 时使用客户端默认值，传入则覆盖。
        usage 结构: {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        """
        voice_id = voice or self.voice
        style_text = style if style is not None else self.style
        print(f"[TTS] voice={voice_id} style={style_text[:30] if style_text else '-'} text_len={len(text)}")
        messages = self._build_messages(text, style_text)

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                completion = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    audio={"format": AUDIO_FORMAT, "voice": voice_id},
                    timeout=REQUEST_TIMEOUT,
                )
                audio_b64 = completion.choices[0].message.audio.data
                audio_bytes = base64.b64decode(audio_b64)

                usage = {
                    "prompt_tokens": completion.usage.prompt_tokens,
                    "completion_tokens": completion.usage.completion_tokens,
                    "total_tokens": completion.usage.total_tokens,
                }
                return audio_bytes, usage

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"TTS 合成失败（重试 {MAX_RETRIES} 次后）: {last_error}"
                    ) from last_error

        # 不应到达这里
        raise RuntimeError(f"TTS 合成失败: {last_error}")

    def _build_messages(self, text: str, style_text: str = "") -> list[dict]:
        """构建 API messages。"""
        messages: list[dict] = []
        style = style_text or self.style
        if style:
            messages.append({"role": "user", "content": style})
        messages.append({"role": "assistant", "content": text})
        return messages
