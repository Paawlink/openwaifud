"""即时对话服务：直连 OpenAI 兼容 API，同步取得回复。

daemon 直接调用网页端配置的 OpenAI 兼容接口（base_url + api_key + model），
单次提问、阻塞等待并返回回复文本，适合桌宠侧的即时语音对话场景。

每次请求会为内置语音助手「涂鸦」构建 system prompt，注入 daemon 的
实时状态快照与存活的 OpenCode 实例列表（见 :mod:`openwaifud.chat.prompt`）；
服务内维护短期多轮对话历史，并支持对话技能（OpenAI function calling，
见 :mod:`openwaifud.chat.skills`），使涂鸦能在多轮确认后创建 OpenCode 会话。

配置持久化到本地 JSON 文件（默认 ``~/.config/openwaifud/chat.json``，
可用环境变量 ``OPENWAIFUD_CHAT_CONFIG`` 覆盖路径）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from openwaifud.chat.prompt import build_system_prompt
from openwaifud.chat.skills import ChatSkill
from openwaifud.models import ChatConfig, DaemonState, PluginInstanceInfo

# 上游推理可能较慢，给足总超时
_REQUEST_TIMEOUT_SECONDS = 120.0
# 保留的历史消息上限（user/assistant 各算一条），支撑"确认实例"这类多轮交互
_HISTORY_MAX_MESSAGES = 20
# 单次提问内允许的最大工具调用轮数（防止模型循环调用）
_MAX_TOOL_ROUNDS = 3


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

    def __init__(
        self,
        config_path: Path | None = None,
        state_provider: Callable[[], DaemonState] | None = None,
        instances_provider: Callable[[], list[PluginInstanceInfo]] | None = None,
        skills: Sequence[ChatSkill] | None = None,
    ) -> None:
        """
        :param state_provider: 返回 daemon 当前状态快照的回调（通常是
            ``StateManager.get_current_state``），用于向 system prompt 注入
            实时状态；不提供时 prompt 仅含人设。
        :param instances_provider: 返回存活 OpenCode 实例列表的回调（通常是
            ``StateManager.list_live_instances``），供新建会话技能确认目标。
        :param skills: 对话技能列表，作为 tools 声明传给上游模型。
        """
        self._config_path = config_path or _default_config_path()
        self._config = self._load()
        self._state_provider = state_provider
        self._instances_provider = instances_provider
        self._skills: list[ChatSkill] = list(skills or [])
        # 短期多轮对话历史（仅保留最终的 user/assistant 文本回合）
        self._history: list[dict[str, Any]] = []

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
        """提问一次，阻塞等待上游回复并返回文本。

        带短期多轮历史；若模型发起工具调用（对话技能），在本次请求内
        同步执行并把结果回传模型，直到得到最终的文本回复。

        :raises ChatNotConfiguredError: 尚未配置模型。
        :raises ChatUpstreamError: 上游调用失败或响应无法解析。
        """
        if not self.configured:
            raise ChatNotConfiguredError("对话模型未配置，请先在网页端设置 base_url 与 model")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
            *self._history,
            {"role": "user", "content": message},
        ]
        tools = [skill.spec() for skill in self._skills]

        for _ in range(_MAX_TOOL_ROUNDS):
            assistant = await self._request_completion(messages, tools)
            tool_calls = assistant.get("tool_calls")
            if not tool_calls:
                break
            # 把含 tool_calls 的 assistant 消息与各技能执行结果追加后再问一轮
            messages.append(assistant)
            for call in tool_calls:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": self._execute_tool_call(call),
                    }
                )
        else:
            raise ChatUpstreamError(f"工具调用超过 {_MAX_TOOL_ROUNDS} 轮仍未得到文本回复")

        reply = assistant.get("content")
        if not isinstance(reply, str) or not reply.strip():
            raise ChatUpstreamError("上游响应缺少文本内容")

        # 仅持久化最终的文本回合（中间的 tool 交互不进历史），并裁剪上限
        self._history.append({"role": "user", "content": message})
        self._history.append({"role": "assistant", "content": reply})
        if len(self._history) > _HISTORY_MAX_MESSAGES:
            self._history = self._history[-_HISTORY_MAX_MESSAGES:]
        return reply

    async def _request_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """调用一次 chat completions，返回 choices[0].message 对象。"""
        url = f"{self._config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        payload: dict[str, Any] = {"model": self._config.model, "messages": messages}
        if tools:
            payload["tools"] = tools

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
            assistant = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise ChatUpstreamError(f"上游响应格式异常: {e}") from e
        if not isinstance(assistant, dict):
            raise ChatUpstreamError("上游响应格式异常: message 不是对象")
        return assistant

    def _execute_tool_call(self, call: dict[str, Any]) -> str:
        """执行一次工具调用，失败时返回错误文本（交由模型向用户解释）。"""
        func = call.get("function") or {}
        name = str(func.get("name", ""))
        skill = next((s for s in self._skills if s.name == name), None)
        if skill is None:
            return f"执行失败：未知技能 {name}。"
        try:
            arguments = json.loads(func.get("arguments") or "{}")
        except (TypeError, ValueError):
            return "执行失败：技能参数不是合法的 JSON。"
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            return skill.execute(arguments)
        except Exception as e:
            logger.error(f"Skill {name} execution error: {e}")
            return f"执行失败：{e}"

    def _build_system_prompt(self) -> str:
        """构建「涂鸦」的 system prompt，尽力注入实时状态与实例列表。"""
        state: DaemonState | None = None
        if self._state_provider is not None:
            try:
                state = self._state_provider()
            except Exception as e:
                logger.warning(f"State snapshot unavailable for chat prompt: {e}")
        instances: list[PluginInstanceInfo] | None = None
        if self._instances_provider is not None:
            try:
                instances = self._instances_provider()
            except Exception as e:
                logger.warning(f"Instance list unavailable for chat prompt: {e}")
        return build_system_prompt(state, instances)
