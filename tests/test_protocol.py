"""Tests for openwaifud.ble.protocol (session command-line protocol)."""

from openwaifud.ble.protocol import (
    CMD_CLEAR,
    CMD_REMOVE,
    CMD_UPSERT,
    DEVICE_NAME,
    MAX_PAYLOAD,
    SERVICE_UUID,
    STATUS_CHARS,
    WRITE_CHAR_UUID,
    encode_clear,
    encode_session_remove,
    encode_session_upsert,
    status_char,
)
from openwaifud.models import AgentStatus


class TestGattIdentifiers:
    """确保 GATT 标识与固件保持一致。"""

    def test_device_name(self):
        assert DEVICE_NAME == "OpenWaifu"

    def test_service_uuid(self):
        assert SERVICE_UUID == "0000fd50-0000-1000-0880-00805f9b34fb"

    def test_write_char_uuid(self):
        assert WRITE_CHAR_UUID == "00000001-0000-1001-8001-00805f9b07d0"


class TestStatusChars:
    """状态码映射。"""

    def test_all_statuses_have_chars(self):
        for status in AgentStatus:
            assert status in STATUS_CHARS
            assert len(STATUS_CHARS[status]) == 1

    def test_status_char_values(self):
        assert status_char(AgentStatus.THINKING) == "T"
        assert status_char(AgentStatus.CODING) == "C"
        assert status_char(AgentStatus.TESTING) == "V"
        assert status_char(AgentStatus.ERROR) == "E"
        assert status_char(AgentStatus.IDLE) == "I"


class TestEncodeClear:
    def test_clear(self):
        assert encode_clear().decode("utf-8") == CMD_CLEAR


class TestEncodeRemove:
    def test_remove(self):
        line = encode_session_remove("abc123").decode("utf-8")
        assert line == f"{CMD_REMOVE}|abc123"

    def test_remove_sanitizes_separator(self):
        line = encode_session_remove("a|b").decode("utf-8")
        # sid 中的分隔符被替换为空格，避免破坏解析
        assert line == f"{CMD_REMOVE}|a b"


class TestEncodeUpsert:
    def test_basic_fields(self):
        line = encode_session_upsert(
            session_id="s1",
            status=AgentStatus.CODING,
            elapsed_seconds=42,
            plugin_type="opencode",
            task="实现用户登录",
        ).decode("utf-8")
        parts = line.split("|", 5)
        assert parts[0] == CMD_UPSERT
        assert parts[1] == "s1"
        assert parts[2] == "C"
        assert parts[3] == "42"
        assert parts[4] == "opencode"
        assert parts[5] == "实现用户登录"

    def test_task_separator_sanitized(self):
        line = encode_session_upsert("s1", AgentStatus.THINKING, 0, "codex", "a|b|c").decode("utf-8")
        parts = line.split("|", 5)
        # 任务里的 | 被替换为空格，因此 split 后正好 6 段
        assert parts[5] == "a b c"

    def test_negative_elapsed_clamped(self):
        line = encode_session_upsert("s1", AgentStatus.IDLE, -5, "codex", "").decode("utf-8")
        assert line.split("|")[3] == "0"

    def test_empty_plugin_defaults_to_agent(self):
        line = encode_session_upsert("s1", AgentStatus.IDLE, 0, "", "").decode("utf-8")
        assert line.split("|")[4] == "agent"

    def test_long_task_truncated_within_limit(self):
        data = encode_session_upsert("s1", AgentStatus.CODING, 1, "opencode", "中" * 300)
        assert len(data) <= MAX_PAYLOAD
        # 截断后仍是合法 UTF-8（未切断多字节字符）
        data.decode("utf-8")
