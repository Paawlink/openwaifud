"""Tests for openwaifud.models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from openwaifud.models import AgentStatus, ConversationContext, DaemonState, StatusUpdate


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
            StatusUpdate(status="invalid_status")

    def test_invalid_type_raises_validation_error(self):
        """StatusUpdate with wrong type for status raises ValidationError."""
        with pytest.raises(ValidationError):
            StatusUpdate(status=12345)


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
            ConversationContext(session_id="abc-123")

    def test_missing_session_id_raises_validation_error(self):
        """ConversationContext without session_id raises ValidationError."""
        with pytest.raises(ValidationError):
            ConversationContext(plugin_type="opencode")


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
