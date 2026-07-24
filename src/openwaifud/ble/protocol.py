"""BLE 命令行协议：把守护进程侧状态编码为固件可解析的单行命令，写入 Write 特征。

同一条 Write 通道上复用两条**泳道**，按命令前缀区分，职责互不重叠：

泳道 1 · 会话列表全量快照（屏幕主体的实时会话看板）——守护进程每隔
``sync_interval`` 秒按 ``B`` -> 逐个 ``S`` 全量会话 -> ``E`` 的顺序下发当前所有
活跃会话，固件据此把屏幕列表**收敛到与 api/v1/state 完全一致**（快照对账，无需
依赖增量命令的可靠送达，也不会清屏闪烁）。

泳道 2 · 全局事件推送（独立于列表的即时通知）——会话出错 / 被用户取消等
值得关注的事件通过 ``G`` 命令**事件驱动地即时下发**（不参与全量轮询），
驱动固件屏幕左下角的全局状态机（瞬时展示后自动回落）。

命令格式（UTF-8，单行，不含换行，长度 <= :data:`MAX_PAYLOAD` 字节）：

======  ==============================================  ================================
命令    格式                                             含义
======  ==============================================  ================================
开始    ``B``                                           快照同步开始（固件把现有会话标记为“未见”）
更新    ``S|<sid>|<st>|<elapsed>|<plugin>|<task>``       新增或更新一个会话
详情    ``D|<sid>|<kind>|<seq>|<text>``                 会话详情数据（错误/元数据/聊天上下文）
结束    ``E``                                           快照同步结束（固件移除本轮未再出现的会话）
事件    ``G|<ev>|<detail>``                             全局事件（``ev`` 见 :data:`GLOBAL_EVENT_CHARS`）
======  ==============================================  ================================

其中 ``<st>`` 为单字符状态码（见 :data:`STATUS_CHARS`），``<elapsed>`` 为该会话
已运行的整数秒数（固件收到后在本地按秒继续跳动）。字段以 ``|`` 分隔，因此
``sid`` / ``task`` / ``detail`` 中的 ``|``、换行等字符会在编码前被替换为空格。
"""

from __future__ import annotations

from openwaifud.models import AgentStatus, GlobalEventKind

# ---------------------------------------------------------------------------
# 设备与 GATT 标识（须与固件 apps/openwaifu 保持一致）
# ---------------------------------------------------------------------------

DEVICE_NAME = "OpenWaifu"
SERVICE_UUID = "0000fd50-0000-1000-0880-00805f9b34fb"
WRITE_CHAR_UUID = "00000001-0000-1001-8001-00805f9b07d0"

# 单条命令 UTF-8 编码后的最大字节数（与固件 OPENWAIFU_BLE_MAX_MSG_LEN 对齐）
MAX_PAYLOAD: int = 240

# 字段分隔符与命令前缀
FIELD_SEP = "|"
CMD_UPSERT = "S"
CMD_SYNC_BEGIN = "B"
CMD_SYNC_END = "E"
CMD_GLOBAL = "G"
CMD_DETAIL = "D"

# 详情数据类型码（D 命令的 <kind> 字段）
DETAIL_ERROR = "0"  # 错误信息
DETAIL_META = "1"   # 元数据条目（text 格式 "key: value"）
DETAIL_CHAT = "2"   # 聊天消息（text 格式 "role: content"）

# AgentStatus -> 单字符状态码（固件据此显示中文标签与配色）
STATUS_CHARS: dict[AgentStatus, str] = {
    AgentStatus.THINKING: "T",
    AgentStatus.CODING: "C",
    AgentStatus.TESTING: "V",
    AgentStatus.ERROR: "E",
    AgentStatus.IDLE: "I",
}

# GlobalEventKind -> 单字符事件码（``G`` 前缀命名空间隔离，不与会话命令冲突）
GLOBAL_EVENT_CHARS: dict[GlobalEventKind, str] = {
    GlobalEventKind.ERROR: "E",
    GlobalEventKind.CANCEL: "X",
}


# ---------------------------------------------------------------------------
# Exceptions（保留以兼容既有调用方）
# ---------------------------------------------------------------------------


class BLEProtocolError(Exception):
    """BLE 协议编码错误。"""


class BLEConnectionError(Exception):
    """BLE 连接失败。"""


class BLEWriteError(Exception):
    """BLE 特征写入失败。"""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    """去除会破坏命令行解析的字符（分隔符、换行、回车），并压缩两端空白。"""
    if not text:
        return ""
    cleaned = text.replace(FIELD_SEP, " ").replace("\r", " ").replace("\n", " ")
    return cleaned.strip()


def status_char(status: AgentStatus) -> str:
    """返回状态对应的单字符命令码，未知状态回退为空闲 ``I``。"""
    return STATUS_CHARS.get(status, "I")


def global_event_char(kind: GlobalEventKind) -> str:
    """返回全局事件对应的单字符事件码，未知事件回退为出错 ``E``。"""
    return GLOBAL_EVENT_CHARS.get(kind, "E")


def _encode_line(text: str) -> bytes:
    """将整行命令编码为 UTF-8，超长时按字符边界安全截断。"""
    payload = text.encode("utf-8")
    if len(payload) <= MAX_PAYLOAD:
        return payload
    return payload[:MAX_PAYLOAD].decode("utf-8", errors="ignore").encode("utf-8")


# ---------------------------------------------------------------------------
# 命令编码
# ---------------------------------------------------------------------------


def encode_sync_begin() -> bytes:
    """编码“快照同步开始”命令：固件把现有会话标记为“未见”。"""
    return _encode_line(CMD_SYNC_BEGIN)


def encode_sync_end() -> bytes:
    """编码“快照同步结束”命令：固件移除本轮未再出现（仍为“未见”）的会话。"""
    return _encode_line(CMD_SYNC_END)


def encode_global_event(kind: GlobalEventKind, detail: str = "") -> bytes:
    """编码“全局事件”命令（泳道 2）。

    格式：``G|<ev>|<detail>``。先构造固定字段前缀，再用剩余字节预算容纳（可能
    较长的）详情文本，确保整行 UTF-8 编码后不超过 :data:`MAX_PAYLOAD`。
    """
    ev = global_event_char(kind)
    prefix = f"{CMD_GLOBAL}{FIELD_SEP}{ev}{FIELD_SEP}"
    budget = MAX_PAYLOAD - len(prefix.encode("utf-8"))
    if budget <= 0:
        return _encode_line(prefix)

    detail_bytes = _sanitize(detail).encode("utf-8")
    if len(detail_bytes) > budget:
        detail_bytes = detail_bytes[:budget].decode("utf-8", errors="ignore").encode("utf-8")
    return prefix.encode("utf-8") + detail_bytes


def encode_session_upsert(
    session_id: str,
    status: AgentStatus,
    elapsed_seconds: int,
    plugin_type: str,
    task: str,
) -> bytes:
    """编码“新增/更新会话”命令。

    先构造固定字段前缀，再用剩余字节预算容纳（可能较长的）任务描述，确保整行
    UTF-8 编码后不超过 :data:`MAX_PAYLOAD`。
    """
    sid = _sanitize(session_id)
    plugin = _sanitize(plugin_type) or "agent"
    st = status_char(status)
    elapsed = max(0, int(elapsed_seconds))

    prefix = f"{CMD_UPSERT}{FIELD_SEP}{sid}{FIELD_SEP}{st}{FIELD_SEP}{elapsed}{FIELD_SEP}{plugin}{FIELD_SEP}"
    budget = MAX_PAYLOAD - len(prefix.encode("utf-8"))
    if budget <= 0:
        # 极端情况下 sid/plugin 已占满预算，直接截断整行
        return _encode_line(prefix)

    task_bytes = _sanitize(task).encode("utf-8")
    if len(task_bytes) > budget:
        task_bytes = task_bytes[:budget].decode("utf-8", errors="ignore").encode("utf-8")
    return prefix.encode("utf-8") + task_bytes


def encode_session_detail(
    session_id: str,
    kind: str,
    seq: int,
    text: str,
) -> bytes:
    """编码“会话详情”命令。

    格式：``D|<sid>|<kind>|<seq>|<text>``。先构造固定字段前缀，再用剩余字节
    预算容纳（可能较长的）详情文本，确保整行 UTF-8 编码后不超过
    :data:`MAX_PAYLOAD`。

    :param kind: 详情类型码（见 :data:`DETAIL_ERROR` / :data:`DETAIL_META` /
        :data:`DETAIL_CHAT`）。
    :param seq:  序号（0-based），固件端据此替换对应槽位。
    :param text: 详情文本。metadata 为 ``"key: value"``，chat 为 ``"role: content"``。
    """
    sid = _sanitize(session_id)
    k = _sanitize(kind) or DETAIL_ERROR
    s = max(0, int(seq))

    prefix = f"{CMD_DETAIL}{FIELD_SEP}{sid}{FIELD_SEP}{k}{FIELD_SEP}{s}{FIELD_SEP}"
    budget = MAX_PAYLOAD - len(prefix.encode("utf-8"))
    if budget <= 0:
        return _encode_line(prefix)

    text_bytes = _sanitize(text).encode("utf-8")
    if len(text_bytes) > budget:
        text_bytes = text_bytes[:budget].decode("utf-8", errors="ignore").encode("utf-8")
    return prefix.encode("utf-8") + text_bytes
