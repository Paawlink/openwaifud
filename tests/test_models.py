"""Tests for openwaifud.models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from openwaifud.models import (
    AgentStatus,
    ChatMessage,
    ConversationContext,
    DaemonState,
    DetailUpdate,
    GlobalEvent,
    GlobalEventKind,
    SessionDetail,
    StatusUpdate,
)


class TestAgentStatus:
    """Tests for AgentStatus enum."""

    def test_enum_values(self):
        """AgentStatus enum contains expected values."""
        assert AgentStatus.IDLE == "idle"
        assert AgentStatus.THINKING == "thinking"
        assert AgentStatus.CODING == "coding"
        assert AgentStatus.TESTING == "testing"
        assert AgentStatus.ERROR == "error"

    def test_enum_member_count(self):
        """AgentStatus has exactly 5 members."""
        assert len(AgentStatus) == 5


class TestStatusUpdate:
    """Tests for StatusUpdate model."""

    def test_create_with_defaults(self):
        """StatusUpdate can be created with default timestamp."""
        update = StatusUpdate(status=AgentStatus.CODING)
        assert update.status == AgentStatus.CODING
        assert update.error_message is None
        assert isinstance(update.timestamp, datetime)

    def test_create_with_error_message(self):
        """StatusUpdate can include an error message."""
        update = StatusUpdate(status=AgentStatus.ERROR, error_message="Something failed")
        assert update.status == AgentStatus.ERROR
        assert update.error_message == "Something failed"

    def test_invalid_status_raises_validation_error(self):
        """StatusUpdate with invalid status raises ValidationError."""
        with pytest.raises(ValidationError):
            StatusUpdate.model_validate({"status": "invalid_status"})

    def test_invalid_type_raises_validation_error(self):
        """StatusUpdate with wrong type for status raises ValidationError."""
        with pytest.raises(ValidationError):
            StatusUpdate.model_validate({"status": 12345})


class TestConversationContext:
    """Tests for ConversationContext model."""

    def test_create_valid(self):
        """ConversationContext can be created with required fields."""
        ctx = ConversationContext(
            plugin_type="opencode",
            session_id="abc-123",
        )
        assert ctx.plugin_type == "opencode"
        assert ctx.session_id == "abc-123"
        assert ctx.current_task == ""
        assert ctx.metadata == {}
        assert isinstance(ctx.timestamp, datetime)

    def test_create_with_all_fields(self):
        """ConversationContext can be created with all fields."""
        ctx = ConversationContext(
            plugin_type="claudecode",
            session_id="session-1",
            current_task="Fix bug #42",
            metadata={"file": "main.py"},
        )
        assert ctx.current_task == "Fix bug #42"
        assert ctx.metadata == {"file": "main.py"}

    def test_missing_plugin_type_raises_validation_error(self):
        """ConversationContext without plugin_type raises ValidationError."""
        with pytest.raises(ValidationError):
            ConversationContext.model_validate({"session_id": "abc-123"})

    def test_missing_session_id_raises_validation_error(self):
        """ConversationContext without session_id raises ValidationError."""
        with pytest.raises(ValidationError):
            ConversationContext.model_validate({"plugin_type": "opencode"})


class TestGlobalEvent:
    """Tests for GlobalEventKind / GlobalEvent（泳道 2）。"""

    def test_kind_enum_values(self):
        assert GlobalEventKind.ERROR == "error"
        assert GlobalEventKind.CANCEL == "cancel"
        assert GlobalEventKind.DONE == "done"
        assert len(GlobalEventKind) == 3

    def test_create_with_defaults(self):
        ev = GlobalEvent(event=GlobalEventKind.CANCEL)
        assert ev.event == GlobalEventKind.CANCEL
        assert ev.session_id is None
        assert ev.message is None
        assert isinstance(ev.timestamp, datetime)

    def test_create_with_all_fields(self):
        ev = GlobalEvent(event=GlobalEventKind.ERROR, session_id="s1", message="boom")
        assert ev.event == GlobalEventKind.ERROR
        assert ev.session_id == "s1"
        assert ev.message == "boom"

    def test_invalid_event_raises_validation_error(self):
        with pytest.raises(ValidationError):
            GlobalEvent.model_validate({"event": "nope"})

    def test_missing_event_raises_validation_error(self):
        with pytest.raises(ValidationError):
            GlobalEvent.model_validate({"session_id": "s1"})


class TestDaemonState:
    """Tests for DaemonState model."""

    def test_default_values(self):
        """DaemonState has correct default values."""
        state = DaemonState()
        assert state.agent_status == AgentStatus.IDLE
        assert state.context is None
        assert state.ble_connected is False
        assert state.uptime_seconds == 0.0
        assert isinstance(state.timestamp, datetime)


class TestChatMessage:
    """Tests for ChatMessage model."""

    def test_create_with_required_fields(self):
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert isinstance(msg.timestamp, datetime)

    def test_default_content(self):
        msg = ChatMessage(role="assistant")
        assert msg.content == ""

    def test_missing_role_raises(self):
        with pytest.raises(ValidationError):
            ChatMessage.model_validate({"content": "hi"})


class TestSessionDetail:
    """Tests for SessionDetail model."""

    def test_default_values(self):
        detail = SessionDetail(session_id="s1")
        assert detail.session_id == "s1"
        assert detail.plugin_type == "agent"
        assert detail.status == AgentStatus.THINKING
        assert detail.metadata == {}
        assert detail.chat_context == []
        assert detail.started_at is None
        assert detail.updated_at is None

    def test_with_full_data(self):
        msgs = [ChatMessage(role="user", content="hi")]
        detail = SessionDetail(
            session_id="s1",
            plugin_type="opencode",
            status=AgentStatus.CODING,
            current_task="Fix bug",
            metadata={"dir": "/tmp"},
            chat_context=msgs,
        )
        assert detail.plugin_type == "opencode"
        assert len(detail.chat_context) == 1
        assert detail.chat_context[0].role == "user"


class TestDetailUpdate:
    """Tests for DetailUpdate model."""

    def test_create_with_metadata_only(self):
        update = DetailUpdate(session_id="s1", metadata={"key": "value"})
        assert update.session_id == "s1"
        assert update.metadata == {"key": "value"}
        assert update.chat_context is None
        assert update.error_message is None

    def test_create_with_chat_context(self):
        msgs = [ChatMessage(role="tool", content="edit")] 
        update = DetailUpdate(session_id="s1", chat_context=msgs)
        assert update.chat_context is not None
        assert len(update.chat_context) == 1

    def test_missing_session_id_raises(self):
        with pytest.raises(ValidationError):
            DetailUpdate.model_validate({"metadata": {}})
