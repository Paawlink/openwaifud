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


class DaemonState(BaseModel):
    """Current daemon state snapshot."""

    agent_status: AgentStatus = AgentStatus.IDLE
    context: ConversationContext | None = None
    # 当前所有活跃会话（驱动固件端“情绪”状态机）。
    sessions: list[SessionInfo] = Field(default_factory=list)
    ble_connected: bool = False
    uptime_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
