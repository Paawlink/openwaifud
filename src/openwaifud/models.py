"""Data models for OpenWaifuD."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(StrEnum):
    """Agent work status."""

    IDLE = "idle"
    THINKING = "thinking"
    CODING = "coding"
    TESTING = "testing"
    ERROR = "error"


class GlobalEventKind(StrEnum):
    """全局事件类型（泳道 2：独立于会话列表的即时通知）。"""

    ERROR = "error"
    CANCEL = "cancel"
    DONE = "done"


class GlobalEvent(BaseModel):
    """来自 IDE 插件的全局事件（会话出错 / 被用户取消等）。

    与会话列表（泳道 1）不同，全局事件走独立的事件流，由守护进程即时中继到
    设备端，用于驱动屏幕左下角的全局状态机（瞬时展示后自动回落）。
    """

    event: GlobalEventKind
    session_id: str | None = None
    message: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StatusUpdate(BaseModel):
    """Status update request from IDE plugin."""

    status: AgentStatus
    error_message: str | None = None
    # 可选的会话标识：多会话场景下将状态归属到具体会话；
    # 不提供时守护进程会将其应用到最近一个活跃会话。
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationContext(BaseModel):
    """Conversation context from IDE plugin."""

    plugin_type: str = Field(..., description="IDE plugin type: opencode/claudecode/codex")
    session_id: str = Field(..., description="Conversation session ID")
    current_task: str = Field(default="", description="Current task description")
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessage(BaseModel):
    """单条聊天消息（用于会话详情中的上下文展示）。

    由 IDE 插件从对话流中提取并上报，守护进程原样存储并转发给固件详情页。
    content 为摘要文本（已截断），不保证包含完整原始内容。
    """

    role: str = Field(..., description="消息角色：user/assistant/tool/system")
    content: str = Field(default="", description="消息内容摘要（已截断）")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionInfo(BaseModel):
    """单个活跃 Agent 会话的快照（用于 GET /state 展示与 BLE 推送）。"""

    session_id: str
    plugin_type: str = "agent"
    status: AgentStatus = AgentStatus.THINKING
    current_task: str = ""
    error_message: str | None = None
    # 会话已运行的秒数（自首次出现起计）。
    elapsed_seconds: float = 0.0
    # 会话是否已完成/空闲（即将从屏幕上移除）。
    is_done: bool = False


class SessionDetail(BaseModel):
    """单个会话的完整详情（用于详情页展示）。

    在 :class:`SessionInfo` 的基础上扩展了元数据、聊天上下文和墙钟时间戳，
    供 GET /api/v1/session/{id}/detail 返回，并经 BLE D 命令同步到固件。
    """

    session_id: str
    plugin_type: str = "agent"
    status: AgentStatus = AgentStatus.THINKING
    current_task: str = ""
    error_message: str | None = None
    elapsed_seconds: float = 0.0
    is_done: bool = False
    # 由 IDE 插件上报的元数据（directory、source、agent 等）。
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 最近若干条聊天消息摘要（用于详情页上下文展示）。
    chat_context: list[ChatMessage] = Field(default_factory=list)
    # 墙钟时间戳（UTC），供详情页展示"开始时间""最后更新"。
    started_at: datetime | None = None
    updated_at: datetime | None = None


class DetailUpdate(BaseModel):
    """会话详情更新请求（来自 IDE 插件）。

    与 :class:`StatusUpdate` / :class:`ConversationContext` 不同，本接口用于
    上报**详情级别**的数据：元数据与聊天上下文。仅传入的字段（非 None）
    会被合并，便于插件在事件驱动下增量上报。
    """

    session_id: str = Field(..., description="目标会话 ID")
    metadata: dict[str, Any] | None = None
    chat_context: list[ChatMessage] | None = None
    error_message: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PluginInstanceInfo(BaseModel):
    """一个存活的 OpenCode 插件实例的公开视图。

    由插件轮询心跳维护；注入「涂鸦」的 system prompt 供其在对话中
    向用户列举并确认目标实例。
    """

    instance_id: str
    directory: str = ""


class PendingSessionCreate(BaseModel):
    """待 IDE 插件领取的“创建会话”指令。

    由「涂鸦」对话技能在用户确认目标实例后登记，定向下发给指定的
    存活 OpenCode 实例（``instance_id``）；``directory`` 解析为该实例自己
    上报的工作区目录（未上报时回退到用户主目录），使新会话出现在
    目标实例自己的项目里。
    """

    request_id: str
    instance_id: str
    directory: str
    prompt: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatConfig(BaseModel):
    """即时对话接口的模型配置（OpenAI 兼容 API）。

    由网页端配置并持久化到本地文件；api_key 只写不读（GET 仅返回
    是否已配置）。base_url 为兼容端点前缀，如 ``https://api.openai.com/v1``。
    """

    base_url: str = Field(default="", max_length=200, description="OpenAI 兼容 API 前缀")
    api_key: str = Field(default="", max_length=200, description="API Key（只写）")
    model: str = Field(default="", max_length=100, description="模型名，如 gpt-4o-mini")


class ChatRequest(BaseModel):
    """即时对话请求：单次提问，同步返回回复。"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")


class WifiProvisionRequest(BaseModel):
    """WiFi 配网请求（来自网页端配网界面）。

    守护进程收到后经 BLE ``W`` 命令下发到硬件设备，由设备自行连接 WiFi。
    password 允许为空（开放网络）。
    """

    ssid: str = Field(..., min_length=1, max_length=32, description="WiFi SSID（2.4GHz）")
    password: str = Field(default="", max_length=64, description="WiFi 密码，空表示开放网络")


class DaemonState(BaseModel):
    """Current daemon state snapshot."""

    agent_status: AgentStatus = AgentStatus.IDLE
    context: ConversationContext | None = None
    # 当前所有活跃会话（驱动固件端"情绪"状态机）。
    sessions: list[SessionInfo] = Field(default_factory=list)
    ble_connected: bool = False
    uptime_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
