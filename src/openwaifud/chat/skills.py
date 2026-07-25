"""「涂鸦」的对话技能（OpenAI function calling 工具）。

技能是即时对话链路的扩展点：ChatService 把每个技能的 :meth:`spec` 作为
``tools`` 参数传给上游模型；模型在对话中判断出用户意图后发起 tool call，
ChatService 调用对应技能的 :meth:`execute` 并把结果文本回传给模型，由模型
组织成最终的语音回复。

当前唯一的技能是"创建 OpenCode 会话"：用户在语音对话中表达出想新开一个
聊天/任务的意愿时，涂鸦先向用户确认目标实例（不自动选取最新实例），再
调用本技能登记一条定向指令，等待目标实例的 IDE 插件轮询领取并实际创建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

if TYPE_CHECKING:
    from openwaifud.state.manager import StateManager


class ChatSkill(Protocol):
    """对话技能协议：提供 OpenAI tool 声明并同步执行。"""

    name: str

    def spec(self) -> dict[str, Any]:
        """返回 OpenAI 兼容的 tool 声明（``tools`` 数组中的一项）。"""
        ...

    def execute(self, arguments: dict[str, Any]) -> str:
        """执行技能并返回结果文本（作为 tool 消息回传给模型）。"""
        ...


class CreateOpenCodeSessionSkill:
    """在用户确认的 OpenCode 实例中创建一个新会话。"""

    name = "create_opencode_session"

    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "在指定的 OpenCode 实例中创建一个新的编程会话。"
                    "仅在用户明确表达想新开聊天/会话/任务、且已确认目标实例后调用；"
                    "instance_id 必须取自 system prompt 中列出的存活实例，不得编造。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance_id": {
                            "type": "string",
                            "description": "目标 OpenCode 实例的 ID（来自实例列表）",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "可选的首条消息文本（用户想让编程助手做的事）",
                        },
                    },
                    "required": ["instance_id"],
                },
            },
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        instance_id = str(arguments.get("instance_id", "")).strip()
        prompt = str(arguments.get("prompt", "")).strip()
        if not instance_id:
            return "创建失败：缺少 instance_id 参数。"

        pending = self._state_manager.request_session_create(instance_id, prompt)
        if pending is None:
            return f"创建失败：实例 {instance_id} 不存在或已离线，请让用户重新确认实例列表。"

        logger.info(f"Session create queued via chat skill: {pending.request_id} -> {instance_id}")
        return (
            f"已向实例 {instance_id} 的项目（{pending.directory}）下发创建会话指令，"
            f"OpenCode 会在几秒内建好新会话。"
        )
