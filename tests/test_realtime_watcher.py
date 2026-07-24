"""Tests for openwaifud.realtime.watcher."""

import json
from typing import Any, cast

import pytest

from openwaifud.config import Config
from openwaifud.realtime.parsers import ClaudeCodeParser
from openwaifud.realtime.watcher import RealtimeWatcher
from openwaifud.state.manager import StateManager


@pytest.fixture
def disabled_config():
    """Create a config with realtime disabled."""
    return Config(realtime_enabled=False)


@pytest.fixture
def enabled_config():
    """Create a config with realtime enabled."""
    return Config(realtime_enabled=True)


@pytest.fixture
def state_mgr():
    """Create a fresh StateManager."""
    return StateManager(queue_max_size=10)


class TestRealtimeWatcherDisabled:
    """Tests for RealtimeWatcher when disabled by config."""

    async def test_start_does_not_create_observer(self, disabled_config, state_mgr):
        """start() does not create observer when realtime_enabled=False."""
        watcher = RealtimeWatcher(disabled_config, state_mgr)
        await watcher.start()
        assert watcher._observer is None
        assert watcher._running is False


class TestRealtimeWatcherNoDirs:
    """Tests for RealtimeWatcher when no watch directories exist."""

    async def test_start_no_dirs_no_error(self, enabled_config, state_mgr, tmp_path):
        """start() with no available directories does not raise."""
        watcher = RealtimeWatcher(enabled_config, state_mgr)
        # Point all parsers to non-existent dirs
        for parser in watcher._parsers:
            cast(Any, parser)._base_dir = tmp_path / "nonexistent"

        await watcher.start()
        # Observer is None since no paths were scheduled
        assert watcher._observer is None
        await watcher.stop()


class TestRealtimeWatcherIntegration:
    """Integration test: watcher detects file changes and updates state."""

    async def test_process_file_updates_state(self, enabled_config, state_mgr, tmp_path):
        """Watcher._process_file correctly updates StateManager."""
        watcher = RealtimeWatcher(enabled_config, state_mgr)
        # Point Claude Code parser to tmp_path
        claude_parser = watcher._parsers[0]
        assert isinstance(claude_parser, ClaudeCodeParser)
        claude_parser._base_dir = tmp_path

        # Create a session file
        session_file = tmp_path / "test-session.jsonl"
        line = json.dumps(
            {
                "type": "assistant",
                "sessionId": "test-session",
                "uuid": "u1",
                "parentUuid": None,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Writing code..."},
                        {"type": "tool_use", "name": "write_file", "input": {"path": "x.py"}},
                    ],
                },
            }
        )
        session_file.write_text(line + "\n")

        # Simulate processing
        watcher._running = True
        await watcher._process_file(session_file)

        # Verify state was updated
        from openwaifud.models import AgentStatus

        state = state_mgr.get_current_state()
        assert state.agent_status == AgentStatus.CODING
        assert state.context is not None
        assert state.context.plugin_type == "claudecode"
        assert state.context.session_id == "test-session"

    async def test_process_file_tracks_offset(self, enabled_config, state_mgr, tmp_path):
        """Watcher tracks file offset for incremental parsing."""
        watcher = RealtimeWatcher(enabled_config, state_mgr)
        claude_parser = watcher._parsers[0]
        assert isinstance(claude_parser, ClaudeCodeParser)
        claude_parser._base_dir = tmp_path

        session_file = tmp_path / "offset-test.jsonl"
        line1 = json.dumps(
            {
                "type": "user",
                "sessionId": "offset-test",
                "uuid": "u1",
                "parentUuid": None,
                "message": {"role": "user", "content": "do something"},
            }
        )
        session_file.write_text(line1 + "\n")

        watcher._running = True
        await watcher._process_file(session_file)

        # Offset should now be set
        assert session_file in watcher._offsets
        assert watcher._offsets[session_file] > 0

    async def test_stop_cleans_up(self, enabled_config, state_mgr):
        """stop() cleans up debounce tasks and observer."""
        watcher = RealtimeWatcher(enabled_config, state_mgr)
        watcher._running = True
        await watcher.stop()
        assert watcher._running is False
        assert watcher._observer is None
        assert len(watcher._debounce_tasks) == 0
