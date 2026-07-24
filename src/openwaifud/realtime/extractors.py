"""Status extraction and inference from parsed conversation data."""

from __future__ import annotations

from openwaifud.models import AgentStatus, ConversationContext, StatusUpdate
from openwaifud.realtime.parsers import ParseResult

# 工具名 → 状态映射关键词
_CODING_TOOLS = {"write_file", "edit_file", "create_file", "str_replace_editor", "write", "patch"}
_TESTING_TOOLS = {"run_command", "execute", "bash", "terminal", "shell"}
_TESTING_KEYWORDS = {"test", "pytest", "unittest", "assert", "spec", "coverage"}
_ERROR_KEYWORDS = {"error", "failed", "exception", "traceback", "panic", "fatal"}


class StatusExtractor:
    """Infers AgentStatus and builds ConversationContext from ParseResult."""

    def extract_status(self, result: ParseResult) -> AgentStatus:
        """Infer agent status from parse result.

        Priority:
        1. ERROR: content contains error keywords
        2. CODING: has tool_use with coding-related tools
        3. TESTING: has tool_use with execution tools or testing keywords
        4. THINKING: assistant message without tool use
        5. IDLE: fallback (or user message suggesting waiting)
        """
        content_lower = result.last_content.lower()
        tool_names_lower = {t.lower() for t in result.tool_names}

        # Check for error indicators
        if result.last_role == "assistant" and any(kw in content_lower for kw in _ERROR_KEYWORDS):
            return AgentStatus.ERROR

        # Check tool use
        if result.has_tool_use:
            # Coding tools take priority
            if tool_names_lower & _CODING_TOOLS:
                return AgentStatus.CODING
            # Testing/execution tools
            if tool_names_lower & _TESTING_TOOLS:
                # Check if testing-related content
                if any(kw in content_lower for kw in _TESTING_KEYWORDS):
                    return AgentStatus.TESTING
                return AgentStatus.CODING  # Generic execution = still coding
            # Unknown tool use = coding activity
            return AgentStatus.CODING

        # No tool use
        if result.last_role == "assistant":
            return AgentStatus.THINKING

        # User message = agent is about to think
        if result.last_role == "user":
            return AgentStatus.THINKING

        return AgentStatus.IDLE

    def extract_context(self, result: ParseResult) -> ConversationContext:
        """Build ConversationContext from parse result."""
        # current_task: use last user content or last content as fallback
        current_task = result.last_content[:100] if result.last_content else ""

        return ConversationContext(
            plugin_type=result.plugin_type,
            session_id=result.session_id,
            current_task=current_task,
            metadata={
                "has_tool_use": result.has_tool_use,
                "tool_names": result.tool_names,
                "last_role": result.last_role,
            },
            timestamp=result.timestamp,
        )

    def extract(self, result: ParseResult) -> tuple[StatusUpdate, ConversationContext]:
        """Extract both status and context from parse result."""
        status = self.extract_status(result)
        context = self.extract_context(result)

        error_message = None
        if status == AgentStatus.ERROR:
            error_message = result.last_content[:200]

        update = StatusUpdate(
            status=status,
            error_message=error_message,
            timestamp=result.timestamp,
        )

        return update, context
