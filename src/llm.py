"""DeepSeek API 客户端（标准库 urllib 实现，零第三方依赖）。

密钥从环境变量 / 项目根目录 .env 读取，只在服务端使用，绝不下发前端。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://api.deepseek.com/chat/completions"


def load_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def api_key() -> str | None:
    load_env()
    return os.environ.get("DEEPSEEK_API_KEY")


def model_name() -> str:
    load_env()
    return os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def available() -> bool:
    return bool(api_key())


def chat(messages: list[dict], json_mode: bool = False, temperature: float = 0.1,
         max_tokens: int = 1200, timeout: int = 45) -> str:
    key = api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    payload: dict = {"model": model_name(), "messages": messages,
                     "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def chat_json(messages: list[dict], **kw) -> dict:
    return json.loads(chat(messages, json_mode=True, **kw))
