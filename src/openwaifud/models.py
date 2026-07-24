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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationContext(BaseModel):
    """Conversation context from IDE plugin."""

    plugin_type: str = Field(..., description="IDE plugin type: opencode/claudecode/codex")
    session_id: str = Field(..., description="Conversation session ID")
    current_task: str = Field(default="", description="Current task description")
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DaemonState(BaseModel):
    """Current daemon state snapshot."""

    agent_status: AgentStatus = AgentStatus.IDLE
    context: ConversationContext | None = None
    ble_connected: bool = False
    uptime_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
