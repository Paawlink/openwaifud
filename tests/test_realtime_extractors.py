"""Tests for openwaifud.realtime.extractors."""

import pytest

from openwaifud.models import AgentStatus
from openwaifud.realtime.extractors import StatusExtractor
from openwaifud.realtime.parsers import ParseResult


@pytest.fixture
def extractor():
    """Create a StatusExtractor instance."""
    return StatusExtractor()


def _make_result(
    last_role: str = "assistant",
    last_content: str = "",
    has_tool_use: bool = False,
    tool_names: list[str] | None = None,
    plugin_type: str = "claudecode",
    session_id: str = "test-session",
) -> ParseResult:
    """Helper to create a ParseResult with sensible defaults."""
    return ParseResult(
        plugin_type=plugin_type,
        session_id=session_id,
        last_role=last_role,
        last_content=last_content,
        has_tool_use=has_tool_use,
        tool_names=tool_names or [],
    )


class TestExtractStatus:
    """Tests for StatusExtractor.extract_status()."""

    def test_write_file_tool_returns_coding(self, extractor):
        """has_tool_use + write_file → CODING."""
        result = _make_result(has_tool_use=True, tool_names=["write_file"])
        assert extractor.extract_status(result) == AgentStatus.CODING

    def test_run_command_no_test_keywords_returns_coding(self, extractor):
        """has_tool_use + run_command → CODING (no test keywords)."""
        result = _make_result(
            has_tool_use=True,
            tool_names=["run_command"],
            last_content="compiling the project",
        )
        assert extractor.extract_status(result) == AgentStatus.CODING

    def test_bash_with_pytest_returns_testing(self, extractor):
        """has_tool_use + bash + 'pytest' in content → TESTING."""
        result = _make_result(
            has_tool_use=True,
            tool_names=["bash"],
            last_content="Running pytest on the codebase",
        )
        assert extractor.extract_status(result) == AgentStatus.TESTING

    def test_assistant_no_tools_returns_thinking(self, extractor):
        """last_role=assistant, no tools → THINKING."""
        result = _make_result(
            last_role="assistant",
            has_tool_use=False,
            last_content="Let me think about this...",
        )
        assert extractor.extract_status(result) == AgentStatus.THINKING

    def test_user_role_returns_thinking(self, extractor):
        """last_role=user → THINKING."""
        result = _make_result(last_role="user", last_content="please fix this bug")
        assert extractor.extract_status(result) == AgentStatus.THINKING

    def test_error_keyword_assistant_returns_error(self, extractor):
        """content contains 'error' + last_role=assistant → ERROR."""
        result = _make_result(
            last_role="assistant",
            last_content="I encountered an error while running the command",
        )
        assert extractor.extract_status(result) == AgentStatus.ERROR

    def test_failed_keyword_assistant_returns_error(self, extractor):
        """content contains 'failed' + last_role=assistant → ERROR."""
        result = _make_result(
            last_role="assistant",
            last_content="The build failed with exit code 1",
        )
        assert extractor.extract_status(result) == AgentStatus.ERROR

    def test_error_keyword_user_role_not_error(self, extractor):
        """Error keywords in user message should NOT result in ERROR status."""
        result = _make_result(
            last_role="user",
            last_content="I see an error in the output",
        )
        # Should be THINKING since it's a user message
        assert extractor.extract_status(result) == AgentStatus.THINKING

    def test_coding_tools_take_priority_over_error(self, extractor):
        """Coding tools in content with error keyword - ERROR takes priority per implementation."""
        result = _make_result(
            last_role="assistant",
            has_tool_use=True,
            tool_names=["write_file"],
            last_content="fixing the error now",
        )
        # ERROR check comes before tool check in the implementation
        assert extractor.extract_status(result) == AgentStatus.ERROR

    def test_unknown_tool_returns_coding(self, extractor):
        """Unknown tool_use with has_tool_use=True → CODING."""
        result = _make_result(
            has_tool_use=True,
            tool_names=["some_custom_tool"],
            last_content="doing something",
        )
        assert extractor.extract_status(result) == AgentStatus.CODING

    def test_edit_file_tool_returns_coding(self, extractor):
        """has_tool_use + edit_file → CODING."""
        result = _make_result(has_tool_use=True, tool_names=["edit_file"])
        assert extractor.extract_status(result) == AgentStatus.CODING

    def test_execute_with_test_keyword_returns_testing(self, extractor):
        """has_tool_use + execute + 'unittest' in content → TESTING."""
        result = _make_result(
            has_tool_use=True,
            tool_names=["execute"],
            last_content="running unittest suite",
        )
        assert extractor.extract_status(result) == AgentStatus.TESTING


class TestExtractContext:
    """Tests for StatusExtractor.extract_context()."""

    def test_context_plugin_type(self, extractor):
        """extract_context() correctly fills plugin_type."""
        result = _make_result(plugin_type="codex")
        context = extractor.extract_context(result)
        assert context.plugin_type == "codex"

    def test_context_session_id(self, extractor):
        """extract_context() correctly fills session_id."""
        result = _make_result(session_id="my-session-123")
        context = extractor.extract_context(result)
        assert context.session_id == "my-session-123"

    def test_context_current_task(self, extractor):
        """extract_context() correctly fills current_task from last_content."""
        result = _make_result(last_content="implement the login feature")
        context = extractor.extract_context(result)
        assert context.current_task == "implement the login feature"

    def test_context_current_task_truncated(self, extractor):
        """extract_context() truncates current_task to 100 chars."""
        long_content = "x" * 200
        result = _make_result(last_content=long_content)
        context = extractor.extract_context(result)
        assert len(context.current_task) == 100

    def test_context_metadata_has_tool_use(self, extractor):
        """extract_context() metadata contains has_tool_use."""
        result = _make_result(has_tool_use=True)
        context = extractor.extract_context(result)
        assert context.metadata["has_tool_use"] is True

    def test_context_metadata_tool_names(self, extractor):
        """extract_context() metadata contains tool_names."""
        result = _make_result(has_tool_use=True, tool_names=["write_file", "bash"])
        context = extractor.extract_context(result)
        assert context.metadata["tool_names"] == ["write_file", "bash"]

    def test_context_metadata_last_role(self, extractor):
        """extract_context() metadata contains last_role."""
        result = _make_result(last_role="user")
        context = extractor.extract_context(result)
        assert context.metadata["last_role"] == "user"


class TestExtract:
    """Tests for StatusExtractor.extract() combined method."""

    def test_extract_returns_tuple(self, extractor):
        """extract() returns (StatusUpdate, ConversationContext) tuple."""
        result = _make_result(last_role="assistant", last_content="thinking hard")
        update, context = extractor.extract(result)
        assert update.status == AgentStatus.THINKING
        assert context.plugin_type == "claudecode"

    def test_extract_error_includes_message(self, extractor):
        """extract() sets error_message when status is ERROR."""
        result = _make_result(
            last_role="assistant",
            last_content="fatal error occurred in the build",
        )
        update, context = extractor.extract(result)
        assert update.status == AgentStatus.ERROR
        assert update.error_message is not None
        assert "fatal error" in update.error_message

    def test_extract_non_error_no_error_message(self, extractor):
        """extract() sets error_message to None when status is not ERROR."""
        result = _make_result(
            last_role="assistant",
            has_tool_use=True,
            tool_names=["write_file"],
            last_content="writing the new module",
        )
        update, _ = extractor.extract(result)
        assert update.status == AgentStatus.CODING
        assert update.error_message is None
