"""Server-side LLM configuration and JSON-mode probing helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import MIMO_BASE_URL, MIMO_TOKEN_PLAN_URL


RUNTIME_DIR = Path(__file__).resolve().parent.parent / ".runtime"
LLM_CONFIG_FILE = RUNTIME_DIR / "llm_config.json"


def _safe_config(cfg: dict[str, Any]) -> dict[str, Any]:
    provider = (cfg.get("provider") or "").strip()
    url = (cfg.get("url") or "").strip()
    model = (cfg.get("model") or "").strip()
    thinking = (cfg.get("thinking") or "").strip()
    if not provider and "deepseek" in url.lower():
        provider = "deepseek"
    if provider == "deepseek" and not thinking:
        thinking = "enabled"
    return {
        "url": (cfg.get("url") or "").strip(),
        "model": model,
        "thinking": thinking,
        "reasoning_effort": (cfg.get("reasoning_effort") or "").strip(),
        "provider": provider,
    }


def _env_config() -> dict[str, Any]:
    """Read optional server-side LLM config from environment variables."""
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    cfg = {
        "provider": (os.environ.get("LLM_PROVIDER") or "").strip(),
        "url": (os.environ.get("LLM_BASE_URL") or os.environ.get("LLM_URL") or "").strip(),
        "model": (os.environ.get("LLM_MODEL") or "").strip(),
        "thinking": (os.environ.get("LLM_THINKING") or "").strip(),
        "reasoning_effort": (os.environ.get("LLM_REASONING_EFFORT") or "").strip(),
    }
    clean = _safe_config(cfg)
    if key:
        clean["key"] = key
    return {k: v for k, v in clean.items() if v}


def save_llm_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Persist third-party LLM config on the server, never in browser storage."""
    RUNTIME_DIR.mkdir(exist_ok=True)
    clean = _safe_config(cfg)
    key = (cfg.get("key") or "").strip()
    if key:
        clean["key"] = key
    elif LLM_CONFIG_FILE.exists():
        old = read_llm_config(include_key=True)
        if old.get("key") and any(clean.values()):
            clean["key"] = old["key"]

    if not any(clean.values()):
        clear_llm_config()
        return {}

    LLM_CONFIG_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return redact_llm_config(clean)


def read_llm_config(include_key: bool = False) -> dict[str, Any]:
    cfg = _env_config()
    if LLM_CONFIG_FILE.exists():
        cfg.update(json.loads(LLM_CONFIG_FILE.read_text(encoding="utf-8")))
    return cfg if include_key else redact_llm_config(cfg)


def clear_llm_config() -> None:
    if LLM_CONFIG_FILE.exists():
        LLM_CONFIG_FILE.unlink()


def redact_llm_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = _safe_config(cfg)
    key = cfg.get("key") or ""
    out["has_key"] = bool(key)
    out["key_masked"] = mask_secret(key) if key else ""
    return out


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "***"
    return f"{secret[:3]}***{secret[-4:]}"


def effective_llm_config(incoming: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge browser-safe preferences with server-side secret config."""
    server_cfg = read_llm_config(include_key=True)
    merged = dict(server_cfg)
    for key, value in (incoming or {}).items():
        if key == "key":
            continue
        if value not in (None, ""):
            merged[key] = value
    return merged


def make_json_probe_kwargs(
    model: str,
    thinking: str,
    provider: str = "",
    sample_text: str = "萧炎低声道：“三十年河东，三十年河西。”",
) -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                "You return JSON only. The word json is intentionally included. "
                "Return valid JSON matching the requested schema."
            ),
        },
        {
            "role": "user",
            "content": (
                "请把下面文本解析为 json，格式为 "
                '{"speaker":"说话人","quote":"台词","has_dialogue":true}。\n'
                f"文本：{sample_text}"
            ),
        },
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": 256,
        "response_format": {"type": "json_object"},
    }
    normalized = (thinking or "").lower()
    if normalized == "enabled":
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = "high"
    elif normalized == "disabled":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        kwargs["temperature"] = 0
    else:
        kwargs["temperature"] = 0

    if provider.lower() == "deepseek" and normalized == "enabled":
        kwargs.pop("temperature", None)
    return kwargs


def probe_json_mode(
    api_key: str,
    base_url: str,
    model: str,
    thinking: str,
    provider: str = "",
) -> dict[str, Any]:
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        **make_json_probe_kwargs(model=model, thinking=thinking, provider=provider)
    )
    msg = response.choices[0].message
    raw_content = (msg.content or "").strip()
    reasoning = getattr(msg, "reasoning_content", None)
    raw = raw_content
    if not raw and reasoning:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", reasoning)
        raw = match.group(0) if match else reasoning.strip()

    parsed = None
    parse_error = ""
    try:
        parsed = json.loads(raw) if raw else None
    except Exception as exc:  # pragma: no cover - diagnostic path
        parse_error = str(exc)

    usage = getattr(response, "usage", None)
    return {
        "ok": bool(parsed),
        "content_empty": not bool(raw_content),
        "has_reasoning_content": bool(reasoning),
        "json_valid": bool(parsed),
        "parse_error": parse_error,
        "parsed": parsed,
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
        },
    }


def default_base_url_for_mode(mode: str) -> str:
    return MIMO_TOKEN_PLAN_URL if mode == "tokenplan" else MIMO_BASE_URL
