"""OpenCode conversation monitoring via its SQLite database.

Rebuilt to follow the OpenCode storage model used by ``ocmonitor``:
OpenCode v1.2.0+ persists everything in ``~/.local/share/opencode/opencode.db``
with three relevant tables:

- ``session`` (id, parent_id, title, project_id, time_created, ...)
- ``message`` (id, session_id, time_created, data JSON) - one row per turn;
  ``data.role`` is ``user``/``assistant`` and holds model/agent/path info.
- ``part``    (id, message_id, session_id, data JSON) - message fragments;
  ``data.type`` is ``text``/``tool``/... and tool parts carry
  ``data.tool`` and ``data.state.status`` (``completed``/``error``/...).

The reader opens the database read-only so it never mutates OpenCode state.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from openwaifud.realtime.parsers import BaseParser, ParseResult


@dataclass
class OpenCodeActivity:
    """The most recent message activity read from the OpenCode database."""

    session_id: str
    role: str
    time_created: int  # milliseconds since epoch
    text: str = ""
    tool_names: list[str] = field(default_factory=list)
    has_error: bool = False
    project_path: str | None = None
    agent: str | None = None
    model_id: str | None = None


class OpenCodeReader:
    """Read-only accessor for the OpenCode SQLite database.

    Mirrors ``ocmonitor.utils.sqlite_utils.SQLiteProcessor`` in terms of path
    discovery and schema handling, but only exposes what the realtime watcher
    needs: the newest message plus its tool/text parts for status inference.
    """

    DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

    @staticmethod
    def find_database_path(custom_path: Path | None = None) -> Path | None:
        """Locate the OpenCode database, checking overrides then defaults.

        Priority: explicit path -> ``OPENWAIFUD_OPENCODE_DB`` env override ->
        platform default location. Returns the first existing candidate.
        """
        candidates: list[Path] = []

        if custom_path is not None:
            candidates.append(Path(custom_path))

        env_db_path = os.environ.get("OPENWAIFUD_OPENCODE_DB")
        if env_db_path:
            candidates.append(Path(os.path.expanduser(os.path.expandvars(env_db_path))))

        candidates.append(OpenCodeReader.DEFAULT_DB_PATH)

        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                candidates.append(Path(appdata) / "opencode" / "opencode.db")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _connect(db_path: Path) -> sqlite3.Connection:
        """Open a short-lived read-only connection (never mutates OpenCode)."""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _extract_model_id(data: dict) -> str | None:
        """Extract the model id from either ``modelID`` or ``model.modelID``."""
        model_id = data.get("modelID")
        if not model_id and isinstance(data.get("model"), dict):
            model_id = data["model"].get("modelID")
        return model_id.lower() if isinstance(model_id, str) and model_id else None

    @staticmethod
    def _extract_project_path(data: dict) -> str | None:
        """Extract the working directory (cwd, falling back to root)."""
        path_data = data.get("path")
        if isinstance(path_data, dict):
            return path_data.get("cwd") or path_data.get("root")
        return None

    def read_latest_activity(self, db_path: Path, since_ts: int = 0) -> OpenCodeActivity | None:
        """Return the newest message created after ``since_ts`` (in ms).

        Reads the message row plus its parts to derive role, text summary,
        tool usage and tool-error state. Returns ``None`` when there is no
        newer activity. Never raises: failures are logged and swallowed.
        """
        try:
            if not db_path.exists():
                return None

            conn = self._connect(db_path)
            try:
                message_row = conn.execute(
                    """
                    SELECT id, session_id, time_created, data
                    FROM message
                    WHERE time_created > ?
                    ORDER BY time_created DESC
                    LIMIT 1
                    """,
                    (since_ts,),
                ).fetchone()

                if message_row is None:
                    return None

                part_rows = conn.execute(
                    "SELECT data FROM part WHERE message_id = ? ORDER BY rowid",
                    (message_row["id"],),
                ).fetchall()
            finally:
                conn.close()

            return self._build_activity(message_row, part_rows)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"OpenCode database read error on {db_path}: {exc}")
            return None

    def _build_activity(
        self, message_row: sqlite3.Row, part_rows: list[sqlite3.Row]
    ) -> OpenCodeActivity | None:
        """Assemble an :class:`OpenCodeActivity` from raw message/part rows."""
        try:
            message_data = json.loads(message_row["data"])
        except (json.JSONDecodeError, TypeError):
            message_data = {}

        role = message_data.get("role", "")

        text_parts: list[str] = []
        tool_names: list[str] = []
        has_error = message_data.get("finish") == "error"

        for part_row in part_rows:
            try:
                part = json.loads(part_row["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(part, dict):
                continue

            part_type = part.get("type", "")
            if part_type == "text":
                text = part.get("text", "")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            elif part_type == "tool":
                tool_name = part.get("tool") or part.get("name")
                if tool_name and tool_name not in tool_names:
                    tool_names.append(tool_name)
                state = part.get("state")
                if isinstance(state, dict) and state.get("status") == "error":
                    has_error = True

        return OpenCodeActivity(
            session_id=message_row["session_id"],
            role=role,
            time_created=message_row["time_created"],
            text=" ".join(text_parts),
            tool_names=tool_names,
            has_error=has_error,
            project_path=self._extract_project_path(message_data),
            agent=message_data.get("agent"),
            model_id=self._extract_model_id(message_data),
        )


class OpenCodeParser(BaseParser):
    """Parser that turns OpenCode database activity into a :class:`ParseResult`.

    OpenCode stores conversations in a SQLite database rather than JSONL files,
    so this parser tracks progress by ``message.time_created`` (milliseconds)
    instead of a byte offset. The watcher passes a byte-style offset per changed
    file (``opencode.db`` / ``opencode.db-wal``); we normalise against an
    internal high-water mark so the same message is never emitted twice.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._base_dir = Path.home() / ".local" / "share" / "opencode"
        self._explicit_db_path = db_path
        self._reader = OpenCodeReader()
        self._last_ts = 0

    def _resolve_db_path(self) -> Path | None:
        return OpenCodeReader.find_database_path(self._explicit_db_path)

    def get_watch_paths(self) -> list[Path]:
        """Watch the OpenCode data directory (database + WAL live here)."""
        if not self._base_dir.exists():
            logger.debug(f"OpenCode directory not found: {self._base_dir}")
            return []
        return [self._base_dir]

    def find_active_session(self) -> tuple[Path, int] | None:
        """Return the database path for the initial scan, or None if absent."""
        db_path = self._resolve_db_path()
        if db_path is None:
            return None
        return (db_path, 0)

    def parse_latest(self, file_path: Path, offset: int = 0) -> ParseResult | None:
        """Read the newest OpenCode message and build a ParseResult.

        ``file_path`` is ignored beyond triggering: the parser always queries
        the resolved database. ``offset`` is treated as a ``time_created``
        watermark and merged with the internal one.
        """
        db_path = self._resolve_db_path()
        if db_path is None:
            return None

        since_ts = max(offset, self._last_ts)
        activity = self._reader.read_latest_activity(db_path, since_ts)
        if activity is None:
            return None

        self._last_ts = activity.time_created

        metadata: dict[str, str] = {}
        if activity.project_path:
            metadata["project_path"] = activity.project_path
        if activity.agent:
            metadata["agent"] = activity.agent
        if activity.model_id:
            metadata["model_id"] = activity.model_id

        return ParseResult(
            plugin_type="opencode",
            session_id=activity.session_id,
            last_role=activity.role,
            last_content=activity.text[:200],
            has_tool_use=bool(activity.tool_names),
            tool_names=activity.tool_names,
            has_error=activity.has_error,
            timestamp=datetime.fromtimestamp(activity.time_created / 1000, tz=UTC),
            new_offset=activity.time_created,
            metadata=metadata,
        )
