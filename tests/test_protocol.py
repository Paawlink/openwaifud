"""Tests for openwaifud.ble.protocol (session command-line protocol)."""

from openwaifud.ble.protocol import (
    CMD_DETAIL,
    CMD_GLOBAL,
    CMD_UPSERT,
    DETAIL_CHAT,
    DETAIL_ERROR,
    DETAIL_META,
    DEVICE_NAME,
    GLOBAL_EVENT_CHARS,
    MAX_PAYLOAD,
    SERVICE_UUID,
    STATUS_CHARS,
    WRITE_CHAR_UUID,
    encode_global_event,
    encode_session_detail,
    encode_session_upsert,
    global_event_char,
    status_char,
)
from openwaifud.models import AgentStatus, GlobalEventKind


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


class TestGlobalEventChars:
    """全局事件码映射（泳道 2）。"""

    def test_all_kinds_have_chars(self):
        for kind in GlobalEventKind:
            assert kind in GLOBAL_EVENT_CHARS
            assert len(GLOBAL_EVENT_CHARS[kind]) == 1

    def test_global_event_char_values(self):
        assert global_event_char(GlobalEventKind.ERROR) == "E"
        assert global_event_char(GlobalEventKind.CANCEL) == "X"
        assert global_event_char(GlobalEventKind.DONE) == "D"


class TestEncodeGlobalEvent:
    def test_error_fields(self):
        line = encode_global_event(GlobalEventKind.ERROR, "构建失败").decode("utf-8")
        parts = line.split("|", 2)
        assert parts[0] == CMD_GLOBAL
        assert parts[1] == "E"
        assert parts[2] == "构建失败"

    def test_cancel_char_mapping(self):
        line = encode_global_event(GlobalEventKind.CANCEL).decode("utf-8")
        parts = line.split("|", 2)
        assert parts[1] == "X"
        # 无详情时 detail 段为空
        assert parts[2] == ""

    def test_done_char_mapping(self):
        line = encode_global_event(GlobalEventKind.DONE, "重构完成").decode("utf-8")
        parts = line.split("|", 2)
        assert parts[0] == CMD_GLOBAL
        assert parts[1] == "D"
        assert parts[2] == "重构完成"

    def test_detail_separator_sanitized(self):
        line = encode_global_event(GlobalEventKind.ERROR, "a|b\nc").decode("utf-8")
        parts = line.split("|", 2)
        # detail 中的分隔符/换行被替换为空格，因此 split 后正好 3 段
        assert parts[2] == "a b c"

    def test_long_detail_truncated_within_limit(self):
        data = encode_global_event(GlobalEventKind.ERROR, "错" * 300)
        assert len(data) <= MAX_PAYLOAD
        # 截断后仍是合法 UTF-8（未切断多字节字符）
        data.decode("utf-8")


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


class TestEncodeSessionDetail:
    """D 命令（会话详情）编码测试。"""

    def test_error_kind(self):
        line = encode_session_detail("s1", DETAIL_ERROR, 0, "构建失败").decode("utf-8")
        parts = line.split("|", 4)
        assert parts[0] == CMD_DETAIL
        assert parts[1] == "s1"
        assert parts[2] == "0"
        assert parts[3] == "0"
        assert parts[4] == "构建失败"

    def test_meta_kind(self):
        line = encode_session_detail("s1", DETAIL_META, 2, "directory: /tmp").decode("utf-8")
        parts = line.split("|", 4)
        assert parts[0] == CMD_DETAIL
        assert parts[2] == "1"
        assert parts[3] == "2"
        assert parts[4] == "directory: /tmp"

    def test_chat_kind(self):
        line = encode_session_detail("s1", DETAIL_CHAT, 0, "user: Hello").decode("utf-8")
        parts = line.split("|", 4)
        assert parts[0] == CMD_DETAIL
        assert parts[2] == "2"
        assert parts[4] == "user: Hello"

    def test_text_separator_sanitized(self):
        line = encode_session_detail("s1", DETAIL_META, 0, "a|b\nc").decode("utf-8")
        parts = line.split("|", 4)
        # 分隔符/换行被替换为空格
        assert parts[4] == "a b c"

    def test_long_text_truncated_within_limit(self):
        data = encode_session_detail("s1", DETAIL_CHAT, 0, "中" * 300)
        assert len(data) <= MAX_PAYLOAD
        data.decode("utf-8")

    def test_empty_text(self):
        line = encode_session_detail("s1", DETAIL_ERROR, 0, "").decode("utf-8")
        parts = line.split("|", 4)
        assert parts[4] == ""
