"""JSONL parsers for AI agent conversation files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger


@dataclass
class ParseResult:
    """Unified parse result from any agent tool."""

    plugin_type: str  # "claudecode" / "codex" / "opencode"
    session_id: str
    last_role: str  # "user" / "assistant"
    last_content: str  # 最新消息内容摘要（前200字符）
    has_tool_use: bool  # 是否包含工具调用
    tool_names: list[str] = field(default_factory=list)  # 使用的工具名称
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    new_offset: int = 0  # 新的文件偏移位置


class BaseParser:
    """Base class for agent file parsers."""

    def get_watch_paths(self) -> list[Path]:
        """Return directories to watch. Missing dirs are excluded."""
        raise NotImplementedError

    def parse_latest(self, file_path: Path, offset: int = 0) -> ParseResult | None:
        """Parse new content from file starting at offset.

        Returns ParseResult if new content found, None otherwise.
        Exceptions are caught internally - never raises.
        """
        raise NotImplementedError

    def find_active_session(self) -> tuple[Path, int] | None:
        """Find the most recently modified session file.

        Returns (file_path, 0) for initial scan, or None if no sessions found.
        """
        raise NotImplementedError


class ClaudeCodeParser(BaseParser):
    """Parser for Claude Code conversation files.

    Storage: ~/.claude/projects/<encoded-path>/<session-ID>.jsonl
    Format: One JSON object per line with fields:
      - type: "user" | "assistant"
      - sessionId: string
      - uuid: string
      - parentUuid: string | null
      - message: {role: string, content: string | list}
    """

    def __init__(self) -> None:
        self._base_dir = Path.home() / ".claude" / "projects"

    def get_watch_paths(self) -> list[Path]:
        """Return Claude Code project directories to watch."""
        if not self._base_dir.exists():
            logger.debug(f"Claude Code directory not found: {self._base_dir}")
            return []
        return [self._base_dir]

    def find_active_session(self) -> tuple[Path, int] | None:
        """Find the most recently modified Claude Code session file."""
        if not self._base_dir.exists():
            return None
        # Find most recently modified .jsonl file
        jsonl_files = list(self._base_dir.rglob("*.jsonl"))
        if not jsonl_files:
            return None
        latest = max(jsonl_files, key=lambda f: f.stat().st_mtime)
        return (latest, 0)

    def parse_latest(self, file_path: Path, offset: int = 0) -> ParseResult | None:
        """Parse new content from a Claude Code JSONL file starting at offset."""
        try:
            if not file_path.exists():
                return None
            file_size = file_path.stat().st_size
            if file_size <= offset:
                return None  # No new content

            with file_path.open("r", encoding="utf-8") as f:
                f.seek(offset)
                new_lines = f.readlines()
                new_offset = f.tell()

            if not new_lines:
                return None

            # Parse lines, skip malformed ones
            last_role = ""
            last_content = ""
            has_tool_use = False
            tool_names: list[str] = []
            session_id = file_path.stem  # session ID is the filename without extension

            for line in reversed(new_lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = obj.get("type", "")
                message = obj.get("message", {})
                role = message.get("role", msg_type)
                content = message.get("content", "")

                # Extract content text
                if isinstance(content, list):
                    # Content can be a list of blocks
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                has_tool_use = True
                                tool_name = block.get("name", "unknown")
                                if tool_name not in tool_names:
                                    tool_names.append(tool_name)
                            elif block.get("type") == "tool_result":
                                has_tool_use = True
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content_str = " ".join(text_parts)
                elif isinstance(content, str):
                    content_str = content
                else:
                    content_str = str(content)

                if not last_role:
                    last_role = role
                    last_content = content_str[:200]
                break  # Only need the last message

            if not last_role:
                return None

            return ParseResult(
                plugin_type="claudecode",
                session_id=session_id,
                last_role=last_role,
                last_content=last_content,
                has_tool_use=has_tool_use,
                tool_names=tool_names,
                new_offset=new_offset,
            )
        except Exception as e:
            logger.warning(f"ClaudeCodeParser error on {file_path}: {e}")
            return None


class CodexParser(BaseParser):
    """Parser for Codex CLI conversation files.

    Storage: ~/.codex/sessions/YYYY/MM/DD/rollout-TIMESTAMP-<id>.jsonl
    Format:
      - Line 1: {"type": "session_meta", "payload": {"id": ..., "cwd": ...}}
      - Lines 2+: {"type": "response_item",
        "payload": {"type": "message", "role": ..., "content": [...]}}
    """

    def __init__(self) -> None:
        self._base_dir = Path.home() / ".codex" / "sessions"

    def get_watch_paths(self) -> list[Path]:
        """Return Codex session directories to watch."""
        if not self._base_dir.exists():
            logger.debug(f"Codex directory not found: {self._base_dir}")
            return []
        return [self._base_dir]

    def find_active_session(self) -> tuple[Path, int] | None:
        """Find the most recently modified Codex session file."""
        if not self._base_dir.exists():
            return None
        jsonl_files = list(self._base_dir.rglob("*.jsonl"))
        if not jsonl_files:
            return None
        latest = max(jsonl_files, key=lambda f: f.stat().st_mtime)
        return (latest, 0)

    def parse_latest(self, file_path: Path, offset: int = 0) -> ParseResult | None:
        """Parse new content from a Codex JSONL file starting at offset."""
        try:
            if not file_path.exists():
                return None
            file_size = file_path.stat().st_size
            if file_size <= offset:
                return None

            with file_path.open("r", encoding="utf-8") as f:
                f.seek(offset)
                new_lines = f.readlines()
                new_offset = f.tell()

            if not new_lines:
                return None

            session_id = file_path.stem
            last_role = ""
            last_content = ""
            has_tool_use = False
            tool_names: list[str] = []

            # If reading from beginning, parse session_meta from first line
            if offset == 0 and new_lines:
                first_line = new_lines[0].strip()
                try:
                    first_obj = json.loads(first_line)
                    if first_obj.get("type") == "session_meta":
                        payload = first_obj.get("payload", {})
                        session_id = payload.get("id", session_id)
                except json.JSONDecodeError:
                    pass

            # Parse from last line backwards to find latest message
            for line in reversed(new_lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") == "session_meta":
                    continue  # Skip metadata lines

                payload = obj.get("payload", obj)
                role = payload.get("role", "")
                content = payload.get("content", "")

                # Extract text from content
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type in ("input_text", "output_text"):
                                text_parts.append(block.get("text", ""))
                            elif block_type in ("tool_use", "function_call"):
                                has_tool_use = True
                                tool_name = block.get("name", "unknown")
                                if tool_name not in tool_names:
                                    tool_names.append(tool_name)
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content_str = " ".join(text_parts)
                elif isinstance(content, str):
                    content_str = content
                else:
                    content_str = str(content) if content else ""

                if role and not last_role:
                    last_role = role
                    last_content = content_str[:200]
                    break

            if not last_role:
                return None

            return ParseResult(
                plugin_type="codex",
                session_id=session_id,
                last_role=last_role,
                last_content=last_content,
                has_tool_use=has_tool_use,
                tool_names=tool_names,
                new_offset=new_offset,
            )
        except Exception as e:
            logger.warning(f"CodexParser error on {file_path}: {e}")
            return None


class OpenCodeParser(BaseParser):
    """Parser for OpenCode conversation files (best-effort).

    Storage: ~/.local/share/opencode/history/ (format not fully documented)
    Falls back to Claude Code-like JSONL parsing.
    """

    def __init__(self) -> None:
        self._base_dir = Path.home() / ".local" / "share" / "opencode" / "history"

    def get_watch_paths(self) -> list[Path]:
        """Return OpenCode history directories to watch."""
        if not self._base_dir.exists():
            logger.debug(f"OpenCode directory not found: {self._base_dir}")
            return []
        return [self._base_dir]

    def find_active_session(self) -> tuple[Path, int] | None:
        """Find the most recently modified OpenCode session file."""
        if not self._base_dir.exists():
            return None
        # Try both .jsonl and .json files
        files = list(self._base_dir.rglob("*.jsonl")) + list(self._base_dir.rglob("*.json"))
        if not files:
            return None
        latest = max(files, key=lambda f: f.stat().st_mtime)
        return (latest, 0)

    def parse_latest(self, file_path: Path, offset: int = 0) -> ParseResult | None:
        """Best-effort parse using Claude Code-like format."""
        try:
            if not file_path.exists():
                return None
            file_size = file_path.stat().st_size
            if file_size <= offset:
                return None

            with file_path.open("r", encoding="utf-8") as f:
                f.seek(offset)
                new_lines = f.readlines()
                new_offset = f.tell()

            if not new_lines:
                return None

            session_id = file_path.stem
            last_role = ""
            last_content = ""
            has_tool_use = False
            tool_names: list[str] = []

            for line in reversed(new_lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Try common fields
                role = obj.get("role", obj.get("type", ""))
                message = obj.get("message", {})
                if isinstance(message, dict) and "role" in message:
                    role = message.get("role", role)

                content = obj.get(
                    "content",
                    message.get("content", "") if isinstance(message, dict) else "",
                )

                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") in (
                                "text",
                                "input_text",
                                "output_text",
                            ):
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") in ("tool_use", "function_call"):
                                has_tool_use = True
                                if block.get("name"):
                                    tool_names.append(block["name"])
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content_str = " ".join(text_parts)
                elif isinstance(content, str):
                    content_str = content
                else:
                    content_str = ""

                if role and not last_role:
                    last_role = role
                    last_content = content_str[:200]
                    break

            if not last_role:
                return None

            return ParseResult(
                plugin_type="opencode",
                session_id=session_id,
                last_role=last_role,
                last_content=last_content,
                has_tool_use=has_tool_use,
                tool_names=tool_names,
                new_offset=new_offset,
            )
        except Exception as e:
            logger.warning(f"OpenCodeParser error on {file_path}: {e}")
            return None
