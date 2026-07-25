"""即时对话服务：直连 OpenAI 兼容 API，同步取得回复。

与"创建会话"链路不同，本服务不经过 OpenCode 实例：daemon 直接调用
网页端配置的 OpenAI 兼容接口（base_url + api_key + model），单次提问、
阻塞等待并返回回复文本，适合桌宠侧的即时对话场景。

配置持久化到本地 JSON 文件（默认 ``~/.config/openwaifud/chat.json``，
可用环境变量 ``OPENWAIFUD_CHAT_CONFIG`` 覆盖路径）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import aiohttp
from loguru import logger

from openwaifud.models import ChatConfig

# 上游推理可能较慢，给足总超时
_REQUEST_TIMEOUT_SECONDS = 120.0


class ChatNotConfiguredError(Exception):
    """尚未配置 base_url / api_key / model，无法发起对话。"""


class ChatUpstreamError(Exception):
    """上游 API 调用失败（网络错误、非 2xx 或响应格式异常）。"""


def _default_config_path() -> Path:
    override = os.getenv("OPENWAIFUD_CHAT_CONFIG", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "openwaifud" / "chat.json"


class ChatService:
    """管理对话模型配置并调用 OpenAI 兼容的 chat completions 接口。"""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _default_config_path()
        self._config = self._load()

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------

    def _load(self) -> ChatConfig:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            return ChatConfig(**data)
        except FileNotFoundError:
            return ChatConfig()
        except Exception as e:
            logger.warning(f"Chat config unreadable, using defaults: {e}")
            return ChatConfig()

    @property
    def configured(self) -> bool:
        """base_url 与 model 均已配置（api_key 允许为空，兼容本地推理服务）。"""
        return bool(self._config.base_url and self._config.model)

    def get_public_config(self) -> dict[str, object]:
        """返回可公开的配置视图（api_key 只写不读，仅暴露是否已设置）。"""
        return {
            "base_url": self._config.base_url,
            "model": self._config.model,
            "api_key_set": bool(self._config.api_key),
        }

    def save_config(self, update: ChatConfig) -> None:
        """保存配置并持久化。空 api_key 表示保留已有 Key（网页端不回显）。"""
        api_key = update.api_key or self._config.api_key
        self._config = ChatConfig(
            base_url=update.base_url.strip().rstrip("/"),
            api_key=api_key,
            model=update.model.strip(),
        )
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f'Chat config saved: base_url="{self._config.base_url}" model="{self._config.model}"')

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    async def chat(self, message: str) -> str:
        """单次提问，阻塞等待上游回复并返回文本。

        :raises ChatNotConfiguredError: 尚未配置模型。
        :raises ChatUpstreamError: 上游调用失败或响应无法解析。
        """
        if not self.configured:
            raise ChatNotConfiguredError("对话模型未配置，请先在网页端设置 base_url 与 model")

        url = f"{self._config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        payload = {
            "model": self._config.model,
            "messages": [{"role": "user", "content": message}],
        }

        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=payload, headers=headers) as resp,
            ):
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    raise ChatUpstreamError(f"上游返回 HTTP {resp.status}: {body}")
                data = await resp.json(content_type=None)
        except ChatUpstreamError:
            raise
        except Exception as e:
            raise ChatUpstreamError(f"上游请求失败: {e}") from e

        try:
            reply = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ChatUpstreamError(f"上游响应格式异常: {e}") from e
        if not isinstance(reply, str):
            raise ChatUpstreamError("上游响应缺少文本内容")
        return reply
