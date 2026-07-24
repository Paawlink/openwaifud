"""Tests for openwaifud.realtime.opencode (SQLite-backed OpenCode monitoring)."""

import json
import sqlite3

from openwaifud.realtime.opencode import OpenCodeParser, OpenCodeReader

_SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT,
    project_id TEXT,
    time_created INTEGER NOT NULL
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def _make_db(tmp_path):
    """Create an OpenCode-like SQLite database and return its path + connection."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return db_path, conn


def _add_message(conn, *, message_id, session_id, time_created, data, parts=None):
    """Insert one message row plus its part rows."""
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        (message_id, session_id, time_created, json.dumps(data)),
    )
    for idx, part in enumerate(parts or []):
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (f"{message_id}-p{idx}", message_id, session_id, time_created, json.dumps(part)),
        )
    conn.commit()


class TestOpenCodeReaderPathDiscovery:
    """Tests for OpenCodeReader.find_database_path()."""

    def test_explicit_path_wins(self, tmp_path):
        db_path = tmp_path / "custom.db"
        db_path.write_text("")
        assert OpenCodeReader.find_database_path(db_path) == db_path

    def test_env_override(self, tmp_path, monkeypatch):
        db_path = tmp_path / "env.db"
        db_path.write_text("")
        monkeypatch.setenv("OPENWAIFUD_OPENCODE_DB", str(db_path))
        assert OpenCodeReader.find_database_path() == db_path

    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENWAIFUD_OPENCODE_DB", raising=False)
        monkeypatch.setattr(OpenCodeReader, "DEFAULT_DB_PATH", tmp_path / "default-absent.db")
        missing = tmp_path / "nope.db"
        assert OpenCodeReader.find_database_path(missing) is None


class TestOpenCodeReaderActivity:
    """Tests for OpenCodeReader.read_latest_activity()."""

    def test_reads_latest_assistant_message_with_tool(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        _add_message(
            conn,
            message_id="m1",
            session_id="ses_1",
            time_created=1000,
            data={"role": "user"},
            parts=[{"type": "text", "text": "please edit main.py"}],
        )
        _add_message(
            conn,
            message_id="m2",
            session_id="ses_1",
            time_created=2000,
            data={
                "role": "assistant",
                "agent": "build",
                "modelID": "Claude-Sonnet-4",
                "path": {"cwd": "/home/user/proj"},
            },
            parts=[
                {"type": "text", "text": "Editing the file now."},
                {"type": "tool", "tool": "edit", "state": {"status": "completed"}},
            ],
        )
        conn.close()

        activity = OpenCodeReader().read_latest_activity(db_path, since_ts=0)
        assert activity is not None
        assert activity.session_id == "ses_1"
        assert activity.role == "assistant"
        assert activity.time_created == 2000
        assert "Editing the file now." in activity.text
        assert activity.tool_names == ["edit"]
        assert activity.has_error is False
        assert activity.project_path == "/home/user/proj"
        assert activity.agent == "build"
        assert activity.model_id == "claude-sonnet-4"

    def test_since_ts_filters_old_messages(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        _add_message(
            conn,
            message_id="m1",
            session_id="ses_1",
            time_created=1000,
            data={"role": "assistant"},
            parts=[{"type": "text", "text": "old"}],
        )
        conn.close()

        assert OpenCodeReader().read_latest_activity(db_path, since_ts=1000) is None

    def test_tool_error_sets_has_error(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        _add_message(
            conn,
            message_id="m1",
            session_id="ses_1",
            time_created=1000,
            data={"role": "assistant"},
            parts=[{"type": "tool", "tool": "bash", "state": {"status": "error"}}],
        )
        conn.close()

        activity = OpenCodeReader().read_latest_activity(db_path, since_ts=0)
        assert activity is not None
        assert activity.has_error is True
        assert activity.tool_names == ["bash"]

    def test_missing_db_returns_none(self, tmp_path):
        assert OpenCodeReader().read_latest_activity(tmp_path / "absent.db", since_ts=0) is None


class TestOpenCodeParser:
    """Tests for OpenCodeParser."""

    def test_get_watch_paths_missing_dir(self, tmp_path):
        parser = OpenCodeParser()
        parser._base_dir = tmp_path / "nonexistent"
        assert parser.get_watch_paths() == []

    def test_get_watch_paths_existing_dir(self, tmp_path):
        parser = OpenCodeParser()
        parser._base_dir = tmp_path
        assert parser.get_watch_paths() == [tmp_path]

    def test_find_active_session_returns_db(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        conn.close()
        parser = OpenCodeParser(db_path=db_path)
        result = parser.find_active_session()
        assert result == (db_path, 0)

    def test_find_active_session_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENWAIFUD_OPENCODE_DB", raising=False)
        monkeypatch.setattr(OpenCodeReader, "DEFAULT_DB_PATH", tmp_path / "default-absent.db")
        parser = OpenCodeParser(db_path=tmp_path / "absent.db")
        assert parser.find_active_session() is None

    def test_parse_latest_builds_result(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        _add_message(
            conn,
            message_id="m1",
            session_id="ses_42",
            time_created=1500,
            data={"role": "assistant", "agent": "build", "path": {"cwd": "/tmp/proj"}},
            parts=[
                {"type": "text", "text": "Writing code"},
                {"type": "tool", "tool": "write", "state": {"status": "completed"}},
            ],
        )
        conn.close()

        parser = OpenCodeParser(db_path=db_path)
        result = parser.parse_latest(db_path)
        assert result is not None
        assert result.plugin_type == "opencode"
        assert result.session_id == "ses_42"
        assert result.last_role == "assistant"
        assert result.has_tool_use is True
        assert result.tool_names == ["write"]
        assert result.new_offset == 1500
        assert result.metadata["project_path"] == "/tmp/proj"
        assert result.metadata["agent"] == "build"

    def test_parse_latest_no_duplicate_emission(self, tmp_path):
        """The internal watermark prevents re-emitting the same message."""
        db_path, conn = _make_db(tmp_path)
        _add_message(
            conn,
            message_id="m1",
            session_id="ses_1",
            time_created=1000,
            data={"role": "assistant"},
            parts=[{"type": "text", "text": "hi"}],
        )
        conn.close()

        parser = OpenCodeParser(db_path=db_path)
        first = parser.parse_latest(db_path)
        assert first is not None
        # Second call with offset 0 must not re-emit (watermark advanced).
        assert parser.parse_latest(db_path, offset=0) is None

    def test_parse_latest_missing_db_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENWAIFUD_OPENCODE_DB", raising=False)
        monkeypatch.setattr(OpenCodeReader, "DEFAULT_DB_PATH", tmp_path / "default-absent.db")
        parser = OpenCodeParser(db_path=tmp_path / "absent.db")
        assert parser.parse_latest(tmp_path / "absent.db") is None
