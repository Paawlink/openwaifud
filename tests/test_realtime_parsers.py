"""Tests for openwaifud.realtime.parsers."""

import json

from openwaifud.realtime.parsers import ClaudeCodeParser, CodexParser


class TestClaudeCodeParser:
    """Tests for ClaudeCodeParser."""

    def test_get_watch_paths_missing_dir(self, tmp_path):
        """get_watch_paths() returns empty list when directory does not exist."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path / "nonexistent"
        assert parser.get_watch_paths() == []

    def test_get_watch_paths_existing_dir(self, tmp_path):
        """get_watch_paths() returns list with base_dir when it exists."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path
        assert parser.get_watch_paths() == [tmp_path]

    def test_parse_latest_normal_jsonl(self, tmp_path):
        """parse_latest() parses normal JSONL with type/message/content."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path

        session_file = tmp_path / "abc123.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "abc123",
                    "uuid": "u1",
                    "parentUuid": None,
                    "message": {"role": "user", "content": "implement a hello world function"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "abc123",
                    "uuid": "u2",
                    "parentUuid": "u1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I'll create that for you."},
                            {
                                "type": "tool_use",
                                "name": "write_file",
                                "input": {
                                    "path": "hello.py",
                                    "content": "def hello(): return 'world'",
                                },
                            },
                        ],
                    },
                }
            ),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = parser.parse_latest(session_file)
        assert result is not None
        assert result.plugin_type == "claudecode"
        assert result.session_id == "abc123"
        assert result.last_role == "assistant"
        assert "I'll create that for you." in result.last_content
        assert result.has_tool_use is True
        assert "write_file" in result.tool_names

    def test_parse_latest_incremental(self, tmp_path):
        """parse_latest() with offset > 0 only parses new content."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path

        session_file = tmp_path / "sess1.jsonl"
        first_line = json.dumps(
            {
                "type": "user",
                "sessionId": "sess1",
                "uuid": "u1",
                "parentUuid": None,
                "message": {"role": "user", "content": "first message"},
            }
        )
        session_file.write_text(first_line + "\n")
        first_offset = len((first_line + "\n").encode("utf-8"))

        # Parse from beginning
        result1 = parser.parse_latest(session_file, offset=0)
        assert result1 is not None
        assert result1.last_content == "first message"
        assert result1.new_offset == first_offset

        # Append second line
        second_line = json.dumps(
            {
                "type": "assistant",
                "sessionId": "sess1",
                "uuid": "u2",
                "parentUuid": "u1",
                "message": {"role": "assistant", "content": "second message"},
            }
        )
        with session_file.open("a") as f:
            f.write(second_line + "\n")

        # Parse incrementally
        result2 = parser.parse_latest(session_file, offset=result1.new_offset)
        assert result2 is not None
        assert result2.last_role == "assistant"
        assert result2.last_content == "second message"
        assert result2.new_offset > result1.new_offset

    def test_parse_latest_no_new_content(self, tmp_path):
        """parse_latest() returns None when offset is at end of file."""
        parser = ClaudeCodeParser()
        session_file = tmp_path / "empty_session.jsonl"
        line = json.dumps(
            {
                "type": "user",
                "sessionId": "s1",
                "uuid": "u1",
                "parentUuid": None,
                "message": {"role": "user", "content": "hello"},
            }
        )
        session_file.write_text(line + "\n")
        file_size = session_file.stat().st_size

        result = parser.parse_latest(session_file, offset=file_size)
        assert result is None

    def test_parse_latest_malformed_lines(self, tmp_path):
        """parse_latest() skips malformed lines gracefully."""
        parser = ClaudeCodeParser()

        session_file = tmp_path / "bad.jsonl"
        lines = [
            "this is not json",
            "{broken json here",
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "bad",
                    "uuid": "u1",
                    "parentUuid": None,
                    "message": {"role": "assistant", "content": "valid message"},
                }
            ),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = parser.parse_latest(session_file)
        assert result is not None
        assert result.last_role == "assistant"
        assert result.last_content == "valid message"

    def test_parse_latest_all_malformed(self, tmp_path):
        """parse_latest() returns None when all lines are malformed."""
        parser = ClaudeCodeParser()

        session_file = tmp_path / "allbad.jsonl"
        session_file.write_text("not json\nalso not json\n")

        result = parser.parse_latest(session_file)
        assert result is None

    def test_parse_latest_detects_tool_use(self, tmp_path):
        """parse_latest() detects tool_use blocks correctly."""
        parser = ClaudeCodeParser()

        session_file = tmp_path / "tools.jsonl"
        line = json.dumps(
            {
                "type": "assistant",
                "sessionId": "tools",
                "uuid": "u1",
                "parentUuid": None,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Running tests now."},
                        {"type": "tool_use", "name": "bash", "input": {"command": "pytest"}},
                        {"type": "tool_use", "name": "write_file", "input": {"path": "x.py"}},
                    ],
                },
            }
        )
        session_file.write_text(line + "\n")

        result = parser.parse_latest(session_file)
        assert result is not None
        assert result.has_tool_use is True
        assert "bash" in result.tool_names
        assert "write_file" in result.tool_names

    def test_find_active_session_missing_dir(self, tmp_path):
        """find_active_session() returns None when directory does not exist."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path / "nonexistent"
        assert parser.find_active_session() is None

    def test_find_active_session_no_files(self, tmp_path):
        """find_active_session() returns None when no .jsonl files exist."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path
        assert parser.find_active_session() is None

    def test_find_active_session_returns_latest(self, tmp_path):
        """find_active_session() returns the most recently modified file."""
        parser = ClaudeCodeParser()
        parser._base_dir = tmp_path

        import time

        old_file = tmp_path / "old.jsonl"
        old_file.write_text('{"type":"user"}\n')
        time.sleep(0.05)
        new_file = tmp_path / "new.jsonl"
        new_file.write_text('{"type":"user"}\n')

        result = parser.find_active_session()
        assert result is not None
        path, offset = result
        assert path == new_file
        assert offset == 0


class TestCodexParser:
    """Tests for CodexParser."""

    def test_parse_latest_with_session_meta(self, tmp_path):
        """parse_latest() correctly parses JSONL with session_meta header."""
        parser = CodexParser()
        parser._base_dir = tmp_path

        session_file = tmp_path / "rollout-12345.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "codex-session-001",
                        "cwd": "/Users/test/project",
                        "model_provider": "openai",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "fix the bug in main.py"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "I'll fix that bug now."}],
                    },
                }
            ),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = parser.parse_latest(session_file)
        assert result is not None
        assert result.plugin_type == "codex"
        assert result.last_role == "assistant"
        assert "I'll fix that bug now." in result.last_content

    def test_parse_latest_extracts_session_id_from_meta(self, tmp_path):
        """parse_latest() extracts session_id from session_meta payload."""
        parser = CodexParser()
        parser._base_dir = tmp_path

        session_file = tmp_path / "rollout-xyz.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "codex-session-001", "cwd": "/tmp"},
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                }
            ),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = parser.parse_latest(session_file)
        assert result is not None
        assert result.session_id == "codex-session-001"

    def test_parse_latest_incremental(self, tmp_path):
        """parse_latest() incremental parsing (offset > 0) skips session_meta."""
        parser = CodexParser()
        parser._base_dir = tmp_path

        session_file = tmp_path / "session.jsonl"
        first_lines = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "codex-session-001", "cwd": "/tmp"},
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "first"}],
                    },
                }
            ),
        ]
        session_file.write_text("\n".join(first_lines) + "\n")

        result1 = parser.parse_latest(session_file, offset=0)
        assert result1 is not None
        assert result1.session_id == "codex-session-001"

        # Append new line
        new_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done!"}],
                },
            }
        )
        with session_file.open("a") as f:
            f.write(new_line + "\n")

        # Incremental parse - session_id falls back to filename stem
        result2 = parser.parse_latest(session_file, offset=result1.new_offset)
        assert result2 is not None
        assert result2.last_role == "assistant"
        assert result2.last_content == "done!"
        # When offset > 0, session_meta is not re-parsed, session_id is file stem
        assert result2.session_id == "session"

    def test_get_watch_paths_missing_dir(self, tmp_path):
        """get_watch_paths() returns empty list when directory does not exist."""
        parser = CodexParser()
        parser._base_dir = tmp_path / "nonexistent"
        assert parser.get_watch_paths() == []
